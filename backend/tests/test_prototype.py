import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from app.lifecycle import expire_due
from app.main import create_app
from app.models import AuditEvent, Order, utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as instance:
        yield instance


def order(client):
    r = client.post("/orders", json={"simulated_credential": "synthetic-test"})
    assert r.status_code == 201, r.text
    return r.json()


def auth(value):
    return {"Authorization": "Bearer " + value["access_token"]}


def checkout(client, value):
    r = client.post(f"/orders/{value['id']}/checkout", headers=auth(value))
    assert r.status_code == 200, r.text
    return r.json()


def event(client, value, kind="checkout.session.completed", **overrides):
    with Session(client.app.state.engine) as db:
        session_id = db.get(Order, value["id"]).stripe_session_id
    obj = {
        "id": session_id,
        "mode": "payment",
        "livemode": False,
        "metadata": {"order_id": value["id"]},
        "amount_total": 2000,
        "currency": "usd",
        "payment_status": "paid",
    }
    obj.update(overrides)
    return {
        "id": "evt_test_" + uuid4().hex,
        "type": kind,
        "livemode": False,
        "data": {"object": obj},
    }


def send(client, settings, payload, timestamp=None):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(
        settings.webhook_secret.encode(),
        str(timestamp).encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/webhooks/stripe",
        content=raw,
        headers={
            "Stripe-Signature": f"t={timestamp},v1={signature}",
            "Content-Type": "application/json",
        },
    )


def status(client, value):
    return client.get("/orders/" + value["id"], headers=auth(value)).json()


def test_complete_offline_order_and_cleanup(client):
    value = order(client)
    assert value["amount_cents"] == 2000
    assert value["fulfillment_status"] == "manual_tracking_available"
    result = checkout(client, value)
    assert result["checkout_url"] is None
    assert (
        client.post(
            f"/orders/{value['id']}/simulate-payment", headers=auth(value)
        ).status_code
        == 200
    )
    assert status(client, value)["status"] == "paid"
    assert status(client, value)["credential_stored"] is False
    assert client.post(
        f"/orders/{value['id']}/simulate-payment", headers=auth(value)
    ).json()["duplicate"]


@pytest.mark.parametrize(
    "path,method",
    [
        ("", "get"),
        ("/checkout", "post"),
        ("/cancel", "post"),
        ("/credential", "delete"),
        ("/simulate-payment", "post"),
    ],
)
def test_order_access_isolation(client, path, method):
    first, other = order(client), order(client)
    request = getattr(client, method)
    assert request("/orders/" + first["id"] + path).status_code == 404
    assert (
        request("/orders/" + first["id"] + path, headers=auth(other)).status_code == 404
    )


@pytest.mark.parametrize(
    "value",
    [
        "real-token",
        '{"accessToken":"secret"}',
        "eyJhbGciOiJub25lIn0.e30.signature",
        'synthetic-{"access_token":"secret"}',
    ],
)
def test_reject_credentials_without_echo(client, value):
    response = client.post("/orders", json={"simulated_credential": value})
    assert response.status_code == 422
    assert value not in response.text


def test_amount_is_server_owned_and_snapshot_is_saved(client):
    assert (
        client.post(
            "/orders", json={"simulated_credential": "synthetic-x", "amount_cents": 1}
        ).status_code
        == 422
    )
    value = order(client)
    assert (
        client.post(
            "/orders/" + value["id"] + "/checkout?amount_cents=1", headers=auth(value)
        ).status_code
        == 400
    )
    checkout(client, value)
    assert status(client, value)["amount_cents"] == 2000
    with Session(client.app.state.engine) as db:
        saved = db.get(Order, value["id"])
        assert saved.access_token_hash != value["access_token"]
        assert b"synthetic-test" not in saved.credential_ciphertext
        assert (
            db.scalar(select(AuditEvent).where(AuditEvent.order_id == value["id"]))
            is not None
        )


def test_checkout_is_idempotent(client):
    value = order(client)
    first = checkout(client, value)
    second = checkout(client, value)
    assert first["checkout_url"] == second["checkout_url"]
    with Session(client.app.state.engine) as db:
        assert (
            len(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.order_id == value["id"],
                        AuditEvent.event_type == "checkout.created",
                    )
                ).all()
            )
            == 1
        )


