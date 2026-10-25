"""First cut (PLAN.md P2 + C2.4, owner decision D2): a fixed tool chain, no
agent loop, one approval render, nothing charged."""
from types import SimpleNamespace as NS

import agent_tools
import claude_lane
import config
import first_cut


def _index(duration, width=1920, height=1080, words=40):
    sentences, t = [], 0.0
    for i in range(int(duration // 5)):
        sentences.append({"id": f"s{i}", "t0": t, "t1": t + 4.5,
                          "text": "word " * (12 if 300 < t < 345 else 6)})
        t += 5.0
    return {"video": {"duration": duration, "width": width, "height": height},
            "words": [{"w": "x", "s": i, "e": i + 0.3} for i in range(words)],
            "sentences": sentences}


class Ctx:
    def __init__(self, index):
        self.index = index
        self.duration = index["video"]["duration"]
        self.versions_written = []
        self.web_recordings = []
        self.request_previews = 0
        self.last_preview = None


def _record(monkeypatch, ctx, results=None):
    calls = []

    def execute(c, name, args):
        calls.append((name, args))
        if name not in ("render_preview",):
            c.versions_written.append(len(c.versions_written) + 2)
        if name == "record_website_demo":
            c.web_recordings.append({"storage_key": "web/9/capture.mp4"})
        return (results or {}).get(name, f"EDL v{len(c.versions_written) + 1} ok")
    monkeypatch.setattr(agent_tools, "execute", execute)
    return calls


def test_short_landscape_talk_gets_the_whole_clean_chain(monkeypatch):
    ctx = Ctx(_index(60))
    calls = _record(monkeypatch, ctx)
    steps, applied, picked = first_cut.video_chain(ctx, None, _clock(), {"usd": 0})
    assert [c[0] for c in calls] == [
        "cut_silences", "remove_filler_words", "auto_reframe",
        "apply_format_pack", "add_captions"]
    assert calls[2][1] == {"ratio": "9:16"}
    assert calls[3][1] == {"pack": "auto", "intensity": "calm"}
    assert picked is None
    assert "captions" in applied and "reframed to 9:16" in applied


def test_vertical_phone_video_without_speech_skips_speech_and_reframe(
        monkeypatch):
    ctx = Ctx(_index(40, width=1080, height=1920, words=0))
    calls = _record(monkeypatch, ctx)
    first_cut.video_chain(ctx, None, _clock(), {"usd": 0})
    assert [c[0] for c in calls] == ["apply_format_pack"]


def test_long_source_cuts_to_one_scouted_story_first(monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    ctx = Ctx(_index(72 * 60))
    calls = _record(monkeypatch, ctx)
    _steps, applied, picked = first_cut.video_chain(
        ctx, None, _clock(), {"usd": 0})
    assert calls[0][0] == "keep_segments"
    start, end = calls[0][1]["segments"][0]
    assert 30 <= end - start <= 55
    assert 295 <= start <= 345            # the dense passage, not the intro
    assert picked["how"] == "density" and applied[0] == "best moment"


def _fake_create(answer, usage=None, seen=None):
    """claude_lane.create stand-in: one chat-shaped completion."""
    def create(model, messages, max_tokens=None, effort=None, **kw):
        if seen is not None:
            seen["user"] = messages[-1]["content"]
            seen["model"] = model
        return NS(choices=[NS(message=NS(content=answer))], usage=usage)
    return create



def test_the_scout_reads_only_the_first_thirty_minutes(monkeypatch):
    seen = {}
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "create", _fake_create(
        '{"start_id": 300, "end_id": 305, "title": "The pivot"}',
        NS(provider_cost_usd=0.00052), seen))
    monkeypatch.setattr(first_cut.llm, "record", lambda *a: None)
    ctx = Ctx(_index(72 * 60))
    ctx.first_cut_model = "claude-haiku-5-5"
    spend = {"usd": 0.0}
    start, end, title, how = first_cut.scout_range(ctx, None, spend)
    assert how == "scout" and title == "The pivot"
    assert (start, end) == (1500.0, 1529.5)
    last_id = max(int(line.split()[0][2:]) for line in
                  seen["user"].splitlines()[1:])
    assert last_id * 5.0 < config.FIRST_CUT_MAX_SOURCE_S
    assert 0 < spend["usd"] < 0.002 and seen["model"] == "claude-haiku-5-5"


def test_without_a_lane_model_the_scout_never_calls_a_model(monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: True)

    def boom(*a, **k):
        raise AssertionError("no model call without the trial scope")
    monkeypatch.setattr(claude_lane, "create", boom)
    start, end, _t, how = first_cut.scout_range(Ctx(_index(20 * 60)), None,
                                                {"usd": 0.0})
    assert how in ("density", "opening")


def test_a_short_scout_pick_runs_on_to_a_full_story(monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "create", _fake_create(
        '{"start_id": "s30", "end_id": "s31"}', NS(provider_cost_usd=0.0)))
    monkeypatch.setattr(first_cut.llm, "record", lambda *a: None)
    ctx = Ctx(_index(20 * 60))
    ctx.first_cut_model = "claude-haiku-5-5"
    start, end, _t, how = first_cut.scout_range(ctx, None, {"usd": 0.0})
    assert how == "scout" and start == 150.0 and 20 <= end - start <= 60


def test_a_bad_scout_answer_falls_back_to_the_deterministic_pick(monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "create", _fake_create(
        '{"start_id": "s9", "end_id": "s2"}', NS(provider_cost_usd=0.0)))
    monkeypatch.setattr(first_cut.llm, "record", lambda *a: None)
    ctx = Ctx(_index(20 * 60))
    ctx.first_cut_model = "claude-haiku-5-5"
    _s, _e, _t, how = first_cut.scout_range(ctx, None, {"usd": 0.0})
    assert how == "density"


def test_a_late_chain_skips_edits_and_keeps_time_for_the_render(monkeypatch):
    ctx = Ctx(_index(60))
    calls = _record(monkeypatch, ctx)
    late = _clock(elapsed=config.FIRST_CUT_MAX_S - 60)
    steps, applied, _picked = first_cut.video_chain(ctx, None, late,
                                                    {"usd": 0})
    assert calls == [] and applied == []
    assert all(step.get("skipped") == "time" for step in steps)


def test_url_records_then_showcases_the_capture(monkeypatch):
    ctx = Ctx({"video": {"duration": 0}})
    ctx.index = {}
    calls = _record(monkeypatch, ctx)
    steps, applied = first_cut.url_chain(ctx, None, "https://example.com/",
                                         _clock())
    assert [c[0] for c in calls] == ["record_website_demo", "showcase_demo"]
    assert calls[0][1]["orientation"] == "portrait"
    assert calls[0][1]["steps"] == first_cut.DEFAULT_SCROLL_STEPS
    assert calls[1][1]["asset_key"] == "web/9/capture.mp4"


def test_failed_steps_are_not_claimed(monkeypatch):
    ctx = Ctx(_index(60))
    _record(monkeypatch, ctx, {"auto_reframe": "REJECTED: no frame",
                               "add_captions": "CORRECTION NEEDED: x",
                               "cut_silences": "NO CHANGE — the EDL is identical"})
    _steps, applied, _picked = first_cut.video_chain(ctx, None, _clock(),
                                                     {"usd": 0})
    assert "reframed to 9:16" not in applied and "captions" not in applied
    assert "pauses trimmed" not in applied


def test_the_capture_key_is_read_from_the_tool_reply(monkeypatch):
    ctx = Ctx({"video": {"duration": 0}})
    calls = []

    def execute(c, name, args):
        calls.append((name, args))
        if name == "record_website_demo":
            return ("Recorded a walkthrough — 14.8s at 1080x1920: "
                    "storage_key=fetched/4695/40a996fb2036.mp4.\nEVENT TRACK")
        return "EDL v2 placed"
    monkeypatch.setattr(agent_tools, "execute", execute)
    first_cut.url_chain(ctx, None, "https://valmera.io/", _clock())
    assert calls[1] == ("showcase_demo", {"asset_key": "fetched/4695/40a996fb2036.mp4",
                                          "zoom_strength": 0.4})


def test_summary_says_what_was_done():
    assert first_cut._summary(["pauses trimmed", "captions", "a format pack"],
                              31.4) == \
        "a 31-second vertical version: pauses trimmed and captions"
    assert first_cut._summary([], 18, url="https://example.com/") == \
        "a 18-second vertical demo of https://example.com/"


def test_url_is_found_in_a_request():
    assert first_cut.url_in("Record my website https://acme.io/pricing. Thanks") \
        == "https://acme.io/pricing"
    assert first_cut.url_in("no link here") is None


def _clock(elapsed=0.0):
    import time
    return time.monotonic() - elapsed


class _Conn:
    def __init__(self, existing=None):
        self.existing, self.inserted = existing, []

    def cursor(self):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                self.sql = sql

            def fetchone(self):
                return conn.existing
        return Cur()


def test_enqueue_is_once_per_project(monkeypatch):
    added = []
    monkeypatch.setattr(first_cut.dbx, "enqueue_job",
                        lambda conn, p, u, t, payload: added.append(payload) or 7)
    assert first_cut.enqueue(_Conn(existing={"id": 3}), 9, 8) is None
    assert first_cut.enqueue(_Conn(existing=None), 9, 8) == 7
    assert added == [{"first_cut": True, "trial_lane": True}]


class _MsgConn:
    """The message-lock probe of db.resolve_pending_auto_resume."""

    def cursor(self):
        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return {"id": 12}
        return Cur()


def _resume(monkeypatch, *, lane=True, requests=True, remaining=0.1,
            admission={"kind": "trial"}):
    import db
    added, retired = [], []
    monkeypatch.setattr(db, "subscribe_gate_applies", lambda conn, uid: True)
    monkeypatch.setattr(db, "mark_message_subscribe_gated",
                        lambda conn, mid: retired.append(mid))
    monkeypatch.setattr(db, "enqueue_job",
                        lambda conn, p, u, t, payload: added.append(payload) or 77)
    monkeypatch.setattr(first_cut, "project_admission", lambda conn, p: admission)
    monkeypatch.setattr(first_cut, "trial_requests_on",
                        lambda conn, uid=None: requests)
    monkeypatch.setattr(first_cut, "trial_remaining_usd",
                        lambda conn, u: remaining)
    monkeypatch.setattr(claude_lane, "available", lambda: lane)
    out = db.resolve_pending_auto_resume(_MsgConn(), 9, 10, 11, 12)
    return out, added, retired


def test_a_saved_brief_on_the_trial_project_runs_on_the_trial_lane(monkeypatch):
    out, added, retired = _resume(monkeypatch)
    assert out == {"state": "enqueued", "job_id": 77, "trial": True}
    assert added == [{"message_id": 12, "auto_resumed": True,
                      "trial_lane": True}]
    assert retired == []


def test_the_brief_meets_the_wall_without_the_lane_switch_or_budget(
        monkeypatch):
    for kwargs in ({"lane": False}, {"requests": False}, {"remaining": 0.0},
                   {"admission": None}, {"admission": {"kind": "payer"}}):
        out, added, retired = _resume(monkeypatch, **kwargs)
        assert out["state"] == "gated" and added == [] and retired == [12]


def test_a_step_that_changed_nothing_is_never_claimed():
    assert first_cut._ok("EDL v1 -> v2: cut 1 gap(s) in the speech")
    assert not first_cut._ok(
        "No filler words ['um', 'uh'] were found in the transcript, so "
        "nothing was removed.")
    assert not first_cut._ok("NO CHANGE: already vertical")
    assert not first_cut._ok(None)


def test_a_graphic_in_the_corner_mark_is_removed_and_rerendered(monkeypatch):
    calls = []

    def execute(ctx, name, args):
        calls.append((name, args))
        return "EDL v5 -> v6: removed"
    monkeypatch.setattr(first_cut.agent_tools, "execute", execute)
    render = ("Preview v5 rendered. PICTURE QC preview: GRAPHIC ON THE CORNER "
              "MARK: 'ts1' draws from y 0.079, into the free-tier mark's box; "
              "GRAPHIC ON THE CORNER MARK: 'mg2' draws from y 0.1")
    steps = []
    ctx = Ctx(_index(42))
    assert first_cut._clear_corner_mark(ctx, steps, render) is True
    assert ("remove_typeset", {"ids": ["ts1"]}) in calls
    assert ("remove_motion_graphic", {"id": "mg2"}) in calls
    assert first_cut._clear_corner_mark(ctx, [], "Preview v6 rendered, clean") is False
