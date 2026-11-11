import uuid
from pathlib import Path

import backend.storage as st


def _isolate_db(tmp_path, monkeypatch, name="t.db"):
    db = tmp_path / name
    monkeypatch.setenv("INTENT_CART_DB_PATH", str(db))
    return db


def test_save_and_get(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch)
    st.init_db()
    eid = str(uuid.uuid4())
    st.save_execution(eid, "2026-01-01T00:00:00Z", {"q": "x"}, [{"gate": {"decision": "BLOCKED"}}])
    data = st.get_execution(eid)
    assert data["execution_id"] == eid
    assert data["results"][0]["gate"]["decision"] == "BLOCKED"
    # Regression: effective DB path must be the isolated temp path, not local default.
    assert st.db_path() == db
    assert db.exists()
    assert db != (Path(__file__).resolve().parents[1] / "executions.db")


def test_missing(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "missing.db")
    st.init_db()
    assert st.get_execution("nope") is None
    assert st.db_path() == db


def test_list_executions_returns_query_from_mandate(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "list_query.db")
    st.init_db()
    eid1 = str(uuid.uuid4())
    st.save_execution(
        eid1,
        "2026-01-01T00:00:00Z",
        {"query": "socks", "product_type": "socks"},
        [{"gate": {"decision": "APPROVED"}}],
    )
    eid2 = str(uuid.uuid4())
    st.save_execution(
        eid2,
        "2026-01-02T00:00:00Z",
        {"query": "laptop", "product_type": "laptop"},
        [{"gate": {"decision": "REJECTED"}}],
    )
    items = st.list_executions()
    by_id = {item["execution_id"]: item for item in items}
    assert by_id[eid1]["query"] == "socks"
    assert by_id[eid2]["query"] == "laptop"
    assert by_id[eid1]["results_count"] == 1
    assert by_id[eid2]["results_count"] == 1


def test_list_executions_fallback_query_when_mandate_missing(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "list_no_query.db")
    st.init_db()
    eid = str(uuid.uuid4())
    # Save with empty/no mandate
    st.save_execution(eid, "2026-01-01T00:00:00Z", {}, [{"gate": {"decision": "BLOCKED"}}])
    items = st.list_executions()
    by_id = {item["execution_id"]: item for item in items}
    assert by_id[eid]["query"] == "Previous search"


def test_list_executions_fallback_query_when_mandate_malformed(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "list_malformed.db")
    st.init_db()
    eid = str(uuid.uuid4())
    # Directly insert malformed mandate JSON to test safe handling
    conn = st._connect()
    conn.execute(
        "INSERT INTO executions (execution_id, timestamp, mandate, results, session_id) VALUES (?,?,?,?,?)",
        (eid, "2026-01-01T00:00:00Z", "{not valid json", "[]", None),
    )
    conn.commit()
    conn.close()
    items = st.list_executions()
    by_id = {item["execution_id"]: item for item in items}
    assert by_id[eid]["query"] == "Previous search"


# --- Attestation persistence (append-only audit events) ---

def test_save_and_get_attestation(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "attest.db")
    st.init_attestations()
    aid = str(uuid.uuid4())
    eid = str(uuid.uuid4())
    oid = "offer-1"
    sid = str(uuid.uuid4())
    st.save_attestation({
        "id": aid,
        "session_id": sid,
        "execution_id": eid,
        "offer_id": oid,
        "rule_id": "min_size",
        "attested_value": "17 inches",
        "source_url": "https://seller.example.com/product/123",
        "explanation": "Measured screen diagonal personally",
        "timestamp": "2026-01-01T00:00:00Z",
        "mandate_fingerprint": "fp-mandate",
        "offer_evidence_fingerprint": "fp-offer",
        "policy_version": "2.0",
    })
    data = st.get_attestation(aid)
    assert data is not None
    assert data["execution_id"] == eid
    assert data["offer_id"] == oid
    assert data["session_id"] == sid
    assert data["rule_id"] == "min_size"
    assert data["attested_value"] == "17 inches"
    assert data["source_url"] == "https://seller.example.com/product/123"
    assert data["mandate_fingerprint"] == "fp-mandate"
    assert data["offer_evidence_fingerprint"] == "fp-offer"
    assert data["policy_version"] == "2.0"


