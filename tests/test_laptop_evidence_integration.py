import copy

from backend.policy_engine import build_evidence, evaluate, POLICY_VERSION


LAPTOP_FIELDS = {
    "computer_form",
    "ram_gb",
    "storage_gb",
    "storage_interface",
    "processor_family",
    "operating_system",
}

REALISTIC_LAPTOP = {
    "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop",
    "price": 799,
    "condition": "new",
    "structured_attributes": {
        "processor-family": ["AMD Ryzen AI 5"],
        "computer-form": ["Notebook"],
        "operating-system": ["Windows 11 Home"],
    },
    "key_features": ["16GB RAM", "512GB NVMe SSD"],
    "materials": ["Aluminum"],
}


def _field_shape(field_result):
    assert isinstance(field_result, dict)
    assert set(field_result.keys()) >= {"value", "status", "evidence_source", "evidence_excerpt"}
    assert field_result["status"] in ("VERIFIED", "NOT_VERIFIED")


def test_build_evidence_exposes_all_six_normalized_laptop_attributes():
    ev = build_evidence(REALISTIC_LAPTOP)
    assert "normalized_laptop_specs" in ev
    specs = ev["normalized_laptop_specs"]
    assert set(specs.keys()) >= LAPTOP_FIELDS
    for name in LAPTOP_FIELDS:
        _field_shape(specs[name])
    # preserve per-attribute provenance from the normalizer
    assert specs["ram_gb"]["status"] == "VERIFIED"
    assert specs["ram_gb"]["value"] == 16
    assert specs["ram_gb"]["evidence_excerpt"] == "16GB RAM"
    assert specs["storage_gb"]["value"] == 512


def test_verified_ram_and_storage_available_to_min_specs():
    p = dict(REALISTIC_LAPTOP)
    mandate = {"query": "laptop", "allowed_conditions": ["new"], "min_specs": {"ram_gb": 16, "storage_gb": 512}}
    result = evaluate(mandate, p)
    ram_rule = next(r for r in result["rules"] if r["rule"] == "min_ram_gb")
    storage_rule = next(r for r in result["rules"] if r["rule"] == "min_storage_gb")
    assert ram_rule["passed"] is True
    assert ram_rule["observed"] == 16
    assert storage_rule["passed"] is True
    assert storage_rule["observed"] == 512
    assert result["decision"] == "APPROVED"


def test_verified_ram_below_min_specs_rejected_by_generic_mechanism():
    p = dict(REALISTIC_LAPTOP)
    mandate = {"query": "laptop", "allowed_conditions": ["new"], "min_specs": {"ram_gb": 32}}
    result = evaluate(mandate, p)
    ram_rule = next(r for r in result["rules"] if r["rule"] == "min_ram_gb")
    assert ram_rule["observed"] == 16
    assert ram_rule["passed"] is False
    assert result["decision"] == "REJECTED"


