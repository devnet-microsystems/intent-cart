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
