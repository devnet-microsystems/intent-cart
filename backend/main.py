import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from .storage import save_execution, get_execution, save_payment, get_payment_by_key, get_payment_by_id, update_payment_capture, save_session, get_session, list_executions

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .paypal_client import PayPalClient, PayPalAuthError
from .intent_parser import parse_intent
from .channel3_client import Channel3Client
from .policy_engine import evaluate

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="INTENT CART")

def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "")
    if not raw.strip():
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    origins = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if origin and origin != "*":
            origins.append(origin)
    return origins


_ACCESSORY_CATEGORY_SLUGS = {
    "laptop-sleeves",
    "keyboards",
    "wall-chargers",
    "computer-covers-skins",
    "tablet-computer-accessories",
    "computer-accessories",
    "power-adapters-chargers",
    "handheld-device-accessories",
    "input-devices",
}


def _category_slug(product) -> str:
    cat = product.get("category")
    if isinstance(cat, dict):
        return str(cat.get("slug", "") or "")
    return ""


def _is_laptop_category(product) -> bool:
    return _category_slug(product) == "laptops"


def _is_accessory_category(product) -> bool:
    return _category_slug(product) in _ACCESSORY_CATEGORY_SLUGS


def _reorder_products_for_computer_search(products: list, mandate: dict) -> list:
    """Reorder Channel3 results so genuine computers appear before accessories.

    Only applies when the mandate is for a laptop. Accessories are not
    removed; they are pushed to the end so genuine computers are visible
    first. Other categories (desktops, monitors) stay in the middle.
    """
    if mandate.get("product_type") != "laptop":
        return products
    laptops = [p for p in products if _is_laptop_category(p)]
    accessories = [p for p in products if _is_accessory_category(p)]
    others = [p for p in products if not _is_laptop_category(p) and not _is_accessory_category(p)]
    return laptops + others + accessories


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict = {}


@app.get("/health")
async def health():
    return {"status": "ok", "paypal_environment": settings.paypal_environment}


@app.post("/api/intent")
async def create_intent(body: dict):
    text = body.get("text") or body.get("query") or ""
    try:
        parsed = parse_intent(text) or {}
    except RuntimeError as exc:
        logging.warning("intent parsing failed: controlled error")
        raise HTTPException(status_code=502, detail="Intent parsing failed due to invalid upstream response") from exc
    mandate = dict(parsed)
    mandate["version"] = 1
    category = body.get("category")
    sf = body.get("structured_filters") or {}
    # deterministic merge: explicit user filters override parsed values
    for k, v in sf.items():
        if v is not None:
            mandate[k] = v
        elif k not in mandate:
            mandate[k] = v
    if "max_price" in sf:
        mandate["max_price"] = sf.get("max_price")
    sid = str(uuid.uuid4())
    sessions[sid] = {"mandate": mandate, "locked": False, "category": category, "structured_filters": sf}
    save_session(sid, category, text, mandate, False, None, sf)
    return {"session_id": sid, "mandate": mandate}


@app.post("/api/mandate/{session_id}/lock")
async def lock_mandate(session_id: str):
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    m = s["mandate"]
    # validate mandatory constraints
    sf = s.get("structured_filters") or {}
    if sf.get("size") is not None and m.get("size") is None:
        raise HTTPException(400, "Missing mandatory constraints")
    if sf.get("material") is not None and m.get("material") is None:
        raise HTTPException(400, "Missing mandatory constraints")
    if sf.get("product_type") is not None and m.get("product_type") is None:
        raise HTTPException(400, "Missing mandatory constraints")
    s["locked"] = True
    s["locked_mandate"] = dict(m)
    save_session(session_id, s.get("category"), s.get("mandate",{}).get("query"), s["mandate"], True, s["locked_mandate"], s.get("structured_filters", {}))
    return {"locked": True, "mandate": m}