def test_provider_failure_can_retry_same_order(client, monkeypatch):
    value = order(client)
    adapter = client.app.state.checkout
    original = adapter.create
    monkeypatch.setattr(
        adapter,
        "create",
        lambda *a: (_ for _ in ()).throw(TimeoutError("secret provider details")),
    )
    response = client.post("/orders/" + value["id"] + "/checkout", headers=auth(value))
    assert response.status_code == 502
    assert "secret provider details" not in response.text
    assert status(client, value)["status"] == "created"
    monkeypatch.setattr(adapter, "create", original)
    checkout(client, value)


def test_signed_webhook_is_idempotent(client, settings):
    value = order(client)
    checkout(client, value)
    payload = event(client, value)
    assert send(client, settings, payload).json() == {
        "received": True,
        "duplicate": False,
    }
    assert send(client, settings, payload).json() == {
        "received": True,
        "duplicate": True,
    }
    assert status(client, value)["status"] == "paid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_total": 1},
        {"amount_total": True},
        {"currency": "eur"},
        {"metadata": {"order_id": "other"}},
        {"metadata": None},
        {"livemode": True},
        {"mode": "subscription"},
    ],
)
def test_webhook_must_match_saved_order(client, settings, overrides):
    value = order(client)
    checkout(client, value)
    assert send(client, settings, event(client, value, **overrides)).status_code == 400
    assert status(client, value)["status"] == "payment_pending"


def test_unsigned_bad_signature_and_stale_signature(client, settings):
    value = order(client)
    checkout(client, value)
    payload = event(client, value)
    assert client.post("/webhooks/stripe", json=payload).status_code == 400
    assert (
        client.post(
            "/webhooks/stripe", json=payload, headers={"Stripe-Signature": "t=1,v1=bad"}
        ).status_code
        == 400
    )
    assert send(client, settings, payload, timestamp=1).status_code == 400
    assert status(client, value)["status"] == "payment_pending"


def test_no_webhook_secret_never_accepts_unsigned(settings):
    with TestClient(create_app(replace(settings, webhook_secret=""))) as client:
        assert (
            client.post(
                "/webhooks/stripe", json={"type": "checkout.session.completed"}
            ).status_code
            == 503
        )


def test_unpaid_completion_does_not_mark_paid(client, settings):
    value = order(client)
    checkout(client, value)
    assert (
        send(
            client, settings, event(client, value, payment_status="unpaid")
        ).status_code
        == 200
    )
    assert status(client, value)["status"] == "payment_pending"
    assert (
        send(
            client,
            settings,
            event(client, value, "checkout.session.async_payment_succeeded"),
        ).status_code
        == 200
    )
    assert status(client, value)["status"] == "paid"


def test_out_of_order_event_cannot_reverse_payment(client, settings):
    value = order(client)
    checkout(client, value)
    send(client, settings, event(client, value))
    assert (
        send(
            client,
            settings,
            event(client, value, "checkout.session.expired", payment_status="unpaid"),
        ).status_code
        == 200
    )
    assert status(client, value)["status"] == "paid"


