# Valmera — Project Brief

## What It Is
An **agentic AI video editor** (valmera.io). Users upload footage and chat with an agent that edits it by rewriting an EDL; ffmpeg renders it. Four services:

- **Frontend** — Next.js 16 studio UI (chat + preview)
- **Backend** — Flask API (auth, billing, credits, chat routes: `backend/routes/video.py`, `admin_video.py`)
- **Worker** (`worker/`) — dispatcher: job queue, agent loop (LLM turns), faster-whisper indexing, credit charging (`worker/db.charge_turn_credits`)
- **Executor** — Cloudflare Containers are primary for capacity-safe interactive, batch, agent, MCP, and Shorts lanes. Modal app `valmera-executor` is the fenced fallback for provider failures and synchronous operations that exceed Cloudflare's self-serve container limits. Cloud Run is an emergency launch fallback only.

The old app-builder (`engine/AA.py`, `/auth/generate` routes) is retired but
still present for legacy compatibility. Do not modify, restore, or route new
work through it.

## Hosting

| Service | Where | URL / notes |
|---|---|---|
| Frontend | Vercel | `https://valmera.io` — auto-deploys on push to `main` |
| Backend + Worker | Render | `https://entrepreneur-bot-backend.onrender.com` — auto-deploys on push to `main` (~3–5 min). Persistent 10GB disk at `/opt/render/project/src/outputs` |
| Executor | Cloudflare + Modal fallback | Cloudflare Containers auto-deploy via `.github/workflows/deploy-cloudflare-executor.yml` on relevant `worker/` pushes and verify every lane's exact source fingerprint. Modal app `valmera-executor` is an operator-invoked, fenced disaster-recovery fallback. Google Cloud Run is retained at min-instances 0 and deploys manually only. |
| Database | Render managed PostgreSQL | via `$DATABASE_URL`; never store the URL in the repository |
| Email | Brevo | if emails stop: re-whitelist Render's IP (`74.220.48.3`) at `app.brevo.com/security/authorised_ips` |
| Payments | Paddle | **live** (production mode) |

## Email operations

- Brevo's free allowance is 300 emails/day. Keep the shared 280 marketing / 20 account-service budget and check Brevo's actual remaining credits before sending: HTTP 201 can mean queued, not delivered. Never infer available provider credits from the local day's counter alone.
- Marketing follows the admin's real-customer scope, except the main admin account remains included. Hidden pre-relaunch/test accounts must not consume marketing capacity.
- Recent signups (last 30 days) receive 75% of available marketing capacity; older customers retain 25%, with unused shares reassigned. Keep the 48-hour gap and three-marketing-emails-per-seven-days ceiling.
- `backend/routes/newsletter_campaigns.py` contains 27 distinct marketing messages. Lifecycle steps send once per topic; 12 editing lessons rotate by each recipient's successful history. No introductory offers or trial promises.
- Verification and payment notices are account-service messages and remain separate from marketing unsubscribe. Paid-subscriber alerts use a durable outbox and minute-level retries, capped at 15-minute backoff.
- Generate a local preview with `python backend/scripts/preview_email_library.py <output-directory>`; it never sends email. Never consume real customer or founder email quota merely to validate a release.

## Repos

- **Frontend** — `~/Documents/Valmera/frontend-next/` → `github.com/ABO3SKRALMASAOODI/startup_frontend` (Next.js 16 App Router, Tailwind v3). All API calls go through the `/api-backend/` proxy in `next.config.mjs` — never call the Render URL directly from frontend code.
- **Backend/Worker** — `~/Documents/Valmera/hustler-server/` → `github.com/ABO3SKRALMASAOODI/hustler-server` (Flask + Gunicorn).
- **DEAD — never edit:** any surviving old CRA/app-builder checkout outside these two current repositories.

## How to Push

```bash
git config user.name "ABO3SKRALMASAOODI"
git config user.email "shmarymuslim@gmail.com"
# NEVER commit as Codex/noreply@anthropic.com — Vercel Hobby blocks unrecognized committers
git add <files> && git commit -m "description" && git push origin main
```

