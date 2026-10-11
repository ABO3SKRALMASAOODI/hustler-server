"""One design across the product-fix tracks of the Diamandis run (Oct 2026):
the headline band (band_type, measured at write), the P2 template knobs
(headline ' / ' line breaks), the measured render checks (render_qc) and
the source layout (source_layout) each measure the same things — the
claim's cap height against the captions, a picture card's area against the
Looks' floor. These tests hold them to one answer.

Run:  python -m pytest tests/test_pf_integrate.py -q     (from worker/)
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import band_type  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import render_qc  # noqa: E402
import source_layout  # noqa: E402

W, H = 1080, 1920
# a kicker too long for a 0.5-wide column: it wraps above the claim
WRAPPED = dict(text="Founders should *write* their own story",
               kicker="STEVE JOBS · APPLE · BOSTON · 1983", width=0.5, y=0.2, height=0.2)
ASIDE = dict(text="Kurzweil may have been *too conservative* on AI", kicker="ELON MUSK",
             size=1.4, width=0.88, y=0.1756, height=0.0948, kicker_y=0.1066)
FORCED = dict(text="Musk says / AI is / *coming*", size=1.2, y=0.18, height=0.11)


def _browser_ok():
    try:
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


def _p(**kw):
    return motion_templates.check_params("headline", kw)


def _plan_item(i, params):
    item = {"id": f"h{i}", "template": "headline", "start": 0.0, "end": 6.0,
            "params": params}
    return {"id": f"h{i}", "template": "headline", "start": 0.0, "end": 6.0,
            "layer": "above_captions", "tier": None, "purpose": None,
            "persistent": True, "box": None, "item": item}


def test_one_floor_and_one_owns_ratio():
    # the picture floor the render reports, the source layout's card report
    # and the headline write's card advice are one number
    assert render_qc.PICTURE_FLOOR == source_layout.PICTURE_FLOOR == motion_tools.CARD_FLOOR
    # a band claim under this many times the captions reads as their size,
    # at write (motion_tools) and on the render (render_qc.advice)
    assert motion_tools.HEADLINE_OWNS_RATIO == band_type.OWNS_RATIO


def test_the_render_skips_every_line_of_a_wrapped_kicker():
    assert band_type.headline_layout(_p(**WRAPPED), W, H)["kicker_lines"] == 3
    assert render_qc._kicker_rows(_plan_item(0, _p(**WRAPPED)), W, H) == 3
    assert render_qc._kicker_rows(_plan_item(0, _p(**ASIDE)), W, H) == 1
    assert render_qc._kicker_rows(_plan_item(0, _p(**FORCED)), W, H) == 0
    # three kicker lines at the 2.2% floor over a smaller claim: the claim
    # is the last line, not the kicker's second (what lines[1:] read)
    m = {"lines": [{"cap": .022}, {"cap": .022}, {"cap": .022}, {"cap": .0214}],
         "cap": .022}
    assert render_qc._claim_cap(m, 3) == .0214
    assert render_qc._claim_cap(m, True) == .022            # one-line rule, unchanged
    # fewer lines read than the layout predicts: the last line stays the claim
    assert render_qc._claim_cap({"lines": [{"cap": .03}, {"cap": .02}], "cap": .03}, 3) == .02


def test_a_band_headline_under_the_owns_ratio_is_advised():
    def advise(hook_cap, persistent):
        tplan = {"hook": "hl", "payoff": None, "items": [
            {"id": "hl", "persistent": persistent, "layer": "above_captions",
             "start": 0.0, "end": 30.0, "box": [.06, .13, .94, .23]}]}
        tres = {"items": {"hl": {"cap": hook_cap}},
                "cues": [{"t": 1.0, "cap": .026, "box": [.3, .72, .7, .77], "text": "x"}]}
        return render_qc.advice({"type": render_qc.type_findings(tres, tplan, [])})
    small = advise(.020, True)
    assert small and small[0].startswith("HOOK SMALLER THAN THE CAPTIONS")
    near = advise(.028, True)                               # 1.08x the captions
    assert near and near[0].startswith("HOOK NO LARGER THAN THE CAPTIONS")
    assert "1.08x" in near[0] and "1.2x" in near[0]
    assert advise(.032, True) == []                         # 1.23x: owns the band
    # a lockup hook (word_slam) answers to its own tier rule, not the band's
    assert advise(.028, False) == []


@pytest.mark.skipif(not _browser_ok(), reason="no headless Chromium")
def test_the_render_measures_the_claim_the_write_measured():
    # the write's browserless layout pass (band_type) and the render's ink
    # measurement (render_qc.type_pass) read the same claim: a kicker set
    # beside the mark, a wrapped stacked kicker, forced ' / ' lines
    cases = [_p(**c) for c in (ASIDE, WRAPPED, FORCED)]
    tplan = {"W": W, "H": H, "fps": 30.0, "captions": [],
             "items": [_plan_item(i, p) for i, p in enumerate(cases)]}
    tres = render_qc.type_pass(tplan, time.monotonic() + 120)
    for i, p in enumerate(cases):
        est = band_type.headline_layout(p, W, H)["claim_cap"]
        got = tres["items"][f"h{i}"]["cap"]
        assert got == pytest.approx(est, rel=0.06), (p, got, est)
