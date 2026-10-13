"""Short-lived shopfront hold while web and executor revisions roll out.

The new contracts are already resolvable for signed payments. The hold only
keeps September offers usable by the previous frontend until the operator has
verified the new editor lanes and published the new frontend. No entitlements
or existing subscriptions are changed by this switch.
"""
import os
import time
import threading
import psycopg2
from flask import has_app_context

_lock = threading.Lock()
_cache = {"hold": False, "expires": 0.0}
KEY = "catalog_20261013_shopfront_hold"


def shopfront_held():
    if not has_app_context():
        return False
    with _lock:
        if time.monotonic() < _cache["expires"]:
            return _cache["hold"]
        conn = None
        try:
            # A private read cannot commit or roll back a caller's billing work.
            conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=3,
                options='-c default_transaction_read_only=on -c statement_timeout=1500')
            with conn.cursor() as cur:
                cur.execute('SELECT value FROM app_kv WHERE key=%s', (KEY,))
                row = cur.fetchone()
            _cache['hold'] = bool(row and str(row[0]).strip().lower() == 'true')
        except Exception:
            # Keep the last known release state during a transient DB outage.
            pass
        finally:
            if conn is not None:
                conn.close()
        _cache['expires'] = time.monotonic() + 5
        return _cache['hold']
