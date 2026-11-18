# Deploying InvoiceIQ (₹0 on free tiers)

```
Browser ──► Vercel (Next.js) ──API key──► Caddy ─► FastAPI ──► Neon Postgres (+ pgvector, checkpoints)
                                          │    └─► Gemini / Groq
             Gmail ◄──► n8n ◄─────────────┘  (same VM, HMAC-signed webhooks both ways)
```

| Piece | Where | Free tier |
|---|---|---|
| Frontend (Next.js 16) | Vercel, Hobby | yes |
| API (FastAPI + LangGraph) + n8n + Caddy | Oracle Cloud **Always Free** Ampere A1 VM (arm64, up to 4 OCPU / 24 GB) | yes |
| Database, vectors, checkpoints | Neon (already in use) | yes |
| LLMs | Gemini, Groq | free tiers, rate-limited |
| Embeddings | FastEmbed, baked into the API image | local |

> **Status:** these files are written and reviewed, but have **not been built or deployed yet**:
> Docker isn't installed on the development machine, and the plan says to ask before
> deploying. The first `docker compose up --build` on the VM is also the first build. Fix
> anything it reports before pointing DNS at it.

Everything below is the procedure; nothing here runs on its own.

## 0. Before you start

- A domain (or subdomains) you control, e.g. `api.example.in` and `n8n.example.in`. A `.in` costs about ₹200
  for the first year. Without a domain, use the VM's IP with a free `sslip.io` name
  (`api.<ip-with-dashes>.sslip.io`); Caddy can still get certificates for it.
- The Neon project already used in development (or a new branch of it for production).
- Fresh secrets for production. Don't reuse the development ones:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"   # API_KEY
  python -c "import secrets; print(secrets.token_urlsafe(32))"   # N8N_WEBHOOK_SECRET
  python -c "import secrets; print(secrets.token_urlsafe(32))"   # N8N_ENCRYPTION_KEY
  ```

## 1. Database (Neon)

The schema and data are created by the app itself:

```bash
# from your machine, with DATABASE_URL pointing at the production Neon database
cd backend
uv run alembic upgrade head            # tables + pgvector (the API container also runs this on start)
uv run python -m scripts.seed          # vendors, POs, GRNs, invoice history
uv run python -m scripts.ingest_knowledge   # index data/knowledge into pgvector
```

Use the **direct** (non-pooled) connection string: LangGraph's checkpointer uses psycopg with
prepared statements disabled, and the pooled endpoint adds nothing here.

## 2. The VM (Oracle Cloud Always Free)

1. Create an **Ampere A1** instance (Ubuntu 24.04, arm64). 2 OCPU / 12 GB is plenty; the
   embedding model and n8n fit comfortably.
2. Networking: in the VCN security list (and `ufw`, if enabled) open **80** and **443** (TCP,
   plus UDP 443 for HTTP/3). Leave 8000 and 5678 closed: only Caddy is exposed.
3. Install Docker Engine and the Compose plugin (Docker's apt repository for Ubuntu, arm64).
4. DNS: point `API_DOMAIN` and `N8N_DOMAIN` (A records) at the VM's public IP.

## 3. Configure and start

```bash
git clone <your repo> invoiceiq && cd invoiceiq/deploy
cp .env.example .env          # domains, ACME email, N8N_VERSION (pin a stable release), N8N_ENCRYPTION_KEY
cp api.env.example api.env    # DATABASE_URL, LLM keys, API_KEY, FRONTEND_ORIGIN/APP_URL, N8N_*
docker compose up -d --build
docker compose logs -f api    # "Application startup complete" after migrations
curl https://$API_DOMAIN/health
```

`deploy/.env` and `deploy/api.env` are git-ignored, so keep a copy somewhere safe.

What the stack does:
- **api**: the image from `backend/Dockerfile` (python 3.12-slim, multi-arch, FastEmbed model
  baked in, runs as a non-root user). On start it runs `alembic upgrade head`, then uvicorn. It
  recovers runs that were in progress (LangGraph checkpoints in Neon). Uploads and evidence
  bundles go to the `invoiceiq-data` volume.
- **n8n**: self-hosted, SQLite in the `n8n-data` volume. Credentials are encrypted with
  `N8N_ENCRYPTION_KEY`.
- **caddy**: HTTPS certificates from Let's Encrypt, and SSE streamed unbuffered
  (`flush_interval -1`), so the live run view and Ask AP work through it.

## 4. Frontend (Vercel)

1. Import the repository in Vercel and set **Root Directory** to `frontend`. The framework is
   detected as Next.js and pnpm from the lockfile.
2. Environment variables (Production), both **server-side** (never `NEXT_PUBLIC_`):
   - `API_URL` = `https://<API_DOMAIN>`
   - `API_KEY` = the same value as `API_KEY` in `deploy/api.env`
   - `SITE_PASSWORD` = a password for visitors (recommended: the app has no user
     accounts yet, so without it anyone with the link can approve cases or reset the demo)
3. Deploy. Then set `FRONTEND_ORIGIN` and `APP_URL` in `deploy/api.env` to the Vercel URL and
   `docker compose up -d api` to apply them.

The browser only ever talks to Vercel. The `/api/backend` proxy adds the API key server-side
and streams SSE through.

## 5. n8n

Open `https://<N8N_DOMAIN>`, create the owner account, then follow [../n8n/README.md](../n8n/README.md):
- Generate pre-filled workflows with the **production** API URL:
  `uv run python -m scripts.n8n_prefill https://<API_DOMAIN> --email <gmail>` (it reads
  `API_KEY` and `N8N_WEBHOOK_SECRET` from `backend/.env`, so run it with the production values
  there or edit the Config nodes after importing).
- Import the four files, connect Gmail (OAuth inside n8n), and activate them.
- `N8N_BASE_URL=https://<N8N_DOMAIN>` in `deploy/api.env`, then restart the API.

Self-hosting n8n here replaces the n8n cloud trial and the Cloudflare quick tunnel: the URLs
stay fixed.

## 6. Smoke test

1. `https://<vercel-app>/dashboard` loads (the proxy reaches the API with the key).
2. **New run → Try a sample**: the live timeline streams; the case reaches review in about 20–40 s.
3. **Ask AP**: "Why was the TechNova invoice flagged?" gives an answer with citations.
4. Email a PDF to the n8n Gmail inbox: a case appears, and the reviewer email links to it.
5. **Settings → Integrations** shows n8n configured and the API key set.

## Operating it

- **Update:** `git pull && docker compose up -d --build` (migrations run on start).
- **Logs:** `docker compose logs -f api n8n caddy`.
- **Backups:** Neon keeps point-in-time history. Back up the `n8n-data` and `invoiceiq-data`
  volumes (`docker run --rm -v invoiceiq_n8n-data:/d -v $PWD:/b alpine tar czf /b/n8n.tgz -C /d .`).
- **Demo reset:** Settings → Reset demo (deletes runs and records; keeps procedure versions).
- **Costs:** all free tiers. Gemini and Groq free tiers are rate-limited. Under load, transient
  503/429 errors can end a case as `FAILED` (it can be re-run; automatic retries are on the
  "what's next" list).
- **Security:** only 80/443 are open. The API requires `API_KEY` (and refuses tunnelled
  requests without one). The intake webhook is HMAC-signed with a 5-minute window. CORS is
  locked to the Vercel origin.

## Not done here (ask first)

Creating the Oracle VM, the Vercel project, DNS records, and running the first build are
deliberately left for you to approve and do. Each touches an account of yours.
