# InvoiceIQ

Agentic AI invoice processing for a fictional Indian retailer (Acme Retail Pvt Ltd, Bengaluru):
a vendor invoice PDF goes in, and a reasoned, auditable decision comes out —
`APPROVED`, `NEEDS_REVIEW`, `ON_HOLD` or `REJECTED`.

> **The AI extracts and retrieves; the rules decide; a human approves the uncertain cases.**

Stack: Next.js 16 · FastAPI · LangGraph · LangChain · Neon Postgres + pgvector · n8n.
Full README (architecture, LangGraph diagram, eval results) lands in Phase 12.

## Status

- [x] Phase 1 — Foundation: app shell, FastAPI, Neon + Alembic, seed data, `/reference`
- [ ] Phase 2 — Test invoices & knowledge base
- [ ] Phases 3–12 — see the build plan

## Run it

Prerequisites: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+ with pnpm, and a
[Neon](https://neon.tech) project (free).

```bash
# backend
cd backend
cp .env.example .env            # paste Neon's direct connection string into DATABASE_URL
uv sync
uv run alembic upgrade head     # creates tables + enables pgvector
uv run python -m scripts.seed   # loads data/seed/*.json
uv run uvicorn app.main:app --reload --port 8000
```

```bash
# frontend
cd frontend
cp .env.example .env.local
pnpm install
pnpm dev                        # http://localhost:3000/reference
```

On Windows, if compiled packages fail to import from a long path, set
`UV_PROJECT_ENVIRONMENT` to a short folder (see `docs/ASSUMPTIONS.md`).

## Checks

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app scripts tests && uv run pytest
cd frontend && pnpm lint && pnpm typecheck && pnpm build
```

Regenerate the typed API client after backend changes:

```bash
cd backend && uv run python -m scripts.export_openapi && cd ../frontend && pnpm gen:api
```

## Layout

```
backend/   FastAPI app (app/), Alembic migrations, scripts/, tests/
frontend/  Next.js App Router UI
data/      seed data (and, from Phase 2, knowledge base + test invoices)
docs/      ASSUMPTIONS.md, later ARCHITECTURE.md and EVAL_RESULTS.md
n8n/       workflow exports (Phase 10)
deploy/    deploy config (Phase 12)
```
