"""One backend source of truth for plan prices, Paddle IDs, and credits.

Checkout chooses a Paddle price from this catalog, the signed webhook grants
its credits, the Studio reports the same monthly limit, and revenue reporting
uses the same sticker price. Keeping those facts in separate dictionaries once
let a real customer pay while the admin reported $0 and the UI showed the
wrong allowance.
"""

PLANS_LIVE = {
    # October 2026 offers are separate contracts, never renamed legacy IDs.
    "plus_2026": {"monthly_credits": 500, "price_id": "pri_01m4tq08e68mg9t3n4f86qwhb3", "yearly_price_id": "pri_01m4tq08na6vh0x92pfk573f9e"},
    "pro_2026": {"monthly_credits": 1500, "price_id": "pri_01m4tq09etphbn3b0btrtj1sad", "yearly_price_id": "pri_01m4tq09nz5axz948yjdh6jk72"},
    "max_2026": {"monthly_credits": 4000, "price_id": "pri_01m4tq0afzgw5t3h0k8m362fyw", "yearly_price_id": "pri_01m4tq0ar7xt1hkm3a60m9rvts"},
    # September 2026 contracts: never reuse a grandfathered price/plan ID.
    "mcp_connect": {'monthly_credits': 1500, 'price_id': 'pri_01m3krftttqgfsfzeyepww17c5', 'yearly_price_id': 'pri_01m3krfv2zr89215s1k6dbac2z'},
    "advanced": {'monthly_credits': 8000, 'price_id': 'pri_01m3krfvxd8fq4397hwzr071g7', 'yearly_price_id': 'pri_01m3krfw57nk3y15pv083aesw0'},

    # Current shopfront: no trial; annual prices are ten monthly payments.
    "ai": {
        "price_id": "pri_01m00w4aa9nqj2r3x30jkagbq0",
        "yearly_price_id": "pri_01m00w4ahg1q4m56km4nb46a27",
        "monthly_credits": 1000,
        "legacy_prices": {
            "pri_01kyde25cwqf7t2bk1ekky2pyp": 2000,
            "pri_01kyde25n7rxrhajg5xvxxka7y": 2000,
        },
    },
    "ai_pro": {
        "price_id": "pri_01m00w4as67k67tmbxgs8kab8j",
        "yearly_price_id": "pri_01m00w4b14hkwr6y3zp8wjksm9",
        "monthly_credits": 2000,
        "legacy_prices": {
            "pri_01kye15m5262nbs7hjmazrej7j": 4000,
            "pri_01kye15mdacm7wzqp740g3rvy4": 4000,
        },
    },
    "ai_max": {
        "price_id": "pri_01m00w4bakj10vypkqqg8b7jse",
        "yearly_price_id": "pri_01m00w4bpedjqatvn0f640ngzz",
        "monthly_credits": 5000,
        "legacy_prices": {
            "pri_01kyg21hzbbz360kn0ptjnpdar": 10000,
            "pri_01kyg21j78jk6tpkkcpkrysvc4": 10000,
        },
    },
    # Off the shopfront; retained for the existing BYO-model subscriber.
    "mcp": {
        "price_id": "pri_01kyde24w5s63hgzh7wzn4zwnt",
        "yearly_price_id": "pri_01kyde254pd3z24zqd8mzav861",
        "monthly_credits": 0,
    },
    # Retired tiers remain resolvable for grandfathered subscriptions.
    "plus": {
        "price_id": "pri_01jxj6smtjkfsf22hdr4swyr9j",
        "yearly_price_id": "pri_01kkekq1hcvzvyhh3ffk3nk291",
        "monthly_credits": 800,
    },
    "pro": {
        "price_id": "pri_01kk4k4y8c3ygxd620vcxg6ph1",
        "yearly_price_id": "pri_01kkeksjv9pf2nc1gphj67m8ae",
        "monthly_credits": 2400,
    },
    "ultra": {
        "price_id": "pri_01kk4k83cwpmf1jsctgdvhm0n6",
        "yearly_price_id": "pri_01kkektygjg89gywskyj1dycx2",
        "monthly_credits": 5000,
    },
    "titan": {
        "price_id": "pri_01kkekbegh2q5x3kxn28afbw5d",
        "yearly_price_id": "pri_01kkekf5ksjq5dqbfpxakf1g23",
        "monthly_credits": 10000,
    },
    "ace": {
        "price_id": "pri_01kkekgt4zv65t59yw7ybz8w01",
        "yearly_price_id": "pri_01kkekj0am5yfqxx933c6d4tck",
        "monthly_credits": 30000,
    },
}

