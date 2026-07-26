"""Create the $50 "Pro" tier in Paddle, and rename the $30 tier to "Creator".

Run ONCE. It is idempotent-by-inspection, not by construction: it prints the
new ids and you paste them into PLANS in routes/paddle.py. Re-running would
create a SECOND product, so don't.

Why the rename: "With AI" only ever meant something as the opposite of the MCP
plan, and MCP is coming off the pricing page. The two live tiers are now the
same product at two volumes — Creator $30 and Pro $50 — so the Paddle product
names have to say that too, or the checkout page contradicts the pricing page.

Reads PADDLE_API_KEY from backend/.env (live key).
"""

import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")

# The existing $30 tier, created in round 44.
AI_PRODUCT = "pro_01kyde24jmpj6ct7sbxre3g7r2"
AI_MONTHLY = "pri_01kyde25cwqf7t2bk1ekky2pyp"
AI_ANNUAL = "pri_01kyde25n7rxrhajg5xvxxka7y"

# Same shape as the existing prices: the trial lives on the PRICE, so Paddle
# creates the subscription immediately, charges nothing for 3 days, and fires
# subscription.created right away (which is what grants credits).
TRIAL = {"interval": "day", "frequency": 3, "requires_payment_method": True}


def load_key():
    with open(ENV_PATH) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("PADDLE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("PADDLE_API_KEY not found in backend/.env")


KEY = load_key()
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.paddle.com" + path, data=data,
                                 headers=HEADERS, method=method)
    try:
        return json.load(urllib.request.urlopen(req))["data"]
    except urllib.error.HTTPError as e:
        print(f"ERROR {method} {path} -> {e.code}: {e.read().decode()[:600]}")
        raise


def main():
    call("PATCH", f"/products/{AI_PRODUCT}", {"name": "Valmera Creator"})
    call("PATCH", f"/prices/{AI_MONTHLY}",
         {"name": "Creator — monthly", "description": "Creator — monthly"})
    call("PATCH", f"/prices/{AI_ANNUAL}",
         {"name": "Creator — annual", "description": "Creator — annual"})
    print("renamed the $30 product + prices -> Creator")

    prod = call("POST", "/products", {
        "name": "Valmera Pro",
        "tax_category": "saas",
        "type": "standard",
        "description": ("Agentic AI video editing with the model included, at "
                        "the volume a full-time creator or an agency actually "
                        "edits at."),
        "image_url": "https://valmera.io/icon-512.png",
    })
    print("product Valmera Pro:", prod["id"])

    monthly = call("POST", "/prices", {
        "product_id": prod["id"],
        "name": "Pro — monthly", "description": "Pro — monthly",
        "billing_cycle": {"interval": "month", "frequency": 1},
        "trial_period": TRIAL,
        "unit_price": {"amount": "5000", "currency_code": "USD"},
        "tax_mode": "internal",
        "quantity": {"minimum": 1, "maximum": 1},
    })
    annual = call("POST", "/prices", {
        "product_id": prod["id"],
        "name": "Pro — annual", "description": "Pro — annual",
        "billing_cycle": {"interval": "year", "frequency": 1},
        "trial_period": TRIAL,
        "unit_price": {"amount": "50000", "currency_code": "USD"},
        "tax_mode": "internal",
        "quantity": {"minimum": 1, "maximum": 1},
    })
    print("PRO MONTHLY price id:", monthly["id"])
    print("PRO ANNUAL  price id:", annual["id"])
    print("\nPaste these into PLANS_LIVE['ai_pro'] in backend/routes/paddle.py")


if __name__ == "__main__":
    main()
