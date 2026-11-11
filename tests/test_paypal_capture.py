import pytest
import httpx
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.main import app, sessions

_REAL_ASYNC_CLIENT = httpx.AsyncClient


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
    monkeypatch.setattr("backend.main.evaluate", lambda m, p, att=None: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    from backend.storage import save_execution
    import uuid
    eid = str(uuid.uuid4())
    offer = {"offer_id": "offer-1", "price": {"price": 10.0, "currency": "EUR"}, "condition": "new"}
    product = {"id": "p1", "title": "Socks", "offers": [offer]}
    save_execution(eid, "2024-01-01T00:00:00Z", sessions[sid]["locked_mandate"], [{"product": product, "gate": {"decision": "APPROVED"}, "offer": offer}], session_id=sid)
    sessions[sid]["execution_id"] = eid
    # create payment via checkout
    monkeypatch.setattr("backend.main.evaluate", lambda m, p, att=None: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O1", "links": [{"rel": "payer-action", "href": "https://approve"}]})
        if url.endswith("/O1") and request.method == "GET":
            return httpx.Response(200, json={"id": "O1", "status": "APPROVED", "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "EUR", "value": "10.00"}}]})
        if url.endswith("/capture"):
                return httpx.Response(201, json={"id": "O1", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "amount": {"value": "10.00", "currency_code": "EUR"}}]}}]})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = _REAL_ASYNC_CLIENT
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    return sid, eid, r.json()["payment_id"] if "payment_id" in r.json() else None, r.json()["order_id"]


def test_capture_success(client, monkeypatch):
    sid, eid, pid, oid = _setup(client, monkeypatch)
    # pid not returned by checkout; fetch from db
    from backend.storage import get_payment_by_key
    key = f"{sid}:{eid}:offer-1"
    pay = get_payment_by_key(key)
    pid = pay["id"]
    # mock get order approved and capture
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if url.endswith("/O1") and request.method == "GET":
            return httpx.Response(200, json={"id": "O1", "status": "APPROVED", "intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "EUR", "value": "10.00"}}]})
        if url.endswith("/capture"):
                return httpx.Response(201, json={"id": "O1", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "amount": {"value": "10.00", "currency_code": "EUR"}}]}}]})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = _REAL_ASYNC_CLIENT
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/capture", json={"payment_id": pid})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "PAYMENT_CAPTURED"
    assert data["capture_id"] == "CAP1"


def test_capture_invalid_order(client, monkeypatch):
    sid, eid, _, _ = _setup(client, monkeypatch)
    from backend.storage import get_payment_by_key
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    pid = pay["id"]
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if url.endswith("/O1") and request.method == "GET":
            return httpx.Response(200, json={"id": "O1", "status": "CREATED"})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = _REAL_ASYNC_CLIENT
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/capture", json={"payment_id": pid})
    assert r.status_code == 400


def test_capture_idempotent(client, monkeypatch):
    sid, eid, _, _ = _setup(client, monkeypatch)
    from backend.storage import get_payment_by_key, update_payment_capture
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    pid = pay["id"]
    update_payment_capture(pid, "CAP1", 10.0, "EUR", "PAYMENT_CAPTURED")
    r = client.post("/api/orders/capture", json={"payment_id": pid})
    assert r.status_code == 200
    assert r.json()["capture_id"] == "CAP1"

def test_capture_idempotent_no_second_call(client, monkeypatch):
    sid, eid, _, _ = _setup(client, monkeypatch)
    from backend.storage import get_payment_by_key, update_payment_capture
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    pid = pay["id"]
    update_payment_capture(pid, "CAP1", 10.0, "EUR", "PAYMENT_CAPTURED")
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={"error": "should not be called"})
    transport = httpx.MockTransport(handler)
    original = _REAL_ASYNC_CLIENT
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/capture", json={"payment_id": pid})
    assert r.status_code == 200
    assert r.json()["capture_id"] == "CAP1"
    assert calls["n"] == 0


def test_direct_order_capture_disabled_sandbox_only(client, monkeypatch):
    import backend.config as config
    monkeypatch.setattr(config.settings, "paypal_environment", "sandbox")
    r = client.post("/api/orders/O1/capture")
    assert r.status_code == 410
    assert "payment_id" in r.json().get("detail", "")


def test_direct_order_capture_forbidden_outside_sandbox(client, monkeypatch):
    import backend.config as config
    monkeypatch.setattr(config.settings, "paypal_environment", "live")
    r = client.post("/api/orders/O1/capture")
    assert r.status_code == 400
    assert r.json().get("detail") == "Only sandbox mode is allowed"


def test_orders_test_endpoint_sandbox_only(client, monkeypatch):
    import backend.config as config
    monkeypatch.setattr(config.settings, "paypal_environment", "live")
    r = client.post("/api/orders/test")
    assert r.status_code == 400
    assert r.json().get("detail") == "Only sandbox mode is allowed"


def test_capture_rejects_blocked_offer_after_session_state(client, monkeypatch):
    sid, eid, _, _ = _setup(client, monkeypatch)
    from backend.storage import get_payment_by_key
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    pid = pay["id"]
    # Offer becomes blocked at capture time: server-side re-evaluation must stop it.
    monkeypatch.setattr("backend.main.evaluate", lambda m, p, att=None: {"decision": "REJECTED", "rules": [], "policy_version": "2.0"})
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={"error": "should not capture blocked offer"})
    transport = httpx.MockTransport(handler)
    original = _REAL_ASYNC_CLIENT
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/capture", json={"payment_id": pid})
    assert r.status_code == 403
    assert calls["n"] == 0
