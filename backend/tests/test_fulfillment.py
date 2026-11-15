from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, timedelta
from threading import Barrier

import pytest
from app import fulfillment
from app.fulfillment import CONSENT_VERSION, STEPS
from app.lifecycle import expire_due
from app.main import create_app
from app.models import AuditEvent, Fulfillment, utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ADMIN = {"Authorization": "Bearer " + "operator-test-" * 4}


@pytest.fixture
def client(settings):
    with TestClient(
        create_app(replace(settings, operator_token="operator-test-" * 4))
    ) as value:
        yield value


def paid(client):
    order = client.post(
        "/orders", json={"simulated_credential": "synthetic-flow"}
    ).json()
    oid = order["id"]
    owner = {"Authorization": "Bearer " + order["access_token"]}
    assert client.post(f"/orders/{oid}/checkout", headers=owner).status_code == 200
    assert (
        client.post(f"/orders/{oid}/simulate-payment", headers=owner).status_code == 200
    )
    return oid, owner


def authorized(client):
    oid, owner = paid(client)
    assert (
        client.post(
            f"/orders/{oid}/consent",
            headers=owner,
            json={"version": CONSENT_VERSION, "accepted": True},
        ).status_code
        == 200
    )
    return oid, owner


def attest(client, oid, action):
    return client.post(
        f"/operator/orders/{oid}/attest",
        headers=ADMIN,
        json={"action": action, "confirmed": True, "evidence_ref": "receipt-001"},
    )


def upload(client, oid, owner):
    return client.post(
        f"/orders/{oid}/login-credential",
        headers=owner,
        json={"credential_type": "session", "session": "unit-session-secret-only"},
    )


def pause(client, oid, reason="other", headers=ADMIN):
    return client.post(
        f"/operator/orders/{oid}/pause", headers=headers, json={"reason": reason}
    )


def resume(client, oid, headers=ADMIN):
    return client.post(f"/operator/orders/{oid}/resume", headers=headers)


def test_consent_requires_payment_and_explicit_current_version(client):
    order = client.post(
        "/orders", json={"simulated_credential": "synthetic-new"}
    ).json()
    path = f"/orders/{order['id']}/consent"
    headers = {"Authorization": "Bearer " + order["access_token"]}
    assert (
        client.post(
            path, headers=headers, json={"version": CONSENT_VERSION, "accepted": True}
        ).status_code
        == 409
    )
    oid, owner = paid(client)
    path = f"/orders/{oid}/consent"
    assert (
        client.post(
            path, headers=owner, json={"version": "old", "accepted": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=owner, json={"version": CONSENT_VERSION, "accepted": False}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=owner, json={"version": CONSENT_VERSION, "accepted": True}
        ).status_code
        == 200
    )
    assert (
        client.post(
            path, headers=owner, json={"version": CONSENT_VERSION, "accepted": True}
        ).status_code
        == 409
    )


def test_order_owner_cannot_act_as_operator(client):
    oid, owner = authorized(client)
    assert client.get("/operator/orders", headers=owner).status_code == 403
    assert (
        client.post(
            f"/operator/orders/{oid}/attest",
            headers=owner,
            json={
                "action": "login_confirmed",
                "confirmed": True,
                "evidence_ref": "ref-1",
            },
        ).status_code
        == 403
    )
    assert client.get(f"/orders/{oid}/fulfillment").status_code == 404


def test_complete_single_purchase_in_order_and_secret_absent(client):
    oid, owner = authorized(client)
    assert upload(client, oid, owner).status_code == 200
    assert attest(client, oid, "completed").status_code == 409
    reveal = client.post(f"/operator/orders/{oid}/reveal-credential", headers=ADMIN)
    assert reveal.json()["session"] == "unit-session-secret-only"
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    for action in STEPS:
        result = attest(client, oid, action)
        assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert result.json()["credential_stored"] is False
    assert attest(client, oid, "completed").status_code == 409
    assert upload(client, oid, owner).status_code == 409
    audit = client.get(f"/operator/orders/{oid}/audit", headers=ADMIN).text
    assert "unit-session-secret-only" not in audit and "example.invalid" not in audit
    assert "payment_method_removed" in audit and "renewal_cancelled" in audit
    assert client.get(f"/orders/{oid}/fulfillment", headers=owner).json()[
        "steps_completed"
    ] == list(STEPS)


def test_unconsumed_credentials_block_completion(client):
    oid, owner = authorized(client)
    upload(client, oid, owner)
    for action in STEPS[:-1]:
        assert attest(client, oid, action).status_code == 200
    assert attest(client, oid, "completed").status_code == 409
    assert (
        client.delete(f"/orders/{oid}/login-credential", headers=owner).status_code
        == 200
    )
    assert attest(client, oid, "completed").status_code == 200


def test_encrypted_at_rest_and_read_once(client):
    oid, owner = authorized(client)
    result = upload(client, oid, owner)
    assert "unit-session-secret-only" not in result.text
    with Session(client.app.state.engine) as db:
        row = db.get(Fulfillment, oid)
        assert b"unit-session-secret-only" not in row.credential_ciphertext
        assert b"example.invalid" not in row.credential_ciphertext
    client.post(f"/operator/orders/{oid}/reveal-credential", headers=ADMIN)
    with Session(client.app.state.engine) as db:
        assert db.get(Fulfillment, oid).credential_ciphertext is None


def test_expiry_worker_deletes_credentials(client):
    oid, owner = authorized(client)
    upload(client, oid, owner)
    with Session(client.app.state.engine) as db:
        db.get(Fulfillment, oid).credential_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expire_due(client.app.state.engine)
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    with Session(client.app.state.engine) as db:
        assert db.get(Fulfillment, oid).credential_ciphertext is None
        assert db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "manual.credential_expired"
            )
        )


