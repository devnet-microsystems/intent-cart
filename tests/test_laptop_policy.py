"""P1 Step 4B: Laptop vertical Policy Gate regression tests.

Fixtures are representative sanitized Channel3 evidence from P1 Step 2.1 real
API validation. Derived fixtures are explicitly labeled in test names/comments.
"""
import copy

from backend.policy_engine import evaluate, POLICY_VERSION


# Representative sanitized real Channel3 evidence (P1 Step 2.1)
IDEAPAD_REAL = {
    "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop Copilot+ PC AMD Ryzen AI 5 340 16GB RAM 512GB SSD Windows 11",
    "category": {"slug": "laptops", "title": "Laptops"},
    "price": 1065.18,
    "condition": "new",
    "structured_attributes": {
        "computer-form": ["Notebook"],
        "processor-family": ["AMD Ryzen AI"],
        "storage-capacity": ["512 GB"],
        "operating-system": ["Windows 11 Home"],  # real SA had Win11 context; keep explicit
    },
    "key_features": [
        "16\" Touchscreen",
        "16GB RAM",
        "512GB SSD",
        "Windows 11 Copilot+PC",
        "AMD Ryzen AI 5",
    ],
    "description": "AMD Ryzen AI 5 340 processor with integrated graphics.",
}

THINKPAD_REAL = {
    "title": "ThinkPad T14 Gen 6",
    "category": {"slug": "laptops", "title": "Laptops"},
    "price": 1346.41,
    "condition": "new",
    "structured_attributes": {
        "computer-form": ["Notebook"],
        "processor-family": ["Intel Core Ultra 7"],
        "operating-system": ["Windows 11 Pro"],
        "storage-capacity": ["1 TB"],
    },
    "key_features": [
        "32 GB memory",
        "1 TB SSD",
        "Intel Core Ultra 7 268V Processor",
        "Intel Arc Graphics 140V",
        "Windows 11 Pro",
    ],
    "description": "Intel Core Ultra 7 268V processor with integrated NPU.",
}

FREITAG_ACCESSORY = {
    "title": "FREITAG F421 Sleeve for Laptop 15\"",
    "category": {"slug": "laptop-sleeves", "title": "Laptop Sleeves"},
    "price": 78.0,
    "condition": "new",
    "key_features": ["Custodia protettiva per laptop"],
    "description": "Sleeve protezione per laptop da 15 pollici.",
}

DELL_DESKTOP = {
    "title": "Dell Tower Desktop",
    "category": {"slug": "desktop-computers", "title": "Desktop Computers"},
    "price": 923.16,
    "condition": "new",
    "key_features": ["32GB DDR5 Memory", "512GB M.2 SSD", "Windows 11 Home Operating System"],
}


def laptop_mandate(**overrides):
    m = {
        "query": "windows notebook",
        "product_type": "laptop",
        "currency": "EUR",
        "allowed_conditions": ["new"],
    }
    m.update(overrides)
    return m


def rule(result, name):
    return next(r for r in result["rules"] if r["rule"] == name)


def test_positive_ideapad_qualifying_notebook():
    # DERIVED adjustment of real IdeaPad evidence: explicit Windows 11 SA aligned
    # with key_features to satisfy a generic Windows 11 requirement unambiguously.
    p = copy.deepcopy(IDEAPAD_REAL)
    p["structured_attributes"]["operating-system"] = ["Windows 11"]
    m = laptop_mandate(min_ram_gb=16, min_storage_gb=512, required_operating_system="Windows 11")
    result = evaluate(m, p)
    assert result["decision"] == "APPROVED"
    assert rule(result, "laptop_product_classification")["passed"] is True
    assert rule(result, "min_ram_gb")["passed"] is True
    assert rule(result, "min_storage_gb")["passed"] is True
    assert rule(result, "required_operating_system")["passed"] is True
    # provenance preserved
    cls = rule(result, "laptop_product_classification")
    assert cls["evidence_source"]
    assert cls["evidence_excerpt"]


def test_positive_thinkpad_explicit_cpu_ram_storage():
    m = laptop_mandate(
        min_ram_gb=32,
        min_storage_gb=1024,
        required_processor_model="Intel Core Ultra 7 268V",
        required_operating_system="Windows 11 Pro",
    )
    result = evaluate(m, THINKPAD_REAL)
    assert result["decision"] == "APPROVED"
    assert rule(result, "required_processor")["passed"] is True
    assert rule(result, "min_ram_gb")["observed"] == 32


def test_negative_desktop_with_32gb_ram_rejected():
    m = laptop_mandate(min_ram_gb=16)
    result = evaluate(m, DELL_DESKTOP)
    assert result["decision"] == "REJECTED"
    cls = rule(result, "laptop_product_classification")
    assert cls["observed"] == "DESKTOP"
    assert cls["passed"] is False


