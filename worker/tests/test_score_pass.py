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
    monkeypatch.setattr(asks, "_call", lambda ctx, name, schema, system, content, max_tokens=4000, **kw: (
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


# ------------------------------------------------------------------ round 3: aimed moves, own uploads

def test_a_move_aimed_at_a_subject_frames_the_box_located_on_its_picture():
    ctx = NS(index={})
    shots = [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    aims = {(0.0, 4.0): {"toward": "the dog's eyes", "box": [0.4, 0.2, 0.6, 0.3], "shows": "dog close-up", "mode": "push_in", "for": ["a6"]},
            (4.0, 8.0): {"toward": "", "box": None, "shows": "a wide yard", "mode": "drift_left", "for": ["a5"]}}
    ops = patch._move_ops(ctx, {"keep": [[0, 12]]}, {"t0": 0, "t1": 12, "mode": "push_in", "strength": 0.05, "aim": "subject", "for": ["a5"]}, shots, aims)
    assert ops[0]["tool"] == "add_zoom" and ops[0]["args"]["rect"] == [0.4, 0.2, 0.6, 0.3]
    assert "toward the dog's eyes" in ops[0]["args"]["purpose"] and "a6" in ops[0]["args"]["purpose"]
    assert ops[1]["tool"] == "add_zoom_path" and "a wide yard" in ops[1]["args"]["purpose"]
    assert ops[2]["tool"] == "add_zoom" and ops[2]["args"]["cx"] == 0.5        # not looked at: centred, as before
    # a reframed film: the frame's fractions are not the picture's, so no box is used
    ops = patch._move_ops(ctx, {"keep": [[0, 12]], "frame": {"ratio": "9:16"}},
                          {"t0": 0, "t1": 4, "mode": "push_in", "strength": 0.05, "aim": "subject", "for": []}, shots, aims)
    assert "rect" not in ops[0]["args"]


def test_pictures_never_cover_the_same_second():
    kept, off = patch._no_overlap([(10.0, 5.0, "a"), (12.0, 5.0, "b"), (30.0, 4.0, "c"), (40.0, 1.0, "d")], [(28.0, 31.0)])
    spans = [(a, a + d) for a, d, _p in kept]
    assert all(not (x0 < y1 and x1 > y0) for i, (x0, x1) in enumerate(spans) for (y0, y1) in spans[i + 1:])
    assert [p for _a, _d, p in kept] == ["a", "b", "c"] and [p for p, _w in off] == ["d"]
    assert kept[1][0] >= 15.0 and kept[2][0] >= 31.0


def test_the_customers_own_uploads_are_placed_by_the_pass_without_overlap(monkeypatch, tmp_path):
    # Round 2, p5460: "add in these b roll clips that I took ... #5 when I introduce the honey, then spread
    # the others out" had no tool in the pass, and the loop laid #2 over #3.
    own = [{"n": 2, "key": "uploads/3/a.mov", "kind": "video_clip", "name": "IMG_1.mov", "duration_s": 9.0, "in_film": [], "frame": None},
           {"n": 3, "key": "uploads/3/b.mov", "kind": "video_clip", "name": "IMG_2.mov", "duration_s": 9.0, "in_film": [], "frame": None}]
    monkeypatch.setattr(patch, "_own_media", lambda ctx, edl: own)
    plan = {"moves": [], "own": [{"upload": 2, "at": 2.0, "duration_s": 6.0, "from_s": 1.0, "for": ["a1"]},
                                 {"upload": 3, "at": 5.0, "duration_s": 6.0, "from_s": 0.0, "for": ["a1"]},
                                 {"upload": 9, "at": 1.0, "duration_s": 3.0, "from_s": 0.0, "for": ["a1"]}],
            "pictures": [], "ops": [], "left": [], "say": "x"}
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (plan, {"usd": 0.001, "s": 1.0, "model": "m"}))
    written = []
    monkeypatch.setattr(patch, "_commit", lambda ctx, ops, label: written.append(ops) or (7, []))
    got = patch.throughout(_pass_ctx(tmp_path), asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL), "x")
    overlays = [op["args"] for op in written[0] if op["tool"] == "add_overlay"]
    assert [o["asset_key"] for o in overlays] == ["uploads/3/a.mov", "uploads/3/b.mov"]
    assert overlays[0]["source_start_s"] == 1.0 and overlays[1]["start"] >= overlays[0]["start"] + overlays[0]["duration_s"]
    assert got["own"]["placed"] == 2 and got["own"]["kept_off"][0]["upload"] == 9
    assert "Their own uploads: 2 placed" in patch.pass_note(dict(got, version=7))


def test_subject_aims_are_looked_at_once_for_the_shots_the_moves_cover(monkeypatch, tmp_path):
    plan = {"moves": [{"t0": 0, "t1": 60, "mode": "push_in", "strength": 0.05, "aim": "subject", "for": ["a1"]}],
            "own": [], "pictures": [], "ops": [], "left": [], "say": "x"}
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (plan, {"usd": 0.001, "s": 1.0, "model": "m"}))
    looked = []

    def aims(ctx, ledger, edl, spans, deadline):
        looked.append(sorted(set(spans)))
        return {spans[0]: {"toward": "the truck", "box": [0.1, 0.5, 0.3, 0.7], "shows": "truck", "mode": "push_in", "for": ["a1"]}}
    monkeypatch.setattr(patch, "_aims", aims)
    written = []
    monkeypatch.setattr(patch, "_commit", lambda ctx, ops, label: written.append(ops) or (7, []))
    got = patch.throughout(_pass_ctx(tmp_path), asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL), "x")
    assert len(looked) == 1 and looked[0]
    assert written[0][0]["args"].get("rect") == [0.1, 0.5, 0.3, 0.7]
    assert got["aims"]["aimed"] == 1 and got["aims"]["toward"] == ["the truck"]


def test_aims_look_at_one_frame_per_shot_and_read_the_located_box(monkeypatch, tmp_path):
    import llm
    import media
    monkeypatch.setattr(media, "frame_at", lambda src, t, dst, width=None: open(dst, "wb").write(b"x"))
    monkeypatch.setattr(llm, "image_part", lambda path: {"type": "image_url", "image_url": {"url": path}})
    calls = []

    def fake(ctx, name, schema, system, content, max_tokens=4000, **kw):
        calls.append((name, kw.get("model"), sum(1 for c in content if c["type"] == "image_url")))
        return {"shots": [{"shot": 1, "shows": "dog close-up", "toward": "the dog's eyes", "box": [0.4, 0.2, 0.6, 0.3],
                           "mode": "push_in", "for": ["a6"]},
                          {"shot": 2, "shows": "yard", "toward": "", "box": [0, 0, 1, 1], "mode": "drift_left", "for": []}]}, \
            {"usd": 0.0, "s": 0.0, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    ctx = _pass_ctx(tmp_path)
    ctx.proxy_path = lambda: "/proxy.mp4"
    ledger = asks.settle({"lang": "en", "asks": [_ask("edit", "whole")]}, EDL)
    got = patch._aims(ctx, ledger, EDL, [(0.0, 4.0), (4.0, 8.0), (0.0, 4.0)], deadline=1e18)
    assert calls == [("record_shot_aims", asks.CHECK_MODEL, 2)]
    assert got[(0.0, 4.0)]["box"] == [0.4, 0.2, 0.6, 0.3] and got[(0.0, 4.0)]["toward"] == "the dog's eyes"
    assert got[(4.0, 8.0)]["box"] is None and got[(4.0, 8.0)]["mode"] == "drift_left"
