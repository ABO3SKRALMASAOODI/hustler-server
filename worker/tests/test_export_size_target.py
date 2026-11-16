"""The exported file fits the size the customer needs (p5857, ticket 77,
16 Nov 2026): "make the download less than 4.5GB so it can fit onto a DVD".

The editor looked for export_final, set_export_settings and set_bitrate and
found nothing, so a size could never be met and the reply told the customer
to check the size themselves. These tests prove the outcome: the target is
reachable from every editing surface, the export's picture is encoded under
the bitrate that fits, and a delivered file that still lands over is
measured and re-encoded until it fits."""

import copy
import hashlib
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import agent_tools                                         # noqa: E402
import edit_batch                                          # noqa: E402
import renderer                                            # noqa: E402
import schemas                                             # noqa: E402
from test_w1d_tools import Ctx                             # noqa: E402
from tools_ext import delivery_tools as D                  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="needs ffmpeg")


def _noise(path, seconds=4.0, w=640, h=360, venc=None):
    """A grain-heavy picture (x264 cannot make it small) with 192k sound."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=s={w}x{h}:r=25,noise=alls=40:allf=t+u", "-f", "lavfi",
         "-i", "sine=f=220:sample_rate=48000", "-t", f"{seconds}",
         "-map", "0:v", "-map", "1:a",
         *(venc or ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12"]),
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", path],
        check=True)
    return path


def test_an_edl_carries_the_export_target_and_older_edls_are_unchanged():
    plain = schemas.validate_edl({"keep": [[0.0, 10.0]]}, 20.0).model_dump()
    sized = schemas.validate_edl({"keep": [[0.0, 10.0]],
                                  "export": {"max_size_mb": 4500}},
                                 20.0).model_dump()
    assert sized["export"] == {"max_size_mb": 4500.0, "video_kbps": None}
    assert plain.get("export") is None
    # every stored EDL keeps its signature (and its cached renders)
    assert schemas.edl_signature(plain) == schemas.edl_signature(
        {k: v for k, v in plain.items() if k != "export"})
    assert schemas.edl_signature(plain) != schemas.edl_signature(sized)


def test_a_59_minute_film_is_given_a_bitrate_that_fits_a_dvd():
    dur = 3565.4 + 5.0                    # p5857's cut plus the end card
    target = 4500 * 1_000_000
    kbps = renderer.export_video_kbps({"export": {"max_size_mb": 4500}}, dur)
    assert 8000 < kbps < 10000
    ceiling = ((kbps + renderer.EXPORT_AUDIO_KBPS) * 1000 / 8 * dur
               * (1 + renderer.EXPORT_CONTAINER_HEADROOM)
               + renderer.EXPORT_CONTAINER_FIXED_BYTES)
    assert ceiling < target
    enc = renderer.final_video_encode(1920, 1080, 29.97, kbps)
    assert enc[enc.index("-maxrate") + 1] == f"{kbps}k"
    assert enc[enc.index("-bufsize") + 1] == f"{kbps}k"
    # no target: today's export, byte for byte
    assert renderer.final_video_encode(1920, 1080, 29.97) == \
        renderer.final_video_encode(1920, 1080, 29.97, None)
    assert renderer.export_video_kbps({"keep": [[0, 1]]}, dur) is None


@needs_ffmpeg
def test_the_export_encode_lands_under_the_target(tmp_path):
    """The final's own encode (constrained VBR at the target) fits, measured."""
    dur, target_mb = 4.0, 1.2
    kbps = renderer.export_video_kbps({"export": {"max_size_mb": target_mb}},
                                      dur)
    free = _noise(str(tmp_path / "free.mp4"), dur,
                  venc=renderer.final_video_encode(640, 360, 25.0))
    held = _noise(str(tmp_path / "held.mp4"), dur,
                  venc=renderer.final_video_encode(640, 360, 25.0, kbps))
    assert os.path.getsize(free) > target_mb * 1e6     # today: over
    assert os.path.getsize(held) <= target_mb * 1e6    # with the target: fits