Frontend deploys via Vercel (~1–2 min — check the dashboard, SSR issues fail builds). Backend deploys via Render (~3–5 min). Cloudflare deploys on relevant `worker/` changes and verifies every lane's exact deployed source fingerprint. Modal and Cloud Run are manual fallback workflows only; ordinary releases must not publish or warm them.

## Database Access

```bash
psql $DATABASE_URL -c "SQL"   # from the Render Shell tab, or use the external URL below locally
```

- **`models.py` never owns schema** — web startup must not run DDL. All numbered SQL lives in `backend/migrations/`; run it directly against production PostgreSQL in controlled order. `backend/apply_migrations.py` is the complete local/CI convenience runner, not a production replay tool.
- Backend routes use `token_required` and `get_db()` — not `jwt_required`/`get_db_connection()`.
- Deleting a user: delete child rows first (`job_credits`, `jobs`, `email_codes`, `code_request_logs`, `google_auth_codes`) then `users`.

## Credits

Three pools, hidden from users — they see one balance (`credits_balance` = daily + bonus + monthly). Spend order: daily → bonus → monthly.

- **Daily**: 20/day, resets daily, never accumulates.
- **Bonus** (`credits.FREE_GRANT_CREDITS` = 50): one-time free grant at registration, never refilled.
- **Monthly**: set on subscribe, wiped+refreshed each renewal via Paddle webhook, clawed back to 0 on cancel/refund.

Columns on `users`: `credits_daily`, `credits_bonus`, `credits_monthly`, `credits_balance`, `credits_daily_reset`, `credits_monthly_limit`. The worker charges after each agent turn (min 1 credit). `model_prices.USD_PER_CREDIT` = **$0.005** — a credit is billed at 2x model cost; that constant is the margin.

| Plan | Price | Credits/mo |
|---|---|---|
| Free | $0 | 50 one-time bonus only |
| Creator (`ai`) | $15/mo, $150/yr | 1,000 |
| Pro (`ai_pro`) | $30/mo, $300/yr | 2,000 |
| Frontier (`ai_max`) | $50/mo, $500/yr | 5,000 — only plan on the `FRONTIER_*` model |

Retired but grandfathered: Plus 800 / Pro-legacy 2,400 / Ultra 5,000 / Titan 10,000 / Ace 30,000, and `mcp`. When changing plan credits, change **four places together**: `backend/routes/paddle.py` `PLANS`, `backend/routes/paddle_webhook.py` `PLAN_CREDITS`, `backend/credits.py` `PLAN_MONTHLY_LIMITS`, and all frontend/SEO copy quoting numbers. `worker/tests/test_model_prices.py` asserts ≥40% margin on every plan.

### Trials & billing rules

- **New checkouts have no trial.** Shopfront Paddle prices charge immediately. Existing subscriptions on the previous $30/$50/$100 3-day-trial prices keep running; the webhook grants the credits those prices sold.
- A live trial still grants `credits.TRIAL_CREDIT_FRACTION` (**10%** of the plan they bought) and no daily top-up. During a trial, `plan_limit` reports the allowance, not the plan. Hitting it → 402 `trial_cap_reached`.
- **Paddle flips a legacy trial to `active` when the trial ENDS, then tries the card — `active` ≠ paid.** Revenue is only a row in `payments` with `amount_cents > 0` (`grand_total` is minor units). `backend/billing.py` owns plan prices (`PLAN_PRICES_USD`); yearly is amortised for MRR.
- Check `FAILING_STATUSES` before any credit grant — `subscription.updated` keeps arriving during dunning with the paid price id and would otherwise re-fund the pool.
- Grace on failed payment is graded by history: never paid → pool lifted immediately; has paid → `PAID_GRACE_DAYS` (3). `lift_paid_credits` strips credits but keeps `subscription_id` so later retry events still find the user.
- `billing_sync.py` reconciles against Paddle hourly (`POST /admin/billing/sync`); it never downgrades on silence, and a Paddle 404 goes to the admin contradiction list, not auto-action.
- **Subscribe gate** (`routes/video._subscribe_gate_applies`): one free real edit, then the ask. After an unsubscribed account has a done `agent_turn` that moved the timeline past v1 (or a shorts run that rendered clips), the next prompt shows subscription cards — not a new trial. The first indexed turn still runs. Active trials pass because they are subscribed. **Plan gate** (`plan_gate.needs_plan`) remains the credits-empty wall. Both fail open on DB errors. Credit numbers always quoted from the server, never hardcoded in the frontend.
- **Offers** (`backend/offers.py`): introductory and retention discounts were retired on September 8, 2026. Do not mint offers, send discount emails, attach cached discount IDs, or recreate retired Paddle codes. Preserve historical redemption stamps for delayed Paddle webhooks.

