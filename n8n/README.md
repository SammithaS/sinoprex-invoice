# n8n automations (plan §12)

Four workflows, exported in `workflows/`, for **n8n cloud** with **Gmail**:

| File | Trigger | What it does |
|---|---|---|
| `1-email-intake.json` | Gmail: unread mail with a PDF (polls every minute) | Signs the email + PDFs and `POST`s them to `/intake/email` → one case per PDF; replies to the sender with the case reference (no internal links); marks the mail read. No PDF → polite reply. |
| `2-review-notification.json` | Webhook `invoiceiq-review` (called by the backend) | Checks the signature; emails the reviewer (or approver) the question, exceptions and a deep link to the case; after 24 h asks the backend whether it's still waiting and sends a reminder. |
| `3-vendor-follow-up.json` | Webhook `invoiceiq-vendor-followup` (on REQUEST_INFO) | Checks the signature; emails the `vendor-faq.md` template text to the vendor contact **on file**. The seed vendors' addresses are fictional, so by default it goes to a demo inbox with the real recipient noted (`send_to_vendor` = `true` to send for real). |
| `4-daily-summary.json` | Weekdays 09:00 IST | `GET /stats` → summary email (approved, straight-through rate, waiting value, top issues, ERP). |

## Security

- Both directions are **HMAC-SHA256 signed**: headers `X-IIQ-Timestamp` (unix seconds) and
  `X-IIQ-Signature: sha256=<hex HMAC(secret, "<timestamp>.<raw JSON body>")>`; requests older
  than 5 minutes are refused. The backend sends compact JSON so n8n re-creates the exact bytes
  with `JSON.stringify(body)`. Shared secret: `N8N_WEBHOOK_SECRET`.
- `/intake/email` is the only route without the API key; the signature protects it. Workflows 2
  (reminder check) and 4 call the API with `X-API-Key`.
- **Set `API_KEY` before starting a tunnel.** Without it the backend refuses every request that
  arrives through Cloudflare (except `/intake/email` and `/health`).
- The secret and API key live in each workflow's **Config** node (n8n cloud's variables need a
  paid tier); anyone who can edit the workflows can read them.

## Setup

1. **Secrets** (in `backend/.env`; generate random values, e.g.
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`):
   ```
   API_KEY=<random>
   N8N_WEBHOOK_SECRET=<random>
   N8N_BASE_URL=https://<your-name>.app.n8n.cloud
   ```
   The frontend proxy needs the same key: set `API_KEY=<same>` for `next dev` / `next start`
   (e.g. `frontend/.env.local`).
2. **Run the backend and a tunnel** (backend on :8000, or :8010 for the live config):
   ```
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel --url http://localhost:8000
   ```
   It prints a `https://<random>.trycloudflare.com` URL (it changes every time you restart it).
3. **Import** each file in n8n cloud: *Workflows → Import from file*.
4. **Credentials:** open each Gmail node and pick (or create) a Gmail OAuth2 credential — you
   sign in to Google in n8n's own window.
5. **Config nodes:** fill in `backend_url` (the tunnel URL), `webhook_secret`, `api_key`,
   `reviewer_email`, `demo_inbox`, `summary_to`.
6. **Activate** all four workflows (webhooks only answer on their production URL when active).
7. Restart the backend so it reads the new `.env`; **Settings → Integrations** in the app shows
   whether n8n is configured and every notification it sent.

## Pre-filled copies, new tunnel URL, new n8n account

`cd backend && python -m uv run python -m scripts.n8n_prefill <tunnel URL> [--email <gmail>]`
writes `n8n/local/*.json` (git-ignored) with the tunnel URL, secret and API key filled in —
import those instead of `workflows/`.

- **Tunnel restarted** (the quick-tunnel URL changes): run the script with the new URL, then in
  n8n update `backend_url` in the Config nodes of workflows 1, 2 and 4 (or re-import).
- **New n8n account** (e.g. after the 14-day trial): sign up, re-run the script, import the four
  files, connect Gmail again, activate, and set `N8N_BASE_URL` in `backend/.env` to the new
  `https://<name>.app.n8n.cloud`, then restart the backend. The secret and API key can stay.
- **Longer term:** Phase 12 runs n8n self-hosted next to the backend on the free VM, which ends
  both the trial and the changing tunnel URL.

## Try it

- Email a PDF from `data/test-invoices/` (e.g. `technova-contract-rate.pdf`) to the Gmail account
  → within a minute a case appears under New run / Review queue, and you get a reply.
- That case waits for review → the reviewer email arrives with the deep link.
- On the case, choose **Request info** → the vendor follow-up arrives in the demo inbox.
