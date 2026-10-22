"""The join into the end card (owner, 22 Oct 2026: "once the video ends and
just before the end card shows there's a brief freeze", on every short).

The cause: the programme cut to the card's first frame, which is black, and
the renderer then faded the whole card in from black (0.45 s) on top of the
card's own reveal from black — about 0.15 s of pure black and a dark static
stall before the type appeared. OUTRO_VERSION 15 dissolves the card's head
over the programme's last live frames instead (renderer.outro_card_parts):

  * no black frame and no held frame at the join; the card is fully formed
    on the programme's last frame;
  * only the transition changed: the programme before the dissolve is the
    same frames (the mark included), and every card frame from the end of
    the old fade-in on is md5-identical to the OLD card, rebuilt here
    independently from the asset with the old chain;
  * the sound at the join is untouched;
  * render_qc reports a STALL BEFORE END CARD (black or a held picture in
    the programme's last 1.5 s or the card's first 0.3 s) and names its
    cause, and critique_short / audit_motion read it.
"""
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import config                                                 # noqa: E402
import renderer                                               # noqa: E402
import render_qc                                              # noqa: E402
import w0b_probe                                              # noqa: E402
from timeline import Timeline                                 # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg")
IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": 12.0}}
W, H, FPS = 270, 480, 25.0


def _graph(fps=30.0, keep=20.0, **kw):
    e = {"keep": [[0.0, keep]]}
    tl = Timeline(e["keep"], [])
    return renderer.build_filtergraph(
        e, keep, True, tl, None, [], {"words": []}, False,
        W=1080, H=1920, fps=fps, outro_s=5.0, card_idx=7, **kw)


# ── the graph ────────────────────────────────────────────────────────────

def test_the_card_dissolves_in_and_never_fades_from_black():
    g = _graph()
    j = renderer.outro_join_frames(30.0)
    assert j == 9                                   # 0.3 s at 30 fps
    # no renderer fade-in from black on the card any more...
    assert "fade=t=in:st=0" not in g
    # ...its head's alpha ramps 1/10 .. 9/10 over the last 9 frames
    assert f"fade=t=in:s=0:n={j + 1}:alpha=1" in g
    # the reveal plays to the settled frame (asset frame 11 of 30 fps),
    # which is held for the dissolve's length, then the card's own clock
    settle = math.ceil(config.ENDCARD_SETTLE_S * 30 - 1e-6)
    assert f"loop=loop={j}:size=1:start={settle}" in g
    # the head is laid over the programme's last 9 frames (600 frames)
    assert f"setpts=PTS-STARTPTS+{(600 - j - .25) / 30:.6f}/TB" in g
    # the fade-out at the far edge is the old one
    assert "fade=t=out:st=4.65:d=0.35" in g
    # the sound and the card's length are untouched: concat with silence
    assert "concat=n=2:v=1:a=1[vout][aout]" in g
    assert "anullsrc=r=48000:cl=stereo:d=5.000" in g
    assert "afade=t=out:st=19.75" in g


def test_the_mark_is_drawn_exactly_as_before_then_the_join():
    """The mark is laid on the programme as it always was (the same
    overlay on the same stream, so the same pixels and format negotiation);
    the dissolve comes after it, so the mark leaves with the picture it is
    on and never lands on the card."""
    g = _graph(robot_idx=8, wm_anchor_y=None)
    parts = g.split(";")
    wm = next(i for i, p in enumerate(parts) if "[wmbot]overlay" in p)
    join = next(i for i, p in enumerate(parts) if "[oheadt]overlay" in p)
    assert wm < join
    assert parts[join].startswith("[vwm][oheadt]")
    assert not any("[wmbot]" in p for p in parts
                   if "ocomp" in p or "ovid" in p or "ohead" in p)


def test_the_poster_fallback_has_no_reveal(monkeypatch):
    monkeypatch.setattr(renderer, "endcard_path",
                        lambda: renderer.ENDCARD_POSTER_PATH)
    g = _graph()
    assert "loop=loop=9:size=1:start=0" in g
    assert "fade=t=in:st=0" not in g


@pytest.mark.parametrize("fps", [23.976, 24.0, 25.0, 29.97, 30.0, 50.0,
                                 59.94, 60.0])
def test_join_length_and_settle_frame_at_every_rate(fps):
    j = renderer.outro_join_frames(fps)
    assert abs(j / fps - config.OUTRO_JOIN_S) <= .5 / fps + 1e-9
    # never more than half a very short programme
    assert renderer.outro_join_frames(fps, program_s=.2) <= max(1, fps * .1 + 1)
    g = _graph(fps=fps)
    settle = math.ceil(config.ENDCARD_SETTLE_S * round(fps, 3) - 1e-6)
    # the settled frame shows the asset's frame 11 or later (0.367 s)
    assert settle / round(fps, 3) >= 11 / 30 - 1e-6
    assert f"start={settle}," in g


