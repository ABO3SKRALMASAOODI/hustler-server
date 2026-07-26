## Valmera — Project Brief

### What It Is
An AI-powered web app builder. Users describe an app, the AI builds it, and they get a live preview + downloadable code. Users can then send follow-up messages to edit their app iteratively.

---

### Hosting & Infrastructure

**Frontend** → Vercel
- URL: `https://valmera.io`
- Framework: **Next.js 15 (App Router)** — migrated from Create React App on April 2, 2026
- Auto-deploys from GitHub on push to `main`
- Framework Preset in Vercel: **Next.js** (not CRA)
- Build command: default (`next build`) — no override
- Old domain `thehustlerbot.com` is no longer in use

**Backend** → Render (Web Service)
- URL: `https://entrepreneur-bot-backend.onrender.com`
- Auto-deploys from GitHub on push to `main`
- Persistent disk: 10GB mounted at `/opt/render/project/src/outputs`
- Build command: `pip install -r backend/requirements.txt`
- Start command: `bash start.sh`

**Database** → PostgreSQL (Render managed)
- Accessed via `$DATABASE_URL` env var on Render
- To run DB commands: use the Render Shell tab on the backend service

**Email** → Brevo (transactional email)
- Sender: `support@thehustlerbot.com` *(consider updating to support@valmera.io)*
- If emails stop working: check `app.brevo.com → Senders & IP → Authorised IPs` and make sure Render's IP is whitelisted
- Render's current IP: `74.220.48.3`

**Payments** → Paddle
- Currently **live** (production mode)
- Paddle client token is in `PaddleCheckoutPage.js`

---

### Repos & Local Setup

Two repos, both open together in one VS Code workspace:

**Frontend** — `~/Documents/Startup/frontend-next/`
- GitHub: `https://github.com/ABO3SKRALMASAOODI/startup_frontend.git`
- Framework: **Next.js 15 (App Router)** with Tailwind CSS v3
- All API calls go through `/api-backend/` proxy configured in `next.config.js`

**Backend/Engine** — `~/Documents/hustler-server/`
- GitHub: `https://github.com/ABO3SKRALMASAOODI/hustler-server.git`
- Framework: Flask + Gunicorn

**DEAD FOLDERS — never edit:**
- `~/Documents/Startup/frontend/` — old CRA frontend (keep temporarily as reference, then delete)
- `~/Documents/Startup/backend/` — old unused backend folder

---

### Project Structure

```
frontend-next/
  next.config.js          ← API proxy rewrites, headers, staleTimes, ESLint skip
  jsconfig.json           ← Path alias: @/ → ./src/
  tailwind.config.js      ← Tailwind v3 config
  postcss.config.js       ← PostCSS for Tailwind
  public/                 ← Static assets (favicons, .riv files, sitemap, robots.txt)
  src/
    app/
      layout.js           ← Root layout: global CSS, metadata/SEO, Google Analytics, structured data
      page.js             ← Root route: shows LandingPage or redirects to /studio if logged in
      globals.css         ← Merged global styles (Tailwind + custom)
      landing.css         ← Landing page specific styles
      login/page.js       ← Sign in (email + Google OAuth)
      register/page.js    ← Registration
      enter-password/page.js  ← Password entry after email
      verify/page.js      ← Email verification code
      change-password/page.js ← Password reset request
      reset-password/page.js  ← Password reset form
      account/page.js     ← User account management (protected)
      studio/page.js      ← Main app builder UI — chat + preview + code viewer (protected)
      admin/page.js       ← Admin analytics dashboard (protected)
      subscribe/page.js   ← Subscription plan selection / pricing
      paddle-checkout/page.js ← Handles Paddle checkout redirect
      purchase-success/page.js ← Post-purchase confirmation
      templates/page.js   ← Full template gallery
      docs/page.js        ← Documentation / feature guides
      legal/
        page.js           ← Legal page (About, Privacy, Terms, Refund, Cookies, Contact)
        loading.js        ← Loading spinner while legal page hydrates
      home/page.js        ← Always shows LandingPage (no auth redirect)
      google-callback/[code]/page.js  ← Google OAuth one-time code exchange
      github-callback/page.js        ← GitHub OAuth callback
    components/
      StickyNavbar.js     ← Main navigation bar
      AuthShell.js        ← Shared auth page wrapper (dark popup style)
      GoogleAuth.js       ← Google login button + OR divider + auth handler
      ModelSelector.js    ← AI model picker (V6, V6 Pro, V7)
      NameModal.js        ← First-time user name prompt
      PageTracker.js      ← Analytics page view tracker
      Footer.js           ← Site footer (used on Landing, Templates, Subscribe)
      RobotBubble.js      ← Floating robot chat bubble
      Robot.js            ← Rive robot component
      LegalModal.js       ← Legacy legal modal (may be unused)
      pages/
        LandingPage.js    ← Shared landing page component (used by / and /home)
    api/
      api.js              ← Axios instance with baseURL: "/api-backend" + auth interceptor
    utils/
      auth.js             ← setToken/removeToken/getToken — stores in both localStorage AND cookie
    middleware.js          ← Auth guard: redirects unauthenticated users from /studio, /account, /admin to /login

hustler-server/
  backend/
    app.py              ← Flask app entry point, registers all blueprints
    credits.py          ← All credits logic (daily reset, monthly pool, deduction)
    models.py           ← DB schema creation and update_user_subscription_status
    routes/
      auth.py           ← Main routes: register, login, generate, job status, cancel
      verify_email.py   ← Email verification codes via Brevo
      paddle.py         ← Paddle checkout, webhooks, plan changes
      google_auth.py    ← Google OAuth: login, callback, one-time code exchange
      github.py         ← GitHub OAuth callback
      admin.py          ← Admin dashboard + analytics tracking
      deploy.py         ← Deployment routes
      supabase_mgmt.py  ← Supabase management routes
      stripe_mgmt.py    ← Stripe management routes
      ai_proxy.py       ← AI proxy routes
      planner.py        ← Planner routes
  engine/
    AA.py               ← AI agent that builds the app (runs as subprocess per job)
  outputs/              ← Persistent disk — one folder per job (job_id = 8-char UUID)
  outputs_template/     ← 6 hardcoded template projects tracked in git
  start.sh              ← Copies templates to disk on boot, then starts gunicorn
```

