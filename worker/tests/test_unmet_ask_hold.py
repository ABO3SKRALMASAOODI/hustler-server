"""Ticket #77, second recurrence (1 Nov 2026, user 2710, job 132664): a
trial request asking for "speed ramping, slow motion" was rendered without
any speed change. The render hold that followed is gone (owner, 16 Nov 2026:
no holds on the editing path); the ledger's reading stays."""
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


