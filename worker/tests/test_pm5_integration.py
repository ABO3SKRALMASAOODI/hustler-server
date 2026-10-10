"""Where two premium-motion tracks touch the same write: the unified order.

Each track's own suite pins its behaviour; these pin how they compose.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import motion_tools  # noqa: E402
from test_caption_carry import _Ctx, _probe_with  # noqa: E402


def test_a_number_lands_before_its_reading_is_timed(monkeypatch):
    # number landing (punch) moves a figure slam onto its spoken number; the
    # one reading path (fidelity) is worked out on the window as it LANDS,
    # so its caption note names the moved exit, not the requested one
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_with((0.1, 0.62, 0.9, 0.82)))
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.9, 3.0,
                                          params={"text": "*140*"}, id="slam")
    item = ctx.latest_edl()["json"]["motion"][0]
    assert (item["start"], item["end"]) == (2.78, 3.88), item
    assert out.index("NUMBER LANDED") < out.index("NOTE (captions)"), out
    # "characters" is said while the moved slam is up: the reading names it
    # and the exit the slam really has
    assert "until it leaves at 3.88s" in out and '"characters' in out, out
