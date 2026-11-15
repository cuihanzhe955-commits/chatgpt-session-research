"""Periodic cleanup is independent of the web application's import lifecycle."""

from .celery_app import celery
from .config import Settings
from .database import make_engine
from .lifecycle import expire_due


@celery.task
def expire_orders() -> int:
    settings = Settings.from_env()
    settings.validate()
    engine = make_engine(settings)
    try:
        return expire_due(engine)
    finally:
        engine.dispose()
