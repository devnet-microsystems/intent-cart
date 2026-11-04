import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "executions.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS executions (execution_id TEXT PRIMARY KEY, timestamp TEXT, mandate TEXT, results TEXT)"
    )
    conn.commit()
    conn.close()


def save_execution(execution_id, timestamp, mandate, results):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO executions (execution_id, timestamp, mandate, results) VALUES (?,?,?,?)",
        (execution_id, timestamp, json.dumps(mandate), json.dumps(results)),
    )
    conn.commit()
    conn.close()


def get_execution(execution_id):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT execution_id, timestamp, mandate, results FROM executions WHERE execution_id=?",
        (execution_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"execution_id": row[0], "timestamp": row[1], "mandate": json.loads(row[2]), "results": json.loads(row[3])}
