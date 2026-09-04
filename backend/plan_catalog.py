"""One backend source of truth for plan prices, Paddle IDs, and credits.

Checkout chooses a Paddle price from this catalog, the signed webhook grants
its credits, the Studio reports the same monthly limit, and revenue reporting
uses the same sticker price. Keeping those facts in separate dictionaries once
let a real customer pay while the admin reported $0 and the UI showed the
wrong allowance.
"""

PLANS_LIVE = {
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
PURCHASABLE_PLANS = {"ai", "ai_pro", "ai_max"}

PLAN_MONTHLY_CREDITS = {
    "free": 0,
    **{name: config["monthly_credits"]
       for name, config in PLANS_LIVE.items()},
}

PLAN_PRICES_USD = {
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
