"""
Admin observability for the video editor. Everything an operator needs to
understand a session after the fact: per-user rollups, ops counters, a
per-project inspector (full chat + activity, EDL diffs, jobs with timings,
assets with short-lived presigned previews, the raw index, and every model
call persisted by the worker in llm_calls), and a cost view.

Security: every route is behind admin_required (same JWT-email gate as the
legacy admin), presigned links are <=15 min (storage.PRESIGN_EXPIRY), and
llm_calls payloads are capped + key-redacted by the worker before storage.
"""

import os

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app

from routes.admin import admin_required, _scope, METRICS_EPOCH
import model_prices
import storage

admin_video_bp = Blueprint("admin_video", __name__)

# FALLBACK $ per 1M tokens, for a model model_prices.py does not list (default =
# DeepSeek V4 Pro, $1.74 in / $3.48 out). Every listed model is priced from its
# OWN row's `model` column instead, because a turn can run on DeepSeek for a
# free user and Grok for a subscriber and one blended rate is then wrong for
# both. MUST match worker/config.py or the admin spend view disagrees with what
# users were actually charged.
PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "1.74"))
PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "3.48"))
# Cache-hit input price — see worker/config.LLM_PRICE_CACHED_IN_PER_M. Rows
# carry their cache-hit slice in response->>'cached_in' and their reasoning
# tokens (charged only where the provider bills them separately) in
# response->>'reasoning_out'.
PRICE_CACHED_IN_PER_M = float(
    os.getenv("LLM_PRICE_CACHED_IN_PER_M", "0.003625"))

PRICE_FALLBACK = {"in": PRICE_IN_PER_M, "cached_in": PRICE_CACHED_IN_PER_M,
                  "out": PRICE_OUT_PER_M, "reasoning_separate": False}


def adb():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _row_cost(alias=""):
    """USD cost of ONE llm_calls row, priced from that row's own model.

    Identical expression to worker/db.charge_turn_credits, so the admin spend
    view and the credits actually deducted cannot disagree. Cache-HIT input is
    billed at a small fraction of a miss, so counting every prompt token at the
    miss price would show a spend several times what users were charged; rows
    written before the split simply have no 'cached_in' and price as all-miss,
    which is what they were.

    `alias` prefixes the column names when the query joins llm_calls.
    """
    p = (alias + ".") if alias else ""
    return model_prices.row_cost_sql(
        PRICE_FALLBACK, model_col=p + "model", response_col=p + "response",
        prompt_col=p + "prompt_tokens", completion_col=p + "completion_tokens")


def _cost_expr(alias=""):
    """Aggregate cost over a group of llm_calls rows."""
    return "COALESCE(SUM(" + _row_cost(alias) + "), 0)"


# A user message is "unserved" when no agent_turn job ever picked it up —
# the strongest signal that a user asked for something and got silence.
# payload->>'message_id' is text, so the message id is cast to match.
UNSERVED_EXISTS = """NOT EXISTS (SELECT 1 FROM video_jobs vj
                      WHERE vj.type = 'agent_turn'
                        AND vj.payload->>'message_id' = cm.id::text)"""


def _presign(key):
    if not storage.is_configured():
        return None
    try:
        # Admin inspection links stay on the short 15-min expiry (the long
        # PRESIGN_GET_EXPIRY exists for the studio player, not for admin).
        return storage.presign_get(key, expires=storage.PRESIGN_EXPIRY)
    except Exception:
        return None


def _msg_brief(m):
    return {"id": m["id"], "content": m["content"], "meta": m["meta"],
            "created_at": m["created_at"].isoformat()}


_QUALITY_RUBRIC_DIMENSIONS = (
    "visual_coherence", "editorial_specificity", "narrative_support",
    "motion_rhythm", "typography", "restraint")


