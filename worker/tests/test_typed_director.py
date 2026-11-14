"""Typed library routing and clocks, independent of film style/quality ratings."""
import copy
from types import SimpleNamespace as NS

import pytest

from score import asks, film, flags, patch, typed


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
             apply_score=lambda *a: pytest.fail("first-film scope bypass"))
    view = typed.WindowContext(ctx, {"video": {"duration": 90}}, "proxy", 7000, "original", 9)
    view._sfx_grants = {"sound"}
    assert ctx._sfx_grants == {"sound"}
    assert view.apply_score({"keep": [[0, 90]]}, "film").startswith("REJECTED")
    assert writes[0]["keep"] == [[7000, 7090]]
    assert not hasattr(ctx, "director_version")


def test_four_minute_typed_film_reaches_director_without_lifting_first_film_cap(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.touch()
    ledger = {"asks": [{"kind": "film", "cut": "new", "said": "polish", "want": "polished film"}]}
    ctx = NS(duration=240, has_main_video=True, project_id=7, workdir=str(tmp_path),
        index={"video": {"duration": 240}}, proxy_path=lambda: str(source),
        agent_model="gpt-6.1-sol", ask_ledger=copy.deepcopy(ledger), versions_written=[])
    ctx.write_edl = lambda edl, note: ctx.versions_written.append(2) or "EDL v2"
    db = NS(run=lambda *a: {"storage_key": "original"})
    monkeypatch.setattr(flags, "read", lambda db: dict(copy.deepcopy(flags.DEFAULTS), mode="on"))

    def make(view, db, job, cfg, deadline, work, rec):
        assert cfg["models"] == {"scout": "gpt-6.1-sol", "direct": "gpt-6.1-sol"}
        assert view.duration == 240
        assert view.apply_score({"keep": [[0, 240]]}, "film") == "EDL v2"
        view.director_version = 2
        return [], ["crafted film"], {}
    monkeypatch.setattr(film, "_make", make)
    got = asks.direct_film(ctx, db, {"id": 1}, 9, asks.parse({"mode": "on"}))
    assert got["version"] == 2 and got["path"] == "whole_film_director"
    assert ctx.ask_ledger == ledger
    assert not list(tmp_path.glob("typed-film-*"))
    assert flags.MAX_SOURCE_S == 180  # first-film admission remains unchanged


def test_long_whole_request_uses_existing_segmented_library_not_clip_selection(monkeypatch):
    ctx = NS(duration=2700, has_main_video=True, ask_ledger={"asks": []})
    monkeypatch.setattr(flags, "read", lambda db: dict(copy.deepcopy(flags.DEFAULTS), mode="on"))
    monkeypatch.setattr(typed, "_selection", lambda ctx: (None, {"usd": .01}))
    import quality_verifier
    monkeypatch.setattr(quality_verifier, "request_text_for", lambda ctx: "the entire match")
    monkeypatch.setattr(patch, "throughout", lambda ctx, ledger, request, budget:
                        {"version": 3, "applied": {}, "request": request})
    got = typed.run(ctx, None, {}, 9, {"director": True})
    assert got["path"] == "segmented_whole_film"
    assert got["request"] == "the entire match"


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
