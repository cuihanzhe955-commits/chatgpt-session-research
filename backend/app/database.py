from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from .config import Settings
from .models import Base


def make_engine(settings: Settings):
    options = {}
    if settings.database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
            options["poolclass"] = StaticPool
    engine = create_engine(settings.database_url, **options)
    Base.metadata.create_all(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("orders")}
    required = {
        "access_token_hash",
        "product_id",
        "amount_cents",
        "currency",
        "checkout_url",
        "checkout_attempt",
    }
    if not required <= columns:
        engine.dispose()
        raise RuntimeError(
            "Legacy orders schema detected. Back up the database and use a new database; see docs/INTEGRATION.md before migration."
        )
    return engine
