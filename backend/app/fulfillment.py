"""Explicitly attested manual work; never automates a provider's login or billing."""

import hmac
import json
from datetime import UTC, timedelta
from typing import Literal

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import or_, select, update

from .models import AuditEvent, Fulfillment, Order, OrderStatus, utcnow

CONSENT_VERSION = "single-purchase-session-v2"
CONSENT_TEXT = (
    "我授权操作员仅为本订单使用我提交的 Session 登录我指定的账号，按订单所列套餐完成一次购买；"
    "不授权额外消费或后续续费。操作员须核实会员到账、取消自动续费、"
    "移除付款方式、退出账号并删除本系统中的临时登录凭据。"
    "如无法完成任一步骤，应暂停交付并联系我，不得标记全部完成。"
    "我可撤回尚未执行的授权，已发生的购买需另行处理退款。"
)
STEPS = (
    "login_confirmed",
    "membership_active",
    "renewal_cancelled",
    "payment_method_removed",
    "logged_out",
    "completed",
)


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["single-purchase-session-v2"]
    accepted: Literal[True]


class LoginCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_type: Literal["session"] = "session"
    session: SecretStr = Field(min_length=1, max_length=32768)

    @field_validator("session")
    @classmethod
    def nonblank_session(cls, value: SecretStr) -> SecretStr:
        # Opaque input: accepting a value does not verify its issuer or validity.
        cleaned = value.get_secret_value().strip()
        if not cleaned:
            raise ValueError("Session must not be blank")
        return SecretStr(cleaned)


class Attestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "login_confirmed",
        "membership_active",
        "renewal_cancelled",
        "payment_method_removed",
        "logged_out",
        "completed",
    ]
    confirmed: Literal[True]
    # A reference to an external receipt/evidence store, never Session content.
    evidence_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{3,100}$")


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal[
        "checkout_rejected", "payment_failed", "verification_required",
        "renewal_cleanup_failed", "payment_method_cleanup_failed", "other",
    ]


def pause_state(db, order_id):
    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.order_id == order_id,
               AuditEvent.event_type.in_(["manual.paused", "manual.resumed"]))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1)
    )
    if event and event.event_type == "manual.paused":
        return json.loads(event.details)["reason"]
    return None


def active(row):
    return (
        row.consent_version == CONSENT_VERSION
        and row.revoked_at is None
        and row.authorized_until.replace(tzinfo=UTC) > utcnow()
    )


def record(db, row, event, details=None, created_at=None):
    db.add(
        AuditEvent(
            order_id=row.order_id,
            event_type="manual." + event,
            details=json.dumps(details or {}, separators=(",", ":")),
            created_at=created_at or utcnow(),
        )
    )


def summary(db, order_id):
    row = db.get(Fulfillment, order_id)
    if row is None:
        return {
            "status": "awaiting_consent",
            "steps_completed": [],
            "credential_stored": False,
        }
    reason = pause_state(db, order_id)
    status = (
        "completed"
        if row.step == len(STEPS)
        else (
            "revoked_cleanup_required"
            if row.revoked_at
            else (
                "legacy_consent_cleanup_required"
                if row.consent_version != CONSENT_VERSION
                else (
                    ("paused" if reason else "in_progress")
                    if active(row)
                    else "authorization_expired_cleanup_required"
                )
            )
        )
    )
    return {
        "status": status,
        "consent_version": row.consent_version,
        "authorized_until": row.authorized_until.replace(tzinfo=UTC),
        "steps_completed": list(STEPS[: row.step]),
        "next_step": STEPS[row.step] if row.step < len(STEPS) else None,
        "credential_stored": row.credential_ciphertext is not None,
        "credential_deleted_at": row.credential_deleted_at,
        "verification": "operator_attestation_not_provider_verified",
        "pause_reason": reason if status == "paused" else None,
    }


def purge_expired(db):
    now = utcnow()
    due = or_(
        Fulfillment.credential_expires_at <= now,
        Fulfillment.authorized_until <= now,
        Fulfillment.revoked_at.is_not(None),
    )
    rows = db.scalars(
        select(Fulfillment).where(
            Fulfillment.credential_ciphertext.is_not(None),
            due,
        )
    ).all()
    for row in rows:
        changed = db.execute(
            update(Fulfillment)
            .where(
                Fulfillment.order_id == row.order_id,
                due,
                Fulfillment.credential_ciphertext.is_not(None),
            )
            .values(
                credential_ciphertext=None, credential_deleted_at=now, updated_at=now
            )
            .execution_options(synchronize_session="fetch")
        ).rowcount
        if changed:
            record(db, row, "credential_expired")


