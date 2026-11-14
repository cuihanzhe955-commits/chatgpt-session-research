import os

from celery import Celery

celery = Celery(
    "research",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
celery.conf.timezone = "UTC"
celery.conf.imports = ("app.tasks",)
celery.conf.beat_schedule = {
    "expire-orders-every-minute": {"task": "app.tasks.expire_orders", "schedule": 60.0},
}
