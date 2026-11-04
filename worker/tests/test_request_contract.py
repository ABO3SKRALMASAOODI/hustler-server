"""Request preservation and completion cannot contradict the same brief."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

import agent_loop
import request_contract as rc
import scope_guard
import operator_recovery
from test_turn_outcomes import _ctx, _changed


def context():
    before = {"keep": [[0.0, 16.0]], "frame": {"ratio": "9:16"},
              "captions": None, "overlays": [], "effects": {}}
    ctx = _changed(_ctx(versions_written=[2], rendered_versions={2},
                       last_preview={"edl_version": 2}, write_attempts=1))
    ctx.request_contract_required = True
    ctx.verification_request = "Preserve voice, subtitles and branding. Add animated typography."
    ctx.user_message = ctx.verification_request
    ctx.turn_start_edl = {"version": 1, "json": deepcopy(before)}
    ctx.latest_edl = lambda: {"version": 2, "json": before}
    ctx.semantic_contract = {
        "schema_version": 1, "request_digest": rc.digest(ctx), "mode": "edit", "output_count": 1,
        "timing_policy": "preserve", "music_requested": False,
        "requirements": [
            {"id": "subtitles", "action": "preserve", "lanes": ["captions"],
             "description": "Preserve original subtitles without duplication.", "quote": "Preserve voice, subtitles and branding."},
            {"id": "type", "action": "change", "lanes": [],
             "description": "Add animated typography.", "quote": "Add animated typography."}],
    }
    return ctx


def test_preservation_is_not_an_instruction_to_add_captions():
    ctx = context()
    before = ctx.latest_edl()["json"]
    after = deepcopy(before)
    after["captions"] = {"mode": "from_transcript"}
    violations = scope_guard.preservation_violations(before, after,
        ctx.user_message, protected=rc.protected_lanes(ctx))
    assert violations == [scope_guard.LANE_LABELS["captions"]]
    after = deepcopy(before)
    after["effects"]["grade_custom"] = {"temperature": .08}
    assert scope_guard.preservation_violations(before, after,
        protected=rc.protected_lanes(ctx)) == []


def test_completion_receives_the_same_recovery_scope_as_compilation(monkeypatch):
    ctx = context()
    ctx.operator_instruction = "Correct only the selected music gain; preserve the accepted picture."
    captured = []
    def assess(_ctx, _name, _schema, _system, request, **kwargs):
        captured.append(request)
        return {"requirements": [{"id": row["id"], "status": "unknown",
                 "reason": "Awaiting rendered proof", "evidence": [], "blocker_kind": "none"}
                for row in ctx.semantic_contract["requirements"]],
                "completed": "", "remaining": "Awaiting rendered proof"}
    monkeypatch.setattr(rc, "_call", assess)
    rc.assess(ctx, preflight=True)
    assert captured[0]["internal_recovery"] == ctx.operator_instruction
    assert captured[0]["customer_request"] == ctx.verification_request
    assert captured[0]["contract"] == ctx.semantic_contract


def test_focused_grade_cannot_silently_shorten_the_video():
    ctx = context()
    after = deepcopy(ctx.latest_edl()["json"])
    after["keep"] = [[0.0, 12.0]]
    assert "picture timing" in scope_guard.preservation_violations(
        ctx.latest_edl()["json"], after, protected=rc.protected_lanes(ctx))[0]


def test_explicit_channel_exclusion_allows_removal_but_not_recreation():
    ctx = context()
    ctx.semantic_contract["requirements"] = [{"id": "no_caps", "action": "avoid", "lanes": ["captions"]}]
    assert rc.forbidden_additions(ctx, {"captions": None}) == []
    assert rc.forbidden_additions(ctx, {"captions": {"mode": "from_transcript"}}) == ["captions"]


@pytest.mark.parametrize("complete,expected", [(False, "partial"), (True, "fulfilled")])
def test_completion_uses_delivered_requirements_not_render_or_intermediate_errors(monkeypatch, complete, expected):
    ctx = context()
    ctx.turn_tool_outcomes = [{"tool": "find_footage", "kind": "failed"}]
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2,
                              "complete": complete, "requirements": []}
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda *_: {"export_ready": True})
    assert agent_loop._turn_completion(ctx)[0] == expected


def test_completed_review_cannot_follow_a_later_timeline_version():
    ctx = context()
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2, "complete": True}
    assert rc.completion(ctx)
    ctx.latest_edl = lambda: {"version": 3, "json": {}}
    assert not rc.completion(ctx)
    ctx.verification_request = "Now replace the soundtrack."
    with pytest.raises(ValueError, match="current request"):
        rc.protected_lanes(ctx)


@pytest.mark.parametrize("complete,expected", [(True, "fulfilled"), (False, "blocked")])
def test_photos_only_conditional_answer_is_judged_as_an_answer(complete, expected):
    ctx = context()
    ctx.has_main_video = False
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.write_attempts = 0
    ctx.turn_tool_outcomes = []
    ctx.semantic_contract["mode"] = "inspect"
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2,
                              "complete": complete, "requirements": []}
    assert agent_loop._turn_completion(ctx)[0] == expected


@pytest.mark.parametrize("pointer", ["/after/invented", ["after", "invented"]])
def test_invented_evidence_cannot_establish_completion(monkeypatch, pointer):
    ctx = context()
    monkeypatch.setattr(rc, "_call", lambda *args, **kwargs: {
        "requirements": [{"id": r["id"], "status": "satisfied", "reason": "Done",
                          "evidence": [pointer]} for r in ctx.semantic_contract["requirements"]],
        "completed": "", "remaining": ""})
    result = rc.assess(ctx)
    assert not result["complete"]
    assert {r["status"] for r in result["requirements"]} == {"unknown"}
    assert result["remaining"]


def test_model_refs_preserve_slashes_tildes_and_nested_asset_evidence(monkeypatch):
    ctx = context()
    key = "generated/42/a~b.png"
    ctx.latest_edl()["json"]["overlays"] = [{"asset_key": key}]
    monkeypatch.setattr(rc, "_media_evidence", lambda _: {
        key: {"analysis": {"sentences": [{"text": "Original-source proof."}]}}})
    def record(_ctx, _name, schema, _system, evidence, **kwargs):
        refs = {entry["path"]: entry["ref"] for entry in evidence["evidence"]}
        asset_path = "/media/generated~142~1a~0b.png"
        asset = next(entry["value"] for entry in evidence["evidence"] if entry["path"] == asset_path)
        assert asset["analysis"]["sentences"][0]["text"] == "Original-source proof."
        result = {"completed": "Verified.", "remaining": "", "requirements": [
            {"id": "subtitles", "status": "satisfied", "blocker_kind": "none",
             "reason": "Unchanged.", "evidence": [refs["/after/captions"]]},
            {"id": "type", "status": "satisfied", "blocker_kind": "none",
             "reason": "The cited asset is present.", "evidence": [
                 refs["/after/overlays"], refs[asset_path]]}]}
        rc._validate(result, schema)
        return result
    monkeypatch.setattr(rc, "_call", record)
    result = rc.assess(ctx)
    assert result["complete"]
    assert result["requirements"][1]["evidence"] == [
        "/after/overlays", "/media/generated~142~1a~0b.png"]


def test_assessment_registry_loses_no_deep_or_later_evidence():
    evidence = {"after": {"overlays": [{"mask": {"shapes": [{"points": [[1, 2]]}]}}]},
                "media": {f"clips/{i}/a~b.mp4": {"analysis": {"sentences": [{"text": str(i)}]}}
                          for i in range(150)}, "preview": None, "visual_proofs": []}
    entries, pointers = rc._assessment_evidence(evidence)
    assert len(entries) == 153
    for entry in entries:
        assert rc._pointer(evidence, pointers[entry["ref"]]) == entry["value"]
    assert "/media/clips~1149~1a~0b.mp4" in pointers.values()
    assert next(e for e in entries if e["path"] == "/after/overlays")["value"] == evidence["after"]["overlays"]


def test_bad_assessment_does_not_repeat_an_expensive_review(monkeypatch):
    ctx = context()
    calls = []
    def invalid(*args, **kwargs):
        calls.append(1)
        raise ValueError("An evidence ref was not supplied")
    monkeypatch.setattr(rc, "_call", invalid)
    result = rc.assess(ctx)
    assert calls == [1]
    assert not result["complete"]
    assert all(r["status"] == "unknown" and r["blocker_kind"] == "none"
               for r in result["requirements"])


def test_review_selects_output_source_evidence_without_indexer_vectors():
    index = {"video": {"duration": 6321},
             "sentences": [{"t0": i, "t1": i + 1, "text": f"Sentence {i}", "wi0": i}
                           for i in range(6321)],
             "visual_storyboard": {"semantic_status": "observed", "evidence": [
                 {"evidence_id": "selected", "covered_ranges": [[170, 205]],
                  "semantic_description": "A teacher", "retrieval_embedding": [0.1] * 10000},
                 {"evidence_id": "elsewhere", "covered_ranges": [[5000, 5100]],
                  "semantic_description": "A whiteboard"}],
                 "chapters": [{"transcript_excerpt": "repeated text " * 100000}]}}
    result = rc._source_evidence(index, [[170, 205]])
    assert [r["t0"] for r in result["sentences"]] == list(range(169, 206))
    assert result["coverage"]["complete_source_transcript"] is False
    assert result["coverage"]["total_source_sentences"] == 6321
    assert result["visual_storyboard"]["evidence"] == [{
        "evidence_id": "selected", "covered_ranges": [[170, 205]],
        "semantic_description": "A teacher"}]
    whole = rc._source_evidence(index, None)
    assert len(whole["sentences"]) == 6321
    assert whole["coverage"]["complete_source_transcript"] is True
    assert len(whole["visual_storyboard"]["evidence"]) == 2


def test_reworded_edit_reply_is_not_new_repair_evidence(monkeypatch):
    ctx = context()
    monkeypatch.setattr(rc, "_call", lambda *args, **kwargs: {
        "requirements": [{"id": r["id"], "status": "unmet", "reason": "Still missing",
                          "evidence": [], "blocker_kind": "none"}
                         for r in ctx.semantic_contract["requirements"]],
        "completed": "", "remaining": "Still missing"})
    first = rc.assess(ctx, "Draft one")
    ctx.turn_tool_outcomes.append({"tool": "keep_segments", "kind": "no_change"})
    second = rc.assess(ctx, "Draft two")
    assert first["draft_digest"] != second["draft_digest"]
    assert first["evidence_digest"] == second["evidence_digest"]
    ctx.latest_edl()["json"]["texts"] = [{"text": "New delivered work"}]
    assert rc.assess(ctx, "Draft two")["evidence_digest"] != second["evidence_digest"]


@pytest.mark.parametrize("path", [[], ["after", True], ["after", -1], ["after", {}]])
def test_evidence_components_reject_invalid_selector_types(path):
    with pytest.raises(ValueError):
        rc._evidence_pointer(path)


def test_durable_checkpoint_keeps_contract_and_exact_version_assessment():
    ctx = context()
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2, "complete": False}
    saved = agent_loop._preview_checkpoint(ctx)
    fresh = context()
    agent_loop._restore_preview_checkpoint(fresh, saved)
    assert fresh.semantic_contract == ctx.semantic_contract
    assert fresh.request_assessment == ctx.request_assessment


@pytest.mark.parametrize("version,message", [(2, 10), (1, 11)])
def test_recovery_yields_to_new_customer_work(version, message):
    ctx = NS(job={"payload": {"operator_repair": True, "operator_expected_edl_version": 1,
                             "operator_expected_message_id": 10}}, project_id=9, session_id=8,
             db=NS(run=lambda *args: {"version": version, "message_id": message}))
    result = operator_recovery.superseded(ctx)
    assert result["outcome"] == "superseded" and result["billable"] is False


def test_real_nested_evidence_survives_completion_review(monkeypatch):
    ctx = context()
    ctx.latest_edl()["json"]["motion"] = [{"params": {"text": "Original title"}}]
    value = {"completed": "Verified.", "remaining": "", "requirements": [
        {"id": "subtitles", "status": "satisfied", "blocker_kind": "none",
         "reason": "Unchanged.", "evidence": [["after", "captions"]]},
        {"id": "type", "status": "satisfied", "blocker_kind": "none",
         "reason": "Actual title.", "evidence": [["after", "motion", 0, "params", "text"]]},
    ]}
    def record(_ctx, _name, schema, _system, payload, **_kwargs):
        # Exercise the real schema validator; replacing the LLM wrapper alone
        # had concealed its depth-limited citation enum.
        entries = {entry["path"]: entry for entry in payload["evidence"]}
        assert entries["/after/motion"]["value"][0]["params"]["text"] == "Original title"
        selected = deepcopy(value)
        selected["requirements"][0]["evidence"] = [entries["/after/captions"]["ref"]]
        selected["requirements"][1]["evidence"] = [entries["/after/motion"]["ref"]]
        rc._validate(selected, schema)
        return selected
    monkeypatch.setattr(rc, "_call", record)
    assert rc.assess(ctx)["complete"]


def test_assessment_does_not_certify_a_replaced_customer_answer(monkeypatch):
    ctx = context()
    ctx.semantic_contract["mode"] = "inspect"
    for row in ctx.semantic_contract["requirements"]:
        row.update(action="inspect", lanes=[])
    monkeypatch.setattr(rc, "_call", lambda *a, **kw: {
        "completed": "Analysis delivered.", "remaining": "", "requirements": [
            {"id": row["id"], "status": "satisfied", "blocker_kind": "none",
             "reason": "Answers the question.", "evidence": ["/draft"]}
            for row in ctx.semantic_contract["requirements"]]})
    draft = "Hook: 7/10 at 0:03. Top fix: move the payoff earlier."
    rc.assess(ctx, draft)
    assert rc.current_assessment(ctx, draft)["complete"]
    assert rc.current_assessment(ctx, "I cannot review this file.") is None


def test_semantic_multi_asset_review_is_not_rewritten_using_primary_only(monkeypatch):
    ctx = context()
    monkeypatch.setattr(agent_loop, "_grounding_violations", lambda *a:
                        pytest.fail("Single-source regex checker ran"))
    draft = 'In the uploaded FINAL, the line "rebuild collagen" needs a claims review.'
    assert agent_loop._enforce_honesty(ctx, None, [], [], draft, 1, {}) == draft


@pytest.mark.parametrize("index_unavailable", [False, True])
def test_inspected_secondary_file_keeps_its_own_transcript_evidence(index_unavailable):
    import db
    ctx = context()
    ctx.project_id = 7
    ctx._looked_asset_times = {"clips/final.mp4": {3.0, 24.0}}
    assets = [{"storage_key": "clips/final.mp4", "kind": "video_clip", "sha256": "final-sha",
               "meta": {"filename": "FINAL.mp4"}}]
    calls = []
    def run(fn, *args):
        calls.append((fn, args))
        if fn is db.assets_by_kinds:
            return assets
        assert fn is db.get_index_by_sha and args == ("final-sha",)
        if index_unavailable:
            raise TimeoutError("temporary index read failure")
        return {"json": {"sentences": [{"text": "Final-specific speech."}],
                         "video": {"duration": 42.83}}}
    ctx.db = NS(run=run)
    evidence = rc._media_evidence(ctx)["clips/final.mp4"]
    assert evidence["filename"] == "FINAL.mp4"
    if index_unavailable:
        assert evidence["analysis_unavailable"] == "TimeoutError"
        return
    assert evidence["analysis"]["sentences"] == [{"text": "Final-specific speech."}]
    assert evidence["analysis"]["inspected_times"] == [3.0, 24.0]


def test_repair_can_progress_without_approving_preexisting_damage():
    baseline = {"keep": [[0, 10]], "captions": None, "music": []}
    damaged = {"keep": [[0, 5]], "captions": {"mode": "from_transcript"}, "music": []}
    protected = {"cuts", "captions"}
    # Restore captions without first having to restore the entire timeline.
    one_repair = {**damaged, "captions": None}
    assert not scope_guard.recovery_update_violations(damaged, one_repair, baseline, protected)
    assert scope_guard.preservation_violations(baseline, one_repair, protected=protected) == ["picture cuts"]
    assert scope_guard.recovery_update_violations(
        damaged, {**one_repair, "keep": [[0, 3]]}, baseline, protected) == ["picture cuts"]
    assert not scope_guard.recovery_update_violations(one_repair, baseline, baseline, protected)


def test_new_intro_is_not_blocked_by_an_older_incomplete_episode_snapshot():
    historical = {"keep": [[16, 27], [129, 133]]}
    current = {"keep": [[0, 3600]], "inserts": []}
    with_intro = {**current, "inserts": [{"asset_key": "original", "at_output_s": 0,
                                         "duration_s": 11, "source_start_s": 16}]}
    assert not scope_guard.recovery_update_violations(current, with_intro, historical, {"cuts"})
    assert scope_guard.recovery_update_violations(
        current, {**with_intro, "keep": [[0, 40]]}, historical, {"cuts"})


def test_missing_customer_input_waits_without_claiming_completion(monkeypatch):
    ctx = context()
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2, "complete": False,
        "requirements": [{"id": "music", "status": "blocked", "blocker_kind": "customer_input"}]}
    monkeypatch.setattr(agent_loop, "_quality_handoff", lambda *_: {"export_ready": True})
    assert agent_loop._turn_completion(ctx)[0] == "awaiting_user"
    assert not rc.completion(ctx)


def test_analysis_and_verified_explanations_survive_the_reply_stage():
    ctx = context()
    ctx.semantic_contract["mode"] = "inspect"
    ctx.request_assessment = {"request_digest": rc.digest(ctx), "edl_version": 2, "complete": True}
    draft = "Hook: 7/10 at 0:03.\nRetention: 6/10 at 0:18.\nAudio: 8/10 at 0:24."
    assert rc.reply(ctx, draft) == draft
    with pytest.raises(ValueError, match="analysis"):
        rc.protected_lanes(ctx)


def test_short_followup_retains_original_request_and_excludes_later_offers():
    rows = [{"id": 1, "role": "user", "content": "Build the fishing edit in the order I supplied."},
            {"id": 2, "role": "assistant", "content": "Analysis ready."},
            {"id": 3, "role": "assistant", "content": "Preview ready."},
            {"id": 4, "role": "user", "content": "You did not do what I asked."},
            {"id": 5, "role": "assistant", "content": "A later, incorrect offer."}]
    history = rc.history(rows, 4)
    assert history[0]["content"] == rows[0]["content"]
    assert all(row["content"] != rows[-1]["content"] for row in history)


def test_requirements_after_four_thousand_characters_are_not_discarded():
    text = "Please edit this. " * 300 + " Keep the original subtitles and do not add music."
    message = {"id": 4, "content": text}
    assert agent_loop._customer_verification_request(None, 1, message, {}) == text
    assert agent_loop._customer_verification_request(None, 1, message,
        {"verification_request": text[:4000]}) == text


def test_timing_protection_allows_replacing_a_shot_in_the_same_window():
    before = {"keep": [[0, 10]], "inserts": [{"id": "i1", "asset_key": "old", "at_output_s": 5, "duration_s": 2}]}
    after = deepcopy(before)
    after["inserts"][0]["asset_key"] = "replacement"
    assert not scope_guard.preservation_violations(before, after, protected={"timeline_timing"})
    after["inserts"][0]["duration_s"] = 3
    assert scope_guard.preservation_violations(before, after, protected={"timeline_timing"})


def test_source_and_explicit_identical_aspect_ratios_are_equivalent():
    assert not scope_guard.preservation_violations({"frame": {"ratio": "source"}},
        {"frame": {"ratio": "9:16"}}, source_shape=(720, 1280), protected={"frame_ratio"})


def test_composition_boundaries_do_not_change_picture_timing():
    before = {"keep": [[6.48, 10.1], [10.1, 14.88], [15.32, 18.44]]}
    after = {"keep": [[6.48, 14.88], [15.32, 18.44]]}
    assert not scope_guard.preservation_violations(before, after, protected={"timeline_timing"})
    after['keep'][0][1] = 14.87
    assert scope_guard.preservation_violations(before, after, protected={"timeline_timing"})


def test_review_outage_keeps_the_preview_and_cannot_claim_success(monkeypatch):
    ctx = context()
    def unavailable(*args, **kwargs):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(rc, "_call", unavailable)
    result = rc.assess(ctx)
    assert not result["complete"] and ctx.last_preview["edl_version"] == 2
    assert all(r["blocker_kind"] == "provider" for r in result["requirements"])


def test_review_must_account_for_each_requirement(monkeypatch):
    ctx = context()
    monkeypatch.setattr(rc, "_call", lambda *args, **kwargs: {
        "requirements": [], "completed": "Done", "remaining": ""})
    result = rc.assess(ctx)
    assert not result["complete"] and len(result["requirements"]) == 2


def test_only_current_rendered_images_are_attached(tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "download_to", lambda key, path: __import__("pathlib").Path(path).write_bytes(b"\xff\xd8fixture"))
    ctx = NS(workdir=str(tmp_path), index={"sheet_keys": ["source.jpg"]})
    verification = {"render_evidence": {"edl_version": 2, "sheet_key": "render-v2.jpg"}}
    proofs, images = rc._visual_proofs(ctx, {"version": 2}, verification, False)
    assert [p["origin"] for p in proofs] == ["current_output", "original_source"]
    assert len([i for i in images if i["type"] == "image_url"]) == 2
    proofs, _ = rc._visual_proofs(ctx, {"version": 3}, verification, False)
    assert [p["origin"] for p in proofs] == ["original_source"]


@pytest.mark.parametrize('kind', ['continuation', 'retry'])
def test_operator_recovery_resumes_its_own_committed_work(kind):
    payload = {'operator_repair': True, 'operator_expected_edl_version': 1,
               'operator_expected_message_id': 10}
    if kind == 'continuation':
        payload['continuation_state'] = {'durable_slice': True, 'versions_written': [2, 3], 'seen_message_id': 10}
    else:
        payload['mutation_receipt'] = {'job_id': 91, 'committed': True, 'after_version': 3}
    current = {'version': 3, 'message_id': 10}
    ctx = NS(job={'id': 91, 'payload': payload}, project_id=9, session_id=8,
             db=NS(run=lambda *args: current))
    assert operator_recovery.superseded(ctx) is None
    current['version'] = 4
    assert operator_recovery.superseded(ctx)['outcome'] == 'superseded'


def test_live_steering_keeps_the_whole_brief_and_the_new_preservation_baseline(monkeypatch):
    ctx = context()
    ctx.editing_metrics = {}
    ctx.job = {'id': 91, 'project_id': 9, 'payload': {}}
    text = 'Detailed scene direction. ' * 220 + ' Preserve the subtitles and do not add music.'
    worker_db = NS(run=lambda *args: {'messages': [{'id': 11, 'content': text}], 'job_ids': [92]})
    monkeypatch.setattr(agent_loop, '_attachment_context', lambda *args: '')
    messages = []
    assert agent_loop._adopt_steering_messages(ctx, worker_db, ctx.job, 8, messages, 10) == 11
    assert ctx.verification_request.endswith(text)
    assert ctx.user_message.endswith(text)
    assert ctx.request_preservation_baseline == ctx.latest_edl()['json']


def test_recovery_history_is_bounded_before_limiting_later_retry_replies():
    import os
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import db
    dsn = os.getenv('QUEUE_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('requires the isolated queue test database')
    with psycopg2.connect(dsn, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE chat_messages (id bigint, session_id bigint, role text, content text, meta jsonb) ON COMMIT DROP")
            cursor.executemany("INSERT INTO chat_messages VALUES (%s,%s,%s,%s,'{}')", [
                (98,7,'user','Add music'),(99,7,'assistant','Original offer'),
                (100,7,'user','2'),(99,8,'assistant','Other customer'),
                (99,7,'activity','Tool log'),
                *[(i,7,'assistant','Later retry offer') for i in range(101,140)]])
        past = db.recent_chat(conn,7,4,before_id=100)
        assert [row['id'] for row in past] == [98,99,100]
        assert past[-2]['content'] == 'Original offer'
        assert [row['id'] for row in db.recent_chat(conn,7,2)] == [138,139]
