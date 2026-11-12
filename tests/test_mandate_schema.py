import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.intent_parser import Mandate, parse_intent


def test_new_laptop_optional_fields_absent_by_default():
    m = Mandate(query="socks")
    d = m.model_dump()
    assert d["product_type"] is None
    assert d["min_ram_gb"] is None
    assert d["min_storage_gb"] is None
    assert d["required_storage_interface"] is None
    assert d["required_processor_family"] is None
    assert d["required_processor_subfamily"] is None
    assert d["required_processor_model"] is None
    assert d["required_operating_system"] is None
    # existing defaults preserved
    assert d["currency"] == "EUR"
    assert d["min_specs"] == {}
    assert d["allowed_conditions"] == []


def test_min_specs_null_normalized_to_empty_dict():
    # Exact observed real Gemini failure shape from P1 Step 4C.
    m = Mandate.model_validate({"query": "Windows laptop", "product_type": "laptop", "min_specs": None})
    assert m.min_specs == {}


def test_parse_intent_handles_min_specs_null_without_500():
    payload = {
        "query": "Lenovo ThinkPad Intel Core Ultra 7 32GB RAM",
        "product_type": "laptop",
        "min_ram_gb": 32,
        "required_processor_family": "Intel",
        "required_processor_subfamily": "Core Ultra",
        "required_processor_model": "Ultra 7",
        "max_price": 1500.0,
        "currency": "EUR",
        "min_specs": None,
        "allowed_conditions": [],
        "forbidden_conditions": [],
    }
    result = _mock_parse_with_payload(payload)
    assert result["min_specs"] == {}
    assert result["product_type"] == "laptop"


def test_min_specs_empty_list_normalized_to_empty_dict():
    # Real Gemini may return [] when no spec constraints extracted.
    m = Mandate.model_validate({"query": "Windows laptop", "product_type": "laptop", "min_specs": []})
    assert m.min_specs == {}


def test_parse_intent_handles_min_specs_empty_list_without_500():
    payload = {
        "query": "Lenovo ThinkPad laptop",
        "product_type": "laptop",
        "max_price": 1500.0,
        "currency": "EUR",
        "min_specs": [],
        "allowed_conditions": [],
        "forbidden_conditions": [],
    }
    result = _mock_parse_with_payload(payload)
    assert result["min_specs"] == {}
    assert result["product_type"] == "laptop"


def test_min_specs_valid_dictionary_preserved():
    m = Mandate.model_validate({"query": "hub", "min_specs": {"power_delivery_w": 100}})
    assert m.min_specs == {"power_delivery_w": 100}
    payload = {"query": "hub", "min_specs": {"power_delivery_w": 65}}
    result = _mock_parse_with_payload(payload)
    assert result["min_specs"] == {"power_delivery_w": 65}


def test_malformed_min_specs_not_silently_coerced():
    # semantically empty: null/[] already normalized
    with pytest.raises(ValidationError):
        Mandate.model_validate({"query": "laptop", "min_specs": "not-a-dict"})
    with pytest.raises(ValidationError):
        Mandate.model_validate({"query": "laptop", "min_specs": [1, 2]})  # non-empty list
    with pytest.raises(ValidationError):
        Mandate.model_validate({"query": "laptop", "min_specs": 123})
    with pytest.raises(ValidationError):
        Mandate.model_validate({"query": "laptop", "min_specs": True})


def test_parse_intent_malformed_min_specs_raises_controlled_runtime_error():
    payload = {"query": "laptop", "min_specs": [1, 2]}
    with pytest.raises(RuntimeError, match="Invalid Gemini response"):
        _mock_parse_with_payload(payload)
    payload_str = {"query": "laptop", "min_specs": "oops"}
    with pytest.raises(RuntimeError, match="Invalid Gemini response"):
        _mock_parse_with_payload(payload_str)


def test_explicit_laptop_mandate():
    m = Mandate(query="Windows laptop", product_type="laptop")
    assert m.product_type == "laptop"


def test_explicit_windows_requirement():
    m = Mandate(query="Windows laptop", product_type="laptop", required_operating_system="Windows 11 Pro")
    assert m.required_operating_system == "Windows 11 Pro"