---

### Key Differences from Old CRA Setup

| What | Old (CRA) | New (Next.js) |
|------|-----------|---------------|
| **Adding a new page** | Create in `src/pages/`, add `<Route>` in `App.js` | Create `src/app/your-route/page.js` — no config needed |
| **Navigation** | `useNavigate()` → `navigate("/path")` | `useRouter()` → `router.push("/path")` |
| **Links** | `import { Link } from "react-router-dom"` → `<Link to="/path">` | `import Link from "next/link"` → `<Link href="/path">` |
| **URL params** | `useParams()` from react-router-dom | `useParams()` from `next/navigation` |
| **Query strings** | `useSearchParams()` from react-router-dom | `useSearchParams()` from `next/navigation` (must wrap page in `<Suspense>`) |
| **Go back** | `navigate(-1)` | `router.back()` |
| **Current path** | `useLocation().pathname` | `usePathname()` from `next/navigation` |
| **Auth guard** | `<PrivateRoute>` wrapper in App.js | `src/middleware.js` checks cookie server-side |
| **Storing auth token** | `localStorage.setItem("token", ...)` | `setToken(token)` from `@/utils/auth` (sets both localStorage AND cookie) |
| **Removing auth token** | `localStorage.removeItem("token")` | `removeToken()` from `@/utils/auth` (clears both) |
| **Env vars** | `REACT_APP_*` | `NEXT_PUBLIC_*` |
| **Import paths** | `../api/api` | `@/api/api` |
| **Client interactivity** | Everything is client by default | Add `"use client"` at top of every interactive file |
| **Global CSS** | `index.css` + `App.css` | `src/app/globals.css` (imported in `layout.js`) |
| **Proxy config** | `vercel.json` rewrites | `next.config.js` rewrites |
| **Layout wrapper** | `App.js` wraps everything | `src/app/layout.js` wraps everything |
| **SEO metadata** | `public/index.html` `<head>` tags | `metadata` export in `src/app/layout.js` |

---

### Critical SSR Rules

Next.js renders pages on the server first. These rules prevent build failures:

1. **Never use `localStorage`, `window`, `document`, or `sessionStorage` in top-level component code or `useState()` initializers.** Always wrap in `useEffect` or guard with `typeof window !== "undefined"`.

   ```js
   // BAD — breaks build
   const [plan, setPlan] = useState(localStorage.getItem("user_plan") || "free");
   
   // GOOD
   const [plan, setPlan] = useState("free");
   useEffect(() => {
     setPlan(localStorage.getItem("user_plan") || "free");
   }, []);
   
   // ALSO GOOD (inline guard for non-state usage)
   const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
   ```

2. **Pages using `useSearchParams()` must be wrapped in `<Suspense>`:**
   ```js
   import { Suspense } from "react";
   function MyPage() { /* uses useSearchParams */ }
   export default function Page() { return <Suspense><MyPage /></Suspense>; }
   ```

3. **Every component using hooks, browser APIs, or event handlers needs `"use client"` at the top of the file.**

---

### Auth System

Authentication uses **dual storage** — JWT is stored in both places:

- **localStorage** — read by client-side code (Axios interceptor in `api.js`)
- **Cookie** — read by Next.js middleware for server-side route protection

Always use `setToken()` and `removeToken()` from `@/utils/auth` when logging in or out. Never use raw `localStorage.setItem("token", ...)` directly.

The middleware (`src/middleware.js`) protects `/studio`, `/account`, and `/admin` — if no token cookie is found, it redirects to `/login`.

---

### How a Job Works

1. User types a prompt → frontend calls `POST /auth/generate`
2. Backend creates a job folder in `outputs/<job_id>/`, copies the Vite+React scaffold template into it, writes `prompt.txt` and `meta.json`, spawns `AA.py` as a subprocess
3. `AA.py` runs the AI agent — reads prompt, writes/edits files, then runs `npm install` + `vite build`
4. After build: **node_modules is deleted** to save disk space
5. Built `dist/` folder is served by Flask at `/auth/preview/<job_id>/`
6. Frontend polls `GET /auth/job/<job_id>/status` every 3 seconds to get state + preview URL
7. For follow-up edits: `POST /auth/job/<job_id>/message` → spawns AA.py again with `--message` flag → reinstalls node_modules, rebuilds, deletes node_modules again

---

### Google OAuth Flow

Uses a **one-time code exchange** pattern to avoid Safari's query parameter blocking:

1. User clicks "Continue with Google" → browser redirects to `GET /auth/google/login`
2. Backend redirects to Google consent screen
3. Google calls back to `GET /auth/google/callback?code=...`
4. Backend exchanges code for profile, creates/finds user, issues JWT
5. JWT is stored in `google_auth_codes` DB table with a random one-time code
6. Backend redirects to `https://valmera.io/google-callback/{one_time_code}` (path segment, not query param)
7. `google-callback/[code]/page.js` reads the code from the URL path, calls `POST /auth/google/exchange` to get the real JWT
8. Frontend calls `setToken()` (stores in localStorage + cookie) and redirects to `/studio`

**Why path segments instead of query params:** Safari (especially iPhone Safari and Private Browsing) blocks JavaScript access to query parameters on redirected URLs via Intelligent Tracking Prevention. Path segments are not affected.

**DB table:** `google_auth_codes` — stores one-time codes with 5-minute expiry, auto-cleaned on each callback.

---

> **NOTE (product pivot):** Valmera is now an **agentic AI video editor**, not a web-app builder. The "What It Is" / job-pipeline sections above describe the retired app-builder (its `engine/AA.py` + `/auth/generate` routes are still deployed but untouched). The live product is the video studio: `backend/routes/video.py` + `backend/routes/admin_video.py` + the `worker/` service (ffmpeg + faster-whisper + an EDL-editing agent loop). See the `valmera-video-editor-pivot` memory for the full architecture.

