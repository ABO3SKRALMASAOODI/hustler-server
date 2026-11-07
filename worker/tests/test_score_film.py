"""The Director's First cut on today's renderer (worker/score): the switch, the seam in first_cut.run,
the fall to the chain, the spend fence, the burn lowering, and the write path's checks."""
import json
import os
import time
from types import SimpleNamespace as NS

import pytest

import agent_tools
import claude_trial
import config
import db as dbx
import first_cut
from score import compiler, film, flags, lower, perceive
from tests.test_score_compiler import talk_index


# ------------------------------------------------------------------ the switch

def test_a_bad_row_is_off():
    for raw in (None, "", "{", "[]", '"on"', '{"mode": "maybe"}', '{"mode": "on", "percent": "x"}',
                '{"mode": "on", "models": {"direct": "gpt-6-luna"}}'):
        assert flags.parse(raw)["mode"] == "off", raw
    assert flags.parse('{"mode": "on"}')["mode"] == "on"


def test_the_row_is_read_per_job_and_an_unreadable_one_is_off():
    class Boom:
        def run(self, *a):
            raise RuntimeError("no database")
    assert flags.read(Boom())["mode"] == "off"


def _admit(cfg, **kw):
    base = dict(trial_active=True, subscribed=False, engine_on=True, source_s=30.0, url=None)
    base.update(kw)
    return flags.admit(cfg, 5, **base)


def test_only_new_trial_accounts_with_a_short_upload_enter():
    on = flags.parse('{"mode": "on"}')
    assert _admit(on) == (True, "")
    assert _admit(on, subscribed=True) == (False, "subscribed")       # never a subscriber, grandfathered or new
    assert _admit(on, trial_active=False) == (False, "not_trial")
    assert _admit(on, engine_on=False) == (False, "account_rule")
    assert _admit(on, url="https://x.test") == (False, "url")
    assert _admit(on, source_s=181.0) == (False, "source_length")
    assert _admit(flags.parse('{"mode": "off"}')) == (False, "off")


def test_a_named_test_account_enters_while_the_switch_is_off_but_never_as_a_subscriber():
    cfg = flags.parse('{"mode": "off", "test_user_ids": [5]}')
    assert _admit(cfg) == (True, "")
    assert _admit(cfg, subscribed=True) == (False, "subscribed")


def test_percent_splits_accounts_stably():
    cfg = flags.parse('{"mode": "on", "percent": 50}')
    got = [flags.admit(cfg, u, trial_active=True, subscribed=False, engine_on=True, source_s=20)[0] for u in range(400)]
    assert 140 < sum(got) < 260
    assert got == [flags.admit(cfg, u, trial_active=True, subscribed=False, engine_on=True, source_s=20)[0] for u in range(400)]


# ------------------------------------------------------------------ the seam

class _Ctx(NS):
    def latest_edl(self):
        return {"version": 1, "json": {"keep": [[0.0, 5.0]]}}