def test_thinkpad_mandate_maps_to_laptop_product_type():
    m = Mandate(query="ThinkPad", product_type="laptop")
    assert m.product_type == "laptop"


def test_ram_storage_extraction_and_positive_values():
    m = Mandate(query="laptop", product_type="laptop", min_ram_gb=16, min_storage_gb=512)
    assert m.min_ram_gb == 16
    assert m.min_storage_gb == 512


def test_processor_hierarchy_fields():
    m = Mandate(
        query="laptop",
        product_type="laptop",
        required_processor_family="AMD Ryzen AI",
        required_processor_subfamily="AMD Ryzen AI 5",
        required_processor_model="AMD Ryzen AI 5 340",
    )
    assert m.required_processor_family == "AMD Ryzen AI"
    assert m.required_processor_subfamily == "AMD Ryzen AI 5"
    assert m.required_processor_model == "AMD Ryzen AI 5 340"


def test_missing_values_remain_none():
    m = Mandate(query="laptop", product_type="laptop", min_ram_gb=16)
    assert m.min_storage_gb is None
    assert m.required_operating_system is None
    assert m.required_processor_family is None


@pytest.mark.parametrize("field,value", [
    ("min_ram_gb", 0),
    ("min_ram_gb", -8),
    ("min_storage_gb", 0),
    ("min_storage_gb", -256),
])
def test_invalid_zero_negative_capacities_rejected(field, value):
    with pytest.raises(ValidationError):
        Mandate(query="laptop", **{field: value})


def test_legacy_non_laptop_mandate_compatibility():
    payload = {
        "query": "men's mismatched socks",
        "max_price": 15,
        "currency": "EUR",
        "allowed_conditions": ["new"],
        "forbidden_conditions": ["refurbished"],
        "min_specs": {},
        "size": "43",
        "material": "cotton",
        "material_percentage": 100,
        "style": "intentionally mismatched",
        "forbidden_designs": ["Christmas patterns"],
    }
    m = Mandate.model_validate(payload)
    d = m.model_dump()
    for k, v in payload.items():
        assert d[k] == v
    assert d["product_type"] is None
    assert d["min_ram_gb"] is None


def test_old_persisted_mandate_payload_compatibility():
    # Simulates persisted payload before P1 Step 4A.
    old = {
        "query": "USB-C hub",
        "max_price": 50.0,
        "currency": "EUR",
        "allowed_conditions": ["new"],
        "forbidden_conditions": [],
        "min_specs": {"power_delivery_w": 100},
        "pd_mode": "PASS_THROUGH",
        "condition": None,
        "extra_future": "kept",
    }
    m = Mandate.model_validate(old)
    d = m.model_dump()
    assert d["query"] == "USB-C hub"
    assert d["min_specs"]["power_delivery_w"] == 100
    assert d["pd_mode"] == "PASS_THROUGH"
    assert d["extra_future"] == "kept"
    assert d["min_ram_gb"] is None


def test_conflicting_requirement_values_not_silently_chosen():
    # Explicit conflicting values in the same payload must not be silently
    # collapsed to one side. Model keeps the explicitly provided values; callers
    # can detect conflict from preserved fields.
    m = Mandate.model_validate({
        "query": "laptop",
        "min_ram_gb": 16,
        "min_specs": {"ram_gb": 32},
        "required_operating_system": "Windows 11",
        "structured_attributes": {"operating-system": ["Linux"]},
    })
    d = m.model_dump()
    assert d["min_ram_gb"] == 16
    assert d["min_specs"]["ram_gb"] == 32
    assert d["required_operating_system"] == "Windows 11"
    assert d["structured_attributes"]["operating-system"] == ["Linux"]


def test_new_laptop_fields_not_mapped_to_min_specs():
    m = Mandate(query="laptop", product_type="laptop", min_ram_gb=16, min_storage_gb=512)
    d = m.model_dump()
    # Step 4A: schema only; Policy Gate mapping happens in Step 4B.
    assert d["min_specs"] == {}
    assert d["min_ram_gb"] == 16
    assert d["min_storage_gb"] == 512


