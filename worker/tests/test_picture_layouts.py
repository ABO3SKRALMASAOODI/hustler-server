"""Source-fed picture cards, dark sampled canvases and stacked
speaker-plus-evidence layouts (judges, Oct 2026).

* A card takes its footage from the full SOURCE frame (never the already
  cropped 9:16 program), enlarged at most 2x, with face headroom; a
  low-resolution source is shown whole.
* The default canvas is dark and sampled from the footage (grain on
  sub-720p sources), never a blurred self-copy.
* ``panels`` shows two or three regions of the same source frame at once.
* The renderer composes the card's blocks from the source and the card is
  built from exactly the frames composed for it (layout tags), so its edges
  land on the very frame the blocks change — at a cut or mid-segment.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_tools
import picture_cards
import renderer
from schemas import (EDLValidationError, default_edl, edl_signature,
                     validate_edl)
from timeline import Timeline

SRC = 60.0
CARD = {"id": "c", "start": 1.0, "end": 3.0, "box": [.05, .3, .95, .55],
        "fit": "pad", "background": "#406080", "entrance": "none",
        "exit": "none", "shadow": 0, "border": 0}


def _edl(cards, keep=((0.5, 2.37), (3.13, 6.0), (6.71, 8.0)), **fx):
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in keep]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"picture_cards": cards, **fx}
    return validate_edl(e, SRC).model_dump()


def _graph(e, tmp_path, W=320, H=568, src=(320, 180), path="src.mp4"):
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    args = ["-i", str(path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    inputs, _ = picture_cards.prepare_inputs(e, str(tmp_path), W, H, 30,
                                             args, 2)
    graph = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=W, H=H, fps=30,
        frame_mode="crop", src_w=src[0], src_h=src[1], silence_idx=1,
        picture_card_inputs=inputs)
    return graph, args, tl


# ── schema ────────────────────────────────────────────────────────────────

def test_a_program_card_keeps_its_signature_and_shape():
    e = _edl([dict(CARD)])
    card = e["effects"]["picture_cards"][0]
    assert card["source"] is None and card["panels"] is None
    legacy = dict(e)
    legacy["effects"] = dict(e["effects"], picture_cards=[
        {k: v for k, v in card.items() if k not in ("source", "panels")}])
    assert edl_signature(e) == edl_signature(legacy)


def test_source_rect_and_panels_are_validated_and_canonical():
    with pytest.raises((ValueError, EDLValidationError)):
        _edl([dict(CARD, source=[.5, 0, .4, 1])])
    with pytest.raises((ValueError, EDLValidationError)):
        _edl([dict(CARD, panels=[{"box": [.05, .1, .95, .4],
                                  "source": [0, 0, .5, 1]}])])
    with pytest.raises((ValueError, EDLValidationError), match="overlap"):
        _edl([dict(CARD, panels=[
            {"box": [.05, .1, .95, .5], "source": [0, 0, .5, 1]},
            {"box": [.05, .4, .95, .8], "source": [.5, 0, 1, 1]}])])
    e = _edl([dict(CARD, source=[.1, .1, .9, .9], panels=[
        {"box": [.05, .1, .95, .45], "source": [0, 0, .5, 1]},
        {"box": [.1, .5, .9, .9], "source": [.5, 0, 1, 1]}])])
    card = e["effects"]["picture_cards"][0]
    # one canonical shape: the panels carry the rects, box is their bounds
    assert card["source"] is None
    assert card["box"] == [.05, .1, .95, .9]


def test_a_source_card_needs_main_video():
    e = default_edl(SRC)
    e["keep"] = []
    e["canvas"] = {"width": 1080, "height": 1920, "fps": 30}
    e["inserts"] = [{"id": "i", "asset_key": "a.png", "kind": "image",
                     "at_output_s": 0, "duration_s": 3}]
    e["effects"] = {"picture_cards": [dict(CARD, source=[0, 0, 1, 1])]}
    with pytest.raises(EDLValidationError, match="main video"):
        validate_edl(e, SRC)


# ── geometry ──────────────────────────────────────────────────────────────

def test_contained_archival_card_hugs_the_footage_under_the_cap():
    box, rect, k = picture_cards.fit_panel(
        646, 480, 1080, 1920, [.04, .265, .96, .685],
        picture_cards.archival_rect(), "pad")
    rw, rh = (rect[2] - rect[0]) * 646, (rect[3] - rect[1]) * 480
    bw, bh = (box[2] - box[0]) * 1080, (box[3] - box[1]) * 1920
    assert abs(bw / bh - rw / rh) < .01           # the window hugs 4:3
    assert k == pytest.approx(1.6, abs=.02)       # not 3.7x
    assert box[0] == pytest.approx(.04) and box[2] == pytest.approx(.96)
    # centred in the requested band
    assert (box[1] + box[3]) / 2 == pytest.approx((.265 + .685) / 2, abs=1e-3)


def test_enlargement_is_capped_by_growing_the_rect_then_shrinking_the_box():
    # a tight rect would enlarge 6x: it grows around its centre to 2x
    box, rect, k = picture_cards.fit_panel(
        1920, 1080, 1080, 1920, [.04, .2, .96, .6], [.45, .4, .53, .48],
        "crop")
    assert k == pytest.approx(2.0, abs=1e-6)
    assert box == [.04, .2, .96, .6]
    assert rect[0] < .45 and rect[2] > .53
    # a 320x240 source cannot reach full width under 2x: the box shrinks
    box, rect, k = picture_cards.fit_panel(
        320, 240, 1080, 1920, [.04, .2, .96, .6], [0, 0, 1, 1], "pad")
    assert k == pytest.approx(2.0)
    assert (box[2] - box[0]) * 1080 == pytest.approx(640, abs=2)


def test_speaker_rect_keeps_headroom_and_the_face():
    face = [.42, .25, .58, .55]                   # brow-to-chin box
    box = [.04, .1, .96, .5]
    rect, headroom = picture_cards.speaker_rect(1920, 1080, 1080, 1920, box,
                                                face)
    crown = face[1] - picture_cards.HAIR_ABOVE_FACE * (face[3] - face[1])
    assert rect[1] < crown and headroom >= picture_cards.HEADROOM_MIN
    assert rect[0] <= face[0] and rect[2] >= face[2] and rect[3] >= face[3]
    # aspect matches the box, enlargement within the cap
    rw, rh = (rect[2] - rect[0]) * 1920, (rect[3] - rect[1]) * 1080
    assert rw / rh == pytest.approx(994 / 768, rel=.01)
    assert 994 / rw <= picture_cards.SOURCE_UPSCALE_CAP + 1e-6
    # a face against the top edge: the rect stops at the frame, and says so
    rect, headroom = picture_cards.speaker_rect(
        1920, 1080, 1080, 1920, box, [.42, .02, .58, .3])
    assert rect[1] == 0 and headroom < picture_cards.HEADROOM_MIN


def test_median_face_and_dark_sampled_canvas():
    assert picture_cards.median_face([[0, 0, .1, .1], [.2, .2, .4, .4],
                                      [.3, .3, .5, .5]]) == [.2, .2, .4, .4]
    assert picture_cards.median_face([]) is None
    c1, c2 = picture_cards.canvas_colours((66, 52, 49))   # the Jobs master
    r, g, b = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
    assert max(r, g, b) == round(.14 * 255) and r > g > b   # warm, dark
    assert max(int(c2[i:i + 2], 16) for i in (1, 3, 5)) == round(.04 * 255)
    assert picture_cards.canvas_colours(None) == picture_cards.CANVAS_FALLBACK


# ── the render graph ──────────────────────────────────────────────────────

def test_legacy_cards_render_through_the_unchanged_graph(tmp_path):
    graph, _args, _tl = _graph(_edl([dict(CARD)]), tmp_path)
    assert "metadata=" not in graph and "valmera_card" not in graph
    assert graph.count("concat=n=3:v=1:a=1") == 1


def test_source_card_splits_its_block_and_tags_only_its_frames(tmp_path):
    # 1.0-3.0 program = mid first segment .. mid second segment
    graph, _args, _tl = _graph(_edl([dict(CARD, source=[0, 0, 1, 1])]),
                               tmp_path)
    # both kept segments split at the card edges: 5 render blocks
    assert "concat=n=5:v=1:a=1" in graph
    assert graph.count("metadata=mode=add:key=valmera_card:value=c0r0") == 2
    assert "metadata=mode=select:key=valmera_card:value=c0r0" in graph
    # the card's own box is cut back out of the composed program
    x, y, w, h = picture_cards.pixels(320, 568, [.05, .3, .95, .55])
    assert f"crop={w}:{h}:{x}:{y}" in graph


def test_a_card_edge_on_a_focus_handoff_takes_the_new_shots_first_frame(
        tmp_path):
    """The Elon showcase: the stack starts on the 138.04 camera cut, where
    the focus track re-aims half a frame BEFORE the cut frame. The card owns
    that first new-shot frame; it is not left in a one-frame block of the
    old framing."""
    e = default_edl(SRC)
    e["keep"] = [[0.5, 4.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 0.0, "t1": 2.0, "x": .3}, {"t0": 2.0, "t1": 10.0, "x": .7}]}
    e["effects"] = {"picture_cards": [dict(CARD, start=1.5, end=3.0,
                                           source=[0, 0, 1, 1])]}
    e = validate_edl(e, SRC).model_dump()
    tl = Timeline(e["keep"], [], [])
    graph = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=320, H=568, fps=30,
        frame_mode="crop", src_w=320, src_h=180, silence_idx=1,
        src_fps=30.0, focus_origin=0.0,
        picture_card_inputs=[(2, e["effects"]["picture_cards"][0])])
    # blocks: before the handoff | the card (from the handoff) | after it
    assert "concat=n=3:v=1:a=1" in graph
    assert graph.count("metadata=mode=add") == 1
    assert "trim=start=1.983:end=3.500" in graph


def test_split_blocks_get_no_extra_transition(tmp_path):
    fx = {"transition": {"style": "dip_black", "duration_s": .3,
                         "scope": "every_cut"}}
    plain, _a, _t = _graph(_edl([dict(CARD)], **fx), tmp_path)
    split, _a, _t = _graph(_edl([dict(CARD, source=[0, 0, 1, 1])], **fx),
                           tmp_path)
    # two real cuts either way: the card's mid-segment edges are not cuts
    assert plain.count("fade=t=in:st=0") == split.count("fade=t=in:st=0") == 2


def test_inserts_inside_a_source_card_end_its_run():
    tl = Timeline([[0.5, 2.37], [3.13, 6.0]],
                  [{"id": "b", "asset_key": "k", "kind": "image",
                    "at_output_s": 1.87, "duration_s": 1.0}], [])
    assert tl.out_duration == pytest.approx(5.74)
    e = default_edl(SRC)
    e["keep"] = [[0.5, 2.37], [3.13, 6.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["inserts"] = [{"id": "b", "asset_key": "k", "kind": "image",
                     "at_output_s": 1.87, "duration_s": 1.0}]
    e["effects"] = {"picture_cards": [dict(CARD, start=1.0, end=4.5,
                                           source=[0, 0, 1, 1])]}
    e = validate_edl(e, SRC).model_dump()
    graph = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=320, H=568, fps=30,
        frame_mode="crop", src_w=320, src_h=180, silence_idx=1,
        insert_inputs=[(3, e["inserts"][0], False)],
        picture_card_inputs=[(2, e["effects"]["picture_cards"][0])])
    # two runs, one each side of the insert, each its own card branch
    assert "value=c0r0" in graph and "value=c0r1" in graph
    assert graph.count("metadata=mode=select") == 2


def test_a_stack_silences_zooms_inside_it(tmp_path):
    zooms = [{"id": "z", "start": 1.2, "end": 2.0, "strength": .2,
              "mode": "punch"}]
    stack = dict(CARD, panels=[
        {"box": [.05, .05, .95, .4], "source": [0, 0, .5, 1]},
        {"box": [.05, .45, .95, .8], "source": [.5, 0, 1, 1]}])
    single, _a, _t = _graph(_edl([dict(CARD, source=[0, 0, 1, 1])],
                                 zooms=zooms), tmp_path)
    stacked, _a, _t = _graph(_edl([stack], zooms=zooms), tmp_path)
    assert "perspective=" in single          # a zoom plays inside one panel
    assert "perspective=" not in stacked     # never across two


def test_behind_subject_depth_yields_to_a_source_card():
    e = _edl([dict(CARD, source=[0, 0, 1, 1])])
    assert picture_cards.overlaps_source_card(e, [(2.5, 4.0)])
    assert not picture_cards.overlaps_source_card(e, [(3.2, 4.0)])
    assert not picture_cards.overlaps_source_card(_edl([dict(CARD)]),
                                                  [(1.0, 2.0)])


# ── pixels ────────────────────────────────────────────────────────────────

def _ramp_source(tmp_path):
    """Luma = 20 + frame number; left third red-tinted, right third blue."""
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=320x180:r=30:d=10,format=yuv444p,"
         "geq=lum='20+mod(N,200)':cb='if(gt(X,213),200,128)'"
         ":cr='if(lt(X,107),200,128)'",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", "-g", "1",
         str(src)], check=True)
    return src


def _render(tmp_path, e):
    W, H = 320, 568
    tmp_path.mkdir(exist_ok=True)
    graph, args, tl = _graph(e, tmp_path, path=_ramp_source(tmp_path))
    out = tmp_path / "out.mp4"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", graph,
         "-map", "[vout]", "-map", "[aout]", "-t", str(tl.out_duration),
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", "-c:a", "aac",
         str(out)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(out),
                                   "-f", "rawvideo", "-pix_fmt", "yuv444p",
                                   "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, 3, H, W).astype(int)


def _is_card(f):
    """The card's backdrop (#406080) above its box; else the program."""
    y, u, v = f[0][30, 160], f[1][30, 160], f[2][30, 160]
    return abs(u - 150) < 20 and abs(v - 110) < 20 and abs(y - 90) < 25


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_card_switches_on_the_exact_frame_its_blocks_change(tmp_path):
    """Starts on a cut (1.87), ends mid-segment (5.3): every frame is the
    program's own crop or the complete card, the card's first frame is the
    first frame after the cut, and the footage inside it is the SOURCE's
    full width (its red left third)."""
    e = _edl([dict(CARD, start=1.87, end=5.3, source=[0, 0, 1, 1])])
    frames = _render(tmp_path, e)
    kinds = [_is_card(f) for f in frames]
    luma = [int(f[0][284, 160]) - 20 for f in frames]
    on = [i for i, k in enumerate(kinds) if k]
    assert on == list(range(on[0], on[-1] + 1))       # one contiguous run
    first, last = on[0], on[-1]
    # the switch-on frame is the cut: the source jumps (3.13 - 2.37 s)
    assert (luma[first] - luma[first - 1]) % 200 > 10
    # mid-segment switch-off: the source simply continues
    assert (luma[last + 1] - luma[last]) % 200 <= 2
    for i in on:
        y = int(.42 * 568)
        assert frames[i][2][y, 40] > 150             # source-left red
    for i, k in enumerate(kinds):
        if not k:                                     # the 9:16 crop: middle
            assert abs(frames[i][2][284, 160] - 128) < 12


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_stacked_panels_show_two_regions_of_one_frame(tmp_path):
    stack = dict(CARD, start=1.87, end=4.74, panels=[
        {"box": [.05, .3, .95, .55], "source": [0, 0, .32, 1]},
        {"box": [.05, .6, .95, .9], "source": [.68, 0, 1, 1]}])
    frames = _render(tmp_path, _edl([stack]))
    on = [f for f in frames if _is_card(f)]
    assert len(on) > 60
    for f in on[::10]:
        assert f[2][int(.42 * 568), 160] > 150        # panel 1: red third
        assert f[1][int(.75 * 568), 160] > 150        # panel 2: blue third


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_a_proof_fragment_renders_the_same_card(tmp_path):
    """Stitched previews render windows of the program: a fragment that
    starts inside a source card shows the same card pixels as the full
    render (the card's clock is carried by phase_s)."""
    import stitch
    e = _edl([dict(CARD, start=1.0, end=4.0, source=[0, 0, 1, 1],
                   entrance="lift", exit="fade", duration_s=.6)])
    full = _render(tmp_path / "a", e)
    tl = Timeline(e["keep"], [], [])
    part = stitch.window_edl(e, tl, 1.3, 3.3)
    card = part["effects"]["picture_cards"][0]
    assert card["source"] == [0, 0, 1, 1] and card["phase_s"] > 0
    frag = _render(tmp_path / "b", validate_edl(part, SRC).model_dump())
    for t in (.5, 1.2):
        a, b = full[round((1.3 + t) * 30)], frag[round(t * 30)]
        assert _is_card(a) and _is_card(b)
        assert np.abs(a - b).mean() < 4


