"""Round 96b — the step ceiling resumes itself (project 383, 2026-08-07).

A Portuguese-speaking user's first request ran 30 productive model calls in
7.5 minutes — slow-motion, music, grade, transitions, captions, four
previews — and then the loop fell off its `for range(30)` and posted a
canned ENGLISH "I hit my step limit … tell me to continue" with half the
900s time budget unspent and credits available. The user typed "continue"
in effect (their next message), which is our internal counter spending
their patience.

Now the ceiling is a runaway-loop breaker, not a mid-edit stop sign: a pass
that spent its iterations while still landing edits resumes itself with a
fresh, rebuilt context over the SAME user message, sharing the turn's wall
clock and spend cap. A pass that moved nothing still stops at the wall.

Pins:
  * _continue_decision gates on all four walls: the AGENT_AUTO_CONTINUES
    allowance, forward progress, wall-clock runway, and the spend cap.
  * _CONTINUATION_NOTE tells the model it is resuming its OWN turn (user
    saw nothing, do not redo work) and lists what already ran.
  * _run_loop accepts the _cont handoff (clocks and counters ride through,
    so the timeout and budget bound the CHAIN, not each pass).
  * AGENT_AUTO_CONTINUES defaults to 2 (>= 1, or the fix is off).
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_loop                                               # noqa: E402
import config                                                   # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


check("continuations are on by default", config.AGENT_AUTO_CONTINUES >= 1)

d = agent_loop._continue_decision
check("a progressing pass with time and money continues",
      d(0, True, 600, False) and d(config.AGENT_AUTO_CONTINUES - 1,
                                   True, 600, False))
check("the allowance is a hard wall",
      not d(config.AGENT_AUTO_CONTINUES, True, 600, False))
check("a pass that moved nothing is a runaway and stops",
      not d(0, False, 600, False))
check("no continuation into a dying clock", not d(0, True, 90, False))
check("no continuation past the spend cap", not d(0, True, 600, True))

note = agent_loop._CONTINUATION_NOTE.format(done="set_speed x4, add_music")
check("the note says the user saw nothing",
      "NOT seen any reply" in note and "NOT sent anything new" in note)
check("the note forbids redoing finished work",
      "do NOT redo finished work" in note)
check("the note carries what already ran", "set_speed x4, add_music" in note)

sig = inspect.signature(agent_loop._run_loop)
check("_run_loop takes the _cont handoff",
      "_cont" in sig.parameters
      and sig.parameters["_cont"].default is None)

# The tool summary the note is built from: n > 1 gets a count, n == 1 the
# bare name — mirrors how _run_loop renders _cont["timings"]["tools"].
tools = {"set_speed": {"n": 4, "s": 1.0}, "render_preview": {"n": 1, "s": 9.0}}
done = ", ".join(f"{name} x{t['n']}" if t["n"] > 1 else name
                 for name, t in sorted(tools.items()))
check("tool summary reads like an editor's log",
      done == "render_preview, set_speed x4")

print(f"\nALL {PASS} CHECKS PASSED")
