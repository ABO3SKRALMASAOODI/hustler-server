"""Track 4F loop: the owner-ear sound approval flow (agents can never
approve), the critic's read-only verb on the MCP read-only list."""

import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import sound_approval                                         # noqa: E402


def _wav(path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=d=0.6:c=pink:a=0.3", "-af",
                    "afade=t=out:st=0.3:d=0.3", path], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stage_and_approve_need_cc0_and_the_owners_attestation(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "manifest.json").write_text("[]")
    aud = str(tmp_path / "aud")
    src = _wav(str(tmp_path / "rain.wav"))
    with pytest.raises(sound_approval.ApprovalError, match="CC0"):
        sound_approval.stage("sr_x", src, source_url="https://f/1",
                             licence="CC BY 4.0", author="a",
                             audition_dir=aud)
    cid = sound_approval.stage("sr_x", src, source_url="https://f/1",
                               licence="CC0", author="Someone",
                               title="Rain", audition_dir=aud)
    with pytest.raises(sound_approval.ApprovalError, match="attestation"):
        sound_approval.approve("sr_x", cid, role="rain", use="rain on glass",
                               attest="yes", audition_dir=aud,
                               lib_dir=str(lib))
    rec = sound_approval.approve(
        "sr_x", cid, role="rain", use="rain on glass under a quiet line",
        attest=sound_approval.attestation("sr_x"), audition_dir=aud,
        lib_dir=str(lib))
    man = json.loads((lib / "manifest.json").read_text())
    assert rec == "rain_1" and man[0]["license"] == "CC0 1.0"
    assert man[0]["source_url"] == "https://f/1"
    assert (lib / "rain_1.flac").exists()


def test_no_tool_can_reach_the_approval_flow():
    import agent_tools
    import inspect
    for name, spec in agent_tools.TOOLS.items():
        fn = spec[0] if isinstance(spec, tuple) else spec
        mod = getattr(fn, "__module__", "") or ""
        assert "sound_approval" not in mod, name
    d = os.path.join(os.path.dirname(HERE), "tools_ext")
    for f in os.listdir(d):
        if f.endswith(".py"):
            assert "sound_approval" not in open(os.path.join(d, f)).read(), f
    src = inspect.getsource(agent_tools)
    assert "sound_approval" not in src
    assert not [n for n in agent_tools.TOOLS if "approve" in n and "sound"
                in n]


def test_critique_short_is_read_only_everywhere():
    import agent_tools
    assert "critique_short" not in agent_tools.WRITE_TOOLS
    path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "backend",
                        "routes", "mcp.py")
    if os.path.exists(path):
        assert "critique_short" in open(path).read()
