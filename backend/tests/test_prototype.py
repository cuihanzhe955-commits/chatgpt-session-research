import base64
import json
from fastapi.testclient import TestClient
from app.main import app, vault


client = TestClient(app)


def jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "synthetic", "exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.signature"


def test_jwt_expiry_metadata():
    assert vault.inspect_jwt_metadata(jwt(100), now=101)["expired"] is True
    assert vault.inspect_jwt_metadata(jwt(200), now=101)["expired"] is False


def test_order_checkout_and_idempotent_webhook():
    order = client.post("/orders", json={"simulated_credential": "synthetic-token"}).json()
    checkout = client.post(f"/orders/{order['id']}/checkout").json()
    event = {"type": "checkout.session.completed", "data": {"object": {"id": checkout["checkout_url"].split("/")[-2]}}}
    # The adapter's session id is intentionally not exposed in the URL; obtain it through
    # the deterministic test contract by using the persisted order's returned status path.
    from app.main import engine
    from app.models import Order
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        session_id = db.get(Order, order["id"]).stripe_session_id
    event["data"]["object"]["id"] = session_id
    assert client.post("/webhooks/stripe", json=event).json() == {"received": True}
    assert client.post("/webhooks/stripe", json=event).json() == {"received": True}


def test_credential_destruction():
    order = client.post("/orders", json={"simulated_credential": "synthetic-token"}).json()
    response = client.delete(f"/orders/{order['id']}/credential")
    assert response.json()["credential_destroyed"] is True
