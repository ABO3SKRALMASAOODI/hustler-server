"""Q8 (30 Oct): the First cut's speed for engine accounts, without changing
what it builds.

  * the B-roll prefetch starts the scout's searches and the short lists'
    downloads in the background; source_broll then places exactly what it
    would have placed, without searching or downloading again;
  * the search cache answers a repeated search, never caches a failed one,
    and hands out copies;
  * a grandfathered account's source_broll searches the libraries exactly as
    before (D1);
  * the draft-first render queues the 720p approval render without waiting,
    delivers on the draft, and re-queues after a corner-mark removal;
  * the live progress lines: once per step, engine accounts only.
"""
import os
import sys
import threading
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_w4c_broll as w4c                                    # noqa: E402

project = w4c.project          # the B-roll suite's fixture: a real source


# ── the search cache ─────────────────────────────────────────────────────

def _lanes(monkeypatch, lanes):
    import broll_source
    import first_cut_speed as fs
    monkeypatch.setattr(broll_source, "LANES", dict(lanes))
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    fs._SLOW.clear()
    fs.clear_search_cache()


def test_the_search_cache_answers_a_repeat_and_never_keeps_a_failure(
        monkeypatch):
    import first_cut_speed as fs
    calls = []

    def wiki(q, kind, orientation, count):
        calls.append(q)
        if q == "down":
            raise RuntimeError("503")
        return [{"id": f"{q}-1", "provider": "wikimedia", "_files": []}]
    _lanes(monkeypatch, {"wikimedia": wiki})
    a = fs.cached_search("the sun", "photo", "portrait")
    b = fs.cached_search("The  Sun", "photo", "portrait")
    assert len(calls) == 1 and a[0] == b[0]
    b[0][0]["_files"].append("mutated")          # acquisition fills it in
    c = fs.cached_search("the sun", "photo", "portrait")
    assert c[0][0]["_files"] == []               # the cache kept its copy
    fs.cached_search("the sun", "video", "portrait")
    assert len(calls) == 2                       # another kind, another search
    fs.cached_search("down", "photo", "portrait")
    fs.cached_search("down", "photo", "portrait")
    assert calls.count("down") == 2              # a failure is never cached
    monkeypatch.setattr(fs, "SEARCH_TTL_S", 0.0)
    fs.clear_search_cache()
    fs.cached_search("the sun", "photo", "portrait")
    fs.cached_search("the sun", "photo", "portrait")
    assert calls.count("the sun") == 4           # expired: searched again


def test_a_library_that_does_not_answer_in_time_is_not_waited_for(monkeypatch):
    """Openverse timed out at 20 s on every search (30 Oct) while the other
    libraries answered in 0.4 s: its siblings' answers are used at the
    deadline, in the libraries' order, and it is skipped for a while."""
    import first_cut_speed as fs
    slow_calls = []

    def openverse(q, kind, orientation, count):
        slow_calls.append(q)
        time.sleep(1.5)
        return [{"id": "ov", "provider": "openverse"}]
    _lanes(monkeypatch, {
        "openverse": openverse,
        "wikimedia": lambda q, k, o, c: [{"id": "wm", "provider": "wikimedia"}],
        "nasa": lambda q, k, o, c: [{"id": "na", "provider": "nasa"}]})
    monkeypatch.setattr(fs, "LANE_DEADLINE_S", 0.3)
    t0 = time.monotonic()
    c, errors, lanes, partial = fs.deadline_search("power plant", "photo", "landscape")
    assert time.monotonic() - t0 < 1.0
    assert [x["id"] for x in c] == ["wm", "na"] and partial
    assert any(e.startswith("openverse") for e in errors)
    c2, _e, _l, _p = fs.deadline_search("the sun", "photo", "landscape")
    assert [x["id"] for x in c2] == ["wm", "na"] and len(slow_calls) == 1
    # video searches never asked openverse (broll_source.search's rule)
    fs._SLOW.clear()
    c3, _e, lanes3, _p = fs.deadline_search("the sun", "video", "landscape")
    assert "openverse" not in lanes3
    # the same libraries in time: the same answer as broll_source.search
    import broll_source
    fs._SLOW.clear()
    monkeypatch.setattr(fs, "LANE_DEADLINE_S", 5.0)
    ref = broll_source.search("power plant", "photo", "landscape")
    got = fs.deadline_search("power plant", "photo", "landscape")
    assert [x["id"] for x in got[0]] == [x["id"] for x in ref[0]]


