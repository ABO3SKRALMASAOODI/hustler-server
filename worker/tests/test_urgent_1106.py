"""The urgent release of 6 Nov 2026 (.valmera/week-1106/reliability/URGENT.md).

* The funded OpenAI key arrives through a sealed app_kv row that outranks the
  dead env value, and it moves NO customer's edit: the owner's warning, "their
  luna model was terrible for users and was editing nothing".
* A continuous take with no speech never crashes the First cut (FINDINGS #2).
* A first ask typed during analysis is the first edit (FINDINGS #3).

Every test here fails on 59db7af5.
"""
import json
import pathlib
import time
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_tools
import claude_lane
import claude_trial
import config
import continuous_cut
import db as dbx
import first_cut
import indexer
import llm
import provider_creds
import short_clip
import shorts
import storage_creds

ROOT = pathlib.Path(__file__).resolve().parents[2]

LEGACY_SECRET = "legacy-r2-secret-0123456789abcdef"
EXEC_SECRET = "executor-bearer-secret-fedcba9876543210"
FUNDED = "sk-funded-key-that-must-never-show"
DEAD = "sk-the-old-dead-key"
# Sealed by backend provider_creds (cryptography) with the secrets above: the
# worker images have none, so this row must open through yt-dlp's AES-GCM.
VECTOR = json.dumps({
    "alg": "AES-256-GCM",
    "also": [{
        "ct": "e3wBqZKqVISjLtFr2ta7x8xkvKFTn+5v60nJQXXazsJmuuYKPpESHhIrKdifpyQw"
              "igraFKxVVxoG/qBeT34imvxoyMPF5epMECrCXZVum19Mfyrr0tDbuhjMZRadFtac"
              "fCZu9c3xLjAzIW5aXdPBoDzpFCBJrorx",
        "kek": "REMOTE_EXECUTOR_SECRET", "kid": "30a97bdd6f72de32",
        "nonce": "BwcHBwcHBwcHBwcH"}],
    "at": "2026-11-06T00:00:00Z",
    "ct": "hLnMa7lY4ogp5kHrfEUndEgtEUPP3tMz692yR+yY6Q3hZgngrikwBGoAo4zBzFZw"
          "KHeZMPfVZuURJ7DG12al27iuNPiYVOFgeJpHaa6VTVmOwm+C7oFD65jeWp8GoxAM"
          "rej1M2CrQeJMicI32CpGHuAfNc45SU1R",
    "fields": 2, "info": "valmera-provider-keys-v1", "kdf": "HKDF-SHA256",
    "kek": "S3_SECRET_ACCESS_KEY", "kid": "334ec268b3cefa3e",
    "nonce": "BwcHBwcHBwcHBwcH", "v": 1})


def _live_key(monkeypatch):
    """config.__getattr__ again, whatever an earlier suite left assigned."""
    for name in ("OPENAI_API_KEY", "VISION_API_KEY"):
        if name in vars(config):
            monkeypatch.delattr(config, name)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.setattr(config, "VISION_BASE_URL", config.OPENAI_BASE_URL)


@pytest.fixture
def funded(monkeypatch):
    """A process whose env holds the dead key and whose row holds the funded
    one: production after the row is written."""
    monkeypatch.setattr(config, "_OPENAI_ENV_KEY", DEAD)
    monkeypatch.setattr(provider_creds, "key", lambda name, env=None:
                        FUNDED if name == "OPENAI_API_KEY" else "")
    _live_key(monkeypatch)
    monkeypatch.setattr(config, "OPENAI_EDITS", False)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_client_built", None)
    monkeypatch.setattr(llm, "_vision_client", None)
    monkeypatch.setattr(llm, "_vision_client_built", None)


# ------------------------------------------------------------------ #
#  The sealed row                                                      #
# ------------------------------------------------------------------ #

def test_the_two_copies_are_identical():
    assert (ROOT / "backend/provider_creds.py").read_bytes() == \
        (ROOT / "worker/provider_creds.py").read_bytes()


def _keys(raw, env):
    return provider_creds.Keys(fetch=lambda: raw, env=env, background=False,
                               spec=provider_creds.SPEC, what="provider")


