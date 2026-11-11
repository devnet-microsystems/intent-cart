import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from .storage import save_execution, get_execution, save_payment, get_payment_by_key, get_payment_by_id, update_payment_capture, mark_payment_stale, save_session, get_session, list_executions, _connect, reserve_payment_atomic, init_attestations, save_attestation, get_attestation, list_attestations, get_attestations_for_rule

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .paypal_client import PayPalClient, PayPalAuthError
from .intent_parser import parse_intent
from .channel3_client import Channel3Client
from .policy_engine import evaluate, build_product_evidence_payload, canonical_fingerprint, POLICY_VERSION
from .agent_council import AgentCouncil, ALL_AGENTS, AGENT_NAMES, sanitize_findings

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
            ev = evaluate(mandate, build_product_evidence_payload(p, offer))
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
    gate_entry = None
    for r in ex.get("results", []):
        o = r.get("offer")
        if isinstance(o, dict) and o.get("offer_id") == offer_id:
            offer = o
            product = r.get("product")
            gate_entry = r
            break
    if offer is None:
        for r in ex.get("results", []):
            for idx, o in enumerate(r.get("product", {}).get("offers", []) or []):
                if (o.get("offer_id") or f"{r['product'].get('id','offer')}-{idx}") == offer_id:
                    offer = o
                    product = r.get("product")
                    gate_entry = r
                    break
            if offer is not None:
                break
    if offer is None or not (isinstance(offer, dict) and offer.get("offer_id")):
        raise HTTPException(404, "offer not found")

    # Load current attestations for this execution/offer/session.
    attestations = list_attestations(
        session_id=session_id,
        execution_id=execution_id,
        offer_id=offer_id,
    )

    # Compute fingerprints for attestation validation.
    mandate_fp = canonical_fingerprint(mandate)
    evidence_payload = build_product_evidence_payload(product if isinstance(product, dict) else {}, offer if isinstance(offer, dict) else None)
    evidence_fp = canonical_fingerprint(evidence_payload)

    # Re-evaluate the Policy Gate with current attestations.
    ev = evaluate(
        mandate,
        evidence_payload,
        attestations,
    )

    if ev.get("decision") != "APPROVED":
        raise HTTPException(403, "offer not approved")
    price_obj = offer.get("price") if isinstance(offer, dict) else None
    price = price_obj.get("price") if isinstance(price_obj, dict) else (offer.get("price") if isinstance(offer, dict) else None)
    if price is None:
        raise HTTPException(400, "invalid price")
    currency = (price_obj.get("currency") if isinstance(price_obj, dict) else None) or mandate.get("currency", "EUR")
    title = (product.get("title") if isinstance(product, dict) else None) or (product.get("name") if isinstance(product, dict) else None) or "product"
    return {"session_id": session_id, "execution_id": execution_id, "offer_id": offer_id, "title": title, "price": price, "currency": currency, "mandate_reference": mandate, "gate_decision": ev}


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

        existing_status = existing.get("status")
        existing_paypal_order_id = existing.get("paypal_order_id")
        existing_paypal_request_id = existing.get("paypal_request_id")

        # Retry: existing reservation has no paypal_order_id (creation timed out
        # or returned ambiguous error). Retry with the SAME request ID so PayPal
        # idempotency returns the original order instead of creating a new one.
        if existing_status == "CREATED" and not existing_paypal_order_id and existing_paypal_request_id:
            client = PayPalClient()
            try:
                result = await client.create_order(
                    amount=str(auth["price"]),
                    currency=auth["currency"],
                    paypal_request_id=existing_paypal_request_id,
                )
            except PayPalAuthError as exc:
                # Creation failed again — mark ambiguous, keep idempotency key.
                mark_payment_stale(existing["id"], "paypal_creation_retry_failed:" + str(exc), free_key=False)
                raise HTTPException(status_code=502, detail=str(exc))
            order_id = result.get("order_id") or result.get("id")
            approve_url = None
            for link in result.get("links", []):
                if link.get("rel") in ("approve", "payer-action"):
                    approve_url = link.get("href")
            if not approve_url:
                approve_url = result.get("approve_url")
            if not approve_url or not isinstance(approve_url, str) or not approve_url.startswith("https://"):
                # Order created but approval link missing — preserve order ID, fail closed.
                conn = _connect()
                conn.execute("UPDATE payments SET paypal_order_id=? WHERE id=?", (order_id, existing["id"]))
                conn.commit()
                conn.close()
                mark_payment_stale(existing["id"], "paypal_approval_link_unavailable", free_key=False)
                raise HTTPException(status_code=502, detail="PayPal approval link unavailable")
            # Persist order ID immediately.
            conn = _connect()
            conn.execute("UPDATE payments SET paypal_order_id=? WHERE id=?", (order_id, existing["id"]))
            conn.commit()
            conn.close()
            return {"order_id": order_id, "approve_url": approve_url, "status": "CREATED", "payment_id": existing["id"]}

        if existing_paypal_order_id:
            client = PayPalClient()
            try:
                order_status = await client.get_order(existing_paypal_order_id)
            except PayPalAuthError as exc:
                if exc.status_code == 404:
                    # PayPal RESOURCE_NOT_FOUND → stale order, recover below
                    mark_payment_stale(existing["id"], "paypal_resource_not_found")
                elif exc.status_code in (401, 403):
                    # Authentication failure → fail closed, never replace
                    raise HTTPException(status_code=502, detail=str(exc))
                else:
                    # Network failure or PayPal 5xx → fail closed, never replace
                    raise HTTPException(status_code=502, detail=str(exc))
            else:
                paypal_status = order_status.get("status")
                if paypal_status == "CREATED":
                    # Order still valid on PayPal; reuse it.
                    return {"order_id": existing_paypal_order_id, "approve_url": None, "status": "CREATED", "payment_id": existing["id"]}
                if paypal_status == "APPROVED":
                    # Buyer already approved; preserve state, do not duplicate.
                    return {"order_id": existing_paypal_order_id, "approve_url": None, "status": "APPROVED", "payment_id": existing["id"]}
                if paypal_status == "COMPLETED":
                    # Already captured; reject duplicate checkout.
                    raise HTTPException(status_code=400, detail="Payment already captured")
                if paypal_status == "VOIDED":
                    # Terminal state — controlled stale recovery.
                    mark_payment_stale(existing["id"], "paypal_order_voided")
                elif paypal_status in ("SAVED", "PAYER_ACTION_REQUIRED"):
                    # Never silently convert to CREATED — fail closed.
                    raise HTTPException(status_code=502, detail=f"PayPal order in non-recoverable state: {paypal_status}")
                else:
                    # Unknown or missing status — fail closed explicitly.
                    raise HTTPException(status_code=502, detail=f"Unknown PayPal order status: {paypal_status}")
        else:
            # CREATED local payment without paypal_order_id and without request ID → not retryable.
            if existing_status == "CREATED":
                raise HTTPException(status_code=502, detail="Existing payment has no PayPal order reference or request ID")

    # Atomic checkout reservation — reserve BEFORE calling PayPal.
    payment_id = str(uuid.uuid4())
    # UUID-based identifier: 36 chars, well under PayPal's 108-char limit.
    paypal_request_id = f"intent-cart-{payment_id}"
    if len(paypal_request_id) > 108:
        paypal_request_id = paypal_request_id[:108]
    created_at = datetime.now(timezone.utc).isoformat()

    reserved = reserve_payment_atomic(
        payment_id=payment_id,
        session_id=session_id,
        execution_id=execution_id,
        offer_id=offer_id,
        paypal_request_id=paypal_request_id,
        amount=float(auth["price"]),
        currency=auth["currency"],
        mandate_ref=str(auth["mandate_reference"]),
        idempotency_key=key,
        created_at=created_at,
    )

    if not reserved:
        # Another request already reserved this key — check existing payment.
        existing2 = get_payment_by_key(key)
        if existing2:
            if existing2.get("status") == "PAYMENT_CAPTURED":
                raise HTTPException(status_code=400, detail="Payment already captured")
            if existing2.get("paypal_order_id"):
                return {"order_id": existing2["paypal_order_id"], "approve_url": None, "status": existing2.get("status", "CREATED"), "payment_id": existing2["id"]}
            if existing2.get("paypal_request_id"):
                # Retry path: reservation exists but order creation hasn't completed.
                raise HTTPException(status_code=502, detail="Checkout creation in progress, retry")
            raise HTTPException(status_code=502, detail="Existing payment has no PayPal order reference")
        raise HTTPException(status_code=502, detail="Checkout reservation conflict")

    # Call PayPal to create order with idempotency header.
    client = PayPalClient()
    try:
        result = await client.create_order(amount=str(auth["price"]), currency=auth["currency"], paypal_request_id=paypal_request_id)
    except PayPalAuthError as exc:
        # PayPal creation failed — mark reservation ambiguous for audit.
        # Do not free the idempotency_key so retries can find this payment.
        mark_payment_stale(payment_id, "paypal_creation_failed:" + str(exc), free_key=False)
        raise HTTPException(status_code=502, detail=str(exc))

    order_id = result.get("order_id") or result.get("id")
    approve_url = None
    for link in result.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            approve_url = link.get("href")
    if not approve_url:
        approve_url = result.get("approve_url")
    if not approve_url or not isinstance(approve_url, str) or not approve_url.startswith("https://"):
        # Order created but approval link missing — persist order ID, fail closed.
        conn = _connect()
        conn.execute("UPDATE payments SET paypal_order_id=? WHERE id=?", (order_id, payment_id))
        conn.commit()
        conn.close()
        mark_payment_stale(payment_id, "paypal_approval_link_unavailable", free_key=False)
        raise HTTPException(status_code=502, detail="PayPal approval link unavailable")

    # Persist the PayPal order ID atomically.
    conn = _connect()
    conn.execute("UPDATE payments SET paypal_order_id=? WHERE id=?", (order_id, payment_id))
    conn.commit()
    conn.close()

    return {"order_id": order_id, "approve_url": approve_url, "status": "CREATED", "payment_id": payment_id}