def test_a_grandfathered_account_searches_exactly_as_before(monkeypatch):
    import broll_source
    import first_cut_speed as fs
    from tools_ext import broll_tools as bt
    plain = types.SimpleNamespace()               # no trial, no new plan
    pf, fn = bt._prefetch_for(plain, "portrait", 1080, 1920, None)
    assert pf is None and fn is broll_source.search
    trial = types.SimpleNamespace(claude_trial=object())
    assert bt._prefetch_for(trial, "portrait", 1080, 1920, None)[1] is \
        fs.cached_search
    first_cut = types.SimpleNamespace(first_cut_q1=True)
    assert bt._prefetch_for(first_cut, "portrait", 1080, 1920, None)[1] is \
        fs.cached_search


# ── the prefetch ─────────────────────────────────────────────────────────

def _counting(monkeypatch, clip):
    import broll_source
    from tools_ext import broll_tools as bt
    calls = {"search": 0, "fetch": 0}

    def nasa(q, k, o, count=4):
        calls["search"] += 1
        return w4c._cands(clip) if k == "video" else []
    real_fetch = bt._fetch

    def fetch(ctx, cand, W, H):
        calls["fetch"] += 1
        return real_fetch(ctx, cand, W, H)
    monkeypatch.setattr(broll_source, "LANES", {"nasa": nasa})
    import first_cut_speed as fs
    fs._SLOW.clear()
    monkeypatch.setattr(bt, "_fetch", fetch)
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    return calls


@w4c.needs_ffmpeg
def test_source_broll_takes_the_prefetch_and_places_the_same_cutaway(
        project, monkeypatch):
    import copy
    import first_cut_speed as fs
    from tools_ext import broll_tools as bt
    ctx, clip, _store = project
    fs.clear_search_cache()
    monkeypatch.setenv("VALMERA_BROLL_LOCAL", "1")
    monkeypatch.setattr(bt, "UNMATCHABLE_DE", 99.0)
    moment = {"id": "rk", "word": "rocket", "query": "rocket launch",
              "purpose": "the rocket", "kind": "any"}
    # without the prefetch: the reference placement
    calls = _counting(monkeypatch, clip)
    base_edl = copy.deepcopy(ctx._edl)
    out = bt.source_broll(ctx, moments=[dict(moment)])
    assert "EDL v1 -> v2" in out, out
    ref = [i for i in ctx._edl["fx"] if i["type"] == "broll"]
    assert calls == {"search": 2, "fetch": 1}
    # the same project again, the prefetch started from the scout's query
    ctx._edl, ctx.version = base_edl, 1
    ctx.pending_images = []
    calls = _counting(monkeypatch, clip)
    fs.clear_search_cache()
    pf = fs.BrollPrefetch(ctx, ["rocket launch"], ("portrait", 1080, 1920),
                          ("video", "photo")).start()
    ctx.broll_prefetch = pf
    deadline = time.monotonic() + 30
    while calls["fetch"] < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    out = bt.source_broll(ctx, moments=[dict(moment)])
    pf.close()
    assert "EDL v1 -> v2" in out, out
    got = [i for i in ctx._edl["fx"] if i["type"] == "broll"]
    # nothing searched or downloaded twice, the same cutaway placed
    assert calls == {"search": 2, "fetch": 1}
    assert pf.stats["search_hits"] == 2 and pf.stats["fetch_hits"] == 1
    strip = lambda it: {k: v for k, v in it.items() if k not in ("match",)}
    assert [strip(i) for i in got] == [strip(i) for i in ref]
    # a First cut has no agent to show the director's board to
    assert ctx.pending_images == []