def test_the_row_opens_in_a_worker_image_without_cryptography():
    got = _keys(VECTOR, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}).values()
    assert got["OPENAI_API_KEY"] == FUNDED
    got = _keys(VECTOR, {"REMOTE_EXECUTOR_SECRET": EXEC_SECRET}).values()
    assert got["OPENAI_API_KEY"] == FUNDED


def test_a_storage_row_never_opens_as_a_provider_row():
    row = json.loads(VECTOR)
    row["info"] = storage_creds.INFO
    with pytest.raises(storage_creds.CredentialsError):
        storage_creds.open_row(json.dumps(row),
                               {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET},
                               spec=provider_creds.SPEC)


@pytest.mark.parametrize("raw,env", [
    (None, {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}),
    (VECTOR, {"S3_SECRET_ACCESS_KEY": "a-different-secret"}),
    ('{"v": 1}', {"S3_SECRET_ACCESS_KEY": LEGACY_SECRET}),
])
def test_a_row_that_does_not_open_leaves_the_env_key(raw, env):
    assert _keys(raw, env).values() is None


def test_the_row_outranks_the_dead_env_key_and_a_rotation_lands(funded,
                                                                monkeypatch):
    assert config.OPENAI_API_KEY == FUNDED
    assert config.VISION_API_KEY == FUNDED
    first = llm.client()
    assert first.api_key == FUNDED and llm.client() is first
    monkeypatch.setattr(provider_creds, "key", lambda name, env=None:
                        "sk-rotated" if name == "OPENAI_API_KEY" else "")
    assert llm.client().api_key == "sk-rotated"
    assert llm.vision_client().api_key == "sk-rotated"


def test_status_reports_names_and_never_a_value(monkeypatch):
    monkeypatch.setattr(provider_creds, "_row",
                        lambda: {"OPENAI_API_KEY": FUNDED})
    report = json.dumps(provider_creds.status())
    assert FUNDED not in report and '"openai": "app_kv"' in report


def test_the_funded_key_is_redacted_from_recorded_payloads(funded):
    stored = dbx._capped_payload({"a": f"Bearer {FUNDED}", "b": DEAD})
    assert FUNDED not in json.dumps(stored) and DEAD not in json.dumps(stored)


# ------------------------------------------------------------------ #
#  No customer's edit moves onto OpenAI                                #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("plan", ["plus_2026", "pro_2026", "max_2026",
                                  "advanced", "ai_max"])
def test_a_funded_key_gives_a_subscriber_no_openai_editing_lane(funded, plan):
    assert llm.agent_lanes_for(True, plan) == []


def test_a_funded_key_gives_an_unsubscribed_turn_no_openai_lane(funded):
    assert llm.agent_lanes_for(False, "free") == []


