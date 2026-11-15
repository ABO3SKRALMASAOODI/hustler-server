"""Typed library routing and clocks, independent of film style/quality ratings."""
import copy
from types import SimpleNamespace as NS

import pytest

from score import asks, film, flags, patch, typed


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "lost_ask", "lease_lost"])
def test_director_write_revision_uses_rejection_evidence_and_keeps_guards(monkeypatch, tmp_path, outcome):
    import db
    from score import brief, call, compiler, direct, lower
    initial = [{"k": "b", "id": "b1", "at": [0, 3]}]
    replacement = [{"k": "b", "id": "b1", "at": [0, 8]}]
    before, candidate = NS(l1={}), NS(l1={})
    row = {"json": {"keep": [[7000, 7090]]}}
    writes = []
    def write(edl, note):
        writes.append(edl)
        if outcome == "lease_lost":
            raise db.JobLeaseLost("changed")
        return "EDL v3" if outcome == "accepted" else "REJECTED: still removes a distinct action"
    parent = NS(latest_edl=lambda: copy.deepcopy(row), write_edl=write)
    ctx = typed.WindowContext(parent, {"video": {"duration": 90}}, "proxy", 7000, "original", 1)
    monkeypatch.setattr(brief, "load", lambda work: ([], []))
    def model(model, system, content, **kw):
        assert model == "gpt-6.1-sol"
        assert "distinct conveyor action" in content[-1]["text"]
        assert "7000" in content[-1]["text"] and "7090" in content[-1]["text"]
        assert kw["purpose"] == "director_write_repair"
        return NS(text=direct.dump(replacement), usd=.01)
    monkeypatch.setattr(call, "stream", model)
    monkeypatch.setattr(compiler, "compile_score", lambda *a, **k: candidate)
    monkeypatch.setattr(compiler, "validate_l1", lambda l1: [])
    monkeypatch.setattr(film, "bare", lambda comp: False)
    monkeypatch.setattr(direct, "ask_facts", lambda lines, comp, *a:
        ([], ["a1"] if comp is candidate and outcome == "lost_ask" else [], {}))
    monkeypatch.setattr(film, "_one_mix", lambda *a: None)
    monkeypatch.setattr(lower, "lower", lambda *a: ({"keep": [[0, 90]]}, {}, {}))
    monkeypatch.setattr(film, "_publish", lambda ctx, worker_db, edl, *a: edl)
    rec = {}
    args = dict(work=str(tmp_path), cfg={"models": {"direct": "gpt-6.1-sol"},
        "effort": {"direct": "medium"}}, index={}, sc={}, bin_doc={}, original_key="original",
        worker_db=None, comp=before, text=direct.dump(initial),
        refusal="REJECTED: distinct conveyor action removed", rec=rec)
    if outcome == "lease_lost":
        with pytest.raises(db.JobLeaseLost):
            typed.recover_write(ctx, **args)
        return
    got = typed.recover_write(ctx, **args)
    assert bool(got) == (outcome == "accepted")
    assert writes == ([] if outcome == "lost_ask" else [{"keep": [[7000, 7090]]}])
    assert (tmp_path / "score.jsonl").exists() == (outcome == "accepted")


def _empty_picture_search(tmp_path):
    return dict(ctx=NS(director_footage_cfg={"mode": "on"}, director_footage=None),
        job={"id": 7}, cfg={"models": {"scout": "gpt-6.1-sol"}}, deadline=220,
        work=str(tmp_path), index={}, sess={"prompts": [{"text": "Polish my film"}]},
        per={}, sc={"asks": [{"id": "a1", "said": "Polish my film"}],
            "scope": "long", "windows": [{"t0": 0, "t1": 900}],
            "wishes": [{"w": "1-3", "want": "sunrise", "mine": False, "own": False}]},
        bin_doc={"items": [], "own": [], "wishes": [{"check": [
            {"ok": False, "why": "Could be sunset; sunrise is not established"}]}]},
        foot=None, rec={}, acquire=object())


