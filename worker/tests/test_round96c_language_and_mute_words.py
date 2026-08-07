"""Round 96c — same-script language flips + word-level caption mutes
(project 384, 2026-08-07).

One session, two defects the developer watched a user hit:

  * The user wrote "Cut the silences, add big captions, and a punchy zoom
    on the key line" — English — and the turn's reply came back in GERMAN.
    Round 85's guard measures SCRIPT, and English->German is Latin->Latin:
    blind by construction (the en<->pt walls have the same hole). Now
    function-word fingerprints catch same-script flips: disjoint marker
    lists (a word on two lists votes for neither), a 3-hit 2:1-dominance
    threshold on each side, and the reply must contain ZERO of the user's
    own markers — quote a foreign word and nothing fires.

  * Captions vanished for seconds past the muted title window: apply_mutes
    dropped WHOLE caption events, so the block straddling 5.5s died with
    the mute and captions resumed one block late. Transcript mutes now act
    at the WORD level before grouping (same stage as filler removal), with
    only display-padding ends pulled back to the window edge.

Pins:
  * _LANG_MARKERS are pairwise disjoint and each language keeps enough
    words to reach the 3-hit threshold.
  * _language_flip catches the session's real English->German pair, stays
    silent on same-language replies, quoted foreign words, and cross-script
    cases the script check already owns.
  * _drop_muted_words / _clamp_event_ends_to_mutes mute exactly the window;
    apply_mutes (dictated caption items) is unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_loop                                               # noqa: E402
import captions                                                 # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


# ── the marker tables ────────────────────────────────────────────────

flat = [w for ws in agent_loop._LANG_MARKERS.values() for w in ws]
check("marker lists are pairwise disjoint", len(flat) == len(set(flat)))
check("every language keeps enough markers to clear the 3-hit bar",
      all(len(ws) >= 8 for ws in agent_loop._LANG_MARKERS.values()))

# ── the session's real flip ──────────────────────────────────────────

USER = ("Edit this into a 10-12 minute cozy Sims 4 youtube video. Remove "
        "long pauses and uninteresting sections. "
        "Cut the silences, add big captions, and a punchy zoom on the key "
        "line")
GERMAN = ("Die langen Pausen wurden gekürzt und die Captions auf große "
          "Podcast-Captions umgestellt. Der gewünschte Punch-Zoom auf der "
          "Hazel-Willowbrook-Enthüllung konnte noch nicht gesetzt werden, "
          "weil das Zoom-Tool die Zielangaben wiederholt abgelehnt hat; "
          "die Vorschau zeigt die bisherigen Zooms korrekt.")
check("project 384's English->German reply is caught",
      agent_loop._language_flip(USER, USER, GERMAN) == ("words", "en", "de"))

ENGLISH_OK = ("The long pauses were cut and the captions switched to big "
              "podcast captions. The punch zoom is set on the key line and "
              "the preview shows it landing correctly.")
check("an English reply to an English user is untouched",
      agent_loop._language_flip(USER, USER, ENGLISH_OK) is None)

QUOTED = ("The clip you uploaded is named 'Das Boot und die Männer der "
          "See' and the title now uses it. The zoom was kept on the key "
          "line, and the captions are the big podcast style you asked for.")
check("quoting German inside an English reply never fires",
      agent_loop._language_flip(USER, USER, QUOTED) is None)

PT_USER = ("Edite este vídeo de forma cinematográfica. Aplique câmera "
           "lenta nos momentos mais emocionantes, substitua a música por "
           "uma trilha alegre, faça cortes suaves com a música e finalize "
           "com um fade-out. Retirar a letra da musica")
PT_REPLY = ("A música anterior foi removida e substituída, mantendo a "
            "edição com 142,8 segundos. O preview foi verificado e está "
            "com o texto final correto, sem sobreposição de legendas.")
check("a Portuguese reply to a Portuguese user is untouched",
      agent_loop._language_flip(PT_USER, PT_USER, PT_REPLY) is None)
check("a German reply to that Portuguese user is caught",
      agent_loop._language_flip(PT_USER, PT_USER, GERMAN)
      == ("words", "pt", "de"))

CYRILLIC = ("Длинные паузы вырезаны, субтитры переключены на крупный "
            "стиль подкаста, зум установлен на ключевой фразе.")
flip = agent_loop._language_flip(USER, USER, CYRILLIC)
check("cross-script flips still belong to the script check",
      flip is not None and flip[0] == "script")

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