def test_a_prefetch_for_another_frame_stands_aside(monkeypatch):
    import first_cut_speed as fs
    from tools_ext import broll_tools as bt
    pf = fs.BrollPrefetch(types.SimpleNamespace(), ["x"],
                          ("portrait", 1080, 1920), ("photo",))
    ctx = types.SimpleNamespace(broll_prefetch=pf, first_cut_q1=True)
    assert bt._prefetch_for(ctx, "landscape", 1920, 1080, None)[0] is None
    assert bt._prefetch_for(ctx, "portrait", 1080, 1920, ["nasa"])[0] is None
    assert bt._prefetch_for(ctx, "portrait", 1080, 1920, None)[0] is pf
    pf.close()


def test_the_shortlist_follows_source_brolls_rule(monkeypatch):
    """At most two from one library, SHORTLIST in all, deduplicated across
    moments: the same candidates the placement's short list draws."""
    import broll_source
    import first_cut_speed as fs
    from tools_ext import broll_tools as bt
    fs.clear_search_cache()

    def cand(p, n):
        return {"provider": p, "id": f"{p}:{n}", "kind": "photo",
                "title": f"sun {p} {n}", "width": 2000, "height": 3000,
                "_url": "x"}
    monkeypatch.setattr(broll_source, "LANES", {"pexels": lambda *a: (
        [cand("pexels", 1), cand("pexels", 2), cand("pexels", 3),
         cand("wikimedia", 1), cand("openverse", 1)])})
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    fs._SLOW.clear()
    fetched = []
    lock = threading.Lock()

    def fake_fetch(ctx, c, W, H):
        with lock:
            fetched.append(c["id"])
        return {"cand": c, "got": {"path": "/x"}}
    monkeypatch.setattr(bt, "_fetch", fake_fetch)
    pf = fs.BrollPrefetch(types.SimpleNamespace(), ["the sun", "the sun "],
                          ("portrait", 1080, 1920), ("photo",)).start()
    deadline = time.monotonic() + 5
    while len(fetched) < bt.SHORTLIST and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    pf.close()
    assert pf.queries == ["the sun"]
    assert len(fetched) == bt.SHORTLIST
    assert sum(1 for f in fetched if f.startswith("pexels")) <= 2
    mine = {"provider": "pexels", "id": "pexels:1"}
    hit = pf.fetched(mine)
    assert hit is None or hit["cand"] is mine


def test_the_prefetch_starts_only_for_a_talking_head_on_an_engine_account(
        monkeypatch):
    import config
    import first_cut
    import first_cut_speed as fs
    started = []
    monkeypatch.setattr(fs, "start_broll_prefetch",
                        lambda ctx, m, v: started.append((m, v)) or "pf")
    talking = {"faces": {"share": 0.9}}
    monkeypatch.setattr(first_cut, "_faces", lambda idx: idx.get("faces"))
    monkeypatch.setattr(first_cut, "_speech", lambda idx: True)
    monkeypatch.setattr(first_cut, "_orientation", lambda idx: "vertical")
    moments = [(1.0, 1.5, "sun", "the sun in space")]
    ctx = types.SimpleNamespace(first_cut_q1=False, index=talking)
    assert first_cut._start_prefetch(ctx, moments) is None
    ctx = types.SimpleNamespace(first_cut_q1=True, index={"faces": None})
    assert first_cut._start_prefetch(ctx, moments) is None
    ctx = types.SimpleNamespace(first_cut_q1=True, index=talking)
    monkeypatch.setattr(config, "FIRST_CUT_BROLL_PREFETCH", False)
    assert first_cut._start_prefetch(ctx, moments) is None
    monkeypatch.setattr(config, "FIRST_CUT_BROLL_PREFETCH", True)
    assert first_cut._start_prefetch(ctx, moments) == "pf"
    assert started == [(moments, True)]


def test_the_predicted_frame_is_the_one_source_broll_reads():
    import first_cut_speed as fs
    edl = {"version": 3, "json": {"keep": [[0, 10]]}}
    vertical = types.SimpleNamespace(latest_edl=lambda: edl)
    # a vertical source keeps its EDL frame (none: source_broll's default)
    assert fs.predicted_frame(vertical, True) == ("landscape", 1920, 1080)
    # a landscape talking head is reframed to 9:16 before the cutaways
    assert fs.predicted_frame(vertical, False) == ("portrait", 1080, 1920)


# ── draft first ──────────────────────────────────────────────────────────

