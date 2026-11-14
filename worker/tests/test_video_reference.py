import base64
from PIL import Image
import pytest
import videogen
import os
import subprocess
import sys
from pathlib import Path


def test_private_reference_is_exact_image_bytes_not_unreachable_url(tmp_path):
    path = tmp_path / 'reference.png'
    Image.new('RGB', (24, 24), (30, 90, 140)).save(path)
    value = videogen.reference_image(str(path))
    assert value.startswith('data:image/png;base64,')
    assert base64.b64decode(value.split(',', 1)[1]) == path.read_bytes()


def test_non_image_cannot_be_sent_as_reference(tmp_path):
    path = tmp_path / 'bad.jpg'
    path.write_text('<html>not an image</html>')
    with pytest.raises(Exception):
        videogen.reference_image(str(path))


def test_configured_provider_is_actually_loadable_by_the_editor():
    env = dict(os.environ, FAL_KEY='test-only-not-a-secret', VIDEO_PROVIDER='fal')
    run = subprocess.run([sys.executable, '-c',
        "import agent_tools; assert 'generate_video' in agent_tools.TOOLS; "
        "assert 'generate_video' in agent_tools.TOOL_DOMAINS['acquisition']; "
        "assert any(t['function']['name']=='generate_video' for t in agent_tools.openai_tools())"],
        env=env, cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr[-1200:]


def test_provider_gate_is_consistent_without_mutating_registry(monkeypatch):
    import agent_tools
    import config
    import skill_validator
    assert 'generate_video' in agent_tools.TOOLS
    assert set(agent_tools.TOOLS) == skill_validator.live_tool_names()
    for key, available in [('', False), ('test-only-not-a-secret', True)]:
        monkeypatch.setattr(config, 'FAL_KEY', key)
        monkeypatch.setattr(config, 'VIDEO_PROVIDER', 'fal')
        names = {t['function']['name'] for t in agent_tools.openai_tools()}
        assert ('generate_video' in names) is available
        assert agent_tools._tool_disabled('generate_video') is not available
        assert ('generate_video' in agent_tools.tool_domain_map()['acquisition']) is available