def _quality_scorecard(rows):
    """Aggregate assistant outcome metadata into a deploy/cohort scorecard.

    No synthetic one-number quality score: it would reward feature density and
    make it easy to game the product. The operator gets the independent craft
    rubric, repair/advisory rate, cost, churn and evidence coverage side by
    side, grouped by the worker content fingerprint that produced the turn.
    """
    cohorts = {}

    def distribution(values):
        """Small exact cohort distribution without a stats dependency."""
        ordered = sorted(float(value or 0) for value in values)
        if not ordered:
            return {"p50": 0.0, "p90": 0.0, "max": 0.0}

        def pct(q):
            if len(ordered) == 1:
                return ordered[0]
            pos = (len(ordered) - 1) * q
            lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
            return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

        return {"p50": round(pct(.5), 3), "p90": round(pct(.9), 3),
                "max": round(ordered[-1], 3)}

    def number(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    for row in rows:
        meta = row.get("meta") or {}
        metrics = meta.get("editing_metrics") or {}
        code = str(metrics.get("code_version") or "legacy_unstamped")
        family = str(metrics.get("editorial_family") or "legacy_unclassified")
        cohort_key = (code, family)
        c = cohorts.setdefault(cohort_key, {
            "code_version": code, "editorial_family": family,
            "turns": 0, "outcomes": {}, "quality": {}, "audio_review": {},
            "story_review": {},
            "tool_outcomes": {}, "model_call_purposes": {},
            "editorial_decisions": {}, "editorial_choices": {},
            "treatment_choices": {},
            "decision_placements": {},
            "user_feedback": {"up": 0, "down": 0, "unrated": 0},
            "behavior": {"exported_before_next_request": 0,
                         "rapid_followup": 0, "no_observed_signal": 0},
            "contract_versions": {},
            "sums": {"tokens_in": 0.0, "tokens_cached_in": 0.0,
                     "tokens_out": 0.0,
                     "estimated_model_cost_usd": 0.0,
                     "cache_ratio": 0.0, "versions_written": 0.0,
                     "previews_rendered": 0.0, "tool_schema_chars": 0.0,
                     "model_calls": 0.0, "agent_dispatches": 0.0,
                     "tool_calls": 0.0,
                     "audio_mix_reviews": 0.0, "audio_asset_reviews": 0.0,
                     "audio_mix_reviews_reused": 0.0,
                     "visual_reviews_reused": 0.0,
                     "audio_passes_reopened": 0.0,
                     "audio_repairs_resolved": 0.0,
                     "visual_passes_reopened": 0.0,
                     "visual_repairs_resolved": 0.0,
                     "complete_previews_routed_to_proof": 0.0,
                     "audio_review_clips": 0.0,
                     "story_reviews": 0.0,
                     "screening_frames": 0.0,
                     "screening_pages": 0.0,
                     "duplicate_writes_prevented": 0.0,
                     "recipe_calls": 0.0, "recipe_commits": 0.0,
                     "recipe_aborts": 0.0,
                     "recipe_operations_committed": 0.0,
                     "recipe_references_resolved": 0.0,
                     "clean_finishing_checkpoints": 0.0,
                     "post_pass_variations_prevented": 0.0,
                     "candidates_measured": 0.0,
                     "candidates_heard": 0.0,
                     "broll_sequence_casts": 0.0,
                     "broll_moments_cast": 0.0,
                     "broll_moments_abstained": 0.0,
                     "broll_renditions_reviewed": 0.0,
                     "broll_renditions_rejected": 0.0,
                     "broll_renditions_uncertain": 0.0,
                    "sfx_abstentions": 0.0,
                    "music_abstentions": 0.0,
                    "caption_treatment_casts": 0.0,
                    "caption_visual_casts": 0.0,
                     "caption_visual_cast_fallbacks": 0.0,
                     "caption_proof_candidates_rendered": 0.0,
                     "caption_proof_pages": 0.0,
                     "uploaded_media_comparisons": 0.0,
                     "uploaded_media_assets_requested": 0.0,
                     "uploaded_media_assets_compared": 0.0,
                     "uploaded_media_frames_compared": 0.0,
                     "uploaded_media_comparison_pages": 0.0,
                     "editorial_family_explicit": 0.0,
                     "format_cast_confidence": 0.0,
                     "format_cast_abstained": 0.0,
                     "department_decisions": 0.0,
                     "department_promises": 0.0,
                     "department_promises_fulfilled": 0.0,
                     "department_execution_gaps": 0.0,
                     "motion_contract_active": 0.0,
                     "motion_contract_beats": 0.0,
                     "motion_contract_mapped_beats": 0.0,
                     "motion_contract_fulfilled_beats": 0.0,
                     "motion_contract_gaps": 0.0,
                     "department_closure_rejections": 0.0,
                     "readiness_previews_prevented": 0.0,
                     "treatment_judge_reviews": 0.0,
                     "treatment_judge_reviews_reused": 0.0,
                     "treatment_judge_accepts": 0.0,
                     "treatment_judge_revisions": 0.0,
                     "treatment_judge_abstentions": 0.0},
            "samples": {key: [] for key in (
                "tokens_in", "agent_dispatches", "model_calls",
                "tool_calls", "versions_written", "previews_rendered")},
            "rubric": {d: {"strong": 0, "adequate": 0, "weak": 0,
                            "not_judged": 0, "missing": 0}
                       for d in _QUALITY_RUBRIC_DIMENSIONS},
            "finding_categories": {}, "latest_at": None,
        })
        c["turns"] += 1
        outcome = str(meta.get("outcome") or "unknown")
        c["outcomes"][outcome] = c["outcomes"].get(outcome, 0) + 1
        quality = str(meta.get("quality_status") or "unknown")
        c["quality"][quality] = c["quality"].get(quality, 0) + 1
        for kind, count in (meta.get("tool_outcomes") or {}).items():
            kind = str(kind or "unknown")
            c["tool_outcomes"][kind] = (
                c["tool_outcomes"].get(kind, 0) + int(number(count)))
        for purpose, count in (
                metrics.get("model_calls_by_purpose") or {}).items():
            purpose = str(purpose or "unknown")
            c["model_call_purposes"][purpose] = (
                c["model_call_purposes"].get(purpose, 0)
                + int(number(count)))
        for decision in metrics.get("editorial_decisions") or []:
            if not isinstance(decision, dict):
                continue
            label = (str(decision.get("kind") or "unknown") + ":" +
                     str(decision.get("decision") or "unknown"))
            c["editorial_decisions"][label] = (
                c["editorial_decisions"].get(label, 0) + 1)
            # Stable, reusable treatment facets only. Candidate ids, URLs,
            # free-form reasons and transcript words are intentionally absent.
            for facet in ("preset", "placement_strategy", "emphasis",
                          "animation", "layout"):
                value = decision.get(facet)
                if value in (None, "", []):
                    continue
                choice = f"{decision.get('kind') or 'unknown'}:{facet}={value}"
                c["editorial_choices"][choice] = (
                    c["editorial_choices"].get(choice, 0) + 1)
            placement = str(decision.get("placement_status") or "unknown")
            c["decision_placements"][placement] = (
                c["decision_placements"].get(placement, 0) + 1)
        for facet, raw in (metrics.get("treatment_profile") or {}).items():
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if not isinstance(value, (str, int, float, bool)):
                    continue
                choice = f"{facet}={value}"
                c["treatment_choices"][choice] = (
                    c["treatment_choices"].get(choice, 0) + 1)
        feedback = str(meta.get("feedback") or "unrated")
        if feedback not in c["user_feedback"]:
            feedback = "unrated"
        c["user_feedback"][feedback] += 1
        if row.get("exported_after"):
            c["behavior"]["exported_before_next_request"] += 1
        elif row.get("rapid_followup"):
            c["behavior"]["rapid_followup"] += 1
        else:
            c["behavior"]["no_observed_signal"] += 1
        contract_v = str(metrics.get("editorial_contract_v") or "legacy")
        c["contract_versions"][contract_v] = \
            c["contract_versions"].get(contract_v, 0) + 1
        c["latest_at"] = max(
            filter(None, [c["latest_at"], row.get("created_at")]),
            default=None)
        sums = c["sums"]
        sums["tokens_in"] += number(metrics.get("tokens_in"))
        sums["tokens_cached_in"] += number(metrics.get("tokens_cached_in"))
        sums["tokens_out"] += number(metrics.get("tokens_out"))
        sums["estimated_model_cost_usd"] += number(
            metrics.get("estimated_model_cost_usd"))
        sums["cache_ratio"] += number(metrics.get("prompt_cache_ratio"))
        sums["model_calls"] += number(metrics.get("model_calls"))
        sums["agent_dispatches"] += number(metrics.get("agent_dispatches"))
        sums["tool_calls"] += number(metrics.get("tool_calls"))
        sums["audio_mix_reviews"] += number(metrics.get("audio_mix_reviews"))
        for key in ("audio_mix_reviews_reused", "visual_reviews_reused",
                    "audio_passes_reopened", "audio_repairs_resolved",
                    "visual_passes_reopened", "visual_repairs_resolved"):
            sums[key] += number(metrics.get(key))
        sums["complete_previews_routed_to_proof"] += number(
            metrics.get("complete_previews_routed_to_proof"))
        sums["audio_asset_reviews"] += number(
            metrics.get("audio_asset_reviews"))
        sums["audio_review_clips"] += number(metrics.get("audio_review_clips"))
        sums["story_reviews"] += number(metrics.get("story_reviews"))
        sums["versions_written"] += number(metrics.get("versions_written"))
        sums["previews_rendered"] += number(metrics.get("previews_rendered"))
        sums["tool_schema_chars"] += number(
            metrics.get("post_plan_tool_schema_chars") or
            metrics.get("initial_tool_schema_chars"))
        sums["duplicate_writes_prevented"] += number(
            metrics.get("duplicate_writes_prevented"))
        for key in ("recipe_calls", "recipe_commits", "recipe_aborts",
                    "recipe_operations_committed",
                    "recipe_references_resolved"):
            sums[key] += number(metrics.get(key))
        sums["clean_finishing_checkpoints"] += number(
            metrics.get("clean_finishing_checkpoints"))
        sums["post_pass_variations_prevented"] += number(
            metrics.get("post_pass_variations_prevented"))
        sums["candidates_measured"] += sum(number(metrics.get(key)) for key in
            ("music_candidates_measured", "sfx_candidates_measured",
             "broll_candidates_compared", "motion_profiles_measured"))
        sums["candidates_heard"] += sum(number(metrics.get(key)) for key in
            ("music_candidates_heard", "sfx_candidates_heard"))
        for key in ("broll_sequence_casts", "broll_moments_cast",
                    "broll_moments_abstained",
                    "broll_renditions_reviewed",
                    "broll_renditions_rejected",
                    "broll_renditions_uncertain", "sfx_abstentions",
                    "music_abstentions"):
            sums[key] += number(metrics.get(key))
        for key in ("caption_treatment_casts", "caption_visual_casts",
                    "caption_visual_cast_fallbacks",
                    "caption_proof_candidates_rendered",
                    "caption_proof_pages"):
            sums[key] += number(metrics.get(key))
        for key in ("uploaded_media_comparisons",
                    "uploaded_media_assets_requested",
                    "uploaded_media_assets_compared",
                    "uploaded_media_frames_compared",
                    "uploaded_media_comparison_pages",
                    "editorial_family_explicit", "format_cast_confidence",
                    "format_cast_abstained", "department_decisions",
                    "department_promises",
                    "department_promises_fulfilled",
                    "department_execution_gaps",
                    "motion_contract_active", "motion_contract_beats",
                    "motion_contract_mapped_beats",
                    "motion_contract_fulfilled_beats",
                    "motion_contract_gaps",
                    "department_closure_rejections",
                    "readiness_previews_prevented",
                    "treatment_judge_reviews",
                    "treatment_judge_reviews_reused",
                    "treatment_judge_accepts",
                    "treatment_judge_revisions",
                    "treatment_judge_abstentions"):
            sums[key] += number(metrics.get(key))
        for key in c["samples"]:
            c["samples"][key].append(number(metrics.get(key)))
        qe = metrics.get("quality_evidence") or {}
        sums["screening_frames"] += number(qe.get("screening_frames"))
        sums["screening_pages"] += number(qe.get("screening_pages"))
        audio_verdict = str(qe.get("audio_review_verdict") or "not_reviewed")
        c["audio_review"][audio_verdict] = \
            c["audio_review"].get(audio_verdict, 0) + 1
        story_verdict = str(qe.get("story_review_verdict") or "not_reviewed")
        c["story_review"][story_verdict] = \
            c["story_review"].get(story_verdict, 0) + 1
        rubric = qe.get("visual_rubric") or {}
        for dimension in _QUALITY_RUBRIC_DIMENSIONS:
            level = str((rubric.get(dimension) or {}).get("level") or "missing")
            if level not in c["rubric"][dimension]:
                level = "missing"
            c["rubric"][dimension][level] += 1
        for category in qe.get("visual_finding_categories") or []:
            category = str(category or "other")
            c["finding_categories"][category] = \
                c["finding_categories"].get(category, 0) + 1
        for category in qe.get("story_finding_categories") or []:
            category = "story/" + str(category or "other")
            c["finding_categories"][category] = \
                c["finding_categories"].get(category, 0) + 1

    out = []
    for cohort in cohorts.values():
        n = max(1, cohort["turns"])
        totals = cohort.pop("sums")
        cohort["averages"] = {
            key: round(value / n, 3) for key, value in totals.items()
        }
        cohort["distributions"] = {
            key: distribution(values)
            for key, values in cohort.pop("samples").items()}
        dispatches = totals["agent_dispatches"]
        recipe_calls = totals["recipe_calls"]
        recipe_commits = totals["recipe_commits"]
        cohort["efficiency"] = {
            "weighted_prompt_cache_ratio": round(
                totals["tokens_cached_in"] / totals["tokens_in"], 3)
            if totals["tokens_in"] else 0.0,
            "input_tokens_per_agent_dispatch": round(
                totals["tokens_in"] / dispatches, 1)
            if dispatches else 0.0,
            "agent_dispatches_per_written_version": round(
                dispatches / totals["versions_written"], 3)
            if totals["versions_written"] else 0.0,
            "recipe_commit_rate": round(recipe_commits / recipe_calls, 3)
            if recipe_calls else None,
            "operations_per_recipe_commit": round(
                totals["recipe_operations_committed"] / recipe_commits, 3)
            if recipe_commits else 0.0,
            "department_promise_fulfillment_rate": round(
                totals["department_promises_fulfilled"] /
                totals["department_promises"], 3)
            if totals["department_promises"] else None,
            "motion_contract_fulfillment_rate": round(
                totals["motion_contract_fulfilled_beats"] /
                totals["motion_contract_mapped_beats"], 3)
            if totals["motion_contract_mapped_beats"] else None,
        }
        rated = cohort["user_feedback"]["up"] + \
            cohort["user_feedback"]["down"]
        cohort["user_feedback"]["up_rate"] = (
            round(cohort["user_feedback"]["up"] / rated, 3)
            if rated else None)
        if cohort["latest_at"] is not None and hasattr(
                cohort["latest_at"], "isoformat"):
            cohort["latest_at"] = cohort["latest_at"].isoformat()
        out.append(cohort)
    return sorted(out, key=lambda c: c.get("latest_at") or "", reverse=True)


@admin_video_bp.route("/admin/video/quality-scorecard", methods=["GET"])
@admin_required
def video_quality_scorecard():
    days = min(90, max(1, request.args.get("days", type=int) or 14))
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT cm.meta, cm.created_at,
                              EXISTS (
                                SELECT 1
                                FROM projects p
                                JOIN client_events ce
                                  ON ce.project_id = p.id
                                WHERE p.chat_session_id = cm.session_id
                                  AND ce.kind = 'download_triggered'
                                  AND ce.created_at >= cm.created_at
                                  AND ce.created_at < LEAST(
                                    cm.created_at + INTERVAL '24 hours',
                                    COALESCE((
                                      SELECT MIN(nu.created_at)
                                      FROM chat_messages nu
                                      WHERE nu.session_id = cm.session_id
                                        AND nu.role = 'user'
                                        AND nu.id > cm.id
                                    ), cm.created_at + INTERVAL '24 hours'))
                              ) AS exported_after,
                              EXISTS (
                                SELECT 1 FROM chat_messages nu
                                WHERE nu.session_id = cm.session_id
                                  AND nu.role = 'user' AND nu.id > cm.id
                                  AND nu.created_at <
                                      cm.created_at + INTERVAL '30 minutes'
                              ) AS rapid_followup
                       FROM chat_messages cm
                       WHERE cm.role = 'assistant'
                         AND cm.created_at > NOW() - (%s || ' days')::interval
                         AND cm.meta ? 'editing_metrics'
                       ORDER BY cm.created_at DESC LIMIT 10000""",
                    (str(days),))
        rows = cur.fetchall()
    return jsonify({
        "days": days,
        "turns": len(rows),
        "cohorts": _quality_scorecard(rows),
        "interpretation": (
            "Compare code-version and editorial-family cohorts across actual "
            "user feedback, export-before-next-request and rapid-follow-up "
            "behavior, rubric weakness/advisory rates, model cost, "
            "screening coverage, schema characters, EDL churn, renders and "
            "candidate evidence. There is intentionally no gameable single "
            "quality number."),
    })


@admin_video_bp.route("/admin/video/overview", methods=["GET"])
@admin_required
def video_overview():
    with adb() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            SELECT u.id, u.email,
                   COUNT(DISTINCT p.id) AS projects,
                   COALESCE(m.msgs, 0) AS messages,
                   COALESCE(j.done, 0) AS jobs_done,
                   COALESCE(j.failed, 0) AS jobs_failed,
                   COALESCE(j.active, 0) AS jobs_active,
                   COALESCE(a.bytes, 0) AS storage_bytes,
                   COALESCE(l.tokens_in, 0) AS tokens_in,
                   COALESCE(l.tokens_out, 0) AS tokens_out,
                   COALESCE(l.est_cost, 0) AS est_cost,
                   GREATEST(COALESCE(j.last, to_timestamp(0)),
                            COALESCE(m.last, to_timestamp(0))) AS last_active
            FROM users u
            JOIN projects p ON p.user_id = u.id
            LEFT JOIN (SELECT p2.user_id, COUNT(*) AS msgs,
                              MAX(cm.created_at) AS last
                       FROM chat_messages cm
                       JOIN projects p2 ON p2.chat_session_id = cm.session_id
                       WHERE cm.role = 'user'
                       GROUP BY p2.user_id) m ON m.user_id = u.id
            LEFT JOIN (SELECT user_id,
                              COUNT(*) FILTER (WHERE state='done') AS done,
                              COUNT(*) FILTER (WHERE state='failed') AS failed,
                              COUNT(*) FILTER (WHERE state IN
                                               ('queued','running')) AS active,
                              MAX(updated_at) AS last
                       FROM video_jobs GROUP BY user_id) j ON j.user_id = u.id
            LEFT JOIN (SELECT p3.user_id, SUM(ast.bytes)::bigint AS bytes
                       FROM assets ast
                       JOIN projects p3 ON p3.id = ast.project_id
                       GROUP BY p3.user_id) a ON a.user_id = u.id
            LEFT JOIN (SELECT p4.user_id,
                              SUM(lc.prompt_tokens) AS tokens_in,
                              SUM(lc.completion_tokens) AS tokens_out,
                              """ + _cost_expr("lc") + """ AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            GROUP BY u.id, u.email, m.msgs, j.done, j.failed, j.active,
                     a.bytes, l.tokens_in, l.tokens_out, l.est_cost,
                     j.last, m.last
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """)
        users = cur.fetchall()

        # global ops counters + 14-day trends
        cur.execute("""
            SELECT DATE(created_at) AS day,
                   COUNT(*) FILTER (WHERE type='agent_turn') AS turns,
                   COUNT(*) FILTER (WHERE type IN ('preview','final')) AS renders
            FROM video_jobs
            WHERE created_at > NOW() - INTERVAL '14 days'
            GROUP BY 1 ORDER BY 1
        """)
        daily = cur.fetchall()

        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE type='agent_turn') AS turns_total,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->>'auto_render')::boolean IS TRUE) AS auto_renders,
              COALESCE(SUM((result->'honesty'->>'false_claims')::int)
                FILTER (WHERE type='agent_turn'), 0) AS false_claims,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->'honesty'->>'corrective_note')::boolean
                    IS TRUE) AS corrective_notes,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->'honesty'->>'fallback_reply')::boolean
                    IS TRUE) AS fallback_replies,
              COUNT(*) FILTER (WHERE state='failed') AS failed,
              COUNT(*) FILTER (WHERE state IN ('done','failed')) AS finished,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                (result->'timings'->>'queue_wait_s')::float)
                FILTER (WHERE result->'timings'->>'queue_wait_s'
                        IS NOT NULL) AS median_queue_wait_s
            FROM video_jobs
        """)
        ops = cur.fetchone()

        stage_medians = {}
        for jtype, stages in (("index", ("whisper_s", "proxy_s", "shots_s",
                                         "total_s")),
                              ("preview", ("download_s", "encode_s",
                                           "upload_s", "total_s")),
                              ("final", ("download_s", "encode_s",
                                         "upload_s", "total_s")),
                              ("agent_turn", ("llm_s", "total_s"))):
            row = {}
            for st in stages:
                cur.execute(f"""
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                        (result->'timings'->>%s)::float) AS med
                    FROM video_jobs
                    WHERE type = %s AND state = 'done'
                      AND result->'timings'->>%s IS NOT NULL
                """, (st, jtype, st))
                med = cur.fetchone()["med"]
                if med is not None:
                    row[st] = round(med, 2)
            stage_medians[jtype] = row

        cur.execute("""
            SELECT COUNT(*) AS n FROM chat_messages
            WHERE role='activity' AND content LIKE '%NO CHANGE%'
        """)
        no_change = cur.fetchone()["n"]

        # attention feed: only things a human can act on (failed/stuck jobs).
        # Unserved messages are deliberately NOT here — with auto-resume live
        # they self-heal, and there is no admin action to take on old ones.
        cur.execute("""
            SELECT * FROM (
                SELECT 'failed_job' AS type, p.id AS project_id,
                       p.title AS project_title, u.email,
                       vj.type || ': ' || LEFT(COALESCE(vj.error, ''), 140)
                           AS detail,
                       vj.updated_at AS happened_at
                FROM video_jobs vj
                JOIN projects p ON p.id = vj.project_id
                JOIN users u ON u.id = vj.user_id
                WHERE vj.state = 'failed'
                  AND vj.updated_at > NOW() - INTERVAL '7 days'
                UNION ALL
                SELECT 'stuck_job', p.id, p.title, u.email,
                       vj.type || ' stuck (' || vj.state || ')',
                       COALESCE(vj.heartbeat_at, vj.created_at)
                FROM video_jobs vj
                JOIN projects p ON p.id = vj.project_id
                JOIN users u ON u.id = vj.user_id
                WHERE vj.state IN ('queued', 'running')
                  AND ((vj.heartbeat_at IS NULL
                        AND vj.created_at < NOW() - INTERVAL '10 minutes')
                       OR vj.heartbeat_at < NOW() - INTERVAL '10 minutes')
                UNION ALL
                SELECT 'upload_failed', ce.project_id, p.title, u.email,
                       -- Every JSON accessor is parenthesised on purpose:
                       -- `||` binds tighter than `->>`, so the unparenthesised
                       -- form parses as (' - ' || ce.detail) ->> 'filename'
                       -- and dies with "invalid input syntax for type json"
                       -- at RUNTIME, taking the whole overview page with it.
                       COALESCE((ce.detail->>'reason'), ce.kind)
                         || COALESCE(' - ' || (ce.detail->>'filename'), '')
                         || COALESCE(' (' || ROUND(
                              (CASE WHEN (ce.detail->>'bytes') ~ '^[0-9]+$'
                                    THEN (ce.detail->>'bytes')::numeric
                               END) / 1073741824.0, 2) || ' GB)', ''),
                       ce.created_at
                FROM client_events ce
                LEFT JOIN projects p ON p.id = ce.project_id
                LEFT JOIN users u ON u.id = ce.user_id
                WHERE ce.kind IN ('upload_rejected', 'upload_failed')
                  AND ce.created_at > NOW() - INTERVAL '7 days'
            ) t
            ORDER BY happened_at DESC NULLS LAST
            LIMIT 40
        """)
        attention = cur.fetchall()

        # headline totals + liveness — "is everything working" at a glance
        cur.execute("""
            SELECT
              (SELECT COUNT(*) FROM projects) AS projects,
              (SELECT COUNT(*) FROM assets WHERE kind='original') AS videos,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE type IN ('preview','final')
                   AND state='done') AS renders_done,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE state='queued') AS queued_now,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE state='running') AS running_now,
              (SELECT MAX(updated_at) FROM video_jobs
                 WHERE state IN ('done','failed','running'))
                 AS last_worker_activity
        """)
        totals = cur.fetchone()

        # Which model actually ran for each purpose (ground truth from
        # llm_calls) — so "am I really on Grok?" is answerable at a glance and
        # the agent-vs-vision-vs-image split is no longer a mystery.
        cur.execute("""
            SELECT purpose,
                   (ARRAY_AGG(model ORDER BY created_at DESC))[1] AS model,
                   COUNT(*) AS calls,
                   MAX(created_at) AS last_at
            FROM llm_calls
            WHERE created_at > NOW() - INTERVAL '30 days'
              AND model IS NOT NULL
            GROUP BY purpose
            ORDER BY last_at DESC NULLS LAST
        """)
        model_rows = cur.fetchall()

        # Vision liveness, inferred from behaviour. A day with agent turns but
        # not one vision call is the signature of a lost vision key: on a
        # healthy day look_at runs on roughly a third of turns. Requiring a
        # meaningful number of agent calls first is what stops a quiet weekend
        # from reading as an outage.
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE purpose = 'agent') AS agent_calls,
                   COUNT(*) FILTER (WHERE purpose LIKE 'vision%') AS vision_calls
            FROM llm_calls
            WHERE created_at > NOW() - INTERVAL '24 hours'
        """)
        vrow = cur.fetchone() or {}
        _agent_n = int(vrow.get("agent_calls") or 0)
        _vision_n = int(vrow.get("vision_calls") or 0)
        # None = "not enough traffic to say", which must not render as a
        # red light. Only a confident zero is an outage.
        vision_live = None if _agent_n < 25 else bool(_vision_n)

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")

    def _provider(b):
        b = (b or "").lower()
        if "dashscope" in b:
            return "DashScope (Qwen)"
        if "deepseek" in b:
            return "DeepSeek"
        if "x.ai" in b:
            return "xAI (Grok)"
        if "openai" in b:
            return "OpenAI"
        return "custom"

    return jsonify({
        "totals": {
            "users": len(users),
            "projects": totals["projects"],
            "videos": totals["videos"],
            "renders_done": totals["renders_done"],
            "queued_now": totals["queued_now"],
            "running_now": totals["running_now"],
            "last_worker_activity": totals["last_worker_activity"].isoformat()
                if totals["last_worker_activity"] else None,
        },
        "health": {
            "storage_configured": bool(os.getenv("S3_ENDPOINT")
                                       and os.getenv("S3_BUCKET")),
            "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
            # IS THE AGENT STILL LOOKING AT THE FOOTAGE. Vision is honest-off
            # by contract — when its provider is unconfigured every tool says
            # "visual inspection unavailable" and nothing fails — which is
            # right for the user and invisible to us. The Jul 26 2026 provider
            # switch dropped the dispatcher's inherited vision key and it took
            # TWO DAYS and 419 agent calls with zero look_at to notice.
            # Inferred from behaviour, not from this service's env: the worker
            # is a different service with different variables, so its env is
            # not ours to read and its BEHAVIOUR is the only truth available.
            "vision_live": vision_live,
        },
        "users": [{**u, "last_active": u["last_active"].isoformat()
                   if u.get("last_active") else None,
                   "storage_bytes": int(u["storage_bytes"] or 0),
                   "tokens_in": int(u["tokens_in"] or 0),
                   "tokens_out": int(u["tokens_out"] or 0),
                   "est_cost": round(float(u["est_cost"] or 0), 4)}
                  for u in users],
        "daily": [{"day": d["day"].isoformat(), "turns": d["turns"],
                   "renders": d["renders"]} for d in daily],
        "ops": {
            "turns_total": ops["turns_total"],
            "auto_renders": ops["auto_renders"],
            "false_claims": ops["false_claims"],
            "corrective_notes": ops["corrective_notes"],
            "fallback_replies": ops["fallback_replies"],
            "no_change_count": no_change,
            "job_failure_rate": round(
                ops["failed"] / ops["finished"], 4) if ops["finished"] else 0,
            "median_queue_wait_s": round(ops["median_queue_wait_s"], 2)
                if ops["median_queue_wait_s"] is not None else None,
            "stage_medians": stage_medians,
        },
        "attention": [
            {"type": a["type"], "project_id": a["project_id"],
             "project_title": a["project_title"], "email": a["email"],
             "detail": a["detail"],
             "at": a["happened_at"].isoformat()
                 if a["happened_at"] else None}
            for a in attention],
        "models": {
            # Backend service config (concierge/index-greet run here). The
            # worker service can be pointed elsewhere — trust "observed".
            "configured": {
                "provider": _provider(base_url),
                "base_url": base_url,
                "agent_model": os.getenv("AGENT_MODEL", "deepseek-v4-pro"),
                # Vision runs on its OWN provider (worker/config.py): the
                # chat provider may take no images at all — DeepSeek 400s on
                # every one, which blinded the agent on Jul 26 2026.
                "vision_model": os.getenv("VISION_MODEL", "grok-4.5"),
                "vision_base_url": os.getenv("VISION_BASE_URL",
                                             "https://api.x.ai/v1"),
                "image_gen_model": os.getenv("IMAGE_GEN_MODEL",
                                             "grok-imagine-image-quality"),
                "image_edit_model": os.getenv("IMAGE_EDIT_MODEL", "") or None,
                "whisper_model": os.getenv("WHISPER_MODEL", "medium"),
                "price_in_per_m": PRICE_IN_PER_M,
                "price_out_per_m": PRICE_OUT_PER_M,
            },
            # What each purpose ACTUALLY used, last 30 days, newest first.
            "observed": [
                {"purpose": m["purpose"], "model": m["model"],
                 "calls": int(m["calls"] or 0),
                 "last_at": m["last_at"].isoformat() if m["last_at"] else None}
                for m in model_rows],
        },
    })


