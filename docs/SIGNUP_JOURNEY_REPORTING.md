# Website acquisition measurement

Migration `backend/migrations/028_signup_journeys.sql` must precede this backend release. It adds columns/tables and a filtered view; it does not remove historical rows. Apply this one migration in a bounded transaction, then deploy the backend and frontend.

`POST /admin/journey` is anonymous and bounded. A random UUID identifies each page navigation. Cumulative active seconds and scroll depth are updated monotonically, and event UUIDs deduplicate retries. The browser reports every 15 seconds while visible, and on route changes, visibility changes and pagehide. Active time stops after 30 seconds without interaction. Session IDs rotate after 30 minutes without activity. Blocked storage uses an in-memory identity. Analytics is optional and must never gate authentication.

Only explicitly enumerated action names, numeric HTTP statuses, sanitized paths, referrer host, random browser/session IDs and device/browser classes are accepted. Passwords, field values, prompts, query strings, OAuth codes, videos and recordings are not collected. New visits do not store IP addresses or call external geolocation. Do Not Track, Global Privacy Control and the existing admin-device exclusion are honored.

First email verification and Google code exchange for a newly verified account write an idempotent, server-confirmed signup attribution. A form submit or successful existing-user login cannot count as a new signup. Google codes are consumed atomically and expire after five minutes.

`GET /admin/charts/visits` returns all recorded history, daily verified signups, total views, unique browser counts, the period signup/visitor ratio, and the peak unique-visitor day. Historical account counts retain the configured customer epoch and operator exclusions. Historical time-update rows and known crawler agents are excluded consistently in all existing page-visit reports. Raw rows remain untouched.

`GET /admin/journeys` is admin-only. It returns page engagement, action counts, the latest 50 anonymous journeys from the last 30 days, and a separately labelled new-browser cohort linked to verified signups. A last observed page after 30 minutes is an exit candidate, not proof of abandonment. Historical engagement cannot be reconstructed from old wall-clock timers. Cookies, content blockers, unrecognized bots and cross-device usage limit accuracy; a period signup/visitor ratio is not an individual conversion funnel.

Validation: backend suite, Cloudflare adapter lifecycle tests, frontend library suite and production build. The audit at `audits/2026-10-03/signup-journey` in the parent workspace also executes the actual SQL against a local PostgreSQL WASM engine, testing migration replay, idempotence, out-of-order timing, bot exclusions and daily signup ratios.

Reliability: rejected container images are destroyed under an exclusive reset reservation before another call can enter the shard. An abandoned pre-run startup no longer schedules a stop after releasing its reservation. Failed MCP responses retain a bounded category, response fingerprint and generated internal reference when available; no raw response or tool arguments are copied into the event log.
