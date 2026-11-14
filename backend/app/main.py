import hmac
import json
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from typing import Annotated
from uuid import uuid4

import stripe
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .adapters import ResearchCheckoutAdapter
from .config import Settings
from .database import make_engine
from .lifecycle import expire_due, expired
from .models import AuditEvent, Order, OrderStatus, utcnow
from .security import CredentialVault, new_order_token, token_hash

PRODUCTS = {
    "research-test": {
        "id": "research-test",
        "name": "测试订单体验",
        "amount_cents": 2000,
        "currency": "usd",
    }
}
bearer = HTTPBearer(auto_error=False)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = "research-test"
    simulated_credential: str = Field(
        pattern=r"^synthetic-[A-Za-z0-9_-]{1,128}$", max_length=138
    )


def session(request: Request):
    with Session(request.app.state.engine) as db:
        yield db


DatabaseSession = Annotated[Session, Depends(session)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def audit(db, event_type, order_id=None, provider_event_id=None, details=None):
    db.add(
        AuditEvent(
            order_id=order_id,
            event_type=event_type,
            provider_event_id=provider_event_id,
            details=json.dumps(details or {}, separators=(",", ":")),
        )
    )


def owned(db, order_id, credentials):
    order = db.get(Order, order_id)
    supplied = (
        credentials.credentials
        if credentials and credentials.scheme.lower() == "bearer"
        else ""
    )
    if (
        not order
        or not supplied
        or not hmac.compare_digest(order.access_token_hash, token_hash(supplied))
    ):
        raise HTTPException(404, "order not found or access token invalid")
    return order


def view(order, mode):
    return {
        "id": order.id,
        "status": order.status,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "amount_cents": order.amount_cents,
        "currency": order.currency,
        "credential_stored": order.credential_ciphertext is not None,
        "expires_at": order.expires_at.replace(tzinfo=UTC),
        "created_at": order.created_at.replace(tzinfo=UTC),
        "payment_mode": mode,
        "checkout_url": order.checkout_url,
        "fulfillment_status": "not_implemented",
    }


def process_event(engine, event):
    supported = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    }
    event_type = event.get("type")
    if not isinstance(event_type, str):
        raise HTTPException(400, "invalid event type")
    if event.get("livemode") is not False:
        raise HTTPException(400, "only test events are accepted")
    if event_type not in supported:
        return {"received": True, "ignored": True}
    event_id = event.get("id")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id) > 255
        or not isinstance(obj, dict)
    ):
        raise HTTPException(400, "invalid event envelope")
    with Session(engine) as db:
        if db.scalar(
            select(AuditEvent.id).where(AuditEvent.provider_event_id == event_id)
        ):
            return {"received": True, "duplicate": True}
        order = db.scalar(select(Order).where(Order.stripe_session_id == obj.get("id")))
        if not order or not obj.get("id"):
            # Retryable: webhook can race the transaction which stores the session id.
            raise HTTPException(409, "checkout session not yet available")
        if (
            obj.get("livemode") is not False
            or obj.get("mode") != "payment"
            or not isinstance(obj.get("metadata"), dict)
            or obj["metadata"].get("order_id") != order.id
            or type(obj.get("amount_total")) is not int
            or obj["amount_total"] != order.amount_cents
            or obj.get("currency") != order.currency
        ):
            raise HTTPException(400, "checkout does not match order")
        if event_type in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }:
            target = OrderStatus.PAID if obj.get("payment_status") == "paid" else None
        else:
            target = (
                OrderStatus.EXPIRED
                if event_type.endswith("expired")
                else OrderStatus.FAILED
            )
        allowed = (
            [OrderStatus.PAYMENT_PENDING, OrderStatus.FAILED, OrderStatus.EXPIRED]
            if target == OrderStatus.PAID
            else [OrderStatus.PAYMENT_PENDING]
        )
        if target:
            db.execute(
                update(Order)
                .where(Order.id == order.id, Order.status.in_(allowed))
                .values(status=target, credential_ciphertext=None)
            )
        audit(db, "stripe." + event_type, order.id, event_id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if db.scalar(
                select(AuditEvent.id).where(AuditEvent.provider_event_id == event_id)
            ):
                return {"received": True, "duplicate": True}
            raise
    return {"received": True, "duplicate": False}


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        settings.validate()
        application.state.engine = make_engine(settings)
        application.state.vault = CredentialVault(settings.vault_key.encode())
        application.state.checkout = ResearchCheckoutAdapter(settings)
        yield
        application.state.engine.dispose()

    application = FastAPI(
        title="Session Research — Test Checkout API",
        version="0.2.0",
        lifespan=lifespan,
        root_path=settings.root_path,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Pydantic's default response echoes input, which may include pasted credentials.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                    for e in exc.errors()
                ]
            },
        )

    @application.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/health")
    def health(db: DatabaseSession):
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "payment_mode": settings.payment_mode,
            "real_fulfillment": False,
        }

    @application.get("/products")
    def products():
        return {
            "products": list(PRODUCTS.values()),
            "payment_mode": settings.payment_mode,
        }

    @application.post("/orders", status_code=201)
    def create_order(payload: OrderCreate, request: Request, db: DatabaseSession):
        product = PRODUCTS.get(payload.product_id)
        if not product:
            raise HTTPException(404, "product not found")
        token = new_order_token()
        order = Order(
            id=str(uuid4()),
            access_token_hash=token_hash(token),
            product_id=product["id"],
            product_name=product["name"],
            amount_cents=product["amount_cents"],
            currency=product["currency"],
            credential_ciphertext=request.app.state.vault.seal(
                payload.simulated_credential
            ),
            expires_at=utcnow() + timedelta(minutes=30),
        )
        db.add(order)
        audit(db, "order.created", order.id)
        db.commit()
        db.refresh(order)
        return {**view(order, settings.payment_mode), "access_token": token}

    @application.get("/orders/{order_id}")
    def get_order(
        order_id: str,
        request: Request,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        expire_due(request.app.state.engine)
        return view(owned(db, order_id, credentials), settings.payment_mode)

    @application.post("/orders/{order_id}/checkout")
    def create_checkout(
        order_id: str,
        request: Request,
        credentials: Credentials,
    ):
        if request.query_params:
            raise HTTPException(
                400,
                "checkout uses the saved order price; query overrides are not accepted",
            )
        engine = request.app.state.engine
        with Session(engine) as db:
            order = owned(db, order_id, credentials)
            if order.status == OrderStatus.PAYMENT_PENDING and not expired(order):
                return view(order, settings.payment_mode)
            if expired(order):
                expire_due(engine)
                raise HTTPException(409, "order expired")
            # A stale reservation may be retried with the same Stripe idempotency key.
            stale = (
                order.status == OrderStatus.CHECKOUT_PENDING
                and order.updated_at.replace(tzinfo=UTC)
                < utcnow() - timedelta(minutes=2)
            )
            if order.status != OrderStatus.CREATED and not stale:
                raise HTTPException(
                    409, "order is not checkoutable or checkout is in progress"
                )
            attempt = str(uuid4())
            changed = db.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.status == order.status,
                    Order.updated_at == order.updated_at,
                )
                .values(
                    status=OrderStatus.CHECKOUT_PENDING,
                    checkout_attempt=attempt,
                    updated_at=utcnow(),
                )
            ).rowcount
            if not changed:
                raise HTTPException(409, "checkout is in progress")
            amount, currency, name = (
                order.amount_cents,
                order.currency,
                order.product_name,
            )
            db.commit()
        try:
            result = request.app.state.checkout.create(order_id, amount, currency, name)
        except (stripe.error.StripeError, OSError, ValueError):
            with Session(engine) as db:
                db.execute(
                    update(Order)
                    .where(
                        Order.id == order_id,
                        Order.status == OrderStatus.CHECKOUT_PENDING,
                        Order.checkout_attempt == attempt,
                    )
                    .values(status=OrderStatus.CREATED)
                )
                audit(db, "checkout.provider_error", order_id)
                db.commit()
            raise HTTPException(
                502, "checkout provider unavailable; retry this order"
            ) from None
        with Session(engine) as db:
            order = db.get(Order, order_id)
            if order.checkout_attempt != attempt:
                raise HTTPException(
                    409, "checkout reservation changed; query this order again"
                )
            order.stripe_session_id = result.session_id
            order.checkout_url = result.checkout_url
            if order.status == OrderStatus.CHECKOUT_PENDING:
                order.status = OrderStatus.PAYMENT_PENDING
            audit(
                db,
                "checkout.created",
                order_id,
                details={"mode": settings.payment_mode},
            )
            db.commit()
            db.refresh(order)
            return view(order, settings.payment_mode)

    @application.post("/webhooks/stripe")
    async def webhook(request: Request):
        if not settings.webhook_secret:
            raise HTTPException(503, "Stripe webhook signing is not configured")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 262144:
                raise HTTPException(413, "webhook too large")
        signature = request.headers.get("stripe-signature")
        if not signature:
            raise HTTPException(400, "missing webhook signature")
        try:
            stripe.Webhook.construct_event(
                bytes(raw), signature, settings.webhook_secret
            )
            event = json.loads(raw)
        except (ValueError, stripe.error.SignatureVerificationError):
            raise HTTPException(400, "invalid webhook signature") from None
        if not isinstance(event, dict):
            raise HTTPException(400, "invalid event")
        return process_event(request.app.state.engine, event)

    @application.post("/orders/{order_id}/simulate-payment")
    def simulate_payment(
        order_id: str,
        request: Request,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        if settings.payment_mode != "offline":
            raise HTTPException(404, "offline simulation is disabled")
        order = owned(db, order_id, credentials)
        if order.status not in {
            OrderStatus.PAYMENT_PENDING,
            OrderStatus.PAID,
        } or expired(order):
            raise HTTPException(409, "create an unexpired checkout first")
        event = {
            "id": "evt_offline_" + order.id,
            "type": "checkout.session.completed",
            "livemode": False,
            "data": {
                "object": {
                    "id": order.stripe_session_id,
                    "livemode": False,
                    "mode": "payment",
                    "metadata": {"order_id": order.id},
                    "amount_total": order.amount_cents,
                    "currency": order.currency,
                    "payment_status": "paid",
                }
            },
        }
        return process_event(request.app.state.engine, event)

    @application.delete("/orders/{order_id}/credential")
    def destroy_credential(
        order_id: str,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        order = owned(db, order_id, credentials)
        order.credential_ciphertext = None
        audit(db, "credential.destroyed", order.id)
        db.commit()
        return {"id": order.id, "credential_destroyed": True}

    @application.post("/orders/{order_id}/cancel")
    def cancel_order(
        order_id: str,
        credentials: Credentials,
        db: DatabaseSession,
    ):
        order = owned(db, order_id, credentials)
        # A browser redirect is not a provider-side cancellation. Do not claim to cancel an active Checkout.
        changed = db.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == OrderStatus.CREATED)
            .values(status=OrderStatus.CANCELLED, credential_ciphertext=None)
        ).rowcount
        if not changed:
            raise HTTPException(
                409, "only orders without a started checkout can be cancelled"
            )
        audit(db, "order.cancelled", order.id)
        db.commit()
        return {"id": order.id, "status": "cancelled"}

    return application


app = create_app()
