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
    assert '"no-store" in os.environ.get("CACHE_CONTROL"' in source
    assert "--header 'Cache-Control: no-cache'" in source
    assert 'python - "$body_file"' in source
    assert 'RESPONSE="$response"' not in source
    assert "verify_public_mcp.py" in source
    assert "image: postgres:16" in source
    assert source.count("python backend/apply_migrations.py") == 2
    assert "SELECT COUNT(*) FROM schema_migrations" in source


def test_render_startup_has_no_retired_app_builder_recovery_side_effects():
    source = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in source
    assert "exec gunicorn" in source
    assert "os.listdir(OUTPUTS)" not in source
    assert "UPDATE jobs SET state" not in source
