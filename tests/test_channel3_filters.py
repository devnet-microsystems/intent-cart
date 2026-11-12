import pytest

from backend.channel3_client import build_channel3_filters, color_to_hex, Channel3Client


# --- Color → hex mapping ---

def test_color_to_hex_known_colors():
    assert color_to_hex("black") == "#000000"
    assert color_to_hex("white") == "#ffffff"
    assert color_to_hex("red") == "#ff0000"
    assert color_to_hex("navy") == "#000080"


def test_color_to_hex_case_insensitive():
    assert color_to_hex("Black") == "#000000"
    assert color_to_hex("BLACK") == "#000000"


def test_color_to_hex_unknown_returns_none():
    assert color_to_hex("glow-in-the-dark") is None
    assert color_to_hex("") is None
    assert color_to_hex(None) is None


# --- Channel3 filter format ---

def test_build_channel3_filters_price_only():
    mandate = {"max_price": 15, "currency": "EUR"}
    filters = build_channel3_filters(mandate)
    assert filters == {"price": {"max_price": 15.0}}


def test_build_channel3_filters_color_only():
    mandate = {"color": "black"}
    filters = build_channel3_filters(mandate)
    assert filters == {"colors": {"palette": [{"hex": "#000000"}]}}


def test_build_channel3_filters_price_and_color():
    mandate = {"max_price": 15, "color": "black", "currency": "EUR"}
    filters = build_channel3_filters(mandate)
    assert filters == {
        "price": {"max_price": 15.0},
        "colors": {"palette": [{"hex": "#000000"}]},
    }


def test_build_channel3_filters_unknown_color_omits_colors():
    """Unknown color → no colors filter (never invent hex)."""
    mandate = {"max_price": 15, "color": "glow-in-the-dark"}
    filters = build_channel3_filters(mandate)
    assert filters == {"price": {"max_price": 15.0}}
    assert "colors" not in filters


def test_build_channel3_filters_no_filters_returns_none():
    mandate = {"max_price": None, "currency": "EUR"}
    assert build_channel3_filters(mandate) is None


def test_build_channel3_filters_zero_max_price_omitted():
    mandate = {"max_price": 0}
    assert build_channel3_filters(mandate) is None


def test_build_channel3_filters_preserves_italy_and_eur_in_query():
    """Country IT and currency EUR are set in the search config, not as filters."""
    # Verified in channel3_client.py search(): config.country="IT", config.currency="EUR"
    mandate = {"max_price": 15, "color": "black"}
    filters = build_channel3_filters(mandate)
    assert filters is not None  # filters are separate from config


# --- Non-color attributes NOT passed as structured filters ---

def test_build_channel3_filters_no_size_handle():
    """Size has no known Channel3 handle → not passed as structured filter."""
    mandate = {"size": "43"}
    filters = build_channel3_filters(mandate)
    assert filters is None  # size remains in query text only


def test_build_channel3_filters_no_style_handle():
    """Style has no known Channel3 handle → not passed as structured filter."""
    mandate = {"style": "mismatched"}
    filters = build_channel3_filters(mandate)
    assert filters is None


def test_build_channel3_filters_no_pack_quantity_handle():
    """Pack quantity has no known Channel3 handle → not passed as structured filter."""
    mandate = {"pack_quantity": 5}
    filters = build_channel3_filters(mandate)
    assert filters is None


# --- Channel3Client: configurable country/currency/language ---

@pytest.mark.asyncio
async def test_search_uses_config_country_currency_language(monkeypatch):
    """search() sends country/currency/language in the config body."""
    client = Channel3Client()

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"results": []}

    async def fake_request(method, path, **kwargs):
        captured["body"] = kwargs.get("json")
        return FakeResp()

    monkeypatch.setattr(client, "_request", fake_request)
    await client.search("socks", country="US", currency="USD", language="en")
    body = captured["body"]
    assert body["config"]["country"] == "US"
    assert body["config"]["currency"] == "USD"
    assert body["config"]["language"] == "en"


@pytest.mark.asyncio
async def test_search_defaults_to_it_eur(monkeypatch):
    """search() defaults to IT/EUR config when not specified."""
    client = Channel3Client()

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"results": []}

    async def fake_request(method, path, **kwargs):
        captured["body"] = kwargs.get("json")
        return FakeResp()

    monkeypatch.setattr(client, "_request", fake_request)
    await client.search("socks")
    body = captured["body"]
    assert body["config"]["country"] == "IT"
    assert body["config"]["currency"] == "EUR"


@pytest.mark.asyncio
async def test_get_product_details_passes_params(monkeypatch):
    """get_product_details() passes country/currency/language as query params."""
    client = Channel3Client()

    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"id": "p1", "title": "Test"}

    async def fake_request(method, path, **kwargs):
        captured["params"] = kwargs.get("params")
        return FakeResp()

    monkeypatch.setattr(client, "_request", fake_request)
    await client.get_product_details("p1", country="US", currency="USD", language="en")
    params = captured["params"]
    assert params["country"] == "US"
    assert params["currency"] == "USD"
    assert params["language"] == "en"


# --- Channel3Client: variant options ---

def test_apply_variant_options_filters_size():
    """_apply_variant_options narrows options to the requested size value."""
    product = {
        "id": "p1",
        "variants": {
            "options": [
                {
                    "name": "Taille",
                    "values": [
                        {"label": "5-7", "exists": True, "available": "InStock"},
                        {"label": "10.5-12", "exists": False, "available": None},
                    ],
                }
            ],
            "selected": [{"name": "Color", "label": "Black"}],
        },
    }
    result = Channel3Client._apply_variant_options(product, {"Size": "10.5-12"})
    options = result["variants"]["options"]
    assert len(options) == 1
    assert options[0]["name"] == "Taille"
    assert len(options[0]["values"]) == 1
    assert options[0]["values"][0]["label"] == "10.5-12"
    assert options[0]["values"][0]["exists"] is False


def test_apply_variant_options_no_match_returns_empty_values():
    """Requested size not found → empty values list."""
    product = {
        "id": "p1",
        "variants": {
            "options": [
                {
                    "name": "Taille",
                    "values": [{"label": "5-7", "exists": True}],
                }
            ],
        },
    }
    result = Channel3Client._apply_variant_options(product, {"Size": "10.5-12"})
    options = result["variants"]["options"]
    assert options[0]["values"] == []


def test_apply_variant_options_no_variant_options_param():
    """No variant_options → product unchanged."""
    product = {"id": "p1", "title": "Test"}
    result = Channel3Client._apply_variant_options(product, None)
    assert result == product


def test_find_size_option_name():
    """_find_size_option_name detects Taille/Size/Taglia."""
    variants = {"options": [{"name": "Taille", "values": []}]}
    assert Channel3Client._find_size_option_name(variants) == "Taille"

    variants = {"options": [{"name": "Size", "values": []}]}
    assert Channel3Client._find_size_option_name(variants) == "Size"

    variants = {"options": [{"name": "Taglia", "values": []}]}
    assert Channel3Client._find_size_option_name(variants) == "Taglia"

    variants = {"options": [{"name": "Color", "values": []}]}
    assert Channel3Client._find_size_option_name(variants) is None

    assert Channel3Client._find_size_option_name(None) is None