@app.post("/api/attestations")
async def create_attestation(body: dict):
    session_id = body.get("session_id")
    execution_id = body.get("execution_id")
    offer_id = body.get("offer_id")
    rule_id = body.get("rule_id")
    attested_value = body.get("attested_value")
    source_url = body.get("source_url")
    explanation = body.get("explanation")

    if not session_id or not execution_id or not offer_id or not rule_id:
        raise HTTPException(status_code=400, detail="session_id, execution_id, offer_id, and rule_id are required")
    if not attested_value or str(attested_value).strip() == "":
        raise HTTPException(status_code=400, detail="attested_value is required")
    if not source_url or str(source_url).strip() == "":
        raise HTTPException(status_code=400, detail="source_url is required")

    ex = get_execution(execution_id)
    if not ex:
        raise HTTPException(status_code=404, detail="execution not found")
    if ex.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="execution does not belong to session")

    s = sessions.get(session_id)
    if s is None:
        ps = get_session(session_id)
        if ps:
            s = {"mandate": ps["mandate"], "locked": ps["locked"], "locked_mandate": ps["locked_mandate"], "category": ps.get("category"), "structured_filters": ps.get("structured_filters", {})}
            sessions[session_id] = s
    if not s or not s.get("locked"):
        raise HTTPException(status_code=409, detail="mandate not locked — edit and relock the mandate, then perform a fresh search")

    # Use the locked mandate (same object evaluate() receives).
    mandate = s.get("locked_mandate") or s["mandate"]

    # Verify the offer exists in this execution
    offer = None
    product = None
    gate_entry = None
    for r in ex.get("results", []):
        o = r.get("offer")
        if isinstance(o, dict) and o.get("offer_id") == offer_id:
            offer = o
            product = r.get("product")
            gate_entry = r
            break
    if offer is None:
        raise HTTPException(status_code=404, detail="offer not found")

    gate = gate_entry.get("gate") if isinstance(gate_entry, dict) else None
    if not isinstance(gate, dict):
        raise HTTPException(status_code=404, detail="gate evaluation not found for offer")

    # Find the specific rule in the current Policy Gate evaluation
    rules = gate.get("rules") or []
    target_rule = None
    for r in rules:
        if r.get("rule") == rule_id:
            target_rule = r
            break
    if target_rule is None:
        raise HTTPException(status_code=400, detail="unknown rule ID")

    # Rule must be REQUIRED and unresolved (not passed, not failed)
    if target_rule.get("priority") != "REQUIRED":
        raise HTTPException(status_code=400, detail="rule is not REQUIRED")
    if target_rule.get("passed") is True:
        raise HTTPException(status_code=400, detail="rule is already satisfied")
    if target_rule.get("status") == "FAILED":
        raise HTTPException(status_code=400, detail="rule has failed — attestation cannot override a failure")
    if target_rule.get("observed") not in (None, ""):
        raise HTTPException(status_code=409, detail="rule has a demonstrated observed value — attestation cannot override")

    # Verify fingerprints match current execution (same canonical inputs as evaluate()).
    mandate_fp = canonical_fingerprint(mandate)
    evidence_payload = build_product_evidence_payload(product if isinstance(product, dict) else {}, offer if isinstance(offer, dict) else None)
    offer_evidence_fp = canonical_fingerprint(evidence_payload)
    policy_version = gate.get("policy_version", POLICY_VERSION)

    record = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "execution_id": execution_id,
        "offer_id": offer_id,
        "rule_id": rule_id,
        "attested_value": str(attested_value).strip(),
        "source_url": str(source_url).strip(),
        "explanation": explanation or "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mandate_fingerprint": mandate_fp,
        "offer_evidence_fingerprint": offer_evidence_fp,
        "policy_version": policy_version,
    }
    save_attestation(record)
    return {
        "id": record["id"],
        "session_id": session_id,
        "execution_id": execution_id,
        "offer_id": offer_id,
        "rule_id": rule_id,
        "attested_value": record["attested_value"],
        "source_url": record["source_url"],
        "timestamp": record["timestamp"],
        "policy_version": record["policy_version"],
    }


