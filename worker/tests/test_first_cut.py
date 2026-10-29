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
                          "text": "Word " + "word " * (10 if 300 < t < 345 else 4) + "end."})
        t += 5.0
    return {"video": {"duration": duration, "width": width, "height": height},
            "words": [{"w": "word", "s": i, "e": i + 0.3} for i in range(words)],
            "sentences": sentences}


class Ctx:
    def __init__(self, index):
        self.index = index
        self.duration = index["video"]["duration"]
        self.versions_written = []
        self.web_recordings = []
        # No request_previews: main removed it from ToolContext (19489348),
        # and a fake that kept it hid that every real First cut crashed.
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
        "add_motion_graphic", "add_captions"]
    assert calls[0][1] == {"min_silence_s": first_cut.PAUSE_CUT_S, "padding_s": first_cut.PAUSE_PAD_S}
    assert calls[2][1] == {"ratio": "9:16"}
    hook = calls[3][1]
    assert hook["template"] == "hook_title" and hook["start"] == 0.0
    assert hook["params"]["font"] == "Anton" and "*" in hook["params"]["text"]
    assert calls[4][1]["max_words_per_caption"] == first_cut.CAPTION_WORDS
    assert picked["how"] == "rules"
    assert "captions" in applied and "reframed to 9:16" in applied
    assert "a hook title" in applied


def test_vertical_phone_video_without_speech_skips_speech_and_reframe(
        monkeypatch):
    ctx = Ctx(_index(40, width=1080, height=1920, words=0))
    calls = _record(monkeypatch, ctx)
    first_cut.video_chain(ctx, None, _clock(), {"usd": 0})
    assert [c[0] for c in calls] == []


def test_a_screen_recording_keeps_its_shape(monkeypatch):
    index = _index(50)
    index["spatial"] = {"samples": [{"t": i, "faces": [], "dense_ui": True,
                                     "text": []} for i in range(10)]}
    ctx = Ctx(index)
    calls = _record(monkeypatch, ctx)
    _steps, applied, _p = first_cut.video_chain(ctx, None, _clock(), {"usd": 0})
    assert "auto_reframe" not in [c[0] for c in calls]
    assert any("own shape" in a for a in applied)
    assert first_cut._summary(applied, 30) .startswith("a 30-second version")


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


def test_url_records_a_tour_then_showcases_it_under_the_site_title(monkeypatch):
    ctx = Ctx({"video": {"duration": 0}})
    ctx.index = {}
    calls = []

    def execute(c, name, args):
        calls.append((name, args))
        if name == "record_website_demo":
            c.web_recordings.append({
                "storage_key": "web/9/capture.mp4",
                "page_title": "Acme | Payments infrastructure for the internet",
                "h1": "Financial infrastructure to grow your revenue"})
        return "EDL v2 ok"
    monkeypatch.setattr(agent_tools, "execute", execute)
    steps, applied = first_cut.url_chain(ctx, None, "https://example.com/",
                                         _clock())
    assert [c[0] for c in calls] == ["record_website_demo", "showcase_demo",
                                     "add_motion_graphic"]
    assert calls[0][1]["orientation"] == "portrait"
    assert calls[0][1]["steps"] == first_cut.TOUR_STEPS
    assert calls[1][1]["asset_key"] == "web/9/capture.mp4"
    assert calls[1][1]["zoom_strength"] == first_cut.DEMO_ZOOM
    # the site's name as a two-family poster, never its own <h1> (which the
    # hero already shows), on a soft scrim over the live site
    assert calls[2][1]["params"]["text"] == "Meet / *Acme*"
    assert calls[2][1]["end"] == first_cut.DEMO_TITLE_S
    assert calls[2][1]["mute_captions"] is True


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
                                          "zoom_strength": first_cut.DEMO_ZOOM})
    # the site's own name from its host when the capture carries no title
    assert calls[2][1]["params"]["text"] == "Meet / *Valmera*"


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


# ---- craft defaults (REFERENCE_PLAYBOOK.md, C1 29 Oct 2026) ----------------

def test_the_hook_is_faithful_short_and_accented():
    index = {"words": [{"w": "a"}, {"w": "million"}, {"w": "times"}]}
    assert first_cut.clean_hook("The *real* cost of AI") == "The *real* cost of AI"
    assert first_cut.clean_hook("Growing the economy") == "Growing the *economy*"
    assert first_cut.clean_hook("10x the economy", index) is None    # never said
    assert first_cut.clean_hook("one", index) is None
    assert first_cut.clean_hook("So uh the *big* tour") == "So the *big* tour"
    assert first_cut.clean_hook("a b c d e f g h") is None
    assert first_cut._opening_hook([{"text": "And so we built the thing."}]) \
        == "we built the *thing*"


