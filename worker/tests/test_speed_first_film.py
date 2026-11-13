"""SPEED (14 Nov 2026): a trial's first film reaches its customer fast.

The first live Director film (p5755) took about 6 minutes from upload: index 89 s (31 s of it the visual
storyboard, which no First cut reads), the film 97 s, then 77 s of render, the last look on that preview, and
64 s more of render for the three labels the look removed. Here: the project is ready before the storyboard,
and the look reads a low-resolution proof of its six moments beside the film's one full render.
"""
from types import SimpleNamespace as NS

import agent_tools
import config
import db as dbx
import first_cut
import indexer
from score import film, taste


def test_the_proof_maps_each_look_moment_into_its_reel():
    segs = [{"start": 1.6, "end": 2.4, "reel_start": 0.0}, {"start": 9.6, "end": 10.4, "reel_start": 0.8},
            {"start": 19.6, "end": 21.0, "reel_start": 1.6}]
    at = first_cut._proof_times([2.0, 10.0, 20.0, 30.0], segs)
    assert at == {2.0: 0.4, 10.0: 1.2, 20.0: 2.0}           # 30 s is not in the reel: that pair is not drawn


def test_a_moment_on_a_window_edge_stays_inside_its_own_window():
    at = first_cut._proof_times([1.6, 2.4], [{"start": 1.6, "end": 2.4, "reel_start": 5.0}])
    assert 5.0 < at[1.6] < 5.8 and 5.0 < at[2.4] < 5.8


def _l1(dur_s=30.0, fps=30.0):
    return {"fps": fps, "dur_f": int(dur_s * fps), "items": [], "spine": [{"f": [0, int(dur_s * fps)], "src": [0.0, dur_s]}],
            "frame": [720, 1280]}


class _Db:
    def __init__(self, result=None, state="done"):
        self.calls, self.result, self.state = [], result, state

    def run(self, fn, *args):
        name = getattr(fn, "__name__", str(fn))
        self.calls.append((name, args))
        if fn is dbx.get_or_enqueue_preview_check_job:
            return 77, True
        if fn is dbx.get_job:
            return {"id": args[0], "state": self.state, "result": self.result}
        if fn is dbx.get_asset:
            return {"id": args[0], "storage_key": "media/1/proof.mp4", "meta": {}}
        return None


def _ctx():
    return NS(project_id=1, job={"id": 5, "user_id": 3, "type": "agent_turn", "payload": {}}, project={},
              director_state={"comp": NS(l1=_l1())}, first_cut_phases={},
              latest_edl=lambda: {"version": 2, "json": {"keep": [[0.0, 30.0]]}})


def _segments(times):
    out, reel = [], 0.0
    for t in times:
        out.append({"start": t - 0.4, "end": t + 0.4, "reel_start": reel})
        reel += 0.8
    return out


def test_the_look_reads_a_proof_beside_the_one_full_render(monkeypatch):
    times = taste.look_times(_l1())
    order, seen = [], {}
    monkeypatch.setattr(agent_tools, "render_preview", lambda ctx, **k: order.append(("approval", k)) or "queued")
    import storage
    monkeypatch.setattr(storage, "presign_get", lambda key, expires=0: f"https://signed/{key}")

    def look(ctx, db, job, started, spend, scope, proof=None):
        order.append(("look",))
        seen["proof"] = proof
        return None
    monkeypatch.setattr(film, "last_look", look)
    db = _Db({"render_asset_id": 41, "proof_segments": _segments(times)})
    ctx = _ctx()
    got = first_cut._director_early_look(ctx, db, ctx.job, 0.0, {"usd": 0.0}, None)
    assert got == ("looked", None)
    assert order[0] == ("approval", {"quality": "approval", "_wait_timeout_s": 0})     # the full render starts first
    assert order[-1] == ("look",)
    payload = next(a for n, a in db.calls if n == "get_or_enqueue_preview_check_job")[2]
    assert payload["check_pages"] == [payload["check_ranges"]] and len(payload["check_ranges"]) == len(times)
    assert all(round(b - a, 3) <= 0.8 for a, b in payload["check_ranges"])
    assert payload["proof_budget_s"] == config.DIRECTOR_PROOF_BUDGET_S
    assert payload["audio_model_review"] is False and "quality" not in payload            # draft: the proxy, 480p
    url, at = seen["proof"]
    assert url == "https://signed/media/1/proof.mp4" and set(at) == set(times)
    assert ctx.first_cut_phases["early_look"]["proof_job"] == 77


