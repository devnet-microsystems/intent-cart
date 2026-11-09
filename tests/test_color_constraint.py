from backend.policy_engine import evaluate, POLICY_VERSION
from backend.intent_parser import Mandate, _normalize_extracted_mandate, parse_intent
from backend.main import authorize_purchase, _reorder_products_for_computer_search, app
from backend.storage import save_session, get_session
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. Estrazione di `black` dal parser (schema Mandate accetta `color`)
# ---------------------------------------------------------------------------

def test_mandate_schema_accepts_color():
    m = Mandate(query="T-shirt", color="black")
    assert m.color == "black"


def test_mandate_schema_color_defaults_none():
    m = Mandate(query="T-shirt")
    assert m.color is None


# ---------------------------------------------------------------------------
# 2. Normalizzazione del colore (colour -> color)
# ---------------------------------------------------------------------------

def test_normalize_colour_british_spelling_to_color():
    data = {"query": "T-shirt", "colour": "black"}
    out = _normalize_extracted_mandate(data, "black T-shirt")
    assert out["color"] == "black"
    assert "colour" not in out


def test_normalize_color_canonical_kept():
    data = {"query": "T-shirt", "color": "black"}
    out = _normalize_extracted_mandate(data, "black T-shirt")
    assert out["color"] == "black"


def test_normalize_color_both_spellings_canonical_wins():
    data = {"query": "T-shirt", "color": "black", "colour": "white"}
    out = _normalize_extracted_mandate(data, "black T-shirt")
    assert out["color"] == "black"
    assert "colour" not in out


# ---------------------------------------------------------------------------
# 3. Persistenza e recupero del mandato bloccato (colore sopravvive al DB)
# ---------------------------------------------------------------------------

def test_locked_mandate_persists_color():
    sid = "test-session-color-persist"
    mandate = {"query": "black T-shirt", "color": "black", "max_price": 25, "size": "L", "material": "cotton", "version": 1}
    save_session(sid, "clothing_accessories", "black T-shirt", mandate, True, mandate, {})
    recovered = get_session(sid)
    assert recovered is not None
    assert recovered["locked"] is True
    assert recovered["locked_mandate"]["color"] == "black"
    assert recovered["mandate"]["color"] == "black"


# ---------------------------------------------------------------------------
# 4. Pass del prodotto nero verificato (APPROVED)
# ---------------------------------------------------------------------------

def test_black_product_approved():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "color": "black",
        "_provenance": {"color_source": ["product.color"]},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "APPROVED"


# ---------------------------------------------------------------------------
# 5. Rejection di un prodotto rosso (REJECTED)
# ---------------------------------------------------------------------------

def test_red_product_rejected():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "color": "red",
        "_provenance": {"color_source": ["product.color"]},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "REJECTED"


# ---------------------------------------------------------------------------
# 6. NEEDS_CONFIRMATION se il colore non è verificabile
# ---------------------------------------------------------------------------

def test_missing_color_evidence_needs_confirmation():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        # no color field at all
        "_provenance": {},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# 7. Gestione di attributi colore conflittuali (NEEDS_CONFIRMATION)
# ---------------------------------------------------------------------------

def test_conflicting_color_evidence_needs_confirmation():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "color": "black",
        "_provenance": {
            "color_source": ["product.color"],
            "color_conflicts": ["structured_attributes.color: navy"],
        },
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# 8. Corrispondenza tra colore e taglia nella stessa variante
# ---------------------------------------------------------------------------

def test_same_variant_size_and_color_match_approved():
    mandate = {"color": "black", "size": "L", "max_price": 25, "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "variants": [
            {"size": "S", "color": "black"},
            {"size": "L", "color": "red"},
            {"size": "L", "color": "black"},
        ],
        "_provenance": {},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "APPROVED"


