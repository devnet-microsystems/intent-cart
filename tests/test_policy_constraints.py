from backend.policy_engine import evaluate, POLICY_VERSION

mandate = {
    "max_price": 15,
    "currency": "EUR",
    "allowed_conditions": ["new"],
    "forbidden_conditions": ["refurbished"],
    "min_specs": {},
    "size": "43",
    "material": "cotton",
    "material_percentage": 100,
    "style": "intentionally mismatched",
    "forbidden_designs": ["Christmas patterns", "snowmen"],
}

def base_product():
    return {"price": 10, "condition": "new", "currency": "EUR", "size": "43", "material": "cotton", "material_percentage": 100, "style": "intentionally mismatched", "design": "stripes"}

def test_missing_size_blocked():
    p = base_product(); p["size"] = None
    assert evaluate(mandate, p)["decision"] == "NEEDS_CONFIRMATION"

def test_wrong_size_blocked():
    p = base_product(); p["size"] = "40"
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_missing_material_blocked():
    p = base_product(); p["material"] = None
    assert evaluate(mandate, p)["decision"] == "NEEDS_CONFIRMATION"

def test_composition_below_100_blocked():
    p = base_product(); p["material_percentage"] = 80
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_missing_mismatched_blocked():
    p = base_product(); p["style"] = "plain"
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_christmas_blocked():
    p = base_product(); p["design"] = "Christmas patterns"
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_snowmen_blocked():
    p = base_product(); p["design"] = "snowmen"
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_missing_evidence_blocked():
    p = {"price": 10, "condition": "new"}
    assert evaluate(mandate, p)["decision"] == "NEEDS_CONFIRMATION"

def test_valid_product_approved():
    assert evaluate(mandate, base_product())["decision"] == "APPROVED"

def test_policy_version():
    assert evaluate(mandate, base_product())["policy_version"] == POLICY_VERSION

def test_empty_min_specs_cannot_bypass():
    m = dict(mandate, min_specs={})
    p = {"price": 10, "condition": "new"}
    assert evaluate(m, p)["decision"] == "NEEDS_CONFIRMATION"


def test_zero_rules_never_approved():
    # An empty mandate/product carries no verifiable REQUIRED criteria and
    # must never be APPROVED (fail-closed to NEEDS_CONFIRMATION).
    result = evaluate({}, {})
    assert result["decision"] == "NEEDS_CONFIRMATION"
    assert result["decision"] != "APPROVED"


def test_preferred_fail_does_not_reject():
    m = dict(mandate, constraint_priorities={"size": "PREFERRED"})
    p = base_product()
    p["size"] = "40"  # PREFERRED size fails, but is not REQUIRED
    assert evaluate(m, p)["decision"] == "APPROVED"


def test_preferred_not_verified_does_not_block():
    m = dict(mandate, constraint_priorities={"size": "PREFERRED"})
    p = base_product()
    p["size"] = None  # PREFERRED size unverified, but is not REQUIRED
    assert evaluate(m, p)["decision"] == "APPROVED"


# --- Condition normalization tests ---

def test_condition_normalizes_to_allowed_conditions():
    """Mandate with condition='new' must normalize to allowed_conditions=['new']."""
    m = {"query": "socks", "condition": "new"}
    from backend.intent_parser import Mandate
    parsed = Mandate.model_validate(m).model_dump()
    assert parsed["allowed_conditions"] == ["new"]
    assert parsed["condition"] is None
    assert parsed["condition_original"] == "new"


def test_condition_new_passes():
    """Product with condition 'new' passes when allowed_conditions=['new']."""
    m = {"query": "socks", "allowed_conditions": ["new"]}
    p = {"price": 10, "condition": "new"}
    assert evaluate(m, p)["decision"] == "APPROVED"


def test_condition_used_fails():
    """Product with condition 'used' fails when allowed_conditions=['new']."""
    m = {"query": "socks", "allowed_conditions": ["new"]}
    p = {"price": 10, "condition": "used"}
    assert evaluate(m, p)["decision"] == "REJECTED"


def test_missing_condition_not_verified():
    """Missing product condition → NOT VERIFIED → NEEDS_CONFIRMATION."""
    m = {"query": "socks", "allowed_conditions": ["new"]}
    p = {"price": 10, "condition": None}
    assert evaluate(m, p)["decision"] == "NEEDS_CONFIRMATION"


