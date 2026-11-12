"""The ask ledger for typed requests (lane E, 12 Nov 2026): the switch, the digest, routing by the
model's kinds, the Patch on named items, the check on the rendered film, one repair, and an outcome
that is never fulfilled with an ask open."""
import json
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_tools
import spend_ledger
from score import asks, patch

EDL = {"keep": [[0.0, 20.0]],
       "texts": [{"id": "t1", "text": "Big sale", "start": 0.0, "end": 3.0, "template": "title", "size_scale": 1.0}],
       "sfx": [{"id": "s1", "storage_key": "sound:whoosh", "at": 2.0}, {"id": "s2", "storage_key": "sound:pop", "at": 5.0}],
       "captions": {"mode": "from_transcript", "style": {"color": "#FFFFFF", "size": "m"}}}


def _ask(kind="edit", items=(), **kw):
    a = {"said": "x", "want": "y", "kind": kind, "items": list(items), "how": "look", "where": "whole film", "pass": "z"}
    a.update(kw)
    return a


# ------------------------------------------------------------------ the switch

def test_no_row_is_internal_accounts_only_and_a_bad_row_is_off():
    assert asks.parse(None)["mode"] == "internal"
    for raw in ("", "{", "[]", '{"mode": "maybe"}', '{"mode": "on", "repairs": "x"}'):
        assert asks.parse(raw)["mode"] == "off", raw
    assert asks.parse('{"mode": "on", "director": false}')["director"] is False


def test_admission():
    internal = next(iter(spend_ledger.INTERNAL_USER_IDS))
    cfg = asks.parse(None)
    assert asks.admitted(cfg, internal)
    assert not asks.admitted(cfg, 999999)
    assert asks.admitted(asks.parse('{"mode": "internal", "user_ids": [999999]}'), 999999)
    assert asks.admitted(asks.parse('{"mode": "on"}'), 999999)
    assert not asks.admitted(asks.parse('{"mode": "off"}'), internal)
    assert asks.admitted(asks.parse('{"mode": "off", "user_ids": [7]}'), 7)


def test_an_unreadable_row_is_off():
    class Boom:
        def run(self, *a):
            raise RuntimeError("no database")
    assert asks.read(Boom())["mode"] == "off"


# ------------------------------------------------------------------ digest, ledger, route

def test_every_digest_id_resolves():
    text, items = asks.digest(EDL)
    assert {"texts:t1", "sfx:s1", "sfx:s2", "captions"} <= set(items)
    for iid in items:
        assert iid in text
    assert "20.0 s" in text.splitlines()[0]


def test_a_change_naming_nothing_real_is_an_ordinary_edit():
    ledger = asks.settle({"lang": "en", "asks": [_ask("change", ["texts:nope"]), _ask("change", ["texts:t1"])]}, EDL)
    assert [a["kind"] for a in ledger["asks"]] == ["edit", "change"]
    assert [a["id"] for a in ledger["asks"]] == ["a1", "a2"]
    assert ledger["route"] == "loop"


@pytest.mark.parametrize("kinds,route", [
    (["answer"], "reply"), (["keep", "answer"], "reply"), (["change"], "patch"), (["change", "keep"], "patch"),
    (["change", "edit"], "loop"), (["edit"], "loop"), (["film", "change"], "film"), (["film", "keep"], "film")])
def test_route_is_set_logic_on_the_models_kinds(kinds, route):
    assert asks.route([_ask(k) for k in kinds]) == route


