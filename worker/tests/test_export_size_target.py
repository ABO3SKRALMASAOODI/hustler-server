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
    from harness2.envelope import Envelope

    class _Stage:
        edl = {"keep": [[0.0, 20.0]], "export": {"max_size_mb": 4500.0}}
    try:
        assert ops.op_export(_Stage(), {"max_size_gb": 4.5},
                             Envelope(tool="edit"), 0)
    finally:
        ops._run_verb = orig
    assert seen == {"name": "set_export_settings",
                    "args": {"max_size_gb": 4.5}}


# -- review fixes (16 Nov 2026) ---------------------------------------------

def test_a_new_export_target_reuses_the_preview_picture_but_never_a_final():
    """p5857's own case: setting the size on a 59 min film changed nothing a
    preview shows, yet forced a full preview re-render (332-694 s)."""
    import render_plan
    import stitch
    from timeline import Timeline
    plain = schemas.validate_edl({"keep": [[0.0, 10.0]]}, 20.0).model_dump()
    sized = schemas.validate_edl({"keep": [[0.0, 10.0]],
                                  "export": {"max_size_mb": 4500}},
                                 20.0).model_dump()
    assert render_plan.can_reuse_picture(plain, sized)
    tl = Timeline(plain["keep"], [], None)
    windows, reason = stitch.plan(plain, sized, tl, tl, 20.0,
                                  tl.out_duration)
    assert reason is None or "structural" not in reason, reason
    assert stitch.plan_timeline(plain, sized, tl, tl, tl.out_duration,
                                [], [])[2] != \
        "non-timeline structural change"
    # a final made for another target (or none) is never reused or stitched
    assert renderer._same_export_target("preview", plain, sized)
    assert not renderer._same_export_target("final", plain, sized)
    other = dict(sized, export={"max_size_mb": 2000.0, "video_kbps": None})
    assert not renderer._same_export_target("final", sized, other)
    assert renderer._same_export_target("final", sized, copy.deepcopy(sized))
    assert renderer._same_export_target("final", plain, dict(plain))


def test_the_batch_layer_takes_the_size_in_gb_as_every_surface_teaches():
    before = schemas.validate_edl({"keep": [[0.0, 20.0]]}, 20.0).model_dump()
    for value, mb in (({"max_size_gb": 4.5}, 4500.0),
                      ({"max_size_gb": "4.5 GB"}, 4500.0),
                      ({"max_size_mb": "2000"}, 2000.0),
                      ({"max_size_gb": 4.5, "max_size_mb": 4000}, 4000.0),
                      ({"max_size_gb": 4.5, "note": "dvd"}, 4500.0),
                      (4.5, 4500.0)):
        after = edit_batch.apply_batch(
            copy.deepcopy(before),
            [{"action": "set", "layer": "export", "value": value}], 20.0, [])
        edl = after if isinstance(after, dict) and "keep" in after \
            else after[0]
        ex = schemas.validate_edl(edl, 20.0).export
        assert ex.max_size_mb == mb, (value, ex)
    # an upsert in GB patches the size: raised from 2000 MB to 4.5 GB, the
    # bitrate kept (never read as 4.5 MB by the near-spelling repair)
    held = schemas.validate_edl({"keep": [[0.0, 20.0]],
                                 "export": {"max_size_mb": 2000,
                                            "video_kbps": 9000}},
                                20.0).model_dump()
    after = edit_batch.apply_batch(
        copy.deepcopy(held),
        [{"action": "upsert", "layer": "export",
          "value": {"max_size_gb": 4.5}}], 20.0, [])
    edl = after if isinstance(after, dict) and "keep" in after else after[0]
    assert edl["export"] == {"max_size_mb": 4500.0, "video_kbps": 9000}
    rate = schemas.validate_edl({"keep": [[0.0, 20.0]],
                                 "export": {"video_kbps": "8000"}}, 20.0)
    assert rate.export.video_kbps == 8000
    # an export that names nothing is no export: the EDL dumps unchanged
    empty = schemas.validate_edl({"keep": [[0.0, 20.0]],
                                  "export": {"foo": 1}}, 20.0).model_dump()
    assert "export" not in empty


def test_a_failed_size_fit_ships_the_verified_file_and_reports_it(
        tmp_path, monkeypatch):
    path = str(tmp_path / "final.mp4")
    with open(path, "wb") as f:
        f.write(b"\0" * 3_000_000)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    monkeypatch.setattr(renderer.media, "duration_of", lambda p: 4.0)
    monkeypatch.setattr(renderer.media, "probe",
                        lambda p: {"width": 640, "height": 360, "fps": 25})
    monkeypatch.setattr(renderer, "_stream_bytes", lambda p, k: 96_000)

    def boom(*a, **k):
        raise renderer.media.MediaError("No space left on device") \
            if hasattr(renderer.media, "MediaError") else OSError("disk")
    monkeypatch.setattr(renderer.media, "run", boom)
    edl = {"export": {"max_size_mb": 1.0}}
    with pytest.raises(Exception):
        renderer.fit_export_size(edl, path, str(tmp_path))
    # the delivered file is back, byte for byte, and nothing is left over
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha
    assert sorted(os.listdir(tmp_path)) == ["final.mp4"]
    # what the render job ships and reports instead of failing the export
    rep = renderer.export_fit_failed_report(edl, path, OSError("disk full"))
    assert rep["fits"] is False and rep["bytes"] == 3_000_000
    assert rep["target_bytes"] == 1_000_000 and "disk full" in rep["error"]
    text = renderer.export_size_text(rep)
    assert "OVER" in text and "3.0 MB" in text and "shipped" in text


def test_the_job_keeps_the_export_when_the_size_fit_fails():
    """The render job's call site recovers: JobLeaseLost still stops it,
    any other failure ships the file with the measured report."""
    import inspect
    src = inspect.getsource(renderer)
    at = src.index("size_fit = fit_export_size(")
    block = src[at - 200:at + 900]
    assert "except dbx.JobLeaseLost" in block
    assert "export_fit_failed_report(" in block


def test_harness_v2_reports_the_measured_ceiling_as_v1_does():
    from harness2 import ops
    from harness2.envelope import Envelope
    edl = schemas.validate_edl({"keep": [[0.0, 3565.4]]}, 3600.0).model_dump()

    class _Stage:
        def __init__(self):
            self.edl = edl

    stage = _Stage()

    def run_verb(st, name, args, env, index):
        st.edl = dict(st.edl, export={"max_size_mb": 4500.0,
                                      "video_kbps": None})
        return True
    orig = ops._run_verb
    ops._run_verb = run_verb
    try:
        env = Envelope(tool="edit")
        assert ops.op_export(stage, {"max_size_gb": 4.5}, env, 0)
    finally:
        ops._run_verb = orig
    text = env.text()
    assert "at most" in text and "kb/s" in text and "GB" in text, text
    assert "59 min" in text and "(the target is 4.5 GB)" in text
    # the same line v1's reply carries
    assert D.export_ceiling_line(stage.edl) in text