# ── HOW LONG DID THIS PERSON WAIT? ──────────────────────────────────────────
#
# The three waits a customer actually experiences, per project, so they can be
# read against the one number that should predict them: how long their video
# is. Every churn investigation this codebase has run started with someone
# saying "it took forever" and ended in a hand-written SQL query.
#
#   upload_s  their bytes leaving their machine — from the moment the browser
#             says it started (client_events.upload_started, round 57) to the
#             asset row existing. Falls back to the project's own creation
#             time for everything uploaded before that event existed.
#   index_s   the first successful analysis. A CACHE HIT is flagged, because
#             re-uploading a file we have already indexed is 10 seconds and
#             tells you nothing about the pipeline's real speed.
#   edit_s    the MEDIAN preview render — what the user waits, every time, to
#             see a change they just made. The median and not the mean: one
#             cold 4K encode should not describe a session of small cuts.
#
# The LATERALs pick a specific row (ORDER BY ... LIMIT 1) rather than
# aggregating: MIN(created_at) with MIN(updated_at) can silently pair the start
# of one job with the end of another and produce a duration that never happened.
_PROJECT_TIMINGS = """
    LEFT JOIN LATERAL (
        SELECT a.id, a.duration_s, a.bytes, a.width, a.height, a.created_at
        FROM assets a
        WHERE a.project_id = p.id AND a.kind = 'original'
        ORDER BY a.id ASC LIMIT 1) o ON TRUE
    LEFT JOIN LATERAL (
        SELECT ce.created_at
        FROM client_events ce
        WHERE ce.project_id = p.id AND ce.kind = 'upload_started'
          AND ce.created_at <= o.created_at
        ORDER BY (ce.detail->>'bytes' ~ '^[0-9]+$'
                  AND (ce.detail->>'bytes')::bigint = o.bytes) DESC NULLS LAST,
                 ce.id DESC
        LIMIT 1) ue ON TRUE
    LEFT JOIN LATERAL (
        SELECT vj.created_at AS t0, vj.updated_at AS t1,
               (vj.result->>'cached') AS cached
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'index' AND vj.state = 'done'
        ORDER BY vj.id ASC LIMIT 1) ij ON TRUE
    LEFT JOIN LATERAL (
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (vj.updated_at - vj.created_at))
               ) AS med,
               COUNT(*) AS n
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'preview'
          AND vj.state = 'done' AND vj.updated_at > vj.created_at) pv ON TRUE
    LEFT JOIN LATERAL (
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (vj.updated_at - vj.created_at))
               ) AS med,
               COUNT(*) AS n
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'agent_turn'
          AND vj.state = 'done' AND vj.updated_at > vj.created_at) ag ON TRUE
"""

_TIMING_COLS = """
    o.duration_s AS duration_s, o.bytes AS source_bytes,
    o.width AS width, o.height AS height,
    EXTRACT(EPOCH FROM (o.created_at
                        - COALESCE(ue.created_at, p.created_at))) AS upload_s,
    (ue.created_at IS NOT NULL) AS upload_measured,
    EXTRACT(EPOCH FROM (ij.t1 - ij.t0)) AS index_s,
    (ij.cached = 'true') AS index_cached,
    pv.med AS edit_s, pv.n AS previews,
    ag.med AS turn_s, ag.n AS turns
"""


def _timing_out(r):
    """Round the seconds and drop the negatives. A clock that runs backwards
    (an asset row written before its own upload_started event was flushed) is
    not a measurement — reporting it as one puts a nonsense point on the chart."""
    def secs(v):
        if v is None:
            return None
        v = float(v)
        return round(v, 1) if v >= 0 else None
    return {
        "duration_s": round(float(r["duration_s"]), 1) if r["duration_s"] else None,
        "source_bytes": r["source_bytes"],
        "width": r["width"], "height": r["height"],
        "upload_s": secs(r["upload_s"]),
        # False = derived from the project's creation time, because this upload
        # predates client_events (round 57). It includes however long the user
        # spent choosing a file, so it is an upper bound, not a measurement.
        "upload_measured": bool(r["upload_measured"]),
        "index_s": secs(r["index_s"]),
        "index_cached": bool(r["index_cached"]),
        "edit_s": secs(r["edit_s"]), "previews": r["previews"] or 0,
        "turn_s": secs(r["turn_s"]), "turns": r["turns"] or 0,
    }


