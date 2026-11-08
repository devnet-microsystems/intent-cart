from pathlib import Path

import backend.storage as storage


def test_db_path_uses_env_and_creates_parent(tmp_path, monkeypatch):
    target = tmp_path / "volume" / "nested" / "intent.db"
    monkeypatch.setenv("INTENT_CART_DB_PATH", str(target))
    p = storage.db_path()
    assert p == target
    assert p.parent.exists()


def test_db_path_defaults_to_repo_local_file(monkeypatch):
    monkeypatch.delenv("INTENT_CART_DB_PATH", raising=False)
    p = storage.db_path()
    assert p.name == "executions.db"
    assert p.parent.name != "volume"


def test_configured_db_path_is_used_for_operations(tmp_path, monkeypatch):
    target = tmp_path / "data" / "test.db"
    monkeypatch.setenv("INTENT_CART_DB_PATH", str(target))
    storage.init_db()
    storage.save_execution("e1", "2026-01-01T00:00:00Z", {"q": "x"}, [{"ok": True}], session_id="s1")
    got = storage.get_execution("e1")
    assert got is not None
    assert got["execution_id"] == "e1"
    assert target.exists()
    # existing local DB path is not modified by this isolated temp DB
    local_default = Path(__file__).resolve().parents[1] / "executions.db"
    if local_default.exists():
        assert target != local_default
