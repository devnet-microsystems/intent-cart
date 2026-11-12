import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_structured_filters_persisted(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {
        "query": "men's mismatched socks",
        "category": "clothing_accessories",
        "structured_filters": {
            "product_type": "socks",
            "size": "43",
            "material": "cotton",
            "material_percentage": 100,
            "style": "mismatched",
            "forbidden_designs": ["Christmas", "snowmen"],
            "max_price": None,
            "currency": "EUR",
        },
    }
    res = client.post("/api/intent", json=payload)
    assert res.status_code == 200
    data = res.json()
    sid = data["session_id"]
    from backend.main import sessions

    assert sessions[sid]["category"] == "clothing_accessories"
    assert sessions[sid]["structured_filters"]["max_price"] is None
    assert sessions[sid]["mandate"]["material"] == "cotton"
    assert sessions[sid]["mandate"]["size"] == "43"


def test_no_inheritance(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    r1 = client.post("/api/intent", json={"query": "a", "category": "clothing_accessories", "structured_filters": {"material": "cotton"}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions

    assert sessions[sid2].get("structured_filters", {}) == {}


def test_free_text_still_works(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "max_price": None})
    res = client.post("/api/intent", json={"text": "hello"})
    assert res.status_code == 200
    assert "session_id" in res.json()

def test_merge_override_material(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "material": "polyester", "size": "42", "material_percentage": 50, "style": "plain", "forbidden_designs": ["logos"]})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "material": "cotton", "size": "43", "material_percentage": 100, "style": "mismatched", "forbidden_designs": ["Christmas", "snowmen"], "max_price": None}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["material"] == "cotton"
    assert m["size"] == "43"
    assert m["material_percentage"] == 100
    assert m["style"] == "mismatched"
    assert m["forbidden_designs"] == ["Christmas", "snowmen"]
    assert m["max_price"] is None
    assert res.json()["mandate"] == m


def test_merge_supplement(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text, "currency": "EUR"})
    payload = {"query": "socks", "structured_filters": {"material": "cotton"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["material"] == "cotton"
    assert m["currency"] == "EUR"


def test_no_inheritance_merge(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    r1 = client.post("/api/intent", json={"query": "a", "structured_filters": {"material": "cotton"}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions
    assert sessions[sid2]["mandate"].get("material") is None


def test_lock_preserves_structured(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "product_type": "socks", "size": "43", "material": "cotton", "material_percentage": 100, "style": "mismatched",
        "forbidden_designs": ["Christmas"], "max_price": None, "currency": "EUR"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 200
    from backend.main import sessions
    assert sessions[sid]["locked"] is True
    assert sessions[sid]["locked_mandate"]["material"] == "cotton"
    assert sessions[sid]["locked_mandate"]["max_price"] is None


def test_lock_missing_prevents(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "socks"})
    payload = {"query": "socks", "structured_filters": {"material": "cotton"}}
    # force missing size by not providing; but mandate has no size
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    # inject structured filter claiming size present
    from backend.main import sessions
    sessions[sid]["structured_filters"] = {"size": "43"}
    # mandate lacks size -> should fail
    # ensure mandate size None
    sessions[sid]["mandate"]["size"] = None
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 400


def test_constraint_priorities_persist(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "category": "clothing_accessories", "structured_filters": {
        "product_type": "socks", "material": "cotton",
        "constraint_priorities": {"material": "PREFERRED", "size": "REQUIRED"}}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["constraint_priorities"]["material"] == "PREFERRED"
    assert m["constraint_priorities"]["size"] == "REQUIRED"
    assert sessions[sid]["structured_filters"]["constraint_priorities"]["material"] == "PREFERRED"


def test_priorities_survive_lock(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": text})
    payload = {"query": "socks", "structured_filters": {"material": "cotton", "constraint_priorities": {"material": "PREFERRED"}}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")
    from backend.main import sessions
    assert sessions[sid]["locked_mandate"]["constraint_priorities"]["material"] == "PREFERRED"


def test_no_inherit_priorities(client, monkeypatch):
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "text"})
    r1 = client.post("/api/intent", json={"query": "a", "structured_filters": {"constraint_priorities": {"material": "PREFERRED"}}})
    sid1 = r1.json()["session_id"]
    r2 = client.post("/api/intent", json={"query": "b"})
    sid2 = r2.json()["session_id"]
    from backend.main import sessions
    assert sessions[sid2]["mandate"].get("constraint_priorities") is None


# --- P0: Lock completeness check ---

def test_lock_blocks_when_pack_quantity_missing_from_mandate(client, monkeypatch):
    """Lock must fail when text mentions pack quantity but mandate lacks pack_quantity."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "5-pack socks"})
    payload = {"query": "5-pack socks", "structured_filters": {}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 400
    body = lock.json()
    assert "pack_quantity" in body.get("detail", "")


def test_lock_succeeds_when_structured_filters_covered_by_merge(client, monkeypatch):
    """Lock succeeds when structured_filters values are merged into the mandate (even if Gemini's parsed mandate lacked them)."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "black socks"})
    payload = {"query": "black socks", "structured_filters": {"color": "black", "size": "43"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    # merge puts color and size into mandate → lock succeeds
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 200
    data = lock.json()
    assert data["locked"] is True


def test_lock_blocks_when_mandate_tampered_after_merge(client, monkeypatch):
    """Lock must fail if the mandate is altered after the merge (structured_filters value missing from mandate)."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "socks"})
    payload = {"query": "socks", "structured_filters": {"material": "cotton"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    from backend.main import sessions
    # Tamper: remove size from mandate while keeping it in structured_filters
    sessions[sid]["structured_filters"] = {"size": "43"}
    sessions[sid]["mandate"]["size"] = None
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 400
    body = lock.json()
    assert "size" in body.get("detail", "")


def test_lock_succeeds_with_all_constraints_present(client, monkeypatch):
    """Lock succeeds when all structured filters are represented in the mandate."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {"query": "black cotton socks", "color": "black", "size": "43", "material": "cotton"})
    payload = {"query": "black cotton socks", "structured_filters": {"color": "black", "size": "43", "material": "cotton"}}
    res = client.post("/api/intent", json=payload)
    sid = res.json()["session_id"]
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 200
    data = lock.json()
    assert data["locked"] is True


# --- Market context (P0.4.1) ---

def test_market_context_parsed_and_persisted(client, monkeypatch):
    """US/USD/en market context parsed by Gemini and persisted in mandate."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
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
    })
    res = client.post("/api/intent", json={"text": "Find black ribbed crew socks 5-pack, max $15 USD, US market"})
    assert res.status_code == 200
    data = res.json()
    sid = data["session_id"]
    from backend.main import sessions
    m = sessions[sid]["mandate"]
    assert m["country"] == "US"
    assert m["currency"] == "USD"
    assert m["language"] == "en"


def test_market_context_survives_lock(client, monkeypatch):
    """Market context preserved in locked_mandate after lock."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black socks",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": "black socks US USD en"})
    sid = res.json()["session_id"]
    lock = client.post(f"/api/mandate/{sid}/lock")
    assert lock.status_code == 200
    from backend.main import sessions
    lm = sessions[sid]["locked_mandate"]
    assert lm["country"] == "US"
    assert lm["currency"] == "USD"
    assert lm["language"] == "en"


def test_search_reads_market_context_from_locked_mandate(client, monkeypatch):
    """Search endpoint reads country/currency/language from locked mandate, not defaults."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black socks",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": "black socks US USD en"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")

    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    mock_client.get_product_details_batch = AsyncMock(return_value={})
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    client.post(f"/api/search/{sid}")
    # Verify search was called with country=US, currency=USD, language=en
    call_kwargs = mock_client.search.call_args.kwargs
    assert call_kwargs["country"] == "US", f"Expected country=US, got {call_kwargs.get('country')}"
    assert call_kwargs["currency"] == "USD", f"Expected currency=USD, got {call_kwargs.get('currency')}"
    assert call_kwargs["language"] == "en", f"Expected language=en, got {call_kwargs.get('language')}"


def test_search_no_fallback_to_it_for_us_mandate(client, monkeypatch):
    """Explicitly US mandate must not fall back to IT in search."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black socks",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": "black socks US USD en"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")

    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    mock_client.get_product_details_batch = AsyncMock(return_value={})
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    client.post(f"/api/search/{sid}")
    call_kwargs = mock_client.search.call_args.kwargs
    assert call_kwargs["country"] == "US"
    assert call_kwargs["country"] != "IT"


def test_historical_session_without_market_context_uses_defaults(client, monkeypatch):
    """Historical sessions without country/language fall back to defaults in search."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black socks",
        "currency": "EUR",
        "color": "black",
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": "black socks"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")

    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    mock_client.get_product_details_batch = AsyncMock(return_value={})
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    client.post(f"/api/search/{sid}")
    call_kwargs = mock_client.search.call_args.kwargs
    assert call_kwargs["country"] == "IT"
    assert call_kwargs["currency"] == "EUR"
    assert call_kwargs["language"] == "en"


def test_search_query_includes_pack_quantity(client, monkeypatch):
    """Search query is enriched with pack quantity when missing from extracted query."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black ribbed crew socks",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "style": "ribbed crew",
        "pack_quantity": 5,
        "max_price": 15,
        "allowed_conditions": ["new"],
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": "Find black ribbed crew socks in a 5-pack, max $15 USD, US market"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")

    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    mock_client.get_product_details_batch = AsyncMock(return_value={})
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    client.post(f"/api/search/{sid}")
    call_args = mock_client.search.call_args
    # First positional arg is the query
    query = call_args[0][0] if call_args[0] else call_args.kwargs.get("query", "")
    kw = call_args.kwargs
    assert kw["country"] == "US"
    assert kw["currency"] == "USD"
    assert kw["language"] == "en"
    # The enriched query should include 5-pack
    assert "5-pack" in query, f"Expected '5-pack' in query, got: {query}"


def test_search_query_does_not_duplicate_pack_quantity(client, monkeypatch):
    """Search query is not duplicated with pack quantity when already present."""
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
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
    })
    res = client.post("/api/intent", json={"text": "Find black ribbed crew socks in a 5-pack, max $15 USD, US market"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")

    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    mock_client.get_product_details_batch = AsyncMock(return_value={})
    monkeypatch.setattr("backend.main.Channel3Client", lambda: mock_client)

    client.post(f"/api/search/{sid}")
    call_args = mock_client.search.call_args
    query = call_args[0][0] if call_args[0] else call_args.kwargs.get("query", "")
    # Query should appear once, not duplicated
    assert query.count("5-pack") == 1, f"Query should not duplicate '5-pack': {query}"


def test_original_request_matches_locked_mandate(client, monkeypatch):
    """Original user request is preserved in locked_mandate._original_request."""
    user_text = "Find new black ribbed crew socks in a 5-pack, maximum budget $15 USD, for delivery in the United States."
    monkeypatch.setattr("backend.main.parse_intent", lambda text: {
        "query": "black ribbed crew socks",
        "country": "US",
        "currency": "USD",
        "language": "en",
        "color": "black",
        "style": "ribbed crew",
        "pack_quantity": 5,
        "max_price": 15,
        "allowed_conditions": ["new"],
        "min_specs": {},
    })
    res = client.post("/api/intent", json={"text": user_text, "category": "clothing_accessories"})
    sid = res.json()["session_id"]
    client.post(f"/api/mandate/{sid}/lock")
    from backend.main import sessions
    lm = sessions[sid]["locked_mandate"]
    assert lm["_original_request"] == user_text
