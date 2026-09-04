"""Choose the backend database route consistently across request and jobs."""

import os


def preferred_database_url(environ=None):
    """Prefer the explicitly configured direct route, then the pool route.

    Render currently carries both. Background billing/newsletter code already
    preferred the direct route, while request handlers used only the pool;
    a pool outage therefore split one service into working and broken halves.
    """
    env = os.environ if environ is None else environ
    return (str(env.get("DIRECT_DATABASE_URL") or "").strip()
            or str(env.get("DATABASE_URL") or "").strip()
            or None)


def preferred_database_route(environ=None):
    env = os.environ if environ is None else environ
    return ("direct" if str(env.get("DIRECT_DATABASE_URL") or "").strip()
            else "primary")