# ── the tool ──────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, width, height, samples=None, edl=None):
        self.has_main_video = True
        self.workdir = None
        self.duration = SRC
        self.index = {"video": {"width": width, "height": height,
                                "fps": 30, "duration": SRC}}
        if samples is not None:
            self.index["spatial"] = {"v": 1, "samples": samples}
        e = edl or default_edl(SRC)
        if edl is None:
            e["keep"] = [[10.0, 20.0], [25.0, 35.0]]
            e["frame"] = {"ratio": "9:16", "mode": "crop"}
        self._edl = validate_edl(e, SRC).model_dump()
        self.written = []

    def latest_edl(self):
        return {"json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), SRC).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written) + 1}: {desc}"

    def proxy_path(self):
        raise RuntimeError("no proxy in this test")

    def card(self):
        return self._edl["effects"]["picture_cards"][0]


def test_low_resolution_default_is_the_whole_frame_on_a_dark_grained_canvas():
    ctx = _Ctx(646, 480)
    res = agent_tools.set_picture_card(ctx, "c", 0, 20,
                                       box=[.04, .265, .96, .685])
    card = ctx.card()
    assert card["source"] == picture_cards.archival_rect()
    assert card["fit"] == "pad"
    assert card["background_style"] == "radial_gradient"
    assert card["grain"] == .25 and card["vignette"] == .35
    assert (card["background"], card["background_color2"]) == \
        picture_cards.CANVAS_FALLBACK
    assert "enlarged 1.60x" in res and "contain" in res and "CANVAS:" in res


def test_hd_speaker_card_is_framed_from_measured_faces():
    samples = [{"t": t, "faces": [[.40, .25, .55, .52]]}
               for t in (11.0, 14.0, 18.0)]
    samples.append({"t": 30.0, "faces": [[.9, .9, .95, .95]]})  # not shown
    ctx = _Ctx(1920, 1080, samples=samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       box=[.04, .1, .96, .5])
    card = ctx.card()
    assert card["fit"] == "crop" and card["grain"] is None
    x0, y0, x1, y1 = card["source"]
    assert x0 < .40 and x1 > .55 and y0 < .25
    assert "headroom above the head" in res
    head = int(res.split("% headroom")[0].rsplit(" ", 1)[-1])
    assert head >= 8


def test_blur_on_low_resolution_footage_is_allowed_with_a_warning():
    ctx = _Ctx(646, 480)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       background_style="blur")
    assert ctx.card()["background_style"] == "blur"
    assert "muddy smear" in res