@admin_video_bp.route("/admin/video/projects", methods=["GET"])
@admin_required
def video_projects():
    search = (request.args.get("search") or "").strip()
    with adb() as conn:
        cur = conn.cursor()
        # PARENTS ONLY. A shorts run can create many child projects, each with a
        # chat that opens on an assistant greeting and an EDL seeded by the
        # pipeline — in this list they read as "a session with no upload
        # that somehow holds a fully edited video" (the Aug 9 confusion).
        # The child projects still exist; they surface as shorts_count here
        # and as a children list in the project detail, where their real
        # story (cut from the parent) is visible.
        cur.execute("""
            SELECT p.id, p.title, p.kind, p.created_at, u.email,
                   u.trial_status, u.trial_started_at, u.trial_plan,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user') AS messages,
                   (SELECT MAX(v.updated_at) FROM video_jobs v
                    WHERE v.project_id = p.id) AS last_job,
                   (SELECT COUNT(*) FROM edls e
                    WHERE e.project_id = p.id) AS versions,
                   -- Did this customer EXPORT (finish a video)? A successful
                   -- export = a 'final' render job that reached 'done'.
                   (SELECT COUNT(*) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS exports,
                   (SELECT MAX(vf.updated_at) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS last_export,
                   (SELECT COUNT(*) FROM projects c
                    WHERE c.parent_project_id = p.id) AS shorts_count,
                   -- Round 101: how many times this project's owner met the
                   -- trial wall here (free account past its first edited
                   -- video, prompt answered with the cards instead of a
                   -- turn), and when last. The count is intent: every
                   -- resend re-raises the cards and re-records.
                   (SELECT COUNT(*) FROM client_events ce
                    WHERE ce.project_id = p.id
                      AND ce.kind = 'trial_gate_shown') AS trial_walls,
                   (SELECT MAX(ce.created_at) FROM client_events ce
                    WHERE ce.project_id = p.id
                      AND ce.kind = 'trial_gate_shown') AS last_trial_wall,
                   -- Funnel step immediately to the right of the wall in
                   -- admin: did this project's cards lead to a real Paddle
                   -- trial? Credit only the most recent wall before Paddle
                   -- opened the trial, so one checkout cannot make several
                   -- of the user's projects look converted.
                   (u.trial_started_at IS NOT NULL AND p.id = (
                       SELECT ce.project_id FROM client_events ce
                       WHERE ce.user_id = u.id
                         AND ce.kind = 'trial_gate_shown'
                         AND ce.created_at <=
                             u.trial_started_at AT TIME ZONE 'UTC'
                       ORDER BY ce.created_at DESC, ce.id DESC
                       LIMIT 1
                   )) AS trial_started_after_wall,
                   -- User activity inside the children rolls up so a parent
                   -- whose owner refined short #3 by chat doesn't read as
                   -- untouched.
                   (SELECT COUNT(*) FROM chat_messages cm2
                    JOIN projects c2 ON c2.chat_session_id = cm2.session_id
                    WHERE c2.parent_project_id = p.id
                      AND cm2.role='user') AS shorts_messages,
                   """ + _TIMING_COLS + """
            FROM projects p JOIN users u ON u.id = p.user_id
            """ + _PROJECT_TIMINGS + """
            WHERE p.parent_project_id IS NULL
              AND (u.email ILIKE %s OR p.title ILIKE %s)
            ORDER BY p.id DESC LIMIT 100
        """, (f"%{search}%", f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"projects": [
        {"id": r["id"], "title": r["title"], "email": r["email"],
         "kind": r["kind"],
         "messages": r["messages"], "versions": r["versions"],
         "exports": r["exports"],
         "shorts_count": r["shorts_count"],
         "shorts_messages": r["shorts_messages"],
         "trial_walls": r["trial_walls"],
         "last_trial_wall": (r["last_trial_wall"].isoformat()
                             if r["last_trial_wall"] else None),
         "trial_started_after_wall": bool(r["trial_started_after_wall"]),
         "trial_started_at": (r["trial_started_at"].isoformat()
                              if r["trial_started_at"] else None),
         "trial_status": r["trial_status"],
         "trial_plan": r["trial_plan"],
         "created_at": r["created_at"].isoformat(),
         "last_job": r["last_job"].isoformat() if r["last_job"] else None,
         "last_export": (r["last_export"].isoformat()
                         if r["last_export"] else None),
         **_timing_out(r)}
        for r in rows]})


@admin_video_bp.route("/admin/video/timings", methods=["GET"])
@admin_required
def video_timings():
    """Every project that has a video, as points for the wait-vs-length chart.

    Deliberately a WIDER set than the project list (which is capped at 100 and
    filtered by a search box): the shape only shows up across the whole corpus,
    and the single most useful thing it shows is where the line STOPS being a
    line — the length past which a wait becomes a churn."""
    try:
        days = max(1, min(int(request.args.get("days") or 90), 3650))
    except (TypeError, ValueError):
        days = 90
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.created_at, u.email, """ + _TIMING_COLS + """
            FROM projects p JOIN users u ON u.id = p.user_id
            """ + _PROJECT_TIMINGS + """
            WHERE p.created_at > NOW() - (%s || ' days')::interval
              AND o.duration_s IS NOT NULL
            ORDER BY p.id DESC LIMIT 1000
        """, (str(days),))
        rows = cur.fetchall()
    return jsonify({"days": days, "points": [
        {"id": r["id"], "email": r["email"],
         "created_at": r["created_at"].isoformat(), **_timing_out(r)}
        for r in rows]})


PREVIEWABLE = ("thumb", "sheet", "render", "proxy", "image_ref", "original",
               "music", "video_clip")


@admin_video_bp.route("/admin/video/projects/<int:project_id>",
                      methods=["GET"])
@admin_required
def video_project_detail(project_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT p.*, u.email, u.trial_status,
                              u.trial_started_at, u.trial_plan,
                              (u.trial_started_at IS NOT NULL AND p.id = (
                                  SELECT ce.project_id
                                  FROM client_events ce
                                  WHERE ce.user_id = u.id
                                    AND ce.kind = 'trial_gate_shown'
                                    AND ce.created_at <=
                                        u.trial_started_at AT TIME ZONE 'UTC'
                                  ORDER BY ce.created_at DESC, ce.id DESC
                                  LIMIT 1
                              )) AS trial_started_after_wall
                       FROM projects p
                       JOIN users u ON u.id = p.user_id
                       WHERE p.id = %s""", (project_id,))
        p = cur.fetchone()
        if not p:
            return jsonify({"error": "Project not found"}), 404

        cur.execute("""SELECT id, role, content, meta, created_at
                       FROM chat_messages WHERE session_id = %s
                       ORDER BY id ASC LIMIT 2000""", (p["chat_session_id"],))
        messages = cur.fetchall()

        cur.execute("""SELECT version, json, created_by, created_at
                       FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 200""", (project_id,))
        edls = cur.fetchall()

        cur.execute("""SELECT id, type, state, progress, error, payload,
                              result, attempts, created_at, updated_at
                       FROM video_jobs WHERE project_id = %s
                       ORDER BY id DESC LIMIT 300""", (project_id,))
        jobs = cur.fetchall()

        cur.execute("""SELECT * FROM assets WHERE project_id = %s
                       ORDER BY id DESC LIMIT 400""", (project_id,))
        assets = cur.fetchall()

        cur.execute("""SELECT COUNT(*) AS n FROM llm_calls
                       WHERE project_id = %s""", (project_id,))
        llm_count = cur.fetchone()["n"]

        cur.execute("""SELECT id, state, error, payload, result,
                              created_at, updated_at
                       FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                       ORDER BY id ASC""", (project_id,))
        turn_jobs = cur.fetchall()

        cur.execute("""SELECT id, job_id, purpose, model, prompt_tokens,
                              completion_tokens, created_at
                       FROM llm_calls
                       WHERE project_id = %s AND job_id IS NOT NULL
                       ORDER BY id ASC""", (project_id,))
        llm_summaries = cur.fetchall()

        cur.execute(f"""SELECT cm.id FROM chat_messages cm
                        WHERE cm.session_id = %s AND cm.role = 'user'
                          AND {UNSERVED_EXISTS}
                        ORDER BY cm.id ASC""", (p["chat_session_id"],))
        unserved_ids = [r["id"] for r in cur.fetchall()]

        cur.execute("""SELECT id, type, progress, payload,
                              EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_s
                       FROM video_jobs
                       WHERE project_id = %s AND state IN ('running', 'queued')
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        _live = cur.fetchone()
        live_turn = {
            "job_id": _live["id"], "type": _live["type"],
            "progress": _live["progress"],
            "message_id": (_live["payload"] or {}).get("message_id"),
            "running_for_s": int(_live["age_s"] or 0),
        } if _live else None

        # Thumbnails + contact sheets have no asset rows — their keys live
        # in the index JSON and in render results. Surface them here so the
        # admin grid can show everything.
        cur.execute("""SELECT i.json FROM indexes i
                       WHERE i.video_sha256 = (
                           SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        idx_row = cur.fetchone()

    # Who triggered each render? A render asset carries no trigger of its own —
    # that lives on the render JOB's payload (`source: 'user_edit'` for a studio
    # timeline edit; absent for an agent-initiated render). Map the job back to
    # the asset it produced (result.render_asset_id) so the admin card can say
    # "USER edited" vs "AGENT rendered" instead of blaming the agent for the
    # customer's own edits, and flag `force` re-encodes (the studio's
    # "couldn't load" recovery) so a burst of identical re-renders reads clearly.
    render_trigger = {}
    for j in jobs:
        if j["type"] not in ("preview", "final"):
            continue
        res = j.get("result") if isinstance(j.get("result"), dict) else {}
        pay = j.get("payload") if isinstance(j.get("payload"), dict) else {}
        aid = res.get("render_asset_id")
        if aid is None:
            continue
        render_trigger[aid] = {
            "source": "user_edit" if pay.get("source") == "user_edit"
                      else "agent",
            "forced": bool(pay.get("force")),
        }

    out_assets = []
    for a in assets:
        row = {"id": a["id"], "kind": a["kind"],
               "storage_key": a["storage_key"], "bytes": a["bytes"],
               "duration_s": a["duration_s"], "width": a["width"],
               "height": a["height"], "meta": a.get("meta") or {},
               "created_at": a["created_at"].isoformat()}
        if a["id"] in render_trigger:
            row["trigger"] = render_trigger[a["id"]]
        if a["kind"] in PREVIEWABLE:
            row["url"] = _presign(a["storage_key"])
        out_assets.append(row)

    seen_keys = {a["storage_key"] for a in assets}
    idx = (idx_row or {}).get("json") or {}
    for skey in idx.get("sheet_keys") or []:
        if skey not in seen_keys:
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": skey, "meta": {},
                               "url": _presign(skey)})
    # v10: the filmstrip tiles ARE the visual index — show them where the
    # contact sheets used to appear.
    for tkey in idx.get("tile_keys") or []:
        if tkey not in seen_keys:
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": tkey, "meta": {"tile": True},
                               "url": _presign(tkey)})
    for shot in idx.get("shots") or []:
        tkey = shot.get("thumb_key")
        if tkey and tkey not in seen_keys:
            out_assets.append({"id": None, "kind": "thumb",
                               "storage_key": tkey,
                               "meta": {"shot": shot.get("id")},
                               "url": _presign(tkey)})
    for a in assets:
        rkey = (a.get("meta") or {}).get("sheet_key")
        if rkey and rkey not in seen_keys:
            seen_keys.add(rkey)
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": rkey,
                               "meta": {"render_asset": a["id"]},
                               "url": _presign(rkey)})

    # Group the session into agent turns: a turn owns the window from its
    # triggering user message up to (not including) the next user message.
    # Rows before the first turn (canned replies, index_ready) stay only in
    # the flat "messages" array above.
    llm_by_job = {}
    for r in llm_summaries:
        llm_by_job.setdefault(r["job_id"], []).append(
            {"id": r["id"], "purpose": r["purpose"], "model": r["model"],
             "prompt_tokens": r["prompt_tokens"],
             "completion_tokens": r["completion_tokens"],
             "created_at": r["created_at"].isoformat()})

    msg_by_id = {m["id"]: m for m in messages}
    user_msg_ids = sorted(m["id"] for m in messages if m["role"] == "user")

    turns = []
    for t in turn_jobs:
        payload = t.get("payload") if isinstance(t.get("payload"), dict) \
            else {}
        res = t.get("result") if isinstance(t.get("result"), dict) else {}
        try:
            mid = int(payload.get("message_id"))
        except (TypeError, ValueError):
            mid = None
        um = msg_by_id.get(mid)
        activity, assistant_msgs = [], []
        if um:
            nxt = next((i for i in user_msg_ids if i > um["id"]), None)
            for m in messages:  # ordered by id ASC
                if m["id"] <= um["id"]:
                    continue
                if nxt is not None and m["id"] >= nxt:
                    break
                if m["role"] == "activity":
                    activity.append(_msg_brief(m))
                elif m["role"] == "assistant":
                    assistant_msgs.append(_msg_brief(m))
        try:
            edl_version = int(res["edl_version"]) \
                if res.get("edl_version") is not None else None
        except (TypeError, ValueError):
            edl_version = None
        turns.append({
            "job_id": t["id"], "state": t["state"],
            "created_at": t["created_at"].isoformat(),
            "updated_at": t["updated_at"].isoformat(),
            "user_message": _msg_brief(um) if um else None,
            "activity": activity,
            "assistant_messages": assistant_msgs,
            "llm_calls": llm_by_job.get(t["id"], []),
            "honesty": res.get("honesty"),
            "timings": res.get("timings"),
            "credits_charged": res.get("credits_charged"),
            "edl_version": edl_version,
            "error": t["error"],
        })

    # Export signal for THIS conversation: successful exports = 'final' jobs
    # that reached 'done'; also surface attempts (incl. failed) to spot a
    # customer who TRIED to export but the render failed.
    final_done = [j for j in jobs if j["type"] == "final"
                  and j["state"] == "done"]
    final_all = [j for j in jobs if j["type"] == "final"]
    last_export = max((j["updated_at"] for j in final_done), default=None)
    exports = {
        "count": len(final_done),
        "attempts": len(final_all),
        "failed": len([j for j in final_all if j["state"] == "failed"]),
        "last_at": last_export.isoformat() if last_export else None,
    }

    # Uploads that never became an asset, in the project they were aimed at.
    # Shown beside the chat because that is where the absence shows: a project
    # whose whole story is "user tried to add a video and nothing happened".
    with adb() as conn2:
        cur2 = conn2.cursor()
        cur2.execute("""SELECT id, kind, detail, created_at, project_id
                        FROM client_events
                        WHERE project_id = %s AND kind = ANY(%s)
                        ORDER BY id DESC LIMIT 50""",
                     (project_id, list(UPLOAD_EVENT_KINDS)))
        upload_events = [_upload_event_row(e) for e in cur2.fetchall()]
        # The trial wall's story for THIS project: how many times the cards
        # rose instead of a turn, first and last time. Zero rows → null.
        cur2.execute("""SELECT COUNT(*) AS n, MIN(created_at) AS first_at,
                               MAX(created_at) AS last_at
                        FROM client_events
                        WHERE project_id = %s
                          AND kind = 'trial_gate_shown'""", (project_id,))
        tw = cur2.fetchone()
        trial_wall = ({"count": tw["n"],
                       "first_at": tw["first_at"].isoformat(),
                       "last_at": tw["last_at"].isoformat()}
                      if tw and tw["n"] else None)

    # Family: a shorts parent lists its children; a child names its parent.
    # This is what turns "a session with no upload but a fully edited video"
    # back into a story — the child was CUT from the parent by the shorts
    # pipeline, and its chat starts at the greeting by design.
    with adb() as conn3:
        cur3 = conn3.cursor()
        cur3.execute("""SELECT c.id, c.title, c.kind, c.created_at,
                               (SELECT COUNT(*) FROM chat_messages cm
                                WHERE cm.session_id = c.chat_session_id
                                  AND cm.role='user') AS messages,
                               (SELECT COUNT(*) FROM video_jobs vf
                                WHERE vf.project_id = c.id
                                  AND vf.type='final'
                                  AND vf.state='done') AS exports
                        FROM projects c WHERE c.parent_project_id = %s
                        ORDER BY c.id""", (project_id,))
        children = [{"id": c["id"], "title": c["title"], "kind": c["kind"],
                     "messages": c["messages"], "exports": c["exports"],
                     "created_at": c["created_at"].isoformat()}
                    for c in cur3.fetchall()]
        parent = None
        if p.get("parent_project_id"):
            cur3.execute("SELECT id, title FROM projects WHERE id = %s",
                         (p["parent_project_id"],))
            pr = cur3.fetchone()
            if pr:
                parent = {"id": pr["id"], "title": pr["title"]}

        # The board itself — what the shorts run actually produced, in board
        # order, each clip WATCHABLE from the parent page. Before this the
        # parent inspector showed children only as title pills, so a shorts
        # session's output was invisible without opening every child project
        # one by one (owner request, 2026-08-11).
        shorts_board = None
        clips = (((p.get("meta") or {}).get("shorts") or {})
                 .get("clips")) or []
        if clips:
            child_ids = [int(c["child_project_id"]) for c in clips
                         if c.get("child_project_id")]
            renders, final_jobs = {}, {}
            if child_ids:
                cur3.execute("""
                    SELECT DISTINCT ON (a.project_id, a.meta->>'variant')
                           a.project_id, a.meta->>'variant' AS variant,
                           a.storage_key,
                           a.meta->>'edl_version' AS edl_v
                    FROM assets a
                    WHERE a.project_id = ANY(%s) AND a.kind = 'render'
                    ORDER BY a.project_id, a.meta->>'variant', a.id DESC""",
                             (child_ids,))
                for r in cur3.fetchall():
                    renders.setdefault(r["project_id"], {})[r["variant"]] = r
                cur3.execute("""
                    SELECT DISTINCT ON (project_id)
                           project_id, state, progress, error
                    FROM video_jobs
                    WHERE project_id = ANY(%s) AND type = 'final'
                    ORDER BY project_id, id DESC""", (child_ids,))
                final_jobs = {r["project_id"]: r for r in cur3.fetchall()}
            child_meta = {c["id"]: c for c in children}
            shorts_board = []
            ordered = sorted(clips, key=lambda c: (
                c.get("order", 10 ** 6),
                c.get("child_project_id") or 10 ** 12))
            for i, cclip in enumerate(ordered, 1):
                cid = cclip.get("child_project_id")
                dur = None
                try:
                    dur = round(float(cclip["end"]) - float(cclip["start"]),
                                1)
                except (KeyError, TypeError, ValueError):
                    pass
                rend = renders.get(cid) or {}
                best = rend.get("final") or rend.get("preview")
                fj = final_jobs.get(cid)
                shorts_board.append({
                    "card": i,
                    "child_project_id": cid,
                    "title": cclip.get("title"),
                    "duration_s": dur,
                    "edl_version": cclip.get("edl_version"),
                    "seed_error": cclip.get("seed_error"),
                    "messages": (child_meta.get(cid) or {}).get("messages"),
                    "final_job": ({"state": fj["state"],
                                   "progress": fj["progress"],
                                   "error": fj["error"]} if fj else None),
                    "render": ({"variant": best["variant"],
                                "edl_version": best["edl_v"],
                                "url": _presign(best["storage_key"])}
                               if best else None),
                })

    return jsonify({
        "project": {"id": p["id"], "title": p["title"], "email": p["email"],
                    "kind": p.get("kind"),
                    "created_at": p["created_at"].isoformat()},
        "children": children,
        "parent": parent,
        "shorts_board": shorts_board,
        "exports": exports,
        "trial_wall": trial_wall,
        "trial_conversion": ({
            "started_at": p["trial_started_at"].isoformat(),
            "status": p["trial_status"],
            "plan": p["trial_plan"],
        } if p.get("trial_started_after_wall") and p.get("trial_started_at")
          else None),
        "upload_events": upload_events,
        "upload_failures": len([e for e in upload_events
                                if e["kind"] in UPLOAD_FAILURE_KINDS]),
        "messages": [
            {"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"], "created_at": m["created_at"].isoformat()}
            for m in messages],
        "edls": [
            {"version": e["version"], "json": e["json"],
             "created_by": e["created_by"],
             "created_at": e["created_at"].isoformat()} for e in edls],
        "jobs": [
            {"id": j["id"], "type": j["type"], "state": j["state"],
             "progress": j["progress"], "error": j["error"],
             "payload": j["payload"], "result": j["result"],
             "attempts": j["attempts"],
             "created_at": j["created_at"].isoformat(),
             "updated_at": j["updated_at"].isoformat()} for j in jobs],
        "assets": out_assets,
        "llm_call_count": llm_count,
        "turns": turns,
        "unserved_message_ids": unserved_ids,
        # A turn that is STILL WORKING looks exactly like a turn that was
        # ignored: the user's message sits there with no assistant reply under
        # it. A founder read that as "this customer got no response" on
        # 2026-07-25 while the agent was 90 seconds into a two-minute edit that
        # then answered fine. Name the in-flight turn so the two are never
        # confused again.
        "live_turn": live_turn,
    })


