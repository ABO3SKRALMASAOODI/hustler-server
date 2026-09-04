"""Release status contexts have one comprehensive workflow owner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_render_production_status_has_one_authoritative_writer():
    owners = []
    for path in WORKFLOWS.glob("*.yml"):
        if "context=render-production" in path.read_text(encoding="utf-8"):
            owners.append(path.name)

    assert owners == ["verify-production-release.yml"]


def test_render_release_gate_covers_full_suite_health_and_public_mcp():
    source = (WORKFLOWS / "verify-production-release.yml").read_text(
        encoding="utf-8")

    assert "python -m pytest -q backend/tests" in source
    assert "/healthz" in source
    assert "payload.get(\"commit\")" in source
    assert "verify_public_mcp.py" in source