def test_program_source_keeps_the_legacy_card():
    ctx = _Ctx(1920, 1080)
    agent_tools.set_picture_card(ctx, "c", 0, 9, source="program",
                                 background="#101012")
    card = ctx.card()
    assert card["source"] is None and card["panels"] is None
    assert card["background_style"] is None


def test_stacked_panels_through_the_tool():
    e = default_edl(SRC)
    e["keep"] = [[10.0, 20.0], [25.0, 35.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"zooms": [{"id": "z1", "start": 2.0, "end": 3.0,
                               "strength": .1, "mode": "punch"}]}
    ctx = _Ctx(1920, 1080, edl=e)
    res = agent_tools.set_picture_card(ctx, "ev", 0, 9, panels=[
        {"box": [.08, .04, .92, .38], "source": [.2, .17, .53, .6],
         "fit": "crop"},
        {"box": [.08, .4, .92, .57], "source": [.545, .655, .84, .835]}])
    card = ctx.card()
    assert len(card["panels"]) == 2
    evidence = card["panels"][1]
    # the evidence panel hugs its 2.9:1 rect (pad by default)
    bw = (evidence["box"][2] - evidence["box"][0]) * 1080
    bh = (evidence["box"][3] - evidence["box"][1]) * 1920
    assert bw / bh == pytest.approx(566.4 / 194.4, rel=.02)
    assert "zoom(s) z1" in res and "do NOT play" in res
    assert agent_tools.set_picture_card(
        ctx, "ev", 0, 9, panels=[{"box": [.1, .1, .9, .4],
                                  "source": "auto"}]).startswith("REJECTED")
    assert agent_tools.set_picture_card(
        ctx, "ev", 0, 9, source="full", panels=[
            {"box": [.1, .1, .9, .4], "source": "auto"},
            {"box": [.1, .5, .9, .9], "source": "full"}]).startswith(
                "REJECTED")


def test_source_cards_need_footage_in_their_window():
    e = default_edl(SRC)
    e["keep"] = [[10.0, 20.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["inserts"] = [{"id": "b", "asset_key": "k", "kind": "image",
                     "at_output_s": 10.0, "duration_s": 5.0}]
    ctx = _Ctx(1920, 1080, edl=e)
    res = agent_tools.set_picture_card(ctx, "c", 10.5, 14.5)
    assert res.startswith("REJECTED") and "no main footage" in res
    # the same window as a program card is fine
    assert agent_tools.set_picture_card(
        ctx, "c", 10.5, 14.5, source="program").startswith("EDL v")


# ── review fixes (Oct 2026) ───────────────────────────────────────────────

def test_a_late_card_branch_waits_only_for_its_own_run(tmp_path):
    """MEMORY: overlay holds every program frame until it knows the card
    branch's next timestamp. The branch's lead frames sit just before its
    run's trim window — not at zero, where every frame before a card at 30 s
    of a 1080x1920 final queued in RAM (5.1 GB peak against 0.6 GB)."""
    e = _edl([dict(CARD, start=30.0, end=35.0, source=[0, 0, 1, 1])],
             keep=((0.0, 40.0),))
    graph, _a, _t = _graph(e, tmp_path)
    reach = picture_cards.SOURCE_CARD_REACH_S
    assert f"trim=start={30 - reach:.6f}:end={35 + reach:.6f}" in graph
    assert f"if(lt(N,2),({30 - reach:.6f}-(2-N)/30.000000)/TB" in graph


def test_runs_carry_their_program_spans(tmp_path):
    e = default_edl(SRC)
    e["keep"] = [[0.5, 2.37], [3.13, 6.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["inserts"] = [{"id": "b", "asset_key": "k", "kind": "image",
                     "at_output_s": 1.87, "duration_s": 1.0}]
    e["effects"] = {"picture_cards": [dict(CARD, start=1.0, end=4.5,
                                           source=[0, 0, 1, 1])]}
    e = validate_edl(e, SRC).model_dump()
    tl = Timeline(e["keep"], e["inserts"], [])
    graph = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=320, H=568, fps=30,
        frame_mode="crop", src_w=320, src_h=180, silence_idx=1,
        insert_inputs=[(3, e["inserts"][0], False)],
        picture_card_inputs=[(2, e["effects"]["picture_cards"][0])])
    reach = picture_cards.SOURCE_CARD_REACH_S
    # run 0: 1.0 -> the insert at 1.87; run 1: after it (2.87) -> 4.5
    assert f"trim=start={1.0 - reach:.6f}:end={1.87 + reach:.6f}" in graph
    assert f"trim=start={2.87 - reach:.6f}:end={4.5 + reach:.6f}" in graph


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("look", [
    {},
    {"background_style": "radial_gradient", "background_color2": "#050505",
     "grain": .25},
])
def test_an_insert_inside_a_source_card_plays_full_frame(tmp_path, look):
    """The card steps aside for the insert on its exact frames and returns
    after it — a grained (looping) plate included, which used to hold the
    run's last frame over the insert's first third of a second and past
    the card's end."""
    src = tmp_path / "g.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=green:s=320x180:r=30:d=12", "-c:v", "libx264",
                    "-qp", "0", str(src)], check=True)
    img = tmp_path / "red.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=320x568:d=1", "-frames:v", "1", str(img)],
                   check=True)
    e = default_edl(SRC)
    e["keep"] = [[0.0, 5.0], [5.0, 10.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["inserts"] = [{"id": "b", "asset_key": "k", "kind": "image",
                     "at_output_s": 5.0, "duration_s": 2.0}]
    e["effects"] = {"picture_cards": [dict(
        CARD, start=3.0, end=9.0, box=[.05, .3, .95, .7],
        background="#203040", source=[0, 0, 1, 1], **look)]}
    e = validate_edl(e, SRC).model_dump()
    tl = Timeline(e["keep"], e["inserts"], [])
    args = ["-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-loop", "1", "-framerate", "30", "-t", "2.5", "-i", str(img)]
    inputs, _ = picture_cards.prepare_inputs(e, str(tmp_path), 320, 568, 30,
                                             args, 3)
    graph = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=320, H=568, fps=30,
        frame_mode="crop", src_w=320, src_h=180, silence_idx=1,
        insert_inputs=[(2, e["inserts"][0], False)],
        picture_card_inputs=inputs)
    out = tmp_path / "o.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args,
                        "-filter_complex", graph, "-map", "[vout]",
                        "-map", "[aout]", "-t", str(tl.out_duration),
                        "-c:v", "libx264", "-qp", "0", "-c:a", "aac",
                        str(out)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(out),
                                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 568, 320, 3).astype(int)

    def kind(f):
        if f[284, 160, 0] > 200 and f[20, 160, 0] > 200:
            return "I"                          # the insert, full-frame
        return "P" if f[20, 160, 1] > 110 else "C"
    kinds = "".join(kind(f) for f in frames)
    assert kinds == "P" * 90 + "C" * 60 + "I" * 60 + "C" * 60 + "P" * 90


