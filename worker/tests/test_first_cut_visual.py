"""First cut visual variety (Q1, 30 Oct 2026): cut-ins, cutaways, the
screen camera, the explainer crop, sung-or-spoken, and who gets them (D1:
trial accounts and NEW subscribers only)."""
from types import SimpleNamespace as NS

import numpy as np

import agent_tools
import claude_plans
import first_cut
import first_cut_visual as fcv
from timeline import Timeline


# ---------------------------------------------------------------- D1 gate

class _Cur:
    """Answers claude_plans' queries: the studio_for_all_plans flag and
    the payment history."""

    def __init__(self, flag, paid_before_cutoff):
        self.flag, self.paid = flag, paid_before_cutoff
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if "to_regclass('public.app_kv')" in s:
            self.row = {"t": "app_kv"}
        elif "to_regclass" in s:
            self.row = {"t": None}
        elif "FROM app_kv" in s:
            import json
            self.row = {"value": json.dumps(self.flag)}
        elif "FROM payments" in s:
            self.row = {"x": 1} if self.paid else None
        else:
            self.row = None

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, flag, paid_before_cutoff=False):
        self.flag, self.paid = flag, paid_before_cutoff

    def cursor(self, *a, **k):
        return _Cur(self.flag, self.paid)


_ON = {"on": True, "cutoff_at": "2026-10-26T00:00:00+00:00"}


def test_trial_accounts_get_the_new_devices():
    assert fcv.engine_on(_Conn(_ON), 7, subscribed=False) is True
    assert fcv.engine_on(None, 7, subscribed=False) is True


def test_a_grandfathered_subscriber_never_gets_them():
    # paid before the D1 cutoff: today's behaviour exactly
    assert fcv.engine_on(_Conn(_ON, paid_before_cutoff=True), 7, True) is False
    # the new-subscriber offer is off: every payer is grandfathered
    assert fcv.engine_on(_Conn({"on": False}), 7, True) is False
    # any doubt about a payer reads as grandfathered
    assert fcv.engine_on(None, 7, True) is False


def test_a_new_subscriber_gets_them():
    assert fcv.engine_on(_Conn(_ON, paid_before_cutoff=False), 7, True) is True