def test_empty_typed_supply_replans_pictures_but_keeps_original_asks_and_checks(monkeypatch, tmp_path):
    from score import scout, supply
    monkeypatch.setattr(typed, "time", NS(monotonic=lambda: 0))
    args = _empty_picture_search(tmp_path)
    alternate = [{"w": "1-3", "want": "a clock at dawn", "mine": False}]
    calls = []
    def propose(work, index, session, per, deadline, model):
        assert "Could be sunset" in session["execution"]
        assert session["prompts"] == args["sess"]["prompts"]
        assert model == "gpt-6.1-sol"
        return {"wishes": alternate, "asks": [], "windows": [{"t0": 0, "t1": 20}]}
    checked = {"items": [], "own": [], "wishes": [{"check": [{"ok": False}]}]}
    def check(work, sc, per, acquire, deadline, foot):
        calls.append(sc)
        assert acquire is args["acquire"]
        return checked  # the second search can still reject everything
    monkeypatch.setattr(scout, "run", propose)
    monkeypatch.setattr(film, "footage_start", lambda *a, **k: None)
    monkeypatch.setattr(supply, "run", check)
    sc, doc, foot = typed.recover_supply(**args)
    assert len(calls) == 1 and doc is checked
    assert sc["wishes"] == alternate
    assert sc["asks"] == args["sc"]["asks"]
    assert sc["windows"] == args["sc"]["windows"] and sc["scope"] == "long"
    assert args["rec"]["picture_recovery"]["result"] == "alternative checked"


@pytest.mark.parametrize("reason", ["picture", "own_view", "own_only", "no_wish", "deadline", "pending", "late_picture"])
def test_picture_recovery_keeps_usable_or_pending_supply_and_source_only_wishes(monkeypatch, tmp_path, reason):
    from score import scout
    monkeypatch.setattr(typed, "time", NS(monotonic=lambda: 0))
    args = _empty_picture_search(tmp_path)
    if reason == "picture": args["bin_doc"]["items"] = [{"id": 0}]
    if reason == "own_view": args["bin_doc"]["own"] = [{"id": 0}]
    if reason == "own_only": args["sc"]["wishes"][0]["mine"] = True
    if reason == "no_wish": args["sc"]["wishes"] = []
    if reason == "deadline": args["deadline"] = 100
    if reason == "pending": args["foot"] = {"thread": NS(join=lambda seconds: None, is_alive=lambda: True)}
    if reason == "late_picture": args["foot"] = {"offers": [{"id": 0}]}
    monkeypatch.setattr(scout, "run", lambda *a, **k: pytest.fail("must preserve existing supply"))
    assert typed.recover_supply(**args) == (args["sc"], args["bin_doc"], args["foot"])


def test_recovery_failure_keeps_the_empty_bin_and_owns_the_new_footage_thread(monkeypatch, tmp_path):
    import json
    from score import scout, supply
    monkeypatch.setattr(typed, "time", NS(monotonic=lambda: 0))
    args = _empty_picture_search(tmp_path)
    foot = {"thread": object()}
    monkeypatch.setattr(scout, "run", lambda *a, **k: {"wishes": [{"want": "another honest picture"}]})
    monkeypatch.setattr(film, "footage_start", lambda *a, **k: foot)
    def fail(*a, **k):
        raise RuntimeError("search unavailable")
    monkeypatch.setattr(supply, "run", fail)
    sc, doc, got = typed.recover_supply(**args)
    assert got is foot and args["ctx"].director_footage is foot
    assert doc == args["bin_doc"] == json.loads((tmp_path / "bin.json").read_text())
    assert sc["asks"] == args["sc"]["asks"]
    assert "search unavailable" in args["rec"]["picture_recovery"]["error"]


def test_window_index_keeps_late_class_words_and_spatial_clock():
    source = {"video": {"duration": 7320, "width": 1920, "height": 1080},
        "words": [{"w": "early", "t0": 1, "t1": 2},
                  {"w": "late", "t0": 7000, "t1": 7001},
                  {"w": "payoff", "t0": 7088, "t1": 7089}],
        "sentences": [{"id": 1, "wi0": 0, "wi1": 0, "t0": 1, "t1": 2},
                      {"id": 2, "wi0": 1, "wi1": 2, "t0": 7000, "t1": 7089}],
        "shots": [{"id": 1, "start": 0, "end": 7320}],
        "spatial": {"samples": [{"t": 1}, {"t": 7040}]},
        "tile_keys": ["full-source-tile"]}
    before = copy.deepcopy(source)
    out = typed.window_index(source, 7000, 7090)
    assert source == before
    assert out["video"]["duration"] == 90
    assert [w["w"] for w in out["words"]] == ["late", "payoff"]
    assert out["sentences"][0]["wi0"] == 0
    assert out["sentences"][0]["wi1"] == 1
    assert out["words"][-1]["t1"] == 89
    assert out["spatial"]["samples"] == [{"t": 40}]
    assert out["shots"] == [{"id": 1, "start": 0, "end": 90}]
    assert "tile_keys" not in out