class _ShotCtx(_Ctx):
    """Two cameras: a close-up of one speaker, then a wide of the other."""

    def __init__(self, faces_b, track=None, **kw):
        e = default_edl(SRC)
        e["keep"] = [[10.0, 20.0]]
        e["frame"] = {"ratio": "9:16", "mode": "crop"}
        if track:
            e["frame"]["focus_track"] = track
        samples = [{"t": t, "faces": [[.40, .20, .62, .62]]}
                   for t in (11.0, 12.5, 14.0)]
        samples += [{"t": t, "faces": [f]} for t, f in faces_b]
        super().__init__(1920, 1080, samples=samples, edl=e, **kw)
        self.index["shots"] = [{"id": 0, "start": 0.0, "end": 15.0},
                               {"id": 1, "start": 15.0, "end": 60.0}]


def test_a_card_across_a_camera_cut_keeps_the_program_picture():
    """One source rect frames ONE shot: across a cut to another camera the
    speaker crop would show a wall. The program follows the reframe shot by
    shot, so an 'auto' card there stays a program card and says why."""
    ctx = _ShotCtx([(16.0, [.10, .30, .18, .45]), (18.0, [.11, .31, .19, .46])])
    res = agent_tools.set_picture_card(ctx, "c", 2.0, 8.0)
    card = ctx.card()
    assert card["source"] is None and card["panels"] is None
    assert "crosses a camera cut (source 15s)" in res and "split" in res
    # one shot per card: framed from the source
    agent_tools.set_picture_card(ctx, "c", 1.0, 4.5)
    assert ctx.card()["source"] is not None