def test_a_lookup_that_fails_keeps_a_payer_grandfathered(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(claude_plans, "applies", boom)
    assert fcv.engine_on(_Conn(_ON), 7, True) is False


def test_run_marks_the_context_for_the_account(monkeypatch):
    """first_cut.run asks engine_on with the account's billing state and
    the chain sees the answer on ctx.first_cut_q1."""
    seen = {}

    def chain(ctx, worker_db, started, spend):
        seen["q1"] = ctx.first_cut_q1
        return [], [], None
    for subscribed, paid, want in ((False, False, True), (True, True, False),
                                   (True, False, True)):
        db = _WorkerDb(_Conn(_ON, paid_before_cutoff=paid), subscribed)
        monkeypatch.setattr(first_cut, "video_chain", chain)
        monkeypatch.setattr(agent_tools, "ToolContext", _ToolCtx)
        import claude_trial
        monkeypatch.setattr(claude_trial, "resolve",
                            lambda *a, **k: _Decision())
        monkeypatch.setattr(claude_trial, "credit_status",
                            lambda *a, **k: {"on": False})
        first_cut.run(db, {"id": 1, "user_id": 7, "project_id": 3,
                           "payload": {"first_cut": True}},
                      {"id": 3, "chat_session_id": 5}, {"json": {}})
        assert seen["q1"] is want, (subscribed, paid)


class _Decision:
    kind = "none"


class _ToolCtx:
    def __init__(self, db, job, project, index, workdir):
        self.index, self.duration = index or {}, 30.0
        self.versions_written, self.request_previews = [], 0
        self.last_preview = None

    def latest_edl(self):
        raise RuntimeError("no edl")


class _WorkerDb:
    def __init__(self, conn, subscribed):
        self.conn, self.subscribed = conn, subscribed

    def run(self, fn, *args, **kw):
        import db as dbx
        if fn is dbx.user_billing:
            return self.subscribed, "pro_2026" if self.subscribed else "free", False
        if fn in (dbx.record_client_event, dbx.add_message):
            return None
        return fn(self.conn, *args, **kw)


def test_a_grandfathered_payers_chain_is_the_c2_chain(monkeypatch):
    """The same talking-head source: without the gate the chain is call
    for call the one before Q1 (no cutaways, no cut-ins, no source_broll,
    the old punches); with it the new devices run."""
    names_off = _talking_chain(monkeypatch, q1=False)
    names_on = _talking_chain(monkeypatch, q1=True)
    assert "punch_in_on_emphasis" in names_off
    for name in ("source_broll", "add_zoom_path", "action_follow"):
        assert name not in names_off
    assert "source_broll" in names_on
    assert "punch_in_on_emphasis" not in names_on


# ------------------------------------------------------- fakes for chains

def _talk_index(duration=40.0, vertical=True):
    words, sents, t, sid = [], [], 0.0, 0
    text = ("The sun gives us more energy than we could ever use, and "
            "a factory full of robots could harness it all. ")
    toks = text.split()
    while t < duration - 1:
        start = t
        for tok in toks:
            if t >= duration - 1:
                break
            words.append({"w": tok, "t0": round(t, 2), "t1": round(t + 0.24, 2),
                          "confidence": 0.98})
            t += 0.3
        sents.append({"id": f"s{sid}", "t0": start, "t1": t,
                      "text": " ".join(w["w"] for w in words
                                       if start <= w["t0"] < t)})
        sid += 1
        t += 0.2
    faces = [[0.35, 0.2, 0.65, 0.45]]
    return {"video": {"duration": duration,
                      "width": 1080 if vertical else 1920,
                      "height": 1920 if vertical else 1080},
            "words": words, "sentences": sents,
            "spatial": {"samples": [{"t": float(k), "faces": faces, "text": []}
                                    for k in range(int(duration))]}}


class _EdlCtx:
    def __init__(self, index):
        self.index = index
        self.duration = index["video"]["duration"]
        self.versions_written, self.web_recordings = [], []
        self.request_previews, self.last_preview = 0, None
        self.edl = {"keep": [[0.0, self.duration]], "fx": []}

    def latest_edl(self):
        return {"json": self.edl, "version": len(self.versions_written) + 1}

    def proxy_path(self):
        raise RuntimeError("no proxy in tests")


def _talking_chain(monkeypatch, q1):
    ctx = _EdlCtx(_talk_index())
    ctx.first_cut_q1 = q1
    calls = []

    def execute(c, name, args):
        calls.append(name)
        c.versions_written.append(1)
        if name == "source_broll":
            for m in args["moments"]:
                c.edl["fx"].append({"type": "broll", "start": m["at"],
                                    "end": m["at"] + m["duration_s"]})
        return "EDL v2 ok"
    monkeypatch.setattr(agent_tools, "execute", execute)

    def trim(c, worker_db, spend):
        c.first_cut_hook = "The *sun* gives"
        c.first_cut_broll = [(1.5 + 6 * k, 1.8 + 6 * k, "sun", "the sun")
                             for k in range(5)] if q1 else None
        return 0.0, c.duration, "rules"
    monkeypatch.setattr(first_cut, "trim_range", trim)
    first_cut.video_chain(c := ctx, None, _clock(), {"usd": 0})
    assert c is ctx
    return calls


def _clock():
    import time
    return time.monotonic()


# --------------------------------------------------------- sung or spoken

def _words(n, dur, gap, conf=0.98, text=None):
    out, t = [], 0.0
    vocab = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
             "kilo lima mike november oscar papa quebec romeo sierra tango "
             "uniform victor whiskey xray yankee zulu").split()
    toks = (text.split() if text else
            [a + b for a in vocab for b in ("", "s", "ed", "ing", "er", "ly")])
    for k in range(n):
        out.append({"w": toks[k % len(toks)],
                    "t0": round(t, 2), "t1": round(t + dur, 2),
                    "confidence": conf})
        t += dur + gap
    return out


