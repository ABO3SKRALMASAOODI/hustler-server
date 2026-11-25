import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import subscription_box as SB


def test_box_client_reaches_the_tunnel_port_whatever_the_macs_default(monkeypatch):
    files = SB.client_files(41234)
    text = files['client/proxy_client.py'].decode()
    assert "BASE = 'http://127.0.0.1:41234/v1'" in text
    assert text.count('\nBASE = ') == 1


def test_bridge_forwards_to_the_proxys_home_port(monkeypatch):
    monkeypatch.delenv('CODEX_PROXY_PORT', raising=False)
    assert SB.local_port() == 8788
    monkeypatch.setenv('CODEX_PROXY_PORT', '8790')
    assert SB.local_port() == 8790


@pytest.mark.parametrize("configured", [True, False], ids=["configured", "default"])
def test_copied_probe_uploads_resolved_worker(tmp_path, monkeypatch, configured):
    """An installed skill has no sibling worker. Inspect the bytes sent to the box."""
    from contextlib import nullcontext
    import importlib.util
    import io
    import json
    from pathlib import Path
    import shlex
    import shutil
    import tarfile
    from types import SimpleNamespace

    here = Path(__file__).resolve().parent
    monkeypatch.syspath_prepend(str(here.parent / '_shell'))
    import brand
    worker = Path(brand.worker_dir())
    installed = tmp_path / 'install/plugins/valmera/skills/valmera-longform/footage_box.py'
    installed.parent.mkdir(parents=True)
    monkeypatch.setattr(brand, 'HERE', str(installed.parent.parent / '_shell'))
    monkeypatch.setattr(brand, 'V', str(tmp_path))
    if configured:
        monkeypatch.setenv('VALMERA_WORKER', str(worker))
    else:
        monkeypatch.delenv('VALMERA_WORKER', raising=False)
        synced_worker = tmp_path / 'worktrees/skill-main/worker'
        synced_worker.parent.mkdir(parents=True)
        synced_worker.symlink_to(worker, target_is_directory=True)
    shutil.copyfile(here / installed.name, installed)
    spec = importlib.util.spec_from_file_location('installed_box', installed)
    box = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(box)
    monkeypatch.setenv('CODEX_PROXY_LOCAL', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'local-test')
    monkeypatch.setenv('VALMERA_RUN', 'probe-copy')
    monkeypatch.setenv('CODEX_PROXY_PROOF', 'factory-copy')
    monkeypatch.setattr(SB, 'bridge', lambda host, key: nullcontext(41234))
    idea = dict(title='Copied probe', story='A source to check', named=[dict(name='Archive')])
    expected = dict(covered=1, clips=1, subjects=[dict(name='Archive')], ok=True)
    uploaded = {}

    def local_box(command, **kw):
        if command.startswith('docker ps'):
            return SimpleNamespace(stdout='vbox-batch-test\n')
        if command.startswith('tar xf'):
            with tarfile.open(fileobj=io.BytesIO(kw['input'])) as package:
                for name in package.getnames():
                    uploaded[name] = package.extractfile(name).read()
        elif '/runner.py ' in command:
            assert json.loads(uploaded['input.json']) == dict(_probe=True, idea=idea)
            for helper in ('ask', 'hear'):
                assert uploaded[f'footage_{helper}.py'] == (worker / 'footage' / (helper + '.py')).read_bytes()
            assert uploaded['fetch.py'] == box.PROBE.encode()
            assert b'127.0.0.1:41234/v1' in uploaded['client/proxy_client.py']
            assert 'factory-copy' in shlex.split(command)
            assert 'probe-copy' in shlex.split(command)
        elif command.startswith('tar cf'):
            with tarfile.open(fileobj=kw['stdout'], mode='w') as result:
                data = json.dumps(expected).encode()
                member = tarfile.TarInfo('probe.json')
                member.size = len(data)
                result.addfile(member, io.BytesIO(data))

    monkeypatch.setattr(box, 'ssh', local_box)
    result = box.probe(idea)
    assert {key: result[key] for key in expected} == expected