def test_a_late_or_failed_proof_leaves_the_look_after_the_render(monkeypatch):
    monkeypatch.setattr(agent_tools, "render_preview", lambda ctx, **k: "queued")
    monkeypatch.setattr(film, "last_look", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no look")))
    ctx = _ctx()
    assert first_cut._director_early_look(ctx, _Db(None, state="failed"), ctx.job, 0.0, {}, None) is None
    assert "skipped" in ctx.first_cut_phases["early_look"]
    monkeypatch.setattr(config, "DIRECTOR_PROOF_WAIT_S", 0.0)
    ctx = _ctx()
    assert first_cut._director_early_look(ctx, _Db(None, state="running"), ctx.job, 0.0, {}, None) is None


def test_a_proof_holding_under_three_moments_is_not_read(monkeypatch):
    monkeypatch.setattr(agent_tools, "render_preview", lambda ctx, **k: "queued")
    monkeypatch.setattr(film, "last_look", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no look")))
    times = taste.look_times(_l1())
    ctx = _ctx()
    db = _Db({"render_asset_id": 41, "proof_segments": _segments(times[:2])})
    assert first_cut._director_early_look(ctx, db, ctx.job, 0.0, {}, None) is None


def test_the_switch_off_or_no_film_runs_nothing(monkeypatch):
    monkeypatch.setattr(agent_tools, "render_preview", lambda ctx, **k: (_ for _ in ()).throw(AssertionError("queued")))
    monkeypatch.setattr(config, "DIRECTOR_PROOF_LOOK", False)
    ctx = _ctx()
    assert first_cut._director_early_look(ctx, _Db(), ctx.job, 0.0, {}, None) is None
    monkeypatch.setattr(config, "DIRECTOR_PROOF_LOOK", True)
    ctx.director_state = None
    assert first_cut._director_early_look(ctx, _Db(), ctx.job, 0.0, {}, None) is None


# ------------------------------------------------------------------ the seam in first_cut.run

def _run(monkeypatch, tmp_path, early, renders):
    from tests.test_score_film import _run as film_run
    import claude_trial
    calls = {"render": 0, "look": 0}

    def attempt(ctx, *a):
        ctx.versions_written.append(2)
        return [{"tool": "director", "ok": True}], ["12 shots"], {"how": "director"}
    monkeypatch.setattr(first_cut, "_director_early_look", lambda *a: early)

    def post_look(*a):
        calls["look"] += 1
        return None
    monkeypatch.setattr(first_cut, "_director_look", post_look)
    real = list(renders)

    def counting(c, steps, picked):
        calls["render"] += 1
        ok = real.pop(0)
        c.last_preview = {"edl_version": 1, "duration_s": 5.0} if ok else None
        return "Preview ready" if ok else "FAILED"
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    monkeypatch.setattr(first_cut, "_render_cut", counting)
    result = film_run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope)
    return result, calls


def test_a_film_the_proof_look_read_renders_once_and_is_not_looked_at_again(monkeypatch, tmp_path):
    (result, *_), calls = _run(monkeypatch, tmp_path, ("looked", None), [True])
    assert calls == {"render": 1, "look": 0}
    assert result["outcome"] == "fulfilled" and result["first_cut"]["picked"] == {"how": "director"}


def test_a_film_the_proof_look_cut_renders_only_the_cut(monkeypatch, tmp_path):
    (result, *_), calls = _run(monkeypatch, tmp_path, ("looked", ["10 shots"]), [True])
    assert calls == {"render": 1, "look": 0}
    assert result["first_cut"]["picked"]["look"] is True


def test_without_the_proof_the_look_follows_the_render_as_before(monkeypatch, tmp_path):
    (result, *_), calls = _run(monkeypatch, tmp_path, None, [True])
    assert calls == {"render": 1, "look": 1}


def test_a_film_the_proof_look_took_back_goes_to_the_chain_without_rendering_the_upload(monkeypatch, tmp_path):
    fell = []
    monkeypatch.setattr(film, "fell", lambda ctx, db, job, stage, reason, usd=0.0, start_edl=None: fell.append(stage))

    def early(ctx, *a):
        ctx.director_fell_why = "the last look: 5 of 6 frames for the upload"
        ctx.director_version = None
        return ("looked", None)
    from tests.test_score_film import _run as film_run
    import claude_trial
    renders, chain = [], []

    def attempt(ctx, *a):
        ctx.versions_written.append(2)
        return [{"tool": "director", "ok": True}], ["12 shots"], {"how": "director"}
    monkeypatch.setattr(first_cut, "_director_early_look", early)

    def render(c, steps, picked):
        renders.append(picked)
        c.last_preview = {"edl_version": 1, "duration_s": 5.0}
        return "Preview ready"
    monkeypatch.setattr(first_cut, "_render_cut", render)
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    result, said, reads, chain_args = film_run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope)
    assert fell == ["render"] and chain_args                    # filed (as the look), then the chain
    assert renders == [{"how": "best_moment"}]                    # one render: the chain's cut, never the upload
    assert result["first_cut"]["picked"] == {"how": "best_moment"}