def test_expiry_wipes_ciphertext_and_late_payment_is_recorded(client, settings):
    value = order(client)
    checkout(client, value)
    with Session(client.app.state.engine) as db:
        db.execute(
            update(Order)
            .where(Order.id == value["id"])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert expire_due(client.app.state.engine) == 1
    assert expire_due(client.app.state.engine) == 0
    assert status(client, value)["status"] == "expired"
    assert not status(client, value)["credential_stored"]
    assert send(client, settings, event(client, value)).status_code == 200
    assert status(client, value)["status"] == "paid"


def test_cancel_only_before_checkout_and_destroy_is_idempotent(client):
    value = order(client)
    assert (
        client.post(
            "/orders/" + value["id"] + "/cancel", headers=auth(value)
        ).status_code
        == 200
    )
    assert not status(client, value)["credential_stored"]
    assert (
        client.post(
            "/orders/" + value["id"] + "/checkout", headers=auth(value)
        ).status_code
        == 409
    )
    other = order(client)
    checkout(client, other)
    assert (
        client.post(
            "/orders/" + other["id"] + "/cancel", headers=auth(other)
        ).status_code
        == 409
    )
    for _ in range(2):
        assert (
            client.delete(
                "/orders/" + other["id"] + "/credential", headers=auth(other)
            ).status_code
            == 200
        )


def test_test_mode_configuration_and_simulator_guard(settings):
    with pytest.raises(ValueError, match="test secret"):
        replace(settings, stripe_secret_key="sk_live_forbidden").validate()
    with pytest.raises(ValueError, match="persistent"):
        replace(settings, vault_key="").validate()
    with pytest.raises(ValueError, match="requires"):
        replace(settings, payment_mode="stripe_test").validate()
    with TestClient(
        create_app(
            replace(
                settings, payment_mode="stripe_test", stripe_secret_key="sk_test_fake"
            )
        )
    ) as client:
        value = order(client)
        assert (
            client.post(
                "/orders/" + value["id"] + "/simulate-payment", headers=auth(value)
            ).status_code
            == 404
        )


def test_stripe_adapter_uses_saved_price_and_idempotency(settings, monkeypatch):
    from app.adapters import ResearchCheckoutAdapter

    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return type(
            "Checkout",
            (),
            {"id": "cs_test_sdk", "url": "https://checkout.stripe.com/test"},
        )()

    monkeypatch.setattr("stripe.checkout.Session.create", fake)
    result = ResearchCheckoutAdapter(
        replace(settings, payment_mode="stripe_test", stripe_secret_key="sk_test_fake")
    ).create("order-1", 2000, "usd", "Test")
    assert result.session_id == "cs_test_sdk"
    assert captured["idempotency_key"] == "research-checkout-order-1"
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 2000


def test_health_and_cors(client):
    assert client.get("/health").json()["status"] == "ok"
    response = client.options(
        "/orders",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    blocked = client.options(
        "/orders",
        headers={
            "Origin": "https://unknown.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_concurrent_checkout_does_not_create_two_sessions(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    value = order(client)
    entered, release = Event(), Event()
    original = client.app.state.checkout.create
    calls = []

    def slow(*args):
        calls.append(args)
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(client.app.state.checkout, "create", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post, "/orders/" + value["id"] + "/checkout", headers=auth(value)
        )
        assert entered.wait(timeout=10)
        try:
            second = client.post(
                "/orders/" + value["id"] + "/checkout", headers=auth(value)
            )
            assert second.status_code == 409
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    assert len(calls) == 1


def test_concurrent_duplicate_webhook(client, settings):
    from concurrent.futures import ThreadPoolExecutor

    value = order(client)
    checkout(client, value)
    payload = event(client, value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(client, settings, payload), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["duplicate"] for r in responses) == [False, True]
    assert status(client, value)["status"] == "paid"


@pytest.mark.parametrize("kind", ["invoice.paid", "customer.created"])
def test_unrelated_events_do_not_change_order(client, settings, kind):
    value = order(client)
    checkout(client, value)
    assert send(client, settings, event(client, value, kind)).json()["ignored"]
    assert status(client, value)["status"] == "payment_pending"


def test_expired_checkout_is_refused(client):
    value = order(client)
    with Session(client.app.state.engine) as db:
        db.execute(
            update(Order)
            .where(Order.id == value["id"])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert (
        client.post(
            "/orders/" + value["id"] + "/checkout", headers=auth(value)
        ).status_code
        == 409
    )
    assert not status(client, value)["credential_stored"]


def test_persistent_vault_key_survives_restart(settings):
    from app.security import CredentialVault

    first = CredentialVault(settings.vault_key.encode())
    sealed = first.seal("synthetic-restart")
    second = CredentialVault(settings.vault_key.encode())
    assert second._fernet.decrypt(sealed) == b"synthetic-restart"


def test_stale_checkout_failure_cannot_reset_newer_success(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    value = order(client)
    entered, release = Event(), Event()
    lock = Lock()
    calls = 0
    original = client.app.state.checkout.create

    def provider(*args):
        nonlocal calls
        with lock:
            calls += 1
            first = calls == 1
        if first:
            entered.set()
            assert release.wait(timeout=10)
            raise TimeoutError()
        return original(*args)

    monkeypatch.setattr(client.app.state.checkout, "create", provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post, "/orders/" + value["id"] + "/checkout", headers=auth(value)
        )
        assert entered.wait(timeout=10)
        try:
            with Session(client.app.state.engine) as db:
                db.execute(
                    update(Order)
                    .where(Order.id == value["id"])
                    .values(updated_at=utcnow() - timedelta(minutes=3))
                )
                db.commit()
            checkout(client, value)
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 502
    assert status(client, value)["status"] == "payment_pending"