def test_missing_ram_storage_not_exposed_as_verified_numeric_values():
    p = {
        "title": "Thin Notebook",
        "price": 500,
        "condition": "new",
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    ev = build_evidence(p)
    specs = ev["normalized_laptop_specs"]
    assert specs["ram_gb"]["status"] == "NOT_VERIFIED"
    assert specs["ram_gb"]["value"] is None
    assert specs["storage_gb"]["status"] == "NOT_VERIFIED"
    assert "ram_gb" not in ev["specs"]
    assert "storage_gb" not in ev["specs"]

    mandate = {"query": "laptop", "allowed_conditions": ["new"], "min_specs": {"ram_gb": 8}}
    result = evaluate(mandate, p)
    ram_rule = next(r for r in result["rules"] if r["rule"] == "min_ram_gb")
    assert ram_rule["observed"] is None
    assert ram_rule["passed"] is False
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_conflicting_title_specs_values_cannot_pass_numeric_mapping():
    p = {
        "title": "Notebook 32GB RAM",
        "specs": {"ram_gb": 16},
        "price": 700,
        "condition": "new",
        "structured_attributes": {"computer-form": ["Notebook"]},
        "key_features": ["512GB SSD"],
    }
    ev = build_evidence(p)
    assert ev["normalized_laptop_specs"]["ram_gb"]["status"] == "NOT_VERIFIED"
    assert ev["normalized_laptop_specs"]["ram_gb"]["value"] is None
    assert "conflicts" in ev["normalized_laptop_specs"]["ram_gb"]
    # conflicting raw value must not bypass NOT_VERIFIED through specs mapping
    assert "ram_gb" not in ev["specs"]

    mandate = {"query": "laptop", "allowed_conditions": ["new"], "min_specs": {"ram_gb": 8}}
    result = evaluate(mandate, p)
    ram_rule = next(r for r in result["rules"] if r["rule"] == "min_ram_gb")
    assert ram_rule["observed"] is None
    assert ram_rule["passed"] is False
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_original_raw_evidence_and_provenance_remain_available():
    p = copy.deepcopy(REALISTIC_LAPTOP)
    ev = build_evidence(p)
    prov = ev["_provenance"]
    assert prov["structured_attributes"]["processor-family"] == ["AMD Ryzen AI 5"]
    assert prov["key_features"] == ["16GB RAM", "512GB NVMe SSD"]
    assert prov["materials"] == ["Aluminum"]
    assert prov["title"].startswith("IdeaPad Slim 5")
    assert prov["raw_specs"] is None  # no raw specs provided
    # per-attribute provenance preserved in normalized result and adapter provenance
    assert ev["normalized_laptop_specs"]["processor_family"]["evidence_source"] == "product.structured_attributes.processor-family"
    assert prov.get("ram_gb_source")
    assert p == REALISTIC_LAPTOP  # no mutation


def test_existing_generic_min_specs_unchanged():
    mandate = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"hdmi_ports": 1}}
    approved = {"price": 50, "condition": "new", "specs": {"hdmi_ports": 1}}
    needs = {"price": 50, "condition": "new", "specs": {}}
    assert evaluate(mandate, approved)["decision"] == "APPROVED"
    assert evaluate(mandate, needs)["decision"] == "NEEDS_CONFIRMATION"


def test_existing_material_style_design_rules_unchanged():
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
    base = {"price": 10, "condition": "new", "currency": "EUR", "size": "43", "material": "cotton", "material_percentage": 100, "style": "intentionally mismatched", "design": "stripes"}
    assert evaluate(mandate, base)["decision"] == "APPROVED"
    bad = dict(base); bad["design"] = "snowmen"
    assert evaluate(mandate, bad)["decision"] == "REJECTED"
    missing = {"price": 10, "condition": "new"}
    assert evaluate(mandate, missing)["decision"] == "NEEDS_CONFIRMATION"
    assert evaluate(mandate, base)["policy_version"] == POLICY_VERSION


def test_power_delivery_rules_unchanged():
    mandate = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}, "pd_mode": "PASS_THROUGH"}
    product = {"price": 79.95, "condition": "new", "pd_input_max_w": 100, "host_output_max_w": None, "pd_pass_through_supported": True, "specs": {}}
    assert evaluate(mandate, product)["decision"] == "APPROVED"
    mandate_host = dict(mandate, pd_mode="HOST_OUTPUT")
    assert evaluate(mandate_host, product)["decision"] == "NEEDS_CONFIRMATION"
    product85 = dict(product, host_output_max_w=85)
    assert evaluate(mandate_host, product85)["decision"] == "REJECTED"


def test_multiple_offers_remain_independent():
    m = {"query": "laptop", "allowed_conditions": ["new"], "min_specs": {"ram_gb": 16}}
    offer_a = {"price": 700, "condition": "new", "key_features": ["16GB RAM", "512GB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    offer_b = {"price": 600, "condition": "new", "key_features": ["8GB RAM", "256GB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    ra = evaluate(m, offer_a)
    rb = evaluate(m, offer_b)
    assert ra["decision"] == "APPROVED"
    assert ra["rules"][-1]["observed"] == 16
    assert rb["decision"] == "NEEDS_CONFIRMATION" or rb["decision"] == "REJECTED"
    # independence: evaluating B does not affect A and vice versa
    ra2 = evaluate(m, offer_a)
    assert ra2["decision"] == "APPROVED"


def test_unrelated_existing_specs_preserved():
    p = {
        "price": 50, "condition": "new",
        "specs": {"hdmi_ports": 2, "usb_ports": 4},
        "key_features": ["16GB RAM", "512GB SSD"],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    ev = build_evidence(p)
    assert ev["specs"]["hdmi_ports"] == 2
    assert ev["specs"]["usb_ports"] == 4
    assert ev["specs"]["ram_gb"] == 16
    assert ev["specs"]["storage_gb"] == 512
