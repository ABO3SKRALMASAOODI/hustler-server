"""Round 90: the turn stops waiting on pixels, and the critic grows a floor.

Three weeks of production said the same thing twice. Turns were slow because
29% of every one of them was a preview encode the reply blocked on — and the
cost of that encode tracks the FOOTAGE, not the edit (measured across one
afternoon: 0.12s to 4.3s of encode per second of output, so the same
one-word caption fix cost 10s on one project and 82s on another). And the
edits were timid because every quality signal in the system was a ceiling:
of 447 self-checks 244 came back "looks clean", and the median shipped
project carried 4.2 cuts/min, zero text and zero sfx against reference reels
at 32 cuts/min with word-synced typography throughout.

Both halves are pinned here.

Pure logic — no ffmpeg, no DB, no network. Run from worker/:
    python tests/test_round90_turn.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import taste                                                 # noqa: E402
from timeline import Timeline                                # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def fired(findings, needle):
    return any(needle in f for f in findings)


VERTICAL = {"width": 1080, "height": 1920}


def _index(words=40, shots=1, dur=40.0, speech_from=0.5):
    ws = [{"w": f"w{i}", "t0": speech_from + i * 0.4,
           "t1": speech_from + 0.3 + i * 0.4} for i in range(words)]
    return {"words": ws, "video": dict(VERTICAL, duration=dur),
            "shots": [{"t0": 0.0, "t1": dur}] * shots}


def _crit(edl, index=None, ask=""):
    index = index or _index()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    return taste.critique(edl, index, tl, VERTICAL["width"],
                          VERTICAL["height"], ask)


RAW = {"keep": [[0.0, 40.0]], "effects": {}}
# Captions + music + a grade: the three things every reference reel carries.
DRESSED = {"keep": [[0.0, 40.0]], "effects": {"grade": "cinematic"},
           "captions": {"mode": "from_transcript"},
           "music": [{"id": "m1", "start": 0.0, "end": 40.0,
                      "gain_db": -18.0, "duck": True}]}


print("== the floor: an edit with nothing in it ==")

f = _crit(RAW, ask="edit it")
check("a bare edit on an open brief is called out",
      fired(f, "THIS IS NOT AN EDIT YET"))
check("...and it says so ONCE, not five times",
      sum(1 for x in f if "NOT AN EDIT YET" in x) == 1
      and not fired(f, "there is no music under this edit"))

check("a dressed edit raises no floor finding at all",
      not any("NOT AN EDIT YET" in x or "no music under" in x
              or "ungraded" in x for x in _crit(DRESSED, ask="edit it")))

print("== the floor stays out of a narrow request ==")

check("'make the text bigger' never triggers a floor",
      not fired(_crit(RAW, ask="make the text bigger"), "NOT AN EDIT YET"))
check("nor does a specific brief with its own instructions",
      not fired(_crit(RAW, ask="cut the first 3 seconds and add a zoom at 8s"),
                "NOT AN EDIT YET"))
check("an empty ask counts as open (the agent is finishing its own edit)",
      fired(_crit(RAW, ask=""), "NOT AN EDIT YET"))

print("== the floor is per-layer once something exists ==")

no_music = dict(DRESSED)
no_music.pop("music")
f = _crit(no_music, ask="edit it")
check("music missing is reported on its own", fired(f, "no music under"))
check("...without claiming the whole edit is empty",
      not fired(f, "NOT AN EDIT YET"))

no_grade = dict(DRESSED, effects={})
check("an ungraded picture is reported (the corpus's most universal marker)",
      fired(_crit(no_grade, ask="edit it"), "ungraded"))

silent_words = dict(DRESSED)
silent_words.pop("captions")
check("40 spoken words with nothing on screen is reported",
      fired(_crit(silent_words, ask="edit it"), "not one of them is on screen"))

print("== the floor never contradicts the user ==")

check("'keep it simple' silences the whole floor",
      not fired(_crit(RAW, ask="edit it but keep it simple"),
                "NOT AN EDIT YET"))
check("'no music' silences the music floor only",
      not fired(_crit(no_music, ask="edit it, no music"), "no music under"))
check("...and the rest of the audit still runs",
      fired(_crit(dict(no_music, effects={}), ask="edit it, no music"),
            "ungraded"))

print("== project 234: the critic may not override the customer ==")

# The real brief, and the real edit it was written against: 68s, speech from
# 10.5s. The audit reported dead air, the agent obeyed it over the customer
# and cut 68 -> 32.5 -> 26.8 -> 25 -> 22s across two turns. The user re-sent
# the identical brief, the agent restored the length, and the audit fired
# again. Then the credits ran out and that user never came back.
BRIEF_234 = ("Use the full duration of the selected clip and do not shorten "
             "it. Add some zooms and captions.")
long_idx = _index(words=60, dur=68.0, speech_from=10.5)
long_edl = {"keep": [[0.0, 68.0]], "effects": {},
            "captions": {"mode": "from_transcript"},
            "music": [{"id": "m1", "start": 0.0, "end": 68.0,
                       "gain_db": -18.0, "duck": True}]}
tl68 = Timeline(long_edl["keep"], [], [])

with_brief = taste.critique(long_edl, long_idx, tl68, 1080, 1920, BRIEF_234)
check("'do not shorten it' makes the dead-air finding inadmissible",
      not fired(with_brief, "dead air"))

without = taste.critique(long_edl, long_idx, tl68, 1080, 1920, "edit it")
check("...and without that instruction it still fires",
      fired(without, "dead air"))

check("the constraint is actually parsed off the real brief",
      "keep_length" in taste.user_constraints(BRIEF_234))
check("a brief that says nothing about length constrains nothing",
      taste.user_constraints("add captions and a zoom") == set())

print("== the audit line says which direction a finding points ==")

line = taste.audit_line(_crit(RAW, ask="edit it"))
check("the header no longer says only 'fix what the user did not ask for'",
      "bare" in line and "overreaches" in line)
check("a clean edit still produces no line at all",
      taste.audit_line([]) == "")


print("== the turn hands the preview to the queue and does not wait ==")

import agent_loop                                            # noqa: E402
import agent_tools                                           # noqa: E402
import db as dbx                                             # noqa: E402


class FakeDb:
    """Records what the turn asked the database to do. enqueue_job returns an
    id the way the real one does; add_message swallows the activity row."""

    def __init__(self):
        self.enqueued = []
        self.messages = []

    def run(self, fn, *a, **kw):
        if fn is dbx.enqueue_job:
            _pid, _uid, jtype, payload = a
            self.enqueued.append((jtype, payload))
            return 4242
        if fn is dbx.add_message:
            self.messages.append(a)
            return None
        raise AssertionError(f"unexpected db call: {fn}")


class FakeCtx:
    def __init__(self, version=7, written=(7,), rendered=()):
        self._v = version
        self.versions_written = list(written)
        self.rendered_versions = set(rendered)
        self.project_id = 363
        self.job = {"id": 2530, "user_id": 99}
        self.autorendered = False
        self.preview_pending = None

    def latest_edl(self):
        return {"version": self._v, "json": {"keep": [[0.0, 10.0]]}}


db = FakeDb()
ctx = FakeCtx()
timings = {}
latest = agent_loop._queue_turn_preview(ctx, db, 1, timings)

check("the version this turn ended on is returned", latest["version"] == 7)
check("a preview job is enqueued", [t for t, _ in db.enqueued] == ["preview"])
check("...for that exact version",
      db.enqueued[0][1]["edl_version"] == 7)
check("...tagged as the turn's, so a failure can be explained to the user",
      db.enqueued[0][1]["source"] == "turn")
check("the reply carries the pending render so the fence can allow it",
      ctx.preview_pending == {"job": 4242, "edl_version": 7})
check("NOTHING was waited on — no render call, no poll loop",
      timings.get("queue_preview_s") is not None
      and "auto_render_s" not in timings)

db2 = FakeDb()
ctx2 = FakeCtx(written=())
agent_loop._queue_turn_preview(ctx2, db2, 1, {})
check("a turn that changed nothing queues nothing", db2.enqueued == [])

db3 = FakeDb()
ctx3 = FakeCtx(version=7, written=(7,), rendered=(7,))
agent_loop._queue_turn_preview(ctx3, db3, 1, {})
check("a version the agent already rendered itself is not re-queued",
      db3.enqueued == [])


class BrokenDb(FakeDb):
    def run(self, fn, *a, **kw):
        if fn is dbx.enqueue_job:
            raise RuntimeError("db is down")
        return super().run(fn, *a, **kw)


ctx4 = FakeCtx()
latest4 = agent_loop._queue_turn_preview(ctx4, BrokenDb(), 1, {})
check("a queue failure never loses the landed edit",
      latest4["version"] == 7 and ctx4.preview_pending is None)

print("== a queued preview is an honest thing for the reply to imply ==")

check("a claim of a render is NOT a violation once one is queued",
      agent_loop._reply_violations("Here's the cut — it's 32s now.",
                                   wrote=True, previewed=True) == [])
check("...and still is when nothing was rendered or queued",
      agent_loop._reply_violations("Rendered the preview for you.",
                                   wrote=True, previewed=False) != [])

facts = agent_loop._turn_facts(
    type("C", (), {"latest_edl": lambda s: {"version": 7},
                   "versions_written": [7], "write_calls": ["add_text"],
                   "images_generated": [], "urls_fetched": [],
                   "last_preview": None, "last_selfcheck": None,
                   "preview_pending": {"job": 1, "edl_version": 7}})(), 6)
check("the turn facts say the render is under way",
      "RENDERING NOW" in facts)
check("...and forbid claiming to have watched it",
      "may NOT claim to have watched it" in facts)

print("== batch: many edits, one round trip ==")

check("batch is a registered tool", "batch" in agent_tools.TOOLS)
_spec = next(t["function"] for t in agent_tools.openai_tools()
             if t["function"]["name"] == "batch")
check("it is offered to the model as an array of {tool, args}",
      _spec["parameters"]["properties"]["calls"]["type"] == "array"
      and set(_spec["parameters"]["properties"]["calls"]["items"]
              ["properties"]) == {"tool", "args"})
check("an empty batch is rejected, not silently ignored",
      agent_tools.batch(None, calls=[]).startswith("REJECTED"))
check("the runaway backstop sits ABOVE what a step can physically emit",
      agent_tools.BATCH_MAX_CALLS >= 100)
check("...and past it the refusal says how to proceed, not just no",
      "two batches" in agent_tools.batch(
          None, calls=[{"tool": "get_edl"}] * (agent_tools.BATCH_MAX_CALLS + 1)))
check("a batch cannot contain a batch",
      "cannot contain a batch" in agent_tools.batch(
          None, calls=[{"tool": "batch", "args": {}}]))
check("an unknown tool inside a batch fails only that line",
      "unknown tool" in agent_tools.batch(
          None, calls=[{"tool": "no_such_tool"}]))
check("the intermediate Before/After states are stripped from a write",
      agent_tools._batch_digest(
          "add_text",
          "EDL v3 -> v4: added text 'hi'. Before: A. After: B.")
      == "EDL v3 -> v4: added text 'hi'.")
check("a READ result is never truncated — a half transcript is worse than a "
      "long one",
      agent_tools._batch_digest("get_words", "x" * 5000) == "x" * 5000)


print(f"\n{PASS} checks passed")
