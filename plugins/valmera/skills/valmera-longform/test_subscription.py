"""Local workflow requests cannot retry into a paid provider."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_footage_proxy_failure_is_one_attempt_with_buffered_deadline(monkeypatch):
    import cinema as C
    C.worker()
    from footage import ask
    from score import call
    seen = []
    def fail(*args, **kwargs):
        seen.append(kwargs)
        raise TimeoutError('buffered response did not arrive')
    monkeypatch.setenv('CODEX_PROXY_LOCAL', '1')
    monkeypatch.setattr(ask.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(call, 'stream', fail)
    with pytest.raises(TimeoutError):
        ask.ask('Review the picture', [], 'test', budget_s=120)
    assert len(seen) == 1
    assert seen[0]['deadline'] == 285.


def test_cached_box_media_does_not_open_network(tmp_path, monkeypatch):
    import footage_box as F
    (tmp_path / 'footage.json').write_text('{"lines": [], "voices": []}')
    monkeypatch.setenv('CODEX_PROXY_LOCAL', '1')
    monkeypatch.setattr(F, 'ssh', lambda *a, **k: pytest.fail('cached media opened SSH'))
    assert F.fetch({}, tmp_path) == dict(lines=[], voices=[])


def test_private_container_installs_guard_before_worker(tmp_path, monkeypatch):
    import footage_box as F
    import subprocess
    (tmp_path / 'input.json').write_text('{}')
    commands = []
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k:
        b'[{"Config":{"Env":["DATABASE_URL=never-inherit","YTDLP_PROXY=fetch-route"],"Image":"worker"},"Image":"sha256:installed"}]')
    monkeypatch.setattr(subprocess, 'call', lambda cmd, **kw: commands.append((cmd,kw)) or 0)
    monkeypatch.setattr(sys, 'argv', ['runner.py','batch',str(tmp_path),str(tmp_path/'cache'),'own-job','44535'])
    with pytest.raises(SystemExit) as done:
        exec(compile(F.RUNNER, 'private_runner', 'exec'), {})
    assert done.value.code == 0
    cmd = commands[0][0]
    assert cmd[cmd.index('--network') + 1] == 'host'
    assert 'CODEX_PROXY_LOCAL=1' in cmd
    assert 'PYTHONPATH='+str(tmp_path)+'/client' in cmd
    assert 'OPENAI_BASE_URL=http://127.0.0.1:44535/v1' in cmd
    assert not any('DATABASE_URL' in arg for arg in cmd)