def test_a_recovery_stays_on_claude(funded, monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    lanes = llm.agent_lanes_for(True, "plus_2026", operator_repair=True)
    assert [lane["name"] for lane in lanes] == ["claude_recovery"]


def test_only_the_explicit_switch_gives_openai_a_lane(funded, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_EDITS", True)
    models = [lane["model"] for lane in llm.agent_lanes_for(True, "plus_2026")]
    assert models == ["gpt-6-luna"]


class _StopTurn(Exception):
    pass


def _turn(monkeypatch, tmp_path, subscribed, plan, paid_lane):
    """run_agent_job up to the lane choice, the real code on both sides."""
    said = []

    class Db:
        def run(self, fn, *args):
            if fn is dbx.get_project:
                return {"id": 5, "chat_session_id": 9, "owner_id": 8}
            if fn is dbx.latest_asset:
                return None
            if fn is dbx.user_billing:
                return subscribed, plan, False
            if fn is dbx.add_message:
                said.append(args)
                return 1
            if getattr(fn, "__name__", "") == "_get_msg":
                return {"id": 1, "content": "make it nice", "meta": {}}
            return None

    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    ctx_box = {}

    class Ctx(NS):
        def latest_edl(self):
            return {"version": 1, "json": {}}

    def fake_ctx(worker_db, job, project, index, workdir):
        ctx = Ctx(project_id=5, user_message="", job=job)
        ctx_box["ctx"] = ctx
        return ctx
    monkeypatch.setattr(agent_tools, "ToolContext", fake_ctx)
    import operator_recovery
    monkeypatch.setattr(operator_recovery, "superseded", lambda ctx: None)
    monkeypatch.setattr(claude_trial, "resolve", lambda *a, **k:
                        claude_trial.Decision(claude_trial.Decision.NOT_TRIAL))
    monkeypatch.setattr(agent_loop, "_claude_paid_lane",
                        lambda *a: paid_lane)
    # Stop the turn where the model would first be called, with the lanes set.
    monkeypatch.setattr(llm, "set_turn_plan", lambda *a, **k: (_ for _ in ()
                        ).throw(_StopTurn()))
    job = {"id": 1, "project_id": 5, "user_id": 8, "payload": {"message_id": 1}}
    return ctx_box, said, Db(), job


def test_a_paying_turn_with_a_funded_key_runs_on_claude_alone(funded, monkeypatch,
                                                              tmp_path):
    """The turn a subscriber runs today, with the funded key delivered: Claude
    is the only lane, so a Claude refusal has no Luna to fall onto."""
    paid = {"lane": claude_lane.lane("claude-haiku-5-5", name="claude_paid"),
            "credit_model": "gpt-6-luna"}
    box, said, db, job = _turn(monkeypatch, tmp_path, True, "plus_2026", paid)
    with pytest.raises(_StopTurn):
        agent_loop.run_agent_job(db, job)
    ctx = box["ctx"]
    assert [lane["model"] for lane in ctx.agent_lanes] == ["claude-haiku-5-5"]
    assert ctx.agent_model == "claude-haiku-5-5"


def test_a_subscriber_with_no_claude_lane_is_told_never_moved_to_luna(
        funded, monkeypatch, tmp_path):
    box, said, db, job = _turn(monkeypatch, tmp_path, True, "pro_2026", None)
    result = agent_loop.run_agent_job(db, job)
    assert result["outcome"] == "blocked" and result["billable"] is False
    assert "editing model" in said[0][2]
    assert getattr(box["ctx"], "agent_model", None) != "gpt-6-luna"


def test_an_account_with_no_plan_gets_the_cards_never_luna(funded, monkeypatch,
                                                           tmp_path):
    box, said, db, job = _turn(monkeypatch, tmp_path, False, "free", None)
    result = agent_loop.run_agent_job(db, job)
    assert result["outcome"] == "blocked"
    assert said[0][2] == claude_trial.UNAVAILABLE_TEXT


def test_the_shorts_clip_pick_never_runs_on_openai(funded, monkeypatch):
    called = []
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: called.append(a))
    out = shorts._ask_json(None, {"id": 1}, True, "plus_2026", "sys", "user",
                           "shorts_plan")
    assert out is None and called == []


def test_the_continuous_plan_never_runs_on_openai(funded, monkeypatch):
    called = []
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: called.append(a))
    ctx = NS(index={"video": {"duration": 51.267}})
    got = continuous_cut.plan_or_whole(ctx, {"usd": 0})
    assert called == [] and got["keep"] == [[0.0, 51.267]]


# ------------------------------------------------------------------ #
#  FINDINGS #2: one continuous take with no speech                     #
# ------------------------------------------------------------------ #

class _Ctx:
    def __init__(self, duration=51.267, height=1280):
        self.index = {"video": {"duration": duration, "width": 720,
                                "height": height},
                      "words": [], "sentences": [], "shots": [
                          {"start": 0.0, "end": duration}]}
        self.duration = duration
        self.versions_written = []
        self.first_cut_q1 = True
        self.first_cut_model = "claude-haiku-5-5"
        self.keep = [[0.0, duration]]

    def latest_edl(self):
        return {"version": 1, "json": {"keep": self.keep}}

    def proxy_path(self):
        return "/nonexistent"