def install(application, settings, DatabaseSession, Credentials, owned):
    # Dependency aliases are injected to share the existing order authorization.
    def operator(credentials):
        if not settings.operator_token:
            raise HTTPException(503, "manual operator console is not configured")
        supplied = (
            credentials.credentials
            if credentials and credentials.scheme.lower() == "bearer"
            else ""
        )
        if not supplied or not hmac.compare_digest(supplied, settings.operator_token):
            raise HTTPException(403, "operator authentication required")

    def fetch(db, order_id):
        purge_expired(db)
        db.commit()
        row = db.get(Fulfillment, order_id)
        if row is None:
            raise HTTPException(409, "customer consent is required")
        return row

    def save(db, row, changes, event, details=None):
        # Keep audit ordering deterministic even when the database clock has coarse precision.
        changed_at = max(
            utcnow(), row.updated_at.replace(tzinfo=UTC) + timedelta(microseconds=1)
        )
        changed = db.execute(
            update(Fulfillment)
            .where(
                Fulfillment.order_id == row.order_id,
                Fulfillment.updated_at == row.updated_at,
            )
            .values(**changes, updated_at=changed_at)
        ).rowcount
        if not changed:
            db.rollback()
            raise HTTPException(409, "workflow changed; refresh before retrying")
        record(db, row, event, details, changed_at)
        db.commit()

    @application.get("/fulfillment-policy")
    def policy():
        return {
            "version": CONSENT_VERSION,
            "text": CONSENT_TEXT,
            "authorization_hours": 24,
            "credential_ttl_minutes": 15,
            "credential_type": "session",
            "execution": "manual",
            "customer_payment": settings.payment_mode,
            "operator_enabled": bool(settings.operator_token),
        }

    @application.post("/orders/{order_id}/consent")
    def consent(
        order_id: str, payload: Consent, credentials: Credentials, db: DatabaseSession
    ):
        if not settings.operator_token:
            raise HTTPException(503, "manual operator console is not configured")
        order = owned(db, order_id, credentials)
        if order.status != OrderStatus.PAID:
            raise HTTPException(
                409, "payment must be confirmed first (currently test payments only)"
            )
        if db.get(Fulfillment, order_id):
            raise HTTPException(
                409, "consent already recorded; cannot reset or extend this order"
            )
        now = utcnow()
        row = Fulfillment(
            order_id=order_id,
            consent_version=payload.version,
            consent_at=now,
            authorized_until=now + timedelta(hours=24),
            updated_at=now,
        )
        db.add(row)
        record(
            db,
            row,
            "consent_granted",
            {
                "version": CONSENT_VERSION,
                "product_id": order.product_id,
                "amount_cents": order.amount_cents,
                "currency": order.currency,
            },
        )
        # Unique primary key prevents duplicate consent under concurrent requests.
        from sqlalchemy.exc import IntegrityError

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "consent already recorded") from None
        return summary(db, order_id)

    @application.get("/orders/{order_id}/fulfillment")
    def customer_status(order_id: str, credentials: Credentials, db: DatabaseSession):
        owned(db, order_id, credentials)
        purge_expired(db)
        db.commit()
        return summary(db, order_id)

    @application.post("/orders/{order_id}/login-credential")
    def upload(
        order_id: str,
        payload: LoginCredential,
        request: Request,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        owned(db, order_id, credentials)
        if not settings.operator_token:
            raise HTTPException(503, "manual operator console is not configured")
        if request.url.scheme != "https" and request.client.host not in {
            "127.0.0.1",
            "::1",
            "testclient",
        }:
            raise HTTPException(400, "HTTPS is required for login credentials")
        row = fetch(db, order_id)
        if not active(row) or row.step >= 2 or pause_state(db, order_id):
            raise HTTPException(409, "login authorization is no longer active")
        sealed = Fernet(settings.vault_key.encode()).encrypt(
            json.dumps(
                {
                    "credential_type": "session",
                    "session": payload.session.get_secret_value(),
                }
            ).encode()
        )
        save(
            db,
            row,
            {
                "credential_ciphertext": sealed,
                "credential_expires_at": utcnow() + timedelta(minutes=15),
                "credential_deleted_at": None,
            },
            "credential_submitted",
        )
        return summary(db, order_id)

    @application.delete("/orders/{order_id}/login-credential")
    def delete(order_id: str, credentials: Credentials, db: DatabaseSession):
        owned(db, order_id, credentials)
        row = fetch(db, order_id)
        save(
            db,
            row,
            {"credential_ciphertext": None, "credential_deleted_at": utcnow()},
            "credential_deleted",
        )
        return summary(db, order_id)

    @application.post("/orders/{order_id}/revoke-consent")
    def revoke(order_id: str, credentials: Credentials, db: DatabaseSession):
        owned(db, order_id, credentials)
        row = fetch(db, order_id)
        if row.step == len(STEPS):
            raise HTTPException(409, "order already completed")
        save(
            db,
            row,
            {
                "revoked_at": row.revoked_at or utcnow(),
                "credential_ciphertext": None,
                "credential_deleted_at": utcnow(),
            },
            "consent_revoked",
        )
        return summary(db, order_id)

    @application.get("/operator/orders")
    def queue(credentials: Credentials, db: DatabaseSession):
        operator(credentials)
        purge_expired(db)
        db.commit()
        ids = db.scalars(
            select(Fulfillment.order_id)
            .order_by(Fulfillment.updated_at.desc())
            .limit(100)
        ).all()
        return {
            "orders": [
                {
                    "order_id": oid,
                    **summary(db, oid),
                    "product_name": db.get(Order, oid).product_name,
                    "amount_cents": db.get(Order, oid).amount_cents,
                    "currency": db.get(Order, oid).currency,
                    "customer_payment_mode": settings.payment_mode,
                }
                for oid in ids
            ]
        }

    @application.post("/operator/orders/{order_id}/reveal-credential")
    def reveal(order_id: str, credentials: Credentials, db: DatabaseSession):
        operator(credentials)
        row = fetch(db, order_id)
        if (not active(row) or row.step >= 2 or row.credential_ciphertext is None
                or pause_state(db, order_id)):
            raise HTTPException(409, "credential unavailable or authorization inactive")
        value = json.loads(
            Fernet(settings.vault_key.encode()).decrypt(row.credential_ciphertext)
        )
        # Consume atomically before returning; a lost response requires customer resubmission.
        save(
            db,
            row,
            {"credential_ciphertext": None, "credential_deleted_at": utcnow()},
            "credential_consumed",
        )
        return value

    @application.post("/operator/orders/{order_id}/attest")
    def attest(
        order_id: str,
        payload: Attestation,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        operator(credentials)
        row = fetch(db, order_id)
        index = STEPS.index(payload.action)
        # Cleanup remains available after consent is withdrawn/expires. It never authorizes payment.
        cleanup = index in {2, 3, 4}
        if pause_state(db, order_id) and not cleanup:
            raise HTTPException(409, "workflow paused; resolve and resume first")
        if not active(row) and not cleanup:
            raise HTTPException(409, "authorization inactive; cleanup only")
        if row.step >= len(STEPS) or (
            index != row.step and not (not active(row) and cleanup and index > row.step)
        ):
            raise HTTPException(409, "complete the preceding step first")
        if not active(row) and index > row.step:
            # Do not fill skipped steps with fabricated attestations.
            record(
                db,
                row,
                "cleanup_attested",
                {"action": payload.action, "evidence_ref": payload.evidence_ref},
            )
            db.commit()
            return summary(db, order_id)
        if payload.action == "completed" and row.credential_ciphertext is not None:
            raise HTTPException(
                409, "delete stored login credentials before completion"
            )
        save(
            db,
            row,
            {"step": row.step + 1},
            "step_attested",
            {"action": payload.action, "evidence_ref": payload.evidence_ref},
        )
        return summary(db, order_id)

    @application.post("/operator/orders/{order_id}/pause")
    def pause(order_id: str, payload: PauseRequest, credentials: Credentials,
              db: DatabaseSession):
        operator(credentials)
        row = fetch(db, order_id)
        if not active(row) or row.step == len(STEPS):
            raise HTTPException(409, "only an active unfinished workflow may be paused")
        if pause_state(db, order_id):
            raise HTTPException(409, "workflow already paused")
        # Pause consumes stored credentials, and shares the step CAS to fence stale work.
        save(db, row, {"credential_ciphertext": None,
                       "credential_deleted_at": utcnow()}, "paused",
             {"reason": payload.reason})
        return summary(db, order_id)

    @application.post("/operator/orders/{order_id}/resume")
    def resume(order_id: str, credentials: Credentials, db: DatabaseSession):
        operator(credentials)
        row = fetch(db, order_id)
        if not active(row) or row.step == len(STEPS) or not pause_state(db, order_id):
            raise HTTPException(409, "active paused workflow required")
        save(db, row, {}, "resumed")
        return summary(db, order_id)

    @application.get("/operator/orders/{order_id}/audit")
    def history(order_id: str, credentials: Credentials, db: DatabaseSession):
        operator(credentials)
        rows = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.order_id == order_id, AuditEvent.event_type.like("manual.%")
            )
            .order_by(AuditEvent.created_at)
        ).all()
        return {
            "events": [
                {
                    "event": row.event_type,
                    "at": row.created_at,
                    "details": json.loads(row.details),
                }
                for row in rows
            ]
        }