def test_the_opening_skips_mic_checks_and_fragments():
    rows = [{"id": "s1", "t0": 2.1, "t1": 4.0, "text": "Okay, is this recording?"},
            {"id": "s2", "t0": 4.5, "t1": 5.5, "text": "Okay, cool."},
            {"id": "s3", "t0": 7.9, "t1": 11.5, "text": "This is the Python tutorial."},
            {"id": "s4", "t0": 12.0, "t1": 14.0, "text": "Thanks for watching."}]
    first, last = first_cut._trim_pick(rows, 15.0)
    assert first["id"] == "s3" and last["id"] == "s4"
    frag = [{"id": "s1", "t0": 0.0, "t1": 2.8, "text": "economy by a factor of ten."},
            {"id": "s2", "t0": 3.9, "t1": 6.8, "text": "So since we're small"}]
    first, last = first_cut._trim_pick(frag, 6.9)
    assert first["id"] == "s2" and last["id"] == "s2"


class _EdlCtx(Ctx):
    def __init__(self, index, keep):
        super().__init__(index)
        self.keep = keep

    def latest_edl(self):
        return {"json": {"keep": self.keep}}


def test_the_ends_start_on_a_real_word_and_run_on_live(monkeypatch):
    words = [{"w": "And", "t0": 1.0, "t1": 1.2}, {"w": "this", "t0": 1.25, "t1": 1.5},
             {"w": "works.", "t0": 1.55, "t1": 2.0}, {"w": "Last", "t0": 8.0, "t1": 8.4},
             {"w": "line.", "t0": 8.45, "t1": 9.0}, {"w": "Next", "t0": 9.4, "t1": 9.8}]
    ctx = _EdlCtx({"video": {"duration": 12.0}, "words": words},
                  [[0.5, 2.1], [7.9, 9.1]])
    calls = _record(monkeypatch, ctx)
    first_cut.shape_ends(ctx, [], _clock())
    name, args = calls[0]
    assert name == "keep_segments" and args["snap_to_words"] is False
    (s0, _e0), (_s1, e1) = args["segments"]
    assert s0 == 1.21                              # on "this", not "And"
    assert e1 == round(9.4 - first_cut.TAIL_NEXT_GAP_S, 3)   # live, short of "Next"
    ctx2 = _EdlCtx({"video": {"duration": 9.2}, "words": words[1:5]},
                   [[1.21, 2.1], [7.9, 9.15]])
    calls = _record(monkeypatch, ctx2)
    first_cut.shape_ends(ctx2, [], _clock())
    assert calls[0][1]["segments"][-1][1] == 9.2   # the source ends first


