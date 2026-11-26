"""Tools and turns, 26 Nov 2026 (session reviewer tickets).

#1841  cut_range on short ranges (a repeated word: "de de", "con con") came
       back NO CHANGE while re-reporting an unrelated edge repair; the agent
       looped to the no-progress stop and the condense kept its repetitions.

Run:  python -m pytest tests/test_ec2_tools_1126.py -q     (from worker/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import audit                                                  # noqa: E402
import sentence_snap                                          # noqa: E402
import word_edges                                             # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402


def _w(text, t0, t1):
    return {"w": text, "t0": t0, "t1": t1}


# Project 6307's own words (Spanish talk), three-decimal ASR times.
WORDS = [
    _w("y", 66.80, 67.30), _w("requiere", 67.40, 68.12),
    _w("presupuestos", 68.28, 68.84), _w("y", 68.84, 69.08),
    _w("mano", 69.08, 69.32), _w("de", 69.32, 69.48), _w("obra", 69.48, 69.72),
    _w("de", 69.72, 70.28), _w("de", 70.44, 71.08),
    _w("diferentes", 71.08, 71.56), _w("áreas.", 71.60, 72.10),
    _w("seguro", 73.21, 73.77), _w("para", 73.775, 74.015),
    _w("las", 74.015, 74.175), _w("personas", 74.175, 74.495),
    _w("con", 74.495, 74.815), _w("con", 74.815, 75.215),
    _w("discapacidad.", 75.215, 75.775),
]


class _Ctx:
    def __init__(self, keep, dur=90.0, shots=None, trial=True):
        self.project_id = 1
        self.duration = dur
        self.has_main_video = True
        self.workdir = None
        self.index = {"video": {"duration": dur, "width": 1920,
                                "height": 1080, "fps": 25.0},
                      "shots": shots or [], "words": list(WORDS),
                      "sentences": [], "silences": []}
        self.written = []
        edl = default_edl(dur)
        edl["keep"] = keep
        self._edl = validate_edl(edl, dur).model_dump()
        self.claude_trial = {"lane": 1} if trial else None
        self.claude_paid = None
        self._executing_tool = self._editor_tool = "cut_range"

    def cut_sound_source(self):
        return None

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def _removed(before, after):
    return agent_tools.output_duration(before) - agent_tools.output_duration(after)


def test_a_repeated_word_cut_at_its_shown_times_lands():
    """'con con' (74.495-74.815 shown as 74.50-74.81): the edges sat 5 ms
    inside the word on both sides and the whole-word pass restored it."""
    before = [[60.0, 80.0]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 74.50, 74.81, snap_to_words=True)
    assert res.startswith("EDL v"), res
    keep = ctx._edl["keep"]
    assert len(keep) == 2
    assert abs(keep[0][1] - 74.495) < 0.002 and abs(keep[1][0] - 74.815) < 0.002
    assert abs(_removed(before, keep) - 0.32) < 0.01
    assert "Left the word 'con' out whole" in res


def test_an_in_sentence_stutter_cut_is_not_snapped_shut():
    """'obra de de diferentes': the sentence snap moved both edges of the
    cut to the same clause gap, so the cut closed and nothing was removed."""
    before = [[60.0, 80.0]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    assert res.startswith("EDL v"), res
    keep = ctx._edl["keep"]
    # the first 'de' (69.72-70.28) is out, the second (70.44) kept whole
    assert keep[0][1] == 69.72 and 70.28 <= keep[1][0] <= 70.44
    assert "SENTENCE SNAP" not in res


def test_sentence_snap_keeps_a_trim_inside_one_sentence():
    prev = [[60.0, 80.0]]
    keep, notes = sentence_snap.snap_keep([[60.0, 69.72], [70.4, 80.0]],
                                          WORDS, 90.0, prev_keep=prev)
    assert keep == [[60.0, 69.72], [70.4, 80.0]] and notes == []


def test_a_sentence_snap_never_closes_a_cut():
    # a cut across the clause boundary after 'áreas.' still snaps, but the
    # two pieces never meet
    prev = [[60.0, 80.0]]
    keep, _notes = sentence_snap.snap_keep([[60.0, 71.3], [73.5, 80.0]],
                                           WORDS, 90.0, prev_keep=prev)
    assert len(keep) == 2 and keep[0][1] < keep[1][0]


def test_a_cut_leaves_every_other_edge_exactly_as_saved():
    """Rounding every edge rewrote word-exact edges far from the cut
    (511.525 -> 511.52) and each write re-placed and re-reported them."""
    before = [[60.0, 74.495], [74.815, 80.0], [81.0, 85.525]]
    ctx = _Ctx(before)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    keep = ctx._edl["keep"]
    assert [74.815, 80.0] in keep and [81.0, 85.525] in keep
    assert keep[1][1] == 74.495
    assert "74.495" not in res and "85.525" not in res
    assert audit.subtract_spans([[1.0, 5.525]], [[2.0, 3.004]]) == \
        [(1.0, 2.0), (3.0, 5.525)]


def test_a_saved_edge_is_not_pulled_back_onto_a_shot_cut():
    """The write boundary moved the end 1040.22 -> 1040.26 (sound still
    running); shot hygiene moved it back on every write, so every cut
    re-reported that stale repair."""
    shots = [{"id": 1, "start": 0.0, "end": 85.24},
             {"id": 2, "start": 85.24, "end": 90.0}]
    before = [[60.0, 85.26]]
    ctx = _Ctx(before, shots=shots)
    res = agent_tools.cut_range(ctx, 69.72, 70.4, snap_to_words=True)
    assert ctx._edl["keep"][-1][1] == 85.26
    assert "SHOT-CUT HYGIENE" not in res
    # a NEW edge on the cut is still moved off it
    keep, notes = agent_tools._snap_keep_to_shots(
        [[60.0, 85.26]], ctx.index, WORDS, prev_keep=[[60.0, 80.0]])
    assert keep[0][1] < 85.24 and notes


def test_whole_word_snap_still_keeps_a_word_an_edge_really_cuts():
    spans, moves = word_edges.whole([[60.0, 74.60]], WORDS, 90.0,
                                    previous=[[60.0, 80.0]])
    assert spans[0][1] >= 74.815 and "Kept the whole word 'con'" in moves[0]