def test_no_variant_matches_both_size_and_color_needs_confirmation():
    mandate = {"color": "black", "size": "L", "max_price": 25, "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "material": "cotton",
        "variants": [
            {"size": "S", "color": "black"},
            {"size": "L", "color": "red"},
        ],
        "_provenance": {},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# 9. Rivalutazione colore nel checkout e nella capture
# ---------------------------------------------------------------------------

def test_authorize_purchase_rejects_wrong_color(monkeypatch):
    import uuid
    sid = f"test-session-auth-color-wrong-{uuid.uuid4().hex[:8]}"
    eid = f"test-exec-auth-color-wrong-{uuid.uuid4().hex[:8]}"
    mandate = {
        "query": "black T-shirt",
        "color": "black",
        "max_price": 25,
        "locked": True,
        "locked_mandate": {"color": "black", "max_price": 25},
    }
    save_session(sid, "clothing_accessories", "black T-shirt", mandate, True, mandate["locked_mandate"], {})

    from backend.main import sessions
    sessions[sid] = {"mandate": mandate, "locked": True, "locked_mandate": mandate["locked_mandate"], "category": "clothing_accessories", "structured_filters": {}}

    from backend.storage import save_execution
    offer = {"offer_id": "offer-red", "price": {"price": 20.0, "currency": "EUR"}, "condition": "new"}
    product = {"id": "p1", "title": "Red T-shirt", "offers": [offer], "color": "red"}
    save_execution(eid, "2024-01-01T00:00:00Z", mandate, [{"product": product, "gate": {}, "offer": offer}], session_id=sid)

    # authorize_purchase calls evaluate internally; wrong color => REJECTED => 403
    try:
        authorize_purchase(sid, eid, "offer-red")
        assert False, "Expected HTTPException for wrong color"
    except Exception as exc:
        assert "offer not approved" in str(exc).lower() or "403" in str(exc)


def test_authorize_purchase_approves_correct_color(monkeypatch):
    import uuid
    sid = f"test-session-auth-color-ok-{uuid.uuid4().hex[:8]}"
    eid = f"test-exec-auth-color-ok-{uuid.uuid4().hex[:8]}"
    mandate = {
        "query": "black T-shirt",
        "color": "black",
        "max_price": 25,
        "locked": True,
        "locked_mandate": {"color": "black", "max_price": 25},
    }
    save_session(sid, "clothing_accessories", "black T-shirt", mandate, True, mandate["locked_mandate"], {})

    from backend.main import sessions
    sessions[sid] = {"mandate": mandate, "locked": True, "locked_mandate": mandate["locked_mandate"], "category": "clothing_accessories", "structured_filters": {}}

    from backend.storage import save_execution
    offer = {"offer_id": "offer-black", "price": {"price": 20.0, "currency": "EUR"}, "condition": "new"}
    product = {"id": "p1", "title": "Black T-shirt", "offers": [offer], "color": "black"}
    save_execution(eid, "2024-01-01T00:00:00Z", mandate, [{"product": product, "gate": {}, "offer": offer}], session_id=sid)

    result = authorize_purchase(sid, eid, "offer-black")
    assert result["title"] == "Black T-shirt"
    assert result["price"] == 20.0


# ---------------------------------------------------------------------------
# 10. Compatibilità con mandati precedenti senza vincolo colore
# ---------------------------------------------------------------------------

def test_mandate_without_color_still_works():
    mandate = {"max_price": 15, "size": "43", "material": "cotton"}
    product = {"price": 10, "condition": "new", "size": "43", "material": "cotton"}
    result = evaluate(mandate, product)
    # No color requirement => color rule skipped => other rules decide
    assert result["decision"] == "APPROVED"


def test_mandate_without_color_ignores_product_color():
    mandate = {"max_price": 15, "size": "43", "material": "cotton"}
    product = {"price": 10, "condition": "new", "size": "43", "material": "cotton", "color": "pink"}
    result = evaluate(mandate, product)
    assert result["decision"] == "APPROVED"


# ---------------------------------------------------------------------------
# Policy version preserved
# ---------------------------------------------------------------------------

def test_color_rule_policy_version():
    mandate = {"color": "black", "max_price": 25}
    product = {"price": 20, "color": "black", "_provenance": {"color_source": ["product.color"]}}
    result = evaluate(mandate, product)
    assert result["policy_version"] == POLICY_VERSION


# ---------------------------------------------------------------------------
# Channel3 result reordering for laptop searches
# ---------------------------------------------------------------------------

def _make_product(title, slug):
    return {"title": title, "category": {"slug": slug, "title": slug}}


def test_reorder_laptop_mandate_laptops_first():
    products = [
        _make_product("Laptop Sleeve", "laptop-sleeves"),
        _make_product("Dell XPS 14", "laptops"),
        _make_product("Magic Keyboard", "keyboards"),
        _make_product("Mac mini", "desktop-computers"),
    ]
    mandate = {"product_type": "laptop", "query": "MacBook Pro"}
    ordered = _reorder_products_for_computer_search(products, mandate)
    assert ordered[0]["title"] == "Dell XPS 14"
    assert ordered[-1]["title"] in ("Magic Keyboard", "Laptop Sleeve")
    # All laptops before all accessories
    laptop_idx = next(i for i, p in enumerate(ordered) if p["title"] == "Dell XPS 14")
    accessory_idxs = [i for i, p in enumerate(ordered) if p["title"] in ("Laptop Sleeve", "Magic Keyboard")]
    assert laptop_idx < min(accessory_idxs)


def test_reorder_non_laptop_mandate_unchanged():
    products = [
        _make_product("Laptop Sleeve", "laptop-sleeves"),
        _make_product("Dell XPS 14", "laptops"),
    ]
    mandate = {"product_type": None, "query": "computer"}
    ordered = _reorder_products_for_computer_search(products, mandate)
    assert ordered == products


def test_reorder_accessories_not_removed():
    products = [
        _make_product("Laptop Sleeve", "laptop-sleeves"),
        _make_product("Dell XPS 14", "laptops"),
        _make_product("Magic Keyboard", "keyboards"),
    ]
    mandate = {"product_type": "laptop", "query": "MacBook Pro"}
    ordered = _reorder_products_for_computer_search(products, mandate)
    titles = [p["title"] for p in ordered]
    assert "Laptop Sleeve" in titles
    assert "Magic Keyboard" in titles
    assert "Dell XPS 14" in titles
    assert titles.index("Dell XPS 14") < titles.index("Laptop Sleeve")
    assert titles.index("Dell XPS 14") < titles.index("Magic Keyboard")


def test_reorder_empty_products():
    mandate = {"product_type": "laptop", "query": "MacBook Pro"}
    assert _reorder_products_for_computer_search([], mandate) == []


def test_reorder_no_category_products():
    products = [
        {"title": "No Category Item"},
        _make_product("Dell XPS 14", "laptops"),
    ]
    mandate = {"product_type": "laptop", "query": "computer"}
    ordered = _reorder_products_for_computer_search(products, mandate)
    # No-category items go in "others" (middle), after laptops
    assert ordered[0]["title"] == "Dell XPS 14"
    assert ordered[1]["title"] == "No Category Item"


# ---------------------------------------------------------------------------
# Regression: UI color must map to Mandate.color, never to Mandate.style
# ---------------------------------------------------------------------------

def test_structured_filters_color_maps_to_mandate_color_not_style():
    client = TestClient(app)
    with patch("backend.main.parse_intent", return_value={"query": "men's cctton t shirt"}):
        payload = {
            "query": "men's cctton t shirt",
            "category": "clothing_accessories",
            "structured_filters": {
                "product_type": None,
                "size": None,
                "material": None,
                "material_percentage": None,
                "style": None,
                "color": "black",
                "forbidden_designs": [],
                "max_price": None,
                "currency": "EUR",
            },
        }
        res = client.post("/api/intent", json=payload)
        assert res.status_code == 200
        mandate = res.json()["mandate"]
        assert mandate["color"] == "black", f"Expected color=black, got {mandate.get('color')}"
        assert mandate.get("style") != "black", f"Expected style!=black, got style={mandate.get('style')}"


def test_color_rule_verifies_black_product_approved():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "color": "black",
        "_provenance": {"color_source": ["product.color"]},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "APPROVED"


def test_color_rule_rejects_red_product():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "color": "red",
        "_provenance": {"color_source": ["product.color"]},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "REJECTED"


def test_color_rule_missing_color_needs_confirmation():
    mandate = {"color": "black", "max_price": 25, "size": "L", "material": "cotton"}
    product = {
        "price": 20,
        "condition": "new",
        "size": "L",
        "material": "cotton",
        "_provenance": {},
    }
    result = evaluate(mandate, product)
    assert result["decision"] == "NEEDS_CONFIRMATION"


# ---------------------------------------------------------------------------
# Regression: Channel3 HTTP 422 must not produce an unhandled 500
# ---------------------------------------------------------------------------

def test_channel3_422_returns_controlled_502(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    async def _raise_status_error(*args, **kwargs):
        request = httpx.Request("POST", "https://api.trychannel3.com/v1/search")
        response = httpx.Response(422, request=request)
        raise httpx.HTTPStatusError("Upstream error", request=request, response=response)

    monkeypatch.setattr("backend.main.Channel3Client.search", _raise_status_error)

    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    client = TestClient(app)
    payload = {
        "query": "men's cctton t shirt",
        "category": "clothing_accessories",
        "structured_filters": {
            "product_type": None,
            "size": None,
            "material": None,
            "material_percentage": None,
            "style": None,
            "color": "black",
            "forbidden_designs": [],
            "max_price": None,
            "currency": "EUR",
        },
    }
    res = client.post("/api/intent", json=payload)
    assert res.status_code == 200
    sid = res.json()["session_id"]
    res2 = client.post(f"/api/mandate/{sid}/lock")
    assert res2.status_code == 200

    res3 = client.post(f"/api/search/{sid}")
    assert res3.status_code == 502
