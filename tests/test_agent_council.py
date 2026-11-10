import json
import logging

import pytest

from backend.agent_council import AgentCouncil, ALL_AGENTS

ALLOWED_STATUSES = {
    "NO_CONCERN_FOUND",
    "CONCERN",
    "INSUFFICIENT_DATA",
    "UNAVAILABLE",
}

INTENT = "intent_auditor"
EVIDENCE = "evidence_analyst"
RED_TEAM = "red_team_auditor"


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeGenerateContent:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_content(self, model=None, contents=None, config=None):
        self.calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
                "system_instruction": getattr(config, "system_instruction", None),
            }
        )
        if not self.responses:
            raise RuntimeError("nessuna risposta simulata disponibile")
        payload = self.responses[(len(self.calls) - 1) % len(self.responses)]
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(json.dumps(payload))


class _FakeModels:
    def __init__(self, generate_content):
        self.generate_content = generate_content


class _FakeAio:
    def __init__(self, generate_content):
        self.models = _FakeModels(generate_content)


class _FakeClient:
    def __init__(self, responses):
        self.aio = _FakeAio(_FakeGenerateContent(responses))


MANDATE = {
    "query": "notebook aziendale 16 GB RAM, materiale alluminio, colore grigio",
    "max_price": 1200,
    "currency": "EUR",
    "min_ram_gb": 16,
    "product_type": "laptop",
    "color": "grigio",
    # 'material' omesso volontariamente
    "allowed_conditions": ["new"],
}

PRODUCT = {
    "id": "P1",
    "title": "Notebook Business",
    "condition": "new",
    "materials": ["alluminio"],
    "structured_attributes": {"color": "grigio", "material": "alluminio"},
    "specs": {"ram_gb": 16, "storage_gb": 512},
    "variants": [{"color": "grigio", "size": "14\""}],
    "category": {"slug": "laptops"},
}

OFFER = {
    "offer_id": "O1",
    "price": {"price": "1100", "currency": "EUR"},
    "condition": "new",
}


def _responses(*payloads):
    return list(payloads)


@pytest.fixture
def council(monkeypatch):
    """AgentCouncil con client Gemini finto; nessuna credenziale reale usata."""
    fake = _FakeClient([])
    monkeypatch.setattr("backend.agent_council.genai.Client", lambda **kw: fake)
    return AgentCouncil(), fake


# --------------------------------------------------------------- test agenti

@pytest.mark.asyncio
async def test_intent_auditor_trova_constraint_omesso(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        {
            "status": "CONCERN",
            "findings": [
                {
                    "severity": "HIGH",
                    "requirement": "material",
                    "source_ref": "user_request",
                    "message": "L'utente richiede materiale alluminio ma il mandato non lo vincola",
                }
            ],
        }
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    ia = result["agents"][INTENT]
    assert ia["status"] == "CONCERN"
    assert len(ia["findings"]) == 1
    assert ia["findings"][0]["requirement"] == "material"
    assert ia["findings"][0]["severity"] == "HIGH"


@pytest.mark.asyncio
async def test_evidence_analyst_trova_evidenza_mancante(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        {
            "status": "CONCERN",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "requirement": "max_price",
                    "source_ref": "offer.price",
                    "message": "Prezzo offerta non esplicitato in modo verificabile",
                }
            ],
        }
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    ea = result["agents"][EVIDENCE]
    assert ea["status"] == "CONCERN"
    assert ea["findings"][0]["source_ref"] == "offer.price"


