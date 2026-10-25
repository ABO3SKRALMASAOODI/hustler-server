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

## Repos

- **Frontend** — `~/Documents/Valmera/frontend-next/` → `github.com/ABO3SKRALMASAOODI/startup_frontend` (Next.js 16 App Router, Tailwind v3). All API calls go through the `/api-backend/` proxy in `next.config.mjs` — never call the Render URL directly from frontend code.
- **Backend/Worker** — `~/Documents/Valmera/hustler-server/` → `github.com/ABO3SKRALMASAOODI/hustler-server` (Flask + Gunicorn).
- **DEAD — never edit:** any surviving old CRA/app-builder checkout outside these two current repositories.

## How to Push

```bash
git config user.name "ABO3SKRALMASAOODI"
git config user.email "shmarymuslim@gmail.com"
# NEVER commit as Claude/noreply@anthropic.com — Vercel Hobby blocks unrecognized committers
git add <files> && git commit -m "description" && git push origin main
```

Frontend deploys via Vercel (~1–2 min — check the dashboard, SSR issues fail builds). Backend deploys via Render (~3–5 min). Cloudflare deploys on relevant `worker/` changes and verifies every lane's exact deployed source fingerprint. Modal and Cloud Run are manual fallback workflows only; ordinary releases must not publish or warm them.

## Executor releases (at most 2 a day)

Every push that touches `worker/**` is an executor release: both images are
rebuilt, all five Cloudflare lanes are probed, and every warm container is
replaced (warm MCP sessions and preview media are lost, so the next calls
start cold). October 2026 had 76 releases, 16 on one day. The rule:

- **At most two executor releases a day.** Batch worker changes into as few
  pushes as possible; backend-only, skill (`plugins/`) and frontend pushes do
  not release the executor.
- **Coalescing is automatic:** a deploy run with a newer `main` already
  waiting behind it skips its publish and the waiting run releases both
  (`worker/cloudflare/release_budget.py`); a third release in 24 h is a
  workflow warning. It never blocks, because Render deploys the dispatcher
  (and its MCP tool catalog) on every push.
- **Drained, never killed:** SIGTERM drains an executor (`worker/http_server.py`),
  the rollout grace is 300-900 s per lane, and work a deploy interrupts before
  it ran is handed back unspent and relaunched (`worker/CLOUDFLARE_EXECUTOR.md`,
  "Deploy handoffs"). Do not deploy during a customer's long export when it
  can wait; check `video_jobs` for running `final`/`index` rows of real users
  first.

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
- **Bonus** (`credits.FREE_GRANT_CREDITS`): 0 since 19 Aug 2026 (no free credits; the free trial below is separate and runs on its own budget).
- **Monthly**: set on subscribe, wiped+refreshed each renewal via Paddle webhook, clawed back to 0 on cancel/refund.

Columns on `users`: `credits_daily`, `credits_bonus`, `credits_monthly`, `credits_balance`, `credits_daily_reset`, `credits_monthly_limit`. The worker charges after each agent turn (min 1 credit). `model_prices.USD_PER_CREDIT` = **$0.005** — a credit is billed at 2x model cost; that constant is the margin.

| Plan (shopfront, `plan_catalog.NEW_PLANS`) | Price | Credits/mo |
|---|---|---|
| Free | $0 | 0 (free trial on the first project only, see below) |
| Plus (`plus_2026`) | $15/mo, $150/yr | 500 — MCP only (owner decision D1, "Plus includes the Studio AI editor", is on hold) |
| Pro (`pro_2026`) | $50/mo, $500/yr | 1,500 |
| Max (`max_2026`) | $100/mo, $1,000/yr | 4,000 |

MCP Connect (`mcp_connect`, $25, 1,500, MCP-only). Grandfathered: Creator/Pro/Frontier (`ai`/`ai_pro`/`ai_max`), Advanced, Plus 800 / Pro-legacy 2,400 / Ultra 5,000 / Titan 10,000 / Ace 30,000, and `mcp`. When changing plan credits, change **four places together**: `backend/routes/paddle.py` `PLANS`, `backend/routes/paddle_webhook.py` `PLAN_CREDITS`, `backend/credits.py` `PLAN_MONTHLY_LIMITS`, and all frontend/SEO copy quoting numbers. `worker/tests/test_model_prices.py` asserts ≥40% margin on every plan.

### Trials & billing rules

