"""Round 96c — same-script language flips + word-level caption mutes
(project 384, 2026-08-07).

One session, two defects the developer watched a user hit:

  * The user wrote English and the turn's reply came back in GERMAN. A
    model now judges that (reply_language, tests/test_reply_language.py).

  * Captions vanished for seconds past the muted title window: apply_mutes
    dropped WHOLE caption events, so the block straddling 5.5s died with
    the mute and captions resumed one block late. Transcript mutes now act
    at the WORD level before grouping (same stage as filler removal), with
    only display-padding ends pulled back to the window edge.

Pins:
  * _drop_muted_words / _clamp_event_ends_to_mutes mute exactly the window;
    apply_mutes (dictated caption items) is unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import captions                                                 # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


# ── word-level caption mutes ─────────────────────────────────────────

WORDS = [{"w": "welcome", "t0": 0.2, "t1": 0.8},
         {"w": "to", "t0": 0.9, "t1": 1.1},
         {"w": "my", "t0": 1.2, "t1": 1.5},
         {"w": "video", "t0": 5.1, "t1": 5.45},
         {"w": "today", "t0": 5.6, "t1": 6.1},
         {"w": "we're", "t0": 6.2, "t1": 6.5}]
kept = captions._drop_muted_words(WORDS, [[0.0, 5.5]])
check("words inside the mute window are gone, the rest survive",
      [w["w"] for w in kept] == ["today", "we're"])
check("no mutes means untouched words",
      captions._drop_muted_words(WORDS, None) is WORDS)

events = [{"start": 3.0, "end": 6.0, "text": "held into the window"},
          {"start": 3.0, "end": 25.0, "text": "spans the whole window"},
          {"start": 21.0, "end": 24.0, "text": "after"}]
clamped = captions._clamp_event_ends_to_mutes(
    [dict(e) for e in events], [[5.5, 20.0]])
check("display padding is pulled back to the window edge",
      clamped[0]["end"] == 5.5)
check("an event spanning the whole window is never desynced",
      clamped[1]["end"] == 25.0)
check("events past the window keep their timing",
      clamped[2]["start"] == 21.0 and clamped[2]["end"] == 24.0)

gone = captions._clamp_event_ends_to_mutes(
    [{"start": 5.5, "end": 5.52, "text": "sliver"}], [[5.5, 20.0]])
check("a clamped-away sliver is dropped, not written", gone == [])

dictated = [{"start": 1.0, "end": 4.0, "text": "over the effect"},
            {"start": 30.0, "end": 33.0, "text": "later"}]
check("dictated caption items keep whole-event muting",
      [e["text"] for e in captions.apply_mutes(dictated, [[0.0, 5.5]])]
      == ["later"])

print(f"\nALL {PASS} CHECKS PASSED")
