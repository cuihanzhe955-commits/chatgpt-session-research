import os
from datetime import timedelta
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
import stripe
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from .adapters import ResearchCheckoutAdapter
from .models import Base, Order, OrderStatus, utcnow
from .security import CredentialVault


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./research.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Base.metadata.create_all(engine)
vault = CredentialVault()
checkout = ResearchCheckoutAdapter()
app = FastAPI(title="ChatGPT Session Research Prototype")


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
    session_id = body.get("data", {}).get("object", {}).get("id")
    with Session(engine) as db:
        order = db.scalar(select(Order).where(Order.stripe_session_id == session_id))
        if not order:
            raise HTTPException(404, "checkout session not found")
        if event_type in {"checkout.session.completed", "invoice.paid"}:
            order.status = OrderStatus.PAID
        elif event_type in {"checkout.session.expired", "invoice.payment_failed"}:
            order.status = OrderStatus.EXPIRED if "expired" in event_type else OrderStatus.FAILED
        db.commit()
    return {"received": True}


@app.delete("/orders/{order_id}/credential")
def destroy_credential(order_id: str):
    with Session(engine) as db:
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "order not found")
        order.credential_ciphertext = None
        db.commit()
        return {"id": order.id, "credential_destroyed": True}
