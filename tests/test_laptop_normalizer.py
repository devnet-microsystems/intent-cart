import copy

from backend.laptop_normalizer import normalize_laptop_specs


def _assert_shape(field_result):
    assert set(field_result.keys()) >= {"value", "status", "evidence_source", "evidence_excerpt"}
    assert field_result["status"] in ("VERIFIED", "NOT_VERIFIED")


def test_returns_all_six_fields_with_consistent_shape():
    result = normalize_laptop_specs({"title": "IdeaPad Slim 5"})
    assert set(result.keys()) >= {
        "computer_form",
        "ram_gb",
        "storage_gb",
        "storage_interface",
        "processor_family",
        "operating_system",
        "product_classification",
    }
    for k in ("computer_form", "ram_gb", "storage_gb", "storage_interface", "processor_family", "operating_system", "product_classification"):
        _assert_shape(result[k])


def test_explicit_values_from_specs():
    p = {
        "specs": {
            "computer_form": "Notebook",
            "ram_gb": 16,
            "storage_gb": 512,
            "storage_interface": "NVMe",
            "processor_family": "Intel Core i7",
            "operating_system": "Windows 11 Pro",
        }
    }
    r = normalize_laptop_specs(p)
    assert r["computer_form"] == {"value": "Notebook", "status": "VERIFIED", "evidence_source": "product.specs.computer_form", "evidence_excerpt": "Notebook"}
    assert r["ram_gb"]["value"] == 16 and r["ram_gb"]["status"] == "VERIFIED" and r["ram_gb"]["evidence_source"] == "product.specs.ram_gb"
    assert r["storage_gb"]["value"] == 512 and r["storage_gb"]["status"] == "VERIFIED"
    assert r["storage_interface"]["value"] == "NVMe" and r["storage_interface"]["status"] == "VERIFIED"
    assert r["processor_family"]["value"] == "Intel Core i7" and r["processor_family"]["status"] == "VERIFIED"
    assert r["operating_system"]["value"] == "Windows 11 Pro" and r["operating_system"]["status"] == "VERIFIED"


def test_structured_attributes_realistic_channel3_fixture():
    # Realistic Channel3-shaped fixture (coverage-audit style).
    p = {
        "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop",
        "structured_attributes": {
            "processor-family": ["AMD Ryzen AI 5"],
            "computer-form": ["Notebook"],
            "operating-system": ["Windows 11 Home"],
            "hard-drive-capacity": ["512 GB"],
            "hard-drive-interface": ["PCIe NVMe"],
            "ram": ["16 GB"],
        },
        "key_features": ["16GB RAM", "512GB SSD"],
        "materials": ["Aluminum"],
    }
    r = normalize_laptop_specs(p)
    assert r["computer_form"]["status"] == "VERIFIED"
    assert r["computer_form"]["value"] == "Notebook"
    assert r["computer_form"]["evidence_source"] == "product.structured_attributes.computer-form"
    assert r["processor_family"]["status"] == "VERIFIED"
    assert r["processor_family"]["evidence_excerpt"] == "AMD Ryzen AI 5"
    assert r["operating_system"]["value"] == "Windows 11 Home"


