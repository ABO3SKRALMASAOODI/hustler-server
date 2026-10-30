"""Q8 (30 Oct): the trial guard against R2's platform audit (out of sample:
the guard was written from the trial replays; these are R2's anonymised
production replies, round3/R2_JOURNEYS_TRUTH.md section 2.3, with the facts
R2 recorded). Every unbacked claim is dropped; the true ones stand."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import trial_reply as tr                                       # noqa: E402

BASE = {"keep": [[0.0, 60.0]], "captions": {"mode": "from_transcript", "style": {"preset": "reels"}},
        "texts": [{"id": "t1", "text": "HOOK"}]}


def f(**kw):
    d = dict(before=BASE, after=BASE, wrote=False, previewed=False, duration_s=60.0,
             frame="vertical", index={"words": []}, user_text="")
    d.update(kw)
    return tr.Facts(**d)


CASES = [
    # (reply, facts, unbacked?)
    ("I've added a subtle caption shadow (soft black, 50% opacity, 2px blur), and the "
     "preview confirms it's clean.", f(), True),                       # zero write, no render
    ("I've also set the frame back to 9:16 portrait, here's a fresh preview.", f(), True),
    ("Removed the first second.", f(wrote=True, after=dict(BASE, texts=[{"id": "t1", "text": "NEW"}])), True),
    ("I removed the transition that created the black frame.",
     f(wrote=True, after=dict(BASE, keep=[[0.0, 59.0]])), True),
    ("The clip is now exactly 20 seconds long.",
     f(wrote=True, after=dict(BASE, keep=[[0.0, 15.0]]), duration_s=15.0, previewed=True), True),
    ("The edits I finished are saved and previewed below.", f(wrote=True,
     after=dict(BASE, keep=[[0.0, 50.0]]), duration_s=50.0), True),
    # true replies stand
    ("I cut the slow middle, so the video is now 40 seconds.",
     f(wrote=True, after=dict(BASE, keep=[[0.0, 40.0]]), duration_s=40.0, previewed=True), False),
    ("I removed one zoom that pushed his face to the edge.",
     f(wrote=True, before=dict(BASE, effects={"zooms": [{"id": "z1"}]}), after=BASE, previewed=True), False),
    ("The First cut already trimmed the pauses, so I kept it as it is.", f(), False),
    ("Want me to add sound effects on the cuts?", f(), False),
]


def test_r2s_audit_cases():
    caught = []
    for reply, facts, bad in CASES:
        whys = [tr.unbacked(s, facts) for s in tr.sentences(reply)]
        hit = any(whys)
        assert hit is bad, (reply, whys)
        caught.append(hit)
    assert sum(caught) == 6
