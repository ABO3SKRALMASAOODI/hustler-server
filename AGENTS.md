# Valmera — Project Brief

## What It Is
An **agentic AI video editor** (valmera.io). Users upload footage and chat with an agent that edits it by rewriting an EDL; ffmpeg renders it. Four services:

- **Frontend** — Next.js 15 studio UI (chat + preview)
- **Backend** — Flask API (auth, billing, credits, chat routes: `backend/routes/video.py`, `admin_video.py`)
- **Worker** (`worker/`) — dispatcher: job queue, agent loop (LLM turns), faster-whisper indexing, credit charging (`worker/db.charge_turn_credits`)
- **Executor** — Modal app `valmera-executor`; durable functions run the CPU-heavy ffmpeg index/preview/final renders and synchronous media tools, scale to zero. Cloud Run is an emergency launch fallback only.

The old app-builder (`engine/AA.py`, `/auth/generate` routes) is retired but still deployed — never touch it.

## Hosting

| Service | Where | URL / notes |
|---|---|---|
| Frontend | Vercel | `https://valmera.io` — auto-deploys on push to `main` |
| Backend + Worker | Render | `https://entrepreneur-bot-backend.onrender.com` — auto-deploys on push to `main` (~3–5 min). Persistent 10GB disk at `/opt/render/project/src/outputs` |
| Executor | Modal | app `valmera-executor`, environment `main`. Auto-deploys via `.github/workflows/deploy-modal-executor.yml` on pushes touching `worker/`; setup and verification are in `worker/MODAL_EXECUTOR.md`. Google Cloud Run is retained at min-instances 0 and deploys manually via `.github/workflows/deploy-executor.yml` only. |
| Database | Render managed PostgreSQL | via `$DATABASE_URL`; external URL at bottom of this file |
| Email | Brevo | if emails stop: re-whitelist Render's IP (`74.220.48.3`) at `app.brevo.com/security/authorised_ips` |
| Payments | Paddle | **live** (production mode) |

## Repos

- **Frontend** — `~/Documents/Startup/frontend-next/` → `github.com/ABO3SKRALMASAOODI/startup_frontend` (Next.js 15 App Router, Tailwind v3). All API calls go through the `/api-backend/` proxy in `next.config.js` — never call the Render URL directly from frontend code.
- **Backend/Worker** — `~/Documents/hustler-server/` → `github.com/ABO3SKRALMASAOODI/hustler-server` (Flask + Gunicorn).
- **DEAD — never edit:** `~/Documents/Startup/frontend/` (old CRA) and `~/Documents/Startup/backend/`.

## How to Push

```bash
git config user.name "ABO3SKRALMASAOODI"
git config user.email "shmarymuslim@gmail.com"
# NEVER commit as Codex/noreply@anthropic.com — Vercel Hobby blocks unrecognized committers
git add <files> && git commit -m "description" && git push origin main
```

Frontend deploys via Vercel (~1–2 min — check the dashboard, SSR issues fail builds). Backend deploys via Render (~3–5 min). Modal executor functions deploy from `.github/workflows/deploy-modal-executor.yml` when `worker/` changes; the workflow verifies the deployed code fingerprint. Cloud Run fallback deploys manually only.

## Database Access

```bash
psql $DATABASE_URL -c "SQL"   # from the Render Shell tab, or use the external URL below locally
```

- **Never modify `models.py` for schema changes** — schema is managed directly via psql. Use `models.py` only for its helpers (`get_db()`, `update_user_subscription_status`).
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
- **Offers** (`backend/offers.py`): one 50%-off per account ever (`UNIQUE (user_id, kind)` on `user_offers` + `mint()` refuses after any `used_at`), monthly plans only, first period only, Frontier never discountable. `used_at` is set only by the Paddle webhook.

## Required production env (Render)

- **`PADDLE_WEBHOOK_SECRET`** — the webhook fails OPEN without it (anyone could forge a subscription).
- **`SECRET_KEY`** — falls back to a literal default if unset → forgeable JWTs.

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
3. **Auth tokens**: always `setToken()`/`removeToken()` from `@/utils/auth` (sets localStorage + cookie; the cookie drives `src/middleware.js` route protection for `/studio`, `/account`, `/admin`).
4. **OAuth**: never pass tokens/codes as query params (Safari ITP blocks them) — use path segments (`/google-callback/{code}`, one-time codes in `google_auth_codes`). Google OAuth redirect URI points at the backend; JS origins include both `valmera.io` and `www.valmera.io`.
5. **Admin** is gated to `thevalmera@gmail.com`.
6. Only `next.config.js` may exist — delete any `next.config.ts` immediately.
7. Render shell is ephemeral; only the persistent disk and repo survive redeploys.
8. Old domain `thehustlerbot.com` is dead — everything is `valmera.io`.

---

render external url: postgresql://the_hustler_bot_user:ajcmtxLo05sonfhqiTjA4kRAegN099DO@dpg-d0vgraggjchc7385l1u0-a.oregon-postgres.render.com/the_hustler_bot
