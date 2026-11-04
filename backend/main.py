import logging
import uuid
from datetime import datetime, timezone
from .storage import save_execution, get_execution

from fastapi import FastAPI, HTTPException, Query

from .config import settings
from .paypal_client import PayPalClient, PayPalAuthError
from .intent_parser import parse_intent
from .channel3_client import Channel3Client
from .policy_engine import evaluate

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="INTENT CART")

sessions: dict = {}


@app.get("/health")
async def health():
    return {"status": "ok", "paypal_environment": settings.paypal_environment}


@app.post("/api/intent")
async def create_intent(body: dict):
    text = body.get("text", "")
    mandate = parse_intent(text)
    mandate["version"] = 1
    sid = str(uuid.uuid4())
    sessions[sid] = {"mandate": mandate, "locked": False}
    return {"session_id": sid, "mandate": mandate}


@app.post("/api/mandate/{session_id}/lock")
async def lock_mandate(session_id: str):
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s["locked"] = True
    return {"locked": True, "mandate": s["mandate"]}


@app.post("/api/search/{session_id}")
async def search(session_id: str):
    s = sessions.get(session_id)
    if not s or not s["locked"]:
        raise HTTPException(400, "mandate not locked")
    client = Channel3Client()
    products = await client.search(s["mandate"].get("query", ""))
    results = []
    for p in products:
        offer = p.get("offer", p)
        ev = evaluate(s["mandate"], {
            "price": offer.get("price"),
            "condition": p.get("condition"),
            "specs": p.get("specs", {}),
        })
        results.append({"product": p, "gate": ev})
    execution_id = str(uuid.uuid4())
    save_execution(execution_id, datetime.now(timezone.utc).isoformat(), s["mandate"], results)
    return {"execution_id": execution_id, "results": results}


@app.post("/api/orders/checkout")
async def checkout_from_selection(body: dict):
    session_id = body.get("session_id")
    product = body.get("product")
    s = sessions.get(session_id)
    if not s or not s["locked"]:
        raise HTTPException(400, "mandate not locked")
    mandate = s["mandate"]
    offer = product.get("offer", product) if isinstance(product, dict) else {}
    ev = evaluate(mandate, {
        "price": offer.get("price"),
        "condition": (product or {}).get("condition"),
        "specs": (product or {}).get("specs", {}),
    })
    if ev["decision"] != "APPROVED":
        raise HTTPException(403, "Product rejected by policy gate")
    price = offer.get("price")
    currency = mandate.get("currency", "EUR")
    if price is None or float(price) > float(mandate.get("max_price", 0)):
        raise HTTPException(403, "Price exceeds mandate")
    client = PayPalClient()
    try:
        result = await client.create_order(amount=str(price), currency=currency)
    except PayPalAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result


@app.post("/api/orders/test")
async def create_test_order():
    if settings.paypal_environment != "sandbox":
        raise HTTPException(status_code=400, detail="Only sandbox mode is allowed")
    client = PayPalClient()
    try:
        result = await client.create_order(amount="1.00", currency="EUR")
    except PayPalAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result


@app.get("/checkout/return")
async def checkout_return(token: str = Query(...)):
    client = PayPalClient()
    try:
        result = await client.get_order(token)
    except PayPalAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"order_id": result["order_id"], "status": result["status"]}


@app.get("/checkout/cancel")
async def checkout_cancel():
    return {"status": "cancelled"}


@app.post("/api/orders/{order_id}/capture")
async def capture_order(order_id: str):
    client = PayPalClient()
    try:
        result = await client.capture_order(order_id)
    except PayPalAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result


@app.get("/api/executions/{execution_id}")
async def get_saved_execution(execution_id: str):
    data = get_execution(execution_id)
    if not data:
        raise HTTPException(status_code=404, detail="execution not found")
    return data
