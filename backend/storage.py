import json
import os
import sqlite3
from pathlib import Path

_DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "executions.db"


def db_path() -> Path:
    raw = os.getenv("INTENT_CART_DB_PATH", "").strip()
    path = Path(raw).expanduser() if raw else _DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(db_path())


# Backward-compatible alias; callers should prefer db_path().
DB_PATH = _DEFAULT_DB_PATH


def init_db():
    conn = _connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS executions (execution_id TEXT PRIMARY KEY, timestamp TEXT, mandate TEXT, results TEXT, session_id TEXT)"
    )
    conn.commit()
    # migrate older schema
    cols = [r[1] for r in conn.execute("PRAGMA table_info(executions)").fetchall()]
    for col, typ in [("session_id", "TEXT")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE executions ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()


def save_execution(execution_id, timestamp, mandate, results, session_id=None):
    init_db()
    conn = _connect()
    conn.execute(
        "INSERT INTO executions (execution_id, timestamp, mandate, results, session_id) VALUES (?,?,?,?,?)",
        (execution_id, timestamp, json.dumps(mandate), json.dumps(results), session_id),
    )
    conn.commit()
    conn.close()


def list_executions():
    init_db()
    conn = _connect()
    rows = conn.execute(
        "SELECT execution_id, timestamp, session_id, json_array_length(results), mandate FROM executions ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        query = "Previous search"
        try:
            mandate = json.loads(r[4]) if r[4] else None
            if mandate and isinstance(mandate, dict) and mandate.get("query"):
                query = mandate["query"]
        except (json.JSONDecodeError, TypeError):
            query = "Previous search"
        results.append(
            {
                "execution_id": r[0],
                "timestamp": r[1],
                "session_id": r[2],
                "results_count": r[3] or 0,
                "query": query,
            }
        )
    return results


def get_execution(execution_id):
    init_db()
    conn = _connect()
    row = conn.execute(
        "SELECT execution_id, timestamp, mandate, results, session_id FROM executions WHERE execution_id=?",
        (execution_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"execution_id": row[0], "timestamp": row[1], "mandate": json.loads(row[2]), "results": json.loads(row[3]), "session_id": row[4]}


def init_payments():
    conn = _connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, session_id TEXT, execution_id TEXT, offer_id TEXT, paypal_order_id TEXT, status TEXT, amount REAL, currency TEXT, mandate_ref TEXT, idempotency_key TEXT UNIQUE, created_at TEXT, capture_id TEXT, captured_amount REAL, captured_currency TEXT, captured_at TEXT)"
    )
    # migrate older schema
    cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
    for col, typ in [("capture_id","TEXT"),("captured_amount","REAL"),("captured_currency","TEXT"),("captured_at","TEXT"),("stale_reason","TEXT"),("paypal_request_id","TEXT")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE payments ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()


def save_payment(record):
    init_payments()
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO payments (id, session_id, execution_id, offer_id, paypal_order_id, status, amount, currency, mandate_ref, idempotency_key, paypal_request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (record["id"], record["session_id"], record["execution_id"], record["offer_id"], record["paypal_order_id"], record["status"], record["amount"], record["currency"], record["mandate_ref"], record["idempotency_key"], record.get("paypal_request_id"), record["created_at"]),
    )
    conn.commit()
    conn.close()


def get_payment_by_key(key):
    init_payments()
    conn = _connect()
    row = conn.execute("SELECT id, paypal_order_id, status, amount, currency, paypal_request_id, stale_reason FROM payments WHERE idempotency_key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "paypal_order_id": row[1], "status": row[2], "amount": row[3], "currency": row[4], "paypal_request_id": row[5], "stale_reason": row[6]}


def mark_payment_stale(payment_id, reason, free_key=True):
    init_payments()
    conn = _connect()
    row = conn.execute("SELECT idempotency_key FROM payments WHERE id=?", (payment_id,)).fetchone()
    if row:
        old_key = row[0]
        if free_key:
            # Free up the idempotency_key so a replacement payment can be inserted.
            # The stale key is preserved with a suffix for audit traceability.
            new_key = old_key + ":stale:" + payment_id[:8]
        else:
            # Keep the key so retries can still find this payment.
            new_key = old_key
        conn.execute(
            "UPDATE payments SET stale_reason=?, idempotency_key=? WHERE id=?",
            (reason, new_key, payment_id),
        )
    conn.commit()
    conn.close()


def reserve_payment_atomic(payment_id, session_id, execution_id, offer_id, paypal_request_id, amount, currency, mandate_ref, idempotency_key, created_at):
    """Atomically insert a payment record using BEGIN IMMEDIATE for serialization.

    Returns the rowid if inserted, None if the idempotency_key already exists
    (another request beat us to it). Raises sqlite3.IntegrityError on constraint
    violations. Uses an immediate transaction to serialize claims for the same
    idempotency key, preventing two requests from creating independent PayPal
    orders for the same logical checkout.
    """
    init_payments()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO payments (id, session_id, execution_id, offer_id, paypal_order_id, status, amount, currency, mandate_ref, idempotency_key, paypal_request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (payment_id, session_id, execution_id, offer_id, None, "CREATED", amount, currency, mandate_ref, idempotency_key, paypal_request_id, created_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def mark_payment_replaced(payment_id, replacement_id):
    """Mark a stale payment as replaced by a new payment, preserving audit trail."""
    init_payments()
    conn = _connect()
    conn.execute(
        "UPDATE payments SET stale_reason=?, idempotency_key=? WHERE id=?",
        ("replaced_by:" + replacement_id, payment_id + ":replaced", payment_id),
    )
    conn.commit()
    conn.close()


def get_payment_by_id(payment_id):
    init_payments()
    conn = _connect()
    row = conn.execute("SELECT id, session_id, execution_id, offer_id, paypal_order_id, status, amount, currency, mandate_ref, idempotency_key, capture_id, captured_amount, captured_currency, stale_reason FROM payments WHERE id=?", (payment_id,)).fetchone()
    conn.close()
    return {"id": row[0], "session_id": row[1], "execution_id": row[2], "offer_id": row[3], "paypal_order_id": row[4], "status": row[5], "amount": row[6], "currency": row[7], "mandate_ref": row[8], "idempotency_key": row[9], "capture_id": row[10], "captured_amount": row[11], "captured_currency": row[12], "stale_reason": row[13]}


def update_payment_capture(payment_id, capture_id, amount, currency, status="PAYMENT_CAPTURED"):
    init_payments()
    conn = _connect()
    conn.execute("UPDATE payments SET capture_id=?, captured_amount=?, captured_currency=?, status=?, captured_at=? WHERE id=?", (capture_id, amount, currency, status, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), payment_id))
    conn.commit()
    conn.close()


def init_sessions():
    conn = _connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        category TEXT,
        query TEXT,
        mandate TEXT,
        locked_mandate TEXT,
        locked INTEGER,
        structured_filters TEXT
    )""")
    conn.commit()
    conn.close()

def save_session(session_id, category, query, mandate, locked, locked_mandate=None, structured_filters=None):
    init_sessions()
    conn = _connect()
    conn.execute("INSERT OR REPLACE INTO sessions (session_id,category,query,mandate,locked_mandate,locked,structured_filters) VALUES (?,?,?,?,?,?,?)",
        (session_id, category, query, json.dumps(mandate), json.dumps(locked_mandate) if locked_mandate else None, 1 if locked else 0, json.dumps(structured_filters) if structured_filters is not None else None))
    conn.commit()
    conn.close()

def get_session(session_id):
    init_sessions()
    conn = _connect()
    row = conn.execute("SELECT session_id,category,query,mandate,locked_mandate,locked,structured_filters FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"session_id": row[0], "category": row[1], "query": row[2], "mandate": json.loads(row[3]), "locked_mandate": json.loads(row[4]) if row[4] else None, "locked": bool(row[5]), "structured_filters": json.loads(row[6]) if row[6] else {}}
