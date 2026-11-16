"""Harness v2 reads and supply: timeline (B4), look (B5/B6), render and
slides (D15/D16), find (D17), and the film ops (C11, C12, C14)."""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import read_tools  # noqa: E402
from harness2 import (commit, envelope, find_tool, look, ops as ops_mod,  # noqa: E402
                      render_tool, schema_ops, slides, tickets,
                      timeline_tool)
from schemas import describe_edl  # noqa: E402
from w0c_support import tool_context  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    commit._RECEIPTS.clear()
    yield
    commit._RECEIPTS.clear()


def _ctx(duration=60.0, keep=None):
    ctx, fake = tool_context({"keep": keep or [[0.0, duration]]},
                             duration=duration)
    ctx.cut_sound_source = lambda: None
    return ctx, fake


# -- B4 timeline ------------------------------------------------------------

P5857_V1 = {"keep": [[0.0, 4858.2]]}
P5613_V28 = {"keep": [[12.0, 96.5], [140.25, 300.0], [512.0, 880.75]],
             "texts": [{"id": "t1", "text": "Set 1", "start": 0.5,
                        "end": 2.5}]}


@pytest.mark.parametrize("edl,duration", [(P5857_V1, 4858.2),
                                          (P5613_V28, 1098.5)])
def test_timeline_matches_describe_edl(edl, duration):
    ctx, fake = _ctx(duration=duration, keep=edl["keep"])
    if edl.get("texts"):
        commit.edit(ctx, [{"set": "texts", "value": edl["texts"]}], "texts")
    env = timeline_tool.timeline(ctx)
    row = ctx.latest_edl()
    summary = describe_edl(row["json"], duration)
    program = sum(b - a for a, b in edl["keep"])
    assert env.measured["program_s"] == pytest.approx(program, abs=0.05)
    assert env.measured["pieces"] == len(edl["keep"])
    assert f"v{row['version']}" in env.body
    assert "what plays where" in env.body
    lines = [ln for ln in env.body.splitlines() if "source " in ln
             and ln.strip()[0].isdigit()]
    assert len(lines) == len(edl["keep"])
    # describe_edl agrees on the length
    assert f"{program:g}"[:3] in summary.replace(",", "") or \
        f"{round(program)}" in summary.replace(",", "")


def test_timeline_versions_and_schema():
    ctx, fake = _ctx()
    commit.edit(ctx, [{"cut": [[5, 9]]}], "cut")
    fake.history = [{"version": 8, "created_by": "agent", "created_at": None},
                    {"version": 7, "created_by": "user", "created_at": None}]
    real = fake.run

    def run(fn, *a, **k):
        if fn is dbx.edl_history:
            return fake.history
        return real(fn, *a, **k)
    fake.run = run
    env = timeline_tool.timeline(ctx, versions=True)
    assert "v8  agent" in env.body and "v7  user" in env.body
    ops = timeline_tool.timeline(ctx, schema="ops").body
    for name in schema_ops.op_names():
        assert name in ops
    assert "add_title_card(" in ops            # the long tail of verbs
    layer = timeline_tool.timeline(ctx, schema="texts").body
    assert "TextItem" in layer and "start" in layer


def test_timeline_pages_by_range():
    keep = [[i * 10.0, i * 10.0 + 5.0] for i in range(200)]
    ctx, _fake = _ctx(duration=2000.0, keep=keep)
    env = timeline_tool.timeline(ctx)
    assert "more pieces: pass range" in env.body
    env = timeline_tool.timeline(ctx, range=[100, 150])
    rows = [ln for ln in env.body.splitlines() if "source " in ln]
    assert 9 <= len(rows) <= 12


# -- B5 / B6 look -----------------------------------------------------------

def _make_video(path, seconds=8, fps=10):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate={fps}:duration={seconds}",
                    "-pix_fmt", "yuv420p", path], check=True)


@pytest.fixture
def vid_ctx(tmp_path, monkeypatch):
    path = str(tmp_path / "src.mp4")
    _make_video(path)
    ctx, fake = _ctx(duration=8.0, keep=[[0.0, 8.0]])
    ctx.proxy_path = lambda: path
    ctx._proxy_local = path
    ctx.direct_sight = True          # the v2 loop's own eyes
    monkeypatch.setattr(agent_tools, "_can_receive_images",
                        lambda c: True)
    return ctx, fake