@pytest.mark.parametrize("captions", [[{"start": 1, "end": 2, "text": "late"}],
    {"mode": "custom", "items": [{"start": 1, "end": 2, "text": "late"}]}])
def test_source_clock_does_not_move_program_layers_or_external_media(captions):
    edl = {"keep": [[1, 90]], "captions": captions,
        "texts": [{"start": 0, "end": 3}], "sfx": [{"at": 1}],
        "inserts": [{"asset_key": "original", "at_output_s": 0, "source_start_s": 3},
                    {"asset_key": "archive", "at_output_s": 5, "source_start_s": 4}],
        "overlays": [{"asset_key": "original", "start": 6, "source_start_s": 10}]}
    before = copy.deepcopy(edl)
    out = typed.source_clock(edl, 7000, "original")
    assert edl == before
    assert out["keep"] == [[7001, 7090]]
    items = out["captions"] if isinstance(captions, list) else out["captions"]["items"]
    assert items[0]["start"] == 7001
    assert out["texts"] == edl["texts"] and out["sfx"] == edl["sfx"]
    assert out["inserts"][0]["source_start_s"] == 7003
    assert out["inserts"][0]["at_output_s"] == 0
    assert out["inserts"][1] == edl["inserts"][1]
    assert out["overlays"][0]["source_start_s"] == 7010


def test_typed_write_uses_request_guards_and_propagates_asset_grants():
    writes = []
    ctx = NS(write_edl=lambda edl, note: writes.append(edl) or "REJECTED: keep protected",
             latest_edl=lambda: {"json": {"keep": [[0, 7320]]}},
             apply_score=lambda *a: pytest.fail("first-film scope bypass"))
    view = typed.WindowContext(ctx, {"video": {"duration": 90}}, "proxy", 7000, "original", 9)
    view._sfx_grants = {"sound"}
    assert ctx._sfx_grants == {"sound"}
    assert view.apply_score({"keep": [[0, 90]]}, "film").startswith("REJECTED")
    assert writes[0]["keep"] == [[7000, 7090]]
    assert not hasattr(ctx, "director_version")


def test_wordless_montage_declares_canvas_without_changing_pictures_or_bypassing_guard():
    from schemas import validate_edl
    writes = []
    ctx = NS(latest_edl=lambda: {"json": {"keep": [[0, 10]]}},
             write_edl=lambda edl, note: writes.append(edl) or "REJECTED: source must remain")
    view = typed.WindowContext(ctx, {"video": {"duration": 10}}, "proxy", 0, "original", 9)
    edl = {"keep": [], "frame": {"ratio": "9:16", "mode": "crop"},
        "inserts": [{"id": "shot", "asset_key": "original", "kind": "video",
                     "at_output_s": 0, "duration_s": 3, "source_start_s": 2}]}
    before = copy.deepcopy(edl)
    prepared = view.director_prepare_edl(edl, {"frame": [720, 1280], "fps": 30})
    normalized = validate_edl(prepared, 10).model_dump()
    assert normalized["canvas"] == {"width": 720, "height": 1280, "fps": 30, "bg_color": "#000000"}
    assert prepared["inserts"] == before["inserts"] and edl == before
    assert view.apply_score(prepared, "montage").startswith("REJECTED")
    assert writes == [prepared]
    assert not hasattr(ctx, "_score_write")


