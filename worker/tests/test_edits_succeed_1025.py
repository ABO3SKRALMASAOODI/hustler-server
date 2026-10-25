"""25 Oct 2026: Studio requests are carried through to a result.

The owner: "you failed to fix the issues that most need fixing … all you've
been doing is setting a one-try policy, otherwise fail and refund the
credits." These tests replay the patterns of the real requests that were
held open or cut short (strategy-1025/FAILURES.md §5), never customer media:
the prompts' phrasing, the reviewers' finding lines and the EDL shapes.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_tools
import db as dbx
import failure_policy
import main as worker_main
import preview_critic
import quality_verifier
import request_intent
import request_scope
import story_critic
from schemas import default_edl


# ── The duration parser reads the PROGRAMME length, not the source's ──────

@pytest.mark.parametrize("prompt, low, high", (
    # 1105's real request 32493 parsed to 264.6-275.4 s (the source).
    ("Create a dynamic highlight video from this 4.5-minute talking-head "
     "video.\n\nThe main message of the video is: ... Target length: "
     "60–90 seconds.", 60, 90),
    ("Target length: 60–90 seconds.", 60, 90),
    ("Make a 30 second reel from this 10 minute video", 29.4, 30.6),
    ("Cut this 45 minute podcast into a highlight. Length should be about "
     "60 seconds.", 54, 66),
    ("Create a 60–90 sec highlight from this video.", 60, 90),
    ("create the best possible 30 second version of this video", 29.4, 30.6),
    ("also reduce the clip to 20sec", 19.6, 20.4),
    # 1918: "I don't want it 12 seconds. I want it 10 minutes" was missed.
    ("I don't want it 12 seconds. I want it 10 minutes", 588, 612),
    ("Use all 50 clips. Make it 10 minutes long, make it romantic", 588, 612),
    # 1912's 30-40 s ad stays 30-40 s; "clear within 8-10 seconds" is a
    # reveal, not the programme.
    ("Create a 30–40 second vertical 9:16 Instagram Story/Reel ad. The "
     "product must be clear within 8-10 seconds.", 30, 40),
))
def test_programme_length_not_source_length(prompt, low, high):
    target = quality_verifier.requested_duration_target(prompt)
    assert target is not None, prompt
    assert (round(target["min_s"], 1), round(target["max_s"], 1)) == \
        (low, high), prompt
    assert not target["advisory"]


@pytest.mark.parametrize("prompt", (
    "I have a 10 minute video, make shorts from it",
    "Make a trailer of this interview of 4 minutes",
    "Turn my 45 minute podcast into clips",
    "Make the intro 5 seconds long",
    "the intro duration is 5 seconds",
))
def test_source_and_element_lengths_are_not_programme_targets(prompt):
    assert quality_verifier.requested_duration_target(prompt) is None, prompt


def test_a_weak_duration_reading_advises_instead_of_forcing_a_rebuild():
    prompt = "a 60 second reel with captions"
    target = quality_verifier.requested_duration_target(prompt)
    assert target["advisory"] is True
    rows = quality_verifier._request_findings(default_edl(42), prompt)
    assert rows and not any(quality_verifier.is_blocking(r) for r in rows)


# ── Audio QC is scoped to what the customer asked for ─────────────────────

QUIET = ("the whole mix is quiet: -28.9 LUFS against the -14 social target "
         "— set_master_loudness fixes it in one call")
SILENT = ("the mix is essentially SILENT (integrated -100.0 LUFS) — every "
          "audio layer is muted or missing")


def test_strip_all_audio_request_is_not_blocked_on_quiet():
    # 1794, request 36072: "Strip all original audio. The final export
    # must have NO audio track." blocked on "-28.9 LUFS".
    edl = {"keep": [[0, 60]],
           "volume": [{"start": 0, "end": 60, "gain_db": -60}]}
    blocking, advisory = request_scope.split_audio_qc(
        [QUIET], edl, "Strip all original audio. The final export must "
        "have NO audio track.")
    assert blocking == [] and advisory == [QUIET]


def test_silent_by_design_ad_without_supplied_music_is_not_blocked():
    # 1912, request 39424: photos and muted clips, no music supplied.
    edl = {"keep": [], "inserts": [
        {"id": "i1", "kind": "image", "asset_key": "a/model.jpg"},
        {"id": "i2", "kind": "video", "asset_key": "a/train.mp4",
         "mute": True}]}
    blocking, advisory = request_scope.split_audio_qc(
        [SILENT], edl, "Create a 30–40 second ad using ONLY my photos")
    assert blocking == [] and advisory == [SILENT]


def test_lost_sound_still_blocks():
    # Music on the timeline and a silent mix: the sound was lost.
    edl = {"keep": [[0, 30]], "music": [{"id": "m1", "start": 0, "end": 30}]}
    assert request_scope.split_audio_qc(
        [SILENT], edl, "mute the original audio and add music")[0] == [SILENT]
    # An unmuted original with sound that measures silent: lost too.
    assert request_scope.split_audio_qc(
        [SILENT], {"keep": [[0, 30]]}, "make a reel")[0] == [SILENT]


def test_deliberately_lowered_volume_is_not_quiet():
    edl = {"keep": [[0, 30]],
           "volume": [{"start": 0, "end": 30, "gain_db": -14}]}
    assert request_scope.split_audio_qc([QUIET], edl, "lower it to 20%")[0] \
        == []


class _VolCtx:
    def __init__(self, edl, width=1080, height=1920):
        self._edl = {"version": 1, "json": edl}
        self.index = {"video": {"width": width, "height": height}}
        self.written = None

    def latest_edl(self):
        return self._edl

    def clamp(self, value):
        return round(float(value), 3)

    def write_edl(self, edl, desc):
        self.written = edl
        return f"EDL v1 -> v2: {desc}"


def test_whole_programme_volume_cut_survives_social_mastering():
    # 1799 asked six times: "REDUCE … TO 20%", each reply "-14.2 LUFS".
    ctx = _VolCtx({"keep": [[0.0, 30.0]], "frame": {"ratio": "9:16"}})
    res = agent_tools.set_volume(ctx, 0, 30, -14)
    assert ctx.written["master"] == {"loudness": "natural"}
    assert "MASTERING" in res
    # A section dip keeps the social master (the dip stays relative).
    ctx = _VolCtx({"keep": [[0.0, 30.0]], "frame": {"ratio": "9:16"}})
    agent_tools.set_volume(ctx, 10, 15, -10)
    assert not ctx.written.get("master")
    # 16:9 is not mastered by default: nothing to switch off.
    ctx = _VolCtx({"keep": [[0.0, 30.0]]}, width=1920, height=1080)
    agent_tools.set_volume(ctx, 0, 30, -14)
    assert not ctx.written.get("master")


# ── One repair per reviewer category ──────────────────────────────────────

CROP = ("independent visual review [crop] at 3.1s: the speaker's head is "
        "cut at the top edge. Repair: reframe")
CROP_AGAIN = ("independent visual review [crop] at 7.4s: the new framing "
              "crops the speaker's hands. Repair: widen")
CAPTION = ("independent visual review [caption_collision] at 2.0s: captions "
           "sit across the mouth. Repair: move them down")


def _record(version, visual, prior=None):
    return quality_verifier.build_verification_record(
        1, version, {}, default_edl(12), {},
        preview={"edl_version": version, "duration_s": 12,
                 "storage_key": "p.mp4"},
        visual_findings=visual, request_text="make a reel",
        prior_records=prior)


def test_a_category_that_recurs_after_its_repair_becomes_advisory():
    # 1769's Repair edit loop: every pass reframed the speaker and the
    # next critique flagged a new crop.
    first = _record(5, [CROP])
    assert first["status"] == "repair_required"
    second = _record(6, [CROP_AGAIN, CAPTION], {5: first})
    open_codes = [request_scope.finding_key(r)
                  for r in second["unresolved_findings"]]
    assert open_codes == ["visual:caption_collision"]
    assert any((r.get("evidence") or {}).get("recurring_after_repair")
               for r in second["advisories"])
    # The crop comes back a third time: still advisory, the version passes.
    third = _record(7, [CROP], {5: first, 6: second})
    assert third["status"] == "passed"
    assert third["unresolved_findings"] == []


def test_contract_and_safety_findings_are_never_demoted():
    rows = [quality_verifier._finding(
        "requested_duration_outside_target", "story", "too long"),
        quality_verifier._finding("corrupt_glyph", "text", "bad glyph")]
    prior = {3: {"unresolved_findings": rows}}
    assert request_scope.demote_recurring(rows, prior, 4) == rows


def test_handoff_trusts_the_record_over_raw_critic_reports():
    ctx = NS(latest_edl=lambda: {"version": 6, "json": default_edl(12)},
             last_preview={"edl_version": 6},
             verification_request="make a reel",
             last_visual_critic={"verdict": "repair", "findings": [{
                 "severity": "blocker", "category": "crop", "time_s": 7.4,
                 "evidence": "crop again", "repair": "widen",
                 "confidence": .95}]},
             last_taste=[], last_taste_version=6, last_audio_qc_findings=[],
             verification_records={6: {
                 "status": "passed", "unresolved_findings": [],
                 "advisories": [{"message": CROP_AGAIN}]}})
    quality = agent_loop._quality_handoff(ctx)
    assert quality["export_ready"] is True
    assert quality["quality_status"] == "passed"


# ── Reviewer categories the footage or the measurement disproves ──────────

def _finding(category, **kw):
    row = {"severity": "blocker", "category": category, "time_s": 1.9,
           "evidence": "x", "repair": "y", "confidence": .97}
    row.update(kw)
    return row


def test_a_still_based_black_frame_claim_does_not_block():
    # 39634: "black_frame at 1.9s" on the pillarbox of a vertical frame.
    # Every delivered render already passed the measured black-frame check.
    assert not preview_critic.blocks(_finding("black_frame"))
    assert preview_critic.blocks(_finding("caption_collision"))
    assert preview_critic.blocks(_finding("crop"))
    # Relevance of footage the editor chose still blocks once.
    assert preview_critic.blocks(_finding("narrative_relevance"))


def test_abrupt_open_on_the_creators_own_first_words_is_a_note():
    # 1924, requests 42489 and 43916: the Hungarian intro "ebben a mai
    # videóban a" held two runs.
    report = {"verdict": "repair", "findings": [{
        "severity": "major", "category": "abrupt_open", "program_s": 0.0,
        "target_id": "keep-1", "evidence": "ebben a mai videóban a",
        "repair": "start later", "confidence": .93}]}
    scoped = story_critic.scope_to_source(report, {"keep": [[0.0, 40.0]]})
    assert story_critic.repair_lines(scoped) == []
    assert scoped["verdict"] == "pass"
    # A cut that opens mid-source can be an editing defect: unchanged.
    later = story_critic.scope_to_source(report, {"keep": [[12.0, 40.0]]})
    assert story_critic.repair_lines(later)


def test_negated_list_is_not_a_transition_request():
    # 1794, request 36118.
    assert not quality_verifier._requested_transitions(
        "Combine these two fight clips into a single 30-second highlight "
        "clip. Do not change the color, add any transitions, add music, or "
        "alter the footage")
    assert quality_verifier._requested_transitions(
        "Use smooth transitions, no glitch transitions")


# ── The request contract ───────────────────────────────────────────────────

def test_no_subtitles_is_a_contract():
    # 1924: "not shorts but everything, in order, not jumbled, and no
    # subtitles" after a captioned, reordered cut.
    text = "not shorts but everything, in order, not jumbled, and no subtitles"
    edl = default_edl(30)
    edl["captions"] = {"mode": "from_transcript"}
    edl["keep"] = [[20.0, 30.0], [0.0, 10.0]]
    codes = {r["code"] for r in quality_verifier._request_findings(edl, text)}
    assert {"captions_against_request", "order_against_request"} <= codes
    edl["captions"] = None
    edl["keep"] = [[0.0, 10.0], [20.0, 30.0]]
    assert not quality_verifier._request_findings(edl, text)
    lines = request_scope.contract_lines(text)
    assert any("Captions/subtitles: OFF" in line for line in lines)
    assert any("chronological" in line for line in lines)


def test_use_all_clips_is_a_contract():
    text = "Use all 50 clips. Make it 10 minutes long"
    keys = [f"clips/{i}.mp4" for i in range(10)]
    edl = default_edl(595)
    edl["inserts"] = [{"id": "i1", "asset_key": keys[0], "duration_s": 5}]
    rows = quality_verifier._request_findings(edl, text, keys)
    assert "requested_clips_unused" in {r["code"] for r in rows}
    edl["inserts"] = [{"id": f"i{i}", "asset_key": k, "duration_s": 0.5}
                      for i, k in enumerate(keys)]
    assert "requested_clips_unused" not in {
        r["code"] for r in quality_verifier._request_findings(edl, text, keys)}


def test_long_form_brief_gets_the_long_form_contract():
    # 1918: "Make a 10 minute wedding video" got a 12-second montage.
    text = "Make a 10 minute wedding video"
    lines = request_scope.contract_lines(
        text, quality_verifier.requested_duration_target(text))
    assert any(line.startswith("LONG-FORM") for line in lines)
    assert any("about 600 s" in line for line in lines)
    assert not request_scope.contract_lines(
        "make a 30 second reel",
        quality_verifier.requested_duration_target("make a 30 second reel")
    )[1:]


def test_improve_it_keeps_the_customer_brief():
    assert request_intent.is_review_repair(
        "Improve it: keep what works and fix these review notes: x")
    assert request_intent.is_review_repair(
        "Fix the remaining quality issues before export: x")

    class Db:
        @staticmethod
        def run(fn, *args):
            assert fn is dbx.customer_request_before_repair
            assert args[2] == request_intent.REVIEW_REPAIR_PREFIXES
            return {"id": 4, "content": "Make it max 15 seconds"}

    message = {"id": 9, "content": "Improve it: keep what works and fix "
                                   "these review notes: crop at 59 s"}
    assert agent_loop._customer_verification_request(Db(), 7, message, {}) \
        == "Make it max 15 seconds"


def test_repair_brief_sql_excludes_every_generated_prefix():
    seen = {}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params

        def fetchone(self):
            return None

    conn = NS(cursor=lambda: Cur())
    dbx.customer_request_before_repair(
        conn, 7, 9, request_intent.REVIEW_REPAIR_PREFIXES)
    assert "LIKE ANY" in seen["sql"]
    assert seen["params"][2] == [p + "%" for p in
                                 request_intent.REVIEW_REPAIR_PREFIXES]
    dbx.customer_request_before_repair(conn, 7, 9, "Fix it:")
    assert seen["params"][2] == ["Fix it:%"]


# ── Infrastructure no longer ends a Studio turn ────────────────────────────

class _Db:
    def __init__(self, receipt=None, deferred=True):
        self.calls = []
        self.receipt = receipt
        self.deferred = deferred

    def run(self, fn, *args, **kw):
        self.calls.append((fn, args, kw))
        if fn is dbx.get_job:
            return {"payload": ({"mutation_receipt": self.receipt}
                                if self.receipt else {})}
        if fn is dbx.defer_unlaunched_cloudflare_busy:
            return self.deferred
        if fn is dbx.enqueue_agent_continuation:
            return 777
        return None


def _agent_job(**payload):
    return {"id": 50, "type": "agent_turn", "project_id": 9, "user_id": 8,
            "attempts": 1, "total_claims": 2,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=12),
            "payload": dict({"message_id": 3}, **payload)}


def test_agent_turn_without_a_write_is_requeued_quietly(monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _Db()
    err = RuntimeError("Cloudflare lost the executor during this agent_turn "
                       "(container exited)")
    decision = failure_policy.decision_for(err, "agent_turn")
    assert worker_main._quiet_infra_retry(db, _agent_job(), err, decision, 2)
    assert any(fn is dbx.defer_unlaunched_cloudflare_busy
               for fn, _a, _k in db.calls)


def test_agent_turn_with_a_committed_write_continues_from_it(monkeypatch):
    finished = []
    monkeypatch.setattr(worker_main.job_completion, "finalize_success",
                        lambda _db, job, result, claim: finished.append(
                            result) or True)
    db = _Db(receipt={"committed": True, "after_version": 12})
    err = RuntimeError("Error: Container sidecar is shutting down")
    decision = failure_policy.decision_for(err, "agent_turn")
    assert worker_main._quiet_infra_retry(db, _agent_job(), err, decision, 2)
    enqueue = [a for fn, a, _k in db.calls
               if fn is dbx.enqueue_agent_continuation]
    assert enqueue and enqueue[0][4]["death_resume"] == 1
    assert enqueue[0][5:] == (50, 2)          # fenced on this lease
    assert finished[0]["status"] == "continued"
    assert finished[0]["billable"] is False
    assert not any(fn is dbx.defer_unlaunched_cloudflare_busy
                   for fn, _a, _k in db.calls)


def test_mcp_write_that_landed_is_not_replayed(monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _Db(receipt={"committed": True})
    job = dict(_agent_job(), type="mcp_tool",
               created_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    err = RuntimeError("Cloudflare Container shard is busy")
    assert not worker_main._quiet_infra_retry(
        db, job, err, failure_policy.decision_for(err, "mcp_tool"), 2)
    db = _Db()
    assert worker_main._quiet_infra_retry(
        db, job, err, failure_policy.decision_for(err, "mcp_tool"), 2)


def test_a_customer_edit_error_is_still_not_retried(monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _Db()
    err = RuntimeError("inserts[2].at_output_s 4.1 is not on a keep-segment "
                       "boundary")
    assert not worker_main._quiet_infra_retry(
        db, _agent_job(), err, failure_policy.decision_for(err, "agent_turn"),
        2)
    assert db.calls == []


# ── A request keeps working while its contract moves ──────────────────────

def test_contract_progress_keeps_a_request_working(monkeypatch):
    from test_quality_gates_advisory import _advisory_loop_ctx, _stuck_cont
    ctx, db, posted, checkpoint = _advisory_loop_ctx(monkeypatch)
    ctx.verification_request = "Make a 45 second reel"
    # Same reviewer set as the last slice, but the programme went from 5 s
    # to 20 s toward the 45 s ask: progress, so another slice runs.
    cont = _stuck_cont(ctx, contract_signature=[
        ["requested_duration_outside_target", 5, 0]])
    result = agent_loop._run_loop(
        ctx, db, {"id": 4, "project_id": 9, "user_id": 8, "payload": {}},
        2, {"id": 1, "content": ctx.user_message}, _cont=cont)
    assert result["status"] == "continued"
    assert checkpoint["contract_signature"] == [
        ["requested_duration_outside_target", 20, 0]]
    assert posted == []


def test_ambiguous_remote_ownership_is_never_replayed_for_a_turn(monkeypatch):
    monkeypatch.setattr(worker_main.DRAINING, "wait", lambda s: None)
    db = _Db()
    err = dbx.RemoteExecutionUnconfirmed("ownership of the remote run is unknown")
    decision = failure_policy.decision_for(err, "agent_turn")
    assert decision.kind == "remote_ownership_unconfirmed"
    assert not worker_main._quiet_infra_retry(db, _agent_job(), err, decision, 2)
    assert db.calls == []


def test_a_mastered_mix_far_off_target_blocks_unless_intended():
    line = ("the mix measures -31.0 LUFS even though it is mastered to -14 "
            "— most of the program is silence or a layer is muted")
    assert request_scope.split_audio_qc([line], {"keep": [[0, 30]]},
                                        "make a reel")[0] == [line]
    lowered = {"keep": [[0, 30]],
               "volume": [{"start": 0, "end": 30, "gain_db": -14}]}
    assert request_scope.split_audio_qc([line], lowered, "quieter")[0] == []
    natural = ("the mix is -22.0 LUFS against the -14 social target, at its "
               "natural level by request — only set_master_loudness(enabled="
               "true) if the user wants it mastered after all")
    assert request_scope.split_audio_qc([natural], {"keep": [[0, 30]]},
                                        "make a reel")[0] == []