def _frames(top=0, bottom=0, h=100, w=60, n=10, still=False):
    import numpy as np
    rng = np.random.default_rng(3)
    out = []
    for k in range(n):
        f = np.full((h, w), 20, np.uint8)
        pic = rng.integers(40, 220, (h - top - bottom, w), dtype=np.uint8)
        if still:
            pic = np.full_like(pic, 120)
        f[top:h - bottom] = pic
        if top:
            f[top // 2] = 200                       # a static line inside the bar
        out.append(f)
    return out


def test_static_bands_are_found_only_around_a_live_picture():
    assert first_cut.static_bands(_frames(top=14, bottom=16)) == [0.14, 0.84]
    assert first_cut.static_bands(_frames()) is None              # no bars
    assert first_cut.static_bands(_frames(top=14)) is None        # one bar only
    assert first_cut.static_bands(_frames(top=14, bottom=16, still=True)) is None
    assert first_cut.static_bands(_frames(top=45, bottom=45)) is None  # a strip


def test_captions_and_hook_sit_under_the_face():
    faces = {"bottom": 0.58, "width": 0.4, "share": 0.9}
    assert first_cut.chest_y(faces) == 0.66
    assert first_cut.chest_y(faces, [0.15, 0.85]) == 0.694          # after debanding
    assert first_cut.chest_y({"bottom": 0.3, "width": .2, "share": 1}) == 0.58
    assert first_cut.chest_y({"bottom": 0.75, "width": .2, "share": 1}) == 0.70
    assert first_cut.chest_y(None) is None


def test_the_spoken_opening_becomes_a_poster_line():
    words = [{"w": w, "t0": 0.3 * k, "t1": 0.3 * k + 0.25} for k, w in enumerate(
        "So since we're a trillionth of the sun's energy, if you".split())]
    ctx = _EdlCtx({"video": {"duration": 30.0}, "words": words}, [[0.0, 20.0]])
    text, end = first_cut.spoken_opening(ctx)
    assert text == "since we're a *trillionth* of the sun's energy"
    assert 1.2 <= end <= 3.2


def test_without_a_clause_end_the_poster_line_is_six_content_words():
    words = [{"w": w, "t0": 0.3 * k, "t1": 0.3 * k + 0.25} for k, w in enumerate(
        "And we built the biggest rocket in the history of the world today".split())]
    ctx = _EdlCtx({"video": {"duration": 30.0}, "words": words}, [[0.0, 20.0]])
    text, _end = first_cut.spoken_opening(ctx)
    assert text == "we built the *biggest* rocket"


def test_an_unfinished_last_line_walks_back_to_a_complete_one():
    rows = [{"id": "s1", "t0": 0, "t1": 5, "text": "This is the opening line here."},
            {"id": "s2", "t0": 5, "t1": 9, "text": "And I do enjoy your unrelenting optimism."},
            {"id": "s3", "t0": 9, "t1": 10, "text": "Yeah, exactly."},
            {"id": "s4", "t0": 10, "t1": 13, "text": "to me when you've roasted beyond so"}]
    assert first_cut._complete_end(rows, rows[0], rows[3])["id"] == "s2"
    assert first_cut._complete_end(rows, rows[3], rows[3])["id"] == "s4"


def test_website_request_hints_are_read_from_its_words():
    tile = ("Record my website https://www.sparklenloop.com/ as a 20-second "
            "vertical (9:16) demo: scroll the page from top to bottom at a "
            "steady pace, then make it a polished showcase video.")
    h = first_cut.url_hints(tile)
    assert h["mode"] == "scroll" and h["seconds"] == 20
    assert "orientation" not in h
    assert first_cut.tour_steps(h)[1]["mode"] == "scroll"
    h = first_cut.url_hints("Record a demo of https://x.dev in landscape: wait "
                            "2 seconds, scroll down to the bottom.")
    assert h["orientation"] == "landscape" and h["mode"] == "scroll"
    h = first_cut.url_hints("Create a premium 60-second cinematic promotional "
                            "video for https://osai.ie. Open the Pricing page.")
    assert "mode" not in h and first_cut.tour_steps(h)[1]["by"] == 3
    assert "pricing" in first_cut.tour_steps(h)[1]["prefer"].lower()
    assert first_cut.url_hints(None) == {}


def test_montage_windows_pick_moving_shots_one_each_in_order():
    import numpy as np
    fps = 4.0
    n = 240                               # 60 s
    times = np.arange(n) / fps
    diff = np.full(n, 0.2, np.float32)    # mostly static
    luma = np.full(n, 90.0, np.float32)
    shots = [{"start": float(a), "end": float(a + 5)} for a in range(0, 60, 5)]
    for k, sh in enumerate(shots):
        if k % 2 == 0:                    # every other shot moves
            sel = (times >= sh["start"]) & (times < sh["end"])
            diff[sel] = 3.0 + k
    luma[(times >= 20) & (times < 25)] = 3.0     # a black shot (moving)
    diff[(times >= 20) & (times < 25)] = 9.0
    out = first_cut.montage_windows((times, diff, luma), shots, 60.0, target=4.0)
    assert out and out == sorted(out)
    # never inside the black shot, never two windows in one shot
    assert all(not (20 <= a < 25) for a, _b in out)
    owners = [int(a // 5) for a, _b in out]
    assert len(owners) == len(set(owners))
    assert all(int(a // 5) % 2 == 0 for a, _b in out)


def test_beats_are_the_speakers_own_words_and_land_apart(monkeypatch):
    index = {"words": [{"w": w, "t0": t * 0.32, "t1": t * 0.32 + 0.3} for t, w in
                       enumerate("we use a million times more power than "
                                 "one millionth of the sun".split())],
             "sentences": [{"id": "s1", "t0": 0, "t1": 12,
                            "text": "we use a million times more power"}]}
    by_id = {"s1": index["sentences"][0]}
    spans = first_cut.beat_spans(index, by_id, [
        {"id": "s1", "words": "a million times"},
        {"id": "s1", "words": "a billion"},          # never said: dropped
        {"id": "s9", "words": "power"}])             # unknown id: dropped
    assert [t for _a, _b, t in spans] == ["a million times"]
    assert first_cut._star("a million times") == "a *million* times"
    assert first_cut._star("one millionth") == "one *millionth*"
    assert first_cut._star("pretty magnificent") == "pretty *magnificent*"
    nums = [t for _a, _b, t in first_cut.number_beats(index)]
    assert "a million" in nums or "million times" in nums


def test_a_beat_keeps_its_spoken_number():
    index = {"words": [{"w": w, "t0": k * 0.3, "t1": k * 0.3 + 0.28} for k, w in
                       enumerate("the engineer is 30 feet away".split())]}
    row = {"id": "s1", "t0": 0, "t1": 2}
    spans = first_cut.beat_spans(index, {"s1": row}, [{"id": "s1", "words": "30 feet"}])
    assert [t for _a, _b, t in spans] == ["30 feet"]
    assert first_cut._star("30 feet") == "*30* feet"
    assert [t for _a, _b, t in first_cut.number_beats(index)] == ["30 feet"]


def test_a_real_brief_gets_the_agent_after_the_first_cut():
    tile = ("Record my website https://www.sparklenloop.com/ as a 20-second "
            "vertical (9:16) demo: scroll the page from top to bottom at a "
            "steady pace, then make it a polished showcase video.")
    assert not first_cut.is_brief(tile)
    assert not first_cut.is_brief("https://stoepandsite.co.za/")
    assert not first_cut.is_brief("record a demo of https://stripe.com")
    assert first_cut.is_brief(
        "https://klosmo-store.com/ Create a premium fashion e-commerce "
        "promotional video using https://klosmo-store.com. Navigate through "
        "the homepage, open the designer brands section, explore selected "
        "fashion collections, and end on a Discover KLOSMO call to action.")


def test_a_song_is_not_speech_and_a_finished_piece_stays_whole():
    garbage = {"words": [{"w": w, "t0": k, "t1": k + 0.5} for k, w in
                         enumerate(["ʰʰʰʰʰʰʰʰ", "ლ", "ლ", "ʰʰʰ"] * 5)],
               "perception": {"bpm": 83.4, "beats": [0.3, 1.0, 1.8]}}
    assert not first_cut._speech(garbage)
    assert first_cut._music_piece(garbage, 32.0)
    sung = {"words": [{"w": "هنيئاً", "t0": k * 1.2, "t1": k * 1.2 + 1} for k in range(30)],
            "perception": {"bpm": 169.9, "beats": []}}
    assert first_cut._speech(sung) and first_cut._music_piece(sung, 39.0)
    talk = {"words": [{"w": "word", "t0": k * 0.3, "t1": k * 0.3 + 0.25} for k in range(120)],
            "perception": {"bpm": 140.9, "beats": []}}
    assert first_cut._speech(talk) and not first_cut._music_piece(talk, 40.0)
    assert not first_cut._music_piece({"words": [], "perception": {}}, 15.0)


def test_the_first_shape_named_wins():
    assert first_cut.url_hints("a 60 s 16:9 promo of https://osai.ie (a 9:16 "
                               "version is optional)")["orientation"] == "landscape"
    assert "orientation" not in first_cut.url_hints(
        "a vertical (9:16) demo of https://x.io, not landscape")


def test_a_long_slow_narration_over_noise_is_not_a_finished_piece():
    vlog = {"words": [{"w": "word", "t0": k * 1.0, "t1": k * 1.0 + 0.3} for k in range(90)],
            "perception": {"bpm": 120.0, "beats": [1.0, 1.5]}}
    assert first_cut._speech(vlog) and not first_cut._music_piece(vlog, 100.0)


def test_the_first_cut_needs_nothing_main_removed_from_the_tool_context():
    """T1 (30 Oct): after the trial branch merged main, every First cut died
    on ``ctx.request_previews`` (main's 19489348 removed the per-request
    preview counter). The First cut now counts its own renders."""
    import inspect
    import agent_tools
    src = inspect.getsource(first_cut)
    assert "ctx.request_previews or" not in src
    assert "self.request_previews" not in inspect.getsource(
        agent_tools.ToolContext.__init__)
    steps = [{"tool": "cut_silences"}, {"tool": "render_preview"},
             {"tool": "render_preview", "ok": False}, "noise"]
    assert first_cut._renders_done(steps) == 2
    assert first_cut._renders_done(None) == 0
