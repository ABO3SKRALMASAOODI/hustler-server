"""Rubric v3 (owner, 6 Nov 2026): "the same upload, nothing changed, and the
rater gave the 6 ... I would give them maximum 1 or 2", and "give the rater the
capability of actually saying incredible edits, not only 10 out of 10".

The ceilings live in the write path: a score the signal disproves cannot reach
the database, whoever writes it. No database: scripted cursors.
"""
import json
import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ops_loop import journeys, store  # noqa: E402
from test_outcome_loop import NOW, T0, Cur, _session  # noqa: E402


def dims(n=2):
    return {k: n for k in store.CRAFT_DIMENSIONS + store.GATE_DIMENSIONS}


SAME = {"class": "same_upload", "mode": "paired", "unchanged_pct": 99.2,
        "shots_added": 0, "overlay_pct": 0.8,
        "proof": "The delivered video is essentially the upload: 99% ..."}
EDITED = {"class": "edited", "mode": "paired", "unchanged_pct": 40.0,
          "shots_added": 9, "overlay_pct": 38.0, "proof": "A real change"}
NO_PICTURES = {"class": "edited", "mode": "paired", "unchanged_pct": 60.0,
               "shots_added": 0, "overlay_pct": 14.0, "proof": "Cuts only"}


def rating(**kw):
    out = {"rubric": "v3", "score_exact": 2, "tier": "standard",
           "dimensions": dims(), "edit_delta": SAME}
    out.update(kw)
    return out


def test_the_same_upload_cannot_score_above_two():
    with pytest.raises(store.StoreError, match="above the ceiling 2.0"):
        store.check_rating(rating(score_exact=6), "delivered_poor", 6)
    got = store.check_rating(rating(score_exact=1.5), "delivered_poor", 1)
    assert got["score_exact"] == 1.5
    assert got["nothing_edited"].startswith("Nothing really edited: 99%")


@pytest.mark.parametrize("cls,cap", [("trim_only", 3.0), ("light_touch", 4.0)])
def test_thin_edits_have_their_ceilings(cls, cap):
    delta = dict(SAME, **{"class": cls})
    with pytest.raises(store.StoreError, match=f"ceiling {cap}"):
        store.check_rating(rating(score_exact=cap + 1, edit_delta=delta),
                           "delivered_poor", int(cap + 1))
    assert store.check_rating(rating(score_exact=cap, edit_delta=delta),
                              "delivered_poor", int(cap))["tier"] == "standard"


def test_no_new_pictures_stops_at_six():
    with pytest.raises(store.StoreError, match="ceiling 6.0"):
        store.check_rating(rating(score_exact=7, edit_delta=NO_PICTURES),
                           "delivered_good", 7)
    store.check_rating(rating(score_exact=6, edit_delta=NO_PICTURES),
                       "delivered_poor", 6)


def test_a_declared_defect_sets_its_ceiling():
    bad = [{"id": "off_centre_card", "at_s": 12.3, "note": "the card sits left"}]
    with pytest.raises(store.StoreError, match="ceiling 8.0"):
        store.check_rating(rating(score_exact=9, edit_delta=EDITED, defects=bad),
                           "delivered_good", 9)
    with pytest.raises(store.StoreError, match="defects"):
        store.check_rating(rating(score_exact=2, defects=[{"id": "made_up"}]),
                           "delivered_poor", 2)


def test_alien_needs_its_proof():
    tens = {k: 10 for k in store.CRAFT_DIMENSIONS}
    tens.update(fidelity=None, defects=10)
    base = dict(score_exact=10, edit_delta=EDITED, dimensions=tens, tier="alien")
    with pytest.raises(store.StoreError, match="alien_what"):
        store.check_rating(rating(**base), "delivered_good", 10)
    with pytest.raises(store.StoreError, match="alien_nearest"):
        store.check_rating(rating(alien_what="A transition cut in chunks", **base),
                           "delivered_good", 10)
    with pytest.raises(store.StoreError, match="sits above 10"):
        store.check_rating(rating(**dict(base, score_exact=9),
                                  alien_what="x", alien_nearest="DfOh6WivITF"),
                           "delivered_good", 9)
    low = dict(tens, sound=9)
    with pytest.raises(store.StoreError, match="every craft dimension"):
        store.check_rating(rating(**dict(base, dimensions=low),
                                  alien_what="x", alien_nearest="DfOh6WivITF"),
                           "delivered_good", 10)
    got = store.check_rating(rating(alien_what="A transition cut in chunks",
                                    alien_nearest="DfOh6WivITF", **base),
                             "delivered_good", 10)
    assert got["tier"] == "alien"