def test_storage_interface_generic_ssd_not_required():
    payload = {"query": "Windows laptop 512GB SSD", "product_type": "laptop", "min_storage_gb": 512, "required_storage_interface": "SSD", "min_specs": None}
    result = _mock_parse_with_payload(payload)
    assert result["min_storage_gb"] == 512
    assert result["required_storage_interface"] is None


@pytest.mark.parametrize("raw,expected", [
    ("NVMe", "NVMe"),
    ("1TB NVMe SSD", "NVMe"),
    ("PCIe NVMe SSD", "PCIe NVMe"),
    ("SATA SSD", "SATA"),
])
def test_storage_interface_explicit_interfaces_preserved(raw, expected):
    payload = {"query": "laptop", "product_type": "laptop", "required_storage_interface": raw, "min_specs": {}}
    result = _mock_parse_with_payload(payload)
    assert result["required_storage_interface"] == expected


def test_cpu_intel_core_ultra_7_canonical_family_only():
    payload = {
        "query": "ThinkPad",
        "product_type": "laptop",
        "required_processor_family": "Intel",
        "required_processor_subfamily": "Core Ultra",
        "required_processor_model": "Ultra 7",
        "min_specs": None,
    }
    result = _mock_parse_with_payload(payload)
    assert result["required_processor_family"] == "Intel Core Ultra 7"
    assert result["required_processor_subfamily"] is None
    assert result["required_processor_model"] is None