def test_key_features_extraction():
    p = {
        "title": "Generic Notebook",
        "key_features": [
            "15.6 inch display",
            "16GB RAM",
            "512GB NVMe SSD",
            "Intel Core i5 processor",
            "Windows 11",
        ],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    assert r["ram_gb"]["value"] == 16
    assert r["ram_gb"]["evidence_source"].startswith("product.key_features[")
    assert r["ram_gb"]["evidence_excerpt"] == "16GB RAM"
    assert r["storage_gb"]["value"] == 512
    assert "NVMe SSD" in r["storage_gb"]["evidence_excerpt"]
    assert r["storage_interface"]["value"] in ("NVMe", "PCIe NVMe")
    assert r["operating_system"]["value"] == "Windows 11"


def test_title_fallback():
    p = {
        "title": "15.6\" Notebook 16 GB RAM 512GB SSD Intel Core i7 Windows 11 Pro",
        "description": "Portable laptop computer",
    }
    r = normalize_laptop_specs(p)
    assert r["computer_form"]["value"] == "Notebook"
    assert r["ram_gb"]["value"] == 16
    assert r["storage_gb"]["value"] == 512
    assert r["processor_family"]["status"] == "VERIFIED"
    assert r["operating_system"]["value"] == "Windows 11 Pro"


def test_valid_notebook_classification():
    r = normalize_laptop_specs({"structured_attributes": {"computer-form": ["Notebook"]}})
    assert r["computer_form"]["value"] == "Notebook"
    assert r["computer_form"]["status"] == "VERIFIED"


def test_laptop_sleeve_accessory_rejection():
    p = {"title": "Laptop Sleeve 15.6 inch Protective Case Bag for Notebook"}
    r = normalize_laptop_specs(p)
    assert r["computer_form"]["status"] == "NOT_VERIFIED"
    assert r["computer_form"]["value"] is None
    assert "sleeve" in (r["computer_form"]["evidence_excerpt"] or "").lower()
    assert r["computer_form"]["evidence_source"] == "product.title"


def test_dock_accessory_rejection():
    p = {"title": "USB-C Docking Station for Laptop", "key_features": ["16GB RAM not applicable"]}
    r = normalize_laptop_specs(p)
    assert r["computer_form"]["status"] == "NOT_VERIFIED"
    assert r["computer_form"]["value"] is None


def test_missing_ram_or_storage():
    p = {"title": "Thin Notebook", "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["ram_gb"]["status"] == "NOT_VERIFIED"
    assert r["ram_gb"]["value"] is None
    assert r["storage_gb"]["status"] == "NOT_VERIFIED"
    assert r["storage_gb"]["value"] is None


def test_ram_32gb_2x16gb_config():
    p = {"key_features": ["32GB (2x16GB) DDR5 RAM", "1TB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["ram_gb"]["status"] == "VERIFIED"
    assert r["ram_gb"]["value"] == 32
    assert "2x16GB" in r["ram_gb"]["evidence_excerpt"]


def test_decimal_tb_conversion():
    p = {"key_features": ["16GB RAM", "1.5TB SSD"], "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["storage_gb"]["status"] == "VERIFIED"
    assert r["storage_gb"]["value"] == 1536


def test_expandable_versus_installed_storage():
    p = {
        "key_features": ["16GB RAM", "512GB SSD, expandable up to 2TB"],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    # Must not treat expandable/max capacity as installed storage.
    assert r["storage_gb"]["status"] == "VERIFIED"
    assert r["storage_gb"]["value"] == 512
    assert "expandable" not in r["storage_gb"]["evidence_excerpt"].lower()


def test_up_to_storage_not_verified_as_installed():
    p = {"description": "Supports up to 1TB SSD storage", "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["storage_gb"]["status"] == "NOT_VERIFIED"


def test_conflicting_sources():
    p = {
        "specs": {"ram_gb": 16},
        "key_features": ["32GB RAM"],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    assert r["ram_gb"]["status"] == "NOT_VERIFIED"
    assert r["ram_gb"]["value"] is None
    assert "conflicts" in r["ram_gb"]
    sources = {c["evidence_source"] for c in r["ram_gb"]["conflicts"]}
    assert "product.specs.ram_gb" in sources
    excerpts = " ".join(c["evidence_excerpt"] for c in r["ram_gb"]["conflicts"])
    assert "16" in excerpts and "32" in excerpts


def test_no_input_mutation():
    p = {
        "title": "Notebook 16GB RAM 512GB SSD",
        "specs": {"operating_system": "Windows 11"},
        "structured_attributes": {"processor-family": ["Intel Core i5"]},
        "key_features": ["NVMe SSD"],
    }
    original = copy.deepcopy(p)
    normalize_laptop_specs(p)
    assert p == original


def test_exact_evidence_provenance():
    p = {
        "specs": {"storage_interface": "SATA"},
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    assert r["storage_interface"]["evidence_source"] == "product.specs.storage_interface"
    assert r["storage_interface"]["evidence_excerpt"] == "SATA"

def test_ram_not_confused_with_storage():
    p = {"key_features": ["512GB SSD", "8GB RAM"], "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["ram_gb"]["value"] == 8
    assert r["storage_gb"]["value"] == 512


def test_processor_family_does_not_invent_exact_model():
    p = {"structured_attributes": {"processor-family": ["AMD Ryzen AI 5"], "computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    assert r["processor_family"]["status"] == "VERIFIED"
    # Value must reflect evidenced family/subfamily, not a fabricated exact SKU.
    assert "Ryzen" in r["processor_family"]["value"]
    assert r["processor_family"]["evidence_excerpt"] == "AMD Ryzen AI 5"


# --- P1 Step 3A: representative sanitized Channel3 evidence (from 2.1 validation) ---

IDEAPAD_REAL = {
    "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop Copilot+ PC AMD Ryzen AI 5 340 16GB RAM 512GB SSD Windows 11",
    "category": {"slug": "laptops", "title": "Laptops"},
    "structured_attributes": {
        "computer-form": ["Notebook"],
        "processor-family": ["AMD Ryzen AI"],
    },
    "key_features": [
        "16\" Touchscreen",
        "16GB RAM",
        "512GB SSD",
        "Windows 11 Copilot+PC",
        "AMD Ryzen AI 5",
    ],
    "description": "AMD Ryzen AI 5 340 processor with integrated graphics and AI features.",
}

THINKPAD_REAL = {
    "title": "ThinkPad T14 Gen 6",
    "category": {"slug": "laptops", "title": "Laptops"},
    "structured_attributes": {
        "computer-form": ["Notebook"],
        "processor-family": ["Intel Core Ultra 7"],
        "operating-system": ["Linux"],
        "storage-capacity": ["1 TB"],
    },
    "key_features": [
        "Copilot+ AI performance",
        "Intel Core Ultra 7 268V Processor",
        "Intel Arc Graphics 140V",
        "Windows 11 Pro",
    ],
    "description": "Intel Core Ultra 7 268V processor with integrated NPU.",
}

SURFACE_REAL = {
    "title": "Microsoft Surface Laptop 13\" Notebook",
    "category": {"slug": "laptops", "title": "Laptops"},
    "structured_attributes": {
        "computer-form": ["Notebook"],
        "processor-family": ["Qualcomm Snapdragon X Plus"],
    },
    "description": "Portable notebook with Windows 11 Pro.",
}

FREITAG_ACCESSORY = {
    "title": "FREITAG F421 Sleeve for Laptop 15\"",
    "category": {"slug": "laptop-sleeves", "title": "Laptop Sleeves"},
    "key_features": ["Custodia protettiva per laptop"],
    "description": "Sleeve protezione per laptop da 15 pollici.",
}

DELL_DESKTOP = {
    "title": "Dell Tower Desktop",
    "category": {"slug": "desktop-computers", "title": "Desktop Computers"},
    "key_features": ["16GB DDR5 Memory", "512GB M.2 SSD", "Windows 11 Home Operating System"],
}


def test_product_classification_observed_notebooks():
    for name, p in (("ideapad", IDEAPAD_REAL), ("thinkpad", THINKPAD_REAL), ("surface", SURFACE_REAL)):
        r = normalize_laptop_specs(p)
        c = r["product_classification"]
        _assert_shape(c)
        assert c["status"] == "VERIFIED", name
        assert c["value"] == "NOTEBOOK", name
        assert c["evidence_source"]


def test_product_classification_freitag_accessory():
    r = normalize_laptop_specs(FREITAG_ACCESSORY)
    c = r["product_classification"]
    assert c["status"] == "VERIFIED"
    assert c["value"] == "ACCESSORY"
    assert "laptop-sleeves" in (c["evidence_excerpt"] or "").lower() or "product.category" in (c["evidence_source"] or "")
    # Notebook mentions are compatibility references, preserved diagnostically.
    refs = c.get("compatibility_references") or []
    assert any("notebook" in (ref.get("evidence_excerpt") or "").lower() or "laptop" in (ref.get("evidence_excerpt") or "").lower() for ref in refs)


def test_product_classification_accessory_with_compatible_notebook_refs_verified():
    # DERIVED from observed FREITAG structure: explicit accessory category+title
    # with notebook compatibility mentions in description/features.
    p = {
        "title": "Protective Laptop Sleeve 14 inch",
        "category": {"slug": "laptop-sleeves", "title": "Laptop Sleeves"},
        "key_features": ["Fits most 14 inch notebooks"],
        "description": "Padded sleeve compatible with laptops and notebooks.",
    }
    c = normalize_laptop_specs(p)["product_classification"]
    assert c["status"] == "VERIFIED"
    assert c["value"] == "ACCESSORY"


def test_product_classification_genuine_contradictory_form_and_category_unknown():
    # Explicit structured notebook form contradicts explicit accessory category.
    p = {
        "title": "Laptop Sleeve",
        "category": {"slug": "laptop-sleeves", "title": "Laptop Sleeves"},
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    c = normalize_laptop_specs(p)["product_classification"]
    assert c["status"] == "NOT_VERIFIED"
    assert c["value"] == "UNKNOWN"
    assert "conflicts" in c


def test_product_classification_dell_desktop():
    r = normalize_laptop_specs(DELL_DESKTOP)
    c = r["product_classification"]
    assert c["status"] == "VERIFIED"
    assert c["value"] == "DESKTOP"
    assert "desktop" in c["evidence_excerpt"].lower() or "product.category" == c["evidence_source"]


def test_product_classification_unknown_when_ambiguous():
    r = normalize_laptop_specs({"title": "Mystery box", "description": "Electronics item"})
    c = r["product_classification"]
    assert c["value"] == "UNKNOWN"
    assert c["status"] == "NOT_VERIFIED"


def test_absence_of_notebook_evidence_does_not_imply_desktop_or_accessory():
    r = normalize_laptop_specs({"title": "Computer thing", "key_features": ["16GB RAM"]})
    assert r["product_classification"]["value"] == "UNKNOWN"


def test_cpu_hierarchy_amd_ryzen_ai_compatible_observed():
    r = normalize_laptop_specs(IDEAPAD_REAL)
    cpu = r["processor_family"]
    assert cpu["status"] == "VERIFIED"
    assert "cpu_hierarchy" in cpu
    h = cpu["cpu_hierarchy"]
    assert h["family"] == "AMD Ryzen AI"
    assert h["subfamily"] == "AMD Ryzen AI 5"
    assert h["model"] == "AMD Ryzen AI 5 340"
    # preserves original evidence
    all_excerpts = " ".join(e.get("evidence_excerpt", "") for e in cpu.get("evidence_sources", []))
    assert "AMD Ryzen AI" in all_excerpts
    assert "340" in all_excerpts


def test_cpu_hierarchy_intel_core_ultra_compatible_gpu_excluded():
    r = normalize_laptop_specs(THINKPAD_REAL)
    cpu = r["processor_family"]
    assert cpu["status"] == "VERIFIED"
    h = cpu["cpu_hierarchy"]
    assert h["family"] == "Intel Core Ultra 7"
    assert h["model"] == "Intel Core Ultra 7 268V"
    # GPU must not create a CPU conflict
    assert "gpu_evidence" in cpu
    gpu_text = " ".join(g["evidence_excerpt"] for g in cpu["gpu_evidence"])
    assert "Arc Graphics 140V" in gpu_text
    assert not cpu.get("conflicts")
    cpu_excerpts = " ".join(e.get("evidence_excerpt", "") for e in cpu.get("evidence_sources", []))
    assert "Arc Graphics" not in cpu_excerpts


def test_cpu_genuinely_incompatible_families_not_verified():
    p = {
        "key_features": ["Intel Core i7 processor", "AMD Ryzen 5 CPU"],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    cpu = r["processor_family"]
    assert cpu["status"] == "NOT_VERIFIED"
    assert cpu["value"] is None
    assert "conflicts" in cpu
    excerpts = " ".join(c["evidence_excerpt"] for c in cpu["conflicts"])
    assert "Intel" in excerpts and "Ryzen" in excerpts


def test_cpu_conflicting_exact_models_not_verified():
    p = {
        "specs": {"processor": "Intel Core Ultra 7 155H"},
        "key_features": ["Intel Core Ultra 7 258V processor"],
        "structured_attributes": {"computer-form": ["Notebook"]},
    }
    r = normalize_laptop_specs(p)
    cpu = r["processor_family"]
    assert cpu["status"] == "NOT_VERIFIED"
    assert "conflicts" in cpu


def test_cpu_provenance_preserved():
    p = {"structured_attributes": {"processor-family": ["AMD Ryzen AI 5"], "computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    cpu = r["processor_family"]
    assert cpu["evidence_source"]
    assert cpu["evidence_excerpt"] == "AMD Ryzen AI 5"
    assert any(e["evidence_source"] == "product.structured_attributes.processor-family" for e in cpu.get("evidence_sources", []))


def test_step3a_no_mutation_observed_fixtures():
    for p in (IDEAPAD_REAL, THINKPAD_REAL, SURFACE_REAL, FREITAG_ACCESSORY, DELL_DESKTOP):
        original = copy.deepcopy(p)
        normalize_laptop_specs(p)
        assert p == original

def test_os_structured_attribute_included_in_provenance():
    p = {"structured_attributes": {"operating-system": ["Windows"], "computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "VERIFIED"
    assert osr["value"] == "Windows"
    assert "product.structured_attributes.operating-system" in (osr["evidence_source"] or "")
    assert osr["evidence_excerpt"] == "Windows"


def test_os_compatible_specificity_chain_windows_to_11_home_verified():
    # Sanitized, observed IdeaPad-shaped real evidence (execution a4b51b1d, idx 9).
    p = {
        "title": "IdeaPad Slim 5 16\" FHD+ Touchscreen Laptop Copilot+ PC AMD Ryzen AI 5 340 16GB RAM 512GB SSD Windows 11",
        "description": "The Lenovo IdeaPad Slim 5 ... Running Windows 11 Home with AI-powered Copilot features...",
        "structured_attributes": {"operating-system": ["Windows"], "computer-form": ["Notebook"]},
        "key_features": ["16\" Touchscreen", "16GB RAM", "512GB SSD", "Windows 11 Copilot+PC", "AMD Ryzen AI 5"],
    }
    original = copy.deepcopy(p)
    r = normalize_laptop_specs(p)
    assert p == original
    osr = r["operating_system"]
    assert osr["status"] == "VERIFIED"
    assert osr["value"] == "Windows 11 Home"
    src = osr["evidence_source"] or ""
    for expected in (
        "product.structured_attributes.operating-system",
        "product.key_features[3]",
        "product.title",
        "product.description",
    ):
        assert expected in src, src
    excerpts = osr["evidence_excerpt"] or ""
    assert "Windows" in excerpts and "Windows 11 Copilot+PC" in excerpts and "Windows 11 Home" in excerpts
    assert not osr.get("conflicts")


def test_os_compatible_windows_and_windows_11_pro_verified():
    p = {"structured_attributes": {"operating-system": ["Windows"], "computer-form": ["Notebook"]}, "key_features": ["Windows 11 Pro"]}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "VERIFIED" and osr["value"] == "Windows 11 Pro"
    assert "product.structured_attributes.operating-system" in (osr["evidence_source"] or "")
    assert not osr.get("conflicts")


def test_os_generic_windows_only_verified_as_generic():
    p = {"structured_attributes": {"operating-system": ["Windows"], "computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "VERIFIED" and osr["value"] == "Windows" and not osr.get("conflicts")


def test_os_real_conflict_home_vs_pro_not_verified():
    p = {"structured_attributes": {"operating-system": ["Windows 11 Home"], "computer-form": ["Notebook"]}, "key_features": ["Windows 11 Pro"]}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "NOT_VERIFIED" and osr["value"] is None
    vals = {c.get("value") for c in (osr.get("conflicts") or [])}
    assert "Windows 11 Home" in vals and "Windows 11 Pro" in vals
    assert all(c.get("evidence_source") and c.get("evidence_excerpt") for c in (osr.get("conflicts") or []))


def test_os_real_conflict_windows_10_vs_11_not_verified():
    p = {"key_features": ["Windows 10"], "description": "Ships with Windows 11 Home."}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "NOT_VERIFIED" and osr["value"] is None
    vals = {c.get("value") for c in (osr.get("conflicts") or [])}
    assert "Windows 10" in vals and "Windows 11 Home" in vals


def test_os_real_conflict_windows_vs_linux_not_verified():
    p = {"structured_attributes": {"operating-system": ["Linux"]}, "key_features": ["Windows 11 Pro"]}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "NOT_VERIFIED" and osr["value"] is None
    vals = {c.get("value") for c in (osr.get("conflicts") or [])}
    assert "Linux" in vals and "Windows 11 Pro" in vals


def test_os_copilot_pc_not_treated_as_edition():
    p = {"key_features": ["Windows 11 Copilot+PC"], "structured_attributes": {"computer-form": ["Notebook"]}}
    r = normalize_laptop_specs(p)
    osr = r["operating_system"]
    assert osr["status"] == "VERIFIED" and osr["value"] == "Windows 11"