def test_anamorphic_programme_gets_its_head_on_its_own_pixels():
    e = {"keep": [[0.0, 10.0]]}
    tl = Timeline(e["keep"], [])
    g = renderer.build_filtergraph(
        e, 10.0, True, tl, None, [], {"words": []}, False, W=720, H=576,
        fps=25.0, outro_s=5.0, card_idx=3, src_sar=16 / 15, src_fps=25.0)
    # the card is composed at the display width, the head scaled back to
    # the programme's coded pixels before the dissolve
    assert "color=c=black:s=768x576" in g
    assert ",scale=720:576[oheadp]" in g


def test_a_stitched_final_never_reencodes_inside_the_dissolve():
    src = open(os.path.join(os.path.dirname(HERE), "renderer.py")).read()
    assert "outro_join_frames(fps, dur_out)" in src
    assert "edge -= (outro_join_frames(fps, edge) + 1) / fps" in src


# ── real renders (lossless, the probe lane) ──────────────────────────────

@pytest.fixture(scope="module")
def joined(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("endcard_join"))
    src = w0b_probe.make_source(os.path.join(d, "src25.mp4"), rate="25")
    edl = {"keep": [[0.0, 4.0], [5.0, 9.0]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    cases = [
        {"name": "card", "src": src, "idx": IDX, "edl": edl,
         "want_wm": True},
        {"name": "nocard", "src": src, "idx": IDX, "edl": edl,
         "want_wm": True, "suppress_outro": True},
    ]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        res = w0b_probe.run(cases, os.path.join(d, "r"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old
    res["_dir"] = os.path.join(d, "r")
    res["_ref"] = _old_card_reference(os.path.join(d, "old_card.mp4"))
    return res


def _old_card_reference(path):
    """The OUTRO_VERSION 14 card, rebuilt from the asset with the old chain
    (composite on black, 0.45 s fade-in from black, 0.35 s fade-out),
    encoded losslessly: frame md5s and the first frames' peak luma."""
    cw, ch = renderer._even(W * .98), renderer._even(H * .98)
    g = (f"color=c=black:s={W}x{H}:r={FPS:.3f}:d=5.000,format=rgba[obg];"
         f"[0:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
         "format=rgba[ocard];[obg][ocard]overlay=(W-w)/2:(H-h)/2:"
         "shortest=0[ocomp];[ocomp]fade=t=in:st=0:d=0.45,"
         "fade=t=out:st=4.65:d=0.35,format=yuv420p,setsar=1[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1",
                    "-t", "5.000", "-i", renderer.ENDCARD_PATH,
                    "-filter_complex", g, "-map", "[v]", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "0", "-pix_fmt",
                    "yuv420p", path], check=True)
    return {"md5": w0b_probe.frame_md5s(path), "path": path}


def _gray(path, ss, n):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                          f"select=gte(n\\,{ss}),scale={W}:{H},format=gray",
                          "-fps_mode", "passthrough",
                          "-frames:v", str(n), "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W)


@needs_ffmpeg
def test_only_the_transition_changed(joined):
    a, b = joined["card"], joined["nocard"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    j = renderer.outro_join_frames(FPS)
    assert len(a["frames"]) == 200 + 125 and len(b["frames"]) == 200
    # the programme (its mark included) is the same frames up to the
    # dissolve, and every dissolve frame is new
    assert a["frames"][:200 - j] == b["frames"][:200 - j]
    assert all(x != y for x, y in zip(a["frames"][200 - j:200],
                                      b["frames"][200 - j:200]))
    # from the end of the old fade-in, the card is the OLD card, frame for
    # frame; only the transition frames before it changed
    ref = joined["_ref"]["md5"]
    k0 = math.ceil(0.45 * FPS)
    assert a["frames"][200 + k0:] == ref[k0:125]
    assert a["frames"][200:200 + k0] != ref[:k0]
    # the sound under the join is the one the card-less programme ends on:
    # the outro is still its silent 5 s, concatenated
    assert a["duration"] == pytest.approx(13.0, abs=.05)


@needs_ffmpeg
def test_no_black_and_no_held_frame_at_the_join(joined):
    path = os.path.join(joined["_dir"], "card.mp4")
    j = renderer.outro_join_frames(FPS)
    frames = _gray(path, 200 - j - 2, j + 2 + 12)
    # the old card opened on black: its first frames show nothing
    old = _gray(joined["_ref"]["path"], 0, 3)
    assert max(int(f.max()) for f in old) < render_qc.STALL_CARD_DARK
    # the new join: every frame carries picture or card, and every frame
    # differs from the one before until the card's own hold
    assert all(int(f.max()) >= render_qc.STALL_CARD_DARK for f in frames)
    diffs = [float(np.abs(frames[i].astype(int) - frames[i - 1]).mean())
             for i in range(1, j + 3)]
    assert min(diffs) > render_qc.STALL_HELD_DIFF
    # the card's first frame is all but settled (its reveal finished in
    # the dissolve)
    settled = frames[-1].astype(int)
    first = frames[j + 2].astype(int)
    assert np.abs(settled - first).mean() < 1.0
    # and the picture check says so
    p = {"program_s": 8.0, "fps": FPS, "W": W, "H": H,
         "endcard": {"s": 5.0}, "tail": render_qc.tail_plan(
             {"keep": [[0.0, 4.0], [5.0, 9.0]]})}
    assert render_qc.boundary_stall(path, p, 1e12) == []


@needs_ffmpeg
def test_the_old_join_is_reported_as_a_stall(joined, tmp_path):
    """The OUTRO_VERSION 14 join (programme, then the card from black) is a
    STALL BEFORE END CARD the check names as the renderer's join."""
    prog = os.path.join(joined["_dir"], "nocard.mp4")
    old = str(tmp_path / "old_join.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", prog, "-i",
                    joined["_ref"]["path"], "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                    old], check=True)
    p = {"program_s": 8.0, "fps": FPS, "W": W, "H": H,
         "endcard": {"s": 5.0}, "tail": {}}
    rows = render_qc.boundary_stall(old, p, 1e12)
    assert [r[0] for r in rows] == ["black"]
    kind, t0, t1, code, words = rows[0]
    assert code == "join" and t0 == pytest.approx(8.0)
    assert .1 <= t1 - t0 <= .25
    text = render_qc.stall_findings(rows, p)[0]
    assert text.startswith("STALL BEFORE END CARD 8.00-")


# ── the causes it names ──────────────────────────────────────────────────

def test_tail_plan_names_the_edits_own_stalls():
    # 5 s at 1x, the last source second slowed to 0.25x (4 s), then a
    # still spliced after it: 9.8 s, all three in the last 1.5 s
    edl = {"keep": [[0.0, 6.0]],
           "speed": [{"id": "s1", "start": 5.0, "end": 6.0, "factor": 0.25}],
           "inserts": [{"id": "ff", "asset_key": "x.png", "kind": "image",
                        "at_output_s": 9.0, "duration_s": 0.8}],
           "effects": {"fade_out_s": 0.6}}
    tp = render_qc.tail_plan(edl)
    assert tp["fade_out_s"] == 0.6
    assert tp["stills"] and tp["stills"][0][2] == "ff"
    assert tp["slow"] and tp["slow"][0][2] == 0.25
    notes = render_qc.tail_causes(edl)
    assert any("'ff'" in n for n in notes)
    assert any("0.25x" in n for n in notes)
    assert any("fade-out" in n for n in notes)
    plain = {"keep": [[0.0, 9.0]]}
    assert render_qc.tail_causes(plain) == []


def test_stall_causes():
    p = {"program_s": 9.0, "tail": {"fade_out_s": 0.6,
                                    "stills": [[7.0, 8.0, "ff", True]],
                                    "slow": [[8.0, 9.0, 0.25]]}}
    assert render_qc._stall_cause("black", 9.0, 9.2, p)[0] == "join"
    assert render_qc._stall_cause("black", 8.6, 9.0, p)[0] == "fade_out"
    assert render_qc._stall_cause("black", 7.6, 7.8,
                                  dict(p, tail={}))[0] == "picture"
    assert render_qc._stall_cause("held", 7.2, 7.5, p)[0] == "hold"
    assert render_qc._stall_cause("held", 8.2, 8.5, p)[0] == "slow"
    assert render_qc._stall_cause("held", 8.9, 9.1,
                                  dict(p, tail={}))[0] == "join"
    assert render_qc._stall_cause("held", 8.0, 8.3,
                                  dict(p, tail={}))[0] == "footage"


def test_audit_motion_reads_the_tail():
    import motion_qa
    edl = {"keep": [[0.0, 9.0]],
           "speed": [{"id": "s1", "start": 8.0, "end": 9.0, "factor": 0.25}]}
    res = motion_qa.audit(edl, {"words": [], "video": {"duration": 9.0}})
    codes = [f["code"] for f in res["findings"]]
    assert "stall_before_end_card" in codes


def test_critique_reads_the_render_report():
    import motion_critic
    rows = [["black", 9.0, 9.16, "join", "the end card opens on black"],
            ["black", 8.6, 9.0, "fade_out", "the edit's own fade-out"]]
    out, meas = motion_critic.lens_ending(
        None, {}, {"width": W, "height": H, "fps": FPS}, final=True,
        report={"picture_qc": {"stall": rows}})
    assert meas["stall"] == rows
    assert [f["code"] for f in out] == ["stall_before_end_card"] * 2
    assert [f["severity"] for f in out] == ["blocking", "advisory"]
