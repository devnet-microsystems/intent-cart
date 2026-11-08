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
    assert r["rules"][1]["passed"] is False  # condition from offer B