### Credits System

Three pools, completely hidden from users — they see one combined balance (`credits_balance` = daily + bonus + monthly):

- **Daily credits (20/day):** Reset every day regardless of usage. Never accumulate. Spent first.
- **Bonus credits (150 welcome, one-time):** Granted at registration. Spent after daily, before monthly.
- **Monthly credits:** Set when user subscribes (Plus 800 / Pro 2400, plus retired Ultra 5000 / Titan 10000 / Ace 30000 for grandfathered subs). Wiped and refreshed on each billing renewal via Paddle webhook; **clawed back to 0 on cancel/refund**. Spent last.

Key columns in `users` table: `credits_daily`, `credits_bonus`, `credits_monthly`, `credits_balance`, `credits_daily_reset`, `credits_monthly_limit`. The **worker** charges credits after each agent turn (`worker/db.charge_turn_credits`, ~1 credit = $0.01 of model cost, min 1). Values are authoritative in code (`backend/credits.py` `PLAN_MONTHLY_LIMITS`, `backend/routes/paddle_webhook.py` `PLAN_CREDITS`, `worker/config.py`), not here.

---

### Subscription Plans

**Creator (`ai`)**, **Pro (`ai_pro`)** and **Frontier (`ai_max`)** are purchasable (`PURCHASABLE_PLANS` in `paddle.py`). All carry a **3-day free trial configured on the Paddle PRICE**, so checkout creates the subscription immediately with status `trialing` and charges nothing until day 3 — which is why `is_subscribed` is true from day zero and why the admin cohort funnel counts **trial** and **paid** as two separate stages (`trial_status` from `backend/trial_state.py`; the gap between the two columns *is* the trial conversion rate). Plus/Pro(legacy)/Ultra/Titan/Ace and `mcp` are retired from the UI and checkout but stay in `PLANS`/`PLAN_CREDITS` so grandfathered subscribers keep working.

| Plan             | Price | Monthly Credits | Model | Status |
|------------------|-------|-----------------|-------|--------|
| Free             | $0    | 0 — a flat one-off 120 grant, **no free agent turns** | — | live |
| Creator (`ai`)   | $30/mo, $300/yr | 2,000 | `AGENT_MODEL` (standard) | live, purchasable |
| Pro (`ai_pro`)   | $50/mo, $500/yr | 4,000 | `PAID_*` — "more intelligence" | live, purchasable |
| Frontier (`ai_max`) | $100/mo, $1000/yr | 10,000 | `FRONTIER_*` — agent **and vision** | live, purchasable |
| MCP (`mcp`)      | —     | 0 (brings its own model) | — | off the shopfront, one grandfathered sub |
| Plus / Pro(legacy) / Ultra / Titan / Ace | — | 800 / 2,400 / 5,000 / 10,000 / 30,000 | — | retired (grandfathered only) |

**THE MARGIN IS IN THE BURN RATE, NOT THE GRANT (round 49, Jul 27 2026).** `model_prices.USD_PER_CREDIT` is **$0.005** — a credit is spent at *twice* what the model actually costs. So 2,000 credits is **$10** of real API spend, not $20. That one constant is the business; check it before believing any margin claim:

| Plan | Price | Credits | Real cost | Margin | Annual | Annual margin |
|------|-------|---------|-----------|--------|--------|---------------|
| Creator | $30 | 2,000 | $10 | 67% | $300/yr ($25/mo) | 60% |
| Pro | $50 | 4,000 | $20 | 60% | $500/yr ($41.67/mo) | 52% |
| Frontier | $100 | 10,000 | $50 | 50% | $1000/yr ($83.33/mo) | 40% |

It was 1:1 with cost until round 49, which left the **annual** tiers at 28–40% and made an intro discount on an annual plan a below-cost sale. Doubling the burn rate fixed all of them without moving a sticker price — the customer sees a bigger grant and each credit buys half as much model. Change credits in **four** places together: `backend/routes/paddle.py` `PLANS`, `backend/routes/paddle_webhook.py` `PLAN_CREDITS`, `backend/credits.py` `PLAN_MONTHLY_LIMITS`, and the frontend copy (`subscribe`, `checkout`, `seo/PricingMini`, `docs/credits`, `layout.js` JSON-LD, and the SEO pages that quote numbers). `worker/tests/test_model_prices.py` asserts every plan clears 40% on both billing periods.

**Frontier Paddle ids** (created 2026-07-27): product `pro_01kyg21hq9mbaj7pk3y1ewmzxp`, monthly `pri_01kyg21hzbbz360kn0ptjnpdar`, yearly `pri_01kyg21j78jk6tpkkcpkrysvc4`.

### The trial is 10% of the plan (round 49)

A trial grants **`credits.TRIAL_CREDIT_FRACTION` (10%)** of the plan and **no daily top-up** — 200 / 400 / 1,000 credits. Before this a trialling account was granted the FULL monthly pool and could burn all of it (up to **$50** of model spend on Frontier) before Paddle had charged a cent. Conversion is what releases the rest.

Decided in `paddle_webhook._trial_aware_grant`, and three things there are load-bearing:

- `transaction.completed` carries the TRANSACTION's status, never the subscription's, so it cannot tell a real charge from the $0 that opens a trial. A **recorded** trial (`trial_state.is_recorded_trial`) counts as trialing.
- The grant is a **SET**, which is what makes Paddle's repeat events idempotent — and is also what would refill a half-spent trial. A repeat event for a running trial passes `preserve_credits=True`.
- The grant runs **before** `sync_from_subscription`, so the first `subscription.created` sees no recorded trial, grants the slice fresh, and every event after preserves. That also self-heals the race where a transaction event arrives first and wrongly grants the full pool.

`credits.get_balance` reports `trialing` + `trial_cap_reached`, and `plan_limit` during a trial is the **allowance**, not the plan (showing "x / 2,000" to someone who can spend 200 reads as a bug). Hitting it is a **third** wall with its own 402 code `trial_cap_reached` — these users already entered a card, so "start a trial" is nonsense to them and "wait for your cycle" is worse.