@pytest.mark.parametrize("revoke", [True, False])
def test_revocation_or_authorization_expiry_stops_login_and_payment(client, revoke):
    oid, owner = authorized(client)
    upload(client, oid, owner)
    if revoke:
        assert (
            client.post(f"/orders/{oid}/revoke-consent", headers=owner).status_code
            == 200
        )
    else:
        with Session(client.app.state.engine) as db:
            db.get(Fulfillment, oid).authorized_until = utcnow() - timedelta(seconds=1)
            db.commit()
    assert attest(client, oid, "login_confirmed").status_code == 409
    assert attest(client, oid, "membership_active").status_code == 409
    assert attest(client, oid, "completed").status_code == 409
    assert upload(client, oid, owner).status_code == 409
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    for action in ["renewal_cancelled", "payment_method_removed", "logged_out"]:
        assert attest(client, oid, action).status_code == 200
    flow = client.get(f"/orders/{oid}/fulfillment", headers=owner).json()
    assert flow["credential_stored"] is False
    assert flow["steps_completed"] == []  # cleanup must not fabricate a purchase
    assert "cleanup_required" in flow["status"]


def test_validation_does_not_echo_secrets(client):
    oid, owner = authorized(client)
    result = client.post(
        f"/orders/{oid}/login-credential",
        headers=owner,
        json={"session": "", "token": "do-not-echo"},
    )
    assert result.status_code == 422
    assert "do-not-echo" not in result.text


@pytest.mark.parametrize(
    "value", ["opaque-session-test", '{"sessionToken":"json-test-value"}']
)
def test_session_text_roundtrip_and_single_use(client, value):
    oid, owner = authorized(client)
    result = client.post(
        f"/orders/{oid}/login-credential",
        headers=owner,
        json={"credential_type": "session", "session": value},
    )
    assert result.status_code == 200
    assert value not in result.text
    response = client.post(f"/operator/orders/{oid}/reveal-credential", headers=ADMIN)
    assert response.json() == {"credential_type": "session", "session": value}
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    audit = client.get(f"/operator/orders/{oid}/audit", headers=ADMIN).text
    assert value not in audit


@pytest.mark.parametrize(
    "payload",
    [
        {"session": "   "},
        {"session": ""},
        {"session": "x" * 32769},
        {"account": "old-account", "password": "old-password"},
        {"credential_type": "password", "session": "do-not-echo"},
    ],
)
def test_invalid_or_legacy_credential_payload_rejected(client, payload):
    oid, owner = authorized(client)
    response = client.post(
        f"/orders/{oid}/login-credential", headers=owner, json=payload
    )
    assert response.status_code == 422
    for secret in ("old-password", "old-account", "do-not-echo", "x" * 100):
        assert secret not in response.text


def test_session_requires_new_consent_version(client):
    oid, owner = authorized(client)
    with Session(client.app.state.engine) as db:
        db.get(Fulfillment, oid).consent_version = "single-purchase-v1"
        db.commit()
    assert upload(client, oid, owner).status_code == 409
    assert attest(client, oid, "login_confirmed").status_code == 409
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    assert (
        client.get(f"/orders/{oid}/fulfillment", headers=owner).json()["status"]
        == "legacy_consent_cleanup_required"
    )


def test_disabled_operator_rejects_consent_and_secret(settings):
    with TestClient(create_app(settings)) as client:
        oid, owner = paid(client)
        assert client.get("/operator/orders", headers=ADMIN).status_code == 503
        assert (
            client.post(
                f"/orders/{oid}/consent",
                headers=owner,
                json={"version": CONSENT_VERSION, "accepted": True},
            ).status_code
            == 503
        )
        assert upload(client, oid, owner).status_code == 503


