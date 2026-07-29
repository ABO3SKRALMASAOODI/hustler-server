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
- **Bonus credits (`credits.FREE_GRANT_CREDITS` = 50, one-time):** The free taste. Granted at registration by both signup paths, never refilled. Spent after daily, before monthly.
- **Monthly credits:** Set when user subscribes (Plus 800 / Pro 2400, plus retired Ultra 5000 / Titan 10000 / Ace 30000 for grandfathered subs). Wiped and refreshed on each billing renewal via Paddle webhook; **clawed back to 0 on cancel/refund**. Spent last.

Key columns in `users` table: `credits_daily`, `credits_bonus`, `credits_monthly`, `credits_balance`, `credits_daily_reset`, `credits_monthly_limit`. The **worker** charges credits after each agent turn (`worker/db.charge_turn_credits`, ~1 credit = $0.01 of model cost, min 1). Values are authoritative in code (`backend/credits.py` `PLAN_MONTHLY_LIMITS`, `backend/routes/paddle_webhook.py` `PLAN_CREDITS`, `worker/config.py`), not here.

---

### Subscription Plans

**Creator (`ai`)**, **Pro (`ai_pro`)** and **Frontier (`ai_max`)** are purchasable (`PURCHASABLE_PLANS` in `paddle.py`). All carry a **3-day free trial configured on the Paddle PRICE**, so checkout creates the subscription immediately with status `trialing` and charges nothing until day 3 — which is why `is_subscribed` is true from day zero and why the admin cohort funnel counts **trial** and **paid** as two separate stages (`trial_status` from `backend/trial_state.py`; the gap between the two columns *is* the trial conversion rate). Plus/Pro(legacy)/Ultra/Titan/Ace and `mcp` are retired from the UI and checkout but stay in `PLANS`/`PLAN_CREDITS` so grandfathered subscribers keep working.

| Plan             | Price | Monthly Credits | Model | Status |
|------------------|-------|-----------------|-------|--------|
| Free             | $0    | **50 one-time, spendable, never refills** (round 50) | `AGENT_MODEL` (standard) | live |
| Creator (`ai`)   | $30/mo, $300/yr | 2,000 | `AGENT_MODEL` (standard) | live, purchasable |
| Pro (`ai_pro`)   | $50/mo, $500/yr | 4,000 | `AGENT_MODEL` — same as Creator, "more room" | live, purchasable |
| Frontier (`ai_max`) | $100/mo, $1000/yr | 10,000 | `FRONTIER_*` — the only model step | live, purchasable |
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

### The free taste, and the wall as a chat message (round 50)

**`FREE_GRANT_CREDITS` is 50, and they are SPENDABLE.** Round 49 set it to 0 for a real reason — a bar reading 120/120 on an account that could not spend one of them is a number the next click refuses — but the outcome was that nobody could try the editor at all. So the fix is not to hide the number, it is to make it true: 50 credits is **$0.25** of model spend per signup (× `USD_PER_CREDIT`), a few real agent turns on the visitor's own footage, and the gate stays open for exactly as long as the pool lasts. Existing accounts were topped up to the same 50 by `migrations/010_free_taste_credits.sql` — **176 rows, applied to prod Jul 27 2026** — so "all users" means all users, not just post-deploy ones. Subscribers are excluded on purpose: a **trialling** account is `is_subscribed`, and 50 extra credits would quietly break the 10% cap that bounds what an unconverted trial can cost.

**`plan_gate.needs_plan` is now ONE rule: no subscription AND `credits_balance < 1`.** The wall is 1.0, not 0, because `min_credits=1.0` everywhere that spends — a fractional remainder would otherwise open a gate the credit check then closes. Two earlier rules are gone: `GATE_START`/`created_at` (it only separated two groups that got a *different error message* for the same wall, now that everyone holds the same 50) and `FREE_TASTE_TURNS` (counting turns priced a 6-second clip like a 20-minute documentary; counting credits prices what a turn actually costs). It still **fails open** — a DB hiccup must never lock out a paying customer. `backend/tests/test_plan_gate.py` pins all of it.

**THE GATE STILL HANGS OFF `indexed`**, exactly like the credits gate below it, and both live *below* the idempotency and rate-limit checks in `post_message`. It shipped unconditional for an afternoon in round 49, so a new account that typed "hi" into an **empty** project got 402 `plan_required` and a paywall headed *"I've watched it."* Pre-index chat is the concierge: cheap, rate-limited, never charged — **let it answer**.

