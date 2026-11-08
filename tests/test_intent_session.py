import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_structured_filters_persisted(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {
        "query": "men's mismatched socks",
        "category": "clothing_accessories",
        "structured_filters": {
            "product_type": "socks",
            "size": "43",
            "material": "cotton",
            "material_percentage": 100,
            "style": "mismatched",
            "forbidden_designs": ["Christmas", "snowmen"],
            "max_price": None,
            "currency": "EUR",
        },
    }
    res = client.post("/api/intent", json=payload)
    assert res.status_code == 200
    data = res.json()
    sid = data["session_id"]
    from backend.main import sessions

    assert sessions[sid]["category"] == "clothing_accessories"
    assert sessions[sid]["structured_filters"]["max_price"] is None
    assert sessions[sid]["mandate"]["material"] == "cotton"
    assert sessions[sid]["mandate"]["size"] == "43"


def test_no_inheritance(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    r1 = client.post("/api/intent", json={"query": "a", "category": "clothing_accessories", "structured_filters": {"material": "cotton"}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions

    assert sessions[sid2].get("structured_filters", {}) == {}


def test_free_text_still_works(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "max_price": None})
    res = client.post("/api/intent", json={"text": "hello"})
    assert res.status_code == 200
    assert "session_id" in res.json()

def test_merge_override_material(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "material": "polyester", "size": "42", "material_percentage": 50, "style": "plain", "forbidden_designs": ["logos"]})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "material": "cotton", "size": "43", "material_percentage": 100, "style": "mismatched", "forbidden_designs": ["Christmas", "snowmen"], "max_price": None}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["material"] == "cotton"
    assert m["size"] == "43"
    assert m["material_percentage"] == 100
    assert m["style"] == "mismatched"
    assert m["forbidden_designs"] == ["Christmas", "snowmen"]
    assert m["max_price"] is None
    assert res.json()["mandate"] == m


def test_merge_supplement(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "currency": "EUR"})
    payload = {"query": "socks", "structured_filters": {"material": "cotton"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["material"] == "cotton"
    assert m["currency"] == "EUR"


def test_no_inheritance_merge(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    r1 = client.post("/api/intent", json={"query": "a", "structured_filters": {"material": "cotton"}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions
    assert sessions[sid2]["mandate"].get("material") is None


def test_lock_preserves_structured(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "product_type": "socks", "size": "43", "material": "cotton", "material_percentage": 100, "style": "mismatched",
        "forbidden_designs": ["Christmas"], "max_price": None, "currency": "EUR"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 200
    from backend.main import sessions
    assert sessions[sid]["locked"] is True
    assert sessions[sid]["locked_mandate"]["material"] == "cotton"
    assert sessions[sid]["locked_mandate"]["max_price"] is None


def test_lock_missing_prevents(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "socks"})
    payload = {"query": "socks", "structured_filters": {"material": "cotton"}}
    # force missing size by not providing; but mandate has no size
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    # inject structured filter claiming size present
    from backend.main import sessions
    sessions[sid]["structured_filters"] = {"size": "43"}
    # mandate lacks size -> should fail
    # ensure mandate size None
    sessions[sid]["mandate"]["size"] = None
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 400


def test_constraint_priorities_persist(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "product_type": "socks", "material": "cotton",
        "constraint_priorities": {"material": "PREFERRED", "size": "REQUIRED"}}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["constraint_priorities"]["material"] == "PREFERRED"
    assert m["constraint_priorities"]["size"] == "REQUIRED"
    assert sessions[sid]["structured_filters"]["constraint_priorities"]["material"] == "PREFERRED"


def test_priorities_survive_lock(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "structured_filters": {"material": "cotton", "constraint_priorities": {"material": "PREFERRED"}}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")
    from backend.main import sessions
    assert sessions[sid]["locked_mandate"]["constraint_priorities"]["material"] == "PREFERRED"


def test_no_inherit_priorities(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    r1 = client.post("/api/intent", json={"query": "a", "structured_filters": {"constraint_priorities": {"material": "PREFERRED"}}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions
    assert sessions[sid2]["mandate"].get("constraint_priorities") is None