def test_negative_freitag_sleeve_rejected():
    m = laptop_mandate()
    result = evaluate(m, FREITAG_ACCESSORY)
    assert result["decision"] == "REJECTED"
    cls = rule(result, "laptop_product_classification")
    assert cls["observed"] == "ACCESSORY"
    assert cls["passed"] is False


def test_negative_genuine_notebook_ram_below_minimum_rejected():
    # DERIVED from real IdeaPad structure: RAM evidence consistently reduced to 8GB.
    p = copy.deepcopy(IDEAPAD_REAL)
    p["key_features"] = [kf.replace("16GB RAM", "8GB RAM") if "16GB" in kf else kf for kf in p["key_features"]]
    p["title"] = p["title"].replace("16GB RAM", "8GB RAM")
    p["structured_attributes"] = dict(p["structured_attributes"])
    m = laptop_mandate(min_ram_gb=16)
    result = evaluate(m, p)
    assert result["decision"] == "REJECTED"
    assert rule(result, "min_ram_gb")["observed"] == 8
    assert rule(result, "min_ram_gb")["passed"] is False


def test_negative_genuine_notebook_missing_ram_needs_confirmation():
    # DERIVED: remove all explicit RAM evidence from a real notebook-shaped fixture.
    p = copy.deepcopy(IDEAPAD_REAL)
    p["key_features"] = [kf for kf in p["key_features"] if "RAM" not in kf]
    p["title"] = p["title"].replace("16GB RAM", "")
    p["structured_attributes"] = {k: v for k, v in p["structured_attributes"].items() if k != "ram"}
    m = laptop_mandate(min_ram_gb=16)
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    assert rule(result, "min_ram_gb")["observed"] is None
    assert rule(result, "min_ram_gb")["passed"] is False


def test_negative_conflicting_storage_evidence_needs_confirmation():
    # DERIVED: add conflicting raw specs against real key_features/storage SA.
    p = copy.deepcopy(IDEAPAD_REAL)
    p["specs"] = {"storage_gb": 256}
    m = laptop_mandate(min_storage_gb=512)
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    assert rule(result, "min_storage_gb")["observed"] is None


def test_negative_conflicting_operating_systems_needs_confirmation():
    # DERIVED from ThinkPad conflict observed in 2.1 (Linux vs Windows 11 Pro).
    p = copy.deepcopy(THINKPAD_REAL)
    p["structured_attributes"] = dict(p["structured_attributes"])
    p["structured_attributes"]["operating-system"] = ["Linux"]
    p["key_features"] = list(p["key_features"])  # still includes Windows 11 Pro
    m = laptop_mandate(required_operating_system="Windows 11 Pro")
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    osr = rule(result, "required_operating_system")
    assert osr["observed"] is None
    assert osr.get("conflicts") or osr.get("observed_detail") is not None or osr["evidence_excerpt"]


def test_negative_incompatible_cpu_family_rejected():
    m = laptop_mandate(required_processor_family="AMD Ryzen AI")
    result = evaluate(m, THINKPAD_REAL)
    assert result["decision"] == "REJECTED"
    assert rule(result, "required_processor")["passed"] is False


def test_negative_generic_cpu_evidence_when_exact_model_required_needs_confirmation():
    # DERIVED: only family-level CPU evidence, exact model required.
    p = copy.deepcopy(IDEAPAD_REAL)
    p["key_features"] = [kf for kf in p["key_features"] if "Ryzen AI 5" not in kf]
    p["structured_attributes"] = dict(p["structured_attributes"])
    p["structured_attributes"]["processor-family"] = ["AMD Ryzen AI"]
    p["description"] = "Portable laptop with AMD Ryzen AI processor."
    p["title"] = "IdeaPad Slim 5 Laptop 16GB RAM 512GB SSD"
    m = laptop_mandate(required_processor_model="AMD Ryzen AI 5 340")
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    assert rule(result, "required_processor")["observed"] is None


def test_negative_nvme_required_but_only_generic_ssd_verified_needs_confirmation():
    p = copy.deepcopy(IDEAPAD_REAL)
    p["key_features"] = ["16GB RAM", "512GB SSD", "Windows 11"]
    p["structured_attributes"] = {k: v for k, v in p["structured_attributes"].items() if "storage" not in k}
    m = laptop_mandate(required_storage_interface="NVMe")
    result = evaluate(m, p)
    assert result["decision"] == "NEEDS_CONFIRMATION"
    assert rule(result, "required_storage_interface")["observed"] is None