def test_held_words_are_sung_and_short_words_are_spoken():
    assert fcv.sung({"words": _words(34, 1.05, 0.1, 0.88)})      # u5225
    assert not fcv.sung({"words": _words(170, 0.26, 0.15)})      # phone
    # a slow narration: short words, long pauses (0.9 words/s)
    assert not fcv.sung({"words": _words(40, 0.28, 0.85)})
    assert fcv.spoken_slowly({"words": _words(40, 0.28, 0.85)})
    # medium-long words transcribed unsure
    assert fcv.sung({"words": _words(30, 0.4, 0.2, 0.8)})
    assert not fcv.sung({"words": _words(30, 0.4, 0.2, 0.97)})


def test_a_repeating_chorus_is_sung():
    chorus = "we are the light tonight " * 6
    words = _words(30, 0.25, 0.1, text=chorus)
    assert fcv.voice_profile({"words": words})["repeat"] >= 0.3
    assert fcv.sung({"words": words})


def test_a_slow_narration_over_music_is_cut_not_kept_whole():
    """C2's rule kept any sub-60 s piece over a beat under 1.2 words/s
    whole and uncaptioned; Q1 keeps only a SUNG one whole."""
    narration = {"words": _words(40, 0.28, 0.85),
                 "perception": {"bpm": 96, "beats": [0.5, 1.1, 1.7]}}
    duration = 50.0
    assert first_cut._music_piece(narration, duration) is True       # C2
    assert first_cut._music_piece(narration, duration, robust=True) is False
    song = {"words": _words(34, 1.05, 0.1, 0.88),
            "perception": {"bpm": 120, "beats": [0.5, 1.0]}}
    assert first_cut._music_piece(song, 39.0, robust=True) is True
    garbage = {"words": [{"w": "ʰʰʰʰ", "t0": k * 0.1, "t1": k * 0.1 + 0.05}
                         for k in range(40)],
               "perception": {"bpm": 83, "beats": [1.0]}}
    assert first_cut._music_piece(garbage, 32.0, robust=True) is True


# ---------------------------------------------------------------- cut-ins

def test_cut_ins_alternate_on_phrase_turns_and_skip_cutaways():
    points = [round(0.5 + 0.7 * k, 2) for k in range(50)]
    busy = [(10.0, 12.0), (20.0, 22.1)]
    segs = fcv.plan_cut_ins(points, busy, 3.0, 30.0)
    assert segs and segs[0][0] == 3.0 and segs[0][2] is True
    for a, b, _c in segs:
        assert all(not (a < y and b > x) for x, y in busy)
        assert b - a <= fcv.CUT_IN_MAX_S + 1e-6
    closes = [s[2] for s in segs]
    assert all(x != y for x, y in zip(closes, closes[1:]))
    inner = [a for a, _b, _c in segs if a not in (3.0, 12.0, 22.1)]
    assert all(a in points for a in inner)
    # nothing identical holds long: every visible stretch is split
    assert max(b - a for a, b, _c in segs) <= fcv.CUT_IN_MAX_S


def test_phrase_points_are_pauses_and_clause_ends():
    words = [{"w": "One", "t0": 0.0, "t1": 0.3}, {"w": "two,", "t0": 0.32, "t1": 0.6},
             {"w": "three", "t0": 0.62, "t1": 0.9}, {"w": "four", "t0": 1.2, "t1": 1.4}]
    assert fcv.phrase_points(words) == [0.6, 1.18]


def test_the_close_camera_is_pinned_on_the_chin():
    """Pinned at the chin, the closer camera grows the face upward, never
    down into the chest band (title, slams, captions)."""
    idx = _talk_index()
    assert fcv.face_aim(idx) == (0.5, 0.45)
    # under a band crop the face's row is mapped into the live picture
    cx, cy = fcv.face_aim(idx, bands=[0.15, 0.85])
    assert abs(cy - (0.45 - 0.15) / 0.7) < 0.01
    assert fcv.face_aim({"spatial": {"samples": [{"faces": []}]}}) is None


# ---------------------------------------------------------------- b-roll

