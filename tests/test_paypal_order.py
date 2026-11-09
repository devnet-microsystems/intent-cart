import pytest
import httpx
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.main import app, sessions


@pytest.fixture
def client():
    return TestClient(app)


def _setup(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "product_type": "socks", "size": "43", "material": "cotton", "material_percentage": 100,
        "style": "mismatched", "forbidden_designs": ["Christmas"], "max_price": None, "currency": "EUR"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    client.post("/api/mandate/" + sid + "/lock")
    from backend.storage import save_execution
    import uuid
    eid = str(uuid.uuid4())
    offer = {"offer_id": "offer-1", "price": {"price": 10.0, "currency": "EUR"}, "condition": "new"}
    product = {"id": "p1", "title": "Socks", "offers": [offer]}
    save_execution(eid, "2024-01-01T00:00:00Z", sessions[sid]["locked_mandate"], [{"product": product, "gate": {"decision": "APPROVED"}, "offer": offer}], session_id=sid)
    sessions[sid]["execution_id"] = eid
    return sid, eid


def mock_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O1", "links": [{"rel": "payer-action", "href": "https://approve"}]})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)


def test_paypal_order_approved(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    mock_http(monkeypatch)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O1"
    assert data["approve_url"] == "https://approve"
    assert data["status"] == "CREATED"


def test_paypal_order_rejected(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "REJECTED", "rules": [], "policy_version": "2.0"})
    mock_http(monkeypatch)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 403


def test_paypal_order_idempotent(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    mock_http(monkeypatch)
    r1 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    r2 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r1.json()["order_id"] == r2.json()["order_id"]


def test_paypal_order_idempotent_does_not_invent_approve_url(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    mock_http(monkeypatch)
    r1 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    r2 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    # First response had a real PayPal link; idempotent replay must not fabricate one.
    assert r1.json()["approve_url"] == "https://approve"
    assert r2.json()["approve_url"] is None
    assert r2.json()["approve_url"] != "https://approve"


def test_paypal_order_missing_approval_link_returns_explicit_error(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O1", "links": []})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert r.json().get("detail") == "PayPal approval link unavailable"


def test_paypal_order_already_captured_rejected(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    mock_http(monkeypatch)
    # First checkout creates a payment
    r1 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r1.status_code == 200
    # Mark the existing payment as captured
    from backend.storage import update_payment_capture
    update_payment_capture(r1.json()["payment_id"], "CAP1", 10.0, "EUR", "PAYMENT_CAPTURED")
    # Retry checkout on the same payment → must be rejected
    r2 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r2.status_code == 400
    assert r2.json().get("detail") == "Payment already captured"