def _run(monkeypatch, tmp_path, flag_row, seam=None, film_attempt=None, scope=None, renders=None):
    said, reads, chain_args = [], [], []

    class Db:
        def run(self, fn, *args):
            reads.append(getattr(fn, "__name__", str(fn)))
            if fn is dbx.add_message:
                said.append(args[1:])
            if fn is dbx.user_billing:
                return False, "free", False
            if getattr(fn, "__name__", "") == "read_row":
                return flag_row
            return None

    ctx = _Ctx(duration=5.0, project_id=1, versions_written=[])
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(agent_tools, "ToolContext", lambda *a: ctx)
    decision = (claude_trial.Decision(claude_trial.Decision.ACTIVE, scope=scope) if scope
                else claude_trial.Decision(claude_trial.Decision.NOT_TRIAL))
    monkeypatch.setattr(claude_trial, "resolve", lambda *a, **k: decision)
    monkeypatch.setattr(claude_trial, "activate", lambda *a, **k: None)
    monkeypatch.setattr(claude_trial, "deactivate", lambda *a, **k: None)
    monkeypatch.setattr(claude_trial, "credit_status", lambda *a, **k: {})
    import first_cut_visual
    monkeypatch.setattr(first_cut_visual, "engine_on", lambda *a: True)

    def chain(c, db, started, spend, whole=False):
        chain_args.append((c is ctx, whole, dict(spend)))
        return [{"tool": "keep_segments", "ok": True}], ["its best moments"], {"how": "best_moment"}
    monkeypatch.setattr(first_cut, "video_chain", chain)
    if seam is not None:
        monkeypatch.setattr(first_cut, "_director_film", seam)
    if film_attempt is not None:
        monkeypatch.setattr(film, "attempt", film_attempt)
    if renders is not None:
        def render(c, steps, picked):
            ok = renders.pop(0)
            c.last_preview = {"edl_version": 1, "duration_s": 5.0} if ok else None
            return "Preview ready" if ok else "FAILED: the render stopped"
        monkeypatch.setattr(first_cut, "_render_cut", render)
    result = first_cut.run(Db(), {"id": 2, "user_id": 3, "project_id": 1, "payload": {}},
                           {"id": 1, "chat_session_id": 9}, {"json": {"video": {"duration": 5.0}}})
    result["first_cut"]["elapsed_s"] = 0
    return result, said, reads, chain_args


@pytest.mark.parametrize("row", [None, '{"mode": "off"}', "{not json", '{"mode": "on", "percent": 0}'])
def test_switch_off_leaves_first_cut_run_unchanged(monkeypatch, tmp_path, row):
    """Golden: with the switch off (absent, off, unreadable, or at 0 %), first_cut.run gives exactly what
    it gave with no Director at all: the same chain call, steps, reply and result."""
    golden = _run(monkeypatch, tmp_path, row, seam=lambda *a, **k: None)
    got = _run(monkeypatch, tmp_path, row)
    assert got[0] == golden[0]
    assert got[1] == golden[1]
    assert got[3] == golden[3] == [(True, False, {"usd": 0.0})]
    # the only extra thing the job did was read the switch
    extra = [r for r in got[2] if r not in golden[2]]
    assert set(extra) <= {"read_row"}


def test_a_trial_job_with_the_switch_on_gets_the_film_and_skips_the_chain(monkeypatch, tmp_path):
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    seen = {}

    def attempt(ctx, worker_db, job, started, spend, trial_scope, subscribed):
        seen.update(scope=trial_scope, subscribed=subscribed)
        ctx.versions_written.append(2)
        return [{"tool": "director", "ok": True}], ["12 shots", "captions"], {"how": "director"}
    result, said, reads, chain_args = _run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope, renders=[True])
    assert seen == {"scope": scope, "subscribed": False}
    assert chain_args == []                                       # the chain did not run
    assert result["first_cut"]["picked"] == {"how": "director"}
    assert result["outcome"] == "fulfilled"


def test_a_film_whose_preview_fails_falls_to_the_chain_in_the_same_job(monkeypatch, tmp_path):
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    fell = []
    monkeypatch.setattr(film, "fell", lambda ctx, db, job, stage, reason, usd=0.0, start_edl=None: fell.append((stage, start_edl)))

    def attempt(ctx, *a):
        ctx.versions_written.append(2)
        return [{"tool": "director", "ok": True}], ["12 shots"], {"how": "director"}
    result, said, reads, chain_args = _run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=attempt, scope=scope, renders=[False, True])
    assert fell == [("render", {"keep": [[0.0, 5.0]]})]           # back to the upload first
    assert chain_args and result["first_cut"]["picked"] == {"how": "best_moment"}
    assert result["outcome"] == "fulfilled"                       # the customer sees the chain's cut, no error


