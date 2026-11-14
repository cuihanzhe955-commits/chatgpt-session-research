from datetime import UTC

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import AuditEvent, Order, OrderStatus, utcnow

UNPAID = (
    OrderStatus.CREATED,
    OrderStatus.CHECKOUT_PENDING,
    OrderStatus.PAYMENT_PENDING,
    OrderStatus.FAILED,
)


def expired(order: Order) -> bool:
    return order.expires_at.replace(tzinfo=UTC) <= utcnow()


def expire_due(engine) -> int:
    """Compare-and-set prevents a cleanup job from overwriting a concurrent payment."""
    count = 0
    with Session(engine) as db:
        ids = db.scalars(
            select(Order.id).where(
                Order.expires_at <= utcnow(), Order.status.in_(UNPAID)
            )
        ).all()
        for order_id in ids:
            changed = db.execute(
                update(Order)
                .where(
                    Order.id == order_id,
                    Order.status.in_(UNPAID),
                    Order.expires_at <= utcnow(),
                )
                .values(status=OrderStatus.EXPIRED, credential_ciphertext=None)
            ).rowcount
            if changed:
                db.add(
                    AuditEvent(
                        order_id=order_id, event_type="order.expired", details="{}"
                    )
                )
                count += 1
        db.commit()
    return count
