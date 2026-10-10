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
    # the end it names gives "characters" back (before its midpoint, 3.6 s)
    assert "End it at 3.58s" in out, out
    again = motion_tools.set_motion_graphic(ctx, "slam", end=3.58)
    assert "NOTE (captions)" not in again and "NUMBER LANDED" not in again, again


# ── a lockup's reveal times: one definition for the page and the engine ──
# The headline band yields from a phrase_build's first drawn word (layout)
# and a sound's visual partner is a row landing (sound); since the one
# reading path (fidelity) a lockup's rows land on their SPOKEN onsets, so
# both read caption_carry.lockup_reveals — the page's own timing.

import asyncio  # noqa: E402

import caption_carry  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import sfx_placement  # noqa: E402
from test_one_reading_path import PAPER_ROWS, _job, _states, needs_browser  # noqa: E402

SPOKEN = {"v": 1, "rows": [[0.7, 0.92, 1.28], [3.28, 3.5, 3.66], [3.9, 4.2]],
          "bridges": [{"after": -1, "words": [{"t": "and", "s": 0.4}, {"t": "so", "s": 0.52}]},
                      {"after": 0, "words": [{"t": "three", "s": 1.6}, {"t": "years", "s": 1.7}]}]}


def _pb(rows, reading=None, start=0.0, end=5.3, **params):
    m = {"id": "pb", "template": "phrase_build", "start": start, "end": end,
         "params": motion_templates.check_params("phrase_build", dict(params, rows=rows))}
    if reading:
        m["reading"] = reading
    return m


def test_reveals_follow_the_reading_not_the_fallback_at_times():
    # rows: bridge(-1), row 0, bridge(0), row 1, row 2 in reading order
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS, SPOKEN)) == [0.4, 0.7, 1.6, 3.28, 3.9]
    # no reading: the rows' own 'at' times
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS)) == [0.7, 3.28, 3.9]
    # a 'rise' word starts 0.06 s early; reading order is never broken
    rise = _pb([{"text": "late", "at": "2.0"}, {"text": "early", "at": "1.0"}], entrance="rise")
    assert caption_carry.lockup_reveals(rise) == [2.0, 2.0]
    assert caption_carry.lockup_reveals(_pb(PAPER_ROWS[:1], {"v": 1, "rows": [[0.7, 0.92, 1.28]]},
                                            entrance="rise")) == [0.64]
    assert caption_carry.lockup_reveals({"template": "word_slam", "params": {}}) == []


def test_the_headline_yields_from_the_lockups_first_spoken_word():
    lock = _pb(PAPER_ROWS, SPOKEN, start=10.0, end=15.3)
    assert motion_layer._ink_lead(lock) == 0.4          # the leading bridge line
    unread = _pb(PAPER_ROWS, start=10.0, end=15.3)
    assert motion_layer._ink_lead(unread) == 0.7        # the first row's 'at'
    # a stitched piece 1 s into the composition waits that much less
    assert motion_layer._ink_lead(dict(lock, phase_s=1.0)) == 0.0


def test_a_sound_partner_is_a_spoken_row_landing():
    lock = _pb(PAPER_ROWS, SPOKEN, start=10.0, end=15.3)
    lands = sfx_placement._motion_landings(lock)
    assert 13.28 in lands and 13.9 in lands and 10.4 in lands
    # the fallback 'at' of a spoken row is no landing any more
    lock2 = _pb([dict(PAPER_ROWS[0], at="0.2")] + PAPER_ROWS[1:], SPOKEN, start=10.0, end=15.3)
    assert 10.2 not in sfx_placement._motion_landings(lock2)


def test_a_standing_headline_and_a_cut_step_are_no_sound_partners():
    from schemas import default_edl, validate_edl
    edl = default_edl(30.0)
    edl["motion"] = [{"id": "hl", "template": "headline", "start": 0.0, "end": 30.0,
                      "params": motion_templates.check_params("headline", {"text": "A *claim*"})},
                     {"id": "mid", "template": "headline", "start": 12.0, "end": 30.0,
                      "params": motion_templates.check_params("headline", {"text": "Chapter"})}]
    edl["effects"] = {"zooms": [{"id": "cs1", "start": 4.0, "end": 6.0, "scale": 1.08,
                                 "mode": "punch", "ramp_s": 0.0, "cut_step": True}]}
    edl = validate_edl(edl, 30.0).model_dump()
    ev = sfx_placement.visual_events(edl)
    times = [round(t, 2) for t, _label in ev]
    assert 0.0 not in times and 30.0 not in times      # there from the first frame to the last
    assert 12.0 in times                                # a mid-program entrance still counts
    assert 4.0 not in times and 6.0 not in times        # a concealed jump cut is an ordinary cut


@needs_browser
def test_lockup_reveals_match_the_page():
    cases = [(PAPER_ROWS, SPOKEN, {}),
             (PAPER_ROWS, None, {}),
             ([{"text": "Steve Jobs, 1983", "role": "sans", "size": "0.45", "at": "0"},
               {"text": "computer fonts were", "role": "serif", "size": "0.8", "at": ""},
               {"text": "*GARBAGE*", "role": "condensed", "size": "1.5", "at": "0.3"}], None, {})]
    for rows, reading, params in cases:
        reveals = caption_carry.lockup_reveals(_pb(rows, reading, **params))
        times = []
        for t in reveals:
            times += [max(0.0, t - 0.012), t + 0.012]
        st = asyncio.run(_states(_job(rows, reading, end=5.3, **params), times))
        for k, t in enumerate(reveals):
            before, after = st[2 * k][k], st[2 * k + 1][k]
            assert after["words"][0] == 1, (rows, reading, k, t)
            if t > 0.012:
                assert before["words"][0] == 0, (rows, reading, k, t)
