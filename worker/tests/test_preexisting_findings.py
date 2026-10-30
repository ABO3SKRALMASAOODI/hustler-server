"""One preview per typical Claude-lane request (31 Oct 2026).

Production, project 5270: "make the captions bigger" (turn 126653) came back
from its first preview with the three picture-QC notes its First cut (v15)
already had; the editor repaired them unasked and rendered a second preview.
Those notes are the request's starting baseline: advisory, never blocking.
Anything the request changed or asked about still blocks.
"""
import types

import agent_tools
import quality_verifier
import request_scope

STALL = ("STALL BEFORE END CARD 21.09-21.49s: 0.40s of a held picture where "
         "the programme hands over to the end card — the footage itself does "
         "not move there")
FACE_MG1 = ("GRAPHIC ON THE FACE 0.6-2.5s: 'mg1' inks 43% of the speaker's "
            "eyes (its measured ink over the face measured on the source "
            "frame)")
FACE_MG3 = ("GRAPHIC ON THE FACE 20.7-21.2s: 'mg3' inks 51% of the speaker's "
            "mouth")
FACE_MG4 = ("GRAPHIC ON THE FACE 5.0-6.0s: 'mg4' inks 40% of the speaker's "
            "eyes")


def _row(message):
    return {"code": "review_finding", "department": "visual_review",
            "message": "picture QC: " + message, "severity": "error",
            "evidence": {}}


V15 = {"unresolved_findings": [
    _row("STALL BEFORE END CARD 20.85-21.49s: 0.63s of a held picture where "
         "the programme hands over"),
    _row("GRAPHIC ON THE FACE 0.6-2.5s: 'mg1' inks 45% of the speaker's eyes"),
    _row(FACE_MG3)]}

EDL = {"keep": [[0, 22]], "motion": [
    {"id": "mg1", "template": "hook_title", "params": {"size": 1.3}},
    {"id": "mg3", "template": "stat_card", "params": {}}],
    "captions": {"mode": "from_transcript", "scale": 1.6}}


def _bigger_captions():
    import copy
    edl = copy.deepcopy(EDL)
    edl["captions"]["scale"] = 2.1
    return edl


def test_the_first_cuts_own_notes_are_advisory_on_a_captions_request():
    keys = request_scope.preexisting_keys({"record": V15})
    after = _bigger_captions()
    for line in (STALL, FACE_MG1, FACE_MG3):
        assert request_scope.is_preexisting(
            "picture QC: " + line, keys, EDL, after,
            "make the captions bigger"), line


def test_a_new_element_on_the_face_still_blocks():
    keys = request_scope.preexisting_keys(V15)
    assert not request_scope.is_preexisting(
        "picture QC: " + FACE_MG4, keys, EDL, _bigger_captions(),
        "make the captions bigger")


def test_an_element_this_request_changed_still_blocks():
    import copy
    keys = request_scope.preexisting_keys(V15)
    after = copy.deepcopy(EDL)
    after["motion"][0]["params"]["size"] = 1.5      # the title, made bigger
    assert not request_scope.is_preexisting(
        "picture QC: " + FACE_MG1, keys, EDL, after,
        "make the opening title a little bigger")
    # the untouched stat card's note is still the baseline
    assert request_scope.is_preexisting(
        "picture QC: " + FACE_MG3, keys, EDL, after,
        "make the opening title a little bigger")


def test_a_finding_the_request_asks_about_still_blocks():
    keys = request_scope.preexisting_keys(V15)
    assert not request_scope.is_preexisting(
        "picture QC: " + FACE_MG1, keys, EDL, EDL,
        "the title covers my face, fix it")
    assert not request_scope.is_preexisting(
        "picture QC: " + STALL, keys, EDL, EDL,
        "the ending freezes before the end card")


def test_no_baseline_record_demotes_nothing():
    assert request_scope.preexisting_keys(None) == {}
    assert not request_scope.is_preexisting(
        "picture QC: " + STALL, {}, EDL, EDL, "")
    rows = [_row(STALL)]
    assert request_scope.demote_preexisting(rows, {}) == rows


def test_deterministic_findings_are_never_demoted():
    keys = {"line:#": [frozenset()]}
    row = {"code": "complete_preview_missing", "message": "x",
           "severity": "error"}
    assert request_scope.demote_preexisting([row], keys) == [row]


def test_the_verification_record_holds_only_this_requests_findings():
    keys = request_scope.preexisting_keys(V15)
    record = quality_verifier.build_verification_record(
        5270, 16, {}, _bigger_captions(), {},
        preview={"edl_version": 16, "duration_s": 22.3,
                 "caption_pages": [1]},
        visual_findings=["picture QC: " + STALL, "picture QC: " + FACE_MG1,
                         "picture QC: " + FACE_MG4],
        request_text="make the captions bigger",
        baseline_keys=keys, start_edl=EDL)
    unresolved = [r["message"] for r in record["unresolved_findings"]]
    assert unresolved == ["picture QC: " + FACE_MG4]
    demoted = [r for r in record["findings"]
               if (r.get("evidence") or {}).get("preexisting")]
    assert len(demoted) == 2
    # a later version of the same request keeps them advisory
    later = request_scope.preexisting_keys(record)
    assert "picture:graphic on the face" in later


def test_without_baseline_keys_the_record_is_unchanged():
    record = quality_verifier.build_verification_record(
        5270, 16, {}, _bigger_captions(), {},
        preview={"edl_version": 16, "duration_s": 22.3,
                 "caption_pages": [1]},
        visual_findings=["picture QC: " + STALL],
        request_text="make the captions bigger")
    assert [r["message"] for r in record["unresolved_findings"]] == [
        "picture QC: " + STALL]


class _Db:
    def __init__(self, record):
        self.record = record
        self.calls = 0

    def run(self, fn, *args):
        self.calls += 1
        assert fn.__name__ == "latest_verification_record_upto"
        return self.record


def _ctx(**kw):
    ctx = types.SimpleNamespace(
        project_id=5270, claude_trial=None, claude_paid=None,
        turn_start_edl={"version": 15, "json": EDL},
        job={"id": 1, "payload": {}}, user_message="make the captions bigger",
        editing_metrics={}, metrics={})
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_grandfathered_lanes_keep_every_finding():
    ctx = _ctx(db=_Db({"record": V15}))
    assert agent_tools._baseline_keys(ctx) == {}
    keep, known = agent_tools._split_preexisting(
        ctx, {"json": _bigger_captions()}, [STALL, FACE_MG1], "picture QC: ")
    assert keep == [STALL, FACE_MG1] and known == []
    assert ctx.db.calls == 0


def test_the_trial_and_new_subscriber_lanes_split_the_baseline_out():
    for lane in ({"claude_trial": object()}, {"claude_paid": {"m": 1}}):
        ctx = _ctx(db=_Db({"record": V15}), **lane)
        keep, known = agent_tools._split_preexisting(
            ctx, {"json": _bigger_captions()},
            [STALL, FACE_MG1, FACE_MG4], "picture QC: ")
        assert keep == [FACE_MG4]
        assert known == [STALL, FACE_MG1]
        agent_tools._split_preexisting(
            ctx, {"json": _bigger_captions()}, [STALL], "picture QC: ")
        assert ctx.db.calls == 1           # one read per turn


def test_a_first_cut_turn_has_no_baseline():
    ctx = _ctx(db=_Db({"record": V15}), claude_trial=object(),
               job={"id": 1, "payload": {"first_cut": True}})
    assert agent_tools._baseline_keys(ctx) == {}
