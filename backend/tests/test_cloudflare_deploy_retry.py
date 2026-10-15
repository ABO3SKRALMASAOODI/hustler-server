"""Exercise the actual deployment retry block under GitHub's default bash -e."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


@pytest.mark.parametrize("mode,attempts,exit_code", [
    ("success", 1, 0), ("transient", 2, 0),
    ("permanent", 1, 1), ("persistent", 8, 1),
    # A previous container rollout still replacing active instances refuses
    # the new one; the release waits it out instead of failing (Oct 2026).
    ("rollout", 3, 0),
])
def test_deploy_retry_keeps_wrangler_status_through_tee(tmp_path, mode, attempts, exit_code):
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-cloudflare-executor.yml"
    source = workflow.read_text()
    start = source.index("          # Container updates can partially apply")
    end = source.index("\n      - name: install runtime secrets", start)
    script = textwrap.dedent(source[start:end])
    counter = tmp_path / "count"
    npx = tmp_path / "npx"
    npx.write_text('''#!/bin/bash
count=$(cat "$RETRY_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$RETRY_COUNT"
if [ "$RETRY_MODE" = success ] || { [ "$RETRY_MODE" = transient ] && [ "$count" -gt 1 ]; } || { [ "$RETRY_MODE" = rollout ] && [ "$count" -gt 2 ]; }; then exit 0; fi
if [ "$RETRY_MODE" = permanent ]; then echo "invalid configuration";
elif [ "$RETRY_MODE" = rollout ]; then echo 'Error rolling out application "valmera-executor-valmerabatch": a rollout is already in progress';
else echo "Internal Server Error"; fi
exit 1
''')
    npx.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "RETRY_COUNT": str(counter), "RETRY_MODE": mode,
           "GITHUB_SHA": "test", "source_version": "test"}
    result = subprocess.run(["bash", "-e", "-c", script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert int(counter.read_text()) == attempts
