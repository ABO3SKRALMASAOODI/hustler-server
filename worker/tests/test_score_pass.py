"""The whole-film pass (lane E, round 2, 12 Nov 2026): a whole-film ask that keeps the customer's cut
reaches a route that does all of it. Round 1 on copies: every such ask fell to the loop, which moved
15 of 90 photos and placed 2 of 35-50 pictures. The route is set logic on the ledger model's fields;
the shots, faces and coverage are measured; the writes go through the ordinary tools."""
import json
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_tools
from score import asks, patch

EDL = {"keep": [[0.0, 60.0]], "texts": [], "sfx": [], "overlays": [], "effects": {}}
INDEX = {"video": {"duration": 60.0, "width": 1920, "height": 1080},
         "shots": [{"id": i + 1, "start": i * 10.0, "end": i * 10.0 + 10.0} for i in range(6)],
         "spatial": {"samples": [{"t": 15.0, "faces": [[0.4, 0.2, 0.6, 0.5]]}]},
         "words": []}


def _ask(kind="edit", span="part", cut=None, **kw):
    a = {"said": "x", "want": "y", "kind": kind, "span": span, "items": [], "how": "look", "where": "whole film", "pass": "z"}
    if cut is not None:
        a["cut"] = cut
    a.update(kw)
    return a


# ------------------------------------------------------------------ the route

@pytest.mark.parametrize("asks_in,route", [
    ([_ask("film", cut="keep")], "pass"),
    ([_ask("edit", "whole"), _ask("keep")], "pass"),
    ([_ask("film", cut="new"), _ask("edit", "whole")], "film"),
    ([_ask("edit", "part")], "loop"),
    ([_ask("change", items=["texts:t1"])], "loop"),     # nothing named in this EDL: an ordinary edit
    ([_ask("keep"), _ask("answer")], "reply"),
])
def test_a_whole_film_ask_that_keeps_the_cut_routes_to_the_pass(asks_in, route):
    assert asks.settle({"lang": "en", "asks": asks_in}, EDL)["route"] == route


def test_span_and_cut_are_the_models_and_cut_is_only_for_film():
    led = asks.settle({"lang": "en", "asks": [_ask("edit", "sideways", cut="keep"), _ask("film", "whole", cut="odd")]}, EDL)
    assert [(a["span"], a["cut"]) for a in led["asks"]] == [("part", "none"), ("whole", "new")]


def test_the_ledger_schema_asks_the_model_for_span_and_cut():
    item = asks.LEDGER_SCHEMA["properties"]["asks"]["items"]
    assert {"span", "cut"} <= set(item["required"]) and set(item["required"]) == set(item["properties"])


# ------------------------------------------------------------------ measured on the film's clock

def test_shots_are_mapped_through_the_cut():
    edl = {"keep": [[0.0, 15.0], [25.0, 60.0]]}
    shots = asks.program_shots(edl, INDEX)
    assert shots[0] == (0.0, 10.0) and shots[1] == (10.0, 15.0) and (15.0, 20.0) in shots
    assert asks.program_seconds(edl) == pytest.approx(50.0)


def test_the_check_counts_the_whole_film():
    edl = {"keep": [[0.0, 60.0]], "effects": {"zooms": [{"start": 1, "end": 9}, {"start": 21, "end": 29}],
                                               "transition": {"style": "dissolve", "scope": "scene", "junctions": [0, 1], "duration_s": 1.2}},
           "overlays": [{"start": 30.0, "duration_s": 6.0}]}
    m = asks.measured(edl, asks.program_shots(edl, INDEX), 60.0)
    assert m["shots_measured"] == 6 and m["shots_with_a_camera_move"] == 2
    assert m["share_of_film_under_pictures"] == pytest.approx(0.1) and m["upload_kept_share"] == 1.0
    assert m["upload_kept_in_order"] and m["transitions"]["junctions"] == 2
    assert asks.facts(edl, None, "", [], 60.0)["whole_film"]["program_seconds"] == 60.0


# ------------------------------------------------------------------ the pass