def test_look_source_frames_clamp_and_say_coverage(vid_ctx):
    ctx, _fake = vid_ctx
    env = look.look(ctx, target="source", show="frames", times=[1, 3, 99])
    assert "coverage: source frames at 3 time(s)" in env.body
    assert "held to the nearest edge" in env.body
    assert env.see and os.path.exists(env.see[0][1])


def test_look_program_frames_follow_the_keep_map(vid_ctx, monkeypatch):
    ctx, _fake = vid_ctx
    commit.edit(ctx, [{"keep": {"spans": [[0, 2], [5, 8]], "exact": True}}],
                "two pieces")
    seen = {}

    def spy(c, output_times, question, overlay=None):
        seen["out"] = list(output_times)
        seen["edl"] = c.latest_edl()["json"]["keep"]
        return "frames"
    monkeypatch.setattr(agent_tools, "_look_at_output", spy)
    look.look(ctx, target="program", show="frames", times=[1.0, 2.5])
    assert seen["out"] == [1.0, 2.5]
    from timeline import Timeline
    tl = Timeline(seen["edl"])
    assert tl.out_to_src(2.5) == pytest.approx(5.5, abs=0.05)


def test_junctions_take_frames_either_side_of_every_cut(vid_ctx,
                                                        monkeypatch):
    ctx, _fake = vid_ctx
    commit.edit(ctx, [{"keep": {"spans": [[0, 2], [3, 5], [6, 8]],
                                "exact": True}}], "three pieces")
    got = []
    monkeypatch.setattr(agent_tools, "_look_at_output",
                        lambda c, t, q, overlay=None: got.extend(t) or "")
    env = look.look(ctx, target="program", show="junctions")
    assert got == pytest.approx([1.7, 2.3, 3.7, 4.3])
    assert "2 of 2 cut(s)" in env.body


def test_removed_equals_the_diff_ranges(vid_ctx, monkeypatch):
    ctx, fake = vid_ctx
    commit.edit(ctx, [{"cut": {"spans": [[2, 3], [5, 7]], "exact": True}}],
                "cut")
    looked = []
    monkeypatch.setattr(agent_tools, "look_at",
                        lambda c, times=None, **k: looked.extend(times) or "")
    env = look.look(ctx, show="removed")
    prev = fake.rows[-2]["json"]
    new = fake.rows[-1]["json"]
    gone = look.removed_ranges(prev, new)
    assert [[round(a, 2), round(b, 2)] for a, b in sorted(gone)] == \
        [[2.0, 3.0], [5.0, 7.0]]
    assert all(any(a <= t <= b for a, b in gone) for t in looked)
    assert "removed 2 stretch(es)" in env.body


def test_a_missing_render_starts_one_and_shows_the_program(vid_ctx,
                                                           monkeypatch):
    ctx, _fake = vid_ctx
    monkeypatch.setattr(render_tool, "start_preview",
                        lambda c, quality="draft": {"handle": "job:77"})
    monkeypatch.setattr(agent_tools, "_look_at_output",
                        lambda c, t, q, overlay=None: "program frames")
    env = look.look(ctx, target="render", show="frames", times=[1])
    assert env.status == envelope.WORKING
    assert env.running["handle"] == "job:77"
    assert "assembled from source; render running" in env.body


def test_dense_sheet_over_a_range_is_paged(vid_ctx, monkeypatch):
    ctx, _fake = vid_ctx
    calls = []
    monkeypatch.setattr(agent_tools, "look_at",
                        lambda c, times=None, **k: calls.append(times) or "")
    env = look.look(ctx, show="sheet", range=[0, 8], fps=4)
    assert sum(len(c) for c in calls) == 32
    assert all(len(c) <= look.PAGE_TILES for c in calls)
    assert "sheet of 32 tile(s)" in env.body


def test_look_skill_and_activity(vid_ctx, monkeypatch):
    ctx, _fake = vid_ctx
    env = look.look(ctx, show="skill:cutting")
    assert "coverage: skill cutting" in env.body and len(env.body) > 200
    monkeypatch.setattr(agent_tools, "find_activity",
                        lambda c, **k: "ACTIVE 1.0-4.0")
    env = look.look(ctx, show="activity")
    assert "activity measured 0-8 s" in env.body


