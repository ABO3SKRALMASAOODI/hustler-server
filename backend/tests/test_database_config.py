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
