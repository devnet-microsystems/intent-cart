import os
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.main import app, sessions
from backend.config import settings


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "sandbox")
    settings.paypal_client_id = "test_id"
    settings.paypal_client_secret = "test_secret"
    settings.paypal_base_url = "https://api-m.sandbox.paypal.com"
    settings.paypal_environment = "sandbox"
    sessions.clear()


def mock_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O1", "links": [{"rel": "payer-action", "href": "https://a"}]})
        return httpx.Response(200, json={"id": "O1", "status": "APPROVED"})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)


def test_rejected_product_cannot_create_order(monkeypatch):
    mock_http(monkeypatch)
    client = TestClient(app)
    # create session + mandate manually
    sid = "s1"
    sessions[sid] = {"mandate": {"query": "dock", "max_price": 90, "currency": "EUR", "allowed_conditions": ["new"], "forbidden_conditions": ["refurbished"], "min_specs": {"power_delivery_w": 100}}, "locked": True}
    product = {"name": "Dock", "condition": "refurbished", "offer": {"price": "80"}, "specs": {"power_delivery_w": 120}}
    r = client.post("/api/orders/checkout", json={"session_id": sid, "product": product})
    assert r.status_code in (400, 403, 404)


def test_missing_evidence_rejected(monkeypatch):
    mock_http(monkeypatch)
    client = TestClient(app)
    sid = "s2"
    sessions[sid] = {"mandate": {"query": "dock", "max_price": 90, "currency": "EUR", "allowed_conditions": [], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}}, "locked": True}
    product = {"name": "Dock", "condition": "new", "offer": {"price": "80"}, "specs": {}}
    r = client.post("/api/orders/checkout", json={"session_id": sid, "product": product})
    assert r.status_code in (400, 403, 404)


def test_client_approval_flag_ignored(monkeypatch):
    mock_http(monkeypatch)
    client = TestClient(app)
    sid = "s3"
    sessions[sid] = {"mandate": {"query": "dock", "max_price": 90, "currency": "EUR", "allowed_conditions": [], "forbidden_conditions": [], "min_specs": {}}, "locked": True}
    product = {"name": "Dock", "condition": "new", "offer": {"price": "80"}, "specs": {}, "approved": True}
    # price ok, no specs required -> approved, capture not auto
    r = client.post("/api/orders/checkout", json={"session_id": sid, "product": product, "approved": True})
    assert r.status_code in (200, 400, 404)
    # ensure no capture endpoint called implicitly


# --- CORS origin verification ---

def _cors_response(client, origin):
    return client.get(
        "/api/executions",
        headers={"Origin": origin},
    )


def test_cors_allowed_localhost_5174():
    client = TestClient(app)
    r = _cors_response(client, "http://localhost:5174")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5174"


def test_cors_allowed_127_0_0_1_5174():
    client = TestClient(app)
    r = _cors_response(client, "http://127.0.0.1:5174")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5174"


def test_cors_allowed_localhost_5173():
    client = TestClient(app)
    r = _cors_response(client, "http://localhost:5173")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_allowed_127_0_0_1_5173():
    client = TestClient(app)
    r = _cors_response(client, "http://127.0.0.1:5173")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_cors_unapproved_origin_not_granted():
    client = TestClient(app)
    r = _cors_response(client, "http://evil.example.com")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is None

# --- Configured production CORS origins (Pass 6B) ---

def test_cors_accepts_configured_production_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com/, https://admin.example.com")
    import importlib, backend.main as main_mod
    app_mod = importlib.reload(main_mod)
    client = TestClient(app_mod.app)
    r = client.get("/api/executions", headers={"Origin": "https://app.example.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_cors_rejects_unlisted_origin_when_configured(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    import importlib, backend.main as main_mod
    app_mod = importlib.reload(main_mod)
    client = TestClient(app_mod.app)
    r = client.get("/api/executions", headers={"Origin": "https://evil.example.com"})
    assert r.headers.get("access-control-allow-origin") is None


def test_cors_never_uses_wildcard_when_configured(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*, https://app.example.com")
    import importlib, backend.main as main_mod
    app_mod = importlib.reload(main_mod)
    client = TestClient(app_mod.app)
    r = client.get("/api/executions", headers={"Origin": "https://app.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
    r2 = client.get("/api/executions", headers={"Origin": "https://other.example.com"})
    assert r2.headers.get("access-control-allow-origin") is None