## Required production env (Render)

- **`PADDLE_WEBHOOK_SECRET`** — the webhook fails closed with 503 without it;
  Paddle retries after configuration is restored.
- **`PADDLE_API_KEY`** — required to verify the payer identity for first
  purchases; provider lookup failures return 503 and never trust browser data.
- **`SECRET_KEY`** — required for stable JWT/session signing and must contain
  at least 32 non-placeholder characters. If absent or weak, the process uses
  an unpredictable ephemeral key and `/healthz` stays degraded, so the release
  verifier cannot certify the deployment.

## YouTube fetch from the worker (bot wall)

YouTube challenges Render's datacenter IP ("Sign in to confirm you're not a
bot"). `worker/ytaccess.py` is the whole story; the operator-facing facts:

- **PO tokens are the default fix** — baked into the worker image (Dockerfile
  `POT=1` layer, bgutil provider). Anonymous, nothing to rotate, no env needed.
- **Cookies are optional extra strength** and PERISHABLE: an export taken
  from a running browser is rotated out by Google within ~a day (Aug 8-9:
  two jars died this way while the plumbing got blamed). To make a jar that
  lasts: **private/incognito window → log in to youtube.com → export cookies
  from that window → close the window without logging out**, and prefer a
  burner account. Deliver through ANY of: `YTDLP_COOKIES_FILE` (path or
  pasted content), `YTDLP_COOKIES` (content), a Render Secret File (any
  name — `/etc/secrets` is scanned), or over psql:
  `INSERT INTO app_kv (key, value) VALUES ('ytdlp_cookies', <jar>) ON
  CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();`
  (picked up within 5 minutes, no restart).
- **Verify from the worker's own network**: every boot writes the app_kv row
  `ytdlp_probe` (JSON: `ok`, `why`, `cookie_source`, `stale_cookies`, `pot`,
  `code`) — `SELECT value FROM app_kv WHERE key = 'ytdlp_probe';` answers
  "is fetch working in prod" without the Render dashboard.

## Gotchas

1. **CORS is manual** in `app.py` (`before_request`/`after_request`) — don't remove.
2. **SSR**: no `localStorage`/`window`/`document` outside `useEffect` or without a `typeof window` guard — #1 cause of Vercel build failures. Pages using `useSearchParams()` must be wrapped in `<Suspense>`. Interactive components need `"use client"`.
3. **Auth tokens**: always `setToken()`/`removeToken()` from `@/utils/auth` (sets localStorage + cookie; the cookie drives the Next.js 16 `src/proxy.js` route protection for `/studio`, `/account`, `/admin`, and `/cancel`).
4. **OAuth**: never pass tokens/codes as query params (Safari ITP blocks them) — use path segments (`/google-callback/{code}`, one-time codes in `google_auth_codes`). Google OAuth redirect URI points at the backend; JS origins include both `valmera.io` and `www.valmera.io`.
5. **Admin** is gated to `thevalmera@gmail.com`.
6. `next.config.mjs` is authoritative. Do not reintroduce a competing `.js` or `.ts` config.
7. Render shell is ephemeral; only the persistent disk and repo survive redeploys.
8. Old domain `thehustlerbot.com` is dead — everything is `valmera.io`.

---

Retrieve production database access from the deployment secret store. Never
paste credentials into tracked files, issues, logs, or agent context.
