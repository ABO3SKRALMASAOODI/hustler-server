"""The plan stress test on Advanced -> Costs covers the plans on sale today."""
import billing
import credits
import model_prices
from admin_metrics import costs


def test_stress_test_lists_current_plans_before_grandfathered_ones():
    rows = costs.stress_test(0.015)
    plans = []
    for r in rows:
        if r["plan"] not in plans:
            plans.append(r["plan"])
    assert plans == ["mcp_connect", "advanced", "ai", "ai_pro", "ai_max"]
    assert {r["tier"] for r in rows if r["plan"] in ("mcp_connect", "advanced")} == {"current"}
    assert {r["tier"] for r in rows if r["plan"] in ("ai", "ai_pro", "ai_max")} == {"grandfathered"}
    # Two rows per plan: monthly, and the yearly price spread over 12 months.
    assert len(rows) == 10


def test_stress_test_margin_is_price_minus_full_allowance_and_storage():
    rows = {(r["plan"], r["cadence"]): r for r in costs.stress_test(0.015)}
    r = rows[("mcp_connect", "monthly")]
    allowance = credits.PLAN_MONTHLY_LIMITS["mcp_connect"]
    metered = allowance * model_prices.USD_PER_CREDIT
    revenue = float(billing.PLAN_PRICES_USD["mcp_connect"]["monthly"])
    assert r["credits"] == allowance
    assert r["monthly_revenue_usd"] == round(revenue, 2)
    assert r["max_metered_cost_usd"] == round(metered, 2)
    assert r["storage_reserve_usd"] == round(5 * 0.015, 3)
    assert r["gross_margin_pct"] == round((revenue - metered - 5 * 0.015) / revenue * 100, 1)
    yearly = rows[("advanced", "annual")]
    assert yearly["monthly_revenue_usd"] == round(billing.PLAN_PRICES_USD["advanced"]["yearly"] / 12.0, 2)
