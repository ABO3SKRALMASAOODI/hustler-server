"""Every backend execution path chooses the same database route."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database_config import (  # noqa: E402
    preferred_database_route,
    preferred_database_url,
)

ROOT = Path(__file__).resolve().parents[2]


def test_direct_database_is_preferred_when_configured():
    env = {"DATABASE_URL": "postgresql://pool",
           "DIRECT_DATABASE_URL": " postgresql://direct "}

    assert preferred_database_url(env) == "postgresql://direct"
    assert preferred_database_route(env) == "direct"


def test_primary_database_remains_the_default():
    env = {"DATABASE_URL": " postgresql://pool ",
           "DIRECT_DATABASE_URL": ""}

    assert preferred_database_url(env) == "postgresql://pool"
    assert preferred_database_route(env) == "primary"


def test_background_billing_and_email_use_the_shared_route_selector():
    for relative in ("backend/brevo_delivery.py",
                     "backend/paid_subscription_alert.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "preferred_database_url()" in source


def test_checkout_private_connection_uses_working_direct_route(monkeypatch):
    from routes import paddle
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unreachable-pool')
    monkeypatch.setenv('DIRECT_DATABASE_URL', 'postgresql://working-direct')
    calls=[]
    sentinel=object()
    def connect(dsn, **kwargs):
        calls.append((dsn,kwargs))
        return sentinel
    monkeypatch.setattr(paddle.psycopg2, 'connect', connect)
    assert paddle.get_offers_db() is sentinel
    assert calls[0][0]=='postgresql://working-direct'
    assert calls[0][1]['connect_timeout']==3


def test_catalog_hold_uses_same_direct_route_as_checkout(monkeypatch):
    from flask import Flask
    import catalog_rollout
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unreachable-pool')
    monkeypatch.setenv('DIRECT_DATABASE_URL', 'postgresql://working-direct')
    monkeypatch.setattr(catalog_rollout, '_cache', {'hold':False,'expires':0})
    class Conn:
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,args):assert args==(catalog_rollout.KEY,)
        def fetchone(self):return ('true',)
        def close(self):pass
    def connect(dsn,**kwargs):
        assert dsn=='postgresql://working-direct'
        return Conn()
    monkeypatch.setattr(catalog_rollout.psycopg2,'connect',connect)
    with Flask(__name__).app_context():
        assert catalog_rollout.shopfront_held()
