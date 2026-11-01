"""Ticket #77, second recurrence (1 Nov 2026, user 2710, job 132664): a
trial request asking for "speed ramping, slow motion" was rendered without
any speed change, the follow-up edits were refused (the trial's one
preview was spent) and the request ended partial. The editor now gets one
look at the request's undelivered asks before its render."""
import pathlib
from types import SimpleNamespace

import agent_loop
import ask_ledger

U2710 = ((pathlib.Path(__file__).parent / "fixtures" / "u2710_request.txt")
         .read_text(encoding="utf-8"))
START = {"keep": [[0.0, 134.38]], "music": [], "speed": [], "effects": {}}
# the turn's v8: six segments, cinematic grade, dissolves, fade-out, no speed
V8 = {"keep": [[0.0, 10.5], [13.5, 20.5], [40.5, 47.0], [61.0, 66.5],
               [109.0, 117.12], [127.0, 134.38]],
      "music": [], "speed": [],
      "effects": {"grade": "cinematic", "fade_out_s": 0.6,
                  "grade_custom": {"contrast": 1.12},
                  "transition": {"style": "dissolve", "junctions": [0]}}}


class _Ctx(SimpleNamespace):
    def latest_edl(self):
        return {"version": 8, "json": self.edl}


def _ctx(edl, **kw):
    base = dict(claude_trial={"model": "claude-haiku-5-5"}, claude_paid=None,
                edl=edl, turn_start_edl={"version": 2, "json": START},
                versions_written=[3, 4, 5, 6, 7, 8])
    base.update(kw)
    return _Ctx(**base)


def test_2710_ledger_reads_speed_and_music_as_asked():
    led = ask_ledger.ledger(U2710, START, V8, True)
    assert led.get("speed") == "not_done" and led.get("grade") == "done"
    assert "music" in led


def test_2710_the_hold_names_speed_once_before_the_render():
    ctx = _ctx(V8)
    # the report-only call renders nothing and does not spend the hold
    assert agent_loop._unmet_ask_hold(
        ctx, "render_preview", {"report": True, "complete": False}, U2710) is None
    hold = agent_loop._unmet_ask_hold(
        ctx, "render_preview", {"report": True, "complete": True}, U2710)
    assert hold and hold.startswith("NOT RENDERED YET")
    assert "set_speed" in hold and "slow motion" in hold
    # music is never held for: Valmera offers tracks instead of picking one
    assert "music" not in hold.lower()
    assert agent_loop._unmet_ask_hold(
        ctx, "render_preview", {"complete": True}, U2710) is None


def test_the_hold_stays_out_of_the_way():
    with_speed = dict(V8, speed=[{"start": 40.5, "end": 44.0, "factor": 0.5}])
    for ctx, name, text in (
            (_ctx(with_speed), "render_preview", U2710),          # delivered
            (_ctx(V8), "set_speed", U2710),                      # not a render
            (_ctx(V8), "render_preview", "add some music please"),  # music only
            (_ctx(V8, claude_trial=None), "render_preview", U2710)):  # old lane
        assert agent_loop._unmet_ask_hold(ctx, name, {"complete": True}, text) is None
