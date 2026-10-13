"""Populate reporting snapshots with Paddle GETs; never reconcile billing.

Run after migration 035 with DATABASE_URL and PADDLE_API_KEY configured.
Without --write, only report how many contracts need to be read.
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg2
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subscription_reporting import record_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10,
                           options="-c statement_timeout=15000 -c lock_timeout=2000")
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT subscription_id FROM (
                SELECT subscription_id FROM users
                UNION ALL SELECT subscription_id FROM payments
                  WHERE status IN ('paid','completed') AND amount_cents > 0
                ) s WHERE subscription_id LIKE 'sub_%%' ORDER BY subscription_id""")
            ids = [r[0] for r in cur.fetchall()]
        conn.rollback()
        print(f"Subscription reports: {len(ids)} contracts", flush=True)
        if not args.write:
            return
        session = requests.Session()
        session.headers["Authorization"] = "Bearer " + os.environ["PADDLE_API_KEY"]
        base = "https://sandbox-api.paddle.com" if os.getenv("PADDLE_MODE") == "sandbox" else "https://api.paddle.com"
        stored = missing = failed = 0
        for sid in ids:
            try:
                response = session.get(f"{base}/subscriptions/{sid}", timeout=20)
                if response.status_code == 404:
                    missing += 1
                    continue
                response.raise_for_status()
                if record_snapshot(conn, response.json()["data"]):
                    conn.commit()
                    stored += 1
                else:
                    conn.rollback()
                    failed += 1
            except (requests.RequestException, ValueError, KeyError):
                conn.rollback()
                failed += 1
        print(f"Stored {stored}; absent in Paddle {missing}; failed {failed}", flush=True)
        if failed:
            raise SystemExit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
