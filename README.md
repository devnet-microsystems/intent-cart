# INTENT CART

Your intent. Your money. Your rules.

AI-powered shopping agent with strict user-defined purchasing constraints.

## Architecture

AI proposes. Channel3 discovers. Deterministic policy gate decides. PayPal executes only after explicit user approval.

## Stack

- Backend: FastAPI (Python)
- Frontend: React + Vite + AG Grid Community
- Persistence: SQLite
- AI: Gemini (google-genai)
- Product discovery: Channel3
- Payments: PayPal Sandbox

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with credentials.

Backend:
```bash
uvicorn backend.main:app --reload
```

Frontend:
```bash
cd frontend && npm install && npm run dev
```

Tests:
```bash
.venv/bin/python -m pytest -q
cd frontend && npx vitest run
cd frontend && npm run build
```

## Security

- Secrets server-side only.
- `.env` ignored by Git.
- Deterministic fail-closed policy gate.
- No automatic payments.

## Limitations

- Browser visual verification pending.
- Live integrations require credentials.
- Sandbox payments only.