def test_broll_windows_land_after_the_hook_apart_and_clear_of_the_end():
    tl = Timeline([[0.0, 30.0]], [], None)
    moments = [(t, t + 0.3, "sun", "the sun") for t in
               (1.0, 3.5, 4.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0, 28.5)]
    wins = fcv.broll_windows(moments, tl, 30.0, 3.0)
    starts = [w[0] for w in wins]
    assert starts[0] >= 3.15
    for (a0, b0, *_), (a1, *_r) in zip(wins, wins[1:]):
        assert a1 >= b0 + fcv.BROLL_GAP_S - 1e-6
    assert all(b <= 30.0 - fcv.BROLL_TAIL_S + 1e-6 for _a, b, *_ in wins)
    assert sum(b - a for a, b, *_ in wins) <= fcv.BROLL_SHARE * 30.0 + 1e-6
    assert 4 <= len(wins) <= 8
    # a cutaway shortens (never under BROLL_MIN_S) to let the next one in
    assert all(b - a >= fcv.BROLL_MIN_S - 1e-6 for a, b, *_ in wins)


def test_broll_moments_are_the_speakers_own_words():
    idx = _talk_index()
    by_id = {s["id"]: s for s in idx["sentences"]}
    got = fcv.broll_spans(idx, by_id, [
        {"id": "s1", "word": "sun", "query": "the sun surface"},
        {"id": "s1", "word": "solar panel", "query": "solar panels"},
        {"id": "s2", "word": "factory", "query": ""},
        "nonsense"])
    assert [(g[2], g[3]) for g in got] == [("sun", "the sun surface")]


def test_the_scout_asks_for_broll_only_for_the_new_engine():
    q1 = first_cut._with_broll(first_cut.TRIM_SYSTEM)
    assert "BROLL:" in q1 and '"broll": [' in q1 and q1.rstrip().endswith("}.")
    assert "BROLL" not in first_cut.TRIM_SYSTEM
    assert "BROLL:" in first_cut._with_broll(first_cut.SCOUT_SYSTEM)


# ---------------------------------------------------------------- screen

def test_the_screen_window_is_2_to_3x_and_inside_the_frame():
    win, z = fcv.window_for([0.3, 0.2, 0.45, 0.26], 1.6)
    assert abs(z - fcv.SCREEN_ZOOM) < 1e-6
    assert 0 <= win[0] < win[2] <= 1 and 0 <= win[1] < win[3] <= 1
    assert abs((win[2] - win[0]) - 1 / z) < 1e-3
    assert win[0] <= 0.3 and win[2] >= 0.45
    big, zb = fcv.window_for([0.1, 0.1, 0.8, 0.7], 1.6)
    assert zb == fcv.SCREEN_ZOOM_MIN


def test_screen_keyframes_start_wide_push_in_and_travel_eased():
    t1, _ = fcv.window_for([0.3, 0.1, 0.5, 0.2], 1.6)
    t2, _ = fcv.window_for([0.5, 0.6, 0.7, 0.7], 1.6)
    keys = fcv.screen_keyframes([(0.5, t1, 2.2), (2.0, t1, 2.2),
                                 (6.0, t2, 2.2), (6.5, t1, 2.2)], 12.0, 3.0)
    ts = [k["t"] for k in keys]
    assert ts == sorted(ts) and ts[0] == 0.0 and ts[-1] == 12.0
    assert keys[0]["strength"] == 0.0 and keys[2]["strength"] > 1.0
    assert sum(1 for k in keys if k["rect"] == t2) >= 1
    # the 6.5 s region came too soon after the move: held
    assert keys[-1]["rect"] == t2


def test_page_changes_are_whole_screen_changes_only():
    f = np.zeros((6, 40, 64), np.uint8)
    f[2:] = 200                                   # a new page at frame 2
    f[4:, 10:30, 20:40] = 50                      # a local change at 4
    assert fcv.page_changes([k * 0.25 for k in range(6)], f) == [0.5]


def test_the_text_column_skips_the_sidebar():
    samples = [{"text": [[0.01, 0.1 + k * 0.05, 0.12, 0.13 + k * 0.05]
                         for k in range(8)]
                + [[0.27, 0.1 + k * 0.05, 0.89, 0.13 + k * 0.05]
                   for k in range(8)]}]
    col = fcv.text_column({"spatial": {"samples": samples}})
    assert col[0] >= 0.25 and col[1] <= 0.9


