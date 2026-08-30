"""Dependency-free regression checks for Python scope hazards in agent_loop."""

import ast
from pathlib import Path


WORKER = Path(__file__).resolve().parents[1]


def test_quota_fallback_metric_is_not_an_unbound_closure_variable():
    """Aug 27: assigning ``_metric`` later in the agent step made Python
    capture it as a local, so the quota-fallback closure crashed before that
    assignment with ``cannot access free variable '_metric'``. Keep metric
    writes explicit and forbid another local alias with the helper's name.
    """
    source = (WORKER / "agent_loop.py").read_text()
    tree = ast.parse(source)

    assert 'agent_tools._metric(ctx, "provider_quota_fallbacks", 1)' in source
    assert not [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "_metric"
        and isinstance(node.ctx, ast.Store)
    ]