@app.get("/api/attestations")
async def list_attestations_endpoint(session_id: str = None, execution_id: str = None, offer_id: str = None, rule_id: str = None):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    # If execution_id is supplied, verify it belongs to this session.
    if execution_id is not None:
        ex = get_execution(execution_id)
        if not ex:
            raise HTTPException(status_code=404, detail="execution not found")
        if ex.get("session_id") != session_id:
            raise HTTPException(status_code=403, detail="execution does not belong to session")
    return list_attestations(session_id=session_id, execution_id=execution_id, offer_id=offer_id, rule_id=rule_id)


@app.get("/api/attestations/{attestation_id}")
async def get_attestation_endpoint(attestation_id: str, session_id: str):
    att = get_attestation(attestation_id)
    if not att:
        raise HTTPException(status_code=404, detail="attestation not found")
    if att.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="attestation does not belong to session")
    # Verify the attestation's execution belongs to the same session.
    ex = get_execution(att.get("execution_id"))
    if not ex or ex.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="attestation execution does not belong to session")
    return att


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

    # Enrich with session lock state
    session_id = data.get("session_id")
    if session_id:
        session = get_session(session_id)
        if session:
            data["session_id"] = session.get("session_id")
            data["locked"] = session.get("locked", False)
            data["locked_mandate"] = session.get("locked_mandate")
            data["session_query"] = session.get("query")
        else:
            data["locked"] = False
            data["locked_mandate"] = None
            data["session_query"] = None
    else:
        data["locked"] = False
        data["locked_mandate"] = None
        data["session_query"] = None

    return data