@pytest.mark.asyncio
async def test_red_team_trova_contraddizione(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        {
            "status": "CONCERN",
            "findings": [
                {
                    "severity": "HIGH",
                    "requirement": "color",
                    "source_ref": "product.description",
                    "message": "Contraddizione tra colore dichiarato e descrizione merchant",
                }
            ],
        }
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    rt = result["agents"][RED_TEAM]
    assert rt["status"] == "CONCERN"
    assert "Contraddizione" in rt["findings"][0]["message"]


@pytest.mark.asyncio
async def test_tre_chiamate_modello_separate(council):
    agent, fake = council
    payload = {"status": "NO_CONCERN_FOUND", "findings": []}
    fake.aio.models.generate_content.responses = _responses(payload, payload, payload)
    await agent.run(MANDATE, PRODUCT, OFFER)
    gc = fake.aio.models.generate_content
    assert len(gc.calls) == 3, "devono esserci 3 invocazioni modello separate"
    systems = [c["system_instruction"] for c in gc.calls]
    assert len(set(systems)) == 3, "ci si aspetta 3 system prompt distinti"


@pytest.mark.asyncio
async def test_agente_fallito_unavailable_non_blocca_altri(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        RuntimeError("down"),
        {"status": "CONCERN", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    a = result["agents"]
    assert a[INTENT]["status"] == "UNAVAILABLE"
    assert a[EVIDENCE]["status"] == "CONCERN"
    assert a[RED_TEAM]["status"] == "NO_CONCERN_FOUND"
    assert a["summary"]["unavailable"] == 1


@pytest.mark.asyncio
async def test_risposta_malformata_diventa_unavailable(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses("non è json {{{")
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    assert result["agents"][INTENT]["status"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_output_schema_validato(council):
    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        {
            "status": "CONCERN",
            "findings": [
                {
                    "severity": "HIGH",
                    "requirement": "size",
                    "source_ref": "product.size",
                    "message": "taglia non presente nei dati",
                }
            ],
        },
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "INSUFFICIENT_DATA", "findings": []},
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    assert set(result.keys()) == {"agents"}
    assert set(result["agents"].keys()) == {INTENT, EVIDENCE, RED_TEAM, "summary"}
    for key in (INTENT, EVIDENCE, RED_TEAM):
        blob = result["agents"][key]
        assert set(blob.keys()) == {"status", "findings"}
        assert blob["status"] in ALLOWED_STATUSES
        for f in blob["findings"]:
            assert set(f.keys()) == {"severity", "requirement", "source_ref", "message"}
            assert f["severity"] in {"HIGH", "MEDIUM", "LOW"}


@pytest.mark.asyncio
async def test_max_5_finding_per_agente(council):
    agent, fake = council
    findings = [
        {
            "severity": "LOW",
            "requirement": f"req{i}",
            "source_ref": f"ref{i}",
            "message": f"msg{i}",
        }
        for i in range(8)
    ]
    fake.aio.models.generate_content.responses = _responses(
        {"status": "CONCERN", "findings": findings},
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    )
    result = await agent.run(MANDATE, PRODUCT, OFFER)
    assert len(result["agents"][INTENT]["findings"]) == 5


# ------------------------------------------------------------ nessuna credenziale

@pytest.mark.asyncio
async def test_nessuna_credenziale_stampata(council, caplog):
    from backend.config import settings

    agent, fake = council
    fake.aio.models.generate_content.responses = _responses(
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
        {"status": "NO_CONCERN_FOUND", "findings": []},
    )
    with caplog.at_level(logging.WARNING):
        await agent.run(MANDATE, PRODUCT, OFFER)
    assert settings.gemini_api_key not in caplog.text
    for rec in caplog.records:
        assert settings.gemini_api_key not in rec.getMessage()


# ------------------------------------------------------- concurrency dimostrabile

@pytest.mark.asyncio
async def test_tre_invocazioni_modello_in_flight_sovrapposte(council):
    """Prove three model invocations overlap temporally using an asyncio.Event barrier.

    Each fake `generate_content` records its start time and waits on a shared
    barrier until all three have started. If the calls were sequential, the
    barrier would deadlock; with true concurrency all three start before any
    completes and the assertion on temporal overlap passes.
    """
    import asyncio
    import json as _json

    agent, fake = council
    gate = asyncio.Event()
    lock = asyncio.Lock()
    started = 0
    start_times = {}
    end_times = {}
    call_idx = 0

    async def instrumented(model=None, contents=None, config=None):
        nonlocal started, call_idx
        idx = call_idx
        call_idx += 1
        async with lock:
            started += 1
        start_times[idx] = asyncio.get_event_loop().time()
        # Barrier: all three calls must have started before any proceeds.
        if started < 3:
            await gate.wait()
        else:
            gate.set()
        # Simulate non-blocking work.
        await asyncio.sleep(0.01)
        end_times[idx] = asyncio.get_event_loop().time()
        return _FakeResponse(
            _json.dumps({"status": "NO_CONCERN_FOUND", "findings": []})
        )

    # Replace the fake `generate_content` coroutine with the instrumented one.
    fake.aio.models.generate_content.generate_content = instrumented

    await agent.run(MANDATE, PRODUCT, OFFER)

    assert len(start_times) == 3, "devono essersi avviate tre invocazioni"
    assert len(end_times) == 3, "devono essersi completate tre invocazioni"

    # Temporal overlap: latest start < earliest end means all three are
    # simultaneously in flight at some instant (true concurrency, not sequential).
    assert max(start_times.values()) < min(end_times.values()), (
        f"le 3 chiamate non si sovrappongono temporalmente "
        f"(max(start)={max(start_times.values()):.6f} >= "
        f"min(end)={min(end_times.values()):.6f})"
    )