# ---------------------------------------------------------------- explainer

def test_the_action_box_is_where_the_colour_and_detail_are():
    frames = np.full((3, 45, 80, 3), 235, np.uint8)        # snow
    frames[:, 18:30, 52:64] = (200, 70, 20)                # an orange pipe
    frames[2, 18:30, 54:66] = (200, 70, 20)
    boxes = fcv.action_boxes(frames)
    for (x0, x1), pipe in zip(boxes, (0.725, 0.725, 0.75)):
        assert abs((x0 + x1) / 2.0 - pipe) < 0.03
        assert x0 < pipe - 0.07 and x1 > pipe + 0.07


def test_an_explainer_ends_on_its_own_conclusion():
    rows = [{"id": f"s{k}", "t0": 4.0 * k, "t1": 4.0 * k + 3.0,
             "text": "The liner is pulled into the pipe."} for k in range(12)]
    rows[11]["text"] = "PrimusLine"                       # a 1-word tag
    rows[10]["text"] = "Safe, reliable, sustainable."
    got = fcv.conclusion_end(rows, rows[1], rows[5], first_cut._ends_thought)
    assert got["id"] == "s10"
    # too much speech to the conclusion: the scout's end stands
    long_rows = [dict(r, t0=8.0 * k, t1=8.0 * k + 7.5)
                 for k, r in enumerate(rows)]
    got = fcv.conclusion_end(long_rows, long_rows[0], long_rows[3],
                             first_cut._ends_thought)
    assert got["id"] == "s3"


def test_off_topic_cutaways_are_removed_after_one_look(monkeypatch, tmp_path):
    import claude_lane
    import media_cache
    import subprocess as sp
    ctx = _EdlCtx(_talk_index())
    ctx.first_cut_model = "claude-haiku-5-5"
    ctx.workdir = str(tmp_path)
    ctx.edl["fx"] = [
        {"id": "br1", "type": "broll", "start": 4.0, "end": 6.0,
         "media": "still", "source": {"asset_key": "a/1.jpg"}},
        {"id": "br2", "type": "broll", "start": 9.0, "end": 11.0,
         "media": "still", "source": {"asset_key": "a/2.jpg"}}]
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(media_cache, "lease",
                        lambda key, d, name: str(tmp_path / name))
    monkeypatch.setattr(sp, "run", lambda *a, **k: NS(stdout=b"\xff\xd8jpg"))
    seen = {}

    def create(model, messages, **kw):
        seen["n"] = sum(1 for p in messages[1]["content"]
                        if p["type"] == "image_url")
        return NS(choices=[NS(message=NS(content='{"drop": [2]}'))],
                  usage=NS(provider_cost_usd=0.002))
    monkeypatch.setattr(claude_lane, "create", create)
    calls = []

    def run(c, steps, name, args, started):
        calls.append((name, args))
        c.edl["fx"] = [f for f in c.edl["fx"] if f["id"] not in args["ids"]]
        return "EDL v3"
    spend = {"usd": 0.0}
    left = fcv.verify_broll(ctx, [], 0.0, run, spend)
    assert seen["n"] == 2 and calls == [("remove_broll", {"ids": ["br2"]})]
    assert left == [(4.0, 6.0)] and spend["usd"] == 0.002
    # without the lane every cutaway stands and no model is called
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    ctx.edl["fx"].append({"id": "br3", "type": "broll", "start": 12.0,
                          "end": 14.0, "source": {"asset_key": "a/3.jpg"}})
    assert fcv.verify_broll(ctx, [], 0.0, run, spend) == [(4.0, 6.0), (12.0, 14.0)]