def test_the_ledger_is_one_model_call(monkeypatch):
    seen = {}

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        seen["name"], seen["content"] = name, content
        return {"lang": "es", "asks": [_ask("change", ["sfx:s2"], said="quita el pop")]}, {"usd": 0.001, "s": 1.0, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    ctx = NS(latest_edl=lambda: {"version": 3, "json": EDL}, index={"video": {"duration": 20}}, duration=20.0)
    ledger = asks.build(ctx, "quita el pop del segundo 5")
    assert seen["name"] == "record_ask_ledger" and "quita el pop" in seen["content"] and "sfx:s2" in seen["content"]
    assert ledger["route"] == "patch" and ledger["lang"] == "es"


# ------------------------------------------------------------------ the Patch

def test_ops_merge_remove_and_refuse_unknown_ids():
    new, applied, refused = patch.apply_ops(EDL, [
        {"item": "texts:t1", "set_json": '{"size_scale": 1.5, "id": "hijack"}', "remove": False},
        {"item": "sfx:s1", "set_json": "{}", "remove": True},
        {"item": "captions", "set_json": '{"style": {"color": "#FFD400"}}', "remove": False},
        {"item": "texts:zz", "set_json": "{}", "remove": True}])
    assert new["texts"][0]["size_scale"] == 1.5 and new["texts"][0]["id"] == "t1"
    assert [s["id"] for s in new["sfx"]] == ["s2"]
    assert new["captions"]["style"] == {"color": "#FFD400", "size": "m"}
    assert refused == ["texts:zz: no such item"] and len(applied) == 3
    assert EDL["texts"][0]["size_scale"] == 1.0 and len(EDL["sfx"]) == 2


class _Ctx(NS):
    def latest_edl(self):
        return {"version": self.versions_written[-1] if self.versions_written else 1, "json": self.edl}

    def write_edl(self, new, desc):
        if self.refuse:
            return self.refuse.pop(0)
        self.edl = new
        self.versions_written.append(len(self.versions_written) + 2)
        self.desc = desc
        return f"EDL v{self.versions_written[-1]}: ok"


def _ledger(kind="change", items=("texts:t1",)):
    return asks.settle({"lang": "en", "asks": [_ask(kind, items, want="title bigger")]}, EDL)


def test_a_patch_writes_through_the_ordinary_write_path(monkeypatch):
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (
        {"ops": [{"item": "texts:t1", "set_json": '{"size_scale": 1.4}', "remove": False}], "say": "Bigger."},
        {"usd": 0.002, "s": 1.0, "model": "m"}))
    ctx = _Ctx(edl=json.loads(json.dumps(EDL)), versions_written=[], refuse=[])
    got = patch.run(ctx, _ledger(), "make the title bigger")
    assert got["version"] == 2 and ctx.edl["texts"][0]["size_scale"] == 1.4 and "texts:t1" in ctx.desc


def test_a_refused_patch_is_corrected_once_then_goes_to_the_loop(monkeypatch):
    calls = []

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        calls.append(content)
        return {"ops": [{"item": "texts:t1", "set_json": '{"size_scale": 9}', "remove": False}], "say": ""}, {"usd": 0.001, "s": 1, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    ctx = _Ctx(edl=EDL, versions_written=[], refuse=["REJECTED: size_scale", "REJECTED: size_scale"])
    got = patch.run(ctx, _ledger(), "huge title")
    assert "fell" in got and len(calls) == 2 and "refused" in calls[1]


def test_a_patch_op_on_an_item_nobody_named_is_dropped(monkeypatch):
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (
        {"ops": [{"item": "sfx:s1", "set_json": "{}", "remove": True}], "say": ""}, {"usd": 0, "s": 0, "model": "m"}))
    ctx = _Ctx(edl=EDL, versions_written=[], refuse=[])
    assert "fell" in patch.run(ctx, _ledger(), "title bigger")


# ------------------------------------------------------------------ the check, the repair, the outcome

def _verdict(*states):
    return {"asks": [{"id": f"a{i + 1}", "state": s, "seen": "", "line": f"line {i + 1}"} for i, s in enumerate(states)]}


