from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .celery_app import celery
from .models import AuditEvent, Base, Order, OrderStatus
from .main import engine


@celery.task
def expire_orders() -> int:
    now = datetime.now(timezone.utc)
    changed = 0
    with Session(engine) as db:
        orders = db.scalars(select(Order).where(
            Order.expires_at <= now,
            Order.status.in_([OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]),
        )).all()
        for order in orders:
            order.status = OrderStatus.EXPIRED
            db.add(AuditEvent(order_id=order.id, event_type="order.expired", details="{}"))
            changed += 1
        db.commit()
    return changed