@app.post("/api/executions/{execution_id}/agent-council")
async def agent_council_review(execution_id: str, body: dict):
    if not isinstance(body, dict):
        body = {}
    offer_id = body.get("offer_id")
    if not offer_id:
        raise HTTPException(status_code=400, detail="offer_id required")

    # 1. Esecuzione esiste.
    ex = get_execution(execution_id)
    if not ex:
        raise HTTPException(status_code=404, detail="execution not found")

    claimed_session_id = body.get("session_id") or ex.get("session_id")

    # 2. Sessione associata esiste ed è locked.
    s = sessions.get(claimed_session_id)
    if s is None:
        ps = get_session(claimed_session_id)
        if ps:
            s = {
                "mandate": ps["mandate"],
                "locked": ps["locked"],
                "locked_mandate": ps["locked_mandate"],
                "category": ps.get("category"),
                "structured_filters": ps.get("structured_filters", {}),
            }
            sessions[claimed_session_id] = s
    if not s or not s.get("locked"):
        raise HTTPException(status_code=403, detail="session not found or not locked")

    # 3. Esecuzione appartiene a quella sessione.
    if ex.get("session_id") != claimed_session_id:
        raise HTTPException(status_code=403, detail="execution does not belong to session")

    # 4. Offerta selezionata appartiene a quell'esecuzione (dati persistiti).
    offer = None
    product = None
    gate_decision = None
    for r in ex.get("results", []):
        o = r.get("offer")
        if isinstance(o, dict) and o.get("offer_id") == offer_id:
            offer = o
            product = r.get("product")
            g = r.get("gate")
            if isinstance(g, dict):
                gate_decision = g.get("decision")
            break
    if offer is None:
        for r in ex.get("results", []):
            for idx, o in enumerate(r.get("product", {}).get("offers", []) or []):
                candidate_id = o.get("offer_id") or f"{r.get('product', {}).get('id', 'offer')}-{idx}"
                if candidate_id == offer_id:
                    offer = o
                    product = r.get("product")
                    g = r.get("gate")
                    if isinstance(g, dict):
                        gate_decision = g.get("decision")
                    break
            if offer is not None:
                break
    if not isinstance(offer, dict) or not offer.get("offer_id"):
        raise HTTPException(status_code=404, detail="offer not found")

    # 5. La review usa i dati prodotto persistiti, mai dati inviati dal client.
    start = time.time()
    council = AgentCouncil()
    raw = await council.run(
        mandate=ex.get("mandate") or {},
        product=product if isinstance(product, dict) else {},
        offer=offer if isinstance(offer, dict) else {},
    )
    duration_ms = int((time.time() - start) * 1000)

    agent_list = []
    for aid in ALL_AGENTS:
        blob = raw.get("agents", {}).get(aid, {"status": "UNAVAILABLE", "findings": []})
        if not isinstance(blob, dict):
            blob = {"status": "UNAVAILABLE", "findings": []}
        agent_list.append(
            {
                "id": aid,
                "name": AGENT_NAMES[aid],
                "status": blob.get("status", "UNAVAILABLE"),
                "summary": _agent_summary(blob),
                "findings": sanitize_findings(blob.get("findings", [])),
            }
        )

    return {
        "execution_id": execution_id,
        "offer_id": offer_id,
        "advisory_only": True,
        "policy_gate_decision": gate_decision,
        "agents": agent_list,
        "duration_ms": duration_ms,
    }


def _agent_summary(blob: dict) -> str:
    status = blob.get("status", "UNAVAILABLE")
    n = len(blob.get("findings", []) or [])
    if status == "UNAVAILABLE":
        return "agent non disponibile"
    if status == "NO_CONCERN_FOUND":
        return f"nessuna criticità rilevata ({n} finding)"
    if status == "INSUFFICIENT_DATA":
        return f"dati insufficienti ({n} finding)"
    return f"{n} finding segnalati"
