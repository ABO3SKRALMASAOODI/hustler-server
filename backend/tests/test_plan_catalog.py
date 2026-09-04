"""Checkout, entitlement, allowance, and revenue share one plan catalog."""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing  # noqa: E402
import credits  # noqa: E402
import plan_catalog  # noqa: E402
from routes import paddle, paddle_webhook  # noqa: E402


def test_all_billing_consumers_share_the_authoritative_objects():
    assert paddle.PLANS_LIVE is plan_catalog.PLANS_LIVE
    assert paddle.PURCHASABLE_PLANS is plan_catalog.PURCHASABLE_PLANS
    assert paddle_webhook.PLAN_CREDITS is plan_catalog.PLAN_MONTHLY_CREDITS
    assert credits.PLAN_MONTHLY_LIMITS is plan_catalog.PLAN_MONTHLY_CREDITS
    assert billing.PLAN_PRICES_USD is plan_catalog.PLAN_PRICES_USD


def test_every_plan_has_one_credit_limit_and_one_reporting_price():
    names = set(plan_catalog.PLANS_LIVE) | {"free"}
    assert set(plan_catalog.PLAN_MONTHLY_CREDITS) == names
    assert set(plan_catalog.PLAN_PRICES_USD) == names
    for name, config in plan_catalog.PLANS_LIVE.items():
        assert plan_catalog.PLAN_MONTHLY_CREDITS[name] == \
            config["monthly_credits"]


def test_shopfront_plans_are_complete_and_annual_is_ten_months():
    assert plan_catalog.PURCHASABLE_PLANS == {"ai", "ai_pro", "ai_max"}
    for name in plan_catalog.PURCHASABLE_PLANS:
        plan = plan_catalog.PLANS_LIVE[name]
        price = plan_catalog.PLAN_PRICES_USD[name]
        assert plan["price_id"].startswith("pri_")
        assert plan["yearly_price_id"].startswith("pri_")
        assert plan["monthly_credits"] > 0
        assert price["monthly"] > 0
        assert price["yearly"] == price["monthly"] * 10


def test_live_price_ids_are_unique_and_legacy_grants_are_positive():
    seen = {}
    for name, config in plan_catalog.PLANS_LIVE.items():
        for key in ("price_id", "yearly_price_id"):
            price_id = config[key]
            assert price_id not in seen, \
                f"{price_id} maps both {seen.get(price_id)} and {name}"
            seen[price_id] = name
        for price_id, credits_granted in config.get("legacy_prices", {}).items():
            assert price_id not in seen, \
                f"{price_id} maps both {seen.get(price_id)} and {name}"
            assert credits_granted > 0
            seen[price_id] = name