def test_the_check_reads_the_rendered_film_and_an_unanswered_ask_cannot_be_told(monkeypatch, tmp_path):
    seen = {}

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        seen["content"] = content
        return {"asks": [{"id": "a1", "state": "done", "seen": "title at 0-3 s", "line": "Title is bigger."}]}, {"usd": 0.003, "s": 2, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    monkeypatch.setattr(asks, "frame_pages", lambda ctx, preview, limit=3: [])
    ledger = asks.settle({"lang": "en", "asks": [_ask("change", ["texts:t1"]), _ask("edit")]}, EDL)
    v = asks.check(NS(workdir=str(tmp_path)), ledger, "Done.", EDL, {"duration_s": 24.0, "edl_version": 2}, "title bigger")
    assert [r["state"] for r in v["asks"]] == ["done", "cannot_tell"]
    text = seen["content"][0]["text"]
    assert '"rendered_seconds": 24.0' in text and "title bigger" in text


def test_outcome_is_never_fulfilled_with_an_ask_open():
    ledger = _ledger()
    assert asks.outcome(ledger, _verdict("done"), "fulfilled") == "fulfilled"
    for s in ("not_done", "partly", "cannot_tell"):
        assert asks.outcome(ledger, _verdict(s), "fulfilled") == "partial"
    assert asks.outcome(ledger, None, "fulfilled") == "partial"           # an edit nobody checked
    assert asks.outcome(ledger, _verdict("not_done"), "blocked") == "blocked"
    reply = asks.settle({"lang": "en", "asks": [_ask("answer")]}, EDL)
    assert asks.outcome(reply, _verdict("done"), "blocked") == "fulfilled"  # nothing to change, answered
    assert asks.outcome(None, None, "fulfilled") == "fulfilled"


def test_the_reply_lists_asks_only_when_one_is_open():
    ledger = asks.settle({"lang": "en", "asks": [_ask(), _ask()]}, EDL)
    assert asks.reply_block(ledger, _verdict("done", "done")) == ""
    block = asks.reply_block(ledger, _verdict("done", "not_done"))
    assert "- line 1" in block and "- line 2" in block


def _loop_ctx(ledger, version=2, preview_version=2):
    return NS(ask_ledger=ledger, ask_verdict=None, last_preview={"edl_version": preview_version, "duration_s": 10},
              latest_edl=lambda: {"version": version, "json": EDL}, versions_written=[version], workdir="/tmp")


def test_one_repair_then_the_reply_says_what_is_open(monkeypatch):
    ledger = _ledger()
    ledger["cfg"] = {"repairs": 1, "check": True}
    monkeypatch.setattr(asks, "check", lambda *a, **k: _verdict("not_done"))
    monkeypatch.setattr(agent_loop, "_turn_change_list", lambda ctx: "")
    ctx = _loop_ctx(ledger)
    note = agent_loop._asks_check(ctx, "Done!")
    assert note and "not done" in note and ledger["repairs"] == 1
    assert agent_loop._asks_check(ctx, "Done again!") is None             # the one repair is spent
    final, outcome = agent_loop._asks_settle(ctx, "Done!", "fulfilled")
    assert outcome == "partial" and "line 1" in final


def test_a_stale_verdict_never_vouches_for_a_newer_version(monkeypatch):
    ledger = _ledger()
    ctx = _loop_ctx(ledger, version=5)
    ctx.ask_verdict = dict(_verdict("done"), edl_version=4)
    final, outcome = agent_loop._asks_settle(ctx, "Done.", "fulfilled")
    assert outcome == "partial" and final == "Done."


def test_the_check_uses_only_this_versions_render(monkeypatch):
    got = {}

    def fake_check(ctx, ledger, draft, edl, preview, changes):
        got["preview"] = preview
        return _verdict("done")
    monkeypatch.setattr(asks, "check", fake_check)
    monkeypatch.setattr(agent_loop, "_turn_change_list", lambda ctx: "")
    ledger = _ledger()
    ledger["cfg"] = {"repairs": 1, "check": True}
    agent_loop._asks_check(_loop_ctx(ledger, version=3, preview_version=2), "x")
    assert got["preview"] is None


# ------------------------------------------------------------------ the seam

class _Db:
    def __init__(self, row):
        self.row = row

    def run(self, fn, *a):
        class Cur:
            def __enter__(s):
                return s

            def __exit__(s, *e):
                return False

            def execute(s, *a):
                pass

            def fetchone(s):
                return {"value": self.row} if self.row is not None else None

        class Conn:
            def cursor(s):
                return Cur()
        return fn(Conn())


def _seam_ctx():
    return NS(claude_trial=None, user_message="make the title bigger", verification_request=None,
              request_contract_history=[], latest_edl=lambda: {"version": 1, "json": EDL})


def test_switch_off_makes_no_call(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda *a, **k: pytest.fail("no ledger when off"))
    ctx = _seam_ctx()
    assert agent_loop._typed_asks_start(ctx, _Db('{"mode": "off"}'), {"id": 1, "user_id": 5}, 9, {}, None) == ""
    assert ctx.ask_ledger is None


def test_a_change_routes_to_the_patch_and_the_editor_is_told(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda ctx, req, hist=None: _ledger())
    monkeypatch.setattr(patch, "run", lambda ctx, ledger, req: {"version": 2, "applied": ["texts:t1 bigger"], "usage": {}})
    ctx = _seam_ctx()
    note = agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert "ASK LEDGER" in note and "Patch already made the change as EDL v2" in note
    assert ctx.ask_ledger["routed"]["to"] == "patch"


def test_a_whole_film_ask_goes_to_the_director_and_a_fall_tells_the_loop_to_use_the_upload(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda ctx, req, hist=None: asks.settle({"lang": "en", "asks": [_ask("film")]}, EDL))
    monkeypatch.setattr(asks, "direct_film", lambda *a: {"fell": "source 900 s is past the Director's 180 s"})
    ctx = _seam_ctx()
    note = agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert "full upload" in note and ctx.ask_ledger["routed"]["fell"].startswith("source")
    monkeypatch.setattr(asks, "direct_film", lambda *a: {"version": 4, "applied": ["9 shots", "captions"]})
    note = agent_loop._typed_asks_start(_seam_ctx(), _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None)
    assert "Director already made the whole film from the full upload as EDL v4" in note


def test_a_resumed_slice_carries_its_ledger_and_calls_nothing(monkeypatch):
    monkeypatch.setattr(asks, "build", lambda *a, **k: pytest.fail("the ledger is read once per request"))
    ctx = _seam_ctx()
    led = _ledger()
    assert agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 2, "user_id": 5}, 9, {},
                                        {"ask_ledger": led, "ask_verdict": None}) == ""
    assert ctx.ask_ledger is led
    assert agent_loop._asks_checkpoint(ctx)["ask_ledger"] is led