def _chain(monkeypatch, plan, results=None):
    calls = []

    def execute(ctx, name, args):
        calls.append((name, args))
        return (results or {}).get(name, f"EDL v{len(calls) + 1} ok")
    monkeypatch.setattr(agent_tools, "execute", execute)
    monkeypatch.setattr(first_cut, "_motion_curve", lambda ctx: None)
    monkeypatch.setattr(first_cut, "montage_scout",
                        lambda ctx, w, spend, title_only=False, boxes=False:
                        (w, "NIGHT *BENCH*") if title_only else (None, None))
    monkeypatch.setattr(continuous_cut, "plan", lambda ctx, spend: plan)
    monkeypatch.setattr(short_clip, "look", lambda *a, **k: [
        (t, [0.3, 0.4, 0.5, 0.6]) for t in (5.0, 15.0, 25.0, 35.0, 45.0)])
    return calls


def test_a_whole_take_plan_on_a_float_mismatch_does_not_crash(monkeypatch):
    """p5374: the plan rounds the boundary, the stored keep does not, `!=`
    said "changed", keep_segments said NO CHANGE and the chain raised."""
    calls = _chain(monkeypatch, {"keep": [[0.0, 51.3]], "adjustments": {}},
                   {"keep_segments": "NO CHANGE — the EDL is identical"})
    ctx = _Ctx(duration=51.267)
    ctx.keep = [[0.0, 51.2667]]
    _s, applied, picked = first_cut.video_chain(ctx, None, time.monotonic(), {"usd": 0})
    assert picked["how"] == "continuous"
    assert "original sound preserved" in applied[0]


def test_a_refused_trim_or_grade_leaves_the_take_and_the_chain_going(monkeypatch):
    calls = _chain(monkeypatch,
                   {"keep": [[3.0, 40.0]], "adjustments": {"exposure": .1}},
                   {"keep_segments": "REJECTED: nope",
                    "set_grade_custom": "NO CHANGE — identical"})
    _s, applied, picked = first_cut.video_chain(_Ctx(), None, time.monotonic(), {"usd": 0})
    assert picked["how"] == "continuous"
    assert "original sound preserved" in applied[0]


def test_the_take_gets_a_real_edit_not_the_upload_back(monkeypatch):
    calls = _chain(monkeypatch, {"keep": [[0.0, 51.267]], "adjustments": {}})
    _s, applied, _p = first_cut.video_chain(_Ctx(), None, time.monotonic(), {"usd": 0})
    names = [c[0] for c in calls]
    assert "add_motion_graphic" in names and "add_zoom_path" in names
    assert "a hook title" in applied
    assert "a camera that follows the subject" in applied


def test_the_camera_lasts_the_take_instead_of_landing_and_holding(monkeypatch):
    calls = _chain(monkeypatch, {"keep": [[0.0, 51.267]], "adjustments": {}})
    first_cut.video_chain(_Ctx(), None, time.monotonic(), {"usd": 0})
    keys = dict(calls)["add_zoom_path"]["keyframes"]
    pushes = [k["strength"] for k in keys]
    assert pushes == sorted(pushes), "a slow, steady push"
    assert [k["t"] for k in keys if k["strength"] >= pushes[-1] - 0.16][0] > 30


def test_an_unusable_plan_keeps_the_whole_take(monkeypatch):
    def refuse(ctx, spend):
        raise ValueError("Continuous footage needs a grounded edit plan")
    _chain(monkeypatch, {})
    monkeypatch.setattr(continuous_cut, "plan", refuse)
    _s, applied, picked = first_cut.video_chain(_Ctx(), None, time.monotonic(), {"usd": 0})
    assert picked["how"] == "continuous"
    assert picked["plan"]["keep"] == [[0.0, 51.267]]


