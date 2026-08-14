"""Independent render review: fresh evidence, strict structured findings."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import agent_loop
import preview_critic


def test_parse_report_accepts_fenced_json_and_normalizes_findings():
    answer = '''Here is the review:
```json
{"verdict":"pass","findings":[
 {"severity":"major","category":"zoom","time_s":4.2,
  "evidence":"Image 2 tile 1 magnifies only the blank wall",
  "repair":"remove the zoom or aim it at the face","confidence":0.91},
 {"severity":"minor","category":"made_up","time_s":null,
  "evidence":"title is slightly tight","repair":"add margin",
  "confidence":0.6}]}
```'''
    report = preview_critic.parse_report(answer)
    assert report["verdict"] == "repair"  # evidence wins over a stray pass
    assert report["findings"][0]["category"] == "zoom"
    assert report["findings"][1]["category"] == "other"
    lines = preview_critic.repair_lines(report)
    assert len(lines) == 1 and "blank wall" in lines[0]


def test_motion_finding_preserves_exact_repair_identity():
    report = preview_critic.parse_report(
        '{"verdict":"repair","findings":[{'
        '"severity":"major","category":"motion_path","time_s":5.2,'
        '"target_id":"z-proof","motion_motif":"proof_lock",'
        '"evidence":"the path travels across the face before settling",'
        '"repair":"move the middle waypoint below the face",'
        '"confidence":0.94}]}')

    finding = report["findings"][0]
    assert finding["target_id"] == "z-proof"
    assert finding["motion_motif"] == "proof_lock"
    line = preview_critic.repair_lines(report)[0]
    assert "target=z-proof" in line and "motif=proof_lock" in line

    untargeted = {"verdict": "repair", "findings": [dict(
        finding, target_id=None)]}
    assert preview_critic.repair_lines(untargeted) == []


def test_priority_context_survives_large_supporting_history():
    context = preview_critic.pack_context(
        ["MOTION CONTRACT: proof_lock", "B-ROLL PURPOSE: exact product"],
        ["OLD CONVERGENCE HISTORY " + ("x" * 30000)])

    assert len(context) <= preview_critic._CONTEXT_MAX_CHARS
    assert "MOTION CONTRACT: proof_lock" in context
    assert "B-ROLL PURPOSE: exact product" in context
    assert context.index("MOTION CONTRACT") < context.index("OLD CONVERGENCE")


def test_malformed_or_low_confidence_review_cannot_block_delivery():
    assert preview_critic.parse_report("not json") is None
    report = preview_critic.parse_report(
        '{"verdict":"repair","findings":[{"severity":"major",'
        '"category":"crop","evidence":"possibly clipped",'
        '"repair":"inspect exact frame","confidence":0.4}]}')
    assert report is not None
    assert preview_critic.repair_lines(report) == []


def test_weak_craft_rubric_forces_repair_and_relevance_needs_timed_evidence():
    report = preview_critic.parse_report(
        '{"verdict":"pass","findings":['
        '{"severity":"major","category":"narrative_relevance",'
        '"time_s":8.4,"evidence":"generic skyline contradicts the named product reveal",'
        '"repair":"replace it with footage of the product",'
        '"confidence":0.87}],"rubric":{"narrative_support":{'
        '"level":"weak","evidence":"the cutaway does not show the named subject",'
        '"confidence":0.9}}}')

    assert report["verdict"] == "repair"
    assert report["rubric"]["narrative_support"]["level"] == "weak"
    assert "narrative_relevance" in preview_critic.repair_lines(report)[0]

    untimed = {"verdict": "repair", "findings": [dict(
        report["findings"][0], time_s=None)]}
    assert preview_critic.repair_lines(untimed) == []


def test_independent_review_sees_edited_output_and_raw_source(monkeypatch,
                                                             tmp_path):
    seen = {}

    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)

    def download(key, path):
        with open(path, "wb") as fh:
            fh.write(key.encode())

    monkeypatch.setattr(agent_tools.storage, "download_to", download)

    def review(paths, labels, context):
        seen.update(paths=paths, labels=labels, context=context)
        return {"verdict": "pass", "findings": []}

    monkeypatch.setattr(agent_tools.preview_critic, "review", review)

    class Ctx:
        workdir = str(tmp_path)
        index = {"tile_keys": ["raw/first.jpg", "raw/last.jpg"]}
        user_message = "make a clean vertical reel"
        duration = 30.0
        edit_plan = {"brief": "talking-head reel", "steps": ["reframe"],
                     "format": "social interview", "must_keep": ["face"],
                     "must_avoid": ["burned text collision"],
                     "narrative_arc": ["pain", "mechanism", "resolution"],
                     "sequence_map": [{
                         "role": "proof", "anchor": "retention collapsed",
                         "purpose": "prove the cost of the mistake",
                         "visual": "reviewed analytics chart",
                         "sound": "one soft impact then voice", "energy": .8,
                     }],
                     "broll_direction": "literal proof, no generic wallpaper"}
        project_id = 5

        class Db:
            def run(self, _fn, *_args):
                return {"meta": {
                    "filename": "retention-chart.mp4",
                    "description": "mobile analytics retention graph",
                    "broll_moment": {"purpose": "prove retention collapse",
                                     "query": "mobile retention graph"}}}

        db = Db()

        def latest_edl(self):
            return {"json": {"keep": [[0.0, 30.0]],
                             "frame": {"ratio": "9:16", "mode": "crop"},
                             "overlays": [{"asset_key": "stock/graph.mp4",
                                           "start": 7.5, "end": 10.0,
                                           "fit": "cover"}],
                             "effects": {"zooms": []}}}

    report = agent_tools._independent_preview_review(
        Ctx(),
        {"sheet_key": "render/overview.jpg",
         "verify_sheet_key": "render/changed.jpg", "duration_s": 25.0},
        [(4.0, "vertical crop keeps the speaker visible")])
    assert report["verdict"] == "pass"
    assert any("EDITED RENDER overview" in x for x in seen["labels"])
    assert any("EDITED RENDER changed" in x for x in seen["labels"])
    assert sum("RAW SOURCE" in x for x in seen["labels"]) == 2
    assert "talking-head reel" in seen["context"]
    assert "format=social interview" in seen["context"]
    assert "must avoid=burned text collision" in seen["context"]
    assert "pain -> mechanism -> resolution" in seen["context"]
    assert "Beat 1 [proof]" in seen["context"]
    assert "picture=reviewed analytics chart" in seen["context"]
    assert "sound=one soft impact then voice" in seen["context"]
    assert "purpose=prove retention collapse" in seen["context"]
    assert "mobile analytics retention graph" in seen["context"]
    assert "FORMAT-SPECIFIC VISUAL BENCHMARK: podcast_conversation" in \
        seen["context"]
    assert "speaker-aware framing" in seen["context"]
    assert all(os.path.exists(path) for path in seen["paths"])


def test_independent_review_prefers_event_screening_over_redundant_overview(
        monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.storage, "download_to",
        lambda key, path: open(path, "wb").write(key.encode()))
    monkeypatch.setattr(
        agent_tools.preview_critic, "review",
        lambda paths, labels, context: (
            seen.update(paths=paths, labels=labels, context=context) or
            {"verdict": "pass", "findings": []}))

    class Ctx:
        workdir = str(tmp_path)
        index = {}
        user_message = "make this publish-ready"
        duration = 30.0
        edit_plan = {}
        project_id = 8

        def latest_edl(self):
            return {"json": {"keep": [[0, 30]], "effects": {}}}

    result = {
        "sheet_key": "render/legacy-overview.jpg", "duration_s": 30,
        "screening_pages": [{
            "key": "render/screen-1.jpg",
            "frames": [
                {"time_s": 0.08, "reason": "opening frame"},
                {"time_s": 18.2, "reason": "B-roll 3 body"},
            ],
        }],
    }
    report = agent_tools._independent_preview_review(Ctx(), result)

    assert report["verdict"] == "pass"
    assert len(seen["paths"]) == 1
    assert "critic_screening1" in seen["paths"][0]
    assert all("overview" not in label.lower() for label in seen["labels"])
    assert "tile 2=18.20s (B-roll 3 body)" in seen["labels"][0]


def test_mcp_render_frames_skip_valmera_funded_second_critic(monkeypatch):
    class Ctx:
        sight_out = True

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("MCP frames must be reviewed by the outside model")

    monkeypatch.setattr(agent_tools, "_independent_preview_review",
                        should_not_run)
    assert agent_tools._preview_critic_report(Ctx(), {}, []) is None

    monkeypatch.setattr(agent_tools, "_self_check", should_not_run)
    assert agent_tools._preview_fallback_check(
        Ctx(), {}, [], delivered=False, critic_report=None) is None


def test_in_house_render_keeps_independent_critic(monkeypatch):
    expected = {"verdict": "pass", "findings": []}

    class Ctx:
        sight_out = False

    monkeypatch.setattr(agent_tools, "_independent_preview_review",
                        lambda *_args: expected)
    assert agent_tools._preview_critic_report(Ctx(), {}, []) is expected


def test_visual_review_reuses_identical_picture_and_tracks_convergence(
        monkeypatch):
    calls = []

    class Ctx:
        sight_out = False
        edit_plan = {"treatment": "restrained founder proof"}
        index = {"video": {"sha256": "source"}}
        editing_metrics = {}
        _visual_review_cache = {}
        _last_visual_review_state = None
        edl = {"keep": [[0, 10]], "captions": None,
               "music": [{"id": "m1", "gain_db": -20}]}

        def latest_edl(self):
            return {"json": self.edl}

    ctx = Ctx()

    def review(_ctx, _result, _plan=None, convergence_context=None):
        calls.append(convergence_context)
        return {"verdict": "pass", "findings": []}

    monkeypatch.setattr(agent_tools, "_independent_preview_review", review)
    first = agent_tools._preview_critic_report(ctx, {}, [])
    ctx.edl = {**ctx.edl, "music": [{"id": "m1", "gain_db": -26}]}
    reused = agent_tools._preview_critic_report(ctx, {}, [])
    assert first is reused
    assert len(calls) == 1
    assert ctx.editing_metrics["visual_reviews_reused"] == 1

    ctx.edit_plan = {
        "treatment": "restrained founder proof",
        "department_plan": {
            "color": {"mode": "author", "purpose": "warm proof world"},
        },
    }
    agent_tools._preview_critic_report(ctx, {}, [])
    assert len(calls) == 2

    ctx.edl = {**ctx.edl, "effects": {"grade": "warm"}}
    agent_tools._preview_critic_report(ctx, {}, [])
    assert len(calls) == 3
    assert "prior independently reviewed picture verdict was pass" in calls[2]
    assert "effects(grade)" in calls[2]
    assert "Do not reopen an untouched" in calls[2]


def test_critic_compares_framing_treatment_across_shots(monkeypatch):
    seen = {}
    monkeypatch.setattr(preview_critic.llm, "vision_available", lambda: True)

    def ask(prompt, _paths, **_kwargs):
        seen["prompt"] = prompt
        return '{"verdict":"pass","findings":[]}'

    monkeypatch.setattr(preview_critic.llm, "ask_vision", ask)
    report = preview_critic.review(
        ["render.jpg", "raw.jpg"], ["edited", "raw"],
        "brief asks for shot-specific framing")
    assert report["verdict"] == "pass"
    assert "compare treatment ACROSS SHOTS" in seen["prompt"]
    assert "close shot should normally fill" in seen["prompt"]


def test_new_preview_version_with_proven_craft_defect_gets_repair_decision(
        monkeypatch):
    monkeypatch.setattr(agent_loop.config, "AGENT_TURN_TIMEOUT_S", 600)

    class Ctx:
        last_preview = {"edl_version": 7}
        last_visual_critic = {"verdict": "repair", "findings": [{
            "severity": "major", "category": "style_coherence",
            "time_s": 11.0, "evidence": "caption family changes mid-edit",
            "repair": "use one caption family", "confidence": .91}]}

        def latest_edl(self):
            return {"version": 7}

    messages, pushed = [], set()
    assert agent_loop._quality_repair_pushback(
        Ctx(), messages, agent_loop.time.monotonic(), pushed)
    assert pushed == {7}
    assert "caption family changes" in messages[0]["content"]
    assert not agent_loop._quality_repair_pushback(
        Ctx(), messages, agent_loop.time.monotonic(), pushed)
