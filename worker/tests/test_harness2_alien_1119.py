"""Harness v2, 19 Nov 2026: v2's golden losses closed at the source, music of
reference grade for customers, and striking edits by default.

p5744 R2: "slow motion after 6 s" played 6-8.6 s of a 13 s film (the model
copied the example's end); a speed row with no end now runs to the film's
end. p5744 R3-R8: no chill/lofi passed the gate; the reference-grade set is
now the customer library (Mixkit, Pixabay; Content ID tracks left out).
"Bubbles": a drawn bubbles element. p5873 R2: darker and slightly green with
no numbers to steer by; a look change is measured in the envelope. p5760:
ten titled clips came back as locked raw cards; v2's clips are delivered
with their titles, captions and a render. Open requests: the finish op runs
the craft departments the cut lacks; direct runs the Director.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import agent_tools  # noqa: E402
import music_choose  # noqa: E402
import music_library  # noqa: E402
from harness2 import (commit, direct_op, envelope, find_tool,  # noqa: E402
                      finish as finish_mod, ops as ops_mod, schema_ops)
from w0c_support import tool_context  # noqa: E402

P5744_KEEP = [[0.0, 4.75], [9.25, 11.26]]


@pytest.fixture(autouse=True)
def _clean():
    commit._RECEIPTS.clear()
    music_choose._CACHE.clear()
    yield
    commit._RECEIPTS.clear()
    music_choose._CACHE.clear()


def _ctx(duration, keep):
    ctx, fake = tool_context({"keep": keep}, duration=duration)
    ctx.cut_sound_source = lambda: None
    return ctx, fake


# -- p5744 R2: "after 6 s" is the rest of the film ------------------------------

def test_a_speed_row_with_no_end_runs_to_the_films_end():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    env = commit.edit(ctx, [{"speed": [{"start": 6.0, "factor": 0.5}]}],
                      "slow motion after 6 s")
    played = env.measured["speed"][0]
    assert played["film_s"][0] == pytest.approx(6.0, abs=0.02)
    assert played["film_s"][1] == pytest.approx(env.measured["program_s"],
                                                abs=0.03)
    assert "runs to the film's end" in env.text()
    assert any("speed now:" in f for f in env.findings)


def test_end_as_a_word_also_means_the_films_end():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    env = commit.edit(ctx, [{"speed": {"start": 6, "end": "end",
                                       "factor": 0.6}}], "s")
    sp = ctx.latest_edl()["json"]["speed"][0]
    assert sp["end"] == pytest.approx(11.26, abs=0.02)
    assert not env.not_applied


def test_a_speed_ramp_row_runs_the_ramp_verb(monkeypatch):
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    seen = []
    real = agent_tools.execute

    def execute(c, name, args):
        if name == "set_speed_ramp":
            seen.append(args)
            return "NO CHANGE"
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    commit.edit(ctx, [{"speed": {"start": 2, "end": 4, "factor": 0.4,
                                 "ramp": True}}], "ramp")
    assert seen and seen[0]["low"] == 0.4 and seen[0]["hold"] == 0.5
    assert seen[0]["start"] == pytest.approx(2.0, abs=0.01)
    assert seen[0]["end"] == pytest.approx(4.0, abs=0.01)
    # the ramp could not land here: the constant factor plays instead
    assert ctx.latest_edl()["json"]["speed"][0]["factor"] == 0.4


def test_the_schema_example_no_longer_hands_a_window():
    speed = next(e for n, e, _c in schema_ops.OPS if n == "speed")
    assert '"end"' not in speed


# -- music: the reference-grade set is the customer library ----------------------

def test_mixkit_and_pixabay_rows_are_valid_and_owe_no_credit():
    row = next(t for t in music_choose.tracks() if t.get("reference_set"))
    assert row["license"] in ("Mixkit Stock Music Free License",
                              "Pixabay Content License")
    assert music_choose._valid(row)
    assert music_choose.credit_lines(row) == []
    bad = dict(row, youtube_content_id=True)
    assert not music_choose._valid(bad)
    bad = dict(row, source_url="https://evil.example.com/a.mp3")
    assert not music_choose._valid(bad)


def test_the_library_carries_the_set_and_every_row_passed_the_gate():
    pool = music_choose.reference_pool()
    ref = [t for t in pool if t.get("reference_set")]
    assert len(ref) >= 60
    lofi = [t for t in ref if "lofi" in str(t.get("mood"))]
    assert len(lofi) >= 10
    passed = music_choose.gate()["pass"]
    assert all(t["cid"] in passed for t in ref)
    assert all(t["storage_key"] == f"music-library/{t['cid']}.mp3"
               for t in ref)
    assert all(len(t["z"]) == 32 for t in ref)
    assert not any(t.get("youtube_content_id") for t in music_choose.tracks())


def test_no_audio_of_the_set_is_in_the_repository():
    lib = os.path.join(WORKER, "music_library")
    assert not [n for n in os.listdir(lib)
                if n.endswith((".mp3", ".m4a", ".wav"))]


def test_music_find_answers_chill_lofi_with_reference_grade_tracks(
        monkeypatch):
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    env = find_tool.find(ctx, kind="music", query=["chill lofi"])
    body = env.text()
    assert env.measured["matched"]["chill lofi"] >= 5
    assert "match(es) of reference grade" in body


def test_a_named_track_is_the_persons_pick_not_something_to_see():
    """p5744 v2b: "Bubbles" is the library's track; the find answer offers
    the track and no longer sends the model to a bubbles element."""
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    body = find_tool.find(ctx, kind="music", query=["Bubbles"]).text()
    assert "cc0:chill-bubbles-lofi-bright-relaxed" in body
    assert "A track the person names is theirs" in body
    assert "add_element" not in body
    assert "plays in its place" not in body


def test_words_that_name_no_track_may_still_name_something_to_see():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    body = find_tool.find(ctx, kind="music", query=["confetti"]).text()
    assert "add_element" in body


def test_choose_defaults_to_the_reference_pool(monkeypatch):
    seen = {}

    def shortlist(film, used=(), n=6, pool=None):
        seen["pool"] = pool
        return [], {}
    monkeypatch.setattr(music_choose, "shortlist", shortlist)
    music_choose.choose({"kind": "story", "duration_s": 20}, call=None)
    assert seen["pool"] and all(t["cid"] in music_choose.gate()["pass"]
                                for t in seen["pool"])


def test_the_customer_music_library_lists_and_places_reference_tracks(
        monkeypatch):
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    monkeypatch.setattr(music_library, "used_by_user", lambda c: set())
    text = music_library.list_music_library(ctx, mood="chill")
    assert "REFERENCE GRADE" in text and "lib:" in text
    cid = next(t["cid"] for t in music_library.reference_tracks("chill"))
    placed = []
    monkeypatch.setattr(music_library, "_add_reference",
                        lambda c, track, **kw: placed.append(track["cid"])
                        or "EDL v2")
    assert music_library.add_library_music(ctx, f"lib:{cid}") == "EDL v2"
    assert placed == [cid]


# -- "Bubbles": a drawn element ----------------------------------------------------

def test_bubbles_is_a_particle_kind_drawn_over_the_whole_frame(monkeypatch):
    from tools_ext import element_tools
    assert "bubbles" in element_tools.PARTICLES
    tpl = open(os.path.join(WORKER, "motion", "templates",
                            "el_particles.html")).read()
    assert '"bubbles"' in tpl and "kind === 'bubbles'" in tpl
    card = json.load(open(os.path.join(WORKER, "devices", "cards",
                                       "element_particles.json")))
    assert "bubbles" in card["params"]["kind"]
    ops = []
    monkeypatch.setattr(element_tools._write, "commit",
                        lambda c, o, summary: ops.extend(o) or "EDL v2")
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    element_tools.add_element(ctx, "bubbles", 1.0, 6.0)
    item = json.dumps(ops)
    assert '"bubbles"' in item and '"frame"' in item


# -- p5873 R2: a look change is measured in the envelope -------------------------

def test_a_look_change_carries_its_measured_colour(monkeypatch):
    import look_guard
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    monkeypatch.setattr(look_guard, "after_write",
                        lambda c, sig: "\nLOOK COLOUR MEASURED: whites L 85 "
                                       "-> 60 (a* -3.0)")
    env = commit.edit(ctx, [{"grade": {"preset": "warm"}}], "warm")
    assert any("LOOK COLOUR MEASURED" in f for f in env.findings)
    assert "look" in env.measured


def test_an_edit_that_leaves_the_look_alone_is_not_measured(monkeypatch):
    import look_guard
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    called = []
    monkeypatch.setattr(look_guard, "after_write",
                        lambda c, sig: called.append(1) or "x")
    commit.edit(ctx, [{"cut": [[2.0, 4.0]]}], "cut")
    assert not called


# -- p5760: clips are delivered as clips -------------------------------------------

def test_clips_carry_titles_captions_and_a_render(monkeypatch):
    ctx, _fake = _ctx(600.0, [[0.0, 600.0]])
    got = {}

    def make_shorts(c, clips, style_note=None, deliver=None):
        got.update(clips=clips, deliver=deliver)
        return "Shorts story search started as job 9."
    monkeypatch.setattr(ops_mod.Engines, "make_shorts",
                        staticmethod(make_shorts))
    commit.edit(ctx, [{"clips": {"items": [
        {"range": [10, 50], "title": "The fall"},
        {"range": [100, 140], "title": "The comeback"}]}}], "clips")
    assert len(got["clips"]) == 2
    assert got["deliver"] == {"title": True, "captions": True,
                              "render": True}


def test_a_delivered_child_gets_its_title_captions_and_preview(monkeypatch):
    import shorts
    calls = []

    class Ctx:
        def __init__(self, db, job, project, index, wd):
            self.edl = {"keep": [[10.0, 50.0]]}
            self.version = 2

        def latest_edl(self):
            return {"version": self.version, "json": self.edl}

    def execute(c, name, args):
        calls.append((name, args))
        c.version += 1
        return f"EDL v{c.version}"

    class DB:
        def run(self, fn, *a):
            if fn is shorts.dbx.get_project:
                return {"id": a[0]}
            if fn is shorts.dbx.get_or_enqueue_preview_job:
                calls.append(("preview", a[2]["edl_version"]))
                return 77, True
            raise AssertionError(fn)
    monkeypatch.setattr(shorts.agent_tools, "ToolContext", Ctx)
    monkeypatch.setattr(shorts.agent_tools, "execute", execute)
    monkeypatch.setattr(shorts.agent_tools, "_child_payload",
                        lambda c, p: p)
    monkeypatch.setattr(shorts.agent_tools, "_render_signature",
                        lambda *a, **k: "sig")
    version, job, notes = shorts._deliver_child(
        DB(), {"id": 1, "user_id": 3}, 41, {"words": [{"w": "hi"}]},
        {"start": 10, "end": 50, "title": "The fall"}, "/tmp",
        {"title": True, "captions": True, "render": True})
    names = [c[0] for c in calls]
    assert names == ["add_text", "add_captions", "preview"]
    assert calls[0][1]["text"] == "The fall"
    assert calls[2][1] == version == 4 and job == 77


# -- open requests: finish and direct ---------------------------------------------

def _recording_run_verb(calls):
    def run_verb(stage, name, args, env, index):
        calls.append((name, args))
        return True
    return run_verb


def test_finish_runs_the_departments_the_cut_lacks(monkeypatch):
    ctx, _fake = _ctx(30.0, [[0.0, 10.0], [12.0, 30.0]])
    from harness2.stage import Stage
    head = ctx.latest_edl()
    stage = Stage(ctx, head["json"], head["version"])
    env = commit.EditEnv()
    calls = []
    monkeypatch.setattr(finish_mod, "_music",
                        lambda s, v, e, i, rv: rv(s, "add_music", {}, e, i))
    monkeypatch.setattr(finish_mod, "_sound",
                        lambda s, v, e, i, rv: rv(s, "add_sfx", {}, e, i))
    finish_mod.op_finish(stage, {}, env, 0, _recording_run_verb(calls))
    names = [n for n, _a in calls]
    assert names[0] == "apply_look"
    assert "add_zoom" in names                      # no speech: the camera
    assert names[-1] == "set_master_loudness"
    assert "add_music" in names and "add_sfx" in names
    zooms = [a for n, a in calls if n == "add_zoom"]
    assert {z["mode"] for z in zooms} == {"punch", "push_in"}


def test_finish_pictures_go_to_the_broll_director():
    ctx, _fake = _ctx(30.0, [[0.0, 30.0]])
    from harness2.stage import Stage
    head = ctx.latest_edl()
    stage = Stage(ctx, head["json"], head["version"])
    env = commit.EditEnv()
    calls = []
    finish_mod.op_finish(stage, {"departments": ["pictures"], "pictures": [
        {"word": "rocket", "query": "rocket launch at night"}]}, env, 0,
        _recording_run_verb(calls))
    assert calls[0][0] == "source_broll"
    assert calls[0][1]["moments"][0]["word"] == "rocket"


def test_sound_events_read_what_lands_on_screen():
    edl = {"keep": [[0.0, 5.0], [6.0, 12.0]],
           "inserts": [{"id": "in1", "at_output_s": 2.0, "duration_s": 1.0}],
           "texts": [{"id": "tx1", "start": 0.5, "end": 2, "template": "title"}],
           "zooms": [{"id": "z1", "start": 7.0, "end": 8.0, "mode": "punch"},
                     {"id": "z2", "start": 9.0, "end": 11.0,
                      "mode": "push_in"}],
           "captions": {"mode": "from_transcript"},
           "effects": {"transition": {"style": "whip"}}}
    kinds = [(e["kind"], round(e["f"] / 30.0, 1))
             for e in finish_mod.events(edl)]
    assert ("card", 0.5) in kinds and ("picture", 2.0) in kinds
    assert ("move", 7.0) in kinds
    assert not any(k == "move" and t == 9.0 for k, t in kinds)
    assert any(k == "cut" for k, _t in kinds)


def test_finish_and_direct_are_ops_now():
    names = schema_ops.op_names()
    assert {"finish", "direct", "element"} <= set(names)
    assert "finish" in ops_mod.HANDLERS and not ops_mod.ENGINE_OPS


def test_direct_runs_first_and_the_rest_builds_on_its_film(monkeypatch):
    ctx, _fake = _ctx(30.0, [[0.0, 30.0]])
    ran = []

    def run(c, value, env, index):
        ran.append(value)
        row = c.latest_edl()
        edl = dict(row["json"], keep=[[2.0, 20.0]])
        c.write_edl(edl, "the Director's film")
        env.finding("direct: the Director made the film")
        return c.latest_edl()["version"]
    monkeypatch.setattr(direct_op, "run", run)
    env = commit.edit(ctx, [{"direct": {"brief": "a reel"}},
                            {"cut": [[4.0, 6.0]]}], "film")
    assert ran == [{"brief": "a reel"}]
    keep = ctx.latest_edl()["json"]["keep"]
    assert keep[0][0] == pytest.approx(2.0, abs=0.3)
    assert any("Director" in f for f in env.findings)


def test_direct_alone_reports_why_when_the_director_cannot_run(monkeypatch):
    ctx, _fake = _ctx(30.0, [[0.0, 30.0]])
    from score import asks as asks_mod
    monkeypatch.setattr(asks_mod, "direct_film",
                        lambda *a, **k: {"fell": "the Director is off "
                                                 "(app_kv.director)"})
    env = commit.edit(ctx, [{"direct": {}}], "film")
    assert env.not_applied and "Director is off" in env.text()
    assert "finish" in env.text()
    assert not env.changed.startswith("already so")


def test_the_kernel_teaches_striking_by_default_and_narrow_asks_exactly():
    from harness2 import loop
    text = loop.kernel_text()
    assert "An open request" in text and "A narrow request" in text
    assert "`finish`" in text and "`direct`" in text
    assert "no `end` runs to the film's end" in text


# ==============================================================================
# Round two (rater run v2a-1119)
# ==============================================================================

def test_an_opening_title_keeps_its_length_when_its_footage_moves_far():
    """p5728: the 1.47 s opening title became 0-29.44 s after a re-cut moved
    its footage to 28.8 s, and covered the fighters for 29 s."""
    import timeline
    old_keep = [[88.42, 89.795], [90.355, 91.205], [91.355, 92.205]]
    new_keep = [[26.44, 30.71], [32.23, 39.29], [54.0, 61.5], [63.27, 68.08],
                [83.27, 89.07], [101.04, 107.64]]
    edl = {"keep": new_keep, "texts": [
        {"id": "t1", "text": "ДЖЕБ", "start": 0.0, "end": 1.47,
         "template": "title"}]}
    timeline.remap_program_items(edl, timeline.Timeline(old_keep),
                                 timeline.Timeline(new_keep))
    t = edl["texts"][0]
    assert (t["start"], t["end"]) == (0.0, 1.47)
    # a head trim still ends with its footage
    trimmed = [[88.72, 89.795], [90.355, 91.205]]
    edl2 = {"keep": trimmed, "texts": [dict(t)]}
    timeline.remap_program_items(edl2, timeline.Timeline(old_keep),
                                 timeline.Timeline(trimmed))
    assert edl2["texts"][0]["start"] == 0.0
    assert edl2["texts"][0]["end"] < 1.47


def test_the_envelope_lists_what_is_on_screen_with_its_remove_op():
    from harness2 import measure
    edl = {"keep": [[0.0, 20.0], [30.0, 40.0]],
           "captions": [{"text": "УКЛОН", "start": 32.0, "end": 33.0},
                        {"text": "СБОКУ", "start": 33.0, "end": 34.0}],
           "texts": [{"id": "tx1", "text": "ДЖЕБ", "start": 0.0, "end": 25.0,
                      "template": "title", "y": 0.62}],
           "effects": {"grade_custom": {"temperature": -1.0, "tint": -0.46}}}
    lines = measure.on_screen(edl)
    text = "\n".join(lines)
    assert "captions: 2 item(s) on film 22.0-24.0 s" in text
    assert "'mode': 'off'" in text
    assert "held 25.0 s of the 30.0 s film" in text
    assert "'remove': 'texts', 'id': 'tx1'" in text
    assert "camera moves: none" in text
    assert "temperature -1.00" in text


def test_an_edit_envelope_carries_the_on_screen_list():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    env = commit.edit(ctx, [{"cut": [[2.0, 4.0]]}], "cut")
    assert env.on_screen and "on screen now" in env.text()


def test_speed_rows_are_read_on_one_clock():
    """p5744 v2a: the second row was read on the clock the first had
    slowed and landed on 0.2 s."""
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    commit.edit(ctx, [{"speed": [{"start": 3.0, "end": 4.75, "factor": 0.5},
                                 {"start": 4.75, "end": 6.76,
                                  "factor": 0.5}]}], "s")
    spans = sorted((round(s["start"], 2), round(s["end"], 2))
                   for s in ctx.latest_edl()["json"]["speed"])
    assert spans == [(3.0, 4.75), (9.25, 11.26)]


def test_a_ramp_that_cannot_slow_a_30p_take_plays_its_slow_motion(
        monkeypatch):
    """p5728 v2a: "NO CHANGE — that curve stays at 1.0x" came back as
    "already so" four times; the ramp now shows its slow motion with
    repeated frames, or the constant factor plays."""
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    real = agent_tools.execute
    seen = []

    def execute(c, name, args):
        if name == "set_speed_ramp":
            seen.append(args)
            return "NO CHANGE — that curve stays at 1.0x."
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    env = commit.edit(ctx, [{"speed": {"start": 6.0, "factor": 0.5,
                                       "ramp": True}}], "slow after 6")
    assert seen and seen[0]["allow_duplicates"] is True
    sp = ctx.latest_edl()["json"]["speed"]
    assert sp and all(s["factor"] == 0.5 for s in sp)
    assert "already so" not in env.text()


def test_no_change_with_a_reason_is_never_already_so(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    monkeypatch.setattr(agent_tools, "execute",
                        lambda c, n, a: "NO CHANGE — that curve stays at 1.0x.")
    env = commit.edit(ctx, [{"tool": "add_zoom", "args": {"start": 1,
                                                          "end": 2}}], "z")
    assert env.not_applied and "stays at 1.0x" in env.text()
    assert "already so" not in env.text()


def test_loose_layer_ops_are_read_as_what_they_say():
    edl = {"music": [{"id": "bed"}], "texts": [{"id": "tx1"}]}
    assert ops_mod.normalize({"layer": "music", "remove": True,
                              "id": "bed"}, edl) == {"id": "bed",
                                                     "remove": "music"}
    assert ops_mod.normalize({"layer": "remove", "id": "tx1"}, edl) == {
        "id": "tx1", "remove": "texts"}


def test_removing_a_bed_moves_its_bound_devices(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    from harness2.stage import Stage
    head = ctx.latest_edl()
    edl = dict(head["json"], music=[{"id": "bed"}, {"id": "mus1"}],
               fx=[{"id": "drop", "music_id": "bed"}, {"id": "g"}])
    stage = Stage(ctx, edl, head["version"])
    stage._edl = edl
    env = commit.EditEnv()
    seen = []
    monkeypatch.setattr(stage, "replace", lambda e: seen.append(e))
    ops_mod._release_music_binds(stage, [(0, {"remove": "music",
                                              "id": "bed"})], env)
    assert seen and seen[0]["fx"][0]["music_id"] == "mus1"
    assert any("moved to mus1" in r for r in env.repairs)


def test_a_new_track_replaces_the_bed_in_place(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    calls = []
    real = agent_tools.execute

    def execute(c, name, args):
        calls.append((name, args))
        if name == "swap_music":
            edl = dict(c.latest_edl()["json"])
            edl["music"] = [dict(m, storage_key=args["storage_key"])
                            if m["id"] == args["id"] else m
                            for m in edl["music"]]
            return c.write_edl(edl, "swap")
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    monkeypatch.setattr(ops_mod, "_music_key",
                        lambda s, rid: ("music/1/library-x.mp3", ""))
    head = ctx.latest_edl()
    edl = dict(head["json"], music=[{"id": "bed", "storage_key": "a.mp3",
                                     "start": 0, "end": 20}])
    real_latest = ctx.latest_edl
    monkeypatch.setattr(ctx, "latest_edl", lambda: dict(real_latest(),
                                                        json=edl))
    env = commit.edit(ctx, [{"music": {"id": "lib:x", "gain_db": -6}}], "m")
    monkeypatch.setattr(ctx, "latest_edl", real_latest)
    music = ctx.latest_edl()["json"]["music"]
    assert [n for n, _a in calls if n in ("swap_music", "add_music")] == [
        "swap_music"]
    assert len(music) == 1 and music[0]["storage_key"].endswith("library-x.mp3")
    assert music[0]["gain_db"] == -6
    assert any("in place of the old bed" in r for r in env.repairs)


def test_a_named_cc0_track_is_offered_as_the_persons_pick():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    body = find_tool.find(ctx, kind="music", query=["Calm current"]).text()
    assert "cc0:chill-calm-currents-lofi-relax-calm" in body
    assert "A track the person names is theirs" in body
    assert find_tool._named_cc0("chill") == []


def test_grade_takes_continuous_controls(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    seen = []
    real = agent_tools.execute

    def execute(c, name, args):
        seen.append((name, args))
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    commit.edit(ctx, [{"grade": {"grade_custom": {"exposure": 0.2,
                                                  "temperature": 0.3}}}], "g")
    assert ("set_grade_custom", {"exposure": 0.2, "warmth": 0.3}) in seen
    assert not any(n == "set_color_grade" for n, _a in seen)


def test_the_look_readout_says_how_light_the_picture_is():
    import numpy as np
    import look_guard
    L = np.linspace(20, 90, 400)
    src = (L, np.zeros(400), np.zeros(400))
    darker = (L - 9, np.zeros(400), np.full(400, 8.0))
    line = look_guard.colour_line(look_guard.colour([src], [darker]))
    assert "darker by 9" in line and "sun-touched" in line


def test_sport_finish_alternates_the_camera_and_moves_speed_and_beats(
        monkeypatch):
    ctx, _fake = _ctx(60.0, [[0.0, 10.0], [12.0, 22.0], [30.0, 40.0]])
    from harness2.stage import Stage
    head = ctx.latest_edl()
    stage = Stage(ctx, dict(head["json"], music=[{"id": "bed"}]),
                  head["version"])
    env = commit.EditEnv()
    calls = []
    track = {"fps": 4.0, "motion": [((i % 17) / 17.0) for i in range(240)]}
    monkeypatch.setattr(ops_mod.Engines, "activity_track",
                        staticmethod(lambda c: (track, None)))
    monkeypatch.setattr(ops_mod, "_ramp_on_source",
                        lambda *a, **k: False)
    monkeypatch.setattr(finish_mod, "_sound", lambda *a: False)
    finish_mod.op_finish(stage, {"sport": True}, env, 0,
                         _recording_run_verb(calls))
    names = [n for n, _a in calls]
    assert "apply_look" not in names and "add_captions" not in names
    zooms = [a for n, a in calls if n == "add_zoom"]
    assert {z["mode"] for z in zooms} == {"push_in", "punch"}
    factors = sorted(a["factor"] for n, a in calls if n == "set_speed")
    assert factors and factors[0] < 1.0 < factors[-1]
    assert "beat_align_cuts" in names


def test_finish_with_captions_false_leaves_no_captions():
    ctx, _fake = _ctx(30.0, [[0.0, 30.0]])
    from harness2.stage import Stage
    head = ctx.latest_edl()
    stage = Stage(ctx, dict(head["json"], captions={"mode": "from_transcript"}),
                  head["version"])
    env = commit.EditEnv()
    calls = []
    finish_mod.op_finish(stage, {"departments": ["look"], "captions": False},
                         env, 0, _recording_run_verb(calls))
    assert ("add_captions", {"mode": "off"}) in calls


def test_clips_make_the_film_the_clips_in_order(monkeypatch):
    """p5760 v2a: ten clips rendered, and the film the customer exported was
    still the untouched 37-min broadcast."""
    ctx, _fake = _ctx(600.0, [[0.0, 600.0]])
    monkeypatch.setattr(ops_mod.Engines, "make_shorts",
                        staticmethod(lambda c, clips, style_note=None,
                                     deliver=None: "Started job 9."))
    titles = []
    real = agent_tools.execute

    def execute(c, name, args):
        if name == "add_text":
            titles.append((args["text"], args["start"]))
            return "NO CHANGE while staging"
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    env = commit.edit(ctx, [{"clips": {"items": [
        {"range": [100, 140], "title": "Two"},
        {"range": [10, 50], "title": "One"}]}}], "clips")
    keep = ctx.latest_edl()["json"]["keep"]
    assert len(keep) == 2 and keep[0][0] == pytest.approx(10, abs=1)
    assert keep[1][0] == pytest.approx(100, abs=1)
    assert [t for t, _s in sorted(titles, key=lambda x: x[1])] == ["One",
                                                                  "Two"]
    assert ctx.latest_edl()["json"]["frame"]["ratio"] == "9:16"
    assert any("now the 2 clips in order" in f for f in env.findings)


def test_a_misjoined_proof_reel_is_joined_again_exactly(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    import media
    import renderer
    pieces = []
    for i, audio_s in enumerate((1.0, 4.0, 1.0)):
        p = str(tmp_path / f"p{i}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=160x284:r=30:d=1", "-f", "lavfi", "-i",
                        f"sine=d={audio_s}", "-c:v", "libx264", "-c:a", "aac",
                        "-t", str(audio_s), p], check=True)
        pieces.append(p)
    out = str(tmp_path / "out.mp4")
    renderer._concat_check_pieces_exact(pieces, out, str(tmp_path),
                                        [1.0, 1.0, 1.0])
    assert media.duration_of(out) == pytest.approx(3.0, abs=0.1)