# ── Upload failures (round 57) ───────────────────────────────────────────
# A file the user tried to give us that never became an asset. This is the
# blind spot that mattered most: 201 of 203 index jobs have ever succeeded, so
# nothing server-side was failing — the losses were in the browser, where a
# size or duration refusal fired before a project existed and left no trace at
# all. 65 of 214 accounts have no project, and until now every one of them read
# as "signed up and never tried".
UPLOAD_FAILURE_KINDS = ("upload_rejected", "upload_failed")
UPLOAD_EVENT_KINDS = UPLOAD_FAILURE_KINDS + ("upload_started",)


def _upload_event_row(e):
    d = e["detail"] or {}
    return {
        "id": e["id"], "kind": e["kind"],
        "created_at": e["created_at"].isoformat(),
        "project_id": e.get("project_id"),
        "filename": d.get("filename"),
        "bytes": d.get("bytes"),
        "duration_s": d.get("duration_s"),
        "reason": d.get("reason"),
        "stage": d.get("stage"),
        "origin": d.get("origin"),
        "detail": d,
    }


@admin_video_bp.route("/admin/video/upload_failures", methods=["GET"])
@admin_required
def video_upload_failures():
    """Every upload that did not land, newest first, with who and what.

    Includes `upload_started` so the ratio is readable: a start with no
    matching asset is an upload that died in transit, which no error message
    anywhere would ever have shown us.
    """
    limit = min(int(request.args.get("limit", 200)), 500)
    days = min(int(request.args.get("days", 30)), 365)
    failures_only = request.args.get("failures_only", "1") != "0"
    kinds = UPLOAD_FAILURE_KINDS if failures_only else UPLOAD_EVENT_KINDS
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT e.id, e.kind, e.detail, e.created_at,
                              e.project_id, e.user_id, u.email, p.title
                       FROM client_events e
                       LEFT JOIN users u ON u.id = e.user_id
                       LEFT JOIN projects p ON p.id = e.project_id
                       WHERE e.kind = ANY(%s)
                         AND e.created_at > NOW() - (%s || ' days')::interval
                       ORDER BY e.id DESC LIMIT %s""",
                    (list(kinds), str(days), limit))
        rows = cur.fetchall()

        cur.execute("""SELECT kind,
                              COUNT(*) AS n,
                              COUNT(DISTINCT user_id) AS users
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY kind""",
                    (list(UPLOAD_EVENT_KINDS), str(days)))
        totals = {r["kind"]: {"events": r["n"], "users": r["users"]}
                  for r in cur.fetchall()}

        # The reasons, ranked. This is the answer to "what is actually
        # stopping people", which is the whole point of the surface.
        cur.execute("""SELECT COALESCE(detail->>'reason', '(none)') AS reason,
                              COUNT(*) AS n, COUNT(DISTINCT user_id) AS users
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 20""",
                    (list(UPLOAD_FAILURE_KINDS), str(days)))
        by_reason = [{"reason": r["reason"], "events": r["n"],
                      "users": r["users"]} for r in cur.fetchall()]

    return jsonify({
        "days": days,
        "totals": totals,
        "by_reason": by_reason,
        "events": [dict(_upload_event_row(e),
                        user_id=e["user_id"], email=e["email"],
                        project_title=e["title"]) for e in rows],
    })


# The edit-to-file funnel has two client-only boundaries that video_jobs can
# never answer: did the person press Export, and did the browser receive/use
# the download URL? Keep the authoritative worker/server boundaries beside
# them so the exact drop-off is visible on the first admin screen.
EXPORT_FUNNEL_STAGES = (
    ("project_ready", "Project ready"),
    ("first_prompt_sent", "First prompt"),
    ("export_clicked", "Export clicked"),
    ("export_job_started", "Render started"),
    ("export_render_done", "Render finished"),
    ("download_url_ready", "Download URL ready"),
    ("download_triggered", "Browser download triggered"),
)
EXPORT_FAILURE_KINDS = ("export_blocked", "download_failed")


@admin_video_bp.route("/admin/video/export_funnel", methods=["GET"])
@admin_required
def video_export_funnel():
    """Recent edit-to-download stages plus ranked blockers.

    Counts are deliberately returned as events, projects and users. Repeated
    download clicks are useful reliability evidence but must not masquerade
    as additional people converting.
    """
    try:
        days = min(max(int(request.args.get("days", 30)), 1), 365)
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    except (TypeError, ValueError):
        days, limit = 30, 50
    stage_kinds = [kind for kind, _label in EXPORT_FUNNEL_STAGES]
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT kind, COUNT(*) AS events,
                              COUNT(DISTINCT project_id) FILTER
                                  (WHERE project_id IS NOT NULL) AS projects,
                              COUNT(DISTINCT user_id) AS users,
                              MIN(created_at) AS first_seen,
                              MAX(created_at) AS last_seen
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY kind""",
                    (stage_kinds, str(days)))
        counts = {row["kind"]: row for row in cur.fetchall()}

        cur.execute("""SELECT kind,
                              COALESCE(NULLIF(detail->>'code', ''),
                                       NULLIF(detail->>'reason', ''),
                                       '(unspecified)') AS reason,
                              COUNT(*) AS events,
                              COUNT(DISTINCT project_id) FILTER
                                  (WHERE project_id IS NOT NULL) AS projects,
                              COUNT(DISTINCT user_id) AS users
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY kind, 2
                       ORDER BY events DESC LIMIT 20""",
                    (list(EXPORT_FAILURE_KINDS), str(days)))
        reasons = cur.fetchall()

        cur.execute("""SELECT e.id, e.kind, e.detail, e.created_at,
                              e.project_id, e.user_id, u.email, p.title
                       FROM client_events e
                       LEFT JOIN users u ON u.id = e.user_id
                       LEFT JOIN projects p ON p.id = e.project_id
                       WHERE e.kind = ANY(%s)
                         AND e.created_at > NOW() - (%s || ' days')::interval
                       ORDER BY e.id DESC LIMIT %s""",
                    (list(EXPORT_FAILURE_KINDS), str(days), limit))
        failures = cur.fetchall()

    stages = []
    for kind, label in EXPORT_FUNNEL_STAGES:
        row = counts.get(kind) or {}
        stages.append({
            "kind": kind, "label": label,
            "events": int(row.get("events") or 0),
            "projects": int(row.get("projects") or 0),
            "users": int(row.get("users") or 0),
            "first_seen": row.get("first_seen").isoformat()
                if row.get("first_seen") else None,
            "last_seen": row.get("last_seen").isoformat()
                if row.get("last_seen") else None,
        })
    return jsonify({
        "days": days,
        "stages": stages,
        "by_reason": [{"kind": r["kind"], "reason": r["reason"],
                       "events": int(r["events"] or 0),
                       "projects": int(r["projects"] or 0),
                       "users": int(r["users"] or 0)} for r in reasons],
        "failures": [{
            "id": e["id"], "kind": e["kind"],
            "detail": e.get("detail") or {},
            "created_at": e["created_at"].isoformat(),
            "project_id": e.get("project_id"),
            "project_title": e.get("title"),
            "user_id": e.get("user_id"), "email": e.get("email"),
        } for e in failures],
    })