def test_a_ledger_fault_leaves_todays_loop(monkeypatch):
    def boom(*a, **k):
        raise ValueError("no structured answer")
    monkeypatch.setattr(asks, "build", boom)
    ctx = _seam_ctx()
    assert agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None) == ""
    assert ctx.ask_ledger is None


def test_a_trial_turn_never_enters():
    ctx = _seam_ctx()
    ctx.claude_trial = object()
    assert agent_loop._typed_asks_start(ctx, _Db('{"mode": "on"}'), {"id": 1, "user_id": 5}, 9, {}, None) == ""


def test_the_switch_may_name_a_claude_model_for_the_ledger_and_check_only():
    assert asks.parse('{"mode": "on", "model": "claude-sonnet-5-5"}')["model"] == "claude-sonnet-5-5"
    assert asks.parse('{"mode": "on", "model": "gpt-6-luna"}')["model"] is None
    assert asks.parse(None)["model"] is None


def test_a_named_model_is_used_only_on_a_claude_lane(monkeypatch):
    import llm
    used = []

    class Resp:
        usage = None
        choices = [NS(message=NS(tool_calls=[NS(function=NS(name="record_x", arguments='{"a": 1}'))]), finish_reason="stop")]
    monkeypatch.setattr(llm, "create_with_dialect", lambda client, model, *a, **k: used.append(model) or Resp())
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    asks._call(NS(llm_client=None, agent_model="claude-haiku-5-5", ask_model="claude-sonnet-5-5"), "record_x", {}, "s", "c")
    asks._call(NS(llm_client=None, agent_model="gpt-6-luna", ask_model="claude-sonnet-5-5"), "record_x", {}, "s", "c")
    asks._call(NS(llm_client=None, agent_model="claude-haiku-5-5"), "record_x", {}, "s", "c")
    assert used == ["claude-sonnet-5-5", "gpt-6-luna", "claude-haiku-5-5"]