# ------------------------------------------------------------------ the index: ready before the storyboard

class _IdxDb:
    def __init__(self, admission, pending=None):
        self.admission, self.pending = admission, pending

    def run(self, fn, *args):
        if fn is first_cut.project_admission:
            return self.admission
        if fn is dbx.pending_user_message:
            return self.pending
        raise AssertionError(fn)


def test_a_first_cut_upload_is_ready_before_its_storyboard():
    job = {"payload": {}}
    assert indexer.storyboard_after_ready(_IdxDb({"first_cut": True}), job, 1, 9) is True


def test_a_waiting_request_a_reindex_or_no_first_cut_keeps_todays_order(monkeypatch):
    assert indexer.storyboard_after_ready(_IdxDb({"first_cut": True}, pending={"id": 4}), {"payload": {}}, 1, 9) is False
    assert indexer.storyboard_after_ready(_IdxDb({"first_cut": True}), {"payload": {"reindex": True}}, 1, 9) is False
    assert indexer.storyboard_after_ready(_IdxDb(None), {"payload": {}}, 1, 9) is False
    assert indexer.storyboard_after_ready(_IdxDb({"kind": "trial"}), {"payload": {}}, 1, 9) is False
    assert indexer.storyboard_after_ready(_IdxDb({"first_cut": True}), {"payload": {}}, 1, None) is False
    monkeypatch.setattr(config, "STORYBOARD_AFTER_READY", False)
    assert indexer.storyboard_after_ready(_IdxDb({"first_cut": True}), {"payload": {}}, 1, 9) is False


def test_a_failing_check_keeps_todays_order():
    class Broken:
        def run(self, fn, *a):
            raise RuntimeError("db down")
    assert indexer.storyboard_after_ready(Broken(), {"payload": {}}, 1, 9) is False


def test_the_storyboard_merges_only_its_own_keys():
    seen = []

    class Cur:
        rowcount = 1

        def execute(self, sql, args):
            seen.append((sql, args))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cur()
    assert dbx.set_index_storyboard(Conn(), "abc", {"version": 3}, None) == 1
    sql, args = seen[0]
    assert "UPDATE indexes" in sql and "'{visual_storyboard}'" in sql and "INSERT" not in sql
    assert args[-1] == "abc"


def test_a_screen_shaped_upload_keeps_its_storyboard_before_ready():
    screen = {"samples": [{"dense_ui": True, "faces": []} for _ in range(10)]}
    talking = {"samples": [{"text": [], "faces": [[0, 0, 1, 1]]} for _ in range(10)]}
    db = _IdxDb({"first_cut": True})
    assert indexer.storyboard_after_ready(db, {"payload": {}}, 1, 9, spatial=screen) is False
    assert indexer.storyboard_after_ready(db, {"payload": {}}, 1, 9, spatial=talking) is True
    assert first_cut.dense_ui({"spatial": screen}) and not first_cut.dense_ui({"spatial": talking})
    assert not first_cut._screen_like({"spatial": screen})       # no storyboard: never a screen, as before


def test_a_proof_window_rebases_the_beds_drop_or_leaves_it_out():
    import stitch
    drop = {"id": "bed.drop", "type": "arrange", "stage": "music_bus", "kind": "hole", "music_id": "bed",
            "start": 5.58, "end": 6.4, "fade_ms": 90.0}
    record = {"id": "bed.fit", "type": "arrange", "stage": "music_bus", "kind": "fit_intent", "music_id": "bed"}
    assert stitch._window_fx([drop, record], 0.63, 1.43, None, {"bed"}) == [record]       # the drop is after this piece
    got = stitch._window_fx([drop], 5.0, 6.0, None, {"bed"})
    assert got == [dict(drop, start=0.58, end=1.0)]                                      # clipped to the piece's clock
    assert stitch._window_fx([drop], 5.0, 6.0, None, set()) == []                         # no music, no bus item
