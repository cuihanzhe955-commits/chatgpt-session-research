import os
from uuid import uuid4

import pytest
from app.config import Settings
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture
def settings(tmp_path):
    postgres = os.getenv("TEST_POSTGRES_URL")
    admin = None
    schema = "test_" + uuid4().hex
    url = "sqlite:///" + str(tmp_path / "test.db")
    if postgres:
        admin = create_engine(postgres)
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        url = (
            make_url(postgres)
            .update_query_dict({"options": "-csearch_path=" + schema})
            .render_as_string(hide_password=False)
        )
    try:
        yield Settings(
            database_url=url,
            vault_key=Fernet.generate_key().decode(),
            webhook_secret="whsec_unit_test",
        )
    finally:
        if admin:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()