# -- D15 render / wait ------------------------------------------------------

def test_render_never_blocks_and_wait_settles(vid_ctx, monkeypatch):
    ctx, fake = vid_ctx
    commit.edit(ctx, [{"cut": [[2, 3]]}], "cut")
    jobs = {41: {"id": 41, "type": "preview", "state": "running",
                 "progress": 40, "payload": {"edl_version": 8}}}
    real = fake.run

    def run(fn, *a, **k):
        if fn is dbx.get_or_enqueue_preview_job:
            return 41, True
        if fn is dbx.get_job:
            return jobs.get(a[0])
        if fn is read_tools.find_render_asset:
            return None
        return real(fn, *a, **k)
    fake.run = run
    monkeypatch.setattr(agent_tools, "_look_at_output",
                        lambda c, t, q, overlay=None: "program frames")
    monkeypatch.setattr(agent_tools, "_touch_job_heartbeat",
                        lambda *a, **k: None)
    env = render_tool.render(ctx, scope="full", wait_s=0)
    assert env.status == envelope.WORKING
    assert env.running["handle"] == "job:41"
    assert "assembled from source frames" in env.body
    jobs[41].update(state="done", result={
        "duration_s": 7.0, "edl_version": 8,
        "audio_qc": {"integrated_lufs": -14.2, "true_peak_dbtp": -1.3},
        "midword_audit": []})
    from harness2 import wait_tool
    done = wait_tool.wait(ctx, "job:41", max_s=1)
    assert done.status == envelope.DONE
    assert done.measured["lufs"] == -14.2 and done.measured["duration_s"] == 7.0


# -- D16 slides ---------------------------------------------------------------

SLIDES = [{"title": "Serve errors: 2", "items": [
    {"t": 1.0, "note": "long serve"}, {"t": 4.5, "note": "net"}]},
    {"title": "Points won", "items": [{"t": 6.0, "note": "block"}]}]


def test_slides_read_back_with_thumbnails(vid_ctx, tmp_path):
    ctx, _fake = vid_ctx
    path, kind, frames, notes = slides.build(ctx, slides._clean(SLIDES),
                                             str(tmp_path / "deck"))
    assert kind == "pptx" and not notes
    from pptx import Presentation
    deck = Presentation(path)
    assert len(deck.slides) == 2
    text = " ".join(sh.text_frame.text for s in deck.slides for sh in s.shapes
                    if sh.has_text_frame)
    assert "Serve errors: 2" in text and "0:01.0  long serve" in text
    pics = sum(1 for s in deck.slides for sh in s.shapes
               if sh.shape_type == 13)
    assert pics == 3
    thumbs = slides.thumbnails(slides._clean(SLIDES), frames, str(tmp_path))
    assert len(thumbs) == 2 and all(os.path.exists(p) for _l, p in thumbs)


def test_slides_fall_back_to_pdf(vid_ctx, tmp_path, monkeypatch):
    ctx, _fake = vid_ctx

    def broken(*a, **k):
        raise ImportError("No module named 'pptx'")
    monkeypatch.setattr(slides, "write_pptx", broken)
    path, kind, _frames, notes = slides.build(ctx, slides._clean(SLIDES),
                                              str(tmp_path / "deck"))
    assert kind == "pdf" and path.endswith(".pdf") and notes
    with open(path, "rb") as fh:
        assert fh.read(5) == b"%PDF-"


def test_render_slides_stores_a_linked_asset(vid_ctx, monkeypatch):
    ctx, fake = vid_ctx
    stored = []
    monkeypatch.setattr(slides.storage, "upload_file",
                        lambda p, k, t: stored.append((k, t)))
    monkeypatch.setattr(slides.storage, "presign_get",
                        lambda k, expires=None: f"https://x/{k}")
    env = render_tool.render(ctx, output="slides", slides=SLIDES)
    assert env.measured["format"] == "pptx" and env.measured["slides"] == 2
    assert stored and stored[0][0].endswith(".pptx")
    assert "https://x/" in env.body and len(env.see) == 2
    asset = next(iter(fake.assets.values()))
    assert asset["kind"] == "render" and asset["meta"]["variant"] == "slides"


