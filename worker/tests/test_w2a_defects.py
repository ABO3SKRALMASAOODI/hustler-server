"""Track 2A: the run-report defects in the typesetter's scope (RUN_JRE2054).

  20. The caption placement cliff: a graphic 5 px taller flipped the
      captions from under it to over the hair (or onto it). Captions now
      keep their side of the face and drop to one-line pages instead.
   5. A slam placed in the headline band is kept under the corner mark's
      floor (and says so) instead of landing on the mark.
   6. A full-frame html graphic that hides the captions is reported as
      hiding them (heard but unshown), not as showing the words.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import caption_carry  # noqa: E402
import caption_place  # noqa: E402
from test_caption_place import _edl, _index, _plan  # noqa: E402


def _slam(y0, y1):
    return {"id": "g1", "template": "word_slam", "start": 0.3, "end": 8.0,
            "params": {"text": "VIDEO GAMES"},
            "footprint": {"box": [0.1, y0, 0.9, y1], "ar": round(1080 / 1920, 4),
                          "faces": []}}


def _ys(p):
    return sorted({round(pl["y"], 3) for pl in p.placed.values()})


def test_a_graphic_a_few_pixels_taller_does_not_throw_the_captions_over_the_head(
        monkeypatch):
    ix = _index()
    # a speaker framed lower (s08's full-bleed crop): room over the hair
    for smp in ix["spatial"]["samples"]:
        smp["faces"] = [[0.42, 0.42, 0.58, 0.6]]
    # the band under the graphic: 0.085 (two lines) vs 0.08 (one line fits)
    roomy = _plan(_edl(motion=[_slam(0.66, 0.70)]), ix)
    tight_edl = _edl(motion=[_slam(0.66, 0.705)])
    # the old behaviour (no side rule): the band over the hair wins
    monkeypatch.setattr(caption_carry, "_crosses_face", lambda *a, **k: False)
    old = _plan(tight_edl, ix)
    monkeypatch.undo()
    new = _plan(tight_edl, ix)
    roomy_y = _ys(roomy)
    assert roomy_y and min(roomy_y) > 0.7          # under the graphic
    assert any(y < 0.4 for y in _ys(old)), _ys(old)   # the cliff existed
    assert _ys(new) and min(_ys(new)) > 0.7, _ys(new)  # no cliff now
    for pl in new.placed.values():               # in the band under it
        assert pl["z"][0] >= 0.705 and pl["z"][1] <= 0.8 + 1e-6


def test_a_slam_in_the_headline_band_is_kept_under_the_corner_mark(monkeypatch):
    """Run report defect 5 (s06 'rounds23' at y 0.178 drew 0.076-0.27 on the
    corner mark, silently): the keep-out now treats the mark's zone as an
    issue on every frame and moves the slam below the mark's floor."""
    import keepout
    import motion_tools
    from schemas import default_edl
    from test_keepout import _Ctx
    monkeypatch.setattr(motion_tools, "_probe_item",
                        lambda item, W, H, fps=30.0: None)
    mark = keepout.watermark_zone(1080, 1920)
    edl = default_edl(10.0)
    ctx = _Ctx(edl, {"video": {"width": 1080, "height": 1920}}, 10.0)
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 1.0, 2.5,
        params={"text": "ROUNDS 2-3", "y": 0.178}, id="rounds23")
    assert "corner mark" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    box = item["footprint"]["box"]
    assert box[1] >= mark[3] - keepout.EDGE_TOL or box[0] >= mark[2], (box, mark)
    assert not keepout.on_mark(box, keepout.mark_zone(1080, 1920))


def test_the_mark_issue_holds_on_landscape_frames_too():
    # merged with Wave 1's corner-mark floor (keepout.mark_zone / on_mark)
    import keepout
    zone = keepout.mark_zone(1920, 1080)
    inside = (zone[0] + 0.01, zone[1] + 0.01, zone[2] + 0.1, zone[3] + 0.1)
    assert keepout.on_mark(inside, zone)
    assert not keepout.on_mark((0.6, 0.4, 0.9, 0.6), zone)


def _html_full(start=1.9, end=5.2, params=None):
    return {"id": "page", "template": "html", "start": start, "end": end,
            "html": "<div class='mg-root'><svg></svg></div>",
            "params": params or {"accent": "#ff0000", "count": "0",
                                 "ease": "outCubic"},
            "footprint": {"box": [0.0, 0.0, 1.0, 1.0],
                          "ar": round(1080 / 1920, 4), "faces": []}}


def test_a_full_frame_page_that_hides_the_captions_is_reported_as_hiding_them():
    """Run report defect 6: words kept under a full-frame html graphic (no
    band clear of it) were counted as shown."""
    from timeline import Timeline
    edl = _edl(motion=[_html_full()])
    ix = _index()
    gaps = caption_carry.heard_unshown(edl, ix, Timeline(edl["keep"]))
    assert gaps, "the hidden words must be reported"
    g = gaps[0]
    assert g["owner"] == "page" and "draws over the captions" in g["cause"]
    assert 1.9 <= g["start"] < 5.2


def test_an_authored_pages_settings_are_not_words_on_screen():
    """Run report defect 24: html params like '0' or '#ff0000' read as text."""
    item = _html_full(params={"accent": "#ff0000", "count": "0", "speed": 1.5,
                              "ease": "outCubic", "title": "Video games"})
    lines = caption_carry.graphic_lines(item)
    texts = " ".join(t for _k, t in lines)
    assert "Video games" in texts
    assert "#ff0000" not in texts and " 0" not in f" {texts} " \
        and "outCubic" not in texts


def test_a_spelled_out_range_reads_as_two_numbers():
    import number_reveal
    words = [{"w": w, "t0": 0.3 * i, "t1": 0.3 * i + 0.25} for i, w in enumerate(
        "rounds of three hundred sixty, three hundred seventy pounds".split())]
    got = [sorted(n["values"]) for n in number_reveal.spoken_numbers(words)]
    assert [360.0] in got and [370.0] in got, got
    # a scale word keeps a comma inside one number
    words = [{"w": w, "t0": 0.3 * i, "t1": 0.3 * i + 0.25} for i, w in enumerate(
        "three thousand, two hundred people".split())]
    assert [sorted(n["values"]) for n in number_reveal.spoken_numbers(words)] == [[3200.0]]


def test_the_counter_page_scales_by_size_and_never_counts_a_comparison():
    import motion_templates
    body = open(os.path.join(os.path.dirname(HERE), "motion", "templates",
                             "counter.html"), encoding="utf-8").read()
    assert "* S\n" in body or "* S;" in body or ") * S" in body
    assert "[<>≤≥~≈]" in body
    assert motion_templates.spec("counter")["params"]["size"]["min"] <= 0.62