### The plan gate: free upload, paid editing (round 49)

`FREE_TASTE_TURNS` is now **0**. Round 48's answer to "don't ask for a card before the product has done anything" was five free agent turns — five lots of real model spend per signup, mostly on accounts that never convert. Round 49 gives the free part away somewhere cheaper: **creating a project, uploading and INDEXING are ungated** (the gate was removed from `/projects` and `/uploads` in `video.py`). A new account reaches "here is what I found in your video — 8 shots, 1,553 words, 45 pauses" before it is asked for anything, and *that* screen asks for the trial (`PlanCTACard`, variant `video_ready`). The gate now stands at the agent turn alone.

The studio no longer redirects on `needs_plan`; it remembers it and shows the card once per project. Both cards are **dismissible** — the studio behind still works, and the API is the enforcement.

### The 50%-off offer (round 49)

`backend/offers.py` owns it end to end. **TWO moments, and neither is signup:**

- **winback** — 24 hours after registering, to an account that started **no trial**, sent as the `offer_50` newsletter campaign (idempotent forever via `newsletter_sends`). A live trial can never match it (a trialling user is `is_subscribed`); `_never_trialled` additionally excludes anyone who trialled and lapsed, because starting a trial *is* taking action.
- **save** — offered on the cancel-confirmation page (`/cancel`) to a trialling user who has not already taken a discount; applied to the *existing* subscription via `PATCH /subscriptions/{id}`.
- **welcome** — **RETIRED as a mint.** Nothing creates it; the kind stays only so existing prod rows read back. It used to be minted the instant an account existed (and again on every pricing-page load), so the first screen a visitor ever saw was already discounted — which sells the discount before the product and spends the one offer the account can ever hold at the moment it is worth least. `/billing/offer` is now **read-only**; the pricing page opens at full price for everyone.

**Frontier is NOT discountable** (`offers.DISCOUNTABLE_PLANS = ("ai", "ai_pro")`, mirrored in `components/Offer.js`). $100 for 10,000 credits is already a 50% margin, so half off is a month sold at exactly cost. Enforced at Paddle via `restrict_to` as well as in the two checkout routes and the card UI.

**ONE 50% PER ACCOUNT, EVER.** Enforced twice: `UNIQUE (user_id, kind)` on `user_offers` makes every mint path idempotent, and `mint()` refuses outright once any of that user's offers has `used_at` set. That second clause is what stops the save-offer stacking a second 50% on someone who already started their trial on the welcome discount — they get an honest cancel screen instead. `used_at` is written from the **Paddle webhook**, when Paddle confirms a discount is actually on the subscription — never when we merely offered one.

**Monthly only, first period only.** The Paddle discount is created with `recur: false` and `restrict_to` the two monthly price ids, so Paddle itself refuses to apply it to an annual plan (where 50% off would be a year of credits sold below cost). The discount is resolved lazily (env `PADDLE_DISCOUNT_ID` → lookup by code `VALMERA50` → create) and cached; if it cannot be resolved, `live_offer()` returns None and **no countdown or struck-through price is shown anywhere** — the honest-off contract. The countdown is `seconds_remaining` computed on the server; the client only ages it between fetches, so a skewed device clock can never show a price checkout will not honour.

Table (already created on prod):

```sql
CREATE TABLE IF NOT EXISTS user_offers (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    percent_off INTEGER NOT NULL DEFAULT 50,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(), expires_at TIMESTAMP NOT NULL,
    emailed_at TIMESTAMP, served_at TIMESTAMP, used_at TIMESTAMP,
    UNIQUE (user_id, kind));
```

---

### Required production env (Render)

