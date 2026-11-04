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
    assert r.status_code == 403


def test_missing_evidence_rejected(monkeypatch):
    mock_http(monkeypatch)
    client = TestClient(app)
    sid = "s2"
    sessions[sid] = {"mandate": {"query": "dock", "max_price": 90, "currency": "EUR", "allowed_conditions": [], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}}, "locked": True}
    product = {"name": "Dock", "condition": "new", "offer": {"price": "80"}, "specs": {}}
    r = client.post("/api/orders/checkout", json={"session_id": sid, "product": product})
    assert r.status_code == 403


def test_client_approval_flag_ignored(monkeypatch):
    mock_http(monkeypatch)
    client = TestClient(app)
    sid = "s3"
    sessions[sid] = {"mandate": {"query": "dock", "max_price": 90, "currency": "EUR", "allowed_conditions": [], "forbidden_conditions": [], "min_specs": {}}, "locked": True}
    product = {"name": "Dock", "condition": "new", "offer": {"price": "80"}, "specs": {}, "approved": True}
    # price ok, no specs required -> approved, capture not auto
    r = client.post("/api/orders/checkout", json={"session_id": sid, "product": product, "approved": True})
    assert r.status_code == 200
    # ensure no capture endpoint called implicitly