def test_a_film_that_falls_hands_the_same_job_to_the_chain(monkeypatch, tmp_path):
    scope = claude_trial.Scope(3, 0.34, 0.0, "claude-haiku-5-5", granted=True)
    result, said, reads, chain_args = _run(monkeypatch, tmp_path, '{"mode": "on"}', film_attempt=lambda *a: None, scope=scope)
    assert chain_args and result["first_cut"]["picked"] == {"how": "best_moment"}


# ------------------------------------------------------------------ the fall, the fence, the allowance

class _MeterDb:
    def __init__(self, today=0.0, cap=4.15):
        self.today, self.cap, self.events, self.allowance = today, cap, [], []

    def run(self, fn, *args):
        name = getattr(fn, "__name__", "")
        if name == "read_row":
            return '{"mode": "on"}'
        if name == "pool_totals":
            return {"pool": 10.0, "month": 10.0, "today": self.today, "grants_today": 1}
        if name == "_allowance_tx":
            self.allowance.append(round(args[1], 4))
            return None
        if fn is dbx.record_client_event:
            self.events.append((args[2], args[3]))
        return None


def _film_ctx(tmp_path):
    return _Ctx(duration=30.0, project_id=1, versions_written=[], workdir=str(tmp_path), first_cut_q1=True,
                first_cut_phases={}, index={})


def test_a_day_past_its_pacing_falls_to_the_chain_without_a_call(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_trial, "flag", lambda *a, **k: {"daily_usd_cap": 4.15, "pool_usd_cap": 140})
    db = _MeterDb(today=3.9)
    scope = claude_trial.Scope(3, 0.34, 0.0, "m", granted=True)
    monkeypatch.setattr(film, "_make", lambda *a: pytest.fail("no film past the day's cap"))
    assert film.attempt(_film_ctx(tmp_path), db, {"id": 1, "user_id": 3, "project_id": 1}, time.monotonic(), {"usd": 0}, scope, False) is None
    assert db.events == [("director_skipped", {"job_id": 1, "why": "daily_pacing"})]


def test_every_failure_is_filed_and_the_allowance_keeps_only_what_the_film_spent(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_trial, "flag", lambda *a, **k: {"daily_usd_cap": 4.15, "pool_usd_cap": 140})
    db = _MeterDb()
    scope = claude_trial.Scope(3, 0.34, 0.0, "m", granted=True)

    def make(*a):
        scope.spent_usd += 0.41                       # the Direct call was paid, then the answer held no film
        raise film.Fall("direct", "empty: the answer compiled to no beats")
    monkeypatch.setattr(film, "_make", make)
    spend = {"usd": 0.0}
    assert film.attempt(_film_ctx(tmp_path), db, {"id": 1, "user_id": 3, "project_id": 1}, time.monotonic(), spend, scope, False) is None
    kind, detail = db.events[-1]
    assert kind == "director_fell_back" and detail["stage"] == "direct" and detail["usd"] == 0.41
    assert db.allowance == [0.7, -0.29]               # reserve, then give back what was not spent
    assert round(scope.cap_usd, 2) == 0.75            # the trial's own $0.34 stays whole for its follow-ups
    assert round(scope.cap_usd - scope.spent_usd, 2) == 0.34
    assert spend["usd"] == pytest.approx(0.41)


def test_an_unexpected_error_falls_too(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_trial, "flag", lambda *a, **k: {"daily_usd_cap": 4.15, "pool_usd_cap": 140})
    db = _MeterDb()
    monkeypatch.setattr(film, "_make", lambda *a: 1 / 0)
    assert film.attempt(_film_ctx(tmp_path), db, {"id": 1, "user_id": 3, "project_id": 1}, time.monotonic(), {"usd": 0},
                        claude_trial.Scope(3, 0.34, 0.0, "m", granted=True), False) is None
    assert db.events[-1][0] == "director_fell_back"


# ------------------------------------------------------------------ the burn, at the root

