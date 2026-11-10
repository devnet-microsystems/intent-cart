import json
import uuid

from fastapi.testclient import TestClient

from backend.main import app, sessions
import backend.agent_council as ac
from backend.storage import save_session, save_execution


def _make_db(monkeypatch, tmp_path, name="council_api.db"):
    db = tmp_path / name
    monkeypatch.setenv("INTENT_CART_DB_PATH", str(db))
    return db


def _setup(monkeypatch, tmp_path, locked=True, mandate=None, results=None):
    _make_db(monkeypatch, tmp_path)
    sessions.clear()
    sid = str(uuid.uuid4())
    eid = str(uuid.uuid4())
    mandate = mandate or {
        "query": "laptop",
        "max_price": 1200,
        "currency": "EUR",
        "product_type": "laptop",
        "material": "aluminum",
    }
    product = {
        "id": "p1",
        "title": "Laptop X",
        "material": "aluminum",
        "materials": ["aluminum"],
        "condition": "new",
        "specs": {"ram_gb": 16, "storage_gb": 512},
    }
    offer = {"offer_id": "offer-1", "price": {"price": 999, "currency": "EUR"}, "condition": "new"}
    gate = {"decision": "APPROVED", "rules": [], "policy_version": "2.0"}
    results = results or [{"product": product, "offer": offer, "gate": gate}]
    sessions[sid] = {"mandate": mandate, "locked": locked, "locked_mandate": dict(mandate)}
    save_session(sid, "cat", "query", mandate, locked, dict(mandate), {})
    save_execution(eid, "2026-01-01T00:00:00Z", mandate, results, sid)
    return sid, eid


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeGenerateContent:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_content(self, model=None, contents=None, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self.responses:
            raise RuntimeError("nessuna risposta simulata disponibile")
        payload = self.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(json.dumps(payload))


class _FakeModels:
    def __init__(self, generate_content):
        self.generate_content = generate_content


class _FakeAio:
    def __init__(self, generate_content):
        self.models = _FakeModels(generate_content)


class _FakeClient:
    def __init__(self, responses):
        self.aio = _FakeAio(_FakeGenerateContent(responses))


def _install_fake(monkeypatch, responses):
    fake = _FakeClient(responses)
    monkeypatch.setattr("backend.agent_council.genai.Client", lambda **kw: fake)
    return fake


def _post(client, eid, offer_id="offer-1", session_id=None, extra=None):
    body = {"offer_id": offer_id}
    if session_id is not None:
        body["session_id"] = session_id
    if extra:
        body.update(extra)
    return client.post(f"/api/executions/{eid}/agent-council", json=body)


def test_404_execution_not_found(monkeypatch, tmp_path):
    _make_db(monkeypatch, tmp_path)
    client = TestClient(app)
    r = client.post("/api/executions/does-not-exist/agent-council", json={"offer_id": "offer-1"})
    assert r.status_code == 404


def test_403_session_not_locked(monkeypatch, tmp_path):
    _sid, eid = _setup(monkeypatch, tmp_path, locked=False)
    client = TestClient(app)
    r = _post(client, eid)
    assert r.status_code == 403


def test_403_execution_not_belong_to_session(monkeypatch, tmp_path):
    _make_db(monkeypatch, tmp_path)
    sessions.clear()
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    eid = str(uuid.uuid4())
    mandate = {"query": "laptop", "max_price": 1200, "currency": "EUR", "material": "aluminum"}
    offer = {"offer_id": "offer-1", "price": {"price": 999, "currency": "EUR"}, "condition": "new"}
    results = [{
        "product": {"id": "p1", "title": "X", "material": "aluminum", "materials": ["aluminum"], "condition": "new"},
        "offer": offer,
        "gate": {"decision": "APPROVED", "rules": [], "policy_version": "2.0"},
    }]
    sessions[sid_a] = {"mandate": mandate, "locked": True, "locked_mandate": dict(mandate)}
    save_session(sid_a, "cat", "q", mandate, True, dict(mandate), {})
    save_execution(eid, "2026-01-01T00:00:00Z", mandate, results, sid_a)
    sessions[sid_b] = {"mandate": mandate, "locked": True, "locked_mandate": dict(mandate)}
    save_session(sid_b, "cat", "q", mandate, True, dict(mandate), {})
    client = TestClient(app)
    r = _post(client, eid, session_id=sid_b)
    assert r.status_code == 403


def test_404_offer_not_in_execution(monkeypatch, tmp_path):
    _sid, eid = _setup(monkeypatch, tmp_path)
    client = TestClient(app)
    r = _post(client, eid, offer_id="unknown-offer")
    assert r.status_code == 404


def test_200_complete_structure(monkeypatch, tmp_path):
    _sid, eid = _setup(monkeypatch, tmp_path)
    fake = _install_fake(monkeypatch, [
        {
            "status": "CONCERN",
            "findings": [
                {"severity": "HIGH", "requirement": "material",
                 "source_ref": "execution.results[].product",
                 "message": "materiale non coerente"},
            ],
        },
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {
            "status": "CONCERN",
            "findings": [
                {"severity": "MEDIUM", "requirement": "color",
                 "source_ref": "model.hallucinated_path",
                 "message": "citazione non verificabile"},
            ],
        },
    ])
    client = TestClient(app)
    r = _post(client, eid)
    assert r.status_code == 200
    data = r.json()
    assert data["execution_id"] == eid
    assert data["offer_id"] == "offer-1"
    assert data["advisory_only"] is True
    assert data["policy_gate_decision"] == "APPROVED"
    assert isinstance(data["duration_ms"], int)
    agents = data["agents"]
    assert len(agents) == 3
    assert {a["id"] for a in agents} == {"intent_auditor", "evidence_analyst", "red_team_auditor"}
    by_id = {a["id"]: a for a in agents}
    assert by_id["intent_auditor"]["status"] == "CONCERN"
    assert by_id["evidence_analyst"]["status"] == "NO_CONCERN_FOUND"
    assert by_id["red_team_auditor"]["status"] == "CONCERN"
    for a in agents:
        assert a["name"]
        assert isinstance(a["summary"], str)
        assert isinstance(a["findings"], list)
        for f in a["findings"]:
            assert f["severity"] in ("HIGH", "MEDIUM", "LOW")
            assert f["requirement"]
            assert f["message"]
            # source_ref must be a whitelisted persisted path or omitted (None)
            assert f["source_ref"] is None or f["source_ref"] in ac.DATA_SOURCE_WHITELIST
    # unverified model citation must not be surfaced as evidence
    assert by_id["red_team_auditor"]["findings"][0]["source_ref"] is None
    # whitelisted persisted citation is preserved
    assert by_id["intent_auditor"]["findings"][0]["source_ref"] == "execution.results[].product"


def test_all_three_reports_present_when_one_unavailable(monkeypatch, tmp_path):
    _sid, eid = _setup(monkeypatch, tmp_path)
    fake = _install_fake(monkeypatch, [
        RuntimeError("down"),
        {"status": "CONCERN", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    ])
    client = TestClient(app)
    r = _post(client, eid)
    assert r.status_code == 200
    data = r.json()
    agents = {a["id"]: a for a in data["agents"]}
    assert set(agents) == {"intent_auditor", "evidence_analyst", "red_team_auditor"}
    assert agents["intent_auditor"]["status"] == "UNAVAILABLE"
    assert agents["evidence_analyst"]["status"] == "CONCERN"
    assert agents["red_team_auditor"]["status"] == "NO_CONCERN_FOUND"


def test_policy_gate_decision_included(monkeypatch, tmp_path):
    _make_db(monkeypatch, tmp_path)
    sessions.clear()
    sid = str(uuid.uuid4())
    eid = str(uuid.uuid4())
    mandate = {"query": "laptop", "max_price": 1200, "currency": "EUR", "product_type": "laptop", "material": "aluminum"}
    product = {"id": "p1", "title": "Laptop X", "material": "aluminum", "materials": ["aluminum"],
               "condition": "new"}
    offer = {"offer_id": "offer-1", "price": {"price": 999, "currency": "EUR"}, "condition": "new"}
    results = [{"product": product, "offer": offer,
                "gate": {"decision": "REJECTED", "rules": [], "policy_version": "2.0"}}]
    sessions[sid] = {"mandate": mandate, "locked": True, "locked_mandate": dict(mandate)}
    save_session(sid, "cat", "query", mandate, True, dict(mandate), {})
    save_execution(eid, "2026-01-01T00:00:00Z", mandate, results, sid)
    fake = _install_fake(monkeypatch, [
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    ])
    client = TestClient(app)
    r = _post(client, eid)
    assert r.status_code == 200
    assert r.json()["policy_gate_decision"] == "REJECTED"


def test_uses_persisted_data_not_client_payload(monkeypatch, tmp_path):
    mandate = {"query": "laptop", "max_price": 1200, "currency": "EUR", "product_type": "laptop", "material": "aluminum"}
    results = [{
        "product": {"id": "p1", "title": "Laptop X", "material": "steel", "materials": ["steel"], "condition": "new"},
        "offer": {"offer_id": "offer-1", "price": {"price": 999, "currency": "EUR"}, "condition": "new"},
        "gate": {"decision": "APPROVED", "rules": [], "policy_version": "2.0"},
    }]
    _sid, eid = _setup(monkeypatch, tmp_path, mandate=mandate, results=results)
    fake = _install_fake(monkeypatch, [
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    ])
    client = TestClient(app)
    # the client tries to inject conflicting product/mandate data
    r = _post(client, eid, extra={"product": {"material": "titanium"}, "mandate": {"max_price": 1}})
    assert r.status_code == 200
    data = r.json()
    assert data["policy_gate_decision"] == "APPROVED"
    # the council must have reviewed the PERSISTED product (steel), not the
    # client-injected material (titanium) or budget (1)
    contents = " ".join(str(c.get("contents")) for c in fake.aio.models.generate_content.calls)
    assert "steel" in contents
    assert "titanium" not in contents
    assert '"max_price": 1200' in contents
