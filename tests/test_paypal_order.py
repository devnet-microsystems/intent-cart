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
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "O1", "status": "CREATED"})
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


def _setup_with_existing_payment(client, monkeypatch, paypal_order_id="O_EXISTING", status="CREATED"):
    """Create session, execution, and a pre-existing CREATED payment record."""
    sid, eid = _setup(client, monkeypatch)
    from backend.storage import save_payment
    import uuid
    payment_id = str(uuid.uuid4())
    key = f"{sid}:{eid}:offer-1"
    save_payment({
        "id": payment_id,
        "session_id": sid,
        "execution_id": eid,
        "offer_id": "offer-1",
        "paypal_order_id": paypal_order_id,
        "status": status,
        "amount": 10.0,
        "currency": "EUR",
        "mandate_ref": '{"query": "socks"}',
        "idempotency_key": key,
        "created_at": "2024-01-01T00:00:00Z",
    })
    return sid, eid, payment_id, paypal_order_id


def mock_get_order_status(monkeypatch, get_status, get_status_code=200):
    """Mock PayPal HTTP: token + GET order with given status."""
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            if get_status_code == 404:
                return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "Resource not found"})
            return httpx.Response(200, json={"id": "O_EXISTING", "status": get_status})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)


def mock_get_order_auth_error(monkeypatch):
    """Mock PayPal HTTP: token succeeds, GET order raises auth error."""
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)


def test_checkout_reuses_valid_created_order(client, monkeypatch):
    """Existing CREATED order still valid on PayPal → reused, no new order."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_VALID", "CREATED")
    mock_get_order_status(monkeypatch, "CREATED")
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_VALID"
    assert data["status"] == "CREATED"
    assert data["payment_id"] == payment_id
    assert data["approve_url"] is None
    # Old payment record preserved (not overwritten)
    from backend.storage import get_payment_by_id
    pay = get_payment_by_id(payment_id)
    assert pay["status"] == "CREATED"
    assert pay["paypal_order_id"] == "O_VALID"


def test_checkout_recovers_from_stale_order(client, monkeypatch):
    """PayPal returns 404 for existing order → stale, new order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_STALE", "CREATED")

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "Resource not found"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O_NEW", "links": [{"rel": "payer-action", "href": "https://approve-new"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_NEW"
    assert data["approve_url"] == "https://approve-new"
    assert data["status"] == "CREATED"
    # Old payment marked stale and preserved
    from backend.storage import get_payment_by_id
    old_pay = get_payment_by_id(payment_id)
    assert old_pay["stale_reason"] == "paypal_resource_not_found"
    # New payment created with different ID
    from backend.storage import _connect, init_payments
    init_payments()
    conn = _connect()
    row = conn.execute("SELECT id, paypal_order_id, stale_reason FROM payments WHERE paypal_order_id='O_NEW'").fetchone()
    conn.close()
    assert row is not None
    assert row[2] is None  # new payment has no stale_reason


def test_checkout_does_not_duplicate_approved_order(client, monkeypatch):
    """Existing order APPROVED on PayPal → not duplicated."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_APPROVED", "CREATED")
    mock_get_order_status(monkeypatch, "APPROVED")
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_APPROVED"
    assert data["status"] == "APPROVED"
    assert data["payment_id"] == payment_id
    assert data["approve_url"] is None


def test_checkout_does_not_duplicate_completed_payment(client, monkeypatch):
    """Existing payment COMPLETED → checkout rejected, no new order."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_COMPLETED", "PAYMENT_CAPTURED")
    # No need to mock PayPal: COMPLETED payment is rejected before any PayPal call
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 400
    assert r.json().get("detail") == "Payment already captured"