def test_canvas_transition_checks_final_deletions_with_pictures_in_one_valid_write(monkeypatch):
    import agent_tools
    import edit_integrity
    from schemas import validate_edl
    row = {"json": {"keep": [[i*2, i*2+1] for i in range(25)]}}
    ctx = NS(latest_edl=lambda: copy.deepcopy(row))
    target = {"keep": [], "canvas": {"width": 720, "height": 1280, "fps": 30},
              "inserts": [{"id": "shot", "asset_key": "original", "kind": "video",
                           "at_output_s": 0, "duration_s": 3, "source_start_s": 2}]}
    batches = []
    def checked_keep(got, keep, *args, **kwargs):
        assert got is ctx and keep  # never save an empty keep-only intermediate
        spans = edit_integrity.removed_spans(row["json"]["keep"], keep)
        assert len(spans) <= 12
        batches.append(len(spans))
        row["json"]["keep"] = keep
        return "EDL v2"
    def checked_write(edl, note):
        validate_edl(edl, 50)
        assert len(edit_integrity.removed_spans(row["json"]["keep"], edl["keep"])) == 12
        assert edl["inserts"] == target["inserts"]
        return "REJECTED: final source-evidence check still refuses"
    monkeypatch.setattr(agent_tools, "_write_keep", checked_keep)
    ctx.write_edl = checked_write
    assert typed.write_film(ctx, target).startswith("REJECTED")
    assert batches == [12, 1]


@pytest.mark.parametrize("refuse_batch", [None, 2])
def test_long_director_cut_uses_checked_batches_and_stops_on_refusal(monkeypatch, refuse_batch):
    import agent_tools
    import edit_integrity
    row = {"version": 1, "json": {"keep": [[0, 100]], "texts": [{"text": "existing"}]}}
    target = {"keep": [[2 * i, 2 * i + 1] for i in range(25)], "texts": [{"text": "directed"}]}
    batches, final_writes = [], []
    ctx = NS(latest_edl=lambda: copy.deepcopy(row))
    def write_keep(got, keep, desc, snap_to_words):
        assert got is ctx and snap_to_words is False
        removed = edit_integrity.removed_spans(row["json"]["keep"], keep)
        assert 1 <= len(removed) <= 12
        batches.append(removed)
        if len(batches) == refuse_batch:
            return "REJECTED: the source evidence does not support these deletions"
        row["version"] += 1
        row["json"]["keep"] = keep
        return f"EDL v{row['version']}"
    def write(edl, note):
        assert not edit_integrity.removed_spans(row["json"]["keep"], edl["keep"])
        final_writes.append(edl)
        return "EDL v5"
    monkeypatch.setattr(agent_tools, "_write_keep", write_keep)
    ctx.write_edl = write
    result = typed.write_film(ctx, target)
    if refuse_batch:
        assert result.startswith("REJECTED") and len(batches) == 2
        assert final_writes == []
    else:
        assert result == "EDL v5" and [len(b) for b in batches] == [12, 12, 1]
        assert final_writes == [target]


@pytest.mark.parametrize("test_rung", [False, True])
def test_four_minute_typed_film_reaches_director_without_lifting_first_film_cap(monkeypatch, tmp_path, test_rung):
    source = tmp_path / "source.mp4"
    source.touch()
    ledger = {"asks": [{"kind": "film", "cut": "new", "said": "polish", "want": "polished film"}]}
    ctx = NS(duration=240, has_main_video=True, project_id=7, workdir=str(tmp_path),
        index={"video": {"duration": 240}}, proxy_path=lambda: str(source),
        agent_model="gpt-6.1-sol", ask_ledger=copy.deepcopy(ledger), versions_written=[])
    ctx.write_edl = lambda edl, note: ctx.versions_written.append(2) or "EDL v2"
    ctx.latest_edl = lambda: {"json": {"keep": [[0, 240]]}}
    db = NS(run=lambda *a: {"storage_key": "original"})
    dcfg = flags.parse({"mode": "on", **({"test_user_ids": [5],
        "models": {"scout": "gpt-6.1-sol", "direct": "gpt-6.1-sol"}} if test_rung else {})})
    monkeypatch.setattr(flags, "read", lambda db: dcfg)

    def make(view, db, job, cfg, deadline, work, rec):
        assert cfg["models"] == {"scout": "gpt-6.1-sol", "direct": "gpt-6.1-sol"}
        assert view.duration == 240
        assert view.apply_score({"keep": [[0, 240]]}, "film") == "EDL v2"
        view.director_version = 2
        return [], ["crafted film"], {}
    monkeypatch.setattr(film, "_make", make)
    got = asks.direct_film(ctx, db, {"id": 1, "user_id": 5}, 9, asks.parse({"mode": "on"}))
    assert got["version"] == 2 and got["path"] == "whole_film_director"
    assert ctx.ask_ledger == ledger
    assert not list(tmp_path.glob("typed-film-*"))
    assert flags.MAX_SOURCE_S == 180  # first-film admission remains unchanged