def test_pause_requires_operator_and_valid_reason(client):
    oid, owner = authorized(client)
    assert pause(client, oid, headers=owner).status_code == 403
    assert pause(client, oid, reason="not-a-reason").status_code == 422
    result = pause(client, oid, reason="verification_required")
    assert result.status_code == 200
    assert result.json()["status"] == "paused"
    assert result.json()["pause_reason"] == "verification_required"
    assert pause(client, oid).status_code == 409


def test_pause_deletes_credentials_and_blocks_work_until_resume(client):
    oid, owner = authorized(client)
    before = client.get(f"/orders/{oid}/fulfillment", headers=owner).json()
    assert upload(client, oid, owner).status_code == 200
    result = pause(client, oid, reason="payment_failed")
    assert result.status_code == 200
    paused = result.json()
    assert paused["credential_stored"] is False
    assert paused["credential_deleted_at"] is not None
    assert upload(client, oid, owner).status_code == 409
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    for action in ["login_confirmed", "membership_active", "renewal_cancelled", "completed"]:
        assert attest(client, oid, action).status_code == 409

    result = resume(client, oid)
    assert result.status_code == 200
    resumed = result.json()
    assert resumed["status"] == "in_progress"
    assert resumed["pause_reason"] is None
    assert resumed["authorized_until"] == before["authorized_until"]
    assert resumed["credential_stored"] is False
    assert resumed["credential_deleted_at"] == paused["credential_deleted_at"]
    assert resume(client, oid).status_code == 409
    assert (
        client.post(
            f"/operator/orders/{oid}/reveal-credential", headers=ADMIN
        ).status_code
        == 409
    )
    assert upload(client, oid, owner).status_code == 200


def test_paused_cleanup_advances_only_at_cleanup_stage(client):
    oid, _ = authorized(client)
    for action in STEPS[:2]:
        assert attest(client, oid, action).status_code == 200
    assert pause(client, oid, reason="renewal_cleanup_failed").status_code == 200
    for action in STEPS[2:5]:
        result = attest(client, oid, action)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "paused"
    assert result.json()["next_step"] == "completed"
    assert attest(client, oid, "completed").status_code == 409
    assert resume(client, oid).status_code == 200
    result = attest(client, oid, "completed")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["pause_reason"] is None
    assert pause(client, oid).status_code == 409
    assert resume(client, oid).status_code == 409


@pytest.mark.parametrize("revoke", [True, False])
def test_paused_workflow_cannot_resume_after_revoke_or_expiry(client, revoke):
    oid, owner = authorized(client)
    assert pause(client, oid).status_code == 200
    if revoke:
        assert (
            client.post(f"/orders/{oid}/revoke-consent", headers=owner).status_code
            == 200
        )
        expected = "revoked_cleanup_required"
    else:
        with Session(client.app.state.engine) as db:
            db.get(Fulfillment, oid).authorized_until = utcnow() - timedelta(seconds=1)
            db.commit()
        expected = "authorization_expired_cleanup_required"
    flow = client.get(f"/orders/{oid}/fulfillment", headers=owner).json()
    assert flow["status"] == expected
    assert flow["pause_reason"] is None
    assert resume(client, oid).status_code == 409
    assert pause(client, oid).status_code == 409


def test_pause_resume_order_is_deterministic_with_frozen_clock(client, monkeypatch):
    oid, _ = authorized(client)
    fixed = utcnow()
    monkeypatch.setattr(fulfillment, "utcnow", lambda: fixed)
    assert pause(client, oid).status_code == 200
    assert resume(client, oid).status_code == 200
    with Session(client.app.state.engine) as db:
        events = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.order_id == oid,
                AuditEvent.event_type.in_(["manual.paused", "manual.resumed"]),
            )
            .order_by(AuditEvent.created_at)
        ).all()
    assert [event.event_type for event in events] == ["manual.paused", "manual.resumed"]
    assert events[0].created_at.replace(tzinfo=UTC) == fixed
    assert events[1].created_at.replace(tzinfo=UTC) == fixed + timedelta(microseconds=1)


def test_simultaneous_pause_requests_allow_only_one_transition(client):
    oid, owner = authorized(client)
    assert upload(client, oid, owner).status_code == 200
    barrier = Barrier(2)

    def request_pause():
        barrier.wait()
        return pause(client, oid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request_pause(), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    flow = client.get(f"/orders/{oid}/fulfillment", headers=owner).json()
    assert flow["status"] == "paused"
    assert flow["credential_stored"] is False
    events = client.get(f"/operator/orders/{oid}/audit", headers=ADMIN).json()["events"]
    assert sum(event["event"] == "manual.paused" for event in events) == 1