FILM = {"k": "film", "v": 1, "fps": 30, "frame": "9:16", "spine": "main", "band": "P2", "seed": 7}


def _lowered(tmp_path, beats):
    idx = talk_index(20.0)
    txt = "\n".join(json.dumps(x) for x in [FILM] + beats) + "\n"
    comp = compiler.compile_score(txt, index=idx)
    return lower.lower(comp.l1, str(tmp_path))


def test_a_burn_is_light_never_an_opaque_white_box_or_white_frames(tmp_path):
    """p5374 today: burns lowered to white_hit at full strength drew opaque white boxes over the type and
    full-white frames (rated broken_render)."""
    beats = [{"k": "b", "id": "b01", "w": "0-20", "job": "j", "cut": "pulse 1", "pic": "main",
              "do": [{"g": "burn", "at": "w3"}, {"g": "burn", "at": "w9", "rect": [0.62, 0.28, 0.86, 0.62]},
                     {"g": "flash", "mode": "burn", "at": "w14"}]}]
    edl, locals_, rep = _lowered(tmp_path, beats)
    hits = [f for f in edl["fx"] if f.get("kind") == "white_hit"]
    assert hits and all(f["intensity"] <= lower.BURN_PEAK and f.get("region") is None for f in hits)
    assert all(round((f["end"] - f["start"]) * 30) <= 1 for f in hits)        # one frame of peak, then it drains
    glows = [o for o in edl["overlays"] if o["id"].startswith("gl.")]
    assert len(glows) == 1 and glows[0]["z_index"] < 4                          # under the type stills
    assert max(k["v"] for k in glows[0]["opacity"]) <= lower.GLOW_GAIN
    assert rep["burns_as_glow"] == 1 and rep["burns_as_light"] == 2


def test_a_two_frame_white_flash_lowers_to_a_flash_validate_edl_accepts(tmp_path):
    """Every first product replay fell at the write: a 2-frame white flash lowered to a 0.07 s vector."""
    import schemas
    beats = [{"k": "b", "id": "b01", "w": "0-20", "job": "j", "cut": "pulse 1", "pic": "main",
              "do": [{"g": "flash", "at": "w4", "f": 2, "mode": "white"}, {"g": "flash", "at": "w9", "f": 2, "mode": "tint", "c": "#E11F79"}]}]
    edl, locals_, rep = _lowered(tmp_path, beats)
    schemas.validate_edl(edl, 20.0)
    assert not edl["vectors"] and rep["fills_as_flash"] == 2
    kinds = sorted(f["kind"] for f in edl["fx"] if f["id"].startswith("fl."))
    assert kinds == ["colour_hit", "white_hit"]


def test_a_lowered_film_passes_validate_edl(tmp_path):
    import schemas
    beats = [{"k": "b", "id": "b01", "w": "0-12", "job": "j", "cut": "pulse 0.8", "pic": "main", "type": "0-2|3-5|6-8",
              "do": [{"g": "big", "t": "HELLO", "at": "w2"}, {"g": "counter", "from": 1, "to": 9, "at": "w4..w8"}, {"g": "neg_card", "at": "w7"}]},
             {"k": "b", "id": "b02", "w": "13-30", "job": "k", "cut": "pulse 1.2", "pic": [{"plate": "#C2080D"}, "main"],
              "do": [{"g": "chip", "t": "SHOP", "at": "w13..w20"}], "snd": [{"s": "whoosh_soft_1", "at": "w13", "db": -16}]}]
    edl, locals_, rep = _lowered(tmp_path, beats)
    model = schemas.validate_edl(edl, 20.0)
    assert model.keep and edl["texts"] and edl["captions"]
    assert all(s["storage_key"].startswith("sound:") for s in edl["sfx"])
    assert not edl.get("music")                                                 # Valmera never picks music
    assert all(os.path.exists(p) for k, p in locals_.items() if k.startswith("today/"))


