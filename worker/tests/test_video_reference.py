import base64
from PIL import Image
import pytest
import videogen
import os
import subprocess
import sys


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
        env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr[-1200:]