def test_the_draft_is_delivered_while_the_approval_render_runs(monkeypatch):
    import agent_tools
    import first_cut
    calls = []

    def render_preview(ctx, quality="draft", _wait_timeout_s=None, **k):
        calls.append(("queue", quality, _wait_timeout_s))
        return "PREREQUISITE: complete preview for EDL v4 is still running as job 9."

    def execute(ctx, name, args):
        calls.append(("execute", name, args.get("quality"), args.get("complete")))
        ctx.last_preview = {"edl_version": 4, "quality": "draft"}
        return "Preview v4 rendered: 27.1s"
    monkeypatch.setattr(agent_tools, "render_preview", render_preview)
    monkeypatch.setattr(agent_tools, "execute", execute)
    ctx = types.SimpleNamespace()
    steps = []
    note = first_cut._render_draft_first(ctx, steps)
    # the draft first, then the approval queued (two encodes side by side
    # on one machine slowed the draft to the approval's pace)
    assert calls == [("execute", "render_preview", "draft", True),
                     ("queue", "approval", 0)]
    assert note.startswith("Preview v4 rendered")
    assert steps[-1]["quality"] == "draft" and steps[-1]["ok"]
    assert "still running" in ctx.first_cut_upgrade


def test_a_corner_mark_removal_requeues_the_approval(monkeypatch):
    import agent_tools
    import first_cut
    calls = []
    notes = iter(["PICTURE QC: GRAPHIC ON THE CORNER MARK: 'mg2' here",
                  "Preview v5 rendered: 20.0s"])
    monkeypatch.setattr(agent_tools, "render_preview",
                        lambda ctx, quality="draft", _wait_timeout_s=None, **k:
                        calls.append(("queue", quality)) or "queued")
    monkeypatch.setattr(agent_tools, "execute",
                        lambda ctx, name, args: (calls.append((name, args.get("id")))
                                                 or (next(notes) if name == "render_preview"
                                                     else "removed")))
    steps = []
    note = first_cut._render_draft_first(types.SimpleNamespace(), steps)
    assert note == "Preview v5 rendered: 20.0s"
    assert calls == [("render_preview", None),
                     ("remove_motion_graphic", "mg2"),
                     ("render_preview", None), ("queue", "approval")]
    # separate slots: the approval is queued before each draft
    import config
    monkeypatch.setattr(config, "FIRST_CUT_UPGRADE_PARALLEL", True)
    calls.clear()
    notes = iter(["Preview v6 rendered: 20.0s"])
    monkeypatch.setattr(agent_tools, "execute",
                        lambda ctx, name, args: calls.append((name, None)) or next(notes))
    first_cut._render_draft_first(types.SimpleNamespace(), [])
    assert calls == [("queue", "approval"), ("render_preview", None)]


# ── progress lines ───────────────────────────────────────────────────────

def test_progress_lines_are_posted_once_and_only_for_engine_accounts():
    import db as dbx
    import first_cut
    posted = []

    class DB:
        def run(self, fn, *a):
            assert fn is dbx.add_message
            posted.append(a)
            return len(posted)
    ctx = types.SimpleNamespace(first_cut_q1=True, first_cut_db=DB(),
                                first_cut_session=11, job={"id": 5})
    first_cut._say(ctx, "pick", "Picked the strongest 31 seconds")
    first_cut._say(ctx, "pick", "Picked the strongest 31 seconds")
    first_cut._say(ctx, "captions", "Added captions")
    assert [p[2] for p in posted] == ["Picked the strongest 31 seconds",
                                      "Added captions"]
    assert all(p[1] == "activity" and p[3]["kind"] == "first_cut_step"
               and p[3]["job_id"] == 5 for p in posted)
    posted.clear()
    old = types.SimpleNamespace(first_cut_q1=False, first_cut_db=DB(),
                                first_cut_session=11, job={"id": 6})
    first_cut._say(old, "pick", "Picked")
    assert posted == []


def test_a_failing_progress_write_never_sinks_the_cut():
    import first_cut_speed as fs

    class DB:
        def run(self, *a):
            raise RuntimeError("db down")
    fs.progress(types.SimpleNamespace(), DB(), 3, "x", "label")   # no raise
    fs.progress(types.SimpleNamespace(), DB(), None, "x", "label")