def test_half_points_round_down_and_never_up():
    with pytest.raises(store.StoreError, match="never rounded up"):
        store.check_rating(rating(score_exact=7.5, edit_delta=EDITED),
                           "delivered_good", 8)
    with pytest.raises(store.StoreError, match="half points"):
        store.check_rating(rating(score_exact=7.3, edit_delta=EDITED),
                           "delivered_good", 7)
    assert store.check_rating(rating(score_exact=7.5, edit_delta=EDITED),
                              "delivered_good", 7)["score_exact"] == 7.5


def test_a_delivered_result_needs_its_numbers_a_failure_does_not():
    with pytest.raises(store.StoreError, match="edit_delta"):
        store.check_rating(rating(edit_delta=None), "delivered_poor", 2)
    got = store.check_rating(rating(edit_delta=None, score_exact=1,
                                    dimensions=None), "not_delivered", 1)
    assert got["edit_delta"] is None and got["nothing_edited"] is None


def test_only_opus_rates():
    s = _session()
    ev = [{"kind": "frame", "caption": "x",
           "storage_key": f"ops/evidence/{s['key']}/a.jpg"}]
    journey = {"asks": [{"text": "cut", "status": "done"}], "why_short": "x"}
    with pytest.raises(store.StoreError, match="Opus"):
        store.write_verdict(Cur([None]), s, "delivered_poor", 2, "x", ev,
                            root_cause="r", journey=journey, rating=rating(),
                            reviewer="claude-sonnet-5")


def test_a_replaced_score_stays_in_the_evidence_and_the_signals():
    s = _session()
    key = s["key"]
    old = {"verdict": "delivered_good", "score": 6, "reviewer": "claude-opus",
           "reviewed_at": NOW, "signals": {"why_short": "Plain but as asked."},
           "evidence": [{"kind": "contact_sheet", "caption": "Delivered v2",
                         "storage_key": f"ops/evidence/{key}/old.jpg"}]}
    cur = Cur([old, {"id": 3, "review_count": 2}])
    ev = [{"kind": "contact_sheet", "caption": "Board beside the anchors",
           "storage_key": f"ops/evidence/{key}/board.jpg"}]
    store.write_verdict(cur, s, "delivered_poor", 2, "nothing was edited", ev,
                        root_cause="the agent did nothing",
                        journey={"asks": [{"text": "cut", "status": "not_done"}],
                                 "why_short": "Nothing really edited."},
                        rating=rating(reviewer="x"))
    _, p = cur.sql[-1]
    evidence, sig = json.loads(p["evidence"]), json.loads(p["signals"])
    assert len(evidence) == 2
    assert evidence[1]["caption"].startswith("Old score 6/10 (delivered_good, rubric v2")
    assert evidence[1]["storage_key"].endswith("old.jpg")
    assert sig["previous_scores"][0]["score"] == 6
    assert sig["previous_scores"][0]["rubric"] == "v2"
    assert sig["edit_delta"]["class"] == "same_upload"
    assert sig["nothing_edited"].startswith("Nothing really edited")


def test_the_journey_card_shows_what_was_measured():
    sig = {"journey_format": 2, "why_short": "Nothing really edited.",
           "asks": [{"text": "cut", "status": "not_done", "note": None}],
           "rubric": "v3", "score_exact": 1.5, "tier": "standard",
           "edit_delta": SAME, "nothing_edited": "Nothing really edited: 99%...",
           "previous_scores": [{"score": 6, "rubric": "v2"}]}
    card = journeys._review_card({
        "id": 3, "session_key": "k", "verdict": "delivered_poor", "score": 1,
        "reasoning": "r", "root_cause": "c", "request_summary": "s",
        "evidence": [], "reviewer": "claude-opus", "review_count": 2,
        "reviewed_at": NOW, "tickets": [], "signals": sig})
    assert card["rubric"] == "v3" and card["score_exact"] == 1.5
    assert card["edit_delta"]["unchanged_pct"] == 99.2
    assert card["nothing_edited"].startswith("Nothing really edited")
    assert card["previous_score"]["score"] == 6