def test_checkout_auth_error_does_not_create_replacement(client, monkeypatch):
    """PayPal auth error on GET order → 502, no replacement order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_AUTHERR", "CREATED")
    mock_get_order_auth_error(monkeypatch)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    # No new payment: only the original exists
    from backend.storage import _connect, init_payments
    init_payments()
    conn = _connect()
    rows = conn.execute("SELECT id FROM payments WHERE idempotency_key=?", (f"{sid}:{eid}:offer-1",)).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == payment_id


def test_checkout_preserves_stale_payment_audit_trail(client, monkeypatch):
    """After stale recovery, old payment record retains stale_reason and order ID."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, order_id = _setup_with_existing_payment(client, monkeypatch, "O_AUDIT", "CREATED")

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "Resource not found"})
        if "checkout/orders" in url and request.method == "POST":
            return httpx.Response(201, json={"id": "O_REPLACEMENT", "links": [{"rel": "payer-action", "href": "https://approve-replacement"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    # Old payment preserved with stale_reason
    from backend.storage import get_payment_by_id
    old_pay = get_payment_by_id(payment_id)
    assert old_pay["stale_reason"] == "paypal_resource_not_found"
    assert old_pay["paypal_order_id"] == "O_AUDIT"
    assert old_pay["status"] == "CREATED"  # status not changed, only stale_reason set


# ---------------------------------------------------------------------------
# Task 5 — New tests for PayPal order state handling
# ---------------------------------------------------------------------------

def _mock_paypal_handler(token_status=200, token_body=None, order_get=None, order_post=None):
    """Build a mock HTTP handler for PayPal endpoints."""
    token_body = token_body or {"access_token": "T", "token_type": "Bearer"}

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(token_status, json=token_body)
        if "checkout/orders" in url and request.method == "GET":
            if order_get is not None:
                status_code, body = order_get
                return httpx.Response(status_code, json=body)
            return httpx.Response(200, json={"id": "O_EXISTING", "status": "CREATED"})
        if "checkout/orders" in url and request.method == "POST":
            if order_post is not None:
                status_code, body = order_post
                return httpx.Response(status_code, json=body)
            return httpx.Response(201, json={"id": "O_NEW", "links": [{"rel": "payer-action", "href": "https://approve-new"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    return patched


# --- CREATED state reuse ---

def test_checkout_reuses_created_order_from_paypal(client, monkeypatch):
    """Existing CREATED order confirmed by PayPal → reused."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_CREATED_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_CREATED_PP", "status": "CREATED"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_CREATED_PP"
    assert data["status"] == "CREATED"
    assert data["payment_id"] == payment_id


# --- APPROVED state ---

def test_checkout_reuses_approved_order_from_paypal(client, monkeypatch):
    """Existing order APPROVED on PayPal → not duplicated, preserve state."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_APPROVED_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_APPROVED_PP", "status": "APPROVED"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_APPROVED_PP"
    assert data["status"] == "APPROVED"
    assert data["payment_id"] == payment_id
    assert data["approve_url"] is None


# --- COMPLETED state ---

def test_checkout_rejects_completed_order_from_paypal(client, monkeypatch):
    """Existing order COMPLETED on PayPal → reject duplicate checkout."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_COMPLETED_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_COMPLETED_PP", "status": "COMPLETED"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 400
    assert r.json().get("detail") == "Payment already captured"


# --- VOIDED state ---

def test_checkout_recovers_voided_order(client, monkeypatch):
    """Existing order VOIDED on PayPal → stale recovery, new order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_VOIDED_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(
        order_get=(200, {"id": "O_VOIDED_PP", "status": "VOIDED"}),
        order_post=(201, {"id": "O_REPLACEMENT", "links": [{"rel": "payer-action", "href": "https://approve-replacement"}]}),
    ))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == "O_REPLACEMENT"
    # Old payment marked stale
    from backend.storage import get_payment_by_id
    old_pay = get_payment_by_id(payment_id)
    assert old_pay["stale_reason"] == "paypal_order_voided"


# --- SAVED / PAYER_ACTION_REQUIRED → fail closed ---

def test_checkout_rejects_saved_order(client, monkeypatch):
    """PayPal SAVED status → fail closed, no replacement."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_SAVED_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_SAVED_PP", "status": "SAVED"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "SAVED" in r.json().get("detail", "")


def test_checkout_rejects_payer_action_required(client, monkeypatch):
    """PayPal PAYER_ACTION_REQUIRED status → fail closed, no replacement."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_PAR_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_PAR_PP", "status": "PAYER_ACTION_REQUIRED"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "PAYER_ACTION_REQUIRED" in r.json().get("detail", "")


# --- Unknown / missing status ---

def test_checkout_unknown_paypal_status_returns_existing_no_approve_url(client, monkeypatch):
    """Unknown PayPal status → fail closed with 502, never return 200."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_UNKNOWN_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_UNKNOWN_PP", "status": "UNKNOWN_STATUS"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "Unknown PayPal order status" in r.json().get("detail", "")


def test_checkout_missing_paypal_status_returns_existing_no_approve_url(client, monkeypatch):
    """Missing PayPal status field → fail closed with 502."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_MISSING_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(200, {"id": "O_MISSING_PP"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "Unknown PayPal order status" in r.json().get("detail", "")


# --- Missing local paypal_order_id ---

def test_checkout_created_without_paypal_order_id_rejected(client, monkeypatch):
    """Local CREATED payment without paypal_order_id → not a valid checkout."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, None, "CREATED")
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "no PayPal order reference" in r.json().get("detail", "")


# --- PayPal HTTP error codes ---

def test_checkout_paypal_401_on_get_order_fail_closed(client, monkeypatch):
    """PayPal 401 on GET order → 502, no replacement."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_401_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(401, {"error": "unauthorized"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502


def test_checkout_paypal_403_on_get_order_fail_closed(client, monkeypatch):
    """PayPal 403 on GET order → 502, no replacement."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_403_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(403, {"error": "forbidden"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502


def test_checkout_paypal_404_on_get_order_stale_recovery(client, monkeypatch):
    """PayPal 404 on GET order → stale recovery, new order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_404_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(
        order_get=(404, {"name": "RESOURCE_NOT_FOUND", "message": "Resource not found"}),
        order_post=(201, {"id": "O_NEW_404", "links": [{"rel": "payer-action", "href": "https://approve-404"}]}),
    ))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    assert r.json()["order_id"] == "O_NEW_404"


def test_checkout_paypal_500_on_get_order_fail_closed(client, monkeypatch):
    """PayPal 500 on GET order → 502, no replacement."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_500_PP", "CREATED")
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler(order_get=(500, {"error": "internal_server_error"})))
    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502


# --- Network timeout during order creation ---

def test_checkout_paypal_timeout_on_create_order_fail_closed(client, monkeypatch):
    """PayPal timeout on CREATE order → 502, reservation marked stale."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            raise httpx.ConnectError("connection timed out")
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    # Reservation marked stale, no successful order returned
    from backend.storage import get_payment_by_key
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    assert pay is not None
    assert pay["stale_reason"] is not None
    assert "paypal_creation_failed" in pay["stale_reason"]


# --- Concurrent checkout requests (same idempotency key) ---

def test_checkout_concurrent_same_key_single_order(client, monkeypatch):
    """Two sequential checkout requests with same key → only one PayPal order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)
    call_count = {"n": 0}

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "O_CONCURRENT_1", "status": "CREATED"})
        if "checkout/orders" in url and request.method == "POST":
            call_count["n"] += 1
            return httpx.Response(201, json={"id": "O_CONCURRENT_1", "links": [{"rel": "payer-action", "href": "https://approve-concurrent"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    # First request creates the order
    r1 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r1.status_code == 200
    assert r1.json()["order_id"] == "O_CONCURRENT_1"

    # Second request finds existing payment with paypal_order_id, reuses it
    r2 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r2.status_code == 200
    # Only one PayPal POST should have been made (the first one)
    assert call_count["n"] == 1
    assert r2.json()["order_id"] == "O_CONCURRENT_1"
    assert r2.json()["payment_id"] == r1.json()["payment_id"]


# --- No orphaned or duplicated logical checkout records ---

def test_checkout_no_duplicate_payment_records(client, monkeypatch):
    """Multiple checkout attempts → exactly one payment record per idempotency key."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _mock_paypal_handler())
    for _ in range(3):
        r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
        assert r.status_code == 200
    from backend.storage import _connect, init_payments
    init_payments()
    conn = _connect()
    rows = conn.execute("SELECT COUNT(*) FROM payments WHERE idempotency_key=?", (f"{sid}:{eid}:offer-1",)).fetchall()
    conn.close()
    assert rows[0][0] == 1  # Exactly one payment record


# ---------------------------------------------------------------------------
# Task 5 — Policy Gate payload parity tests
# ---------------------------------------------------------------------------

def test_policy_gate_payload_parity_search_vs_authorize():
    """build_product_evidence_payload produces identical keys for search and authorize_purchase paths."""
    from backend.policy_engine import build_product_evidence_payload

    product = {
        "id": "p1",
        "title": "Test Product",
        "description": "A test product",
        "condition": "new",
        "price": 10.0,
        "brands": [{"name": "TestBrand"}],
        "structured_attributes": {"color": "blue", "size": "M"},
        "key_features": ["feature1", "feature2"],
        "materials": ["cotton"],
        "variants": [{"color": "blue", "size": "M"}],
        "category": {"slug": "clothing"},
        "specs": {"weight_g": 100},
        "color": "blue",
        "_provenance": {"source": "channel3"},
    }
    offer = {"offer_id": "offer-1", "price": {"price": 10.0, "currency": "EUR"}, "condition": "new"}

    payload_with_offer = build_product_evidence_payload(product, offer)
    payload_without_offer = build_product_evidence_payload(product)

    # Both should have the same keys
    assert set(payload_with_offer.keys()) == set(payload_without_offer.keys())

    # Expected keys
    expected_keys = {
        "price", "condition", "specs", "title", "description", "brands",
        "structured_attributes", "key_features", "materials", "variants",
        "category", "color", "_provenance",
    }
    assert set(payload_with_offer.keys()) == expected_keys

    # Price should come from offer when provided
    assert payload_with_offer["price"] == 10.0


def test_policy_gate_payload_missing_product_safe():
    """build_product_evidence_payload handles missing/non-dict product safely."""
    from backend.policy_engine import build_product_evidence_payload

    assert build_product_evidence_payload(None) == {}
    assert build_product_evidence_payload("not a dict") == {}
    assert build_product_evidence_payload([]) == {}
    assert build_product_evidence_payload(42) == {}


def test_policy_gate_payload_partial_product():
    """build_product_evidence_payload handles partial product data."""
    from backend.policy_engine import build_product_evidence_payload

    product = {"title": "Partial Product"}
    payload = build_product_evidence_payload(product)
    assert payload["title"] == "Partial Product"
    assert payload["price"] is None
    assert payload["condition"] is None
    assert payload["specs"] == {}
    assert payload["_provenance"] is None


def test_policy_gate_payload_price_from_offer_preferred():
    """Offer price takes precedence over product-level price."""
    from backend.policy_engine import build_product_evidence_payload

    product = {"price": 99.0}
    offer = {"price": {"price": 10.0, "currency": "EUR"}}
    payload = build_product_evidence_payload(product, offer)
    assert payload["price"] == 10.0


# ---------------------------------------------------------------------------
# Task 5 — Concurrency and recovery tests (P0)
# ---------------------------------------------------------------------------

import anyio
from starlette.testclient import TestClient
from httpx import ASGITransport


def test_checkout_concurrent_real_async_single_order(client, monkeypatch):
    """Two concurrent checkout requests → exactly one PayPal order created."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)
    call_count = {"n": 0}
    creation_gate = anyio.Event()

    async def mock_create_order(self, amount="1.00", currency="EUR", paypal_request_id=None):
        call_count["n"] += 1
        creation_gate.set()
        await creation_gate.wait()
        return {"order_id": "O_CONCURRENT_REAL", "approve_url": "https://approve-concurrent"}

    async def mock_get_order(self, order_id):
        return {"order_id": order_id, "status": "CREATED"}

    monkeypatch.setattr("backend.main.PayPalClient.create_order", mock_create_order)
    monkeypatch.setattr("backend.main.PayPalClient.get_order", mock_get_order)

    transport = ASGITransport(app=app)

    async def post_once():
        async with httpx.AsyncClient(transport=transport) as c:
            resp = await c.post(
                "http://testserver/api/orders/checkout",
                json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"},
            )
            return resp.status_code, resp.json()

    async def run_concurrent():
        results = await anyio.gather(post_once(), post_once())
        creation_gate.set()
        return results

    statuses, bodies = zip(*anyio.run(run_concurrent))
    # Each caller gets either 200 (success) or 502 (creation in progress, retry)
    assert all(s in (200, 502) for s in statuses)
    # All successful responses reference the same order
    successful = [b for s, b in zip(statuses, bodies) if s == 200]
    assert len(successful) >= 1
    assert all(b["order_id"] == "O_CONCURRENT_REAL" for b in successful)


# ---------------------------------------------------------------------------
# Direct atomic reservation test (reliable concurrency verification)
# ---------------------------------------------------------------------------

def test_reserve_payment_atomic_concurrent(client, monkeypatch):
    """Two concurrent reserve_payment_atomic calls with same key → exactly one succeeds."""
    from backend.storage import reserve_payment_atomic
    import uuid

    sid, eid = _setup(client, monkeypatch)
    key = f"{sid}:{eid}:offer-1"
    results = []

    async def reserve():
        result = await anyio.to_thread.run_sync(
            lambda: reserve_payment_atomic(
                payment_id=str(uuid.uuid4()),
                session_id=sid,
                execution_id=eid,
                offer_id="offer-1",
                paypal_request_id="req-test-concurrent",
                amount=10.0,
                currency="EUR",
                mandate_ref='{"query": "socks"}',
                idempotency_key=key,
                created_at="2024-01-01T00:00:00Z",
            )
        )
        results.append(result)

    anyio.run(lambda: anyio.gather(reserve(), reserve()))
    assert sum(results) == 1, f"Expected exactly 1 successful reservation, got {sum(results)}"

    # Verify only one payment record exists
    from backend.storage import _connect, init_payments
    init_payments()
    conn = _connect()
    rows = conn.execute("SELECT COUNT(*) FROM payments WHERE idempotency_key=?", (key,)).fetchall()
    conn.close()
    assert rows[0][0] == 1, f"Expected exactly 1 payment record, got {rows[0][0]}"


def test_checkout_retry_with_same_request_id_after_timeout(client, monkeypatch):
    """Existing CREATED reservation without paypal_order_id → retry with same PayPal-Request-Id."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)
    from backend.storage import save_payment
    import uuid
    payment_id = str(uuid.uuid4())
    key = f"{sid}:{eid}:offer-1"
    # Payment exists with request ID but no PayPal order (creation timed out previously)
    save_payment({
        "id": payment_id,
        "session_id": sid,
        "execution_id": eid,
        "offer_id": "offer-1",
        "paypal_order_id": None,
        "status": "CREATED",
        "amount": 10.0,
        "currency": "EUR",
        "mandate_ref": '{"query": "socks"}',
        "idempotency_key": key,
        "paypal_request_id": "req-id-retry",
        "created_at": "2024-01-01T00:00:00Z",
    })

    # First call: PayPal creation fails again (timeout)
    def fail_handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            raise httpx.ConnectError("connection timed out")
        return httpx.Response(200, json={})

    transport_fail = httpx.MockTransport(fail_handler)
    original = httpx.AsyncClient
    def patched_fail(*args, **kwargs):
        kwargs["transport"] = transport_fail
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched_fail)

    r1 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r1.status_code == 502

    # Verify reservation preserved: same key, paypal_request_id kept, stale_reason set
    from backend.storage import get_payment_by_key
    pay = get_payment_by_key(key)
    assert pay is not None
    assert pay["paypal_request_id"] == "req-id-retry"
    assert pay["paypal_order_id"] is None
    assert pay["stale_reason"] is not None
    assert "paypal_creation_retry_failed" in pay["stale_reason"]

    # Second call: PayPal succeeds, reuses same request ID
    def success_handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            assert request.headers.get("PayPal-Request-Id") == "req-id-retry", "Should reuse same request ID"
            return httpx.Response(201, json={"id": "O_RETRY_SUCCESS", "links": [{"rel": "payer-action", "href": "https://approve-retry"}]})
        return httpx.Response(200, json={})

    transport_success = httpx.MockTransport(success_handler)
    def patched_success(*args, **kwargs):
        kwargs["transport"] = transport_success
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched_success)

    r2 = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r2.status_code == 200
    data = r2.json()
    assert data["order_id"] == "O_RETRY_SUCCESS"
    assert data["payment_id"] == payment_id  # Same payment record reused


def test_checkout_order_created_no_approve_url_persists_order_id(client, monkeypatch):
    """PayPal creates order but returns no approve_url → persist order ID, fail closed."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            # Order created but no approve link
            return httpx.Response(201, json={"id": "O_NO_APPROVE", "links": [{"rel": "self", "href": "https://example.com"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "approval link unavailable" in r.json().get("detail", "")

    # Order ID was persisted despite the failure
    from backend.storage import get_payment_by_key
    pay = get_payment_by_key(f"{sid}:{eid}:offer-1")
    assert pay is not None
    assert pay["paypal_order_id"] == "O_NO_APPROVE"
    assert pay["stale_reason"] == "paypal_approval_link_unavailable"
    assert pay["stale_reason"] is not None


def test_checkout_unknown_paypal_status_fail_closed(client, monkeypatch):
    """Unknown PayPal order status → 502, never return 200 with unusable order."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_UNKNOWN_PP2", "CREATED")

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "O_UNKNOWN_PP2", "status": "SOME_WEIRD_STATUS"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "Unknown PayPal order status" in r.json().get("detail", "")


def test_checkout_missing_paypal_status_fail_closed(client, monkeypatch):
    """Missing PayPal status field → 502, fail closed."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid, payment_id, _ = _setup_with_existing_payment(client, monkeypatch, "O_MISSING_PP2", "CREATED")

    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "GET":
            return httpx.Response(200, json={"id": "O_MISSING_PP2"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 502
    assert "Unknown PayPal order status" in r.json().get("detail", "")


def test_checkout_request_id_length_under_108_chars(client, monkeypatch):
    """PayPal-Request-Id must be 1-108 characters. UUID-based ID is 36 chars."""
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    sid, eid = _setup(client, monkeypatch)

    captured_ids = []
    def handler(request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "T", "token_type": "Bearer"})
        if "checkout/orders" in url and request.method == "POST":
            captured_ids.append(request.headers.get("PayPal-Request-Id"))
            return httpx.Response(201, json={"id": "O_LEN", "links": [{"rel": "payer-action", "href": "https://approve-len"}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", patched)

    r = client.post("/api/orders/checkout", json={"session_id": sid, "execution_id": eid, "offer_id": "offer-1"})
    assert r.status_code == 200
    assert len(captured_ids) == 1
    rid = captured_ids[0]
    assert 1 <= len(rid) <= 108, f"PayPal-Request-Id length {len(rid)} outside 1-108 range"
    assert rid.startswith("intent-cart-"), "Should use UUID-based identifier"