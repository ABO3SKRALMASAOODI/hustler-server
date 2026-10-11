"""Admin metrics: one definition per number, shared by every admin surface.

Modules:
  defs       populations (who is a customer), robot rules, tracking dates
  registry   the label / "how this is counted" line for every metric key
  ranges     admin-timezone periods, comparisons and day lists
  db         one read-only, time-limited connection per request
  cache      in-process TTL cache (v2) and the shared app_kv report cache
  visitors   people vs link previews vs robots vs no-signal loads
  ...        one module per admin page (money, funnel, customers, ...)

Nothing here writes to the database except cache.store_shared (app_kv) and
billing_sync's daily snapshot, which live behind explicit feature checks.
"""