def test_long_whole_request_reaches_director_not_thin_patch_pass(monkeypatch, tmp_path):
    ctx = NS(duration=2700, has_main_video=True, ask_ledger={"asks": []}, project_id=7,
        workdir=str(tmp_path), index={"video": {"duration": 2700}},
        proxy_path=lambda: str(tmp_path / "source.mp4"), agent_model="gpt-6.1-sol")
    monkeypatch.setattr(flags, "read", lambda db: dict(copy.deepcopy(flags.DEFAULTS), mode="on"))
    monkeypatch.setattr(typed, "_selection", lambda ctx: (None, {"usd": .01}))
    import quality_verifier
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "the entire match")
    monkeypatch.setattr(patch, "throughout", lambda *a: pytest.fail("whole-film craft cannot fall to a patch"))
    def make(view, *a):
        assert view.duration == 2700
        assert view.director_typed is True
        view.director_version = 3
        return [], ["whole film"], {}
    monkeypatch.setattr(film, "_make", make)
    got = typed.run(ctx, NS(run=lambda *a: {"storage_key": "original"}), {}, 9, {"director": True})
    assert got["path"] == "whole_film_director"
    assert got["source_window"] == [0, 2700]


@pytest.mark.parametrize("is_typed", [False, True])
def test_director_prompt_scope_override_is_confined_to_typed_execution(monkeypatch, tmp_path, is_typed):
    import json
    from score import brief
    folder = tmp_path / "brief"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"styles": [], "typed_execution": is_typed}))
    (folder / "film.txt").write_text("Customer: edit this into a 15-20 minute film.")
    monkeypatch.setattr(brief, "prefix_text", lambda: "first-film defaults")
    monkeypatch.setattr(brief, "image_block", lambda path: {"type": "image", "source": {}})
    system, content = brief.load(str(tmp_path))
    assert system[0]["text"] == "first-film defaults"
    assert len(system) == (2 if is_typed else 1)
    if is_typed:
        assert system[1]["text"] == brief.TYPED_EXECUTION
    assert content[0]["text"] == "Customer: edit this into a 15-20 minute film."


def test_job_lease_loss_never_becomes_a_fallback(monkeypatch):
    import db
    def lost(*a):
        raise db.JobLeaseLost("lease changed")
    monkeypatch.setattr(typed, "run", lost)
    with pytest.raises(db.JobLeaseLost):
        asks.direct_film(None, None, {}, None, {})


def test_optional_question_stays_nonterminal_until_an_attempt_is_rendered(monkeypatch):
    import agent_loop
    import agent_tools
    ctx = NS(ask_ledger={"asks": [{"kind": "film"}]}, rendered_versions=set(),
             last_preview=None, _question_held=True)
    monkeypatch.setattr(agent_tools, "_question_needs_customer", lambda *a: False)
    assert agent_loop._typed_question_hold(ctx, "ask_user", {"question": "Which font?"})
    # An earlier cached preview is not this request's rendered attempt.
    ctx.rendered_versions = {1}
    ctx.last_preview = {"cached": True}
    assert agent_loop._typed_question_hold(ctx, "ask_user", {"question": "Which font?"})
    ctx.last_preview = {"edl_version": 2}
    ctx.rendered_versions = {2}
    assert agent_loop._typed_question_hold(ctx, "ask_user", {"question": "Which font?"}) is None


def test_essential_input_and_nonediting_questions_are_not_held(monkeypatch):
    import agent_loop
    import agent_tools
    ctx = NS(ask_ledger={"asks": [{"kind": "film"}]}, rendered_versions=set())
    monkeypatch.setattr(agent_tools, "_question_needs_customer", lambda *a: True)
    assert agent_loop._typed_question_hold(ctx, "ask_user", {"question": "Upload the missing clip?"}) is None
    ctx.ask_ledger = {"asks": [{"kind": "answer"}]}
    monkeypatch.setattr(agent_tools, "_question_needs_customer", lambda *a: pytest.fail("not an edit"))
    assert agent_loop._typed_question_hold(ctx, "ask_user", {}) is None