@admin_video_bp.route("/admin/video/projects/<int:project_id>/index",
                      methods=["GET"])
@admin_required
def video_project_index(project_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT i.json, i.pipeline_version, i.created_at
                       FROM indexes i
                       WHERE i.video_sha256 = (
                           SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "No index for this project"}), 404
    return jsonify({"index": row["json"],
                    "pipeline_version": row["pipeline_version"],
                    "created_at": row["created_at"].isoformat()})


@admin_video_bp.route("/admin/video/projects/<int:project_id>/llm_calls",
                      methods=["GET"])
@admin_required
def video_project_llm_calls(project_id):
    page = max(1, request.args.get("page", type=int) or 1)
    per = 20
    job_id = request.args.get("job_id", type=int)
    purpose = request.args.get("purpose")
    where = ["project_id = %s"]
    params = [project_id]
    if job_id is not None:
        where.append("job_id = %s")
        params.append(job_id)
    if purpose:
        where.append("purpose = %s")
        params.append(purpose)
    where_sql = " AND ".join(where)
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM llm_calls WHERE {where_sql}",
                    params)
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT id, job_id, purpose, model, request, response,
                               prompt_tokens, completion_tokens, created_at
                        FROM llm_calls WHERE {where_sql}
                        ORDER BY id DESC LIMIT %s OFFSET %s""",
                    params + [per, (page - 1) * per])
        rows = cur.fetchall()

    def _vision_urls(req):
        # Vision requests record image STORAGE KEYS (never bytes) — presign
        # them so the admin can see the exact tiles the model saw.
        names = (req or {}).get("images") or []
        urls = {}
        for n in names:
            if isinstance(n, str) and "/" in n:
                try:
                    urls[n] = storage.presign_get(
                        n, expires=storage.PRESIGN_EXPIRY)
                except Exception:
                    urls[n] = None
        return urls or None

    calls = []
    for r in rows:
        c = {**r, "created_at": r["created_at"].isoformat()}
        if (r["purpose"] or "").startswith("vision"):
            c["image_urls"] = _vision_urls(r["request"])
        calls.append(c)
    return jsonify({"total": total, "page": page, "per_page": per,
                    "calls": calls})


@admin_video_bp.route("/admin/video/costs", methods=["GET"])
@admin_required
def video_costs():
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.email, DATE(lc.created_at) AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc
            JOIN projects p ON p.id = lc.project_id
            JOIN users u ON u.id = p.user_id
            WHERE lc.created_at > NOW() - INTERVAL '30 days'
            GROUP BY u.email, DATE(lc.created_at)
            ORDER BY day DESC, est_cost DESC
            LIMIT 500
        """)
        rows = cur.fetchall()
        cur.execute(f"""
            SELECT lc.purpose, COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc GROUP BY lc.purpose ORDER BY est_cost DESC
        """)
        by_purpose = cur.fetchall()
        # Per MODEL, because that is the axis spend now splits on: free
        # accounts and paying ones can answer on different providers, and the
        # reasoning column is the evidence for whether a provider bills
        # thinking tokens on top of the completion (model_prices.py).
        cur.execute(f"""
            SELECT COALESCE(NULLIF(lc.model, ''), 'unknown') AS model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(COALESCE(
                       (lc.response->>'cached_in')::float, 0)), 0) AS cached_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   COALESCE(SUM(COALESCE(
                       (lc.response->>'reasoning_out')::float, 0)), 0)
                       AS reasoning_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc
            WHERE lc.created_at > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY est_cost DESC
        """)
        by_model = cur.fetchall()
    return jsonify({
        "pricing": {"in_per_m": PRICE_IN_PER_M, "out_per_m": PRICE_OUT_PER_M,
                    "cached_in_per_m": PRICE_CACHED_IN_PER_M,
                    "models": model_prices.MODEL_PRICES,
                    "note": "each row is priced from its OWN model; the "
                            "in/out/cached numbers above are only the fallback "
                            "for a model not in the table. Cache-hit input is "
                            "priced separately; reasoning tokens are charged "
                            "only where the provider bills them on top of "
                            "completion_tokens."},
        "daily": [{**r, "day": r["day"].isoformat(),
                   "est_cost": round(float(r["est_cost"] or 0), 4)}
                  for r in rows],
        "by_purpose": [{**r, "est_cost": round(float(r["est_cost"] or 0), 4)}
                       for r in by_purpose],
        "by_model": [{**r,
                      "cached_in": int(float(r["cached_in"] or 0)),
                      "reasoning_out": int(float(r["reasoning_out"] or 0)),
                      "est_cost": round(float(r["est_cost"] or 0), 4)}
                     for r in by_model],
    })


UPLOAD_KINDS = ("original", "music", "image_ref", "video_clip")
# Repainted copies of a source (round 39 erase). Deliberately NOT in
# UPLOAD_KINDS — nobody uploaded them — but they are full-size video objects,
# so they belong in the per-user media view where their storage is visible.
CLEAN_KINDS = ("clean_source", "clean_proxy")


def _machine_made(alias):
    """SQL predicate: this asset was made BY THE PRODUCT, not uploaded.

    Every asset the agent synthesizes, generates, downloads from a pasted link
    or records from a website lands in the same kinds a person's own files do —
    a locally-built glitch card and a user's own clip are both 'video_clip'.
    Counting on kind alone made the admin report that a user "uploaded
    corrupt-digital.mp4" the day add_corrupt_screen shipped, when in fact the
    agent had built it inside their project. The producing tools each stamp
    meta: generated (colour/title cards, glitch screens, generate_image,
    generate_video), fetched (fetch_url), recorded (record_website).

    Compared as text, not cast to bool: a cast raises on any meta value that
    isn't a bool literal, which would take down the whole admin page over one
    odd row written by a future tool.
    """
    return (f"(COALESCE({alias}.meta->>'generated', '') = 'true' "
            f"OR COALESCE({alias}.meta->>'fetched', '') = 'true' "
            f"OR COALESCE({alias}.meta->>'recorded', '') = 'true')")


def _is_machine(a):
    """Python twin of _machine_made, for rows already fetched."""
    m = a.get("meta") or {}
    return bool(m.get("generated") or m.get("fetched") or m.get("recorded"))


def _asset_row(a):
    """One media row for the admin, carrying WHERE it came from.

    `origin` is the honest provenance line: which tool/model made it, or the
    URL it was pulled from — so a glitch card reads "local:glitch-digital" and
    a downloaded clip shows its source page, instead of both looking like
    files the customer chose to upload.
    """
    m = a.get("meta") or {}
    origin = None
    if m.get("generated"):
        origin = m.get("model") or "generated"
    elif m.get("fetched"):
        origin = m.get("source_url") or "fetched from a link"
    elif m.get("recorded"):
        origin = m.get("source_url") or "website capture"
    return {"id": a["id"], "project_id": a["project_id"],
            "kind": a["kind"],
            "filename": m.get("filename"),
            "bytes": a["bytes"], "duration_s": a["duration_s"],
            "width": a["width"], "height": a["height"],
            "created_at": a["created_at"].isoformat(),
            "origin": origin,
            "url": _presign(a["storage_key"])}