# -- D17 find ---------------------------------------------------------------

def test_find_describes_every_hit_and_place_takes_it(monkeypatch, tmp_path):
    ctx, fake = _ctx()
    import stock
    import mcp_search
    import llm
    hits_by_q = {
        "beach": [{"id": "pexels:video:1", "title": "Waves at dusk",
                   "_thumb": "http://t/1.jpg", "duration_s": 9.0,
                   "license": "Pexels"},
                  {"id": "pexels:video:2", "_thumb": "http://t/2.jpg",
                   "duration_s": 7.0}],
        "city": [{"id": "pexels:video:3", "_thumb": "http://t/3.jpg"}]}
    monkeypatch.setattr(stock, "search", lambda q, **k: [dict(h) for h in
                                                         hits_by_q[q]])
    monkeypatch.setattr(llm, "ask_vision", lambda *a, **k: (_ for _ in ())
                        .throw(RuntimeError("vision down")))
    import net_fetch
    import shutil

    def fake_download(url, local, **k):
        _make_video  # noqa: B018
        from PIL import Image
        Image.new("RGB", (64, 36), "blue").save(local)
    monkeypatch.setattr(net_fetch, "download", fake_download)
    env = find_tool.find(ctx, kind="stock_video", query=["beach", "city"])
    assert env.measured == {"queries": 2, "results": 3}
    for line in env.body.splitlines():
        if line.strip().startswith("pexels:"):
            assert line.rstrip().split(" | ")[-1].strip()
    assert "Waves at dusk" in env.body
    assert find_tool.UNDESCRIBED in env.body
    assert len(env.see) == 2                      # a board per query
    assert find_tool.candidates(ctx, "pexels:video:2") == [
        "pexels:video:2", "pexels:video:1"]
    # place by id: the first download fails, the next result is placed
    placed = []

    def fake_place(stage, item_id, at, dur, mode, source_start):
        if item_id == "pexels:video:1":
            return "failed: 404"
        edl = stage.edl
        edl = dict(edl, texts=[{"id": "stand_in", "text": item_id,
                                "start": at, "end": at + dur}])
        placed.append(item_id)
        return stage.write_edl(edl, "placed")
    monkeypatch.setattr(ops_mod.Engines, "place", staticmethod(fake_place))
    out = commit.edit(ctx, [{"place": {"id": "pexels:video:1", "at": 2,
                                       "dur": 3}}], "place")
    assert placed == ["pexels:video:2"] and out.version == 8
    assert any("the next result of the same search" in r
               for r in out.repairs)


def test_find_music_offers_only_the_reference_grade_set(monkeypatch):
    ctx, _fake = _ctx()
    import music_choose
    pool = [{"cid": "good-one", "style": "lofi"}, {"cid": "good-two"}]
    monkeypatch.setattr(music_choose, "set_tracks", lambda: [])
    monkeypatch.setattr(music_choose, "choosable",
                        lambda strict=False: pool if strict else pool + [
                            {"cid": "gate-failed"}])
    monkeypatch.setattr(music_choose, "shortlist",
                        lambda film, n=6, pool=None: ([{"track": t}
                                                       for t in pool], {}))
    env = find_tool.find(ctx, kind="music", query="chill lo-fi")
    assert "lib:good-one" in env.body and "gate-failed" not in env.body
    key, note = find_tool.music_asset(ctx, "lib:gate-failed")
    assert key is None and "reference-grade" in note


# -- C11 film ops -------------------------------------------------------------

def test_card_at_source_lands_on_the_film_clock(monkeypatch):
    import storage
    monkeypatch.setattr(storage, "upload_file", lambda *a, **k: None)
    ctx, fake = _ctx(duration=60.0)
    commit.edit(ctx, [{"cut": [[0, 10]]}], "cut head")
    env = commit.edit(ctx, [{"card": {"text": "Set 2", "at_source": 30.0,
                                      "dur": 2.0}}], "card")
    assert env.version == 9, env.text()
    assert any("sits at 20 s on the film" in r for r in env.repairs)