@needs_ffmpeg
def test_a_delivered_file_over_the_target_is_re_encoded_until_it_fits(
        tmp_path):
    path = _noise(str(tmp_path / "final.mp4"), 4.0)
    before = os.path.getsize(path)
    assert before > 1_000_000
    rep = renderer.fit_export_size({"export": {"max_size_mb": 1.0}}, path,
                                   str(tmp_path))
    after = os.path.getsize(path)
    assert rep["fits"] and rep["bytes"] == after <= 1_000_000
    assert rep["first_bytes"] == before and rep["passes"] >= 1
    assert abs(float(renderer.media.duration_of(path)) - 4.0) < 0.1
    assert "fits" in renderer.export_size_text(rep)
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith("fit_")]
    assert leftovers == []


@needs_ffmpeg
def test_a_file_already_under_the_target_is_untouched(tmp_path):
    path = _noise(str(tmp_path / "final.mp4"), 2.0, w=160, h=90,
                  venc=["-c:v", "libx264", "-crf", "35"])
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    rep = renderer.fit_export_size({"export": {"max_size_mb": 50}}, path,
                                   str(tmp_path))
    assert rep["fits"] and rep["passes"] == 0
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha
    assert renderer.fit_export_size({"keep": [[0, 1]]}, path,
                                    str(tmp_path)) is None


def test_the_editor_sets_a_dvd_size_and_reads_the_measured_ceiling():
    ctx = Ctx({"keep": [[0.0, 20.0]]})
    out = D.set_export_settings(ctx, max_size_gb=4.5)
    assert out.startswith("EDL v"), out
    assert ctx.edl["export"]["max_size_mb"] == 4500.0
    assert "at most" in out and "kb/s" in out and "4.5 GB" in out
    # strings the model writes are read as their number
    ctx2 = Ctx({"keep": [[0.0, 20.0]]})
    assert D.set_export_settings(
        ctx2, max_size_gb="4.5 GB").startswith("EDL v")
    assert ctx2.edl["export"]["max_size_mb"] == 4500.0
    out = D.set_export_settings(ctx, clear=True)
    assert out.startswith("EDL v") and ctx.edl.get("export") is None


def test_the_target_is_reachable_from_every_editing_surface():
    # v1 and MCP (every agent_tools.TOOLS write verb is on the MCP surface;
    # the delivery department loads with load_tools)
    import tools_ext
    assert "set_export_settings" in agent_tools.TOOLS
    assert "set_export_settings" in agent_tools.WRITE_TOOLS
    assert "set_export_settings" in tools_ext.EXT_DEPARTMENTS["delivery"]
    assert not tools_ext.is_disabled("set_export_settings")
    # the MCP / v1 batch path
    before = schemas.validate_edl({"keep": [[0.0, 20.0]]}, 20.0).model_dump()
    after = edit_batch.apply_batch(
        copy.deepcopy(before),
        [{"action": "upsert", "layer": "export",
          "value": {"max_size_mb": 4500}}], 20.0, [])
    edl = after if isinstance(after, dict) and "keep" in after else None
    if edl is None:                      # apply_batch returns (edl, ...)
        edl = after[0]
    assert schemas.validate_edl(edl, 20.0).export.max_size_mb == 4500.0
    # harness v2's op
    from harness2 import ops, schema_ops
    assert "export" in ops.HANDLERS and "export" in schema_ops.op_names()
    seen = {}

    def run_verb(stage, name, args, env, index):
        seen.update(name=name, args=args)
        return True
    orig = ops._run_verb
    ops._run_verb = run_verb
    try:
        assert ops.op_export(None, {"max_size_gb": 4.5}, None, 0)
    finally:
        ops._run_verb = orig
    assert seen == {"name": "set_export_settings",
                    "args": {"max_size_gb": 4.5}}
