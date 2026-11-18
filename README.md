# InvoiceIQ

Agentic AI invoice processing for a fictional Indian retailer (Acme Retail Pvt Ltd, Bengaluru):
a vendor invoice PDF goes in, and a reasoned, auditable decision comes out —
`APPROVED`, `NEEDS_REVIEW`, `ON_HOLD` or `REJECTED`.

> **The AI extracts and retrieves; the rules decide; a human approves the uncertain cases.**

Stack: Next.js 16 · FastAPI · LangGraph · LangChain · Neon Postgres + pgvector · n8n.
Full README (architecture, LangGraph diagram, eval results) lands in Phase 12.

## Status

- [x] Phase 1 — Foundation: app shell, FastAPI, Neon + Alembic, seed data, `/reference`
- [x] Phase 2 — Test invoices & knowledge base: 25 PDFs (4 layouts, 3 scans) with ground truth, AP policy, GST rules, vendor FAQ, 10 contracts
- [x] Phase 3 — Extraction: Gemini (text + vision) with Groq/Gemini-lite fallbacks, per-field evidence and calibrated confidence
- [ ] Phases 4–12 — see the build plan

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
uv run python -m scripts.ingest_knowledge   # indexes data/knowledge/ into PGVector
# --loop: psycopg (LangGraph's Postgres checkpointer) needs a selector event loop on Windows
uv run uvicorn app.main:app --reload --port 8000 --loop app.loop:selector_loop_factory
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

Regenerate test data (deterministic; see `data/test-invoices/README.md`):

```bash
cd backend && uv run python -m scripts.build_seed_data && uv run python -m scripts.generate_invoices
```

Extraction against the test invoices (needs `GOOGLE_API_KEY`; `--replay` uses recorded outputs, no LLM):

```bash
cd backend && uv run python -m scripts.eval_extraction            # live, prints field accuracy
cd backend && uv run python -m scripts.eval_extraction --record   # refresh tests/fixtures/extraction
```

Regenerate the typed API client after backend changes:

```bash
cd backend && uv run python -m scripts.export_openapi && cd ../frontend && pnpm gen:api
```

## Layout

```
backend/   FastAPI app (app/), Alembic migrations, scripts/, tests/
frontend/  Next.js App Router UI
data/      seed/ reference data, knowledge/ RAG corpus, test-invoices/ PDFs + ground truth
docs/      ASSUMPTIONS.md, later ARCHITECTURE.md and EVAL_RESULTS.md
n8n/       workflow exports (Phase 10)
deploy/    deploy config (Phase 12)
```