def test_default_film_routing_and_explicit_holds():
    cfg = asks.parse(None)
    assert cfg["director"] and cfg["pass"] and not cfg["patch"]
    assert not asks.parse({"mode": "on", "director": False})["director"]
    assert asks.parse({"mode": "off"})["mode"] == "off"


def test_global_director_hold_is_preserved(monkeypatch):
    monkeypatch.setattr(flags, "read", lambda db: dict(flags.DEFAULTS, mode="off"))
    monkeypatch.setattr(film, "_make", lambda *a: pytest.fail("Director is held"))
    assert typed.run(None, None, {}, 9, {"director": True}) == {
        "fell": "the Director is off (app_kv.director)"}


def test_story_selector_reads_the_end_of_a_long_class_and_keeps_every_requested_option(monkeypatch):
    import quality_verifier
    ctx = NS(duration=7320, index={"sentences": [
        {"t0": 0, "t1": 30, "text": "Opening context"},
        {"t0": 7100, "t1": 7190, "text": "Late class complete payoff"}]},
        latest_edl=lambda: {"json": {"keep": [[0, 30]]}}, ask_ledger={"asks": []})
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "Give me two calm, interesting clips")
    def select(ctx, name, schema, system, content, **kw):
        assert "Late class complete payoff" in content
        assert "7100.0-7190.0" in content
        assert "SHORT IS A MICRO-STORY" in system
        return {"mode": "clips", "clips": [
            {"start": 7100, "end": 7190, "title": "Late", "hook": "Late payoff"},
            {"start": 200, "end": 290, "title": "Other", "hook": "Other story"}]}, {}
    monkeypatch.setattr(asks, "_call", select)
    clips, _ = typed._selection(ctx)
    assert [c["start"] for c in clips] == [7100, 200]


def test_a_bad_story_window_is_not_silently_shortened_into_a_clip(monkeypatch):
    import quality_verifier
    ctx = NS(duration=7320, index={}, latest_edl=lambda: {"json": {}}, ask_ledger={"asks": []})
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "A complete clip")
    monkeypatch.setattr(asks, "_call", lambda *a, **k: ({"mode": "clips", "clips": [
        {"start": 7300, "end": 7400, "title": "bad", "hook": "bad"}]}, {}))
    with pytest.raises(ValueError, match="outside the source"):
        typed._selection(ctx)


def test_pending_footage_is_joined_and_applied_before_cleanup(monkeypatch):
    events = []
    view = NS(director_state={"footage": {"thread": NS(join=lambda: events.append("joined"))}})
    monkeypatch.setattr(film, "close", lambda ctx: events.append("handoff"))
    monkeypatch.setattr(film, "upgrade", lambda *args: events.append("upgrade") or {"applied": ["real footage"]})
    assert typed._finish_footage(view, None, {}) == {"applied": ["real footage"]}
    assert events == ["joined", "handoff", "upgrade"]


@pytest.mark.parametrize("row, mode", [(None, "on"), ({"mode": "off"}, "off"),
    ({"mode": "on"}, "on"), ("broken", "off")])
def test_typed_footage_permission_preserves_explicit_holds(row, mode):
    from footage import switch
    assert typed.footage_config(NS(run=lambda fn: row))["mode"] == mode
    assert switch.DEFAULTS["mode"] == "off"  # first-film rollout unchanged


def test_typed_footage_unreadable_switch_fails_closed():
    def fail(fn):
        raise RuntimeError("unavailable")
    assert typed.footage_config(NS(run=fail))["mode"] == "off"