def test_windows11_generic_accepts_pro_but_pro_requirement_rejects_home():
    # DERIVED: make OS evidence unambiguously Windows 11 Home across sources so
    # the normalizer can verify it (real data may conflict; conflict is tested separately).
    p = copy.deepcopy(IDEAPAD_REAL)
    p["structured_attributes"]["operating-system"] = ["Windows 11 Home"]
    p["key_features"] = [kf.replace("Windows 11 Copilot+PC", "Windows 11 Home") if "Windows 11" in kf else kf for kf in p["key_features"]]
    p["title"] = p["title"].replace("Windows 11", "Windows 11 Home")
    # isolated OS compatibility: generic accepts Home
    res = evaluate(laptop_mandate(required_operating_system="Windows 11"), p)
    assert rule(res, "required_operating_system")["passed"] is True
    p2 = copy.deepcopy(p)
    res2 = evaluate(laptop_mandate(required_operating_system="Windows 11 Pro"), p2)
    assert rule(res2, "required_operating_system")["passed"] is False
    assert res2["decision"] == "REJECTED"


def test_generic_windows_requirement_accepts_verified_windows_editions():
    # DERIVED fixtures with explicit, non-conflicting OS evidence.
    base = copy.deepcopy(IDEAPAD_REAL)
    base["structured_attributes"] = dict(base["structured_attributes"])
    base["key_features"] = [kf for kf in base["key_features"] if "Windows" not in kf and "Copilot" not in kf]

    def with_os(name):
        p = copy.deepcopy(base)
        p["structured_attributes"]["operating-system"] = [name]
        p["key_features"] = list(p["key_features"]) + [name]
        p["title"] = "Notebook 16GB RAM 512GB SSD " + name
        return p

    for os_name in ("Windows 11 Home", "Windows 11 Pro", "Windows 10 Pro"):
        res = evaluate(laptop_mandate(required_operating_system="Windows"), with_os(os_name))
        osr = rule(res, "required_operating_system")
        assert osr["passed"] is True, os_name
        assert osr["status"] == "APPLICABLE"

    # Generic Windows requirement must not accept verified Linux.
    linux = with_os("Linux")
    linux["structured_attributes"]["operating-system"] = ["Linux"]
    linux["key_features"] = [kf for kf in linux["key_features"] if "Windows" not in kf] + ["Linux"]
    linux["title"] = "Notebook 16GB RAM 512GB SSD Linux"
    linux["description"] = "Portable notebook running Linux."
    res_linux = evaluate(laptop_mandate(required_operating_system="Windows"), linux)
    osr_linux = rule(res_linux, "required_operating_system")
    assert osr_linux["passed"] is False
    assert osr_linux["status"] == "APPLICABLE"
    assert osr_linux["observed"] == "Linux"
    assert res_linux["decision"] != "APPROVED"

    # Missing/conflicting OS evidence remains NOT VERIFIED (fail-closed, not pass).
    missing = copy.deepcopy(base)
    missing["structured_attributes"].pop("operating-system", None)
    missing["title"] = "Notebook 16GB RAM 512GB SSD"
    res_missing = evaluate(laptop_mandate(required_operating_system="Windows"), missing)
    osr_missing = rule(res_missing, "required_operating_system")
    assert osr_missing["passed"] is False
    assert osr_missing["observed"] is None
    assert res_missing["decision"] == "NEEDS_CONFIRMATION"


def test_os_edition_specific_matching_unchanged():
    base = copy.deepcopy(IDEAPAD_REAL)
    base["structured_attributes"] = dict(base["structured_attributes"])
    base["key_features"] = [kf for kf in base["key_features"] if "Windows" not in kf and "Copilot" not in kf]
    base["title"] = "Notebook 16GB RAM 512GB SSD"

    def with_os(name):
        p = copy.deepcopy(base)
        p["structured_attributes"]["operating-system"] = [name]
        p["key_features"] = list(p["key_features"]) + [name]
        return p

    assert rule(evaluate(laptop_mandate(required_operating_system="Windows 11"), with_os("Windows 11 Pro")), "required_operating_system")["passed"] is True
    assert rule(evaluate(laptop_mandate(required_operating_system="Windows 11 Pro"), with_os("Windows 11 Home")), "required_operating_system")["passed"] is False
    assert rule(evaluate(laptop_mandate(required_operating_system="Windows 11 Pro"), with_os("Windows 11 Pro")), "required_operating_system")["passed"] is True


def test_classification_mandatory_even_without_ram_storage():
    m = laptop_mandate()  # no min_ram/min_storage
    assert evaluate(m, DELL_DESKTOP)["decision"] == "REJECTED"
    assert evaluate(m, {"title": "Mystery computer", "price": 1, "condition": "new"})["decision"] == "NEEDS_CONFIRMATION"