def test_a_cut_between_alike_framings_keeps_the_source_card():
    ctx = _ShotCtx([(16.0, [.41, .21, .62, .63]), (18.0, [.40, .20, .61, .62])])
    res = agent_tools.set_picture_card(ctx, "c", 2.0, 8.0)
    assert ctx.card()["source"] is not None
    assert "camera cut" not in res


def test_an_explicit_rect_across_a_cut_is_kept_with_a_note():
    ctx = _ShotCtx([(16.0, [.10, .30, .18, .45])])
    res = agent_tools.set_picture_card(ctx, "c", 2.0, 8.0,
                                       source=[.2, .1, .8, .9])
    assert ctx.card()["source"] is not None
    assert "same source region in every shot" in res


def test_no_face_aims_at_the_reframes_own_focus_span():
    track = [{"t0": 0.0, "t1": 30.0, "x": .8, "y": .5}]
    e = default_edl(SRC)
    e["keep"] = [[10.0, 20.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": track}
    ctx = _Ctx(1920, 1080, samples=[], edl=e)
    res = agent_tools.set_picture_card(ctx, "c", 1.0, 5.0,
                                       box=[.1, .2, .5, .5])
    x0, _y0, x1, _y1 = ctx.card()["source"]
    assert (x0 + x1) / 2 > .6 and "no face measured" in res


def test_malformed_card_rects_are_rejected_not_raised():
    ctx = _Ctx(1920, 1080)
    for kw in ({"box": [.9, .1, .1, .9]}, {"box": [.1, .1, .15, .9]},
               {"source": [.5, 0, .5, 1]}, {"source": ["a", 0, 1, 1]},
               {"panels": [{"box": [.1, .1, .9, .4], "source": [.3, .3, .3, .3]},
                           {"box": [.1, .5, .9, .9], "source": "full"}]}):
        res = agent_tools.set_picture_card(ctx, "c", 1.0, 5.0, **kw)
        assert res.startswith("REJECTED"), (kw, res)


def test_overlays_inside_a_source_card_are_flagged():
    e = default_edl(SRC)
    e["keep"] = [[10.0, 20.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    ctx = _Ctx(1920, 1080, edl=e)
    ctx._edl["overlays"] = [{"id": "logo", "asset_key": "media/p/logo.png",
                             "kind": "image", "start": 2.0, "duration_s": 1.0}]
    res = agent_tools.set_picture_card(ctx, "c", 1.0, 5.0, source="full")
    assert "overlay(s) logo" in res and "canvas covers the rest" in res


def test_a_zoom_across_a_stack_still_plays_outside_it():
    zooms = [{"id": "z", "start": 0.2, "end": 4.5, "strength": .2,
              "mode": "punch", "ramp_s": .2},
             {"id": "y", "start": 5.0, "end": 6.0, "strength": .1,
              "mode": "punch"}]
    pieces = renderer._zooms_outside(zooms, [(1.0, 3.0)])
    spans = [(p["id"], p["start"], p["end"]) for p in pieces]
    assert spans == [("z", 0.2, 1.0), ("z", 3.0, 4.5), ("y", 5.0, 6.0)]
    # the piece after the stack is already punched in: no second snap
    assert pieces[1]["ramp_s"] == 0.0
    # a zoom wholly inside the stack does not play at all
    assert renderer._zooms_outside(zooms[:1], [(0.0, 5.0)]) == []
