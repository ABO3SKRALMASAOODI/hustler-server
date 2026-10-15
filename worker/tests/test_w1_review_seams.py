"""Seams the Wave 1 review (w1r) found on real renders.

* A device whose window ends in an insert (the payoff hold every podcast
  short carries) or spans reordered keeps asked the sidecar status for no
  source range at all (``fxgraph._source_ranges`` read the window's two
  endpoints), so the resolver read the person matte as absent and the
  device took its substitute on a short whose matte was measured: the top
  recipe, colour_kept_payoff, rendered as nothing on the W1-S1 Thiel cut.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import fxgraph  # noqa: E402
from timeline import Timeline  # noqa: E402


class _Plan:
    def __init__(self, tl):
        self.tl = tl


def test_a_window_into_the_payoff_hold_asks_for_its_footage():
    # 31.4 s of footage, then a 0.6 s still hold at 31.4
    tl = Timeline([[849.0, 865.0], [866.0, 881.4]],
                  [{"id": "h", "kind": "image", "asset_key": "x.png",
                    "at_output_s": 31.4, "duration_s": 0.6}], [])
    item = {"id": "iso", "start": 30.76, "end": 31.9}
    got = fxgraph._source_ranges(item, _Plan(tl))
    assert got and abs(got[0][0] - 880.76) < 1e-3 \
        and abs(got[-1][1] - 881.4) < 1e-3, got


def test_reordered_keeps_and_cuts_ask_for_each_span_only():
    tl = Timeline([[100.0, 104.0], [20.0, 24.0]], [], [])
    got = fxgraph._source_ranges({"id": "p", "start": 3.0, "end": 5.0},
                                 _Plan(tl))
    assert [(round(a, 3), round(b, 3)) for a, b in got] == \
        [(103.0, 104.0), (20.0, 21.0)]
    tl = Timeline([[0.0, 4.0], [30.0, 34.0]], [], [])
    got = fxgraph._source_ranges({"id": "c", "start": 3.0, "end": 5.0},
                                 _Plan(tl))
    assert sum(b - a for a, b in got) < 2.01          # not 3.0 .. 31.0
