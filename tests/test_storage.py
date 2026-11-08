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
