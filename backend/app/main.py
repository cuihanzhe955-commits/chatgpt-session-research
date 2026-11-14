import os
import json
from datetime import timedelta
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
import stripe
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from .adapters import ResearchCheckoutAdapter
from .models import AuditEvent, Base, Order, OrderStatus, utcnow
from .security import CredentialVault


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./research.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Base.metadata.create_all(engine)
vault = CredentialVault()
checkout = ResearchCheckoutAdapter()
app = FastAPI(title="ChatGPT Session Research Prototype")


def audit(db: Session, event_type: str, order_id: str | None = None,
          provider_event_id: str | None = None, details: dict | None = None) -> None:
    db.add(AuditEvent(
        order_id=order_id,
        event_type=event_type,
        provider_event_id=provider_event_id,
        details=json.dumps(details or {}, separators=(",", ":")),
    ))


class OrderCreate(BaseModel):
    simulated_credential: str = Field(min_length=1, description="Synthetic value only")
    amount_cents: int = Field(default=2000, ge=1)


@app.post("/orders")
def create_order(payload: OrderCreate):
    with Session(engine) as db:
        order = Order(
            credential_ciphertext=vault.seal(payload.simulated_credential),
            expires_at=utcnow() + timedelta(minutes=30),
        )
        db.add(order)
        audit(db, "order.created", order.id)
        db.commit()
        db.refresh(order)
        return {"id": order.id, "status": order.status, "expires_at": order.expires_at}


@app.post("/orders/{order_id}/checkout")
def create_checkout(order_id: str, amount_cents: int = 2000):
    with Session(engine) as db:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "order not found")
        if order.status != OrderStatus.CREATED:
            raise HTTPException(409, "order is not checkoutable")
        result = checkout.create(order.id, amount_cents)
        order.stripe_session_id = result.session_id
        order.status = OrderStatus.PAYMENT_PENDING
        audit(db, "checkout.created", order.id, details={"provider": "stripe_test"})
        db.commit()
        return {"id": order.id, "status": order.status, "checkout_url": result.checkout_url}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    raw_body = await request.body()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if secret:
        if not stripe_signature:
            raise HTTPException(400, "missing webhook signature")
        try:
            body = stripe.Webhook.construct_event(raw_body, stripe_signature, secret)
        except (ValueError, stripe.error.SignatureVerificationError) as exc:
            raise HTTPException(400, "invalid webhook signature") from exc
    else:
        body = await request.json()
    event_type = body.get("type")
    provider_event_id = body.get("id")
    session_id = body.get("data", {}).get("object", {}).get("id")
    with Session(engine) as db:
        order = db.scalar(select(Order).where(Order.stripe_session_id == session_id))
        if not order:
            raise HTTPException(404, "checkout session not found")
        if provider_event_id and db.scalar(select(AuditEvent).where(AuditEvent.provider_event_id == provider_event_id)):
            return {"received": True, "duplicate": True}
        if event_type in {"checkout.session.completed", "invoice.paid"}:
            order.status = OrderStatus.PAID
        elif event_type in {"checkout.session.expired", "invoice.payment_failed"}:
            order.status = OrderStatus.EXPIRED if "expired" in event_type else OrderStatus.FAILED
        audit(db, f"stripe.{event_type}", order.id, provider_event_id)
        db.commit()
    return {"received": True, "duplicate": False}


@app.delete("/orders/{order_id}/credential")
def destroy_credential(order_id: str):
    with Session(engine) as db:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "order not found")
        order.credential_ciphertext = None
        audit(db, "credential.destroyed", order.id)
        db.commit()
        return {"id": order.id, "credential_destroyed": True}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with Session(engine) as db:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "order not found")
        return {
            "id": order.id,
            "status": order.status,
            "stripe_session_id": order.stripe_session_id,
            "credential_stored": order.credential_ciphertext is not None,
            "expires_at": order.expires_at,
        }


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str):
    with Session(engine) as db:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "order not found")
        if order.status in {OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.EXPIRED}:
            raise HTTPException(409, "order cannot be cancelled")
        order.status = OrderStatus.CANCELLED
        order.credential_ciphertext = None
        audit(db, "order.cancelled", order.id)
        db.commit()
        return {"id": order.id, "status": order.status}