- **New checkouts have no trial.** Shopfront Paddle prices charge immediately. Existing subscriptions on the previous $30/$50/$100 3-day-trial prices keep running; the webhook grants the credits those prices sold.
- A live trial still grants `credits.TRIAL_CREDIT_FRACTION` (**10%** of the plan they bought) and no daily top-up. During a trial, `plan_limit` reports the allowance, not the plan. Hitting it → 402 `trial_cap_reached`.
- **Paddle flips a legacy trial to `active` when the trial ENDS, then tries the card — `active` ≠ paid.** Revenue is only a row in `payments` with `amount_cents > 0` (`grand_total` is minor units). `backend/billing.py` owns plan prices (`PLAN_PRICES_USD`); yearly is amortised for MRR.
- Check `FAILING_STATUSES` before any credit grant — `subscription.updated` keeps arriving during dunning with the paid price id and would otherwise re-fund the pool.
- Grace on failed payment is graded by history: never paid → pool lifted immediately; has paid → `PAID_GRACE_DAYS` (3). `lift_paid_credits` strips credits but keeps `subscription_id` so later retry events still find the user.
- `billing_sync.py` reconciles against Paddle hourly (`POST /admin/billing/sync`); it never downgrades on silence, and a Paddle 404 goes to the admin contradiction list, not auto-action.
- **Subscribe gate** (`routes/video._subscribe_gate_applies`): there is no free edit. An unsubscribed account's upload is walled (no index) and its requests get the plans reply; the refused request is kept on that reply (`meta.blocked_prompt`) and `POST /projects/<id>/replay-blocked` sends it (or starts the walled analysis / export) after checkout. **Plan gate** (`plan_gate.needs_plan`) remains the credits-empty wall. Both fail open on DB errors. Credit numbers always quoted from the server, never hardcoded in the frontend.
- **Free trial** (`backend/free_trial.py`, owner decision D2, 25 Oct 2026 — ON HOLD, ships OFF): an account's first Studio upload (or website-tile URL) is admitted only while `app_kv.free_result` is on (default off), past `start_at` and within budget (`projects.meta.free_result`): it is analysed, gets the deterministic First cut (`worker/first_cut.py`, an `agent_turn` with `payload.first_cut`), and an unsubscribed account may edit that project on a per-account trial budget (`per_account_usd_cap`, $1.00) that runs ONLY on Claude through the Anthropic API (`worker/claude_lane.py`, `payload.trial_lane`, never the OpenAI wallet, never charged; spend is `result.trial_usd`). Export/download is the paywall (`_request_final` → 402). Kill switch: `app_kv` `free_result` = `{"on": false}`; it also stops by itself at `pool_usd_cap` / `monthly_usd_cap` / `daily_count_cap`. Track D's `trial_budgets` total (`claude_trial.account_status`) is the per-account meter once merged. MCP uploads are never admitted. The "your cut is ready" email is a draft only (`free_trial.ready_email`), not wired to a sender.
- **Bounded requests** (P4): one Studio request gets at most `AGENT_REQUEST_MAX_PREVIEWS` (6) preview renders and `AGENT_REQUEST_MAX_S` (600 s) across its slices; then it delivers the best version with critic findings as `quality_status: "advisory"` (export open, "Improve it"). A partial/blocked request whose deliverable did not change is not charged.
- **Plans:** `routes/video.MCP_ONLY_PLANS` = {`mcp_connect`, `plus_2026`} block the Studio AI editor (D1, Plus includes it, is on hold).
- **Offers** (`backend/offers.py`): one 50%-off per account ever (`UNIQUE (user_id, kind)` on `user_offers` + `mint()` refuses after any `used_at`), monthly plans only, first period only, Frontier never discountable. `used_at` is set only by the Paddle webhook.

## Required production env (Render)

- **`PADDLE_WEBHOOK_SECRET`** — the webhook fails closed with 503 without it;
  Paddle retries after configuration is restored.
- **`PADDLE_API_KEY`** — required to verify the payer identity for first
  purchases; provider lookup failures return 503 and never trust browser data.
- **`ANTHROPIC_API_KEY`** — the free-trial Claude lane. On Render it lets the
  backend admit trial requests; the executors receive it from the GitHub
  secret of the same name through `deploy-cloudflare-executor.yml` (optional:
  without it trial requests meet the plans and the First cut's long-source
  scout uses its deterministic pick).
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

## Admin probe (run it at the start and after every deploy)

`backend/scripts/admin_probe.py` calls every endpoint the `/admin` tabs open
(Overview, Projects, Subscribers, Subscriber Projects, Exports, Waits, People,
Agent I/O, Redesign, Costs, Cohorts, Survey, Users, Traffic, Revenue,
Newsletter, Live), locally, against the production database **read-only**,
and prints per endpoint: PASS/FAIL, the time it would take next to the
database (wall time from here minus this machine's extra network: connects
and one round trip per statement), status, and a sanity note (users > 0 and
equal to the customer count, subscribers equal to the billing flag, today's
visitors not null, lists non-empty, lifetime payments > 0, heavy-report cache
age). It exits non-zero on any error, implausible number, or endpoint over
3 s (an over-budget endpoint is recomputed with `?fresh=1` twice before it is
called slow: the database's speed swings 2-5x).

    zsh /Users/masaoodi/Documents/Valmera/.valmera/admin_probe.sh             # latest main
    ADMIN_PROBE_BACKEND=<worktree>/backend zsh .valmera/admin_probe.sh       # a branch
    zsh .valmera/admin_probe.sh --only projects --json                       # one tab

Safety: a minimal Flask app with only the admin blueprints (no `create_app`,
so no scheduler starts), `default_transaction_read_only=on` forced onto every
connection, GET only, a random local SECRET_KEY. The wrapper reads only
`DATABASE_URL` from the main checkout's `backend/.env` and never prints it.
Every fixer runs it at the start and after deploying, and fixes any admin
endpoint that errors, returns empty or implausible data, or takes over 3 s.

Rules it guards (24 Oct 2026): a failed admin request shows an explicit error
and Retry, never 0 (`frontend lib/adminLoad.js`); 30-day reports go through
`admin_metrics.cache.shared_report` (served at once, recomputed in the
background); migration 039's partial indexes repeat `_mcp_non_success_sql('')`
and `_mcp_refusal_sql('')` verbatim, so change both together
(`tests/test_admin_read_paths.py` checks).

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
