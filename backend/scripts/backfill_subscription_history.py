"""Read Paddle's retained event stream into reporting history only.

Run after migration 037. Dry run by default. --write stores reporting rows;
it never changes subscriptions, access, credits, payments, or sends email.
Paddle retains 90 days of events. The record endpoint reports missing history.
"""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
import psycopg2
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subscription_reporting import record_history

KINDS = ("created", "activated", "canceled", "past_due", "paused", "resumed",
         "trialing", "updated", "imported")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    base = "https://sandbox-api.paddle.com" if os.getenv("PADDLE_MODE") == "sandbox" else "https://api.paddle.com"
    session = requests.Session()
    session.headers["Authorization"] = "Bearer " + os.environ["PADDLE_API_KEY"]
    url = base + "/events"
    params = {"per_page": 200, "event_type": ",".join("subscription." + k for k in KINDS),
              "order_by": "id[ASC]"}
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10,
                           options="-c statement_timeout=15000 -c lock_timeout=2000") if args.write else None
    count = 0
    try:
        while url:
            if urlparse(url).netloc != urlparse(base).netloc or urlparse(url).path != "/events":
                raise ValueError("Unexpected Paddle pagination URL")
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            for event in payload["data"]:
                if conn:
                    if not record_history(conn, event["data"], event):
                        conn.rollback()
                        raise RuntimeError("Could not store reporting history")
                    conn.commit()
                count += 1
            pagination = payload.get("meta", {}).get("pagination", {})
            url = pagination.get("next") if pagination.get("has_more") else None
            params = None
        print(f"{'Stored' if args.write else 'Read'} {count} subscription events")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
