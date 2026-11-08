import pytest
from fastapi.testclient import TestClient

from backend.main import app, sessions, authorize_purchase


@pytest.fixture
def client():
    return TestClient(app)


def _setup(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {
        "query": "socks",
        "category": "clothing_accessories",
        "structured_filters": {
            "product_type": "socks",
            "size": "43",
            "material": "cotton",
            "material_percentage": 100,
            "style": "mismatched",
            "forbidden_designs": ["Christmas"],
            "max_price": None,
            "currency": "EUR",
        },
    }
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


def test_authorize_approved(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    result = authorize_purchase(sid, eid, "offer-1")
    assert result["title"] == "Socks"
    assert result["price"] == 10.0


def test_authorize_rejected(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "REJECTED", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid, eid, "offer-1")


def test_authorize_needs_confirmation(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "NEEDS_CONFIRMATION", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid, eid, "offer-1")


def test_authorize_no_lock(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    res = client.post("/api/intent", json={"query": "socks"})
    sid = res.json()["session_id"]
    with pytest.raises(Exception):
        authorize_purchase(sid, "exec", "offer-1")


def test_authorize_unknown_offer(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid, eid, "nope")

def test_authorize_after_memory_clear(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    from backend.main import sessions
    sessions.clear()
    result = __import__("backend.main", fromlist=["authorize_purchase"]).authorize_purchase(sid, eid, "offer-1")
    assert result["title"] == "Socks"


def test_authorize_rejects_zero_rule(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "NEEDS_CONFIRMATION", "rules": [], "policy_version": "2.0", "no_evaluable_rules": True})
    with pytest.raises(Exception):
        authorize_purchase(sid, eid, "offer-1")


def test_authorize_legacy_execution_without_session_id_rejected(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    from backend.storage import save_execution
    import uuid
    legacy_eid = str(uuid.uuid4())
    save_execution(
        legacy_eid,
        "2024-01-01T00:00:00Z",
        sessions[sid]["locked_mandate"],
        [{"product": {"id": "p1", "title": "Socks", "offers": [{"offer_id": "offer-1", "price": {"price": 10.0, "currency": "EUR"}, "condition": "new"}], "gate": {"decision": "APPROVED"}, "offer": {"offer_id": "offer-1", "price": {"price": 10.0, "currency": "EUR"}, "condition": "new"}}}],
    )
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid, legacy_eid, "offer-1")


def test_authorize_execution_from_other_session_rejected(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    res = client.post(
        "/api/intent",
        json={"query": "headphones", "category": "electronics", "structured_filters": {"product_type": "headphones", "size": None, "material": None, "style": None, "forbidden_designs": [], "max_price": None, "currency": "EUR"}},
    )
    sid2 = res.json()["session_id"]
    client.post("/api/mandate/" + sid2 + "/lock")
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid2, eid, "offer-1")


def test_authorize_same_mandate_different_session_does_not_bypass(client, monkeypatch):
    sid, eid = _setup(client, monkeypatch)
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    res = client.post(
        "/api/intent",
        json={"query": "socks", "category": "clothing_accessories", "structured_filters": {"product_type": "socks", "size": "43", "material": "cotton", "material_percentage": 100, "style": "mismatched", "forbidden_designs": ["Christmas"], "max_price": None, "currency": "EUR"}},
    )
    sid2 = res.json()["session_id"]
    client.post("/api/mandate/" + sid2 + "/lock")
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    with pytest.raises(Exception):
        authorize_purchase(sid2, eid, "offer-1")