@pytest.mark.parametrize("op", [
    {"text": {"text": "Hi", "start": 1, "end": 3}},
    {"audio": {"range": [0, 5], "gain_db": -60}},
    {"speed": [{"start": 20, "end": 30, "factor": 2.0}]},
    {"transition": {"style": "crossfade", "duration_s": 0.3}},
    {"zoom": {"start": 2, "end": 4, "strength": 0.15}},
    {"grade": {"preset": "warm"}},
])
def test_each_film_op_lands(op):
    ctx, fake = _ctx(duration=60.0, keep=[[0, 20], [25, 60]])
    env = commit.edit(ctx, [op], "op")
    assert not env.not_applied, env.text()
    assert env.version == 8 or env.changed.startswith("already so")


def test_frame_op_reframes(monkeypatch):
    ctx, fake = _ctx(duration=60.0)
    env = commit.edit(ctx, [{"frame": {"ratio": "9:16", "mode": "crop"}}],
                      "frame")
    assert not env.not_applied, env.text()
    assert fake.rows[-1]["json"]["frame"]["ratio"] == "9:16"


# -- C12 scenes and clips ---------------------------------------------------

def test_scenes_build_a_covered_canvas_and_report_gaps(monkeypatch):
    ctx, fake = _ctx()

    def fake_scenes(stage, scenes, narration):
        edl = dict(stage.edl, texts=[{"id": f"s{i}", "text": "x",
                                      "start": s["start_s"],
                                      "end": s["end_s"]}
                                     for i, s in enumerate(scenes[:2])])
        stage.write_edl(edl, "scenes")
        return ("PLACED 2 of 3 scene(s)\n  not placed: scene 3 (8-12s, "
                "pexels:video:9): 404\nNEXT: render_preview")
    monkeypatch.setattr(ops_mod.Engines, "place_scenes",
                        staticmethod(fake_scenes))
    env = commit.edit(ctx, [{"scenes": {"narration": "n.mp3", "items": [
        {"id": "a", "from": 0, "to": 4}, {"id": "b", "from": 4, "to": 8},
        {"id": "c", "from": 8, "to": 12}]}}], "scenes")
    assert env.version == 8
    assert any("not placed: scene 3" in f for f in env.findings)
    assert not any(f.startswith("NEXT") for f in env.findings)


def test_clips_create_child_projects(monkeypatch):
    ctx, fake = _ctx()
    asked = []

    def fake_shorts(base, clips, style_note=None):
        asked.append(clips)
        base.serial_job_id = 4512
        return f"Started {len(clips)} clip project(s)."
    monkeypatch.setattr(ops_mod.Engines, "make_shorts",
                        staticmethod(fake_shorts))
    env = commit.edit(ctx, [{"clips": {"items": [
        {"range": [1, 9], "title": "one"}, {"range": [10, 19],
                                            "title": "two"}]}}], "clips")
    assert asked and len(asked[0]) == 2 and asked[0][1]["title"] == "two"
    assert env.running["handle"] == "job:4512"


# -- C14 the tool op ----------------------------------------------------------

def test_a_rejecting_verb_is_not_applied_ticketed_and_the_rest_lands(
        monkeypatch):
    ctx, fake = _ctx()
    rows = []
    real = fake.run

    def run(fn, *a, **k):
        if fn is dbx.add_message:
            rows.append(a)
            return 1
        return real(fn, *a, **k)
    fake.run = run

    def refuse(c, **k):
        return "REJECTED: this verb refuses everything."
    monkeypatch.setitem(agent_tools.TOOLS, "add_freeze_frame",
                        (refuse,) + tuple(agent_tools.TOOLS[
                            "add_freeze_frame"][1:]))
    env = commit.edit(ctx, [{"tool": "add_freeze_frame",
                             "args": {"at": 2.0, "duration_s": 1.0}},
                            {"text": {"text": "Hi", "start": 1, "end": 2}}],
                      "mixed")
    assert env.status == envelope.PARTIAL and env.version == 8
    assert env.not_applied[0]["op"] == 0
    assert "refuses everything" in env.not_applied[0]["why"]
    ticket = [r for r in rows if r[1] == "activity"]
    assert ticket and ticket[0][3]["harness"] == "v2"
    assert ticket[0][3]["tool_outcome"]["status"] == "correction_needed"
    assert ticket[0][3]["refusal_class"] in ("technical", "overrule",
                                             "owner_policy", "regex_intent")