def test_a_screen_ends_live_with_the_runon_muted():
    idx = _talk_index(20.0)
    ctx = _EdlCtx(idx)
    last = [w for w in idx["words"] if w["t0"] < 12.0][-1]
    ctx.edl["keep"] = [[0.0, last["t1"]]]
    calls = []

    def run(c, steps, name, args, started):
        calls.append((name, args))
        if name == "keep_segments":
            c.edl["keep"] = args["segments"]
        return "EDL v2"
    fcv.muted_tail(ctx, [], 0.0, run)
    names = [n for n, _a in calls]
    assert names == ["keep_segments", "set_volume"]
    assert abs(calls[0][1]["segments"][-1][1] - (last["t1"] + fcv.TAIL_S)) < 1e-3
    nxt = [w for w in idx["words"] if w["t0"] >= last["t1"] - 0.01][0]
    assert calls[1][1]["gain_db"] <= -40
    assert last["t1"] - 0.02 <= calls[1][1]["start"] <= nxt["t0"]
    # the run-on word is dropped from the captions once they exist
    assert [f["from"] for f in ctx.first_cut_tail_words][0] == nxt["w"]
    calls.clear()
    fcv.drop_tail_words(ctx, [], 0.0, run)
    assert calls[0][0] == "set_caption_fixes" and \
        calls[0][1]["replacements"][0]["to"] == ""
    # a cut that already ends live is left alone
    calls.clear()
    fin = idx["words"][-1]
    ctx.edl["keep"] = [[0.0, fin["t1"] + 0.35]]
    assert fcv.muted_tail(ctx, [], 0.0, run) is None and not calls


def test_screen_captions_keep_the_c2_look():
    style = {"preset": "reels", "motion_look": "editorial", "size_scale": 1.45}
    got, n = fcv.caption_style(style, talking=False, screen=True, vertical=False)
    assert got == style and n == 2
    got, n = fcv.caption_style(style, talking=True, screen=False, vertical=True)
    assert got["motion_look"] == "serif" and got["single_line"] and n == 3


def test_the_band_crop_spans_the_whole_source_before_any_cut(monkeypatch):
    """Written before the trim, the band crop's zoom covers every source
    second, so a later request that restores footage keeps it (trial phone:
    bars on the first frame and the last 0.5 s)."""
    order = []

    def deband(ctx, steps, started):
        order.append(("deband", [list(k) for k in ctx.edl["keep"]]))
        return "EDL v2 ok", [0.15, 0.85]
    monkeypatch.setattr(first_cut, "deband", deband)
    ctx = _EdlCtx(_talk_index())
    ctx.first_cut_q1 = True

    def execute(c, name, args):
        order.append((name, None))
        c.versions_written.append(1)
        if name == "keep_segments":
            c.edl["keep"] = args["segments"]
        return "EDL v2 ok"
    monkeypatch.setattr(agent_tools, "execute", execute)

    def trim(c, worker_db, spend):
        c.first_cut_hook = "The *sun* gives"
        return 5.0, 30.0, "rules"
    monkeypatch.setattr(first_cut, "trim_range", trim)
    first_cut.video_chain(ctx, None, _clock(), {"usd": 0})
    names = [n for n, _k in order]
    assert names.index("deband") < names.index("keep_segments")
    assert order[names.index("deband")][1] == [[0.0, ctx.duration]]
    assert names.count("deband") == 1


def test_the_band_crop_is_relaid_over_the_whole_program():
    ctx = _EdlCtx(_talk_index())
    ctx.edl["keep"] = [[0.0, 20.0]]
    ctx.edl["effects"] = {"zooms": [
        {"id": "zm1", "start": 0.0, "end": 19.6, "strength": 0.43,
         "cx": 0.5, "cy": 0.2, "purpose": fcv.BAND_PURPOSE + " (rows 0.15-0.85)"},
        {"id": "zm2", "start": 3.0, "end": 5.0, "strength": 0.3}]}
    calls = []

    def run(c, steps, name, args, started):
        calls.append((name, args))
        return "EDL v3"
    assert fcv.refresh_band_crop(ctx, [], 0.0, run) is True
    assert calls[0] == ("remove_zoom", {"id": "zm1"})
    assert calls[1][0] == "add_zoom" and calls[1][1]["start"] == 0.0 \
        and calls[1][1]["end"] == 20.0 and calls[1][1]["strength"] == 0.43
    # already whole: nothing to do
    ctx.edl["effects"]["zooms"][0]["end"] = 20.0
    calls.clear()
    assert fcv.refresh_band_crop(ctx, [], 0.0, run) is False and not calls