def test_a_stray_field_or_an_overshoot_does_not_cost_the_plan(monkeypatch):
    """Haiku's "_note": "" beside a good exposure lift threw the plan away."""
    args = {"subject": "night street", "reason": "one take",
            "start_boundary": 0, "end_boundary": 12,
            "adjustments": {"exposure": 0.08, "shadows": 0.4, "_note": "",
                            "contrast": "more"}}
    call = NS(function=NS(name="plan_continuous_take",
                          arguments=json.dumps(args)))
    reply = NS(choices=[NS(message=NS(tool_calls=[call]))], usage=None)
    monkeypatch.setattr(llm, "create_with_dialect", lambda *a, **k: reply)
    monkeypatch.setattr(llm, "provider_cost_usd", lambda *a: 0)
    monkeypatch.setattr(continuous_cut.subprocess, "run",
                        lambda *a, **k: NS(returncode=0, stdout=b"jpg"))
    ctx = NS(index={"video": {"duration": 51.267}}, first_cut_model="claude-x",
             proxy_path=lambda: "/nonexistent")
    got = continuous_cut.plan(ctx, {"usd": 0})
    assert got["adjustments"] == {"exposure": 0.08, "shadows": 0.15}
    assert got["keep"] == [[0.0, 51.267]]


def test_a_subject_that_fills_the_frame_still_gets_a_moving_camera(monkeypatch):
    """p5374: the bench filled the frame, so no push and no camera at all."""
    calls = _chain(monkeypatch, {"keep": [[0.0, 51.267]], "adjustments": {}})
    monkeypatch.setattr(short_clip, "look", lambda *a, **k: [
        (t, [0.0, 0.1, 1.0, 1.0]) for t in (5.0, 15.0, 25.0, 35.0, 45.0)])
    _s, applied, _p = first_cut.video_chain(_Ctx(), None, time.monotonic(),
                                            {"usd": 0})
    assert "add_zoom_path" in dict(calls)
    assert "a camera that follows the subject" in applied


def test_a_landscape_take_is_fitted_whole(monkeypatch):
    calls = _chain(monkeypatch, {"keep": [[0.0, 51.267]], "adjustments": {}})
    ctx = _Ctx()
    ctx.index["video"].update(width=1280, height=720)
    _s, applied, _p = first_cut.video_chain(ctx, None, time.monotonic(), {"usd": 0})
    assert ("set_frame", {"ratio": "9:16", "mode": "pad_blur"}) in calls
    assert "auto_reframe" not in [c[0] for c in calls]


def test_a_dying_chain_still_answers_the_customer(monkeypatch, tmp_path):
    said = []

    class Db:
        def run(self, fn, *args):
            if fn is dbx.add_message:
                said.append(args)
            if fn is dbx.user_billing:
                return False, "free", False
            return None

    class Ctx(NS):
        versions_written = []

        def latest_edl(self):
            return {"version": 1, "json": {"keep": [[0.0, 5.0]]}}

    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(agent_tools, "ToolContext",
                        lambda *a: Ctx(duration=5.0, project_id=1))
    import operator_recovery
    monkeypatch.setattr(operator_recovery, "superseded", lambda ctx: None)
    monkeypatch.setattr(claude_trial, "resolve", lambda *a, **k:
                        claude_trial.Decision(claude_trial.Decision.NOT_TRIAL))
    monkeypatch.setattr(claude_trial, "credit_status", lambda *a, **k: {})
    import first_cut_visual
    monkeypatch.setattr(first_cut_visual, "engine_on", lambda *a: False)

    def die(*a, **k):
        raise ValueError("anything at all")
    monkeypatch.setattr(first_cut, "video_chain", die)
    result = first_cut.run(Db(), {"id": 2, "user_id": 3, "project_id": 1,
                                  "payload": {}},
                           {"id": 1, "chat_session_id": 9},
                           {"json": {"video": {"duration": 5.0}}})
    assert result["outcome"] == "blocked"
    assert any("couldn't finish" in str(a[2]) for a in said)


# ------------------------------------------------------------------ #
#  FINDINGS #3: the ask typed during analysis is the first edit        #
# ------------------------------------------------------------------ #