def test_refusal_classes_come_from_map_part_2():
    assert tickets.class_of("add_captions") == "technical"
    assert tickets.class_of("no_such_function") == "technical"


# -- G26 MCP: the same dispatcher, envelopes and image blocks ---------------

def test_mcp_runs_v2_calls_through_the_same_dispatcher(monkeypatch):
    import mcp_exec
    ctx, fake = _ctx()
    rows = []
    real = fake.run

    def run(fn, *a, **k):
        if fn is dbx.add_message:
            rows.append(a)
            return 1
        return real(fn, *a, **k)
    fake.run = run
    drained = []
    monkeypatch.setattr(mcp_exec, "_drain_images", lambda c: (
        drained.extend(c.pending_images) or [], 0))
    out = mcp_exec._run_v2(ctx, fake, {"id": 1}, {"chat_session_id": 11},
                           "edit", {"ops": [{"cut": [[1, 2]]}]})
    assert out["harness"] == "v2" and out["edl_changed"] is True
    assert out["text"].startswith("status: done")
    assert out["v2"]["version"] == 8
    assert rows and rows[0][3]["source"] == "mcp"
    src = open(os.path.join(WORKER, "mcp_exec.py")).read()
    assert "if tool in V2_NAMES and (payload.get(\"harness\") == \"v2\"" in src
    names = [t["function"]["name"] for t in mcp_exec._v2_tools()]
    assert names == ["look", "timeline", "edit", "undo", "render", "find",
                     "wait"]


# -- F25 descriptions: the cause logged, a second provider looks -------------

def test_describe_missing_logs_its_cause_and_asks_a_second_lane(
        monkeypatch, tmp_path, capsys):
    import llm
    import mcp_search
    import net_fetch
    from types import SimpleNamespace
    from PIL import Image
    ctx, _fake = _ctx()
    ctx.workdir = str(tmp_path)
    monkeypatch.setattr(net_fetch, "download", lambda url, local, **k:
                        Image.new("RGB", (32, 18)).save(local))
    monkeypatch.setattr(llm, "ask_vision", lambda *a, **k: None)
    monkeypatch.setattr(llm, "last_error",
                        lambda: "429 insufficient_quota")
    answer = json.dumps([{"id": "p:1", "description": "a red bus"}])

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **k: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=answer))], usage=None)))

        def with_options(self, **k):
            return self
    monkeypatch.setattr(llm, "turn_lane", lambda: {"client": Client(),
                                                   "model": "claude-haiku-5-5"})
    monkeypatch.setattr(llm, "agent_sees", lambda m: True)
    hits, missing = mcp_search.describe_missing(
        ctx, [{"id": "p:1", "_thumb": "http://t/1.jpg"}])
    assert hits[0]["description"] == "a red bus" and missing == 0
    out = capsys.readouterr().out
    assert "429 insufficient_quota" in out and "asking the turn lane" in out


def test_v2_clips_pass_the_planner_checks_a_caller_does_not_need(
        monkeypatch):
    """H32: clips the caller chose are their own evidence (a short's own
    footage, a short source, no speech): make_shorts takes them under v2."""
    ctx, fake = _ctx(duration=40.0)
    ctx.project = dict(ctx.project, parent_project_id=5800)
    ctx.index["words"] = []
    seen = []
    monkeypatch.setattr(agent_tools, "_direct_short_clips",
                        lambda c, clips, snap=None: (
                            seen.append(clips) or clips, [], []))
    real = fake.run

    def run(fn, *a, **k):
        if fn is dbx.has_active_job:
            return False
        if fn is dbx.enqueue_job:
            return 4512
        return real(fn, *a, **k)
    fake.run = run
    clips = [{"start": 1.0, "end": 12.0, "title": "one"}]
    v1 = agent_tools.make_shorts(ctx, clips=clips)
    assert v1.startswith("REJECTED: this project IS a generated short")
    ctx.harness = "v2"
    v2 = agent_tools.make_shorts(ctx, clips=clips)
    assert not v2.startswith("REJECTED") and seen
