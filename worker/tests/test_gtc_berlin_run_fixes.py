"""GTC Berlin run (w1024-gtc-berlin-v-NGxyPjcrE, 24 Oct 2026): the defects
its editors hit, pinned.

1. apply_recipe(at=) refused the recipe suggest_recipes had just ranked
   first, on the very moment it named (4629 lab_scan at=25.74 on
   'software', 4628 lab_scan at=27.62 on '350': "no mechanism or number
   word after the hook"): the moment `at` names became a payoff whose hold
   kept every non-payoff recipe off it.
2. An overlay cut from a long original (the project's own 92-min keynote,
   source_start_s 1661.3) was read from the file's first frame: preview job
   106663 decoded 27 minutes of 1080p before the overlay's first frame and
   returned 24.73 s of a 37.69 s edit.
3. PICTURE CHECK said clean while slams, counters and lists sat on the
   speaker's eyes and mouth (4634 v21, 4656 v28, 4629 v26): it now measures
   each graphic's ink over the face read on the source frame, and
   allow_face_overlap (set on every graphic to stop the keep-out moving
   them) no longer silences it.
4. set_picture_card with a new source rect moved the card to the default
   box (4654 c3, job 106772).
5. STALL BEFORE END CARD flagged the keynote stream's own 0.13 s repeats on
   live endings (4637, 4633 render 106814).
6. set_caption_fixes kept one of two spellings without a word and wrote
   'cuDNN,' onto a bare 'CUDNN' (4628 job 106788).
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import recipes  # noqa: E402
from schemas import default_edl  # noqa: E402


# ── 1. apply_recipe(at=) lands what suggest_recipes ranked ────────────────

TEXT = ("everyone thought the factory would take ten years to build but "
        "instead we did it in nineteen months and the reason is simple "
        "the machines build the machines so the cost dropped by ninety "
        "percent and that is why the price is lower now than ever")


def _index(duration=30.0):
    words, t = [], 0.4
    for i, w in enumerate(TEXT.split()):
        words.append({"w": w, "t0": round(t, 3), "t1": round(t + 0.32, 3),
                      "i": i, "p": 0.99})
        t += 0.42
    return {"words": words, "video": {"duration": duration, "fps": 25.0,
                                      "width": 1920, "height": 1080},
            "sentences": [], "shots": [], "silences": []}


def _ctx():
    from w0c_support import tool_context
    edl = default_edl(30.0)
    edl["keep"] = [[0.0, 26.0]]
    ctx, fake = tool_context(edl, duration=30.0, user_message="premium")
    ctx.index = _index()
    return ctx, fake


def test_apply_recipe_at_writes_every_move_suggest_recipes_ranked(
        monkeypatch):
    import agent_tools
    import tools_ext.recipe_tools as rt
    # the lab scan's registered stills come from the frame (an image job);
    # here they stand in, so the test reads only the landing
    monkeypatch.setattr(rt, "_variant_keys",
                        lambda ctx, edl, t: list(recipes.STILLS_PLACEHOLDER))
    ctx, _fake = _ctx()
    edl = ctx.latest_edl()["json"]
    ranked = recipes.rank(rt._plan(ctx, edl), matte_ok=True, edl=edl)
    names = [r["name"] for r in ranked]
    assert "lab_scan" in names, names
    payoff_t = rt._plan(ctx, edl)["payoff"]["t"]
    tried = 0
    for r in ranked:
        if abs(float(r["at"]) - float(payoff_t)) <= 1.5:
            continue            # at= the planner's own payoff: unchanged path
        ctx, fake = _ctx()
        out = agent_tools.TOOLS["apply_recipe"][0](ctx, name=r["name"],
                                                   at=r["at"])
        assert "does not fit" not in out, (r["name"], r["at"], out)
        if recipes.RECIPES[r["name"]].assets:
            continue            # the stand-in stills are not project media
        assert out.startswith("EDL v"), (r["name"], r["at"], out)
        fx = fake.rows[-1]["json"]["fx"]
        # the move lands at `at`: a window opens or closes there (a grey
        # setup ends ON the turn it names)
        edges = [float(i[k]) for i in fx for k in ("start", "end")
                 if str(i.get("id", "")).startswith(f"rc_{r['name']}_")]
        assert edges and min(abs(e - float(r["at"])) for e in edges) <= 1.5
        tried += 1
    assert tried >= 2


def test_a_named_landing_is_not_a_reserved_payoff_hold():
    plan = {"program_s": 20.0, "fps": 30.0, "sections": [(0.0, 20.0)],
            "moments": [{"kind": "stress", "t": 8.0, "strength": 0.9,
                         "word": "software", "tags": [], "section": 0,
                         "bind": {"on": "time", "ref": 8.0}}]}
    m = plan["moments"][0]
    held = dict(plan, payoff={"t": 8.0, "moment": m, "hold_end": 9.5})
    assert recipes._in_hold(held, 8.0)            # the planner's payoff hold
    named = dict(plan, payoff={"t": 8.0, "moment": m, "hold_end": 9.5,
                               "named": True})
    assert not recipes._in_hold(named, 8.0)
    items, landing, why = recipes.build("lab_scan", named,
                                        variant_keys=recipes.STILLS_PLACEHOLDER)
    assert items and landing["t"] == 8.0, why


# ── 2. an overlay is a bounded read of its window ──────────────────────────

def test_a_video_overlay_seeks_to_its_window_and_a_still_loops():
    import renderer
    item = {"id": "dsx", "kind": "video", "asset_key": "originals/4621/x.mp4",
            "start": 29.0, "duration_s": 3.7, "source_start_s": 1661.3}
    args, graph = renderer._overlay_input(item, "/m/x.mp4", 30.0,
                                          copy_timestamps=True)
    assert args[:2] == ["-ss", "1660.300"] and args[-2:] == ["-i", "/m/x.mp4"]
    assert float(args[args.index("-t") + 1]) >= 1.0 + 3.7 + 0.35
    assert graph["source_start_s"] == 1661.3     # -copyts keeps the clock
    _args, rebased = renderer._overlay_input(item, "/m/x.mp4", 30.0)
    assert rebased["source_start_s"] == 1.0      # trimmed on the input clock
    assert item["source_start_s"] == 1661.3      # the document is untouched
    still = dict(item, kind="image", asset_key="a.png")
    sargs, sgraph = renderer._overlay_input(still, "/m/a.png", 30.0)
    assert sargs[:2] == ["-loop", "1"] and sgraph is still


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_an_overlay_late_in_a_long_source_renders_the_same_pixels(
        tmp_path, monkeypatch):
    import media
    import renderer
    from schemas import validate_edl
    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=96x54:r=25:d=90", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=90", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "50", "-c:a", "aac",
                    str(src)], check=True, timeout=120)
    edl = default_edl(90.0)
    edl["keep"] = [[20.0, 26.0]]                 # -copyts main read
    edl["overlays"] = [{"id": "ov", "kind": "video", "asset_key": "own.mp4",
                        "start": 2.0, "duration_s": 2.0, "x": 0.5,
                        "y": 0.5, "scale": 0.5, "source_start_s": 80.0}]
    edl = validate_edl(edl, 90.0).model_dump()
    seen = []
    real = media.run

    def spy(cmd, *a, **k):
        seen.append(list(cmd))
        return real(cmd, *a, **k)
    monkeypatch.setattr(media, "run", spy)
    out = tmp_path / "out.mp4"
    wd = tmp_path / "wd"
    wd.mkdir()
    dur = renderer.render_edl(edl, {}, str(src), str(out), str(wd), True,
                              asset_locals={"own.mp4": str(src)},
                              suppress_outro=True)
    assert abs(dur - 6.0) < 0.2
    main = [c for c in seen if "-filter_complex" in c][-1]
    second = [k for k, x in enumerate(main) if x == "-i"][1]
    assert "-ss" in main[second - 7:second], main[second - 7:second + 1]
    # the overlay's pixels are those of source 81.0 s at program 3.0 s:
    # the same frame a full read of the file gives
    def frame(path, t):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i",
                               str(path), "-frames:v", "1", "-f", "rawvideo",
                               "-pix_fmt", "gray", "-"], check=True,
                              capture_output=True).stdout
    monkeypatch.setattr(renderer, "_overlay_input",
                        lambda item, local, fps, copy_timestamps=False: (
                            ["-i", local], item))
    full = tmp_path / "full.mp4"
    wd2 = tmp_path / "wd2"
    wd2.mkdir()
    renderer.render_edl(edl, {}, str(src), str(full), str(wd2), True,
                        asset_locals={"own.mp4": str(src)},
                        suppress_outro=True)
    for t in (2.5, 3.0, 3.6):
        assert frame(out, t) == frame(full, t)
    bare = tmp_path / "bare.mp4"
    wd3 = tmp_path / "wd3"
    wd3.mkdir()
    renderer.render_edl(dict(edl, overlays=[]), {}, str(src), str(bare),
                        str(wd3), True, suppress_outro=True)
    assert frame(out, 3.0) != frame(bare, 3.0)        # the overlay is drawn


# ── 3. PICTURE CHECK: a graphic's measured ink on the eyes or mouth ────────

def _grid(y0, y1, x0=.2, x1=.8):
    import numpy as np
    import render_qc
    cols, rows = render_qc.INK_GRID
    g = np.zeros((rows, cols), dtype=bool)
    g[int(y0 * rows):int(y1 * rows), int(x0 * cols):int(x1 * cols)] = True
    return g


def _tplan(items):
    return {"items": [dict({"persistent": False, "layer": "above_captions",
                            "template": "word_slam", "start": 2.0,
                            "end": 5.0}, **it) for it in items]}


# a talking head in a 9:16 crop: 4634 v27's face, x .36-.62, y .20-.46
FACE = (0.36, 0.20, 0.62, 0.46)


def _track(a=2.0, b=5.0, face=FACE):
    return [(round(a + .1 * k, 2), [face]) for k in range(int((b - a) * 10))]


def test_ink_on_the_mouth_or_eyes_is_a_finding_even_with_allow_face_overlap():
    import render_qc
    tplan = _tplan([
        {"id": "pay", "item": {"allow_face_overlap": True}},    # on the mouth
        {"id": "c50", "item": {}},                              # on the eyes
        {"id": "below", "item": {}},                            # under the chin
    ])
    tres = {"items": {"pay": {"grid": _grid(.36, .50)},
                      "c50": {"grid": _grid(.22, .31)},
                      "below": {"grid": _grid(.49, .62)}}}
    tracks = {k: _track() for k in ("pay", "c50", "below")}
    got = {r[0]: r for r in render_qc.graphic_faces(tracks, tplan, tres)}
    assert set(got) == {"pay", "c50"}, got
    assert got["pay"][3] == "mouth" and got["pay"][5] is True
    assert got["c50"][3] == "eyes" and got["c50"][5] is False
    line = render_qc.measured_findings({"type": {"face": list(got.values())}})
    assert any(x.startswith("GRAPHIC ON THE FACE") and "'pay'" in x
               and "allow_face_overlap" in x for x in line)


def test_type_behind_him_a_band_a_picture_and_no_face_are_not_judged():
    import render_qc
    tplan = _tplan([
        {"id": "behind", "layer": "behind_subject", "item": {}},
        {"id": "band", "persistent": True, "item": {}},
        {"id": "photo", "template": "still_motion", "item": {}},
        {"id": "broll", "item": {}},
    ])
    on_mouth = {"grid": _grid(.36, .50)}
    tres = {"items": {k: on_mouth for k in ("behind", "band", "photo",
                                            "broll")}}
    tracks = {"behind": _track(), "band": _track(), "photo": _track(),
              "broll": [(t, []) for t, _f in _track()]}    # under b-roll
    assert render_qc.graphic_faces(tracks, tplan, tres) == []
    # an audience member's face in a wide shot is not the speaker
    tiny = (0.45, 0.40, 0.49, 0.43)
    assert render_qc.graphic_faces(
        {"broll": _track(face=tiny)}, tplan, tres) == []


def test_ink_lines_returns_the_ink_grid():
    import io
    import numpy as np
    from PIL import Image
    import render_qc
    a = np.zeros((1920, 1080), dtype=np.uint8)
    a[700:900, 200:880] = 255
    im = Image.fromarray(np.dstack([a, a, a, a]), "RGBA")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    m = render_qc.ink_lines(buf.getvalue())
    g = np.asarray(m["grid"])
    assert g.shape == (render_qc.INK_GRID[1], render_qc.INK_GRID[0])
    share = render_qc._grid_share(g, (200 / 1080, 700 / 1920,
                                      880 / 1080, 900 / 1920))
    assert share > .9
    assert render_qc._grid_share(g, (0, 0, 1, .3)) == 0.0


# ── 4. set_picture_card: a new source keeps the card's box ────────────────

def test_a_new_source_rect_keeps_the_cards_box_and_says_so():
    import agent_tools
    from test_kamath_run_fixes import _CardCtx
    ctx = _CardCtx()
    first = agent_tools.set_picture_card(
        ctx, "c3", 1.0, 8.0, box=[0, 0.14, 1, 0.7], fit="crop",
        source=[0.2175, 0.0, 0.7825, 1.0], entrance="none", follow=False,
        background_style="blur", background_dim=0.6)
    assert first.startswith("EDL v"), first
    box = ctx.card("c3")["box"]
    res = agent_tools.set_picture_card(ctx, "c3", 1.0, 8.0, fit="crop",
                                       source=[0, 0, 0.62, 1])
    assert res.startswith("EDL v"), res
    card = ctx.card("c3")
    assert card["box"] == box, card["box"]
    assert card["source"] != [0.2175, 0.0, 0.7825, 1.0]
    assert "KEPT from the current card" in res and "box" in res.split(
        "KEPT from the current card")[1]
    # naming the box still moves it
    agent_tools.set_picture_card(ctx, "c3", 1.0, 8.0, box=[0, .2, 1, .7],
                                 source=[0, 0, 0.62, 1])
    assert ctx.card("c3")["box"][1] == .2


def test_one_false_box_under_a_graphic_is_not_the_speaker():
    # 4656 v35 'POOF.': one source moment saw a second, larger 'face' on the
    # speaker's knee, under the slam; the speaker's own face was clear
    import render_qc
    tplan = _tplan([{"id": "poof", "item": {"allow_face_overlap": True}}])
    tres = {"items": {"poof": {"grid": _grid(.52, .70)}}}
    knee = (0.20, 0.53, 0.45, 0.68)
    tracks = {"poof": [(2.5, [knee, FACE]), (3.5, [FACE]), (4.5, [FACE])]}
    assert render_qc.graphic_faces(tracks, tplan, tres) == []
    # the same face covered in two of three moments is a hit
    tres = {"items": {"poof": {"grid": _grid(.36, .50)}}}
    tracks = {"poof": [(2.5, [FACE]), (3.5, []), (4.5, [FACE])]}
    assert [r[0] for r in render_qc.graphic_faces(tracks, tplan, tres)] == \
        ["poof"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_source_frames_reads_close_moments_in_one_decode(tmp_path,
                                                         monkeypatch):
    import render_qc
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=25:d=20", "-c:v", "libx264",
                    "-preset", "ultrafast", str(src)], check=True, timeout=60)
    calls = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real(cmd, *a, **k)
    import time as _time
    monkeypatch.setattr(render_qc.subprocess, "run", spy)
    got = render_qc.source_frames(str(src), [3.0, 4.0, 5.0, 15.0],
                                  str(tmp_path), _time.monotonic() + 60)
    assert sorted(got) == [3.0, 4.0, 5.0, 15.0]
    assert len(calls) == 2                       # one read per close run
    assert all(os.path.exists(p) for p in got.values())


# ── 5. STALL BEFORE END CARD: the source's own brief repeats ──────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("frames,flagged", [(4, False), (12, True)])
def test_a_brief_repeat_in_live_footage_is_not_a_stall(tmp_path, frames,
                                                       flagged):
    import render_qc
    path = str(tmp_path / f"tail{frames}.mp4")
    # 9 s of moving picture at 30 fps; frames 240.. repeat (8.0 s on)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=192x108:r=30:d=9",
                    "-filter_complex",
                    "[0:v]split[a][b];[a][b]freezeframes=first=241:"
                    f"last={240 + frames - 1}:replace=240",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                    path], check=True, timeout=60)
    p = {"program_s": 9.0, "fps": 30.0, "W": 192, "H": 108,
         "endcard": None, "tail": {}}
    rows = render_qc.boundary_stall(path, p, 1e12)
    held = [r for r in rows if r[0] == "held"]
    assert bool(held) is flagged, rows
    if flagged:
        assert held[0][3] == "footage"
    # the edit's own freeze still counts however brief
    p["tail"] = {"stills": [[8.0, 8.2, "ff", True]]}
    rows = render_qc.boundary_stall(path, p, 1e12)
    assert [r[3] for r in rows if r[0] == "held"] == ["hold"]


# ── 6. set_caption_fixes says what a punctuation-blind match did ──────────

def test_caption_fixes_name_merged_spellings_and_changed_punctuation():
    import agent_tools
    from w0c_support import tool_context
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 20.0]]
    edl["captions"] = {"mode": "from_transcript"}
    ctx, fake = tool_context(edl, duration=20.0)
    words = "one of them is CUDNN, and CUDNN is fast".split()
    ctx.index = dict(ctx.index, words=[
        {"w": w, "t0": 1.0 + .5 * i, "t1": 1.4 + .5 * i, "i": i, "p": .99}
        for i, w in enumerate(words)])
    out = agent_tools.set_caption_fixes(
        ctx, [["CUDNN", "cuDNN"], ["CUDNN,", "cuDNN,"]], operation="append")
    assert out.startswith("EDL v"), out
    assert "SAME WORDS" in out and "'CUDNN,' -> 'cuDNN,' applies" in out
    # the comma rule also writes a comma onto the bare 'CUDNN' at 4.00 s
    assert "PUNCTUATION" in out and "'CUDNN' at 4.00s now reads 'cuDNN,'" \
        in out
    # a scoped rule per spelling says nothing
    ctx2, _f = tool_context(edl, duration=20.0)
    ctx2.index = ctx.index
    out = agent_tools.set_caption_fixes(ctx2, [
        {"from": "CUDNN,", "to": "cuDNN,", "start": 2.9, "end": 3.5},
        {"from": "CUDNN", "to": "cuDNN", "start": 3.9, "end": 4.5}])
    assert out.startswith("EDL v") and "SAME WORDS" not in out \
        and "PUNCTUATION" not in out, out