def test_missing_condition_required_remains_needs_confirmation():
    p = copy.deepcopy(IDEAPAD_REAL)
    p.pop("condition", None)
    m = laptop_mandate(allowed_conditions=["new"])
    result = evaluate(m, p)
    # condition rule is existing behavior; must remain NOT VERIFIED / NEEDS_CONFIRMATION
    cond = next((r for r in result["rules"] if r["rule"] == "condition"), None)
    assert cond is not None
    assert cond["passed"] is False
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_duplicate_generic_and_laptop_minimums_conjunctive():
    p = copy.deepcopy(IDEAPAD_REAL)
    m = laptop_mandate(min_ram_gb=16, min_specs={"ram_gb": 32})
    result = evaluate(m, p)
    ram_rules = [r for r in result["rules"] if r.get("rule") in ("min_ram_gb", "min_ram_gb") and (r.get("evidence") == "product.specs.ram_gb" or "ram" in r.get("rule", ""))]
    # both requirements evaluated; the stricter 32GB from min_specs fails on 16GB evidence
    assert result["decision"] == "REJECTED"
    assert any(r.get("expected") == 32 or r.get("all_requirement_thresholds") for r in result["rules"] if "ram_gb" in r.get("rule", ""))
    origins = [r.get("requirement_origin") for r in result["rules"] if "ram_gb" in r.get("rule", "")]
    assert "min_specs" in origins or any(r.get("expected") == 32 for r in result["rules"] if "ram_gb" in r.get("rule", ""))


def test_multiple_offers_remain_independent_laptop():
    m = laptop_mandate(min_ram_gb=16)
    offer_a = {"price": 700, "condition": "new", "key_features": ["16GB RAM", "512GB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    offer_b = {"price": 600, "condition": "new", "key_features": ["8GB RAM", "256GB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    ra = evaluate(m, offer_a)
    rb = evaluate(m, offer_b)
    assert rule(ra, "min_ram_gb")["observed"] == 16
    assert rule(rb, "min_ram_gb")["observed"] == 8
    assert ra["decision"] != rb["decision"] or rule(ra, "min_ram_gb")["passed"] != rule(rb, "min_ram_gb")["passed"]
    ra2 = evaluate(m, offer_a)
    assert rule(ra2, "min_ram_gb")["observed"] == 16


def test_missing_evidence_never_produces_approved_laptop():
    m = laptop_mandate(min_ram_gb=16, min_storage_gb=512)
    p = {"title": "Notebook", "category": {"slug": "laptops"}, "structured_attributes": {"computer-form": ["Notebook"]}, "condition": "new", "price": 500}
    result = evaluate(m, p)
    assert result["decision"] != "APPROVED"
    assert result["decision"] == "NEEDS_CONFIRMATION"


def test_existing_price_condition_pd_material_style_design_rules_unchanged():
    # exact existing-style mandates must behave as before (non-laptop)
    mandate = {
        "max_price": 15, "currency": "EUR", "allowed_conditions": ["new"], "forbidden_conditions": ["refurbished"],
        "min_specs": {}, "size": "43", "material": "cotton", "material_percentage": 100,
        "style": "intentionally mismatched", "forbidden_designs": ["Christmas patterns", "snowmen"],
    }
    base = {"price": 10, "condition": "new", "currency": "EUR", "size": "43", "material": "cotton", "material_percentage": 100, "style": "intentionally mismatched", "design": "stripes"}
    assert evaluate(mandate, base)["decision"] == "APPROVED"
    assert evaluate(mandate, base)["policy_version"] == POLICY_VERSION
    # PD unchanged
    pd = {"max_price": 90, "allowed_conditions": ["new"], "forbidden_conditions": [], "min_specs": {"power_delivery_w": 100}, "pd_mode": "PASS_THROUGH"}
    product = {"price": 79.95, "condition": "new", "pd_input_max_w": 100, "host_output_max_w": None, "pd_pass_through_supported": True, "specs": {}}
    assert evaluate(pd, product)["decision"] == "APPROVED"


def test_non_laptop_mandate_unchanged_even_with_laptop_like_product():
    m = {"query": "laptop", "allowed_conditions": ["new"]}  # no product_type
    result = evaluate(m, DELL_DESKTOP)
    assert result["decision"] == "APPROVED"  # legacy behavior: only condition/price rules apply
    assert not any(r["rule"] == "laptop_product_classification" for r in result["rules"])


def test_no_mutation_of_mandate_or_product():
    m = laptop_mandate(min_ram_gb=16, min_specs={"ram_gb": 16})
    p = copy.deepcopy(IDEAPAD_REAL)
    m_before = copy.deepcopy(m)
    p_before = copy.deepcopy(p)
    evaluate(m, p)
    assert m == m_before
    assert p == p_before