@admin_video_bp.route("/admin/video/users", methods=["GET"])
@admin_required
def video_users():
    search = (request.args.get("search") or "").strip()
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.id, u.email, u.created_at, u.plan,
                   u.credits_daily, u.credits_bonus, u.credits_monthly,
                   u.credits_balance,
                   pr.n AS projects,
                   COALESCE(m.msgs, 0) AS messages,
                   COALESCE(m.unserved, 0) AS unserved,
                   COALESCE(j.turns, 0) AS turns,
                   COALESCE(j.exports, 0) AS exports,
                   COALESCE(a.uploads, 0) AS uploads,
                   COALESCE(a.generated, 0) AS generated,
                   COALESCE(a.bytes, 0) AS storage_bytes,
                   COALESCE(l.tokens_in, 0) AS tokens_in,
                   COALESCE(l.tokens_out, 0) AS tokens_out,
                   COALESCE(l.est_cost, 0) AS est_cost,
                   GREATEST(m.last, j.last, a.last) AS last_active
            FROM users u
            JOIN (SELECT user_id, COUNT(*) AS n FROM projects
                  GROUP BY user_id) pr ON pr.user_id = u.id
            LEFT JOIN (SELECT p2.user_id,
                              COUNT(*) FILTER (WHERE cm.role='user') AS msgs,
                              COUNT(*) FILTER (WHERE cm.role='user'
                                  AND {UNSERVED_EXISTS}) AS unserved,
                              MAX(cm.created_at) AS last
                       FROM chat_messages cm
                       JOIN projects p2 ON p2.chat_session_id = cm.session_id
                       GROUP BY p2.user_id) m ON m.user_id = u.id
            LEFT JOIN (SELECT user_id,
                              COUNT(*) FILTER (WHERE type='agent_turn')
                                  AS turns,
                              COUNT(*) FILTER (WHERE type='final'
                                  AND state='done') AS exports,
                              MAX(updated_at) AS last
                       FROM video_jobs GROUP BY user_id) j ON j.user_id = u.id
            LEFT JOIN (SELECT p3.user_id,
                              COUNT(*) FILTER (WHERE ast.kind IN %s
                                  AND NOT {_machine_made('ast')}) AS uploads,
                              COUNT(*) FILTER (WHERE {_machine_made('ast')})
                                  AS generated,
                              SUM(ast.bytes)::bigint AS bytes,
                              MAX(ast.created_at) AS last
                       FROM assets ast
                       JOIN projects p3 ON p3.id = ast.project_id
                       GROUP BY p3.user_id) a ON a.user_id = u.id
            LEFT JOIN (SELECT p4.user_id,
                              SUM(lc.prompt_tokens) AS tokens_in,
                              SUM(lc.completion_tokens) AS tokens_out,
                              """ + _cost_expr("lc") + """ AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            WHERE u.email ILIKE %s
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """, (UPLOAD_KINDS, f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"users": [
        {"id": r["id"], "email": r["email"],
         "created_at": r["created_at"].isoformat(),
         "plan": r["plan"],
         "credits": {"daily": float(r["credits_daily"] or 0),
                     "bonus": float(r["credits_bonus"] or 0),
                     "monthly": float(r["credits_monthly"] or 0),
                     "balance": float(r["credits_balance"] or 0)},
         "projects": r["projects"], "messages": r["messages"],
         "turns": r["turns"], "exports": r["exports"],
         "unserved": r["unserved"],
         "uploads": r["uploads"],
         "generated": r["generated"],
         "storage_bytes": int(r["storage_bytes"] or 0),
         "tokens_in": int(r["tokens_in"] or 0),
         "tokens_out": int(r["tokens_out"] or 0),
         "est_cost": round(float(r["est_cost"] or 0), 4),
         "last_active": r["last_active"].isoformat()
             if r["last_active"] else None}
        for r in rows]})


COHORT_STAGES = [
    ("signed_up", "Signed up"),
    ("uploaded", "Uploaded a video"),
    ("messaged", "Messaged the editor"),
    ("exported", "Exported a video"),
    ("trial", "Started a trial"),
    ("paid", "Paid"),
]

# The stages worth plotting a GROWTH rate for. Deliberately not all six: trial
# and paid are single digits per week right now, and a rate computed on 1 -> 3
# reads as "+200% growth" next to a signup line moving 10%, which makes the
# chart a liar at a glance. They stay in the funnel table where the raw counts
# are visible beside them.
GROWTH_STAGES = [
    ("signed_up", "Signups"),
    ("uploaded", "Uploaded"),
    ("messaged", "Messaged"),
    ("exported", "Exported"),
]


def _growth_percent(now, was):
    """Comparable-period growth, preserving unknown instead of infinity."""
    if was is None or was == 0:
        return None
    return round((now - was) / was * 100.0, 1)


def _attach_growth(cohorts):
    """Period-over-period growth %, per stage, onto each cohort row.

    Growth is the derivative of the funnel the table already shows, so it is
    computed HERE rather than in the browser: the lead-in rows are real zeros
    that must not be divided by, and "no previous period" and "grew 0%" are
    different facts that a client-side subtraction would collapse into the
    same 0. Both come back as null, and the chart leaves a gap.

    The first real cohort has no predecessor and is therefore null, not
    +100% — the product did not grow infinitely in its first week, it simply
    started. Growth from a genuine zero is likewise null rather than a
    division by zero; the count itself carries that story.
    """
    prev = None
    for c in cohorts:
        g = {}
        for key, _lbl in GROWTH_STAGES:
            now = c.get(key) or 0
            was = None if prev is None else (prev.get(key) or 0)
            # A lead-in period is a real zero for this series, but it is not a
            # period the product was live in — growing "from" it is not a fact.
            if was is None or was == 0 or prev.get("lead_in"):
                g[key] = None
            else:
                g[key] = _growth_percent(now, was)
        c["growth"] = g
        prev = c

# TRIAL and PAID are two different questions and were being answered by one
# number. Every plan sells a 3-day trial, Paddle creates the subscription at
# checkout, and `is_subscribed` flips on day zero — so the old "Paid" column
# counted everyone who had ever handed over a card, whether or not a payment
# ever cleared. On a funnel whose whole purpose is to show whether trials
# convert, that is the one column that must not blur them.
#
#   trial  ever started a trial. The demand signal — it is what the pricing
#          page and the discount are optimising, and it is worth counting even
#          for someone who cancelled on day one.
#   paid   money actually moved: the trial ran its course and Paddle charged
#          (trial_status='converted'), or the account subscribed without a
#          trial at all (the grandfathered plans predate trials).
#
# Both read `trial_status`, written from Paddle's own subscription status by
# backend/trial_state.py — never inferred from a timer here.
COHORT_TRIAL_SQL = "u.trial_started_at IS NOT NULL OR u.trial_status IS NOT NULL"
COHORT_PAID_SQL = """
    u.trial_status = 'converted'
    OR (u.trial_started_at IS NULL
        AND (COALESCE(u.is_subscribed, 0) = 1
             OR COALESCE(u.plan, 'free') NOT IN ('free', '')))
"""
# Before the round-46 migration those columns do not exist. Falling back keeps
# the tab rendering (with trial folded into paid, exactly as it read before)
# instead of 500ing the whole page on a missing column.
COHORT_TRIAL_FALLBACK = "FALSE"
COHORT_PAID_FALLBACK = ("COALESCE(u.is_subscribed, 0) = 1 "
                        "OR COALESCE(u.plan, 'free') NOT IN ('free', '')")


def _trial_columns_exist(cur):
    try:
        cur.execute("""SELECT COUNT(*) AS n FROM information_schema.columns
                        WHERE table_name = 'users'
                          AND column_name IN ('trial_status',
                                              'trial_started_at')""")
        row = cur.fetchone()
        return int((row or {}).get("n") or 0) == 2
    except Exception:                                       # pragma: no cover
        return False

# Empty periods to draw BEFORE the metrics epoch. Without a run-up the chart
# opens on the first real cohort's conversion — a line that starts pinned to the
# top of the axis with nothing behind it to read it against. A short flat-zero
# lead-in makes the relaunch land as a visible jump. It is not a fudge: the
# series counts post-relaunch accounts only, and there were genuinely zero of
# those before the epoch.
COHORT_LEAD_IN = {"day": 7, "week": 3, "month": 2}


@admin_video_bp.route("/admin/video/cohorts", methods=["GET"])
@admin_required
def video_cohorts():
    """Lean-Startup cohort funnel analysis: group users by signup cohort and
    track what fraction of each cohort reached each activation/monetization
    stage. Unlike a running total, each signup cohort is measured
    independently, so product improvements show up as newer cohorts
    converting better. ?period=week|month|day (default week)."""
    period = (request.args.get("period") or "week").strip().lower()
    if period not in ("day", "week", "month"):
        period = "week"
    week_to_date = None
    with adb() as conn:
        cur = conn.cursor()
        have_trials = _trial_columns_exist(cur)
        trial_sql = (COHORT_TRIAL_SQL if have_trials
                     else COHORT_TRIAL_FALLBACK)
        paid_sql = COHORT_PAID_SQL if have_trials else COHORT_PAID_FALLBACK
        cur.execute("""
            WITH base AS (
                SELECT u.id,
                    date_trunc(%s, u.created_at) AS cohort,
                    EXISTS(SELECT 1 FROM projects p
                           JOIN assets a ON a.project_id = p.id
                           WHERE p.user_id = u.id AND a.kind = 'original')
                        AS uploaded,
                    EXISTS(SELECT 1 FROM projects p
                           JOIN chat_messages cm
                                ON cm.session_id = p.chat_session_id
                           WHERE p.user_id = u.id AND cm.role = 'user')
                        AS messaged,
                    EXISTS(SELECT 1 FROM video_jobs vj
                           WHERE vj.user_id = u.id AND vj.type = 'final'
                             AND vj.state = 'done')
                        AS exported,
                    (""" + trial_sql + """) AS trial,
                    (""" + paid_sql + """) AS paid
                FROM users u
                -- Only real post-relaunch accounts: old-idea signups and the
                -- long-lived test accounts (all created before the metrics
                -- epoch) otherwise pollute every cohort, and a manually-credited
                -- test account even shows up under "paid".
                WHERE u.created_at IS NOT NULL AND """ + _scope('u') + """
            ),
            agg AS (
                SELECT cohort,
                       COUNT(*) AS signed_up,
                       COUNT(*) FILTER (WHERE uploaded) AS uploaded,
                       COUNT(*) FILTER (WHERE messaged) AS messaged,
                       COUNT(*) FILTER (WHERE exported) AS exported,
                       COUNT(*) FILTER (WHERE trial) AS trial,
                       COUNT(*) FILTER (WHERE paid) AS paid
                FROM base
                GROUP BY cohort
            ),
            -- Every period from the lead-in through now, so the x-axis is a
            -- real timeline. GROUP BY alone emits only periods that HAD
            -- signups, which silently closes the gaps: a dead week vanishes
            -- and the line jumps straight to the next active one as if the
            -- week never happened.
            spine AS (
                SELECT generate_series(
                    date_trunc(%s, %s::timestamptz)
                        - (%s * ('1 ' || %s)::interval),
                    date_trunc(%s, NOW()),
                    ('1 ' || %s)::interval) AS cohort
            )
            SELECT s.cohort,
                   COALESCE(a.signed_up, 0) AS signed_up,
                   COALESCE(a.uploaded, 0) AS uploaded,
                   COALESCE(a.messaged, 0) AS messaged,
                   COALESCE(a.exported, 0) AS exported,
                   COALESCE(a.trial, 0) AS trial,
                   COALESCE(a.paid, 0) AS paid,
                   (s.cohort < date_trunc(%s, %s::timestamptz)) AS lead_in
            FROM spine s
            LEFT JOIN agg a ON a.cohort = s.cohort
            ORDER BY s.cohort
        """, (period, period, METRICS_EPOCH,
              COHORT_LEAD_IN.get(period, 3), period,
              period, period, period, METRICS_EPOCH))
        rows = cur.fetchall()
        if period == "week":
            # The open week must be compared with the SAME amount of elapsed
            # time last week. Comparing Sunday morning with all seven days of
            # the previous week systematically understates growth until the
            # week closes and makes a healthy live number look discouraging.
            cur.execute("""
                WITH bounds AS (
                    SELECT date_trunc('week', NOW()) AS current_start,
                           NOW() AS as_of,
                           date_trunc('week', NOW()) - INTERVAL '1 week'
                               AS previous_start
                )
                SELECT b.current_start, b.as_of, b.previous_start,
                       b.previous_start + (b.as_of - b.current_start)
                           AS previous_cutoff,
                       (SELECT COUNT(*) FROM users u
                        WHERE """ + _scope('u') + """
                          AND u.created_at >= b.current_start
                          AND u.created_at < b.as_of) AS current_signed_up,
                       (SELECT COUNT(*) FROM users u
                        WHERE """ + _scope('u') + """
                          AND u.created_at >= b.previous_start
                          AND u.created_at < b.previous_start
                                             + (b.as_of - b.current_start))
                           AS previous_signed_up
                FROM bounds b
            """)
            pace = cur.fetchone()
            if pace:
                current_count = int(pace["current_signed_up"] or 0)
                previous_count = int(pace["previous_signed_up"] or 0)
                week_to_date = {
                    "current_signed_up": current_count,
                    "previous_signed_up": previous_count,
                    "growth_signed_up": _growth_percent(
                        current_count, previous_count),
                    "current_start": pace["current_start"].isoformat(),
                    "as_of": pace["as_of"].isoformat(),
                    "previous_start": pace["previous_start"].isoformat(),
                    "previous_cutoff": pace["previous_cutoff"].isoformat(),
                }
    cohorts = [{
        "cohort": r["cohort"].date().isoformat() if r["cohort"] else None,
        "signed_up": int(r["signed_up"] or 0),
        "uploaded": int(r["uploaded"] or 0),
        "messaged": int(r["messaged"] or 0),
        "exported": int(r["exported"] or 0),
        "trial": int(r["trial"] or 0),
        "paid": int(r["paid"] or 0),
        # Pre-epoch run-up: real zero for this series, but not a cohort that
        # ever existed — the funnel table below skips these rows.
        "lead_in": bool(r["lead_in"]),
    } for r in rows]
    _attach_growth(cohorts)
    if week_to_date:
        # Make the graph and written current-week row use the fair comparison
        # too. Historical weeks retain ordinary full-week growth.
        for cohort in reversed(cohorts):
            if not cohort["lead_in"]:
                cohort["growth"]["signed_up"] = \
                    week_to_date["growth_signed_up"]
                cohort["growth_basis"] = "week_to_date"
                break
    return jsonify({
        "period": period,
        "metrics_epoch": METRICS_EPOCH,
        "stages": [{"key": k, "label": lbl} for k, lbl in COHORT_STAGES],
        "cohorts": cohorts,
        "growth_stages": [{"key": k, "label": lbl}
                          for k, lbl in GROWTH_STAGES],
        "week_to_date": week_to_date,
        "trial_tracking": have_trials,
        "note": ("Each row is the cohort of users who signed up in that "
                 "period; each stage counts how many of THEM ever reached it "
                 "(a funnel per cohort, not a running total). \"Started a "
                 "trial\" is ever-started and never drops back out, so a "
                 "cancelled trial still counts — it is the demand signal. "
                 "\"Paid\" is money that actually moved: a trial that "
                 "converted, or a subscription taken without a trial. The gap "
                 "between the two columns is your trial conversion rate."
                 + ("" if have_trials else
                    " NOTE: the trial columns are not present on this "
                    "database, so \"Started a trial\" reads 0 and \"Paid\" "
                    "falls back to current subscription state.")),
    })


@admin_video_bp.route("/admin/video/users/<int:user_id>", methods=["GET"])
@admin_required
def video_user_detail(user_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id, email, created_at, plan, is_subscribed,
                              credits_daily, credits_bonus, credits_monthly,
                              credits_balance, credits_daily_reset
                       FROM users WHERE id = %s""", (user_id,))
        u = cur.fetchone()
        if not u:
            return jsonify({"error": "User not found"}), 404

        cur.execute(f"""
            SELECT p.id, p.title, p.created_at,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user') AS messages,
                   (SELECT COUNT(*) FROM video_jobs v
                    WHERE v.project_id = p.id
                      AND v.type='agent_turn') AS turns,
                   (SELECT COUNT(*) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS exports,
                   (SELECT COUNT(*) FROM edls e
                    WHERE e.project_id = p.id) AS versions,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user' AND {UNSERVED_EXISTS}) AS unserved,
                   GREATEST(
                     (SELECT MAX(cm.created_at) FROM chat_messages cm
                      WHERE cm.session_id = p.chat_session_id),
                     (SELECT MAX(v.updated_at) FROM video_jobs v
                      WHERE v.project_id = p.id),
                     (SELECT MAX(a.created_at) FROM assets a
                      WHERE a.project_id = p.id)) AS last_activity
            FROM projects p WHERE p.user_id = %s
            ORDER BY p.id DESC
        """, (user_id,))
        projects = cur.fetchall()

        cur.execute("""SELECT a.id, a.project_id, a.kind, a.storage_key,
                              a.bytes, a.duration_s, a.width, a.height,
                              a.meta, a.created_at
                       FROM assets a
                       JOIN projects p ON p.id = a.project_id
                       WHERE p.user_id = %s AND a.kind IN %s
                       ORDER BY a.id DESC LIMIT 100""",
                    (user_id, UPLOAD_KINDS + CLEAN_KINDS))
        assets = cur.fetchall()

        cur.execute("""SELECT job_id, credits_used, tokens_used, created_at
                       FROM job_credits WHERE user_id = %s
                       ORDER BY created_at DESC LIMIT 50""", (user_id,))
        ledger = cur.fetchall()

        # Every upload this account attempted, including the ones that never
        # reached a project. For an account with no projects at all this is the
        # ONLY row that distinguishes "tried and was refused" from "signed up
        # and left" — which is the difference between a bug and a bounce.
        cur.execute("""SELECT id, kind, detail, created_at, project_id
                       FROM client_events
                       WHERE user_id = %s AND kind = ANY(%s)
                       ORDER BY id DESC LIMIT 100""",
                    (user_id, list(UPLOAD_EVENT_KINDS)))
        upload_events = [_upload_event_row(e) for e in cur.fetchall()]

    return jsonify({
        "user": {"id": u["id"], "email": u["email"],
                 "created_at": u["created_at"].isoformat(),
                 "plan": u["plan"], "is_subscribed": u["is_subscribed"],
                 "credits": {"daily": float(u["credits_daily"] or 0),
                             "bonus": float(u["credits_bonus"] or 0),
                             "monthly": float(u["credits_monthly"] or 0),
                             "balance": float(u["credits_balance"] or 0),
                             "daily_reset": u["credits_daily_reset"]
                                 .isoformat()
                                 if u["credits_daily_reset"] else None}},
        "projects": [
            {"id": p["id"], "title": p["title"],
             "created_at": p["created_at"].isoformat(),
             "messages": p["messages"], "turns": p["turns"],
             "exports": p["exports"],
             "versions": p["versions"], "unserved": p["unserved"],
             "last_activity": p["last_activity"].isoformat()
                 if p["last_activity"] else None}
            for p in projects],
        # Two lists, never one. These rows share the same kinds — the agent's
        # own media is stamped in meta — and merging them told the founder a
        # user had uploaded a file the AGENT had built in their project.
        "uploads": [_asset_row(a) for a in assets if not _is_machine(a)],
        "generated": [_asset_row(a) for a in assets if _is_machine(a)],
        "upload_events": upload_events,
        "upload_failures": len([e for e in upload_events
                                if e["kind"] in UPLOAD_FAILURE_KINDS]),
        "ledger": [
            {"job_id": l["job_id"],
             "credits_used": float(l["credits_used"] or 0),
             "tokens_used": int(l["tokens_used"] or 0),
             "created_at": l["created_at"].isoformat()}
            for l in ledger],
    })