PLANS_SANDBOX = {
    "plus": {
        "price_id": "pri_01jw8722trngfyz12kq158vrz7",
        "yearly_price_id": "SANDBOX_PLUS_YEARLY_TODO",
        "monthly_credits": 800,
    },
    "pro": {
        "price_id": "pri_01kk4wvnbxb7nbh426bnk62xa2",
        "yearly_price_id": "SANDBOX_PRO_YEARLY_TODO",
        "monthly_credits": 2400,
    },
    "ultra": {
        "price_id": "pri_01kk4wwr07ce0xp8x4kvdgt8kg",
        "yearly_price_id": "SANDBOX_ULTRA_YEARLY_TODO",
        "monthly_credits": 5000,
    },
    "titan": {
        "price_id": "SANDBOX_TITAN_MONTHLY_TODO",
        "yearly_price_id": "SANDBOX_TITAN_YEARLY_TODO",
        "monthly_credits": 10000,
    },
    "ace": {
        "price_id": "SANDBOX_ACE_MONTHLY_TODO",
        "yearly_price_id": "SANDBOX_ACE_YEARLY_TODO",
        "monthly_credits": 30000,
    },
}

# Only these tiers can be newly purchased or switched to. Retired and MCP
# definitions above are present solely to keep existing contracts resolvable.
NEW_PLANS = frozenset({"plus_2026", "pro_2026", "max_2026"})
SEPTEMBER_PLANS = frozenset({"mcp_connect", "advanced"})
MONTHLY_CYCLE_PLANS = NEW_PLANS | SEPTEMBER_PLANS
# Display-only comparisons never enter the checkout or entitlement catalogs.
COMPARISON_ONLY_PLANS = ({
    "id": "ultimate", "name": "Ultimate Editor", "monthly": 200,
    "yearly": 2000, "credits": 16000, "sold_out": True,
},)
LEGACY_SHOPFRONT_PLANS = frozenset({"ai", "ai_pro", "ai_max"})
PURCHASABLE_PLANS = NEW_PLANS | SEPTEMBER_PLANS | LEGACY_SHOPFRONT_PLANS

def daily_credits_for_plan(plan):
    """New offers sell a fixed monthly allowance; legacy daily grants persist."""
    return 0 if plan in NEW_PLANS else 20

def available_plans(snapshot):
    """Grandfather until access ends, including scheduled cancellation/grace."""
    if snapshot.get("is_subscribed") and snapshot.get("plan") not in NEW_PLANS:
        if snapshot.get("plan") in SEPTEMBER_PLANS:
            return SEPTEMBER_PLANS
        return LEGACY_SHOPFRONT_PLANS | {snapshot.get("plan")}
    from catalog_rollout import shopfront_held
    if shopfront_held():
        return NEW_PLANS | SEPTEMBER_PLANS
    return NEW_PLANS


PLAN_MONTHLY_CREDITS = {
    "free": 0,
    **{name: config["monthly_credits"]
       for name, config in PLANS_LIVE.items()},
}

PLAN_PRICES_USD = {
    "plus_2026": {"monthly": 25, "yearly": 250},
    "pro_2026": {"monthly": 50, "yearly": 500},
    "max_2026": {"monthly": 100, "yearly": 1000},
    "mcp_connect": {"monthly": 25, "yearly": 250},
    "advanced": {"monthly": 100, "yearly": 1000},
    "ai": {"monthly": 15, "yearly": 150},
    "ai_pro": {"monthly": 30, "yearly": 300},
    "ai_max": {"monthly": 50, "yearly": 500},
    "mcp": {"monthly": 15, "yearly": 150},
    "plus": {"monthly": 20, "yearly": 200},
    "pro": {"monthly": 50, "yearly": 500},
    "ultra": {"monthly": 100, "yearly": 1000},
    "titan": {"monthly": 200, "yearly": 2000},
    "ace": {"monthly": 500, "yearly": 5000},
    "free": {"monthly": 0, "yearly": 0},
}