- **`PADDLE_WEBHOOK_SECRET`** — MUST be set (Paddle → Developer tools → Notifications). The webhook fails OPEN when unset: an unsigned POST is accepted, so anyone could forge `subscription.created` and grant themselves a plan. Code already HMAC-verifies when present — this is pure config.
- **`SECRET_KEY`** — MUST be a real secret. Falls back to the literal `"supersecretkey"` if unset, which makes every JWT forgeable → full account takeover. Verify it is set on both Render services; rotate if in doubt.
- **LLM provider = DeepSeek V4 Pro (default, since Jul 26 2026).** The whole LLM stack (agent tool-calling, vision, concierge) is OpenAI-compatible. You set **only `OPENAI_API_KEY`** (a DeepSeek key) on both the backend web service and the worker — the defaults already select `OPENAI_BASE_URL=https://api.deepseek.com`, `AGENT_MODEL=deepseek-v4-pro`, `VISION_MODEL=deepseek-v4-pro`. To use a different provider set `OPENAI_BASE_URL`/`AGENT_MODEL`/`VISION_MODEL` (for the previous stack: `https://api.x.ai/v1` + `grok-4.5` + prices `2.0`/`6.0`).
  - **Vision has its OWN provider — `VISION_BASE_URL` (default `https://api.x.ai/v1`) + `VISION_MODEL` (default `grok-4.5`) + `VISION_API_KEY`.** **NO DeepSeek V4 tier accepts images**, pro included: the chat API rejects an `image_url` content part at the JSON layer (`400 … unknown variant \`image_url\`, expected \`text\``). This doc previously claimed pro was multimodal and vision inherited `AGENT_MODEL` — so from **13:06 UTC Jul 26 2026 every `look_at`, `look_at_asset` and preview self-check 400'd, 59 in a row**, and the agent asked users to describe their own footage to it. The key follows the same rule as `IMAGE_API_KEY`: inherited only from a provider on the SAME base URL (so an xAI `IMAGE_API_KEY` on the worker lights up vision too), never handed across providers. With no key for it, `vision_available()` is False and the tools say "visual inspection unavailable" — honest-off, not broken. A provider that 400s on an image part also **disables vision for the process** on the first such error rather than repeating a doomed call per look. Verify after any provider change: `SELECT model, count(*) FILTER (WHERE response->>'error' IS NOT NULL), count(*) FROM llm_calls WHERE purpose='vision_look' GROUP BY 1`. Note the accounting nit: vision tokens are billed at the `LLM_PRICE_*` constants like everything else, so running vision on a pricier provider than `AGENT_MODEL` slightly *under*charges.
  - **Prices are PER MODEL now, not per env constant (round 48).** `worker/model_prices.py` (mirrored byte-identical at `backend/model_prices.py`, with `worker/tests/test_model_prices.py` failing if they drift) maps a model id to `(in, cached_in, out, reasoning_separate)`, and every charge and admin cost view prices each `llm_calls` row from **that row's own `model` column**. This is what makes it safe for a free user's turn to run on DeepSeek while a subscriber's runs on Grok. `LLM_PRICE_*` in `worker/config.py` survive only as the fallback for an **unlisted** model. Grok 4.5's cached input is **$0.30/1M, not $2.00** — an earlier comment here said to set it equal to the miss price ("Grok has no caching"), which would have over-charged cached tokens by 6.7x, the identical bug that had just been fixed for DeepSeek.
  - **Reasoning tokens.** `worker/llm.reasoning_tokens()` records them on every row as `response->>'reasoning_out'`, but they are only *charged* where `MODEL_PRICES[...]["reasoning_separate"]` is True — some providers fold thinking into `completion_tokens` (adding it again double-charges), xAI reports it as a separate invoice line. Unlisted models default to folded, because over-charging is the worse error. Verify before flipping a flag: `SELECT model, SUM(completion_tokens), SUM(COALESCE((response->>'reasoning_out')::float,0)) FROM llm_calls WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1` and compare with the provider's two output lines. The admin `/admin/video/costs` response now carries a `by_model` breakdown with both columns.
  - **THE PLANS SELL THREE DIFFERENT MODELS (round 49) — and that copy has to stay true.** The pricing page badges the middle card "MORE INTELLIGENCE" and the top one "FRONTIER INTELLIGENCE", so `worker/llm.agent_client_for(subscribed, plan)` routes three plans to three providers, most specific first: Frontier `ai_max` → `FRONTIER_*`; Pro `ai_pro` → `PAID_*`; Creator/free → `AGENT_MODEL`. **A trial runs its OWN plan's model** — a trial previewing a better model than the plan delivers is the bait-and-switch it was meant to prevent. Every branch falls through when its provider is unconfigured, so a missing key degrades the model rather than 401ing the turn, and `agent_loop` prints a loud line naming the plan when a tier sold on its model is not delivering it. Credits stay correct across the split automatically (per-row `model` pricing, above).
  - **`PAID_BASE_URL` / `PAID_API_KEY` / `PAID_AGENT_MODEL` / `PAID_PLANS` (worker) — the Pro tier's model.** Defaults are **no longer empty**: `https://api.x.ai/v1` + `grok-4.5`, with the key inherited from `VISION_API_KEY`/`IMAGE_API_KEY`/`OPENAI_API_KEY` **only when the base URL matches** (the same rule as `IMAGE_API_KEY`). Round 48 left them empty so shipping was a no-op, which was right for a silent cost optimisation and wrong now that a price tag advertises it — the failure mode is a customer paying $50 for a tier whose distinguishing feature never turned on. `PAID_PLANS` defaults to `ai_pro`; set `ai,ai_pro` to restore round 48's give-it-to-every-subscriber behaviour. A half-set trio counts as OFF.
  - **`FRONTIER_BASE_URL` / `FRONTIER_API_KEY` / `FRONTIER_AGENT_MODEL` / `FRONTIER_VISION_MODEL` (worker) — the $100 plan.** Same defaults (xAI + `grok-4.5`) and the same same-base-URL key inheritance, so **in practice it lights up from the xAI key the worker already needs for vision**. Frontier is the only plan whose **vision** is upgraded too (`llm.vision_client_for`) — that is the half of the promise nobody would notice was missing, since vision has always had its own provider. `llm.vision_available()` returns True for a Frontier turn even if the shared `VISION_*` provider is unconfigured or has been latched blind, and a Frontier-only image 400 must **not** latch `_vision_blind` for the whole process. The plan is published to the **thread** (`llm.set_turn_plan`, cleared in `run_agent_job`'s `finally`) rather than threaded through the eight `ask_vision` call sites — worker lanes are threads, so a module global would hand one user's turn another user's model *and* another user's price.
  - **`AGENT_REASONING_EFFORT` (worker)** — applied from the SECOND loop iteration onward, never the first (iteration 0 is where the model plans the edit; everything after is tool dispatch). Empty default sends no field at all, so DeepSeek cannot 400 on an unknown parameter. Set to `low` on a Grok-backed paid tier to cut what was measured at 70% of output tokens.
  - **Prompt caching changes the charge.** DeepSeek serves a repeated prompt *prefix* from disk cache at **$0.003625/1M — 480× cheaper than a miss**. An agent turn re-sends the same system prompt + ~60 tool schemas every iteration, so most input after the first step is a cache hit; billing it at the miss rate would overcharge a multi-step turn several-fold. So the charge is three-part: `(prompt − cached)×in + cached×cached_in + completion×out`. The cache-hit slice rides in `llm_calls.response->>'cached_in'` (**no new column**), written by the worker's recorder and read by `worker/db.charge_turn_credits`, `ToolContext.running_credits` (the in-turn cap) and the admin cost views — all four must stay in step. A provider that reports no caching yields 0 and prices exactly as before. `LLM_PRICE_CACHED_IN_PER_M` is the fourth constant to change with the model; set it equal to `LLM_PRICE_IN_PER_M` for a provider without caching.
  - **Image generation has its OWN provider** — `IMAGE_BASE_URL` (default `https://api.x.ai/v1`) + `IMAGE_API_KEY`, because DeepSeek ships no image model. The image key is inherited from `OPENAI_API_KEY` **only when both providers are the same URL**; cross-provider it must be set explicitly, and until it is, `generate_image` is *hidden* from the model and its prompt paragraph is stripped (the honest-off contract) instead of 404ing on every call. **So on the DeepSeek default, AI image generation is OFF until you set `IMAGE_API_KEY` to an xAI key on the worker.**
  - **Image generation** auto-detects its backend from `IMAGE_BASE_URL` (not the chat base): on xAI it uses the OpenAI-compatible `/images/generations` with `IMAGE_GEN_MODEL=grok-imagine-image-quality` — **text-to-image only; the round-11 restyle-a-frame / edit modes are NOT available on xAI** (they need DashScope's native endpoint). The `generate_image` tool rejects restyle modes honestly and the agent falls back to a fresh generation. To keep restyling, point `IMAGE_BASE_URL` at DashScope + set `IMAGE_EDIT_MODEL`. **The image model id has 404'd silently TWICE** (`grok-2-image` was never valid; `grok-2-image-1212` was valid once then deprecated 2026-02-24, so the round-33 "fix" swapped one 404 for another and was never live-checked). **If image gen 404s again the id is wrong for your xAI team/tier — check `console.x.ai → Models`, set `IMAGE_GEN_MODEL` on the Render worker to the exact id, and keep `IMAGE_PRICE_USD` (default 0.055, tracking the `-quality` tier) in sync with its real per-image price.** Every attempt is logged to `llm_calls` (purpose `image_gen`) with the exact model + error, so a bad id shows up immediately in the admin Model I/O tab — run `SELECT model, count(*) FILTER (WHERE response->>'error' IS NOT NULL), count(*) FROM llm_calls WHERE purpose='image_gen' GROUP BY 1` after any change.
- **`DEEPGRAM_API_KEY`** (worker) — the transcriber. Deepgram nova-3 is the default whenever this is set; faster-whisper is the automatic fallback and records a warning on the index when it runs. Proven in prod: on a real 19.3-min video, transcription went from **1742s (whisper medium) to 1.53s**, taking the index from 47 min to 16.5 min, and found 45 words whisper missed. `TRANSCRIBER=whisper|deepgram` forces either side. **Switching providers changes index output — bump `PIPELINE_VERSION` in `worker/schemas.py` (a code constant, one commit, both services pick it up together).** It is deliberately NOT an env var anymore: the per-service envs drifted Jul 16-17 2026 and every project open triggered a full re-index in an infinite loop (the backend now also caps self-heal re-indexes at 2 per project per 6h, so no mismatch can starve the worker again). Remove any stale `PIPELINE_VERSION` env from Render — it is ignored.
- **`PEXELS_API_KEY` / `PIXABAY_API_KEY`** (worker, round 44) — stock b-roll. With neither set, `search_stock`/`add_stock_media` are **hidden from the model entirely** and the prompt's stock paragraph is stripped (same honest-off contract as `record_website`/`generate_sfx`), so the agent never offers footage it cannot fetch. Set **either** to turn the capability on; Pexels is tried first and Pixabay is the fallback. Both are free keys. The chosen rendition is the *smallest* one that still covers the project's output frame, and search orientation follows the project aspect, so a 9:16 edit gets vertical footage. Caps: `STOCK_MAX_VIDEO_BYTES` (90 MB), `MAX_STOCK_PER_TURN` (6).
- **`UPLOAD_PARALLELISM`** (worker, default 8) — index artifacts (proxy, wav, thumbnails, contact sheets) are PUT to storage concurrently instead of one after another. They are independent objects blocked in sockets, so this is pure network concurrency on a box whose CPU is the real bottleneck; serial uploads were ~24.5s of an index (~40% of it) sitting directly between the user and "your video is ready". The proxy/wav still **fail the job** on error; thumbs and sheets still degrade to a warning, and sheet keys stay in sheet order (the agent addresses them positionally). Set to 1 to restore the old sequential behaviour.
- **Other worker envs** (background worker service): `DATABASE_URL`, S3/R2 creds, plus optional tuning — `WORKER_MEDIA_SLOTS`/`WORKER_INDEX_SLOTS` (raise to isolate previews from long finals/indexes), `FULL_INDEX_MAX_DURATION_S` (full-index-in-context threshold), `MAX_VISION_SHEETS` (vision-call cap), `PROXY_HEIGHT`/`PROXY_CRF` (index proxy; 540p — it is an analysis/preview artifact, finals render from the ORIGINAL). There is **no flat per-turn spend ceiling** — a turn spends what the user's balance + `AGENT_TURN_BUDGET_GRACE` allows, bounded by `AGENT_TURN_TIMEOUT_S`. The whisper model is baked into the Docker image via `--build-arg WHISPER_MODEL` (**default `medium`**) — keep the build arg in sync with the runtime `WHISPER_MODEL` env. `CLEAN_MAX_SOURCE_S` (default 600) bounds the round-39 erase: repainting burned-in text/objects decodes and re-encodes every frame inside the agent turn, so past that source length the agent refuses honestly (offering blur/crop) instead of dying at `AGENT_TURN_TIMEOUT_S`.

- **Removing burned-in captions / objects (round 39, `worker/inpaint.py`).** `find_burned_text` measures the rectangles from the frames (OpenCV stroke response + temporal voting) — the agent never estimates a box from a contact sheet again — and `erase_burned_text` / `erase_region` REPAINT those pixels (temporal background plate where the shot is steady, `cv2.inpaint` elsewhere) into a cleaned copy of the source. The EDL's `source_clean` points at it and the renderer reads it: full-res for finals, a cleaned 540p proxy for previews. Two things to know: a caption on a solid bar auto-escalates to whole-rectangle repaint **and grows the box to the bar's real extent** (otherwise inpaint reconstructs the hole from more bar), and every erase re-derives from the **original**, never from an already-cleaned file, so undo is exact and repaints never compound. Each distinct region set is one cached R2 object (`cleaned/<pid>/<fp>.mp4` + `_proxy`), kept because older EDL versions still reference it.

- **A transition marks a SCENE CHANGE, not every cut (round 48, `timeline.transition_junctions`).** `set_transitions` used to apply its junction effect at every cut. After `cut_silences` a single talking-head take has one junction per removed pause — 45 on a real 3.9-minute customer job — and essentially all of them are **jump cuts inside one continuous shot**, which are supposed to be invisible. That edit shipped with 45 whip pans through one shot and the user called it broken within minutes. `TransitionSpec.scope` now defaults to `"scene"`: a transition lands only where the two sides come from different indexed **shots**, or where an insert splices in (`"every_cut"` is opt-in, right for a montage of separate clips). On that same customer's EDL the resolver goes **45 → 3**, at the real shot changes. One resolver serves both the tool's reported count and the renderer's emission, so the sentence the user reads and the video they watch cannot disagree; if *nothing* qualifies the tool refuses outright and says why rather than applying 45 effects. No shots in the index → every junction (the honest fallback is the old behaviour, never silently zero). **Cache:** renders are keyed on (project, variant, EDL *version*), not content, so `TRANSITION_VERSION` + a `trans_v` stamp bust the stale ones — mirrored in `backend/routes/video.py` (which gates whether the studio even *asks* for a re-render) with a worker test asserting the two constants match, exactly as `OUTRO_VERSION` does.

- **A video is a legal SOUND source (round 47, `agent_tools._audio_from_clip`).** Users deliver songs as videos — a TikTok/Reel download is the only file they have. Passing a `[video_clip]` storage_key to `add_music` / `add_sfx` / `add_voiceover` / `get_audio_analysis` lifts its audio out (`media.extract_audio_track`: AAC stream-copied to `.m4a`, picture never decoded) into a real `music` asset, and **the EDL stores the RESOLVED key, never the video's** — so callers must use `track["storage_key"]`, not what they were handed. The clip's picture appears nowhere; every tool result says so. Cached per source **sha** as well as key, because the user who hits this re-uploads the same file repeatedly (one did, four times, while being refused). A silent clip is the only honest no. Before this, every audio tool answered "not a music asset here" and the agent told users to convert the file themselves.

- **End card.** `worker/brand/endcard.png` is built by `worker/tools/build_endcard.py` from `worker/brand/robot.png` (the site's Rive navbar robot, rendered to PNG — see `worker/brand/README.md`). Changing its look means bumping `OUTRO_VERSION` in **both** `worker/config.py` and `backend/routes/video.py` — a worker test asserts the two constants match, because a mismatch leaves finished exports serving the old card forever.
- **Executor timeouts are a PAIR (round 48).** The Cloud Run service runs `--timeout 1800` and the dispatcher's `REMOTE_EXECUTOR_TIMEOUT_S` is **1500**, a code constant in `worker/config.py` — deliberately not a per-service env var, because a paired constant split across two services' envs is exactly how `PIPELINE_VERSION` drifted. The dispatcher must always give up FIRST: if it times out after the executor, a wedged job is requeued while the original keeps burning 8 vCPU beside the retry. It was 3600/3300, and two wedged finals ran the 3600 cap to the second on a service billed per instance-second; the longest healthy job on this hardware is ~300s. `worker/tests/test_executor_timeouts.py` asserts the ordering against the `--timeout` in `DEPLOY_EXECUTOR.md`. **If `REMOTE_EXECUTOR_TIMEOUT_S` is set as an env var on Render, delete it** — the code default is now the authority.
- **Do NOT raise Cloud Run `--concurrency` on its own.** It was proposed as a cost/throughput win and it is currently a **no-op**: the dispatcher gates index work behind `WORKER_INDEX_SLOTS` (default 1, `worker/main.py`) and each slot makes one *blocking* call, so the executor never receives two concurrent index requests. `--max-scale 5` already gives two different users two separate 8-vCPU instances, so the parallelism is already there; concurrency only *packs* jobs onto one instance — halving each job's CPU and putting two original videos in the same 16 GiB **in-memory** `/tmp`. If you ever do want it, all three move together: `--concurrency 2` + `WORKER_INDEX_SLOTS=2` + `--memory 32Gi`, tested against your largest video first.
- **Worker capacity is the live ceiling.** `INDEX_SLOTS=1` serializes every customer's analysis: a second uploader waits for the first to finish (one real customer queued **63 minutes** behind another user's video and left). On the current ~1 vCPU box a 19-min upload costs ~16 min to index and ~14 min per preview render, one at a time — and the proxy encode is now ~88% of index time. Raising slot counts on 1 vCPU makes both jobs slower and doubles memory; the real fix is a bigger instance.

---

### Blueprint Registration (app.py)

```python
app.register_blueprint(auth_bp,             url_prefix='/auth')
app.register_blueprint(verify_bp,           url_prefix='/verify')
app.register_blueprint(paddle_checkout_bp)
app.register_blueprint(paddle_webhook)
app.register_blueprint(admin_bp,            url_prefix='/admin')
app.register_blueprint(google_auth_bp,      url_prefix='/auth')
app.register_blueprint(github_bp,           url_prefix='/auth')
app.register_blueprint(deploy_bp)
app.register_blueprint(supabase_bp,         url_prefix='/supabase')
app.register_blueprint(stripe_bp,           url_prefix='/stripe')
app.register_blueprint(ai_proxy_bp)
app.register_blueprint(planner_bp)
```

---

### Frontend Routes

| Path | File | Auth Required |
|------|------|---------------|
| `/` | `app/page.js` → LandingPage (or redirect to /studio if logged in) | No |
| `/home` | `app/home/page.js` → LandingPage | No |
| `/login` | `app/login/page.js` | No |
| `/enter-password` | `app/enter-password/page.js` | No |
| `/register` | `app/register/page.js` | No |
| `/verify` | `app/verify/page.js` | No |
| `/change-password` | `app/change-password/page.js` | No |
| `/reset-password` | `app/reset-password/page.js` | No |
| `/account` | `app/account/page.js` | Yes (middleware) |
| `/legal` | `app/legal/page.js` | No |
| `/paddle-checkout` | `app/paddle-checkout/page.js` | No |
| `/subscribe` | `app/subscribe/page.js` | No |
| `/studio` | `app/studio/page.js` | Yes (middleware) |
| `/admin` | `app/admin/page.js` | Yes (middleware) |
| `/templates` | `app/templates/page.js` | No |
| `/purchase-success` | `app/purchase-success/page.js` | No |
| `/github-callback` | `app/github-callback/page.js` | No |
| `/google-callback/:code` | `app/google-callback/[code]/page.js` | No |
| `/docs` | `app/docs/page.js` | No |

---

### How to Push Changes

**Frontend:**
```bash
cd ~/Documents/Startup/frontend-next
git add src/app/whatever/page.js
git commit -m "description"
git push origin main
# Vercel auto-deploys in ~1-2 minutes
# IMPORTANT: verify build succeeds on Vercel dashboard — builds can fail on SSR issues
```

**Backend:**
```bash
cd ~/Documents/hustler-server
git add backend/routes/auth.py  # or whatever file
git commit -m "description"
git push origin main
# Render auto-deploys in ~3-5 minutes
```

**Common gotchas:**
- Always verify files are saved to disk before committing. Run `git diff <filename>` to confirm.
- If Vercel build fails, check for `localStorage`/`window` usage outside `useEffect` — this is the #1 cause of Next.js build failures.
- If you add a page that uses `useSearchParams`, wrap it in `<Suspense>`.
- Clear `.next` cache locally if dev server behaves strangely: `rm -rf .next && npm run dev`

---

### How to Make Database Changes

**To run a query:** Use the Render Shell tab on the backend service:
```bash
psql $DATABASE_URL -c "YOUR SQL HERE"
```

**To delete a user cleanly** (must delete dependent records first):
```sql
DELETE FROM job_credits WHERE user_id = (SELECT id FROM users WHERE email = 'x@x.com');
DELETE FROM jobs WHERE user_id = (SELECT id FROM users WHERE email = 'x@x.com');
DELETE FROM email_codes WHERE email = 'x@x.com';
DELETE FROM code_request_logs WHERE email = 'x@x.com';
DELETE FROM google_auth_codes WHERE email = 'x@x.com';
DELETE FROM users WHERE email = 'x@x.com';
```

**To add a column:** Add it to `models.py` in the `init_db` function using `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, then redeploy.

---

### Things to Be Cautious Of

1. **Never edit `~/Documents/Startup/backend/`** — dead folder
2. **Never edit `~/Documents/Startup/frontend/`** — old CRA frontend, superseded by `frontend-next/`
3. **CORS is handled manually** in `app.py` with `before_request` and `after_request` hooks — don't remove these or the frontend will break
4. **All frontend API calls must go through `/api-backend/`** proxy (configured in `next.config.js`) — never call the Render URL directly from frontend code (except template preview iframes which must use the direct URL to avoid Vercel timeouts)
5. **Brevo IP whitelist** — if emails stop working, Render's IP may have changed and needs to be re-whitelisted at `app.brevo.com/security/authorised_ips`
6. **Persistent disk** — the `outputs/` folder is on a persistent disk. `start.sh` copies templates to it on boot. If you ever reset the disk, templates need to be recopied
7. **node_modules** — deleted after every build intentionally. Don't add them back to persistent storage
8. **`auth.py` uses `token_required` and `get_db()`** — not `jwt_required` or `get_db_connection()`. Always use the correct function names when adding new routes
9. **Database foreign keys** — `job_credits` references `users`, `jobs` references `users`. Always delete child records before deleting a user
10. **Render shell is ephemeral** — files you create there outside the persistent disk or repo won't survive a redeploy
11. **Safari blocks query params on redirects** — never pass tokens or auth codes as query parameters in OAuth flows. Use URL path segments instead (e.g., `/google-callback/{code}`)
12. **Vercel build failures** — the #1 cause is `localStorage`/`window`/`document` used outside `useEffect` or without a `typeof window !== "undefined"` guard. Always check the Vercel dashboard after pushing.
13. **Google OAuth client** — configured in Google Cloud Console under "The Hustler Bot" project. Authorized redirect URI must point to the backend: `https://entrepreneur-bot-backend.onrender.com/auth/google/callback`. JavaScript origins must include `https://valmera.io` and `https://www.valmera.io`
14. **Admin access** — admin features (Admin button in navbar, admin dashboard) are gated to `thevalmera@gmail.com`
15. **Domain change** — all references to `thehustlerbot.com` should now use `valmera.io`. Key places: `FRONTEND_URL` env var on Render, Google Cloud Console OAuth settings, Brevo sender domain
16. **Auth tokens must use `setToken()`/`removeToken()`** from `@/utils/auth` — this sets both localStorage (for API calls) and a cookie (for middleware route protection). Raw `localStorage.setItem("token", ...)` will break the middleware auth check.
17. **`next.config.js` vs `next.config.ts`** — only `next.config.js` should exist. If a `.ts` version appears (e.g., from scaffold), delete it immediately — it will override your JS config silently.
18. **`useSearchParams` pages** — any page using `useSearchParams()` must export the component wrapped in `<Suspense>`, otherwise the production build will fail.

---

### SEO Configuration

All SEO is managed in `src/app/layout.js`:
- Title, description, keywords
- Open Graph + Twitter Card metadata
- Google Search Console verification tag
- Google Analytics (gtag.js) via `next/script`
- JSON-LD structured data (WebApplication schema)
- Canonical URL, icons, manifest

Additional files:
- `public/sitemap.xml` — lists all public routes
- `public/robots.txt` — blocks private routes (/studio, /account, /admin, etc.)
- Google Search Console sitemap last submitted: April 2, 2026


Always commit with: git config user.name "ABO3SKRALMASAOODI" and git config user.email "shmarymuslim@gmail.com" — never commit as Claude or noreply@anthropic.com. Vercel Hobby plan blocks deploys from unrecognized committers.

Don't modify models.py for schema changes. I manage the database schema directly through Render shell commands. Only use models.py for its existing helper functions like get_db() and update_user_subscription_status().
heres the url:

render external url: postgresql://the_hustler_bot_user:ajcmtxLo05sonfhqiTjA4kRAegN099DO@dpg-d0vgraggjchc7385l1u0-a.oregon-postgres.render.com/the_hustler_bot 
 