def test_units_split_a_long_film_where_a_shot_ends():
    assert patch.units(348.0, [], []) == [(0.0, 348.0)]
    shots = [(i * 7.0, i * 7.0 + 7.0) for i in range(128)]
    parts = patch.units(895.0, shots, [])
    assert parts[0][0] == 0.0 and parts[-1][1] == 895.0 and len(parts) == 3
    assert all(b == c for (_a, b), (c, _d) in zip(parts, parts[1:]))
    assert all(round(b / 7.0, 6).is_integer() for _a, b in parts[:-1])


def test_a_move_row_puts_one_move_on_every_measured_shot():
    ctx = NS(index=INDEX)
    shots = asks.program_shots(EDL, INDEX)
    ops = patch._move_ops(ctx, EDL, {"t0": 0, "t1": 40, "mode": "push_in", "strength": 0.05, "aim": "face", "for": ["a1"]}, shots)
    assert [o["tool"] for o in ops] == ["add_zoom"] * 4
    assert ops[1]["args"]["cx"] == 0.5 and ops[1]["args"]["cy"] == pytest.approx(0.32)      # the measured face
    assert ops[0]["args"]["cx"] == 0.5 and ops[0]["args"]["cy"] == 0.5
    drift = patch._move_ops(ctx, EDL, {"t0": 40, "t1": 60, "mode": "drift_right", "strength": 0.06, "aim": "center", "for": []}, shots)
    assert [o["tool"] for o in drift] == ["add_zoom_path"] * 2
    kf = drift[0]["args"]["keyframes"]
    assert kf[0]["cx"] < kf[1]["cx"] and kf[0]["t"] > 40.0 and kf[1]["t"] < 50.0


def test_a_refused_op_is_dropped_and_the_rest_is_written(monkeypatch):
    calls = []

    def recipe(ctx, operations, brief=None):
        calls.append([op["tool"] for op in operations])
        if any(op["tool"] == "add_text" for op in operations):
            return "RECIPE ABORTED at operation 2 (add_text); no EDL version was created.\nREJECTED: text past the end"
        ctx.versions_written.append(7)
        return "EDL v7: ok"
    monkeypatch.setattr(agent_tools, "apply_edit_recipe", recipe)
    ctx = NS(versions_written=[])
    version, dropped = patch._commit(ctx, [{"tool": "add_zoom", "args": {}}, {"tool": "add_text", "args": {}},
                                          {"tool": "set_fades", "args": {}}], "x")
    assert version == 7 and calls[-1] == ["add_zoom", "set_fades"]
    assert dropped[0][0]["tool"] == "add_text" and "past the end" in dropped[0][1]


def test_the_pass_writes_through_the_real_recipe_although_it_left_the_editors_catalog():
    # 12 Nov replay of p5345: agent_tools.execute answered "Unknown tool 'apply_edit_recipe'" (retired from
    # the live catalog in 0cecaeec), every op was dropped and the pass wrote nothing. The real recipe, on a
    # real tool context, writes one version with each op staged by its own ordinary tool.
    import tempfile
    import db as dbx
    from schemas import default_edl

    assert "apply_edit_recipe" not in agent_tools.TOOLS

    class Db:
        def __init__(self):
            self.rows = [{"version": 1, "json": default_edl(20.0)}]

        def run(self, fn, *args):
            if fn is dbx.latest_edl:
                return self.rows[-1]
            if fn is dbx.insert_edl:
                self.rows.append({"version": self.rows[-1]["version"] + 1, "json": args[1]})
                return self.rows[-1]["version"]
            if fn is dbx.get_edl_version:
                return next(x for x in self.rows if x["version"] == args[1])
            raise AssertionError(f"unexpected DB call: {fn}")

    fake = Db()
    ctx = agent_tools.ToolContext(fake, {"id": 9, "user_id": 3}, {"id": 7, "chat_session_id": 11},
                                  {"video": {"duration": 20.0, "width": 1920, "height": 1080, "fps": 30.0}, "words": []},
                                  tempfile.mkdtemp())
    ctx.user_message = "make the whole film move"
    version, dropped = patch._commit(ctx, [
        {"tool": "add_zoom", "args": {"start": 2.0, "end": 6.0, "mode": "push_in", "strength": 0.06}},
        {"tool": "set_transitions", "args": {"style": "dissolve", "duration_s": 0.5}}], "whole-film pass, program 0-20 s")
    assert version == 2 and len(fake.rows) == 2
    assert [d[0]["tool"] for d in dropped] == ["set_transitions"] and "no cut" in dropped[0][1]   # one cut-less film
    assert len(fake.rows[-1]["json"]["effects"]["zooms"]) == 1