def test_condition_original_preserved():
    """Original wording must be preserved separately from canonical allowed_conditions."""
    from backend.intent_parser import Mandate
    parsed = Mandate.model_validate({"query": "socks", "condition": "new"}).model_dump()
    assert parsed["condition_original"] == "new"
    assert parsed["allowed_conditions"] == ["new"]
    assert "condition" not in parsed or parsed["condition"] is None


def test_empty_allowed_conditions_does_not_satisfy():
    """Empty allowed_conditions list does not prove an explicit condition requirement is satisfied."""
    m = {"query": "socks", "allowed_conditions": []}
    p = {"price": 10, "condition": "new"}
    # No explicit condition requirement → any condition passes
    assert evaluate(m, p)["decision"] == "APPROVED"


def test_forbidden_design_missing_evidence_not_verified():
    # Missing product design must be NOT VERIFIED, never PASS.
    # A REQUIRED NOT VERIFIED rule → NEEDS_CONFIRMATION, never APPROVED.
    p = base_product(); p["design"] = None
    result = evaluate(mandate, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    rule = next(r for r in result["rules"] if r["rule"].startswith("forbidden_design_"))
    assert rule["passed"] is False
    assert rule["observed"] is None


def test_forbidden_design_missing_key_not_verified():
    # Product with no "design" key at all → NOT VERIFIED → NEEDS_CONFIRMATION.
    p = base_product(); del p["design"]
    result = evaluate(mandate, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    rule = next(r for r in result["rules"] if r["rule"].startswith("forbidden_design_"))
    assert rule["passed"] is False
    assert rule["observed"] is None


def test_forbidden_design_christmas_fail():
    # Christmas design positively detected → FAIL → REJECTED.
    p = base_product(); p["design"] = "Christmas patterns sweater"
    assert evaluate(mandate, p)["decision"] == "REJECTED"


def test_forbidden_design_non_christmas_pass():
    # Non-Christmas design positively verified → PASS.
    assert evaluate(mandate, base_product())["decision"] == "APPROVED"


def test_forbidden_design_unknown_negative_constraint_needs_confirmation():
    # An unknown REQUIRED negative constraint (missing evidence) → NEEDS_CONFIRMATION.
    m = dict(mandate, forbidden_designs=["Ugly holiday sweater"])
    p = base_product(); p["design"] = None
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_forbidden_design_unknown_negative_constraint_never_approved():
    # Unknown REQUIRED negative constraint can never produce APPROVED.
    m = dict(mandate, forbidden_designs=["Ugly holiday sweater"])
    p = base_product(); p["design"] = None
    result = evaluate(m, p)
    assert result["decision"] != "APPROVED"


def test_condition_explicit_retained():
    # Explicit "new" condition requirement is retained and enforced.
    m = dict(mandate, allowed_conditions=["new"])
    p = base_product(); p["condition"] = "new"
    assert evaluate(m, p)["decision"] == "APPROVED"
    p["condition"] = "used"
    assert evaluate(m, p)["decision"] == "REJECTED"


def test_price_size_material_fail_closed():
    # Previous fail-closed price, size and material behavior remains intact.
    p = base_product(); p["price"] = 999
    assert evaluate(mandate, p)["decision"] == "REJECTED"
    p = base_product(); p["size"] = None
    assert evaluate(mandate, p)["decision"] == "NEEDS_CONFIRMATION"
    p = base_product(); p["material"] = None
    assert evaluate(mandate, p)["decision"] == "NEEDS_CONFIRMATION"


# --- Evidence adapter tests (real Channel3 fixtures from coverage audit) ---

def test_evidence_adapter_passes_channel3_fields():
    """The adapter must surface structured_attributes, key_features and materials."""
    from backend.policy_engine import build_evidence
    p = {
        "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop",
        "structured_attributes": {"processor-family": ["AMD Ryzen AI 5"], "computer-form": ["Notebook"]},
        "key_features": ["16GB RAM", "512GB SSD"],
        "materials": ["Aluminum"],
    }
    ev = build_evidence(p)
    assert ev["_provenance"]["structured_attributes"]["processor-family"] == ["AMD Ryzen AI 5"]
    assert ev["_provenance"]["key_features"] == ["16GB RAM", "512GB SSD"]
    assert ev["_provenance"]["materials"] == ["Aluminum"]
    assert ev["_provenance"]["title"].startswith("IdeaPad Slim 5")


def test_evidence_adapter_does_not_mutate_product():
    from backend.policy_engine import build_evidence
    p = {"title": "Laptop", "key_features": ["16GB RAM"]}
    build_evidence(p)
    assert p == {"title": "Laptop", "key_features": ["16GB RAM"]}


def test_evidence_adapter_unknown_fields_omitted():
    from backend.policy_engine import build_evidence
    ev = build_evidence({"title": "mystery box"})
    assert "material" not in ev
    assert "design" not in ev
    assert ev["_provenance"]["title"] == "mystery box"


def test_evidence_adapter_flat_fields_preserved():
    """Backwards compatibility: flat price/condition/size fields still work."""
    from backend.policy_engine import build_evidence
    ev = build_evidence({"price": 10, "condition": "new", "size": "43", "material": "cotton"})
    assert ev["price"] == 10
    assert ev["condition"] == "new"
    assert ev["size"] == "43"
    assert ev["material"] == "cotton"


def test_evidence_adapter_material_from_materials_list():
    from backend.policy_engine import build_evidence
    ev = build_evidence({"materials": ["Cotton", "Polyester"]})
    assert ev["material"] == "Cotton"
    assert ev["_provenance"]["material_source"] == "product.materials"


def test_evidence_adapter_material_from_structured_attributes():
    from backend.policy_engine import build_evidence
    ev = build_evidence({"structured_attributes": {"material": ["Cotton"]}})
    assert ev["material"] == "Cotton"
    assert ev["_provenance"]["material_source"] == "product.structured_attributes.material"


def test_evidence_adapter_flat_overrides_derived():
    """An explicit flat field must not be overwritten by a derived one."""
    from backend.policy_engine import build_evidence
    ev = build_evidence({"material": "Wool", "materials": ["Cotton"]})
    assert ev["material"] == "Wool"


def test_evidence_adapter_rules_cite_provenance():
    """Every rule must record where its observed value came from."""
    m = dict(mandate, material="cotton")
    p = base_product()
    p["material"] = None
    p["materials"] = ["Cotton", "Spandex"]
    result = evaluate(m, p)
    rule = next(r for r in result["rules"] if r["rule"] == "material")
    assert rule["evidence_source"] == "product.materials"
    assert rule["evidence_excerpt"] == "Cotton"


def test_evidence_adapter_missing_material_not_verified():
    """A product with no material evidence must not silently pass."""
    m = dict(mandate, material="cotton")
    p = base_product()
    p["material"] = None
    p["materials"] = None
    p["structured_attributes"] = {}
    result = evaluate(m, p)
    rule = next(r for r in result["rules"] if r["rule"] == "material")
    assert rule["observed"] is None
    assert rule["passed"] is False
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_evidence_adapter_multiple_offers_independent():
    """Each offer is evaluated independently; fields are never mixed across offers."""
    m = {"max_price": 100, "currency": "EUR", "allowed_conditions": ["new"]}
    p = {
        "offers": [
            {"price": {"price": 50, "currency": "EUR"}, "condition": "new"},
            {"price": {"price": 200, "currency": "EUR"}, "condition": "new"},
        ]
    }
    # Simulate the search loop: evaluate each offer separately.
    results = [evaluate(m, {"price": o["price"]["price"], "condition": o["condition"]}) for o in p["offers"]]
    assert results[0]["decision"] == "APPROVED"
    assert results[1]["decision"] == "REJECTED"


def test_evidence_adapter_no_cross_offer_contamination():
    """Price from one offer must never influence the condition rule of another."""
    m = {"max_price": 100, "currency": "EUR", "allowed_conditions": ["new"]}
    offer_a = {"price": {"price": 50, "currency": "EUR"}, "condition": "new"}
    offer_b = {"price": {"price": 200, "currency": "EUR"}, "condition": "used"}
    r = evaluate(m, {"price": offer_a["price"]["price"], "condition": offer_b["condition"]})
    assert r["decision"] == "REJECTED"
    assert r["rules"][0]["passed"] is True   # price from offer A


# --- P0: Pack quantity rule ---

def test_pack_quantity_sufficient_passes():
    """Pack quantity >= required → PASS."""
    mandate = {"pack_quantity": 5, "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "pack_quantity": 5}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is True
    assert result["decision"] == "APPROVED"


def test_pack_quantity_insufficient_rejected():
    """Pack quantity < required → REJECTED."""
    mandate = {"pack_quantity": 5, "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "pack_quantity": 3}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is False
    assert result["decision"] == "REJECTED"


def test_pack_quantity_missing_needs_confirmation():
    """Pack quantity not verifiable → NEEDS_CONFIRMATION (not REJECTED)."""
    mandate = {"pack_quantity": 5, "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black"}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is False
    assert pq_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_pack_quantity_variant_aware_same_variant():
    """Pack quantity checked on the variant matching both size and color."""
    mandate = {"pack_quantity": 5, "size": "L", "color": "black", "max_price": 15}
    product = {
        "price": 10, "condition": "new", "size": "L", "color": "black",
        "variants": [
            {"size": "L", "color": "red", "pack_quantity": 3},
            {"size": "L", "color": "black", "pack_quantity": 5},
        ],
    }
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is True
    assert pq_rule["observed"] == 5


def test_pack_quantity_variant_aware_mismatched_variant():
    """Variant matching size+color has insufficient pack → REJECTED even if another variant has enough."""
    mandate = {"pack_quantity": 5, "size": "L", "color": "black", "max_price": 15}
    product = {
        "price": 10, "condition": "new", "size": "L", "color": "black",
        "variants": [
            {"size": "L", "color": "black", "pack_quantity": 3},
            {"size": "S", "color": "black", "pack_quantity": 10},
        ],
    }
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is False
    assert pq_rule["observed"] == 3
    assert result["decision"] == "REJECTED"


# --- P0: Solid/branding false positive ---

def test_solid_pattern_not_proof_of_no_logos():
    """design='Solid' does not prove absence of logos → NEEDS_CONFIRMATION."""
    mandate = {"forbidden_designs": ["logos"], "max_price": 15}
    product = {"price": 10, "condition": "new", "design": "Solid"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_plain_pattern_not_proof_of_no_logos():
    """design='Plain' does not prove absence of logos → NEEDS_CONFIRMATION."""
    mandate = {"forbidden_designs": ["logos"], "max_price": 15}
    product = {"price": 10, "condition": "new", "design": "Plain"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_informative_design_no_logos_passes():
    """Informative design (stripes) without logos → PASS."""
    mandate = {"forbidden_designs": ["logos"], "max_price": 15}
    product = {"price": 10, "condition": "new", "design": "stripes"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is True
    assert result["decision"] == "APPROVED"


def test_design_containing_forbidden_rejected():
    """Design explicitly containing forbidden element → REJECTED."""
    mandate = {"forbidden_designs": ["Christmas"], "max_price": 15}
    product = {"price": 10, "condition": "new", "design": "Christmas patterns"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_Christmas")
    assert fd_rule["passed"] is False
    assert result["decision"] == "REJECTED"


# --- P0: Variant-aware size+color matching ---

def test_variant_matching_size_and_color_same_variant_approved():
    """Size and color must match on the SAME variant."""
    mandate = {"size": "L", "color": "black", "max_price": 15}
    product = {
        "price": 10, "condition": "new",
        "variants": [
            {"size": "L", "color": "red"},
            {"size": "L", "color": "black"},
        ],
    }
    result = evaluate(mandate, product)
    color_rule = next(r for r in result["rules"] if r["rule"] == "color")
    assert color_rule["passed"] is True
    assert result["decision"] == "APPROVED"


def test_variant_no_match_both_size_and_color_needs_confirmation():
    """No variant matches both size and color → NEEDS_CONFIRMATION."""
    mandate = {"size": "L", "color": "black", "max_price": 15}
    product = {
        "price": 10, "condition": "new",
        "variants": [
            {"size": "L", "color": "red"},
            {"size": "S", "color": "black"},
        ],
    }
    result = evaluate(mandate, product)
    color_rule = next(r for r in result["rules"] if r["rule"] == "color")
    assert color_rule["passed"] is False
    assert color_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


# --- P0.2.5: Currency safety ---

def test_max_price_usd_vs_eur_mismatch_rejected():
    """12.99 USD must not be compared against 15 EUR → price rule fails."""
    mandate = {"max_price": 15, "currency": "EUR", "material": "cotton", "color": "black"}
    product = {"price": 12.99, "currency": "USD", "condition": "new", "material": "cotton", "color": "black"}
    result = evaluate(mandate, product)
    price_rule = next(r for r in result["rules"] if r["rule"] == "max_price")
    assert price_rule["passed"] is False
    assert result["decision"] == "REJECTED"


def test_max_price_eur_vs_eur_match_passes():
    """12.99 EUR compared against 15 EUR → price rule passes."""
    mandate = {"max_price": 15, "currency": "EUR", "material": "cotton", "color": "black"}
    product = {"price": 12.99, "currency": "EUR", "condition": "new", "material": "cotton", "color": "black"}
    result = evaluate(mandate, product)
    price_rule = next(r for r in result["rules"] if r["rule"] == "max_price")
    assert price_rule["passed"] is True


def test_max_price_currency_missing_needs_confirmation():
    """Currency absent → price compliance unverifiable → NEEDS_CONFIRMATION."""
    mandate = {"max_price": 15, "currency": "EUR", "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "currency": None}
    result = evaluate(mandate, product)
    price_rule = next(r for r in result["rules"] if r["rule"] == "max_price")
    assert price_rule["passed"] is False
    assert result["decision"] == "NEEDS_CONFIRMATION"


# --- P0.2.6: Hex → color normalization ---

from backend.channel3_client import hex_is_near


def test_hex_near_black():
    assert hex_is_near("#000000", "black") is True
    assert hex_is_near("#090a0a", "black") is True
    assert hex_is_near("#1a1a1a", "black") is True


def test_hex_near_not_black():
    assert hex_is_near("#ffffff", "black") is False
    assert hex_is_near("#ff0000", "black") is False
    assert hex_is_near("#0000ff", "black") is False


def test_hex_near_gray():
    assert hex_is_near("#808080", "gray") is True
    assert hex_is_near("#a9a9a9", "grey") is True


def test_hex_near_blue():
    assert hex_is_near("#0000ff", "blue") is True


def test_hex_near_unknown_returns_false():
    assert hex_is_near("#ff69b4", "black") is False  # hot pink
    assert hex_is_near("", "black") is False
    assert hex_is_near(None, "black") is False
    assert hex_is_near("#000000", "") is False


# --- P0.2.9: Branding negative tests ---

def test_forbidden_design_solid_not_proof_of_no_logos():
    mandate = {"forbidden_designs": ["logos"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "design": "Solid"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_forbidden_design_plain_not_proof_of_no_logos():
    mandate = {"forbidden_designs": ["logos"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "design": "Plain"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_forbidden_design_texture_not_proof_of_no_logos():
    mandate = {"forbidden_designs": ["logos"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "design": "Texture"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_forbidden_design_plaid_not_proof_of_no_logos():
    mandate = {"forbidden_designs": ["logos"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "design": "Plaid"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_forbidden_design_striped_not_proof_of_no_logos():
    mandate = {"forbidden_designs": ["logos"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {"price": 10, "condition": "new", "material": "cotton", "color": "black", "design": "Striped"}
    result = evaluate(mandate, product)
    fd_rule = next(r for r in result["rules"] if r["rule"] == "forbidden_design_logos")
    assert fd_rule["passed"] is False
    assert fd_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_brand_name_does_not_prove_visible_logo():
    """Seller brand present is not evidence of visible logo on product."""
    mandate = {"forbidden_designs": ["visible logos", "branding"], "max_price": 15, "material": "cotton", "color": "black"}
    product = {
        "price": 10, "condition": "new", "material": "cotton", "color": "black",
        "brands": [{"name": "Nike"}],
        "design": "Solid",
    }
    result = evaluate(mandate, product)
    # No visible logo evidence → NEEDS_CONFIRMATION despite brand name
    assert result["decision"] == "NEEDS_CONFIRMATION"


# --- FASE 3/5: Text evidence extraction — pack quantity from title/description ---

def test_pack_quantity_from_title_5_pack():
    """5-pack in title → pack_quantity recognized from text."""
    mandate = {"pack_quantity": 5, "max_price": 15, "color": "black"}
    product = {"price": 10, "condition": "new", "color": "black", "title": "5-pack Ribbed Socks"}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is True
    assert pq_rule["observed"] == 5


def test_pack_quantity_from_description_pack_of_5():
    """pack of 5 in description → pack_quantity recognized from text."""
    mandate = {"pack_quantity": 5, "max_price": 15, "color": "black"}
    product = {"price": 10, "condition": "new", "color": "black", "description": "A pack of 5 black socks"}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is True
    assert pq_rule["observed"] == 5


def test_pack_quantity_ambiguous_quantity_not_recognized():
    """Generic number not followed by pack keyword → not recognized."""
    mandate = {"pack_quantity": 5, "max_price": 15, "color": "black"}
    product = {"price": 10, "condition": "new", "color": "black", "title": "Socks for 5 people"}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_pack_quantity_missing_field_needs_confirmation():
    """No pack quantity text at all → NEEDS_CONFIRMATION."""
    mandate = {"pack_quantity": 5, "max_price": 15, "color": "black"}
    product = {"price": 10, "condition": "new", "color": "black", "title": "Black socks"}
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


# --- FASE 3/5: Text evidence extraction — style from title/description/key_features ---

def test_style_crew_present():
    """crew in text → style recognized."""
    mandate = {"style": "crew", "max_price": 15}
    product = {"price": 10, "condition": "new", "title": "Black crew socks"}
    result = evaluate(mandate, product)
    style_rule = next(r for r in result["rules"] if r["rule"] == "style")
    assert style_rule["passed"] is True
    assert result["decision"] == "APPROVED"


def test_style_crew_absent():
    """No style keyword in text → NEEDS_CONFIRMATION."""
    mandate = {"style": "crew", "max_price": 15}
    product = {"price": 10, "condition": "new", "title": "Black socks"}
    result = evaluate(mandate, product)
    style_rule = next(r for r in result["rules"] if r["rule"] == "style")
    assert style_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_style_ribbed_present():
    """ribbed in text → style recognized."""
    mandate = {"style": "ribbed", "max_price": 15}
    product = {"price": 10, "condition": "new", "title": "Ribbed socks"}
    result = evaluate(mandate, product)
    style_rule = next(r for r in result["rules"] if r["rule"] == "style")
    assert style_rule["passed"] is True


def test_style_ribbed_absent():
    """No ribbed keyword in text → NEEDS_CONFIRMATION."""
    mandate = {"style": "ribbed", "max_price": 15}
    product = {"price": 10, "condition": "new", "title": "Black socks"}
    result = evaluate(mandate, product)
    style_rule = next(r for r in result["rules"] if r["rule"] == "style")
    assert style_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_style_ribbed_and_crew_combined():
    """ribbed + crew in text → combined style 'ribbed crew' recognized."""
    mandate = {"style": "ribbed crew", "max_price": 15, "color": "black"}
    product = {"price": 10, "condition": "new", "color": "black", "title": "5-pack Ribbed Socks", "description": "black crew socks with ribbed texture"}
    result = evaluate(mandate, product)
    style_rule = next(r for r in result["rules"] if r["rule"] == "style")
    assert style_rule["passed"] is True


# --- Size: selected vs not selected in variants.selected ---

def test_size_selected_in_variant():
    """Size in variants.selected → recognized."""
    mandate = {"size": "L", "max_price": 15, "color": "black"}
    product = {
        "price": 10, "condition": "new", "color": "black",
        "variants": {"selected": [{"name": "Size", "label": "L"}, {"name": "Color", "label": "black"}]}
    }
    result = evaluate(mandate, product)
    size_rule = next(r for r in result["rules"] if r["rule"] == "size")
    assert size_rule["passed"] is True


def test_size_not_selected_needs_confirmation():
    """No size in variants.selected → NEEDS_CONFIRMATION."""
    mandate = {"size": "L", "max_price": 15, "color": "black"}
    product = {
        "price": 10, "condition": "new", "color": "black",
        "variants": {"selected": [{"name": "Color", "label": "black"}]}
    }
    result = evaluate(mandate, product)
    size_rule = next(r for r in result["rules"] if r["rule"] == "size")
    assert size_rule["observed"] is None
    assert result["decision"] == "NEEDS_CONFIRMATION"


# --- Currency USD ---

def test_max_price_usd_passes():
    """14.99 USD ≤ 15 USD → price passes."""
    mandate = {"max_price": 15, "currency": "USD", "color": "black"}
    product = {"price": 14.99, "currency": "USD", "condition": "new", "color": "black"}
    result = evaluate(mandate, product)
    price_rule = next(r for r in result["rules"] if r["rule"] == "max_price")
    assert price_rule["passed"] is True


# --- Budget exceeded ---

def test_budget_exceeded_rejected():
    """20 USD > 15 USD → REJECTED."""
    mandate = {"max_price": 15, "currency": "USD", "color": "black"}
    product = {"price": 20.0, "currency": "USD", "condition": "new", "color": "black"}
    result = evaluate(mandate, product)
    price_rule = next(r for r in result["rules"] if r["rule"] == "max_price")
    assert price_rule["passed"] is False
    assert result["decision"] == "REJECTED"


# --- Product availability ---

def test_product_not_available_rejected():
    """Availability OutOfStock with InStock requirement → REJECTED."""
    mandate = {"max_price": 15, "currency": "USD", "availability": "InStock", "color": "black"}
    product = {"price": 10, "currency": "USD", "condition": "new", "color": "black", "availability": "OutOfStock"}
    result = evaluate(mandate, product)
    avail_rule = next(r for r in result["rules"] if r["rule"] == "availability")
    assert avail_rule["passed"] is False
    assert result["decision"] == "REJECTED"


def test_product_available_passes():
    """Availability InStock with InStock requirement → PASS."""
    mandate = {"max_price": 15, "currency": "USD", "availability": "InStock", "color": "black"}
    product = {"price": 10, "currency": "USD", "condition": "new", "color": "black", "availability": "InStock"}
    result = evaluate(mandate, product)
    avail_rule = next(r for r in result["rules"] if r["rule"] == "availability")
    assert avail_rule["passed"] is True


# --- P0.3.6 Regression: pack_quantity from text when no size constraint ---

def test_pack_quantity_from_text_no_size_constraint_approved():
    """No size requirement: pack quantity from title text must not be overridden
    by a matching variant that lacks a pack count field."""
    mandate = {
        "pack_quantity": 5, "color": "black", "style": "ribbed crew",
        "max_price": 15, "currency": "USD", "allowed_conditions": ["new"],
        "availability": "InStock",
    }
    product = {
        "id": "qu2CGKi",
        "title": "5-pack Ribbed Socks",
        "description": "A set of five identical black crew socks crafted from a soft, fine-knit cotton blend.",
        "color": "black",
        "style": "ribbed crew",
        "price": 14.99,
        "currency": "USD",
        "condition": "new",
        "availability": "InStock",
        "variants": {"selected": [{"name": "Color", "label": "Black"}]},
        "structured_attributes": {"style": ["ribbed crew"]},
        "key_features": ["Ribbed leg sections", "Crew Socks style"],
    }
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["passed"] is True
    assert pq_rule["observed"] == 5
    assert result["decision"] == "APPROVED"


def test_pack_quantity_variant_with_pack_count_overrides_text():
    """When matching variant has a pack count, it takes precedence over text."""
    mandate = {"pack_quantity": 5, "color": "black", "max_price": 15}
    product = {
        "price": 10, "condition": "new", "color": "black",
        "title": "5-pack Ribbed Socks",
        "variants": {"selected": [
            {"name": "Color", "label": "Black"},
            {"name": "Pack Count", "label": "3"},
        ]},
    }
    result = evaluate(mandate, product)
    pq_rule = next(r for r in result["rules"] if r["rule"] == "pack_quantity")
    assert pq_rule["observed"] == 3
    assert pq_rule["passed"] is False
    assert result["decision"] == "REJECTED"
