import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from backend.main import app, sessions


@pytest.fixture
def client():
    return TestClient(app)


def _setup_locked_session(client, monkeypatch):
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
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")
    return sid


def test_search_requires_lock(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    res = client.post("/api/intent", json={"query": "socks"})
    sid = res.json()["session_id"]
    # no lock
    mock_client = MagicMock()
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)
    r = client.post(f"/api/search/{sid}")
    assert r.status_code == 400
    mock_client.search.assert_not_called()


def test_search_uses_locked_mandate(client, monkeypatch):
    sid = _setup_locked_session(client, monkeypatch)
    mock_products = [{"title": "Socks", "offers": [{"price": {"price": 7.99, "currency": "EUR"}, "condition": "new"}]}]
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=mock_products)
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    captured = {}
    def fake_eval(mandate, product):
        captured["mandate"] = dict(mandate)
        return {"decision": "REJECTED", "rules": [], "policy_version": "2.0"}
    monkeypatch.setattr("backend.main.evaluate", fake_eval)

    r = client.post(f"/api/search/{sid}")
    assert r.status_code == 200
    data = r.json()
    assert mock_client.search.call_args[0][0] == "men's mismatched socks"
    assert captured["mandate"]["material"] == "cotton"
    assert captured["mandate"]["size"] == "43"
    assert captured["mandate"]["max_price"] is None

    from backend.main import sessions
    assert sessions[sid]["locked_mandate"]["material"] == "cotton"

    # persisted execution
    import sqlite3, json
    conn = sqlite3.connect("executions.db")
    row = conn.execute("SELECT mandate FROM executions WHERE execution_id=?", (data["execution_id"],)).fetchone()
    ex_mandate = json.loads(row[0])
    assert ex_mandate["material"] == "cotton"
    assert ex_mandate["max_price"] is None


def test_search_rejects_other_session(client, monkeypatch):
    sid1 = _setup_locked_session(client, monkeypatch)
    # second session unlocked
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    res2 = client.post("/api/intent", json={"query": "other"})
    sid2 = res2.json()["session_id"]
    mock_client = MagicMock()
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)
    r = client.post(f"/api/search/{sid2}")
    assert r.status_code == 400
    mock_client.search.assert_not_called()


def test_free_text_search_still_works(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "max_price": 10.0})
    res = client.post("/api/intent", json={"text": "cheap socks"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)
    monkeypatch.setattr("backend.main.evaluate", lambda m, p: {"decision": "APPROVED", "rules": [], "policy_version": "2.0"})
    r = client.post(f"/api/search/{sid}")
    assert r.status_code == 200