def test_the_menu_offers_only_what_today_draws():
    menu = compiler.gesture_menu()
    names = {ln.split(" ", 1)[0] for ln in menu.splitlines()}
    assert names == set(compiler.TODAY_GESTURES)
    assert not names & {"whip", "slide", "shred", "shatter", "recolour", "hud", "spot", "pin", "freeze", "stutter"}
    for gone in (" reflect", " fill", " behind"):
        assert all(gone not in ln.split(". ", 1)[0] for ln in menu.splitlines() if ln.split(" ", 1)[0] in ("big", "line", "card"))


def test_the_prefix_is_one_fixed_file_with_the_live_menu_under_its_cap():
    from score import brief
    text = brief.prefix_text()
    assert text == brief.prefix_text()
    assert compiler.gesture_menu() in text                     # rebake director/first-result/bake_assets.py when gestures change
    assert len(text) <= 27209       # the slice's prefix: 27,209 characters measured 11,470 tokens, under the 11,500 cap
    for engine_only in ('"behind":true', "cut:paper", '"g":"whip"', "`glow`"):
        assert engine_only not in text


# ------------------------------------------------------------------ perception, signal only

def test_printed_text_is_a_region_that_stays_put():
    rows = [{"t": float(t), "text": [[0.7, 0.15, 1.0, 0.26]] + ([[0.1, 0.5, 0.2, 0.55]] if t == 3 else [])} for t in range(10)]
    got = perceive.text_clusters({"samples": rows})
    assert [g["box"] for g in got] == [[0.7, 0.15, 1.0, 0.26]] and got[0]["seen"] == 10


# ------------------------------------------------------------------ the write path

def test_apply_score_runs_the_checks_and_skips_only_the_request_guards(monkeypatch):
    seen = []
    import counter_facts
    import followup_recipes
    import mono_policy
    import music_gate
    import request_contract
    import scope_guard
    monkeypatch.setattr(music_gate, "check_write", lambda *a: seen.append("music_gate"))
    monkeypatch.setattr(music_gate, "check_sfx_write", lambda *a: seen.append("check_sfx_write"))
    monkeypatch.setattr(mono_policy, "check_write", lambda *a: (seen.append("mono_policy"), (None, None))[1])
    monkeypatch.setattr(counter_facts, "candidates", lambda *a: seen.append("counter_facts") or False)
    monkeypatch.setattr(request_contract, "enabled", lambda *a: pytest.fail("request contract read on a film"))
    monkeypatch.setattr(scope_guard, "preservation_violations", lambda *a, **k: pytest.fail("scope guard on a film"))
    monkeypatch.setattr(followup_recipes, "check_write", lambda *a: pytest.fail("follow-up recipe on a film"))

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop()
    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx._states_seen = {}
    ctx.duration = 10.0
    ctx.user_message = "First cut"
    ctx.latest_edl = lambda: {"version": 1, "json": {"keep": [[0.0, 10.0]]}}
    monkeypatch.setattr(agent_tools.quality_gate, "advisory_findings", stop)
    with pytest.raises(Stop):
        ctx.apply_score({"keep": [[0.0, 5.0]]}, "film")
    assert seen == ["counter_facts", "music_gate", "check_sfx_write", "mono_policy"]
    assert ctx._score_write is False
    assert ctx.apply_score({"keep": [[0.0, 99.0]]}, "film").startswith("REJECTED")      # validate_edl still refuses


# ------------------------------------------------------------------ the call's deadline