def test_get_attestation_missing(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "attest_missing.db")
    st.init_attestations()
    assert st.get_attestation("no-id") is None


def test_attestations_are_append_only(monkeypatch, tmp_path):
    """Attestations must never be overwritten — INSERT OR REPLACE is forbidden."""
    db = _isolate_db(tmp_path, monkeypatch, "attest_append.db")
    st.init_attestations()
    eid = str(uuid.uuid4())
    oid = "offer-1"
    sid = str(uuid.uuid4())
    # Save two attestations for the same rule
    st.save_attestation({
        "id": str(uuid.uuid4()), "session_id": sid, "execution_id": eid,
        "offer_id": oid, "rule_id": "min_size",
        "attested_value": "17 inches", "source_url": "https://seller.example.com/1",
        "explanation": "first", "timestamp": "2026-01-01T00:00:00Z",
        "mandate_fingerprint": "fp-m", "offer_evidence_fingerprint": "fp-o",
        "policy_version": "2.0",
    })
    st.save_attestation({
        "id": str(uuid.uuid4()), "session_id": sid, "execution_id": eid,
        "offer_id": oid, "rule_id": "min_size",
        "attested_value": "18 inches", "source_url": "https://seller.example.com/2",
        "explanation": "second", "timestamp": "2026-01-02T00:00:00Z",
        "mandate_fingerprint": "fp-m", "offer_evidence_fingerprint": "fp-o",
        "policy_version": "2.0",
    })
    items = st.list_attestations(session_id=sid, execution_id=eid, offer_id=oid, rule_id="min_size")
    assert len(items) == 2, f"Expected 2 append-only attestations, got {len(items)}"


def test_list_attestations_by_session(monkeypatch, tmp_path):
    db = _isolate_db(tmp_path, monkeypatch, "attest_list.db")
    st.init_attestations()
    sid = str(uuid.uuid4())
    eid1 = str(uuid.uuid4())
    eid2 = str(uuid.uuid4())
    st.save_attestation({
        "id": str(uuid.uuid4()), "session_id": sid, "execution_id": eid1,
        "offer_id": "o1", "rule_id": "min_size", "attested_value": "v1",
        "source_url": "https://s.example.com/1", "explanation": "r1",
        "timestamp": "2026-01-01T00:00:00Z", "mandate_fingerprint": "fp-m",
        "offer_evidence_fingerprint": "fp-o", "policy_version": "2.0",
    })
    st.save_attestation({
        "id": str(uuid.uuid4()), "session_id": sid, "execution_id": eid2,
        "offer_id": "o2", "rule_id": "material", "attested_value": "v2",
        "source_url": "https://s.example.com/2", "explanation": "r2",
        "timestamp": "2026-01-02T00:00:00Z", "mandate_fingerprint": "fp-m",
        "offer_evidence_fingerprint": "fp-o", "policy_version": "2.0",
    })
    # Other session should not appear
    st.save_attestation({
        "id": str(uuid.uuid4()), "session_id": "other-session", "execution_id": str(uuid.uuid4()),
        "offer_id": "o3", "rule_id": "color", "attested_value": "v3",
        "source_url": "https://s.example.com/3", "explanation": "r3",
        "timestamp": "2026-01-03T00:00:00Z", "mandate_fingerprint": "fp-m",
        "offer_evidence_fingerprint": "fp-o", "policy_version": "2.0",
    })
    items = st.list_attestations(session_id=sid)
    assert len(items) == 2
    rule_ids = {it["rule_id"] for it in items}
    assert rule_ids == {"min_size", "material"}
