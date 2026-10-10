"""Costs and margin over the last 30 days, split into customers and the owner.

The editing-server ceiling decompresses every job's timings (seconds of
database time), so this report is computed at most once per 10 minutes and
shared through app_kv (cache.shared_report) by /admin/video/costs and
/admin/v2/revenue alike: both always show the same numbers.
"""
import os

import billing
import credits
import model_prices
from admin_metrics import defs

OWNER = f"LOWER(u.email) = '{defs.ADMIN_EMAIL.lower()}'"


def _env_float(name, default):
    try:
        return max(0.0, float(os.getenv(name, default)))
    except ValueError:
        return float(default)


def compute(cur):
    """The full /admin/video/costs payload plus the owner/customer split."""
    from routes.admin_video import (_cost_expr, PRICE_IN_PER_M,
                                    PRICE_OUT_PER_M, PRICE_CACHED_IN_PER_M)
    storage_rate = _env_float("R2_STORAGE_USD_PER_GB_MONTH", "0.015")
    db_gb = _env_float("DATABASE_STORAGE_GB", "5")
    db_rate = _env_float("RENDER_POSTGRES_STORAGE_USD_PER_GB_MONTH", "0.30")
    database_usd = db_gb * db_rate
    fixed_usd = _env_float("PLATFORM_FIXED_COST_USD_MONTH", "0") + database_usd

    cur.execute(f"""
        SELECT u.email, DATE(lc.created_at) AS day, COUNT(*) AS calls,
               COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
               COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
               {_cost_expr("lc")} AS est_cost
          FROM llm_calls lc JOIN projects p ON p.id = lc.project_id
          JOIN users u ON u.id = p.user_id
         WHERE lc.created_at > NOW() - INTERVAL '30 days'
         GROUP BY u.email, DATE(lc.created_at)
         ORDER BY day DESC, est_cost DESC LIMIT 500""")
    daily = [dict(r) for r in cur.fetchall()]
    cur.execute(f"""
        SELECT lc.purpose, COUNT(*) AS calls,
               COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
               COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
               {_cost_expr("lc")} AS est_cost
          FROM llm_calls lc WHERE lc.created_at > NOW() - INTERVAL '30 days'
         GROUP BY lc.purpose ORDER BY est_cost DESC""")
    by_purpose = [dict(r) for r in cur.fetchall()]
    cur.execute(f"""
        SELECT COALESCE(NULLIF(lc.model, ''), 'unknown') AS model,
               COUNT(*) AS calls,
               COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
               COALESCE(SUM(COALESCE((lc.response->>'cached_in')::float, 0)),
                        0) AS cached_in,
               COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
               COALESCE(SUM(COALESCE((lc.response->>'reasoning_out')::float,
                                     0)), 0) AS reasoning_out,
               {_cost_expr("lc")} AS est_cost
          FROM llm_calls lc WHERE lc.created_at > NOW() - INTERVAL '30 days'
         GROUP BY 1 ORDER BY est_cost DESC""")
    by_model = [dict(r) for r in cur.fetchall()]
    cur.execute(f"""
        SELECT COALESCE(({OWNER}), FALSE) AS owner, {_cost_expr("lc")} AS usd
          FROM llm_calls lc LEFT JOIN projects p ON p.id = lc.project_id
          LEFT JOIN users u ON u.id = p.user_id
         WHERE lc.created_at > NOW() - INTERVAL '30 days'
         GROUP BY 1""")
    model_split = {bool(r["owner"]): float(r["usd"] or 0)
                   for r in cur.fetchall()}
    cur.execute(f"""
        SELECT COALESCE(({OWNER}), FALSE) AS owner,
               COALESCE(SUM(GREATEST(COALESCE(NULLIF(
                   vj.result->'timings'->>'gross_compute_usd_ceiling', '')
                   ::numeric, 0), 0)), 0) AS usd
          FROM video_jobs vj LEFT JOIN users u ON u.id = vj.user_id
         WHERE vj.created_at > NOW() - INTERVAL '30 days'
         GROUP BY 1""")
    compute_split = {bool(r["owner"]): float(r["usd"] or 0)
                     for r in cur.fetchall()}
    cur.execute(f"""
        SELECT COALESCE(({OWNER}), FALSE) AS owner,
               COALESCE(SUM(a.bytes), 0) AS bytes
          FROM assets a LEFT JOIN projects p ON p.id = a.project_id
          LEFT JOIN users u ON u.id = p.user_id
         GROUP BY 1""")
    storage_split = {bool(r["owner"]): int(r["bytes"] or 0)
                     for r in cur.fetchall()}
    cur.execute(f"""
        SELECT COALESCE(SUM(p.amount_cents), 0) AS cents
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {defs.success('p')} AND p.currency = 'USD'
           AND COALESCE(p.occurred_at, p.created_at)
               > (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'
           AND (u.id IS NULL OR LOWER(u.email) NOT IN ({defs.EXCLUDED_SQL}))""")
    cash_30d = defs.usd((cur.fetchone() or {}).get("cents"))

    model_30d = sum(float(r["est_cost"] or 0) for r in by_model)
    executor_30d = sum(compute_split.values())
    storage_bytes = sum(storage_split.values())
    storage_gb = storage_bytes / 1_000_000_000.0
    storage_month = storage_gb * storage_rate
    total_30d = model_30d + executor_30d + storage_month + fixed_usd
    cash_margin = ((cash_30d - total_30d) / cash_30d * 100.0
                   if cash_30d > 0 else None)

    owner_storage = storage_split.get(True, 0) / 1e9 * storage_rate
    customer_storage = storage_split.get(False, 0) / 1e9 * storage_rate
    customers_cost = (model_split.get(False, 0) + compute_split.get(False, 0)
                      + customer_storage + fixed_usd)
    margin_excl = ((cash_30d - customers_cost) / cash_30d * 100.0
                   if cash_30d > 0 else None)

    plan_scenarios = []
    reserve = 5.0 * storage_rate
    labels = {"ai": "Creator", "ai_pro": "Pro", "ai_max": "Frontier"}
    for plan in ("ai", "ai_pro", "ai_max"):
        allowance = int(credits.PLAN_MONTHLY_LIMITS[plan])
        metered = allowance * model_prices.USD_PER_CREDIT
        prices = billing.PLAN_PRICES_USD[plan]
        for cadence, revenue in (("monthly", float(prices["monthly"])),
                                 ("annual", float(prices["yearly"]) / 12.0)):
            margin = (revenue - (metered + reserve)) / revenue * 100.0
            plan_scenarios.append({
                "plan": plan, "label": labels[plan], "cadence": cadence,
                "credits": allowance,
                "monthly_revenue_usd": round(revenue, 2),
                "max_metered_cost_usd": round(metered, 2),
                "storage_reserve_usd": round(reserve, 3),
                "gross_margin_pct": round(margin, 1)})

    def _day(r):
        return {**r, "day": r["day"].isoformat(),
                "est_cost": round(float(r["est_cost"] or 0), 4),
                "tokens_in": int(r["tokens_in"] or 0),
                "tokens_out": int(r["tokens_out"] or 0)}

    return {
        "pricing": {"in_per_m": PRICE_IN_PER_M, "out_per_m": PRICE_OUT_PER_M,
                    "cached_in_per_m": PRICE_CACHED_IN_PER_M,
                    "models": model_prices.MODEL_PRICES,
                    "note": "each row is priced from its OWN model; the "
                            "in/out/cached numbers above are only the fallback "
                            "for a model not in the table.",
                    "provider_cost_usd_per_credit": model_prices.USD_PER_CREDIT,
                    "r2_storage_usd_per_gb_month": storage_rate},
        "economics_30d": {
            "cash_revenue_usd": round(cash_30d, 2),
            "model_usd": round(model_30d, 4),
            "executor_ceiling_usd": round(executor_30d, 4),
            "storage_bytes": storage_bytes,
            "storage_gb": round(storage_gb, 3),
            "storage_usd": round(storage_month, 4),
            "database_storage_gb": round(db_gb, 2),
            "database_storage_usd": round(database_usd, 2),
            "fixed_platform_usd": round(fixed_usd, 2),
            "total_cost_usd": round(total_30d, 4),
            "cash_gross_margin_pct": (round(cash_margin, 1)
                                      if cash_margin is not None else None),
            "note": "Cash revenue is successful payments in the last 30 days "
                    "(your and test accounts excluded). Executor is the "
                    "conservative job telemetry ceiling. Database storage "
                    "assumes 5 GB at $0.30/GB.",
        },
        "split": {
            "model_customers_usd": round(model_split.get(False, 0), 4),
            "model_owner_usd": round(model_split.get(True, 0), 4),
            "compute_customers_usd": round(compute_split.get(False, 0), 4),
            "compute_owner_usd": round(compute_split.get(True, 0), 4),
            "storage_customers_usd_month": round(customer_storage, 4),
            "storage_owner_usd_month": round(owner_storage, 4),
            "margin_excl_owner_pct": (round(margin_excl, 1)
                                      if margin_excl is not None else None),
        },
        "plan_scenarios": plan_scenarios,
        "daily": [_day(r) for r in daily],
        "by_purpose": [{**r, "est_cost": round(float(r["est_cost"] or 0), 4)}
                       for r in by_purpose],
        "by_model": [{**r, "cached_in": int(float(r["cached_in"] or 0)),
                      "reasoning_out": int(float(r["reasoning_out"] or 0)),
                      "est_cost": round(float(r["est_cost"] or 0), 4)}
                     for r in by_model],
    }


def revenue_costs(report):
    """The Revenue page's costs block from the shared costs report."""
    e = report.get("economics_30d") or {}
    s = report.get("split") or {}
    return {"days": 30, "computed_at": report.get("computed_at"),
            "cash_usd": e.get("cash_revenue_usd"),
            "model_customers_usd": round(s.get("model_customers_usd") or 0, 2),
            "model_owner_usd": round(s.get("model_owner_usd") or 0, 2),
            "compute_ceiling_usd": round(e.get("executor_ceiling_usd") or 0, 2),
            "storage_customers_usd_month":
                round(s.get("storage_customers_usd_month") or 0, 2),
            "storage_owner_usd_month":
                round(s.get("storage_owner_usd_month") or 0, 2),
            "database_usd_month": e.get("database_storage_usd"),
            "margin_pct": e.get("cash_gross_margin_pct"),
            "margin_excl_owner_pct": s.get("margin_excl_owner_pct")}