# ── Watermark toggles (round 41) ─────────────────────────────────────────
# Live operator switches for the free-tier mark, read by the worker on every
# render (worker/db.video_settings). Deliberately DB-backed rather than env:
# an env var needs a redeploy of the Cloud Run executor to take effect, which
# is minutes and a build — useless as a switch you want to flip and observe.
#
# Table is created lazily here, mirroring newsletter_settings, so no schema
# change lands in models.py.
def _ensure_video_settings(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_settings (
            id INTEGER PRIMARY KEY,
            watermark_enabled BOOLEAN DEFAULT TRUE,
            watermark_force BOOLEAN DEFAULT FALSE,
            watermark_scene_top BOOLEAN DEFAULT FALSE,
            watermark_lower BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # CREATE TABLE IF NOT EXISTS does not add fields to the live table.
    cur.execute("ALTER TABLE video_settings ADD COLUMN IF NOT EXISTS "
                "watermark_scene_top BOOLEAN DEFAULT FALSE")
    cur.execute("ALTER TABLE video_settings ADD COLUMN IF NOT EXISTS "
                "watermark_lower BOOLEAN DEFAULT FALSE")
    cur.execute("INSERT INTO video_settings (id) VALUES (1) "
                "ON CONFLICT (id) DO NOTHING")


def _watermark_position(row):
    """Collapse the compatibility booleans into the admin's one choice."""
    if bool(row.get("watermark_scene_top", False)):
        return "scene"
    if bool(row.get("watermark_lower", False)):
        return "lower"
    return "frame"


@admin_video_bp.route("/admin/video/settings", methods=["GET"])
@admin_required
def video_settings_get():
    with adb() as conn:
        cur = conn.cursor()
        _ensure_video_settings(cur)
        conn.commit()
        cur.execute("SELECT watermark_enabled, watermark_force, "
                    "watermark_scene_top, watermark_lower, updated_at "
                    "FROM video_settings WHERE id = 1")
        row = cur.fetchone() or {}
    return jsonify({
        "watermark_enabled": bool(row.get("watermark_enabled", True)),
        "watermark_force": bool(row.get("watermark_force", False)),
        "watermark_scene_top": bool(row.get("watermark_scene_top", False)),
        "watermark_lower": bool(row.get("watermark_lower", False)),
        "watermark_position": _watermark_position(row),
        "updated_at": (row.get("updated_at").isoformat()
                       if row.get("updated_at") else None),
    })


@admin_video_bp.route("/admin/video/settings", methods=["POST"])
@admin_required
def video_settings_set():
    body = request.get_json(silent=True) or {}
    position = body.get("watermark_position")
    if position is not None and position not in ("frame", "lower", "scene"):
        return jsonify({"error": "invalid watermark_position"}), 400
    with adb() as conn:
        cur = conn.cursor()
        _ensure_video_settings(cur)
        # Enabled/force remain independent switches. Placement is one of
        # three mutually-exclusive choices, stored as booleans so executors
        # deployed before this change continue to understand scene-top.
        sets, vals = [], []
        for key, col in (("watermark_enabled", "watermark_enabled"),
                         ("watermark_force", "watermark_force")):
            if key in body:
                sets.append(f"{col} = %s")
                vals.append(bool(body[key]))
        if position is None:
            # Compatibility for the previous two-state admin and any older
            # callers: switching either legacy boolean off means frame mode.
            if "watermark_scene_top" in body:
                position = ("scene" if bool(body["watermark_scene_top"])
                            else "frame")
            elif "watermark_lower" in body:
                position = ("lower" if bool(body["watermark_lower"])
                            else "frame")
        if position is not None:
            sets.extend(("watermark_scene_top = %s", "watermark_lower = %s"))
            vals.extend((position == "scene", position == "lower"))
        if not sets:
            return jsonify({"error": "nothing to update"}), 400
        cur.execute(f"UPDATE video_settings SET {', '.join(sets)}, "
                    "updated_at = NOW() WHERE id = 1", vals)
        conn.commit()
        cur.execute("SELECT watermark_enabled, watermark_force, "
                    "watermark_scene_top, watermark_lower "
                    "FROM video_settings WHERE id = 1")
        row = cur.fetchone() or {}
    return jsonify({"ok": True,
                    "watermark_enabled": bool(row.get("watermark_enabled")),
                    "watermark_force": bool(row.get("watermark_force")),
                    "watermark_scene_top": bool(
                        row.get("watermark_scene_top")),
                    "watermark_lower": bool(row.get("watermark_lower")),
                    "watermark_position": _watermark_position(row)})


# ─────────────────────────────────────────────────────────────────────────
#  Reliability (2026-08-10) — the isolated failure counters + the
#  sessions-without-export ratio.
#
#  Counters live in metrics_counters (migration 017; the worker increments
#  via db.bump_metric) because the raw evidence does not keep: video_jobs
#  rows are deleted with their project, so counting failures over jobs
#  undercounts forever after the first cleanup. Lazily created here too,
#  mirroring video_settings, so deploy order cannot 500 this page.
# ─────────────────────────────────────────────────────────────────────────

def _ensure_metrics_counters(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics_counters (
            name       TEXT PRIMARY KEY,
            count      BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


@admin_video_bp.route("/admin/video/reliability", methods=["GET"])
@admin_required
def video_reliability():
    """The "how often does it break" panel, isolated by failure kind:
    worker deaths, terminal job failures, tool refusals, tool errors, and
    how many opened sessions never produced an export (count + percentage).

    The session ratio is computed live over projects that still exist.
    Generated shorts children are excluded from it — they are system-made
    and auto-rendered, so they would flatter the export rate — but a shorts
    PARENT counts as exported when any of its clips carries a done final
    (the run's clips ARE that session's deliverable). Account scope matches
    every other admin metric (post-epoch, no test accounts)."""
    with adb() as conn:
        cur = conn.cursor()
        _ensure_metrics_counters(cur)
        conn.commit()
        cur.execute("SELECT name, count, updated_at FROM metrics_counters")
        counters = {r["name"]: r for r in cur.fetchall()}

        # Build the exported-project set once. The former three correlated
        # EXISTS probes re-ran bitmap/index scans for every project and took
        # >1.1s on only 761 sessions; under Render load the admin card appeared
        # stuck on "Loading reliability…". This set-based form grows with the
        # tables, not projects × tables, and preserves the shorts-parent rule.
        cur.execute(f"""
            WITH exported_projects AS (
                SELECT j.project_id
                FROM video_jobs j
                WHERE j.type = 'final' AND j.state = 'done'
                UNION
                SELECT a.project_id
                FROM assets a
                WHERE a.kind = 'render'
                  AND a.meta->>'variant' = 'final'
                UNION
                SELECT c.parent_project_id
                FROM projects c
                JOIN video_jobs cj ON cj.project_id = c.id
                WHERE c.parent_project_id IS NOT NULL
                  AND cj.type = 'final' AND cj.state = 'done'
            ), scoped_projects AS (
                SELECT p.id, p.created_at,
                       (e.project_id IS NOT NULL) AS exported
                FROM projects p
                JOIN users u ON u.id = p.user_id
                LEFT JOIN exported_projects e ON e.project_id = p.id
                WHERE COALESCE(p.kind, 'edit') != 'short'
                  AND {_scope('u')}
            )
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE NOT exported) AS no_export,
                   COUNT(*) FILTER (
                       WHERE created_at >= NOW() - INTERVAL '30 days')
                       AS total_30d,
                   COUNT(*) FILTER (
                       WHERE NOT exported
                         AND created_at >= NOW() - INTERVAL '30 days')
                       AS no_export_30d
            FROM scoped_projects
        """)
        sess = cur.fetchone() or {}

        # Context beside the counter, not a replacement for it: what the
        # still-existing job rows say, so a spike has a first place to look.
        cur.execute("""
            SELECT type, COUNT(*) AS n
            FROM video_jobs
            WHERE state = 'failed'
              AND updated_at >= NOW() - INTERVAL '30 days'
            GROUP BY type ORDER BY n DESC
        """)
        failed_by_type = [dict(r) for r in cur.fetchall()]

    def _c(name):
        row = counters.get(name) or {}
        return {"count": int(row.get("count") or 0),
                "updated_at": (row["updated_at"].isoformat()
                               if row.get("updated_at") else None)}

    total = int(sess.get("total") or 0)
    no_export = int(sess.get("no_export") or 0)
    total_30d = int(sess.get("total_30d") or 0)
    no_export_30d = int(sess.get("no_export_30d") or 0)
    return jsonify({
        "counters": {
            "worker_died": _c("worker_died"),
            "job_failed": _c("job_failed"),
            "tool_refused": _c("tool_refused"),
            "tool_failed": _c("tool_failed"),
        },
        "sessions_without_export": {
            "total_sessions": total,
            "no_export": no_export,
            "pct": round(100.0 * no_export / total, 1) if total else 0.0,
            "total_sessions_30d": total_30d,
            "no_export_30d": no_export_30d,
            "pct_30d": (round(100.0 * no_export_30d / total_30d, 1)
                        if total_30d else 0.0),
        },
        "failed_jobs_by_type_30d": failed_by_type,
        "note": ("Counters accumulate from 2026-08-10 (migration 017) and "
                 "survive project deletion; failed_jobs_by_type_30d is "
                 "computed over still-existing job rows only."),
    })
