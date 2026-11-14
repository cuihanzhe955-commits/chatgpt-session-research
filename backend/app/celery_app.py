import os
from celery import Celery

celery = Celery(
    "research",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
celery.conf.timezone = "UTC"
