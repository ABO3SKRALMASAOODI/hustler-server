"""The persistent headline band of card and letterbox layouts (judges, Oct
2026): the band above the picture sat empty for 5-6 s stretches between hero
lockups, where the references keep a standing claim headline that hero
lockups replace and hand back.

The `headline` motion template is PERSISTENT (motion_templates.persistent):
it holds its band for the program and yields it at render time
(motion_layer.yield_windows -> MG.yields) to any graphic whose box meets the
band — fading out before that graphic lands and back after it leaves — and
the write contract (motion_tools._persistent_contract) keeps it a headline.

Run:  python -m pytest tests/test_headline_band.py -q     (from worker/)
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import caption_carry  # noqa: E402
import keepout  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import taste  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

W, H = 1080, 1920
AR = caption_carry.frame_ar(W, H)


def _browser_ok():
    try:
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not _browser_ok(), reason="no headless Chromium")


def _fp(box):
    return caption_carry.make_footprint(box, W, H, [])


def _hl(start=0.0, end=30.0, **params):
    p = motion_templates.check_params("headline", dict(
        {"text": "The case for *beautiful* type on computers",
         "kicker": "Steve Jobs, 1983", "y": 0.19, "height": 0.2}, **params))
    return {"id": "hl", "template": "headline", "start": start, "end": end, "params": p,
            "footprint": _fp([0.2, 0.09, 0.8, 0.29])}


def _lock(mid, start, end, box=(0.08, 0.06, 0.92, 0.24), **kw):
    item = {"id": mid, "template": "word_slam", "start": start, "end": end,
            "params": motion_templates.check_params("word_slam", {"text": "x", "y": 0.15}),
            "footprint": _fp(list(box))}
    item.update(kw)
    return item


# ── the template ──────────────────────────────────────────────────────────

def test_headline_is_a_silent_persistent_layout_template():
    spec = motion_templates.spec("headline")
    assert spec["persistent"] is True and spec["category"] == "layout"
    assert spec["sfx"] == [] and not spec.get("mutes_captions")
    assert motion_templates.persistent("headline")
    assert motion_templates.persistent({"template": "headline"})
    assert not motion_templates.persistent("word_slam")
    assert not motion_templates.persistent({"template": "html"})
    row = next(t for t in motion_templates.catalog() if t["name"] == "headline")
    assert row["persistent"] is True and "YIELDS" in row["description"]


def test_the_estimate_of_a_headline_is_its_band():
    spec = motion_templates.spec("headline")
    box = keepout.nominal_ink("headline", spec, {"y": 0.19, "height": 0.2, "width": 0.8})
    assert box == pytest.approx((0.1, 0.09, 0.9, 0.29))


# ── yielding ──────────────────────────────────────────────────────────────

def test_it_holds_its_band_whenever_nothing_else_does():
    """Judged Oct 2026: the Jobs band stood empty at 4.2-4.4, 14.09-15.0,
    17.79-18.58 and 32.34-33.04 s — gaps under 1.2 s were kept clear. The
    headline now comes back for every gap of YIELD_MERGE_S (0.15 s) or more
    and swaps on the frame; only its composition's edges take a landing
    within YIELD_EDGE_S."""
    hl = _hl()
    items = [hl,
             _lock("hook", 0.4, 4.2),                 # near the start: from 0
             _lock("b", 4.4, 6.0),                     # 0.2 s later: back between
             _lock("low", 8.0, 9.0, box=(0.1, 0.72, 0.9, 0.8)),   # caption band
             _lock("c", 12.0, 13.0),
             _lock("d", 13.1, 15.0),                   # 0.1 s gap: stays yielded
             _lock("e", 18.0, 29.6),                   # near the end: to it
             {"id": "cap", "template": "caption_motion", "start": 0, "end": 30,
              "params": {}, "_synthetic": True}]
    wins = motion_layer.yield_windows(hl, items, W, H)
    assert wins == [[0.0, 4.2], [4.4, 6.0], [12.0, 15.0], [18.0, 30.0]]
    shown, prog = motion_layer.headline_visible(hl, items, W, H)
    assert shown == pytest.approx(30 - 4.2 - 1.6 - 3 - 12)
    assert prog == wins                                 # starts at 0: same clock
    assert motion_layer.yield_windows(items[1], items, W, H) == []
    # the swap is on the frame: gone as the other lands, back as it leaves
    doc = motion_layer.yields_doc(wins)
    assert doc["out"] == 0.0 and doc["in"] <= motion_layer.YIELD_MERGE_S


def test_a_phrase_build_yields_from_its_first_reveal_and_windows_follow_the_phase():
    rows = [{"text": "injecting some", "role": "sans", "size": "0.5", "at": "1.17"},
            {"text": "*liberal arts*", "role": "serif", "size": "1.4", "at": "1.89"}]
    pb = {"id": "arts", "template": "phrase_build", "start": 21.3, "end": 25.21,
          "params": motion_templates.check_params("phrase_build", {"rows": rows, "y": 0.15}),
          "footprint": _fp([0.09, 0.06, 0.92, 0.24])}
    hl = _hl(end=37.84)
    assert motion_layer.yield_windows(hl, [hl, pb], W, H) == [[22.47, 25.21]]
    # a stitched piece starting 20 s into the composition: composition seconds
    piece = dict(hl, start=0.0, end=10.0, phase_s=20.0, full_duration_s=37.84)
    pb2 = dict(pb, start=1.3, end=5.21)
    assert motion_layer.yield_windows(piece, [piece, pb2], W, H) == [[22.47, 25.21]]


def test_an_unmeasured_graphic_yields_by_its_estimated_box():
    hl = _hl()
    slam = {"id": "s", "template": "word_slam", "start": 5.0, "end": 6.0,
            "params": motion_templates.check_params("word_slam", {"text": "GARBAGE", "y": 0.15})}
    low = {"id": "l", "template": "word_slam", "start": 8.0, "end": 9.0,
           "params": motion_templates.check_params("word_slam", {"text": "x", "y": 0.7})}
    assert motion_layer.yield_windows(hl, [hl, slam, low], W, H) == [[5.0, 6.0]]


def test_build_document_carries_yields_only_when_there_are_some():
    a = motion_engine.build_document("<div></div>", duration=2.0)
    assert motion_engine.build_document("<div></div>", duration=2.0, yields=None) == a
    assert motion_engine.build_document("<div></div>", duration=2.0,
                                        yields=motion_layer.yields_doc([])) == a
    b = motion_engine.build_document("<div></div>", duration=2.0,
                                     yields=motion_layer.yields_doc([[0.5, 1.0]]))
    assert '"yields": {"w": [[0.5, 1.0]], "out": 0.0, "in": 0.1}' in b


@needs_browser
def test_the_runtime_fades_the_page_out_and_back_around_a_yield(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    item = dict(_hl(end=3.0), footprint=None)
    job = motion_templates.build_job(item, 540, 960, 30,
                                     yields=motion_layer.yields_doc([[1.0, 2.0]]))
    clip = motion_engine.render_jobs([job], str(tmp_path / "out"), pages=1)[0]
    assert not clip.empty and clip.captured < clip.frames      # holds are re-used
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip.path, "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    n = clip.w * clip.h * 4
    alpha = [max(raw[i * n + 3:(i + 1) * n:4]) for i in range(len(raw) // n)]
    assert alpha[24] > 200 and alpha[29] > 200   # on screen up to the swap
    assert alpha[30] == 0 and alpha[45] == 0 and alpha[60] == 0   # 1.0-2.0 s: yielded
    assert 0 < alpha[61] < alpha[64]        # back from the frame it is free
    assert alpha[64] > 200


# ── captions and critics ──────────────────────────────────────────────────

def _words(text, t0=1.0, step=0.4):
    out = []
    for k, w in enumerate(text.split()):
        out.append({"w": w, "t0": t0 + k * step, "t1": t0 + k * step + 0.3,
                    "src_t0": t0 + k * step, "src_t1": t0 + k * step + 0.3})
    return out


def test_a_headline_never_takes_spoken_words_out_of_the_captions():
    edl = default_edl(30.0)
    edl["captions"] = {"mode": "from_transcript"}
    edl["motion"] = [_hl()]
    words = _words("computer fonts were garbage and beautiful type matters")
    tl = Timeline(edl["keep"])
    plan = caption_carry.plan(edl, {"words": words}, tl, words)
    assert plan.hidden == {}
    # the same text on a lockup takes the words it shows
    edl["motion"] = [dict(_lock("s", 0.0, 30.0), params=motion_templates.check_params(
        "word_slam", {"text": "*garbage*", "y": 0.15}))]
    plan = caption_carry.plan(edl, {"words": words}, tl, words)
    assert any(owner == "s" for owner, _why in plan.hidden.values())


def test_the_headline_is_layout_not_a_designed_moment():
    edl = validate_edl(dict(default_edl(40.0), motion=[_hl(end=12.0), _lock("s", 2.0, 3.0)]),
                       40.0).model_dump()
    moments = [m["id"] for m in taste._motion_moments(edl, 40.0)]
    assert moments == ["s"]


# ── the write contract ────────────────────────────────────────────────────

class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, edl, duration=60.0):
        self.duration = duration
        self.index = {"video": {"width": 646, "height": 480, "fps": 30.0},
                      "words": [], "shots": [{"id": 1, "start": 0.0, "end": 60.0}]}
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def _probe(item, W_, H_, fps=30.0):
    p = item.get("params") or {}
    y, h = float(p.get("y", 0.5)), float(p.get("height", 0.12))
    box = [0.2, round(y - h / 2, 4), 0.8, round(y + h / 2, 4)]
    return {"errors": [], "visible_frames": 4, "samples": 4, "bboxes": [box], "ink": [box] * 4}


def _card_edl(prog=38.0):
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, prog]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["effects"] = {"picture_cards": [{
        "id": "card", "start": 0.0, "end": prog, "box": [0.06, 0.3042, 0.94, 0.6758],
        "source": [0.19, 0.077, 0.955, 0.85], "entrance": "none", "exit": "none"}]}
    return edl


TEXT = {"text": "The case for *beautiful* type on computers", "kicker": "Steve Jobs, 1983"}


def test_add_places_the_headline_in_the_band_above_the_card(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)
    ctx = _Ctx(_card_edl())
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT), id="hl")
    assert out.startswith("EDL v1"), out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["end"] == pytest.approx(38.0)                  # holds for the program
    # below the feed header AND the free-tier mark's zone (judged: the
    # Jobs kickers at y 0.09-0.115 sat on the mark)
    top, bottom = motion_tools.band_top(1080, 1920), 0.3042 - motion_tools.HEADLINE_GAP
    assert top >= keepout.watermark_zone(1080, 1920)[3] > motion_tools.HEADLINE_SAFE_TOP
    assert item["params"]["y"] == pytest.approx((top + bottom) / 2, abs=1e-3)
    assert item["params"]["height"] == pytest.approx(bottom - top, abs=1e-3)
    assert item.get("mute_captions") is None
    assert "HEADLINE: placed in the band above the picture card" in out
    assert "YIELDS: nothing else occupies its band" in out
    assert not ctx.latest_edl()["json"].get("sfx")
    # a lockup in the band is told the headline yields to it, and the
    # headline's own report then names the window
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 10.0, 11.0,
                                          params={"text": "GARBAGE", "y": 0.18}, id="slam")
    assert "headline hl yields its band to this graphic" in out, out
    out = motion_tools.set_motion_graphic(ctx, "hl", params={"text": "Jobs on *type*"})
    assert "YIELDS: hands its band to the graphics over 10.00-11.00s" in out, out


def test_the_contract_keeps_it_a_headline(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)
    ctx = _Ctx(_card_edl())
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, 3.0, params=dict(TEXT))
    assert out.startswith("REJECTED") and "at least 4" in out
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT),
                                          mute_captions=True)
    assert out.startswith("REJECTED") and "never mutes captions" in out
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(
        TEXT, text="*Two* accents is *noise*"))
    assert out.startswith("REJECTED") and "one accent span" in out
    assert motion_tools.add_motion_graphic(ctx, "headline", 0.0, 20.0, params=dict(TEXT),
                                           id="a").startswith("EDL v1")
    out = motion_tools.add_motion_graphic(ctx, "headline", 19.0, params=dict(TEXT))
    assert out.startswith("REJECTED") and "one headline at a time" in out
    # one per chapter is fine
    assert motion_tools.add_motion_graphic(ctx, "headline", 20.0, params=dict(TEXT),
                                           id="b").startswith("EDL v2")
    out = motion_tools.set_motion_graphic(ctx, "b", start=18.0)
    assert out.startswith("REJECTED") and "one headline at a time" in out


def test_full_bleed_needs_a_deliberate_y(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, 30.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    ctx = _Ctx(edl)
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT))
    assert out.startswith("REJECTED") and "needs a free band" in out
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT, y=0.12))
    assert out.startswith("EDL v1"), out
    # a letterbox picture leaves a band too
    edl["frame"] = {"ratio": "9:16", "mode": "pad", "picture": [0.0, 0.3, 1.0, 0.7]}
    ctx = _Ctx(edl)
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT))
    assert "above the letterboxed picture" in out, out


def test_a_program_long_headline_stays_program_long_through_a_recut():
    from timeline import PINNED_MOTION_TEMPLATES, remap_program_items
    assert set(PINNED_MOTION_TEMPLATES) == {
        n for n in motion_templates.names() if motion_templates.persistent(n)}
    edl = validate_edl(dict(default_edl(60.0), keep=[[0.0, 30.0]],
                            motion=[_hl(end=30.0), _lock("s", 20.0, 21.0)]),
                       60.0).model_dump()
    old = Timeline(edl["keep"])
    edl["keep"] = [[0.0, 10.0], [12.0, 40.0]]          # 2 s out, 10 s more at the end
    remap_program_items(edl, old, Timeline(edl["keep"]))
    hl = next(m for m in edl["motion"] if m["id"] == "hl")
    slam = next(m for m in edl["motion"] if m["id"] == "s")
    assert (hl["start"], hl["end"]) == (0.0, 38.0)
    assert (slam["start"], slam["end"]) == (18.0, 19.0)  # a cued moment follows its words


# ── review fixes (Oct 2026) ───────────────────────────────────────────────

def test_a_phrase_build_cut_into_by_a_piece_yields_from_its_drawn_rows():
    rows = [{"text": "injecting some", "role": "sans", "size": "0.5", "at": "1.17"},
            {"text": "*liberal arts*", "role": "serif", "size": "1.4", "at": "1.89"}]
    pb = {"id": "arts", "template": "phrase_build", "start": 0.0, "end": 3.21,
          "phase_s": 0.7, "full_duration_s": 3.91,
          "params": motion_templates.check_params("phrase_build", {"rows": rows, "y": 0.15}),
          "footprint": _fp([0.09, 0.06, 0.92, 0.24])}
    # a piece starting 22.0 s into the program: the build is 0.7 s in, its
    # first row lands 0.47 s into the piece (22.47 on the composition clock)
    piece = dict(_hl(end=37.84), start=0.0, end=10.0, phase_s=22.0,
                 full_duration_s=37.84)
    assert motion_layer.yield_windows(piece, [piece, pb], W, H) == [[22.47, 25.21]]
    late = dict(pb, phase_s=2.0, full_duration_s=5.2)       # every row already drawn
    assert motion_layer._ink_lead(late) == 0.0


def test_an_authored_page_with_a_capture_box_yields_by_that_box():
    hl = _hl()
    page = {"id": "p", "template": "html", "start": 5.0, "end": 6.0, "html": "<div></div>",
            "params": {}, "box": [0.1, 0.1, 0.9, 0.25]}
    low = dict(page, id="q", start=8.0, end=9.0, box=[0.1, 0.7, 0.9, 0.78])
    blind = dict(page, id="r", start=12.0, end=13.0, box=None)
    assert motion_layer.yield_windows(hl, [hl, page, low, blind], W, H) == [[5.0, 6.0]]


def test_a_headline_longer_than_one_clip_renders_in_pieces_on_one_clock(tmp_path, monkeypatch):
    long_hl = dict(_hl(end=300.0), footprint=_fp([0.2, 0.09, 0.8, 0.29]))
    pieces = motion_layer.render_pieces(long_hl, 30.0)
    assert len(pieces) == 3
    assert pieces[0]["start"] == 0.0 and pieces[-1]["end"] == 300.0
    for a, b in zip(pieces, pieces[1:]):
        assert a["end"] == b["start"]
        assert round(b["start"] * 30, 6) == round(b["start"] * 30)     # frame-aligned
    for p in pieces:
        assert p["end"] - p["start"] <= motion_engine.MAX_DURATION_S
        assert p["phase_s"] == pytest.approx(p["start"])
        assert p["full_duration_s"] == 300.0
    # short, non-persistent and behind-subject items are left whole
    assert motion_layer.render_pieces(_hl(end=30.0), 30.0) == [_hl(end=30.0)]
    slam = _lock("s", 0.0, 200.0)
    assert motion_layer.render_pieces(slam, 30.0) == [slam]
    assert len(motion_layer.render_pieces(dict(long_hl, layer="behind_subject"), 30.0)) == 1

    # the render lane builds one job per piece, each under the engine's
    # limit, sharing the whole item's yield windows (composition seconds)
    seen = []

    def fake_render(jobs, out_dir, pages=None, budget_s=900.0):
        seen.extend(jobs)
        return [motion_engine.RenderedClip(str(tmp_path / f"{k}.mov"), 0, 0, 10, 10,
                                           int(j.duration * 30), 1, False, 0.0)
                for k, j in enumerate(jobs)]
    monkeypatch.setattr(motion_engine, "render_jobs", fake_render)
    edl = {"motion": [long_hl, _lock("s", 150.0, 152.0)]}
    args = []
    inputs, nxt = motion_layer.prepare_inputs(edl, str(tmp_path), W, H, 30.0, 300.0, args, 1)
    hl_jobs = [j for j in seen if "(headline)" in j.label]
    assert len(hl_jobs) == 3 and nxt == 1 + len(seen)
    assert all(j.duration <= motion_engine.MAX_DURATION_S for j in hl_jobs)
    assert [round(j.t0, 3) for j in hl_jobs] == [round(p["start"], 3) for p in pieces]
    marks = {j.html.split('"yields": ')[1].split("}")[0] for j in hl_jobs}
    assert marks == {'{"w": [[150.0, 152.0]], "out": 0.0, "in": 0.1'}
    spans = [(it["start"], it["end"]) for _i, it, _c in inputs if it["template"] == "headline"]
    assert spans == [(p["start"], p["end"]) for p in pieces]


def test_a_pad_letterbox_leaves_a_band_without_a_picture_rect(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, 30.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "pad"}
    ctx = _Ctx(edl)
    ctx.index["video"] = {"width": 1920, "height": 1080, "fps": 30.0}
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT))
    assert out.startswith("EDL v1"), out
    assert "above the letterboxed picture" in out
    item = ctx.latest_edl()["json"]["motion"][0]
    top = (1.0 - (9 / 16) / (16 / 9)) / 2.0                 # the 16:9 picture's top
    assert item["params"]["y"] == pytest.approx(
        (motion_tools.band_top(1080, 1920) + top - motion_tools.HEADLINE_GAP) / 2, abs=2e-3)
    # a shot the focus track crops full-frame leaves no free band
    edl["frame"]["focus_track"] = [{"t0": 0.0, "t1": 12.0, "mode": "pad"},
                                   {"t0": 12.0, "t1": 30.0, "mode": "crop"}]
    ctx = _Ctx(edl)
    ctx.index["video"] = {"width": 1920, "height": 1080, "fps": 30.0}
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT))
    assert out.startswith("REJECTED") and "needs a free band" in out
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, 10.0, params=dict(TEXT))
    assert out.startswith("EDL v1"), out


@needs_browser
def test_pieces_of_a_long_headline_play_exactly_the_frames_of_one_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    item = dict(_hl(end=3.0), footprint=None)
    ydoc = motion_layer.yields_doc([[1.0, 2.0]])
    whole = motion_templates.build_job(item, 270, 480, 30, yields=ydoc)
    monkeypatch.setattr(motion_engine, "MAX_DURATION_S", 1.3)
    pieces = motion_layer.render_pieces(item, 30)
    assert len(pieces) == 3
    jobs = [motion_templates.build_job(p, 270, 480, 30, yields=ydoc) for p in pieces]
    monkeypatch.setattr(motion_engine, "MAX_DURATION_S", 120.0)
    clips = motion_engine.render_jobs([whole] + jobs, str(tmp_path / "out"), pages=1)

    def alphas(clip):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip.path, "-f", "rawvideo",
                              "-pix_fmt", "rgba", "-"], capture_output=True).stdout
        n = clip.w * clip.h * 4
        return [sum(raw[i * n + 3:(i + 1) * n:4]) for i in range(len(raw) // n)]
    ref = alphas(clips[0])
    got = [a for c in clips[1:] for a in alphas(c)]
    assert len(got) == len(ref) == 90
    assert got == ref


def test_a_phrase_build_in_the_band_is_told_the_headline_yields_from_its_first_row(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)
    ctx = _Ctx(_card_edl())
    assert motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(TEXT),
                                           id="hl").startswith("EDL v1")
    rows = [{"text": "injecting some", "role": "sans", "size": "0.5", "at": "1.0"},
            {"text": "*liberal arts*", "role": "serif", "size": "1.4", "at": "1.6"}]
    out = motion_tools.add_motion_graphic(ctx, "phrase_build", 20.0, 24.0,
                                          params={"rows": rows, "y": 0.18}, id="arts")
    assert "headline hl yields its band to this graphic" in out, out