def _setup(monkeypatch, pending):
    resolved, first_cuts, said = [], [], []

    class Db:
        def run(self, fn, *args, **kw):
            if fn is dbx.latest_edl:
                return {"json": {"keep": [[0.0, 12.0]]}}
            if fn is dbx.has_index_greet:
                return False
            if fn is dbx.pending_user_message:
                return pending
            if fn is dbx.get_project:
                return {"kind": "edit"}
            if fn is first_cut.enqueue:
                first_cuts.append(args)
                return 77
            if fn is first_cut.project_admission:
                return {"kind": "trial", "first_cut": True}
            if fn is dbx.add_message:
                said.append(args)
            return None

    monkeypatch.setattr(indexer, "validate_edl", lambda *a: None)
    monkeypatch.setattr(indexer, "_sweep_tray_placements", lambda *a, **k: 0)
    monkeypatch.setattr(indexer, "_greet_via_llm", lambda *a, **k: None)
    monkeypatch.setattr(
        indexer, "_resolve_pending_auto_resume",
        lambda *a, **k: resolved.append(a[4]) or {"state": "enqueued",
                                                  "job_id": 5})
    return Db(), resolved, first_cuts


@pytest.mark.parametrize("key", ["", "sk-anything"])
def test_a_waiting_request_is_the_first_edit_whatever_key_is_set(monkeypatch,
                                                                 key):
    """Behind `config.OPENAI_API_KEY` an unset key skipped the auto-resume and
    the customer got a generic First cut."""
    monkeypatch.setattr(config, "_OPENAI_ENV_KEY", key)
    monkeypatch.setattr(provider_creds, "key", lambda name, env=None: "")
    _live_key(monkeypatch)
    db, resolved, first_cuts = _setup(monkeypatch, {"id": 157774})
    indexer._finish_setup(db, 1, 9, {"duration": 12.0},
                          {"words": [], "shots": []}, user_id=3, asset_id=4)
    assert resolved == [157774] and first_cuts == []


def test_the_editor_does_what_is_clear_in_a_cut_off_request():
    """p5297's ask stopped mid-sentence and the first edit was a question."""
    import agent_prompt
    text = " ".join(str(v) for v in vars(agent_prompt).values()
                    if isinstance(v, str))
    assert "Never answer a request you can partly do with only a question" \
        in text


class _Turn(NS):
    pass


def _editing_turn(**kw):
    return _Turn(agent_lanes=[{"model": "claude-x"}], versions_written=[],
                 project_id=1, job={"id": 1}, **kw)


def test_a_turn_that_changed_nothing_does_the_edit_before_it_asks(monkeypatch):
    import music_gate
    monkeypatch.setattr(music_gate, "repeat_offer_refusal", lambda *a: None)
    ctx = _editing_turn()
    first = agent_tools.ask_user(ctx, "Which colour should the SUV become?")
    assert first.startswith("CORRECTION_NEEDED") and "choose" in first
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(ctx, "Which colour should the SUV become?")


def test_a_question_after_an_edit_or_outside_a_turn_is_asked(monkeypatch):
    import music_gate
    monkeypatch.setattr(music_gate, "repeat_offer_refusal", lambda *a: None)
    edited = _editing_turn()
    edited.versions_written = [2]
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(edited, "Which of these two?")
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(NS(), "An MCP caller's question")


def test_a_music_offer_stays_the_customers_pick(monkeypatch):
    import music_gate
    monkeypatch.setattr(music_gate, "repeat_offer_refusal", lambda *a: None)
    monkeypatch.setattr(music_gate, "offered_slugs", lambda q: ["calm-1"])
    monkeypatch.setattr(music_gate, "open_offer", lambda *a: None)
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(_editing_turn(), "Calm One or Calm Two?")


def test_no_waiting_request_gets_the_first_cut(monkeypatch):
    db, resolved, first_cuts = _setup(monkeypatch, None)
    indexer._finish_setup(db, 1, 9, {"duration": 12.0},
                          {"words": [], "shots": []}, user_id=3, asset_id=4)
    assert resolved == [] and len(first_cuts) == 1


def test_no_first_cut_over_a_request_already_running(monkeypatch):
    """A message sent the moment analysis ended got its own turn before the
    index finished setting up."""
    seen = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            seen.append(sql)

        def fetchone(self):
            return {"id": 3}

    class Conn:
        def cursor(self):
            return Cur()

    assert first_cut.enqueue(Conn(), 1, 2) is None
    assert "message_id" in seen[0] and "'queued', 'running'" in seen[0]
