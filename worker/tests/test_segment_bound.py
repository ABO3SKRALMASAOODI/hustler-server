"""A concat segment must be as long as the EDL says it is.

Round 56. Project 226 — a real customer's 14-span edit with a 1.5s title card —
exported at 158.58s for a 150.48s programme, three times, and the user was told
only "the render is the wrong length". The cause was the per-segment `fps`
filter: on ffmpeg 5.1.9 (Debian bookworm, what the executor image installs) it
emits frames past the segment's real content after a `setpts=PTS-STARTPTS`, and
`concat` then places the next segment at that longer duration. ~+0.6s per
segment, compounding across fourteen of them.

Why it hid for so long:
  * ffmpeg 8.1.2 (a dev Mac) renders the identical graph to the correct length,
    so it never reproduced locally.
  * Video alone is correct even on 5.1 — the stretch only appears when audio
    shares the concat, because that is when the padded video sets the length.
  * A PREVIEW renders from our own proxy and a FINAL from the customer's
    original, which made it look like a property of their file.

The fix bounds each normalized block to its own program length. These tests pin
that the bound is emitted for every kind of concat segment, and that it is the
EDL's number rather than anything measured from the footage.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from renderer import build_filtergraph          # noqa: E402
from schemas import default_edl                 # noqa: E402
from timeline import Timeline                   # noqa: E402

INDEX = {"words": [], "silences": [], "shots": [], "sentences": []}


def _graph(edl, **kw):
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return build_filtergraph(edl, 300.0, True, tl, None, [], INDEX, False,
                             W=478, H=850, fps=29.601, frame_mode=None,
                             insert_inputs=kw.pop("insert_inputs", []),
                             src_w=478, src_h=850, **kw)


def _edl_with_insert():
    e = default_edl(300.0)
    e["keep"] = [[1.72, 19.32], [19.48, 40.5], [40.95, 50.8]]
    e["inserts"] = [{"id": "ins1", "kind": "image", "motion": None,
                     "asset_key": "generated/card.png", "duration_s": 1.5,
                     "at_output_s": 0.0, "source_start_s": None}]
    return e


def test_every_kept_span_is_bounded_to_its_own_length():
    e = _edl_with_insert()
    g = _graph(e, insert_inputs=[(2, e["inserts"][0], False)])
    # 17.600 / 21.020 / 9.850 — the spans, not the source, not the programme.
    for expected in ("trim=end=17.600", "trim=end=21.020", "trim=end=9.850"):
        assert expected in g, f"missing segment bound {expected}"


def test_the_insert_block_is_bounded_too():
    """The title card is a concat segment like any other. It was the block that
    made project 226's export fail, so it may never be the one left unbounded."""
    e = _edl_with_insert()
    g = _graph(e, insert_inputs=[(2, e["inserts"][0], False)])
    assert "trim=end=1.500" in g


def test_the_bound_follows_fps_and_precedes_setsar():
    """Order is load-bearing: the bound exists to discard what `fps` added, so
    it has to come after it. Placing the trim BEFORE fps was measured and does
    not fix the stretch (155.06s, unchanged)."""
    e = _edl_with_insert()
    g = _graph(e, insert_inputs=[(2, e["inserts"][0], False)])
    frag = "fps=29.601,trim=end=17.600,setpts=PTS-STARTPTS,setsar=1"
    assert frag in g, f"expected '{frag}' in the chain"


def test_speed_spans_bound_to_the_PROGRAM_length_not_the_source_span():
    """A 2x span is half as long on the timeline. Bounding it to the source
    span would leave the stretch in place for exactly the edits that use it."""
    e = default_edl(300.0)
    e["keep"] = [[0.0, 20.0]]
    e["speed"] = [{"start": 0.0, "end": 20.0, "factor": 2.0}]
    g = _graph(e)
    assert "trim=end=10.000" in g          # 20s of source, 10s of programme
    assert "trim=end=20.000" not in g


def test_no_bound_when_the_graph_does_not_normalize():
    """The cheap path (no inserts, no reframe, no zoom, no speed) never calls
    the normalizer, emits no `fps` per segment, and therefore has nothing to
    correct. Stored EDLs on that path must keep rendering byte-identically."""
    e = default_edl(300.0)
    e["keep"] = [[1.72, 19.32], [19.48, 40.5]]
    g = _graph(e)
    assert "trim=end=" not in g