def test_cpu_intel_core_ultra_7_268v_canonical_model():
    payload = {
        "query": "ThinkPad",
        "product_type": "laptop",
        "required_processor_family": "Intel Core Ultra 7",
        "required_processor_model": "268V",
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["required_processor_family"] == "Intel Core Ultra 7"
    assert result["required_processor_model"] == "Intel Core Ultra 7 268V"


def test_cpu_amd_ryzen_ai_5_340_canonical_hierarchy():
    payload = {
        "query": "laptop",
        "product_type": "laptop",
        "required_processor_family": "AMD Ryzen AI",
        "required_processor_model": "Ryzen AI 5 340",
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["required_processor_family"] == "AMD Ryzen AI"
    assert result["required_processor_subfamily"] == "AMD Ryzen AI 5"
    assert result["required_processor_model"] == "AMD Ryzen AI 5 340"


def test_unspecified_cpu_requirements_remain_null():
    payload = {"query": "laptop", "product_type": "laptop", "min_ram_gb": 16, "min_specs": None}
    result = _mock_parse_with_payload(payload)
    assert result["required_processor_family"] is None
    assert result["required_processor_subfamily"] is None
    assert result["required_processor_model"] is None


def test_api_intent_returns_controlled_error_for_malformed_structured_output(monkeypatch):
    # /api/intent must not return an unhandled HTTP 500 when Gemini parsing fails.
    from fastapi.testclient import TestClient
    from backend import main

    def boom(_text):
        raise RuntimeError("Invalid Gemini response: malformed min_specs")

    monkeypatch.setattr(main, "parse_intent", boom)
    client = TestClient(main.app, raise_server_exceptions=False)
    res = client.post("/api/intent", json={"text": "windows laptop"})
    assert res.status_code == 502
    body = res.json()
    assert body.get("detail") == "Intent parsing failed due to invalid upstream response"
    # Must not leak internal exception details/secrets.
    assert "malformed" not in str(body)
    assert "GEMINI" not in str(body)


def _mock_parse_with_payload(payload):
    mock_resp = SimpleNamespace(parsed=payload, text=json.dumps(payload))

    class _Models:
        def generate_content(self, **kwargs):
            _mock_parse_with_payload.last_kwargs = kwargs
            return mock_resp

    class _Client:
        def __init__(self, **kwargs):
            self.models = _Models()

    fake_types = SimpleNamespace(
        HttpOptions=lambda **k: k,
        HttpRetryOptions=lambda **k: k,
        GenerateContentConfig=lambda **k: k,
    )
    fake_genai = SimpleNamespace(Client=_Client, types=fake_types)
    with patch("backend.intent_parser.settings") as s:
        s.gemini_api_key = "test-key"
        s.gemini_model = "test-model"
        with patch.dict("sys.modules", {"google": SimpleNamespace(genai=fake_genai), "google.genai": fake_genai, "google.genai.types": fake_types}):
            return parse_intent("Windows laptop with 16GB RAM and 512GB SSD")


def test_parse_intent_extracts_laptop_fields_from_structured_response():
    payload = {
        "query": "Windows laptop",
        "product_type": "laptop",
        "min_ram_gb": 16,
        "min_storage_gb": 512,
        "required_storage_interface": "NVMe",
        "required_processor_family": "AMD Ryzen AI",
        "required_processor_subfamily": "AMD Ryzen AI 5",
        "required_processor_model": "AMD Ryzen AI 5 340",
        "required_operating_system": "Windows 11 Pro",
        "currency": "EUR",
        "min_specs": None,
    }
    result = _mock_parse_with_payload(payload)
    assert result["product_type"] == "laptop"
    assert result["min_ram_gb"] == 16
    assert result["min_storage_gb"] == 512
    assert result["required_storage_interface"] == "NVMe"
    assert result["required_processor_model"] == "AMD Ryzen AI 5 340"
    assert result["required_operating_system"] == "Windows 11 Pro"
    # still not mapped to min_specs in Step 4A/4C.1
    assert result["min_specs"] == {}


def test_parse_intent_prompt_includes_laptop_extraction_rules():
    _mock_parse_with_payload({"query": "laptop", "product_type": "laptop", "min_specs": {}})
    prompt = _mock_parse_with_payload.last_kwargs["contents"]
    assert "product_type" in prompt
    assert "Do not infer Windows" in prompt
    assert "Do not invent processor model" in prompt
    assert "min_specs" in prompt  # explicit instruction not to map
    assert "SSD/HDD describe storage media" in prompt
    assert "Intel Core Ultra 7" in prompt


# --- Pack quantity (P0 purchase path) ---

def test_mandate_schema_accepts_pack_quantity():
    m = Mandate(query="5-pack black socks", pack_quantity=5, pack_unit="pair")
    assert m.pack_quantity == 5
    assert m.pack_unit == "pair"


def test_mandate_schema_pack_quantity_defaults_none():
    m = Mandate(query="black socks")
    assert m.pack_quantity is None
    assert m.pack_unit is None


def test_mandate_schema_pack_quantity_rejects_zero_and_negative():
    with pytest.raises(ValidationError):
        Mandate(query="socks", pack_quantity=0)
    with pytest.raises(ValidationError):
        Mandate(query="socks", pack_quantity=-5)


def test_mandate_schema_pack_quantity_rejects_non_integer():
    with pytest.raises(ValidationError):
        Mandate(query="socks", pack_quantity="5")
    with pytest.raises(ValidationError):
        Mandate(query="socks", pack_quantity=True)


def test_parse_intent_extracts_pack_quantity_from_structured_response():
    payload = {
        "query": "5-pack black cotton crew socks",
        "pack_quantity": 5,
        "pack_unit": "pair",
        "color": "black",
        "material": "cotton",
        "size": "43-46",
        "max_price": 15,
        "currency": "EUR",
        "forbidden_designs": ["logos"],
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["pack_quantity"] == 5
    assert result["pack_unit"] == "pair"
    assert result["color"] == "black"
    assert result["material"] == "cotton"
    assert result["size"] == "43-46"
    assert result["max_price"] == 15
    assert result["forbidden_designs"] == ["logos"]


def test_parse_intent_prompt_includes_pack_quantity_instruction():
    _mock_parse_with_payload({"query": "5-pack socks", "pack_quantity": 5, "pack_unit": "pair"})
    prompt = _mock_parse_with_payload.last_kwargs["contents"]
    assert "Pack constraints" in prompt
    assert "pack_quantity" in prompt
    assert "Do not invent pack quantity" in prompt


# --- Intent fidelity (P0.4.2) ---

def test_style_merged_from_technical_specs_list():
    """Style keywords in technical_specs list are merged into style field."""
    payload = {
        "query": "black ribbed crew socks",
        "style": "crew",
        "color": "black",
        "technical_specs": ["ribbed", "cotton"],
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["style"] == "ribbed crew"
    assert result["technical_specs"] == ["cotton"]


def test_style_merged_from_technical_specs_dict():
    """Style keywords in technical_specs dict values are merged into style field."""
    payload = {
        "query": "black ribbed crew socks",
        "style": "crew",
        "color": "black",
        "technical_specs": {"pattern": "ribbed", "material": "cotton"},
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["style"] == "ribbed crew"
    assert result["technical_specs"] == {"material": "cotton"}


def test_style_ribbbed_not_lost_when_in_technical_specs():
    """'ribbed' cannot disappear from the structured mandate when it appears in technical_specs."""
    payload = {
        "query": "black ribbed crew socks 5-pack",
        "style": "crew",
        "color": "black",
        "pack_quantity": 5,
        "technical_specs": ["ribbed"],
        "max_price": 15,
        "currency": "USD",
        "allowed_conditions": ["new"],
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["style"] == "ribbed crew", f"Expected 'ribbed crew', got {result['style']!r}"


def test_style_canonical_ordering():
    """Style keywords are ordered canonically (ribbed before crew)."""
    payload = {
        "query": "black crew ribbed socks",
        "style": "crew ribbed",
        "color": "black",
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["style"] == "ribbed crew"


def test_style_unchanged_when_no_technical_specs_overlap():
    """Style field is unchanged when technical_specs has no style keywords."""
    payload = {
        "query": "black crew socks",
        "style": "crew",
        "color": "black",
        "technical_specs": ["cotton", "polyester"],
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["style"] == "crew"
    assert result["technical_specs"] == ["cotton", "polyester"]


def test_pack_quantity_preserved_in_parsed_mandate():
    """Pack quantity is preserved from parsed mandate through validation."""
    payload = {
        "query": "5-pack black ribbed crew socks",
        "pack_quantity": 5,
        "pack_unit": "pair",
        "color": "black",
        "style": "ribbed crew",
        "max_price": 15,
        "currency": "USD",
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["pack_quantity"] == 5
    assert result["pack_unit"] == "pair"


# --- Market context (P0.4.1) ---

def test_mandate_schema_market_context_defaults_none():
    """country and language default to None; currency defaults to EUR."""
    m = Mandate(query="black socks")
    d = m.model_dump()
    assert d["country"] is None
    assert d["currency"] == "EUR"
    assert d["language"] is None


def test_mandate_schema_market_context_explicit_values():
    """Explicit market context values are accepted and preserved."""
    m = Mandate(query="black socks", country="US", currency="USD", language="en")
    d = m.model_dump()
    assert d["country"] == "US"
    assert d["currency"] == "USD"
    assert d["language"] == "en"


def test_mandate_schema_currency_not_inferred_from_country():
    """Country does not force currency; they are independent fields."""
    m = Mandate(query="black socks", country="US", currency="EUR")
    assert m.country == "US"
    assert m.currency == "EUR"


def test_parse_intent_prompt_includes_market_context_instruction():
    """Gemini prompt instructs extraction of country/language from explicit text only."""
    _mock_parse_with_payload({"query": "black socks", "country": "US", "currency": "USD", "language": "en"})
    prompt = _mock_parse_with_payload.last_kwargs["contents"]
    assert "country" in prompt
    assert "language" in prompt
    assert "Do not infer country from currency" in prompt


def test_parse_intent_extracts_market_context_from_structured_response():
    """Market context fields survive parsing and validation."""
    payload = {
        "query": "black ribbed crew socks 5-pack",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "style": "ribbed crew",
        "pack_quantity": 5,
        "max_price": 15,
        "allowed_conditions": ["new"],
        "min_specs": {},
    }
    result = _mock_parse_with_payload(payload)
    assert result["country"] == "US"
    assert result["currency"] == "USD"
    assert result["language"] == "en"


def test_legacy_mandate_without_market_context_compatible():
    """Historical sessions without country/language still validate."""
    old = {
        "query": "black socks",
        "max_price": 15,
        "currency": "EUR",
        "allowed_conditions": ["new"],
        "color": "black",
        "min_specs": {},
    }
    m = Mandate.model_validate(old)
    d = m.model_dump()
    assert d["country"] is None
    assert d["language"] is None
    assert d["currency"] == "EUR"