def test_an_empty_ledger_for_a_typed_request_is_asked_again_then_dropped(monkeypatch):
    answers = [{"lang": "en"}, {"lang": "en", "asks": [_ask("edit")]}]
    monkeypatch.setattr(asks, "_call", lambda *a, **k: (answers.pop(0), {"usd": 0.001, "s": 1.0, "model": "m"}))
    ctx = NS(latest_edl=lambda: {"version": 3, "json": EDL}, index={}, duration=20.0)
    ledger = asks.build(ctx, "remove the grade and add dissolves")
    assert len(ledger["asks"]) == 1 and ledger["usage"]["usd"] == 0.002
    answers[:] = [{"lang": "en"}, {"lang": "en", "asks": []}]
    with pytest.raises(ValueError):
        asks.build(ctx, "remove the grade and add dissolves")


def test_the_customers_alternative_rides_with_its_ask_to_the_editor_and_the_check(monkeypatch, tmp_path):
    # 12 Nov replay of p5345: "image-to-video with real motion ... If not, explain the limitation and stop"
    # was read as five asks, and the honest stop left four of them not done. The alternative is a field
    # of the ask the model fills; the check counts it as a branch that meets the ask.
    assert "otherwise" in asks.LEDGER_SCHEMA["properties"]["asks"]["items"]["required"]
    ledger = asks.settle({"lang": "en", "asks": [
        _ask("film", said="real motion from the photos", otherwise="explain the limitation and stop"),
        _ask("keep", said="no new credits")]}, EDL)
    assert ledger["asks"][0]["otherwise"] == "explain the limitation and stop" and ledger["asks"][1]["otherwise"] == ""
    assert "If it can't be done: explain the limitation and stop" in asks.editor_note(ledger)
    seen = {}

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        seen["system"], seen["content"] = system, content
        return {"asks": [{"id": "a1", "state": "not_done", "alternative": "taken", "seen": "still photos; the reply says why", "line": "x"},
                         {"id": "a2", "state": "not_done", "alternative": "taken", "seen": "", "line": "y"}]}, {"usd": 0.001, "s": 1, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    v = asks.check(NS(workdir=str(tmp_path)), ledger, "It can't be done here.", EDL, None, "")
    assert "otherwise (their alternative when it can't be done): explain the limitation and stop" in seen["content"][0]["text"]
    assert "alternative" in asks.CHECK_SCHEMA["properties"]["asks"]["items"]["required"]
    # the alternative counts only for an ask the customer gave one for
    assert [r["state"] for r in v["asks"]] == ["done", "not_done"]
    assert v["asks"][0]["seen"].startswith("their alternative was taken")


def test_a_question_to_the_customer_keeps_the_ledger_on_its_message():
    ctx = NS(ask_ledger=asks.settle({"lang": "en", "asks": [_ask("edit"), _ask("keep")]}, EDL), ask_verdict=None)
    got = agent_loop._asks_summary(ctx)
    assert [a["state"] for a in got["asks"]] == ["unchecked", "unchecked"] and got["open"] is None
    assert agent_loop._asks_summary(NS(ask_ledger=None)) is None
    assert agent_loop._asks_summary(NS(ask_ledger={"asks": "broken"})) is None


def test_the_whole_film_numbers_see_single_cut_transitions_photos_and_the_frame_size():
    edl = {"keep": [], "canvas": {"width": 1080, "height": 1920, "fps": 30},
           "inserts": [{"id": f"ins{i}", "kind": "image", "motion": "zoom_in" if i < 3 else None, "at_output_s": 4.0 * i,
                        "duration_s": 4.0} for i in range(5)],
           "fx": [{"type": "junction", "style": "dissolve", "at": 4.0 * i, "duration_s": 0.4} for i in range(1, 5)]}
    m = asks.measured(edl, [], None, None, {})
    assert m["transitions_at_single_cuts"]["count"] == 4 and m["transitions_at_single_cuts"]["styles"] == {"dissolve": 4}
    assert m["photos"] == {"count": 5, "with_motion": 3} and m["render_size"] == [1080, 1920]
    video = {"keep": [[0, 10]], "frame": {"ratio": "9:16"}}
    assert asks.output_size(video, {"video": {"width": 1920, "height": 1080}}) == [1080, 1920]
    assert asks.output_size({"keep": [[0, 10]]}, {}) is None


def test_a_request_that_rendered_nothing_is_checked_on_its_versions_stored_preview():
    class Db:
        def run(self, fn, *a):
            class Cur:
                def __enter__(s):
                    return s

                def __exit__(s, *e):
                    return False

                def execute(s, sql, args):
                    s.args = args

                def fetchone(s):
                    return {"result": {"edl_version": 7, "duration_s": 20.0, "screening_pages": []}} if s.args[1] == "7" else None

            class Conn:
                def cursor(s):
                    return Cur()
            return fn(Conn(), *a)
    ctx = NS(db=Db(), project_id=1)
    assert asks.stored_preview(ctx, 7)["duration_s"] == 20.0
    assert asks.stored_preview(ctx, 8) is None
    assert asks.stored_preview(NS(db=None, project_id=1), 7) is None


def test_pictures_are_named_the_way_the_customer_calls_them():
    # p5460's third request: "Remove the honey and tea b roll clip ... Yes #5 when I introduce the honey".
    # The check had overlay ids and times only, and could not tell which clip was which (cannot_tell x3).
    rows = [{"id": 6, "kind": "original", "storage_key": "originals/3/a.mp4", "meta": {"filename": "talk.mov"}},
            {"id": 19, "kind": "video_clip", "storage_key": "fetched/3/broll/tea.mp4",
             "meta": {"stock": True, "provider": "pexels", "filename": "b-roll.mp4",
                      "page_url": "https://www.pexels.com/video/two-people-drinking-tea-5977443/"}},
            {"id": 30, "kind": "video_clip", "storage_key": "uploads/3/4870.mov", "meta": {"filename": "IMG_4870.mov"}},
            {"id": 31, "kind": "video_clip", "storage_key": "uploads/3/4872.mov", "meta": {"filename": "IMG_4872.mov"}}]

    class Db:
        def run(self, fn, *a):
            class Cur:
                def __enter__(s):
                    return s

                def __exit__(s, *e):
                    return False

                def execute(s, sql, args):
                    assert args == (3,)

                def fetchall(s):
                    return rows

            class Conn:
                def cursor(s):
                    return Cur()
            return fn(Conn(), *a)
    edl = {"keep": [[0, 60]], "overlays": [{"id": "ov1", "asset_key": "fetched/3/broll/tea.mp4", "start": 39.5, "duration_s": 2.1},
                                           {"id": "ov2", "asset_key": "uploads/3/4872.mov", "start": 5.0, "duration_s": 3.0}]}
    names = asks.asset_names(NS(db=Db(), project_id=3), edl)
    assert names == {"fetched/3/broll/tea.mp4": "found pexels video_clip: two people drinking tea",
                     "uploads/3/4872.mov": "upload #3 IMG_4872.mov"}
    text, items = asks.digest(edl, names)
    assert "overlays:ov1 overlay  39.5-41.6 s [found pexels video_clip: two people drinking tea]" in text
    assert "[upload #3 IMG_4872.mov]" in text and "overlays:ov2" in items
    assert asks.asset_names(NS(db=None, project_id=3), edl) == {}


def test_the_check_reads_the_items_and_the_words_on_the_films_clock(monkeypatch, tmp_path):
    seen = {}

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        seen["content"] = content
        return {"asks": []}, {"usd": 0.0, "s": 0, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    index = {"words": [{"w": "Our", "t0": 1.0, "t1": 1.2}, {"w": "honey", "t0": 1.3, "t1": 1.7}], "video": {"duration": 20.0}}
    monkeypatch.setattr(asks, "asset_names", lambda ctx, edl: {})
    asks.check(NS(workdir=str(tmp_path), index=index), asks.settle({"lang": "en", "asks": [_ask("edit")]}, EDL), "ok", EDL, None, "")
    text = seen["content"][0]["text"]
    assert "texts:t1 text \"Big sale\"" in text and "[1.0-1.7] Our honey" in text


def test_the_typed_route_holds_the_owners_off_switch_on_the_director(monkeypatch):
    # app_kv.director has been off since 12 Nov (device spam); a subscriber's typed whole-film ask must not
    # reach the Director through this route while it is.
    from score import flags as director_flags, film
    monkeypatch.setattr(film, "_make", lambda *a, **k: pytest.fail("the Director ran while switched off"))
    monkeypatch.setattr(director_flags, "read", lambda db: dict(director_flags.DEFAULTS, mode="off"))
    ctx = NS(duration=60.0, has_main_video=True, workdir="/tmp")
    assert asks.direct_film(ctx, None, {"id": 1}, 9, asks.parse('{"mode": "on"}')) == {"fell": "the Director is off (app_kv.director)"}


def test_a_request_about_the_upload_starts_from_it_over_an_untouched_first_cut(monkeypatch):
    # p5624 on the 12 Nov replay: "work only on the first news story ... keep my narration and 16:9" ran on
    # the product's own 52 s vertical cut of a 15 min show. The ledger model says where the request starts.
    assert "start" in asks.LEDGER_SCHEMA["required"]
    assert asks.settle({"lang": "es", "start": "upload", "asks": [_ask("edit")]}, EDL)["start"] == "upload"
    assert asks.settle({"lang": "es", "start": "elsewhere", "asks": [_ask("edit")]}, EDL)["start"] == "current"
    calls = []

    def reset(ctx):
        calls.append(1)
        ctx.versions_written.append(28)
        return "EDL v27 -> v28: reset the edit"
    monkeypatch.setattr(agent_tools, "reset_edit", reset)

    def ctx_for(since=0, start="upload", made_by="automatic_first_cut", main=True):
        c = NS(request_edit_origin={"made_by": made_by, "customer_requests_since": since}, has_main_video=main,
               versions_written=[], latest_edl=lambda: {"version": 27, "json": EDL})
        return c, {"lang": "es", "start": start, "asks": [_ask("edit")]}
    c, led = ctx_for()
    note = agent_loop._typed_start_from_upload(c, led, {"id": 1})
    assert calls == [1] and led["reset"] == {"from": 27, "to": 28} and "starts from the full upload as EDL v28" in note
    for kw in ({"since": 1}, {"start": "current"}, {"made_by": None}, {"main": False}):
        c, led = ctx_for(**kw)
        assert agent_loop._typed_start_from_upload(c, led, {"id": 1}) == "" and calls == [1], kw


def test_the_ledger_is_told_how_much_of_the_upload_the_current_film_is(monkeypatch):
    seen = {}

    def fake(ctx, name, schema, system, content, max_tokens=4000):
        seen["content"], seen["system"] = content, system
        return {"lang": "es", "start": "upload", "asks": [_ask("edit")]}, {"usd": 0.0, "s": 0, "model": "m"}
    monkeypatch.setattr(asks, "_call", fake)
    monkeypatch.setattr(asks, "asset_names", lambda ctx, edl: {})
    ctx = NS(index={"video": {"duration": 895.0, "width": 1920, "height": 1080}}, duration=895.0,
             request_edit_origin={"made_by": "automatic_first_cut", "customer_requests_since": 0})
    led = asks.build(ctx, "trabaja solo la primera noticia", [], {"keep": [[100.0, 138.0]], "frame": {"ratio": "9:16"}})
    assert led["start"] == "upload"
    assert "The current film is 38.0 s of the 895.0 s upload, frame 9:16." in seen["content"]
    assert "start is upload" in seen["system"]
