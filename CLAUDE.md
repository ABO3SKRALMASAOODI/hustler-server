## Valmera — Project Brief

### What It Is
An AI-powered web video editor. 
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

### "ACTIVE" IS NOT "PAID" — the refused card (round 59, `backend/billing.py`)

A trial user showed **converted** in the admin while Paddle showed his **$30.00
charge refused** (`not_enough_balance`), and the revenue panel showed **$0**.
Three screens, three different wrong answers, one cause: **Paddle flips a
subscription to `active` when the TRIAL ENDS, and only then tries the card.**
We read `active` as conversion, released the full 2,000-credit pool, and the
`subscription.past_due` a second later hit an `else` branch that printed
*"no change (grace)"* and **did nothing** — so the account sat there
permanently claiming to be a paying customer who had paid nothing.

- **Money is a row in `payments`, and nothing else is revenue.** A trial opens
  with a genuine, signed, `completed` transaction whose `grand_total` is
  **$0.00** — so "count the completed transactions" was never counting money
  either. Only `amount_cents > 0` is. `grand_total` is **minor units**: `"3000"`
  is $30.00.
- **Revenue was structurally zero for a SECOND reason.** `admin.py` carried its
  own `{'plus': 20, 'pro': 50, 'ultra': 100}` in three places — three **retired**
  plans. Every live customer is on ai/ai_pro/ai_max, so MRR summed to $0 no
  matter who paid, and the two wrongs agreeing is why nobody caught either.
  Prices now live once, in `billing.PLAN_PRICES_USD`; yearly is amortised (a
  $300/yr Creator is **$25** of MRR, not $300 and not $30); MRR counts
  **paying** subscriptions only, so a trial is never booked as revenue.
- **THE GUARD THAT WAS MISSING:** `subscription.updated` keeps arriving while a
  subscription is in dunning and **carries the paid price id**, so it is in
  `GRANT_EVENTS`. Without a `FAILING_STATUSES` check before the grant, every
  such event re-funds the pool after every lift, forever.
- **Grace is graded by payment history, not granted blanket.** Never collected
  a cent (a refused trial conversion) → **the pool is lifted immediately**.
  Has paid before (a renewal declined) → everything stays for
  `PAID_GRACE_DAYS` (3) while Paddle retries. Blanket grace is what let the
  refused trial keep 2,000 credits; no grace would cut off a real customer over
  a bank blip. `lift_paid_credits` deliberately does **not** call
  `update_user_subscription_status(..., False, ...)` — that NULLs
  `subscription_id`, which is how every later event, including the retry that
  succeeds, finds the user. Strip the entitlement, keep the identity. The 50
  free bonus credits are never taken.
- **PADDLE HAS NO API TO FORCE A RETRY.** Its dunning engine retries 7 times
  over 30 days on its own. The only lever we hold is
  `GET /subscriptions/{id}/update-payment-method-transaction` → an inline
  checkout that captures a new card. So "try again daily" is: reconcile daily +
  **one capped email a day** carrying that link (`billing_sync.run_dunning`,
  `GET /paddle/update-payment-method`), never a claim that we re-charged.
- **`billing_sync.py` reconciles against Paddle hourly** and on demand
  (`POST /admin/billing/sync`, `?full=1` backfills the ledger). Webhooks are a
  fast path, not a record — when one is dropped nothing ever revisits the
  account, which is how a subscription **canceled 12 hours earlier** was still
  marked subscribed. **It never downgrades on silence**: a timeout or missing
  key changes nothing. A definite **404 is an answer**, but still not acted on
  — it sets `billing_status='not_in_paddle'` and lands in the admin's
  **contradiction list** for a human. Three legacy `plus` accounts are in that
  state (sandbox-era ids against the live key).
- **The trial cap leaked 10x.** Two live trials held **3,924.80** and
  **2,000.00** credits against allowances of 400 and 200. The grant is a SET
  (what makes Paddle's repeats idempotent) and a repeat trial event passes
  `preserve_credits=True`, which updates `credits_monthly_limit` and
  deliberately leaves `credits_monthly` alone — so once any single event grants
  the full pool, the limit is corrected afterwards and **the pool never is**.
  The webhook comment calls that race self-healing; it heals the limit, not the
  balance. `_clamp_trial_credits` takes the **overage** off the balance rather
  than recomputing it, so credits already spent are not handed back.
- The refused card is a **fourth wall** in the studio with code `payment_failed`
  — "start a trial" and "you're out of credits" are both false to someone whose
  payment failed. `credits.get_balance` carries it, so every surface gets it
  without a second request.
- Schema: `migrations/012_billing_truth.sql` (**applied to prod Jul 29 2026**).
  Everything is behind `billing.columns_ready()`, so it deploys before the psql.

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
 