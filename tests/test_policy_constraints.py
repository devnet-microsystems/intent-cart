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
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_wrong_size_blocked():
    p = base_product(); p["size"] = "40"
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_missing_material_blocked():
    p = base_product(); p["material"] = None
    assert evaluate(mandate, p)["decision"] == "REJECTED"

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
    assert evaluate(mandate, p)["decision"] == "REJECTED"

def test_valid_product_approved():
    assert evaluate(mandate, base_product())["decision"] == "APPROVED"

def test_policy_version():
    assert evaluate(mandate, base_product())["policy_version"] == POLICY_VERSION

def test_empty_min_specs_cannot_bypass():
    m = dict(mandate, min_specs={})
    p = {"price": 10, "condition": "new"}
    assert evaluate(m, p)["decision"] == "REJECTED"
