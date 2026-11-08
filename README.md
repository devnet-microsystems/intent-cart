# INTENT CART

AI-powered shopping prototype that turns a user’s natural-language intent into a reviewed purchasing mandate, evaluates product evidence against explicit constraints, and allows only policy-approved offers to continue to a merchant PayPal Sandbox checkout.

## Status

Local single-user prototype for hackathon demonstration. It is not production-ready and does not provide authentication or multi-user ownership isolation.

Live results depend on configured integrations and on the evidence/offers returned for a given search. Gemini, Channel3, and PayPal require local credentials that are not included in this repository.

## Architecture

- **Frontend:** React 19 + Vite. The UI includes intent input, mandate review, AG Grid-based product evaluation, status display, and a PayPal checkout button for approved offers.
- **Backend:** FastAPI (Python). The backend owns mandate/session state, search orchestration, policy evaluation, and PayPal order/capture endpoints.
- **Intent extraction:** Gemini (`google-genai`) parses natural language into a structured purchasing mandate. Gemini output is untrusted input to the system; it is not authoritative.
- **Mandate review and server-side locking:** The user reviews the extracted mandate before search. The backend validates mandatory constraints and stores a locked mandate for the session.
- **Product discovery:** Channel3 provides product and offer discovery. Discovery results and merchant offers may be incomplete, ambiguous, or unavailable.
- **Laptop specification normalization:** `backend/laptop_normalizer.py` deterministically extracts laptop evidence (form/classification, RAM, storage, storage interface, processor family, operating system) with per-field provenance and verification status. Missing, ambiguous, or conflicting values are treated as not verified.
- **Evidence provenance:** Policy evaluation can expose normalized laptop specs and raw-spec provenance so decisions can be traced to available evidence rather than inferred claims.
- **Deterministic fail-closed Policy Gate:** `backend/policy_engine.py` (policy version 2.0) evaluates each offer against the locked mandate. Required constraints must be verified and satisfied for approval. Missing, ambiguous, conflicting, or unsupported required evidence does not produce approval.
- **Checkout:** The frontend loads the PayPal JavaScript SDK and renders a PayPal button only for approved, locked selections. The backend creates and captures PayPal Sandbox orders for the INTENT CART merchant flow.

## Laptop mandate support

The current laptop vertical supports deterministic handling for laptop-oriented constraints when the mandate is identified as a laptop request, including:

- laptop product classification (notebook vs desktop/accessory evidence);
- minimum RAM and minimum storage;
- required storage interface (for example SSD/NVMe-related evidence when present and verifiable);
- required processor constraints with existing hierarchy/conflict handling (GPU evidence is not treated as CPU proof);
- required operating system with fail-closed compatibility checks.

A product title containing “laptop” is not sufficient for approval. Approval requires verifiable evidence for required laptop constraints. Generic OS requirements accept explicitly verified compatible editions where implemented; ambiguous or conflicting OS evidence remains unverified.

This repository does **not** claim complete coverage for all technology categories, all laptop attributes, or AI-subscription purchasing.

## Policy decisions

The Policy Gate returns one of:

- **APPROVED** — all required constraints are verified and satisfied for the evaluated offer.
- **REJECTED** — at least one required constraint is verified and violated.
- **NEEDS_CONFIRMATION** — required evidence is missing, ambiguous, conflicting, unsupported, or otherwise not verified, and there is no verified required violation that forces rejection.

The gate is fail-closed: uncertain required evidence does not become approval. The deterministic Policy Gate controls decisions; model output cannot override it.

## Evidence provenance and uncertainty

- Missing or ambiguous required evidence is treated as not verified.
- Conflicting evidence is preserved and does not produce approval.
- Only verified numeric laptop RAM/storage evidence is exposed to generic minimum-spec comparison where applicable.
- Approval counts vary with live catalog data, available offers, and the quality of evidence returned by upstream sources.

## PayPal Sandbox checkout

The PayPal integration demonstrates the INTENT CART merchant Sandbox checkout flow:

1. A user searches and locks a mandate.
2. The Policy Gate evaluates discovered offers.
3. Only an approved offer can proceed to PayPal checkout.
4. The backend creates a PayPal Sandbox order for the selected approved offer.
5. The buyer approves in PayPal Sandbox UI, and the backend captures the payment.

Important limitations:

- This is **Sandbox-only** merchant checkout for INTENT CART.
- A captured Sandbox payment demonstrates the INTENT CART payment flow; it does **not** automatically place, purchase, or fulfill an order with the retailer or supplier returned by Channel3.
- Channel3 discovery and merchant offers are separate from the actual PayPal merchant payment in this prototype.

## Local development

### Prerequisites

- Python 3.11+ (project dependencies are installed in a local virtual environment; use the repo’s `.venv` when present)
- Node.js 18+

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Environment variables

Use the placeholder templates and keep real credentials local:

- Root [`.env.example`](.env.example): `PAYPAL_ENVIRONMENT`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `CHANNEL3_API_KEY`, `CHANNEL3_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL`.
- Frontend [`frontend/.env.example`](frontend/.env.example): `VITE_PAYPAL_CLIENT_ID`.

Never commit `.env` files or credentials. The checked-in templates contain placeholders only.

## Tests and build

Run from the repository root unless noted:

```bash
# Backend (recommended: use project virtualenv)
.venv/bin/pytest -q

# Frontend
cd frontend
npm test

# Frontend production build
cd frontend
npm run build
```

## Known limitations

- Local single-user prototype; no authentication, sessions tied to browser/backend state are not a multi-user security boundary.
- External retailer/supplier ordering and fulfillment are not implemented.
- Live catalog coverage, product evidence, and approval rates vary by search and upstream data quality.
- Gemini extraction can be incomplete or inconsistent; the Policy Gate remains deterministic and fail-closed.
- Product availability and prices may change between discovery and checkout.
- The frontend build currently emits a chunk-size warning for the main bundle; this does not affect the documented local build command.
- Local SQLite databases and execution records are development artifacts and must not be treated as release assets.

## Evidence-first policy

INTENT CART prefers rejecting or requiring confirmation for unverifiable offers over approving a purchase that may violate the user’s mandate. AI-generated interpretations never constitute proof of compliance.