class _Ctx(NS):
    def latest_edl(self):
        return {"version": 3, "json": self.edl}


def _pass_ctx(tmp_path):
    return _Ctx(edl=json.loads(json.dumps(EDL)), index=INDEX, duration=60.0, workdir=str(tmp_path),
                versions_written=[], stock_results={}, direct_sight=True, _pending_visual_review_assets=set())


def test_the_pass_finds_checks_and_writes_the_whole_film_in_one_recipe(monkeypatch, tmp_path):
    plan = {"moves": [{"t0": 0, "t1": 60, "mode": "push_in", "strength": 0.05, "aim": "center", "for": ["a1"]}],
            "pictures": [{"at": 12.0, "duration_s": 4.0, "query": "Barack Obama press conference 2014", "variants": [],
                          "kind": "photo", "purpose": "the president", "sound": "whoosh_soft_1", "for": ["a2"]},
                         {"at": 33.0, "duration_s": 4.0, "query": "ICE detention center", "variants": [],
                          "kind": "photo", "purpose": "the place", "sound": "", "for": ["a2"]}],
            "ops": [{"tool": "set_transitions", "args_json": '{"style": "dissolve", "duration_s": 1.0}', "for": ["a3"]},
                    {"tool": "render_preview", "args_json": "{}", "for": []}],
            "left": [{"ask": "a4", "why": "music the customer did not supply"}], "say": "ok"}
    seen = {}
    monkeypatch.setattr(asks, "_call", lambda ctx, name, schema, system, content, max_tokens=4000: (
        seen.setdefault("content", content) and plan, {"usd": 0.01, "s": 2.0, "model": "m"}))

    def execute(ctx, name, args):
        seen.setdefault("tools", []).append(name)
        if name == "research_broll":
            assert ctx.direct_sight is False                 # nothing queues into the editor's eyes
            ctx._last_broll_board_cast = {"ok": True}
            for m in args["moments"]:
                hit = {"id": f"broll:{m['id']}:1", "_broll_moment": {"id": m["id"]}}
                if m["query"].startswith("Barack"):
                    hit["_broll_cast"] = {"moment_id": m["id"], "decision": "use"}
                else:
                    hit["_broll_cast_abstain"] = {"visible_evidence": "a generic jail, not this case"}
                ctx.stock_results[hit["id"]] = hit
            return "candidates"
        if name == "add_stock_media":
            ctx._pending_visual_review_assets.add("fetched/1/obama.jpg")
            return "Added stock image \"x\" to the project: storage_key=fetched/1/obama.jpg. It is SILENT"
        raise AssertionError(name)
    monkeypatch.setattr(agent_tools, "execute", execute)

    def recipe(ctx, operations, brief=None):
        seen["ops"] = operations
        ctx.versions_written.append(4)
        return "EDL v4: applied"
    monkeypatch.setattr(agent_tools, "apply_edit_recipe", recipe)
    ctx = _pass_ctx(tmp_path)
    led = asks.settle({"lang": "es", "asks": [_ask("film", "whole", cut="keep"), _ask("edit", "whole"), _ask("edit", "whole"),
                                              _ask("edit", "part")]}, EDL)
    got = patch.throughout(ctx, led, "mejora mi video completo")
    assert got["version"] == 4 and ctx.direct_sight is True
    tools = [o["tool"] for o in seen["ops"]]
    assert tools.count("add_zoom") == 6 and tools.count("add_overlay") == 1 and "set_transitions" in tools
    assert "render_preview" not in tools                     # only the pass's tools
    ov = next(o for o in seen["ops"] if o["tool"] == "add_overlay")["args"]
    assert ov == {"asset_key": "fetched/1/obama.jpg", "start": 12.0, "duration_s": 4.0, "fit": "cover", "entrance": "fade", "exit": "fade"}
    assert {"tool": "add_sfx", "args": {"storage_key": "sound:whoosh_soft_1", "at": 12.0, "purpose": "entrance of a found picture"}} in seen["ops"]
    assert got["pictures"]["placed"] == 1 and "generic jail" in got["pictures"]["kept_off"][0]["why"]
    assert not ctx._pending_visual_review_assets            # judged by the rendition review here
    note = patch.pass_note(got)
    assert "EDL v4" in note and "6 add_zoom" in note and "music the customer did not supply" in note
    # the editor is told which ask each piece of work serves, so it doesn't thin what was asked for
    assert {x["ask"]: x["ops"] for x in got["serves"]}["a1"] == {"add_zoom": 6}
    assert "- for a1 (y): 6 add_zoom. The customer asked for these" in note
    assert len(got["sources"]) == 1 and "Sources of the placed pictures" in note
    text = seen["content"][0]["text"]
    assert "unit 1 of 1" in text and "Measured shots in this unit (6)" in text and "add_zoom:" in text


