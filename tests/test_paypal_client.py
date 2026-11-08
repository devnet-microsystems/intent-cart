import os
import logging

import httpx
import pytest

from backend.config import settings
from backend.paypal_client import PayPalClient, PayPalAuthError


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "sandbox")
    # reload settings
    import backend.config as cfg
    cfg.settings = cfg.Settings()


def test_missing_credentials(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")
    import backend.config as cfg
    cfg.settings = cfg.Settings()
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        import asyncio
        asyncio.run(client.get_access_token())


@pytest.mark.asyncio
async def test_auth_success(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})

    transport = httpx.MockTransport(handler)
    client = PayPalClient()
    # inject transport via monkeypatch of AsyncClient
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    token = await client.get_access_token()
    assert token == "TOKEN123"


@pytest.mark.asyncio
async def test_wrong_credentials(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.get_access_token()


@pytest.mark.asyncio
async def test_timeout(monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("timeout")
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.get_access_token()


@pytest.mark.asyncio
async def test_invalid_response(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="not json")
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.get_access_token()


@pytest.mark.asyncio
async def test_no_token_leak_in_logs(monkeypatch, caplog):
    def handler(request):
        return httpx.Response(200, json={"access_token": "SECRET_TOKEN_VALUE", "token_type": "Bearer"})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with caplog.at_level(logging.DEBUG):
        await client.get_access_token()
    assert "SECRET_TOKEN_VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_create_order_success(monkeypatch):
    def handler(request):
        if "oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})
        return httpx.Response(201, json={
            "id": "ORDER123",
            "links": [{"rel": "payer-action", "href": "https://approval.url"}],
        })
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    result = await client.create_order()
    assert result == {"order_id": "ORDER123", "approve_url": "https://approval.url"}


@pytest.mark.asyncio
async def test_create_order_error(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"error": "bad_request"})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.create_order()


@pytest.mark.asyncio
async def test_live_mode_blocked(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "live")
    import backend.config as cfg
    cfg.settings = cfg.Settings()
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.create_order()


@pytest.mark.asyncio
async def test_get_order_success(monkeypatch):
    def handler(request):
        if "oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})
        return httpx.Response(200, json={"id": "ORDER123", "status": "APPROVED"})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    result = await client.get_order("ORDER123")
    assert result == {"order_id": "ORDER123", "status": "APPROVED"}


@pytest.mark.asyncio
async def test_capture_success(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})
        if url.endswith("/capture"):
            return httpx.Response(201, json={
                "id": "ORDER123",
                "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "CAP123"}]}}],
            })
        return httpx.Response(200, json={
            "id": "ORDER123",
            "status": "APPROVED",
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "EUR", "value": "1.00"}}],
        })
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    result = await client.capture_order("ORDER123")
    assert result == {"order_id": "ORDER123", "status": "COMPLETED", "capture_id": "CAP123", "amount": None, "currency": None}


@pytest.mark.asyncio
async def test_capture_does_not_log_raw_payload(monkeypatch, capsys):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test_secret")

    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": "ORDER123",
                "status": "APPROVED",
                "intent": "CAPTURE",
                "purchase_units": [{"amount": {"currency_code": "EUR", "value": "1.00"}}],
            })
        return httpx.Response(201, json={
            "id": "ORDER123",
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "CAP123", "amount": {"value": "1.00", "currency_code": "EUR"}, "payer": {"email_address": "buyer@example.test"}}]}}],
        })

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    result = await client.capture_order("ORDER123")
    out = capsys.readouterr()
    combined = (out.out or "") + (out.err or "")
    assert "CAP_PAYLOAD" not in combined
    assert "buyer@example.test" not in combined
    assert result["capture_id"] == "CAP123"


@pytest.mark.asyncio
async def test_capture_blocked_wrong_status(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})
        return httpx.Response(200, json={
            "id": "ORDER123",
            "status": "CREATED",
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "EUR", "value": "1.00"}}],
        })
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.capture_order("ORDER123")


@pytest.mark.asyncio
async def test_capture_error_paypal(monkeypatch):
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "TOKEN123", "token_type": "Bearer"})
        if url.endswith("/capture"):
            return httpx.Response(400, json={"error": "bad"})
        return httpx.Response(200, json={
            "id": "ORDER123",
            "status": "APPROVED",
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "EUR", "value": "1.00"}}],
        })
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    client = PayPalClient()
    with pytest.raises(PayPalAuthError):
        await client.capture_order("ORDER123")