**THE CARD IS A MESSAGE IN THE CHAT, not a modal.** `PlanCTACard` was a full-screen dialog that took the studio away and had to be dismissed; it now renders inline in the conversation, under the bot avatar, where the reply would have gone — because running out of credits *is* the reply to what the user just asked for. Three sources reach it and all three set the same `chat_messages.meta` flags, so there is one wall with one design: the 402 in `post_message`, the worker's budget stop (`agent_loop`), and the concierge's blank-canvas refusal. Variant is read off the flags — `trial_cap_reached` → `trial_spent`, `free_trial_exhausted === false` → `plan_spent` (a subscriber's pool *does* come back — say when), else `free_spent`. The message's own text becomes the card's `note`, so "the edits I finished are saved and previewed below" survives instead of being replaced by marketing. The grant is quoted from the server (`free_credits` on the 402, `plan_limit` from `/credits`) — **never a literal in the frontend**. The round-49 `video_ready` card that popped the moment indexing finished is **gone**: with 50 spendable credits, a freshly indexed video is something the visitor can now go and edit.

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

### Lifecycle email (round 49 redesign)

`newsletter_content.py` owns the shell, a set of **design blocks** (`eyebrow`,
`h1`, `p`, `card`, `feature`, `numbered`, `stats`, `price_row`, `cta`) and the
default templates, which are now **generated from those blocks** rather than
hand-typed as six giant HTML strings. The stored default is still a plain
string, so the admin editor is unaffected. Design language is the pricing cards:
`#0b0b0b` surfaces, `#1e1e1e` hairlines, monospace micro-labels, red accent, and
a **white pill CTA** (the emails were using a red button, so arriving at
/subscribe from an email looked like a different product).

**The header wordmark is TEXT.** It used to be one 220x68 PNG containing robot +
wordmark, so the email opened with an empty box until it loaded — ~1.1s for 15KB
from valmera.io, plus Gmail's image proxy, ≈3 seconds of blank branding. Now the
name renders with the HTML and the only fetch is `public/email-robot.png`
(**5.9KB**, the Rive robot trimmed and sized for a 44px box at 2x). Regenerate it
by rendering a `.riv` with Playwright + `@rive-app/canvas` and trimming to the
alpha bbox — see the round-49 memory.

**Why no 50% mail had ever sent:** `offers.py` and the `offer_50` campaign only
reached production on Jul 27 2026 (round 48 sat committed-but-unpushed for a
day). The engine itself was healthy the whole time — 222 sends across the other
five campaigns. Nothing was broken. `run_daily_tick` is **once per day**
(`already_ran_today`) at `send_hour_utc` (default 15:00), so the first offer send
is the next tick; 55 accounts were eligible at the time of writing. Verify with
the `_eligible('offer_50')` predicate rather than guessing — the binding clauses
are `created_at <= NOW() - 1 day`, `trial_started_at IS NULL`, and `NOT_TODAY`.

### Required production env (Render)

- **`PADDLE_WEBHOOK_SECRET`** — MUST be set (Paddle → Developer tools → Notifications). The webhook fails OPEN when unset: an unsigned POST is accepted, so anyone could forge `subscription.created` and grant themselves a plan. Code already HMAC-verifies when present — this is pure config.
- **`SECRET_KEY`** — MUST be a real secret. Falls back to the literal `"supersecretkey"` if unset, which makes every JWT forgeable → full account takeover. Verify it is set on both Render services; rotate if in doubt.
- **LLM provider = DeepSeek V4 Pro (default, since Jul 26 2026).** The whole LLM stack (agent tool-calling, vision, concierge) is OpenAI-compatible. You set **only `OPENAI_API_KEY`** (a DeepSeek key) on both the backend web service and the worker — the defaults already select `OPENAI_BASE_URL=https://api.deepseek.com`, `AGENT_MODEL=deepseek-v4-pro`, `VISION_MODEL=deepseek-v4-pro`. To use a different provider set `OPENAI_BASE_URL`/`AGENT_MODEL`/`VISION_MODEL` (for the previous stack: `https://api.x.ai/v1` + `grok-4.5` + prices `2.0`/`6.0`).
  - **Vision has its OWN provider — `VISION_BASE_URL` (default `https://api.x.ai/v1`) + `VISION_MODEL` (default `grok-4.5`) + `VISION_API_KEY`.** **NO DeepSeek V4 tier accepts images**, pro included: the chat API rejects an `image_url` content part at the JSON layer (`400 … unknown variant \`image_url\`, expected \`text\``). This doc previously claimed pro was multimodal and vision inherited `AGENT_MODEL` — so from **13:06 UTC Jul 26 2026 every `look_at`, `look_at_asset` and preview self-check 400'd, 59 in a row**, and the agent asked users to describe their own footage to it. The key follows the same rule as `IMAGE_API_KEY`: inherited only from a provider on the SAME base URL (so an xAI `IMAGE_API_KEY` on the worker lights up vision too), never handed across providers. With no key for it, `vision_available()` is False and the tools say "visual inspection unavailable" — honest-off, not broken. A provider that 400s on an image part also **disables vision for the process** on the first such error rather than repeating a doomed call per look. Verify after any provider change: `SELECT model, count(*) FILTER (WHERE response->>'error' IS NOT NULL), count(*) FROM llm_calls WHERE purpose='vision_look' GROUP BY 1`. Note the accounting nit: vision tokens are billed at the `LLM_PRICE_*` constants like everything else, so running vision on a pricier provider than `AGENT_MODEL` slightly *under*charges.
  - **Prices are PER MODEL now, not per env constant (round 48).** `worker/model_prices.py` (mirrored byte-identical at `backend/model_prices.py`, with `worker/tests/test_model_prices.py` failing if they drift) maps a model id to `(in, cached_in, out, reasoning_separate)`, and every charge and admin cost view prices each `llm_calls` row from **that row's own `model` column**. This is what makes it safe for a free user's turn to run on DeepSeek while a subscriber's runs on Grok. `LLM_PRICE_*` in `worker/config.py` survive only as the fallback for an **unlisted** model. Grok 4.5's cached input is **$0.30/1M, not $2.00** — an earlier comment here said to set it equal to the miss price ("Grok has no caching"), which would have over-charged cached tokens by 6.7x, the identical bug that had just been fixed for DeepSeek.
  - **Reasoning tokens.** `worker/llm.reasoning_tokens()` records them on every row as `response->>'reasoning_out'`, but they are only *charged* where `MODEL_PRICES[...]["reasoning_separate"]` is True — some providers fold thinking into `completion_tokens` (adding it again double-charges), xAI reports it as a separate invoice line. Unlisted models default to folded, because over-charging is the worse error. Verify before flipping a flag: `SELECT model, SUM(completion_tokens), SUM(COALESCE((response->>'reasoning_out')::float,0)) FROM llm_calls WHERE created_at > NOW() - INTERVAL '1 day' GROUP BY 1` and compare with the provider's two output lines. The admin `/admin/video/costs` response now carries a `by_model` breakdown with both columns.
  - **TWO MODEL TIERS SHIP, NOT THREE (round 49) — and that is a correction.** `worker/llm.agent_client_for(subscribed, plan)` resolves most-specific-first: Frontier `ai_max` → `FRONTIER_*`; anything in `PAID_PLANS` → `PAID_*` (**empty by default**); everything else → `AGENT_MODEL`. So as shipped: free/Creator/Pro on DeepSeek, **Frontier is the only plan that changes the model**. Pro shipped for one afternoon badged "MORE INTELLIGENCE" while resolving to the *same model id as Frontier* — `grok-4.5` is the strongest model anything in this stack points at, so "stronger than Creator" and "the strongest we have" were the same string, which made the $100 card's whole argument false. Pro went back to selling **room**. `worker/tests/test_model_prices.py` asserts the routing matches the cards, including that `PAID_PLANS` ships empty. **A trial runs its OWN plan's model** — previewing a model the plan does not deliver is the bait-and-switch it was meant to prevent. Every branch falls through when its provider is unconfigured, so a missing key degrades the model rather than 401ing the turn, and `agent_loop` prints a loud line naming the plan when a tier sold on its model is not delivering it.
  - **VISION IS `grok-4.5` FOR EVERY PLAN, including free** — it has to be, because no DeepSeek V4 tier accepts images. So *do not* write copy implying Frontier sees better than Creator; it reasons better about what it sees. Frontier's vision goes through its own client and key, but `FRONTIER_VISION_MODEL` defaults to the same id as `VISION_MODEL`.
  - **`PAID_BASE_URL` / `PAID_API_KEY` / `PAID_AGENT_MODEL` / `PAID_PLANS` (worker) — an OFF lane, kept wired.** The provider defaults to `https://api.x.ai/v1` + `grok-4.5` with the key inherited from `VISION_API_KEY`/`IMAGE_API_KEY`/`OPENAI_API_KEY` **only when the base URL matches**, so promoting Pro back into a model tier is one variable (`PAID_PLANS=ai_pro`) rather than four. Do that only once Frontier has something genuinely stronger to run — otherwise you recreate two identical tiers with different badges. Whatever `PAID_AGENT_MODEL` becomes **must** be in `model_prices.MODEL_PRICES` or it silently bills at the `LLM_PRICE_*` fallback.
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

- **TASTE IS A CHECK, NOT A HOPE (round 52, `worker/taste.py`).** Every audit before this one asked whether an edit was CORRECT (no mid-word cut, no repeated phrase, captions with words to burn). Nothing asked whether it was any GOOD — and on Jul 27 2026 a preacher's reel, a Valorant montage, a plant timelapse, a soda taste-test and a gym reel all came back with the identical edit: cinematic grade + vignette + 1s fade from black + punch zooms on the loudest words + dip-to-black at every cut + music ducked + -14 LUFS. **Two of those briefs said in writing "sin pantallas negras / no black screens at the start" and got a fade from black anyway.** `taste.critique()` is the craft reviewer in the same shape as `audit.py` (pure functions over EDL + index, no LLM, no I/O); `render_preview` stamps a **TASTE AUDIT** into its result beside the mid-word and repetition audits, and the prompt makes fixing it non-optional. Every check is a defect that actually shipped: fade in/out of black on short-form, a zoom before 1.2s, >1 zoom per 11s, adjacent zooms, N identical zooms, a junction effect on jump cuts inside one shot, >1 sfx per 8s, two sfx inside 0.35s, music that stops early or never ducks under speech, >2 whole-programme stylize passes, two aggressive devices at once, a text card burning under captions, sub-0.6x slow motion, a vertical edit past 3 minutes. **It never argues with an instruction** — `user_asked` only ever SUPPRESSES a finding. The judgement behind it is the prompt's **THE DIRECTOR'S EYE** section: name the format first, the first second is the whole edit, restraint is the look, pace is editing, end on purpose, and a real recipe per format (talking-head / sermon / screen demo / montage / music video / timelapse / vlog) instead of one recipe for everything.

- **Round 52 also shipped the five things users asked for that did not exist.** `add_freeze_frame` (freeze the picture, blur + darken the still, hold big centred words on it — the "perla"/power-phrase move three users asked for in one day and got a `dream_blur` over LIVE footage instead; it is a real insert, so captions never land on it and the text is `anchor_insert`-bound like a title card). `set_caption_fixes` ([wrong, right] pairs applied to the burned TEXT only, timings untouched, same word count both sides — "dios"→"Dios", a misheard name→the right one). `enhance_video` (unsharp + optional hqdn3d: the answer to "make it clearer/sharper/HD" that five people asked for and got a colour grade; it CANNOT add resolution and says so), plus stylize kinds `sharpen`/`denoise`/`motion_blur` (real `tmix` frame blending) and `stabilize` (`deshake`). `auto_reframe` now **measures the subject in the pixels first** (`worker/subject.py`, OpenCV Haar faces → detail-energy centroid), so a 9:16 crop follows the speaker with no vision provider at all — vision is the fallback now, not the only route. `beat_align_cuts` reads the library track's **offline** tempo from `music/features.json` (`music_library.measured_tempo`) before refusing: "Abducted" scores 0.44 in-turn against the 0.5 gate and **0.62** in the sidecar that has always shipped in the image. `list_music_library` marks tracks the user already got in their OTHER projects (`db.music_used_by_user`), because "do not use the exact background music as previous projects" is a real sentence a real customer typed. Captions also lift clear of the platform's bottom furniture on vertical output (`captions.bottom_margin_v`, 13% — landscape untouched).

- **Renders pin their own clock (round 52).** Both render paths pass `-fps_mode cfr -r {fps}`, so a rendered file's length comes from the filtergraph's TIMESTAMPS and not from whatever frame rate the last filter link advertises. Two finals failed `_verify_render` as the wrong length on Jul 27 (102.40s for a 93.50s edit; 47.11s for 45.38s) while their PREVIEWS were exact, and one user pressed Download **ten times**. The pin is correct and stays, but **round 56 established it was not the cause of that family** — see the next bullet; the genuinely load-bearing thing round 52 shipped was `renderer._stream_report`, which logs per-stream durations, rates and frame counts on any mismatch and is what finally identified it.

- **A CONCAT SEGMENT IS AS LONG AS THE EDL SAYS IT IS — and dev ffmpeg does not agree with prod's (round 56, `renderer._normalize_video`).** A real customer's export failed verification three times at **158.58s for a 150.48s edit**, and the message was the whole of what he got and the whole of what we got. The cause is the per-segment `fps` filter: on **ffmpeg 5.1.9** — what Debian bookworm installs, so what the executor image runs — `fps` applied after a `setpts=PTS-STARTPTS` emits frames PAST the segment's real content, and `concat` then places the next segment at that longer duration. **~+0.6s per segment, compounding** (14 spans → +8.1s). `fps` is not decoration; it is what makes the concat legal when kept spans, a looped PNG title card and a clip from another file all arrive at different rates. So each normalized block is now bounded to its own program length (`trim=end=<dur>,setpts=PTS-STARTPTS`, **after** `fps` — before it does nothing). Three properties hid this for a day of forensics, and every one of them is a lesson:
  - **ffmpeg 8.1.2 renders the identical graph correctly.** It never reproduced on the dev Mac, which is why round 52 read the same symptom as a clock problem and closed the wrong half. The stream report disproves the clock reading outright: `r == avg == 29.601` exactly, and **240 extra FRAMES** — a longer programme, not a stretched one.
  - **Video alone is correct even on 5.1.** The stretch appears only when audio shares the concat, because that is when the padded video is what the segment's length is taken from.
  - **Preview renders from our proxy, final from the customer's original**, so it read as a property of his file. It is not — ordinary 29.6fps WhatsApp video, uniform PTS, no gaps.
  The fix is unconditional, never version-sniffed: a version-conditional filtergraph would mean dev and prod render *different graphs*, and this bug exists precisely because they already run different ffmpeg builds. On 8.1 the trim is a no-op. Verified against the customer's real EDL and real source on **both** builds — 150.60s on 5.1.9, 150.53s on 8.1.2, for 150.48s expected. The cheap path (no inserts/reframe/zoom/speed) never normalizes, emits no per-segment fps, and still renders byte-identically. **To reproduce anything like this again: run it in the executor's own image via Cloud Build** (`gcloud builds submit` with the executor image as the step) — a dev Mac's ffmpeg is not the ffmpeg that makes your customers' videos.

- **A transition marks a SCENE CHANGE, not every cut (round 48, `timeline.transition_junctions`).** `set_transitions` used to apply its junction effect at every cut. After `cut_silences` a single talking-head take has one junction per removed pause — 45 on a real 3.9-minute customer job — and essentially all of them are **jump cuts inside one continuous shot**, which are supposed to be invisible. That edit shipped with 45 whip pans through one shot and the user called it broken within minutes. `TransitionSpec.scope` now defaults to `"scene"`: a transition lands only where the two sides come from different indexed **shots**, or where an insert splices in (`"every_cut"` is opt-in, right for a montage of separate clips). On that same customer's EDL the resolver goes **45 → 3**, at the real shot changes. One resolver serves both the tool's reported count and the renderer's emission, so the sentence the user reads and the video they watch cannot disagree; if *nothing* qualifies the tool refuses outright and says why rather than applying 45 effects. No shots in the index → every junction (the honest fallback is the old behaviour, never silently zero). **Cache:** renders are keyed on (project, variant, EDL *version*), not content, so `TRANSITION_VERSION` + a `trans_v` stamp bust the stale ones — mirrored in `backend/routes/video.py` (which gates whether the studio even *asks* for a re-render) with a worker test asserting the two constants match, exactly as `OUTRO_VERSION` does.

- **A video is a legal SOUND source (round 47, `agent_tools._audio_from_clip`).** Users deliver songs as videos — a TikTok/Reel download is the only file they have. Passing a `[video_clip]` storage_key to `add_music` / `add_sfx` / `add_voiceover` / `get_audio_analysis` lifts its audio out (`media.extract_audio_track`: AAC stream-copied to `.m4a`, picture never decoded) into a real `music` asset, and **the EDL stores the RESOLVED key, never the video's** — so callers must use `track["storage_key"]`, not what they were handed. The clip's picture appears nowhere; every tool result says so. Cached per source **sha** as well as key, because the user who hits this re-uploads the same file repeatedly (one did, four times, while being refused). A silent clip is the only honest no. Before this, every audio tool answered "not a music asset here" and the agent told users to convert the file themselves.

- **THE TIMELINE'S ARTWORK IS ONE JOB, AND WAVEFORMS ARE NOT STORAGE OBJECTS (round 54, `worker/filmstrip.py`).** The `filmstrip` job builds the main sprite sheet, a small sheet per timeline ASSET (inserted clip / image / b-roll — cached under a *hashed* key because a reference can be a storage path or a bundled `library:slug` with no path), and a **peak envelope** for the main audio and every audio asset. The envelopes ride **inside the job result as base64 uint8** — an envelope at the resolution a 44px lane can draw is a few hundred bytes, so storing one would cost an object, a presign and a round trip to deliver less data than the presigned URL itself. `TIMELINE_MEDIA_VERSION` is mirrored in `backend/routes/video.py` (a worker test asserts they match), but **this gate never withholds**: an older result still serves its frames while `rebuilding: true` asks for the richer one — the round-53 Download lesson applied before it could bite twice. The fingerprint the gate compares against is computed by the BACKEND (`_timeline_media_sig`) and echoed back by the worker untouched, so it cannot disagree with itself; the budget is 2 builds **per asset set**, so an unwritten stamp cannot enqueue forever while a user who keeps adding clips keeps getting artwork. Cost bounds: `MAX_ASSETS` 14, and anything past `ASSET_STRIP_MAX_S` (180s) gets one poster frame instead of a linear decode. Images sample at **t=0** — seeking 10% into a one-frame PNG finds nothing and `media.frame_at` correctly raises, which had silently dropped every image insert back to a flat rectangle.

- **GOING INTO A SCREEN IS ONE ITEM, NOT A ZOOM NEXT TO A CUT (round 55, `add_screen_takeover`).** "I filmed my laptop — push into the screen and continue with the other scene." Every piece already existed and the obvious assembly *cannot* work: an overlay is drawn **above** the zoom stage, so content dropped on the glass sits flat and dead still while the shot pushes past it, and the splice to full-screen lands as a jump because nothing makes the two frames line up. That combination is precisely what users mean by "the transition isn't smooth". What makes it read as one move is that the content is **corner-pinned** to the screen with a real per-frame projective map (`perspective`, `eval=frame`) — so an angled screen is skewed onto the glass and **unskews as the frame arrives; the flattening IS the transition**. Four things are load-bearing:
  - **The camera push and the pin are ONE resolver** (`renderer.screen_lock_geometry` / `screen_lock_corner_paths`), and the takeover writes **no ZoomItem at all**. A zoom item sitting beside an overlay would be remapped by a later cut while the overlay was merely clamped, and the content would slide off the screen it is pinned to.
  - **The pin ends on the IDENTITY map** — the last frame of the push is the asset rendered 1:1 at full frame, pixel-identical to the first frame of the clip that follows, so the handoff needs no crossfade to hide a scale mismatch. That only holds because the progress denominator is the window **minus one frame** (the last frame a filter emits sits at `dur - 1/fps`; the naive denominator lands over a pixel short) and because the tool places the clip **first** and builds the window backwards from where `insert_media` actually snapped it.
  - **`perspective` clamps out-of-range samples to EDGE pixels**, so the asset is padded with a 2px transparent border and the destination quad is grown by exactly that border's share. Without the border the whole frame comes out opaque (the base video vanishes); without the compensation the handoff is 2px out of register. `scale` with `eval=frame` is **not** an alternative — it does not resize the link, proven by test.
  - **The handoff junction is NOT a cut** (`timeline.transition_junctions` excludes it before `scope` is consulted, so `every_cut` cannot reinstate it) — otherwise `set_transitions` drops a dip-to-black into the exact middle of the one join the effect exists to hide. Same one policy on both surfaces: the studio refuses to drag or trim a pinned block and removes the clip with it (`routes/video.py`), exactly as `remove_screen_takeover` does.
  Corners are **measured, not estimated** (`worker/screendet.py`: Canny-quad and Otsu-bright candidates competing on a detail/brightness-step/squareness score, voted across 3 frames) — a corner 2% out is magnified by the push into a sixth of the frame sliding. It **refuses** rather than guessing, and refuses a screen under `SCREEN_QUAD_MIN_FRAC` (8% — a >12x blowup arrives as mush). Honest limit: the pin is fixed to corners measured once, so it does not track a screen that pans across frame. `worker/tests/test_screen_takeover.py` renders the move and asserts glued-at-start, rides-the-push, covers-the-frame, and that the join is invisible.

- **The output frame is chosen in a DRAFT panel, and the crop is aimable (round 54).** `set_frame` accepts `focus_x/focus_y` from the studio, not only from the agent's `auto_reframe` — the renderer has aimed crops since round 36 and no UI had ever offered one, which is why every manual reframe was a dead-centre zoom that users read as "it just zoomed in instead of adjusting it". The studio's reframe panel keeps ratio, fill mode and focus LOCAL until Apply: comparing four ratios used to cost four encodes on the shared box, 25 seconds apart. Its previews come from the already-loaded filmstrip sheet, never from a canvas snapshot of the video — reading pixels back out of a cross-origin video taints the canvas, and `crossOrigin="anonymous"` would make a bucket without CORS on GET refuse to load the video at all.

- **End card.** `worker/brand/endcard.png` is built by `worker/tools/build_endcard.py` from `worker/brand/robot.png` (the site's Rive navbar robot, rendered to PNG — see `worker/brand/README.md`). Changing its look means bumping `OUTRO_VERSION` in **both** `worker/config.py` and `backend/routes/video.py` — a worker test asserts the two constants match, because a mismatch leaves finished exports serving the old card forever.
- **THE EXECUTOR IS A SEPARATE, MANUAL DEPLOY — REDEPLOY IT AFTER EVERY PUSH THAT TOUCHES `worker/` (round 56).** Render redeploys the dispatcher automatically on push to `main`. **Cloud Run does not.** So an ordinary push updates the queue, the agent and the tools while leaving the code that actually makes the pixels on whatever was built last, and the two halves disagree in silence. It has cost real customers twice: round 53 hid **41 of 41** finished exports behind a `trans_v` stamp the executor predated (one customer pressed Download 17 times), and round 55's executor — **19 hours older than round 52** — rejected two users' previews outright with `EDL shape invalid: kind should be one of grain, vignette, …` because their EDLs used the `sharpen` stylize kind its schema had never heard of. Both times the fix was written, tested, pushed and live on the dispatcher the whole time. Redeploy with `gcloud run deploy valmera-executor --source worker/ --region us-central1` (env vars are preserved) — and **always from a clean checkout of `main`**, never a dirty working tree, or you ship unpushed work to production. `worker/version.py` now makes the answer one curl: `/health` reports a **fingerprint of the executor's top-level `worker/*.py`** beside the four paired constants, the dispatcher prints its own on boot and compares, and any remote job that fails gets the skew appended to its error so it lands in the admin job list next to the failure it explains. A hash, not another constant — there are already four (`PIPELINE_VERSION`, `OUTRO_VERSION`, `TRANSITION_VERSION`, `TIMELINE_MEDIA_VERSION`) and **neither incident involved a bumped one**, because nobody stamps a version for a frame-rate flag or a new enum member. **The check never withholds work**: a skewed executor keeps getting jobs, because it renders most things correctly and refusing to use it would take the product down to prevent a subset of edits from being wrong — that is round 53's mistake exactly. It only ever says so. `worker/tests/test_version_skew.py` pins that, including that an unreachable executor is never reported as skew.

- **THE UPLOAD CAP WAS TURNING PEOPLE AWAY IN SILENCE (round 57).** A 2 GiB byte cap shipped beside a 3-hour duration cap, and the two could not both be true: **2 GiB over 3 hours is 1.6 Mbps**, and even ONE hour only fit under **4.7 Mbps** — below what any phone or camera records. So a 5-minute 4K clip or a 40-minute 1080p recording was refused by a platform whose own copy, on ~76 pages, promised 3-hour videos. **The refusal was invisible**: `201 of 203` index jobs have ever succeeded, so nothing server-side was failing; the loss happened in the browser, at `studio/page.js`'s own hardcoded `2 * 1024 * 1024 * 1024`, which fired **before `ensureProject`** and therefore left no project, no job, no row — nothing. Of 214 accounts, **65 had no project at all** and every one of them read as "signed up and never tried". What shipped:
  - **Bytes are an abuse guard; DURATION is the product limit** (`storage.MAX_UPLOAD_GB` 2 → **16**, `MAX_DURATION_S` unchanged at 3h). Duration is what costs us — it drives index and render time — while bytes only cost storage and the user's own upload time. 16 GB holds 1 hour of 4K at 35 Mbps, 10 minutes of 4K at 200 Mbps, and 3 hours of ordinary 1080p. `backend/tests/test_upload_limits_and_events.py` asserts the two caps imply a bitrate a real camera produces, which is the invariant that was violated.
  - **ONE source of truth: `GET /video/limits`.** The studio carries no cap literal anymore — it did, which is why raising `MAX_UPLOAD_GB` on Render would have changed nothing a user could see. The dropzone copy, the pre-flight checks and the attachment caps all read the server's numbers.
  - **`client_events` gained `upload_started` / `upload_rejected` / `upload_failed`**, written by BOTH the browser and the server's own rejection paths (`create_upload`, `complete_upload_core`) so a user who closes the tab the instant their file is refused still leaves a trace. `project_id` is nullable and there is a **`POST /client-event`** with no project, because the failures that mattered most happened before one existed. `upload_started` is not a failure — it is the denominator that makes "died in transit" countable.
  - **It is now impossible to miss**: upload failures join the `attention` feed on the admin overview *and* get their own card with the reasons **ranked**, plus per-project and per-user lists. `client_events` had existed since round 33 and **had no admin surface at all** — 27 player errors sat in it unread.
  - **Duration is pre-flighted in the browser** (`mediaDuration`), because refusing a 5-hour file after a 40-minute upload is the cruellest possible moment. A probe failure is never a rejection; the server re-checks.
  - The size error now says what to DO ("if it's ProRes, export H.264 first") instead of stating a number and stopping.

- **THE BROWSER BUILDS THE PROXY; THE ORIGINAL IS ONLY NEEDED AT EXPORT (round 58).** A real customer's 4.05 GiB / 5min55s 4K clip on Jul 28 took **805 s** to upload on a link measuring 43 Mbps, then **493 s** to index — 386.8 s of it (78%) one ffmpeg downscaling 4K to 540p. Twenty-four and a half minutes before they saw an edit, for a six-minute video. Nothing between upload and export needs the original: transcription, shots, everything the agent sees and every preview read the 540p proxy, and our proxies average **0.70 Mbps** across 202 files — so the same footage is ~31 MB instead of 4 GiB. The browser now builds it with WebCodecs (`src/lib/proxyCore.js`, mediabunny, in a Worker), uploads that, and indexing starts with **zero bytes of the original transferred**; the original streams up behind an already-editable project. Measured in Chrome on 30s of 4K@90Mbps: **540p 1.89x realtime, 360p 2.33x, audio-only 24.71x** — resolution is NOT a lever, decode is the cost exactly as on the server, and **audio-only is the remaining step to sub-60s for any length**. Load-bearing details: rotation is BAKED IN (`allowRotationMetadata:false`) or every frame-level tool sees a sideways picture; `fastStart:false` because 'in-memory' holds an hour-long file on a phone, and the worker moves the header with a **0.27s stream copy** (`media.adopt_client_proxy`, which also normalizes a VFR proxy at 540p — 20x realtime); a transcode losing the race against uploading is **abandoned after a 20s probe**, measured on that machine; the duration gate is bounded at **5s**, not `PROXY_SHORT_FRAC`, because 2% of 3 hours is 3.6 minutes of missing footage. **THE ORIGINAL ALWAYS WINS WHEN ITS BYTES EXIST** — that one rule makes every retry self-healing with no repair job. `meta.upload_state` is load-bearing: `/render/final`, the renderer's final branch and the erase tool all check it and refuse *in words with a percentage*; assets predating this carry none and read as ready. When the original lands, `backend/mp4probe.py` reads its real duration from the moov atom over a few ranged reads (**under 1/50th of the bytes**, header-only walk past the mdat) and re-indexes on >5s drift — it FAILS OPEN, since an unparseable container is not evidence the browser lied.
  - **IT SHIPS OFF. `PROXY_FIRST_UPLOADS=1` on the backend, and ONLY after `gcloud run deploy valmera-executor`.** Render redeploys on push; **Cloud Run does not**. An executor that predates this does not know `client_proxy_key`, would go looking for an original whose bytes are still in the browser, and would **fail every large upload**. Off = the old speed; on-against-stale = no big video can be uploaded at all.
- **THE EDIT PLAYS IN THE BROWSER — the server render is no longer what you wait for (round 58, `src/lib/livePreview.js`).** Previews are a full re-encode of the whole programme: 22s under a minute of source, 41s at 1-3 min, **856s on a 19-minute one**, paid again on every follow-up. Watching a cut needs none of that — skip the removed source spans, take `playbackRate` from the speed spans, transform for zoom, filter for the grade, draw captions/text over the top. Of 186 real projects every feature in actual use except audio is representable. **It is a DRAFT and the badge says so** — three states now (SOURCE / DRAFT / PREVIEW), and anything it cannot show (music, inserts, transitions, grain, erased areas) is NAMED beside the badge, because a user watching their music not play concludes the edit failed. Two bugs found only by running it in Chrome: **assigning `currentTime` does not update it**, so the loop re-issued the same jump **1199 times** and pinned the playhead at the first cut (`createSeekGate` waits for `seeked`: 1199 → 1); and an eased zoom is at strength 0 on its window edges, so picking the winner by *scale* snapped the transform origin (pick by strength). Also guarded: a source without HTTP Range reports `seekable=[0,0]` and clamps every seek to 0 — skipping is abandoned rather than leaving a dead-looking player.
- **AN AGENT TURN IS ROUND-TRIPS × 13 SECONDS (round 58).** Flat across 385 turns — 14.1s at one call, 11.9 at four, 14.4 at ten, 11.8 at forty-nine — and nothing to do with video size. An average call emits **646 output tokens, 402 of them reasoning**, at ~50 tok/s; input is ~32k but almost all fixed overhead (88 tool schemas ≈ 17.4k tokens, system prompt ≈ 15.1k) and DeepSeek serves 31.7k from cache, so the wait is OUTPUT. Two levers: the loop has **always** executed a whole batch of `tool_calls` in one iteration and nothing ever asked the model to send more than one (the prompt now does, with the rule for when not to); and `AGENT_REASONING_EFFORT` is now **safe to switch on** — a provider that rejects the field retries once without it and latches per model, matched narrowly on the field name *plus* an unknown-parameter phrase so an unrelated 400 can't disable it and an outage can't be read as "unsupported".
- **A JSON accessor next to `||` must be parenthesised.** `' - ' || ce.detail->>'filename'` parses as `(' - ' || ce.detail) ->> 'filename'` and dies at RUNTIME with `invalid input syntax for type json` — it would have taken the whole admin overview down, exactly like the `%`-in-a-SQL-comment bug. Same family: cast `detail->>'bytes'` only behind a `~ '^[0-9]+$'` guard, since the sanitiser permits strings and one bad row would break the page for everyone.

- **THE UPLOAD CAP IS DERIVED FROM `--memory`, AND gen1 LIES ABOUT IT (round 57).** The executor's workdir is sized by its Cloud Run memory limit, so **`--memory` is the flag that sets the maximum video length**. `MAX_UPLOAD_GB` (**14**) = 32 GiB / `WORKDIR_HEADROOM` 2.2 = 14.5, reported live as `/health`'s `max_source_gb`. **Raising the cap without raising that first only moves the refusal to after a 40-minute upload.** The trap: on **gen1** the workdir is a gVisor overlay whose `statvfs` reports the **HOST's** disk — the live service claimed **1001 GB free on a 32 GiB instance**, and a capacity check that believed it would wave a job through to a silent OOM kill. **`--execution-environment gen2` is required**: the same call then reports **32.0 GB**, exactly the limit. `/health` reports `fstype` + `free_gb` + `mem_available_gb` so this is checkable, never assumed. 14 GB covers 1h of 4K30, 10 min of 4K at 195 Mbps and 3h of 1080p; it does **not** cover 1h of 4K60 at 50 Mbps (22 GB) — that needs a volume mounted for `WORKER_TMP_DIR`, since 32Gi is Cloud Run's per-instance max.

- **An OOM on Cloud Run is not an error, it is a disappearance (round 57).** `/tmp` is an **in-memory filesystem**, so every byte a job stages there is RAM counted against `--memory`; exceeding it does not raise — the kernel kills the container, the request dies with no body, and the dispatcher records **"Worker died and retries are exhausted"**. That is the least actionable message we produce, for the one failure an operator fixes in a minute. `storage.check_workdir_capacity` now measures free space before staging a source (`WORKDIR_HEADROOM` 2.2x covers the proxy/wav or the render output written beside it) and fails with a message naming `--memory`, `WORKER_TMP_DIR` and the deploy doc. It **fails open** on an unmeasurable filesystem or an unknown object size. The executor moves to **`--cpu 8 --memory 32Gi`** (32Gi is the per-instance maximum; memory is the cheap axis — ~$0.00008/s against ~$0.000192/s for the vCPUs, so ~20% on instance cost, not a doubling). **Past 32Gi the answer is a volume for `WORKER_TMP_DIR`, not more RAM.**

- **Executor timeouts are a PAIR — and now one pair PER JOB KIND (round 57).** A single flat number was sized for the short job: 1500s is five minutes more than the longest job we had ever *seen*, which is a different thing from the longest job that should be *allowed*. Measured on the executor, encodes run **0.5–1.8× realtime**, and it is the **filtergraph** that costs, not the pixels — a **270×480** source still encoded at **0.93× realtime** (zoompan and libass are effectively single-threaded, so 8 vCPU buys almost nothing). `REMOTE_EXECUTOR_TIMEOUTS` is now preview 1500 / final 3400 / index 3400 against Cloud Run `--timeout 3600`, with `EXECUTOR_REQUEST_TIMEOUT_S` mirroring the flag so the ordering is checkable in code as well as against the doc. The pairing invariant is unchanged and still load-bearing (the dispatcher must give up FIRST, or a wedged job is requeued beside an orphan burning 8 vCPU) and `test_executor_timeouts.py` now asserts it for **every** kind. Raising the ceiling is safer than in round 48 because the real wedge defence is `media.run()`'s **stall watchdog**, which fires on ffmpeg emitting no progress at all.
  - **STILL OPEN: a 1-hour heavy edit cannot export.** At 1.8× realtime an hour needs ~6500s against Cloud Run's hard 3600s maximum, so the fix is not a bigger number — it is **chunked parallel rendering**: render the audio in ONE pass (cheap, and it keeps music/ducking/loudness continuity) and the video in N chunks across instances, splitting only at junctions where no zoom, caption or transition spans (`timeline.transition_junctions` already computes that set), then concat `-c copy`. Ordinary and light edits at an hour do fit today.

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
 