def test_a_pass_that_cannot_plan_falls_to_the_loop(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ValueError("record_pass_plan: no structured answer")
    monkeypatch.setattr(asks, "_call", boom)
    got = patch.throughout(_pass_ctx(tmp_path), asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL), "x")
    assert got["fell"].startswith("ValueError") and "wrote nothing" in patch.pass_note(got)


# ------------------------------------------------------------------ the seam

class _Db:
    def __init__(self, row):
        self.row = row

    def run(self, fn, *a):
        row = self.row

        class Cur:
            def __enter__(s):
                return s

            def __exit__(s, *e):
                return False

            def execute(s, *a):
                pass

            def fetchone(s):
                return {"value": row}

        class Conn:
            def cursor(s):
                return Cur()
        return fn(Conn())


def _seam_ctx():
    return NS(claude_trial=None, user_message="add b-roll all through", verification_request=None,
              request_contract_history=[], latest_edl=lambda: {"version": 1, "json": EDL})


def test_the_seam_sends_whole_film_work_to_the_pass(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda ctx, req, hist=None: asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL))
    monkeypatch.setattr(patch, "throughout", lambda ctx, led, req, budget: {
        "version": 5, "applied": {"add_overlay": 12}, "units": 2, "shots": 1, "pictures": {"asked": 14, "placed": 12},
        "usage": {"usd": 0.02}})
    ctx = _seam_ctx()
    note = agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert "whole-film pass already worked through the whole film" in note and "12 of 14 placed" in note
    assert ctx.ask_ledger["routed"]["to"] == "pass" and ctx.ask_ledger["routed"]["pass"]["version"] == 5
    assert asks.summary(ctx.ask_ledger)["usd"] == pytest.approx(0.02)


def test_a_director_fall_on_a_long_source_still_reaches_the_pass(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda ctx, req, hist=None: asks.settle(
        {"lang": "en", "asks": [_ask("film", "whole", cut="new"), _ask("edit", "whole")]}, EDL))
    monkeypatch.setattr(asks, "direct_film", lambda *a: {"fell": "source 473 s is past the Director's 180 s"})
    called = []
    monkeypatch.setattr(patch, "throughout", lambda ctx, led, req, budget: called.append(budget) or {
        "version": 6, "applied": {"add_zoom": 40}, "units": 2, "shots": 40, "usage": {}})
    ctx = _seam_ctx()
    note = agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert called == [600.0] and "EDL v6" in note and "full upload" in note
    assert ctx.ask_ledger["routed"]["to"] == "director" and ctx.ask_ledger["routed"]["pass"]["version"] == 6


def test_the_switch_can_turn_the_pass_off(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda ctx, req, hist=None: asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL))
    monkeypatch.setattr(patch, "throughout", lambda *a: pytest.fail("the pass is off"))
    ctx = _seam_ctx()
    agent_loop._typed_asks_start(ctx, _Db('{"mode": "on", "pass": false}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert ctx.ask_ledger["routed"] == {"to": "loop"}
    assert asks.parse('{"mode": "on", "pass_s": 5000}')["pass_s"] == 1200.0