@app.post("/api/search/{session_id}")
async def search(session_id: str):
    s = sessions.get(session_id)
    if not s or not s.get("locked"):
        raise HTTPException(400, "mandate not locked")
    mandate = s.get("locked_mandate") or s["mandate"]
    client = Channel3Client()
    try:
        products = await client.search(mandate.get("query", ""))
    except httpx.HTTPStatusError as exc:
        logging.warning("Channel3 upstream error: status %s", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Product discovery temporarily unavailable") from exc
    products = _reorder_products_for_computer_search(products, mandate)
    results = []
    for p in products:
        if not p.get("material") and isinstance(p.get("materials"), list) and p["materials"]:
            p["material"] = p["materials"][0]
        offers = p.get("offers") if isinstance(p.get("offers"), list) else []
        if not offers:
            offers = [{}]
        for idx, offer in enumerate(offers):
            if not isinstance(offer, dict):
                offer = {}
            if not offer.get("offer_id"):
                offer["offer_id"] = f"{p.get('id', 'product')}-{idx}"
            price_obj = offer.get("price") if isinstance(offer.get("price"), dict) else None
            price = price_obj.get("price") if isinstance(price_obj, dict) else (offer.get("price") if isinstance(offer, dict) else None)
            currency = (price_obj.get("currency") if isinstance(price_obj, dict) else None) or offer.get("currency") or mandate.get("currency", "EUR")
            if isinstance(offer, dict):
                offer["price"] = {"price": price, "currency": currency} if isinstance(price_obj, dict) else offer.get("price")
            ev = evaluate(mandate, {
                "price": price,
                "condition": (offer.get("condition") if isinstance(offer, dict) else None) or p.get("condition"),
                "specs": p.get("specs", {}),
                "title": p.get("title"),
                "description": p.get("description"),
                "brands": p.get("brands"),
                "structured_attributes": p.get("structured_attributes"),
                "key_features": p.get("key_features"),
                "materials": p.get("materials"),
                "variants": p.get("variants"),
                "category": p.get("category"),
            })
            item = {"product": p, "gate": ev, "offer": offer}
            results.append(item)
    execution_id = str(uuid.uuid4())
    save_execution(execution_id, datetime.now(timezone.utc).isoformat(), mandate, results, session_id)
    return {"execution_id": execution_id, "results": results}


def authorize_purchase(session_id: str, execution_id: str, offer_id: str):
    s = sessions.get(session_id)
    if s is None:
        ps = get_session(session_id)
        if ps:
            s = {"mandate": ps["mandate"], "locked": ps["locked"], "locked_mandate": ps["locked_mandate"], "category": ps["category"], "structured_filters": ps.get("structured_filters", {})}
            sessions[session_id] = s
    if not s or not s.get("locked"):
        raise HTTPException(400, "session not locked")
    mandate = s.get("locked_mandate") or s["mandate"]
    ex = get_execution(execution_id)
    if not ex:
        raise HTTPException(404, "execution not found")
    if ex.get("session_id") != session_id:
        raise HTTPException(403, "execution does not belong to session")
    if ex.get("mandate") != s.get("mandate") and ex.get("mandate") != mandate:
        raise HTTPException(403, "execution does not belong to session")
    offer = None
    product = None
    for r in ex.get("results", []):
        o = r.get("offer")
        if isinstance(o, dict) and o.get("offer_id") == offer_id:
            offer = o
            product = r.get("product")
            break
    if offer is None:
        for r in ex.get("results", []):
            for idx, o in enumerate(r.get("product", {}).get("offers", []) or []):
                if (o.get("offer_id") or f"{r['product'].get('id','offer')}-{idx}") == offer_id:
                    offer = o
                    product = r.get("product")
                    break
            if offer is not None:
                break
    if offer is None or not (isinstance(offer, dict) and offer.get("offer_id")):
        raise HTTPException(404, "offer not found")
    price_obj = offer.get("price") if isinstance(offer, dict) else None
    price = price_obj.get("price") if isinstance(price_obj, dict) else (offer.get("price") if isinstance(offer, dict) else None)
    ev = evaluate(mandate, {
        "price": price,
        "condition": (offer.get("condition") if isinstance(offer, dict) else None) or (product.get("condition") if isinstance(product, dict) else None),
        "specs": (product.get("specs") if isinstance(product, dict) else {}) or {},
        "color": product.get("color") or product.get("colour"),
        "variants": product.get("variants"),
        "structured_attributes": product.get("structured_attributes"),
        "_provenance": product.get("_provenance"),
    })
    if ev.get("decision") != "APPROVED":
        raise HTTPException(403, "offer not approved")
    if price is None:
        raise HTTPException(400, "invalid price")
    currency = (price_obj.get("currency") if isinstance(price_obj, dict) else None) or mandate.get("currency", "EUR")
    title = (product.get("title") if isinstance(product, dict) else None) or (product.get("name") if isinstance(product, dict) else None) or "product"
    return {"session_id": session_id, "execution_id": execution_id, "offer_id": offer_id, "title": title, "price": price, "currency": currency, "mandate_reference": mandate}


@app.post("/api/orders/checkout")
async def checkout_from_selection(body: dict):
    session_id = body.get("session_id")
    execution_id = body.get("execution_id")
    offer_id = body.get("offer_id")
    auth = authorize_purchase(session_id, execution_id, offer_id)
    key = f"{session_id}:{execution_id}:{offer_id}"
    existing = get_payment_by_key(key)
    if existing:
        if existing.get("status") == "PAYMENT_CAPTURED":
            raise HTTPException(status_code=400, detail="Payment already captured")
        return {"order_id": existing["paypal_order_id"], "approve_url": None, "status": "CREATED", "payment_id": existing["id"]}
    client = PayPalClient()
    try:
        result = await client.create_order(amount=str(auth["price"]), currency=auth["currency"])
    except PayPalAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    order_id = result.get("order_id") or result.get("id")
    approve_url = None
    for link in result.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            approve_url = link.get("href")
    if not approve_url:
        approve_url = result.get("approve_url")
    if not approve_url or not isinstance(approve_url, str) or not approve_url.startswith("https://"):
        raise HTTPException(status_code=502, detail="PayPal approval link unavailable")
    payment_id = str(uuid.uuid4())
    save_payment({
        "id": payment_id,
        "session_id": session_id,
        "execution_id": execution_id,
        "offer_id": offer_id,
        "paypal_order_id": order_id,
        "status": "CREATED",
        "amount": float(auth["price"]),
        "currency": auth["currency"],
        "mandate_ref": str(auth["mandate_reference"]),
        "idempotency_key": key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"order_id": order_id, "approve_url": approve_url, "status": "CREATED", "payment_id": payment_id}

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
    if settings.paypal_environment != "sandbox":
        raise HTTPException(status_code=400, detail="Only sandbox mode is allowed")
    raise HTTPException(
        status_code=410,
        detail="Direct PayPal order capture is disabled. Use /api/orders/capture with an authorized payment_id.",
    )



@app.post("/api/orders/capture")
async def capture_payment(body: dict):
    payment_id = body.get("payment_id")
    payment = get_payment_by_id(payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    if payment.get("status") == "PAYMENT_CAPTURED":
        return {"status": "PAYMENT_CAPTURED", "payment_id": payment["id"], "paypal_order_id": payment["paypal_order_id"], "capture_id": payment.get("capture_id"), "amount": f"{float(payment.get('captured_amount', payment['amount'])):.2f}", "currency": payment.get("captured_currency", payment["currency"])}
    session_id = payment["session_id"]
    execution_id = payment["execution_id"]
    offer_id = payment["offer_id"]
    authorize_purchase(session_id, execution_id, offer_id)
    client = PayPalClient()
    try:
        result = await client.capture_order(payment["paypal_order_id"], expected_amount=f"{float(payment['amount']):.2f}", expected_currency=payment["currency"])
    except PayPalAuthError as exc:
        msg = str(exc)
        if "APPROVED" in msg:
            raise HTTPException(status_code=400, detail=msg)
        raise HTTPException(status_code=502, detail=msg)
    capture_id = result.get("capture_id")
    status = result.get("status")
    amount = result.get("amount")
    currency = result.get("currency")
    if status != "COMPLETED" or not capture_id:
        raise HTTPException(400, "capture not completed")
    if float(amount) != float(payment["amount"]) or currency != payment["currency"]:
        raise HTTPException(400, "amount/currency mismatch")
    update_payment_capture(payment["id"], capture_id, float(amount), currency, "PAYMENT_CAPTURED")
    return {"status": "PAYMENT_CAPTURED", "payment_id": payment["id"], "paypal_order_id": payment["paypal_order_id"], "capture_id": capture_id, "amount": f"{float(amount):.2f}", "currency": currency}


@app.get("/api/executions")
async def list_saved_executions():
    return list_executions()


@app.get("/api/executions/{execution_id}")
async def get_saved_execution(execution_id: str):
    data = get_execution(execution_id)
    if not data:
        raise HTTPException(status_code=404, detail="execution not found")
    return data