class _Stream:
    """An SDK stream that thinks for `think` seconds, then writes."""
    def __init__(self, think, text="{}\n"):
        self.think, self.text = think, text
        self.current_message_snapshot = NS(usage=NS(input_tokens=1000, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        end = time.monotonic() + self.think
        while time.monotonic() < end and not self.closed:
            time.sleep(0.01)
        if not self.closed:
            yield self.text

    def get_final_message(self):
        return NS(stop_reason="end_turn", usage=NS(input_tokens=1000, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0))

    def close(self):
        self.closed = True


def _fake_lane(monkeypatch, st):
    import claude_lane
    import llm
    from score import call
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_trial, "guard_call", lambda: None)
    monkeypatch.setattr(claude_lane, "_sdk", lambda: NS(with_options=lambda **k: NS(messages=NS(stream=lambda **b: st))))
    recorded = []
    monkeypatch.setattr(llm, "record", lambda purpose, req, resp, usage=None: recorded.append((purpose, resp)))
    return call, recorded


def test_a_call_that_writes_nothing_by_its_first_text_mark_is_late_and_still_metered(monkeypatch):
    st = _Stream(think=5.0)
    call, recorded = _fake_lane(monkeypatch, st)
    t0 = time.monotonic()
    with pytest.raises(call.Late):
        call.stream("claude-opus-5-5", "s", [], max_tokens=100, effort="medium", purpose="director_direct",
                    deadline=t0 + 3.0, first_text_by=t0 + 0.3)
    assert time.monotonic() - t0 < 1.5                              # it did not wait for the deadline
    assert st.closed and recorded[0][0] == "director_direct" and recorded[0][1]["late"] is True
    assert recorded[0][1]["provider_cost_usd"] > 0                   # the input it bought is on the ledger


def test_a_call_inside_its_marks_returns_its_text(monkeypatch):
    call, recorded = _fake_lane(monkeypatch, _Stream(think=0.05, text='{"k":"film"}\n'))
    t0 = time.monotonic()
    r = call.stream("claude-opus-5-5", "s", [], max_tokens=100, effort="medium", purpose="director_direct",
                    deadline=t0 + 3.0, first_text_by=t0 + 2.0)
    assert r.text == '{"k":"film"}\n' and r.stop == "end_turn" and recorded[0][0] == "director_direct"


GESTURE_ARGS = {"hold": {"pic": {"plate": "#C2080D"}}, "cuts": {"pic": [{"plate": "#C2080D"}, "main"]}, "salvo": {"pic": [{"plate": "#111111"}, "main"]},
                "page": {"t": "HELLO"}, "big": {"t": "HELLO"}, "line": {"t": "hello there"}, "card": {"pic": "main", "w": .5, "h": .3, "rot": -4},
                "band": {"pic": "main"}, "neg_card": {"rect": [.1, .2, .5, .4]}, "flash": {"mode": "white"}, "burn": {}, "tiles": {"mode": "fill", "c": "#FFE600"},
                "punch": {}, "counter": {"from": 1, "to": 29}, "pile": {"pic": ["main", "main"]}, "split": {"pic": ["main", "main"]},
                "glitch": {"rect": [.2, .2, .5, .4]}, "boxes": {"rect": [.2, .2, .6, .5]}, "slat": {}, "shake": {}, "chip": {"t": "HEY"}, "echo": {},
                "type_on": {"t": "typing"}, "wall": {"pic": ["main", "main", "main", "main"]}, "ticker": {"t": "news"}}


@pytest.mark.parametrize("gesture", sorted(compiler.TODAY_GESTURES))
def test_every_offered_gesture_lowers_to_an_edl_the_write_accepts(tmp_path, gesture):
    """A film the write refuses falls to the chain and costs the customer the wait. Each gesture today's
    renderer is offered, at short and long spans and up to the last word, must lower to a valid EDL."""
    import schemas
    idx = talk_index(30.0)
    for at, f in (("w3", 2), ("w6..w14", 8), ("w2..w3", 1), ("each_cut", 3)):
        lines = [FILM, {"k": "b", "id": "b01", "w": "0-200", "job": "j", "cut": "pulse 0.6", "pic": "main",
                        "do": [{"g": gesture, "at": at, "f": f, **GESTURE_ARGS[gesture]}],
                        "snd": [{"s": "impact_1", "at": "s9.e"}, {"s": "whoosh_soft_1", "at": at}]}]
        comp = compiler.compile_score("\n".join(json.dumps(x) for x in lines) + "\n", index=idx)
        edl, _locals, _rep = lower.lower(comp.l1, str(tmp_path))
        schemas.validate_edl(edl, 30.0)


def test_a_counter_never_draws_two_steps_over_each_other(tmp_path):
    """A p5302 replay: a moving count's steps, each stretched to 0.3 s, drew DIA 23 over DIA 24."""
    beats = [{"k": "b", "id": "b01", "w": "0-20", "job": "j", "cut": "pulse 1", "pic": "main",
              "do": [{"g": "counter", "from": 20, "to": 29, "at": "w4..w7", "step": 1, "fmt": "DIA {n}", "size": 0.1, "seat": [0.5, 0.3]}]}]
    edl, _l, rep = _lowered(tmp_path, beats)
    steps = sorted((t for t in edl["texts"] if ".counter" in t["id"]), key=lambda t: t["start"])
    assert steps and steps[-1]["text"] == "DIA 29"
    for a, b in zip(steps, steps[1:]):
        assert a["end"] <= b["start"] + 1e-6 or abs(a["y"] - b["y"]) >= 0.8 * a["font_size"]
    assert all(t["end"] - t["start"] >= 0.3 - 1e-6 for t in steps)


def test_a_counter_reads_the_formats_a_model_writes():
    """A p5374 replay showed "11:%02d pm" on screen."""
    assert compiler.counter_text("11:%02d pm", 4, True) == "11:04 pm"
    assert compiler.counter_text("DIA {n}", 22, True) == "DIA 22"
    assert compiler.counter_text("{n:02d}h", 7, True) == "07h"
    assert compiler.counter_text("%d%%", 50, True) == "50%"
    assert compiler.counter_text("", 3, True) == "3"


@pytest.mark.skipif(compiler.font_path("NotoSansArabic-Bold.ttf") is None, reason="no Arabic-script font on this machine")
def test_a_persian_word_on_the_last_frame_never_outruns_the_programme(tmp_path):
    """p5374 with its Persian words (Gauntlet dirf-on3): joined-script type was lowered after the end clamp,
    so a short word on the last frames, held to today's 0.2 s minimum, ran past the programme and
    validate_edl refused the whole film."""
    import schemas
    idx = talk_index(20.0)
    n = len(idx["words"]) - 1
    beats = [{"k": "b", "id": "b01", "w": f"0-{n}", "job": "j", "cut": "pulse 1", "pic": "main",
              "do": [{"g": "big", "t": "جون", "at": f"w{n}", "size": 0.08, "font": "nastaliq"}]}]
    comp = compiler.compile_score("\n".join(json.dumps(x) for x in [FILM] + beats) + "\n", index=idx)
    word = next(i for i in comp.l1["items"] if i["k"] == "word" and i["t"] == "جون")
    word["f"] = [comp.l1["dur_f"] - 4, comp.l1["dur_f"]]          # four frames, under the 0.2 s minimum
    edl, locals_, rep = lower.lower(comp.l1, str(tmp_path))
    prog = sum(b - a for a, b in edl["keep"]) + sum(float(i.get("duration_s") or 0) for i in edl["inserts"])
    st = [o for o in edl["overlays"] if o["id"].startswith("st.")]
    assert st and rep["script_type_stills"] == len(st)
    assert all(o["start"] + o["duration_s"] <= prog + 1e-6 and o["duration_s"] >= lower.OV_MIN_S for o in edl["overlays"])
    schemas.validate_edl(edl, 20.0)


@pytest.mark.skipif(compiler.font_path("NotoSansArabic-Bold.ttf") is None, reason="no Arabic-script font on this machine")
@pytest.mark.parametrize("gesture", sorted(g for g in compiler.TODAY_GESTURES if "t" in GESTURE_ARGS.get(g, {})))
def test_every_offered_type_gesture_in_persian_lowers_to_an_edl_the_write_accepts(tmp_path, gesture):
    """The same, in a joined script: Persian type takes its own lowering (baked stills), which the Mac
    never ran because its fonts live only in the production images."""
    import schemas
    idx = talk_index(30.0)
    n = len(idx["words"]) - 1
    for at, f in (("w3", 2), ("w6..w14", 8), (f"w{n}", 1), (f"w{n - 1}..w{n}", 2)):
        lines = [FILM, {"k": "b", "id": "b01", "w": f"0-{n}", "job": "j", "cut": "pulse 0.6", "pic": "main",
                        "do": [{"g": gesture, "at": at, "f": f, **GESTURE_ARGS[gesture], "t": "به جون چشات"}]}]
        comp = compiler.compile_score("\n".join(json.dumps(x) for x in lines) + "\n", index=idx)
        edl, _locals, _rep = lower.lower(comp.l1, str(tmp_path))
        schemas.validate_edl(edl, 30.0)


def _bin_work(tmp_path, kind, rgb):
    from PIL import Image
    os.makedirs(tmp_path / "bin", exist_ok=True)
    Image.new("RGB", (630, 1200), rgb).save(tmp_path / "bin" / "0.jpg")
    with open(tmp_path / "bin.json", "w") as f:
        json.dump({"items": [{"id": 0, "kind": kind, "path": "bin/0.jpg", "w": 630, "h": 1200}]}, f)
    with open(tmp_path / "index.json", "w") as f:
        json.dump(talk_index(10.0), f)
    return str(tmp_path)


def _caps_over_bin(work):
    beats = [{"k": "b", "id": "b01", "w": "0-20", "job": "j", "cut": "pulse 1", "pic": "bin:0", "type": [{"cap": "cap:2", "ink": "#EFE6D6"}]}]
    comp = compiler.compile_score("\n".join(json.dumps(x) for x in [FILM] + beats) + "\n", work_dir=work, perception={})
    return comp, [i for i in comp.l1["items"] if i["k"] == "word" and i.get("role") == "cap"]


def _over_bin(comp, caps):
    shots = [i for i in comp.l1["items"] if i["k"] == "shot" and i.get("pic") == "bin:0"]
    return [c for c in caps if any(s["f"][0] <= (c["f"][0] + c["f"][1]) // 2 < s["f"][1] for s in shots)]


def test_a_found_clip_read_from_its_still_is_measured_and_its_words_carry_the_edge(tmp_path):
    """p5374 (Gauntlet dirf-dbg1): a found clip reaches the session as its still, and the fps filter made
    no frame of it, so no found clip was ever measured: cream captions sat bare on a white clock face and
    on bokeh. The still is read now, and since the clip's frames can differ from it, its words carry the edge."""
    import types
    work = _bin_work(tmp_path, "video", (12, 12, 14))            # a dark still: the clip may still turn white
    ctx = types.SimpleNamespace(thumb_cache={}, work=work, assets=compiler.load_bin(work))
    arr = compiler.thumbs(ctx, "0")
    assert arr is not None and len(arr) == 1 and float(arr.mean()) < 0.1
    comp, caps = _caps_over_bin(work)
    over = _over_bin(comp, caps)
    assert len(over) >= 10 and all(i.get("edge") for i in over)
    edl, _l, rep = lower.lower(comp.l1, work)
    assert rep.get("captions_edged") and any(c["style"].get("outline_color") == "#111111" for c in edl["captions"])


def test_a_found_still_image_keeps_its_measured_edge(tmp_path):
    """A found image is what it shows: light ink on a dark picture reads, and gets no edge."""
    comp, caps = _caps_over_bin(_bin_work(tmp_path, "image", (12, 12, 14)))
    over = _over_bin(comp, caps)
    assert len(over) >= 10 and not any(i.get("edge") for i in over)