def test_long_clip_request_enters_shorts_engine_without_editing_parent(monkeypatch):
    import shorts
    import quality_verifier
    ctx = NS(duration=7320, has_main_video=True, project={})
    clips = [{"start": 7000, "end": 7090, "title": "Payoff", "hook": "A complete story"}]
    monkeypatch.setattr(flags, "read", lambda db: dict(copy.deepcopy(flags.DEFAULTS), mode="on"))
    monkeypatch.setattr(typed, "_selection", lambda ctx: (clips, {}))
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "A 90-second clip")
    monkeypatch.setattr(film, "_make", lambda *a: pytest.fail("parent must stay intact"))
    def enqueue(ctx, selected, request):
        assert selected == clips and request == "A 90-second clip"
        return {"path": "podcast_shorts_engine", "job_id": 23}
    monkeypatch.setattr(shorts, "start_typed_craft", enqueue)
    assert typed.run(ctx, None, {}, 9, {"director": True})["job_id"] == 23


def test_short_child_director_is_bounded_to_its_story(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.touch()
    clip = {"start": 7000, "end": 7090, "title": "Late story", "hook": "The payoff"}
    ctx = NS(duration=7320, has_main_video=True, project_id=7, workdir=str(tmp_path),
        project={"parent_project_id": 6, "meta": {"clip": clip}},
        index={"video": {"duration": 7320}, "words": []}, proxy_path=lambda: str(source),
        agent_model="gpt-6.1-sol", versions_written=[])
    writes = []
    ctx.write_edl = lambda edl, note: writes.append(edl) or "EDL v2"
    ctx.latest_edl = lambda: {"json": {"keep": [[7000, 7090]]}}
    db = NS(run=lambda *a: {"storage_key": "original"})
    monkeypatch.setattr(flags, "read", lambda db: dict(copy.deepcopy(flags.DEFAULTS), mode="on"))
    monkeypatch.setattr(typed, "_selection", lambda *a: pytest.fail("do not reselect from the long parent"))
    monkeypatch.setattr(typed.subprocess, "run", lambda *a, **k: None)
    def make(view, db, job, cfg, deadline, work, rec):
        assert view.duration == 90
        view.apply_score({"keep": [[0, 90]]}, "selected story")
        view.director_version = 2
        return [], ["story"], {}
    monkeypatch.setattr(film, "_make", make)
    got = typed.run(ctx, db, {"id": 1}, 9, {"director": True})
    assert got["source_window"] == [7000, 7090]
    assert writes[0]["keep"] == [[7000, 7090]]
    assert not list(tmp_path.glob("typed-film-*"))


def test_typed_short_wait_follows_continuations_and_requires_current_render(monkeypatch):
    import shorts
    import db as dbx
    monkeypatch.setattr(shorts.time, "sleep", lambda seconds: None)
    states = {20: {"id": 20, "project_id": 8, "user_id": 3, "state": "done",
                   "result": {"continued_job_id": 21}},
              21: {"id": 21, "project_id": 8, "user_id": 3, "state": "done"},
              22: {"id": 22, "project_id": 9, "user_id": 3, "state": "failed"}}
    def run(fn, *args):
        if fn is dbx.get_job:
            return states[args[0]]
        if fn is dbx.get_project:
            return {"id": args[0], "parent_project_id": 7, "user_id": 3}
        if fn is dbx.latest_edl:
            return {"version": 5}
        if fn is dbx.latest_asset:
            return {"id": 80 + args[0], "meta": {"edl_version": 5 if args[0] == 8 else 4},
                    "width": 720, "height": 1280, "duration_s": 90}
        raise AssertionError(fn)
    films = shorts._wait_typed_children(NS(run=run), {"id": 10, "project_id": 7, "user_id": 3},
        [{"child_project_id": 8, "editor_job_id": 20}, {"child_project_id": 9, "editor_job_id": 22}])
    films = {f["child_project_id"]: f for f in films}
    assert films[8]["editor_job_id"] == 21 and films[8]["render_asset_id"] == 88
    assert films[9]["render_asset_id"] is None


def test_typed_short_wait_refuses_cross_owner_child(monkeypatch):
    import shorts
    import db as dbx
    def run(fn, *args):
        if fn is dbx.get_project:
            return {"id": 8, "parent_project_id": 7, "user_id": 999}
        return {"project_id": 8, "user_id": 3, "state": "done"}
    with pytest.raises(RuntimeError, match="ownership"):
        shorts._wait_typed_children(NS(run=run), {"id": 10, "project_id": 7, "user_id": 3},
            [{"child_project_id": 8, "editor_job_id": 20}])
