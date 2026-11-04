import uuid
from backend.storage import save_execution, get_execution, init_db
import tempfile, os
from pathlib import Path
import backend.storage as st

def test_save_and_get(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setattr(st, "DB_PATH", db)
    init_db()
    eid = str(uuid.uuid4())
    save_execution(eid, "2026-01-01T00:00:00Z", {"q": "x"}, [{"gate": {"decision": "BLOCKED"}}])
    data = get_execution(eid)
    assert data["execution_id"] == eid
    assert data["results"][0]["gate"]["decision"] == "BLOCKED"

def test_missing():
    import backend.storage as st
    st.init_db()
    assert st.get_execution("nope") is None
