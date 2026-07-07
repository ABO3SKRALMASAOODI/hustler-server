"""Pure-logic unit tests (no ffmpeg, no DB, no network).

Run from the worker/ directory:  python tests/test_units.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import captions as caplib                                    # noqa: E402
from renderer import build_filtergraph                       # noqa: E402
from schemas import (EDLValidationError, default_edl,        # noqa: E402
                     describe_edl, edl_signature, output_duration,
                     validate_edl)
from timeline import Timeline, merge_spans                   # noqa: E402
from transcribe import group_sentences                       # noqa: E402
from schemas import Word                                     # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def expect_reject(name, edl, duration):
    try:
        validate_edl(edl, duration)
    except EDLValidationError as e:
        check(f"{name} (rejected: {str(e)[:60]})", True)
        return
    raise AssertionError(f"FAIL: {name} should have been rejected")


print("== EDL validation ==")
d = default_edl(60.0)
check("default keeps whole video", d["keep"] == [[0.0, 60.0]])

ok = validate_edl({"keep": [[10.0, 5.0 + 10.0], [20, 30]],
                   "captions": {"mode": "from_transcript"}}, 60)
check("valid EDL passes", output_duration(ok.keep) == 15.0)

ok2 = validate_edl({"keep": [[20, 30], [0, 10]]}, 60)
check("unsorted input is sorted", ok2.keep == [[0.0, 10.0], [20.0, 30.0]])

expect_reject("overlap", {"keep": [[0, 10], [5, 20]]}, 60)
expect_reject("negative", {"keep": [[-1, 10]]}, 60)
expect_reject("beyond duration", {"keep": [[0, 75]]}, 60)
expect_reject("tiny span", {"keep": [[1, 1.02]]}, 60)
expect_reject("empty keep", {"keep": []}, 60)
expect_reject("caption out of range",
              {"keep": [[0, 60]],
               "captions": [{"text": "hi", "start": 59, "end": 70}]}, 60)
expect_reject("music gain crazy",
              {"keep": [[0, 60]],
               "music": [{"storage_key": "music/1/a.mp3", "start": 0,
                          "end": 10, "gain_db": 40}]}, 60)
expect_reject("volume span reversed",
              {"keep": [[0, 60]],
               "volume": [{"start": 10, "end": 9, "gain_db": -5}]}, 60)

desc = describe_edl(ok.model_dump(), 60)
check("describe mentions segments", "2 segments" in desc)

print("== Timeline mapping ==")
tl = Timeline([[0, 10], [20, 30], [40, 45]])
check("out duration", tl.out_duration == 25.0)
check("map inside seg1", tl.src_to_out(5.0) == 5.0)
check("map inside seg2", tl.src_to_out(25.0) == 15.0)
check("map inside seg3", tl.src_to_out(42.0) == 22.0)
check("cut region maps to None", tl.src_to_out(15.0) is None)
spans = tl.span_to_out(8.0, 22.0)
check("span crossing a cut splits", spans == [(8.0, 10.0), (10.0, 12.0)])
check("fully cut span is empty", tl.span_to_out(11.0, 19.0) == [])
check("merge_spans merges close",
      merge_spans([(0, 1), (1.1, 2), (5, 6)], gap=0.3) == [(0, 2), (5, 6)])

words = [{"w": "hello", "t0": 1.0, "t1": 1.4},
         {"w": "world", "t0": 15.0, "t1": 15.5},   # cut
         {"w": "again", "t0": 21.0, "t1": 21.5}]
kept = tl.kept_words(words)
check("kept_words drops cut words",
      [w["w"] for w in kept] == ["hello", "again"])
check("kept word remapped to output time", abs(kept[1]["t0"] - 11.0) < 0.01)

print("== Sentence grouping ==")
ws = [Word(w="One", t0=0.0, t1=0.3), Word(w="two.", t0=0.4, t1=0.7),
      Word(w="Three", t0=3.0, t1=3.3),   # >1s gap forces new sentence anyway
      Word(w="four", t0=3.4, t1=3.6)]
sents = group_sentences(ws)
check("splits on punctuation", sents[0].text == "One two.")
check("ids sequential", [s.id for s in sents] == ["s1", "s2"])
check("word index ranges", (sents[0].wi0, sents[0].wi1) == (0, 1))

print("== Captions ==")
out_words = [{"w": "hello", "t0": 0.0, "t1": 0.4},
             {"w": "this", "t0": 0.5, "t1": 0.8},
             {"w": "is", "t0": 0.9, "t1": 1.0},
             {"w": "a", "t0": 1.1, "t1": 1.2},
             {"w": "really-long-word-that-keeps-going-on", "t0": 1.3, "t1": 2.0},
             {"w": "and", "t0": 2.1, "t1": 2.3},
             {"w": "more", "t0": 2.4, "t1": 2.6},
             {"w": "words", "t0": 2.7, "t1": 3.0},
             {"w": "to", "t0": 3.1, "t1": 3.2},
             {"w": "overflow", "t0": 3.3, "t1": 3.8},
             {"w": "the", "t0": 3.9, "t1": 4.0},
             {"w": "event", "t0": 4.1, "t1": 4.5},
             {"w": "limit", "t0": 4.6, "t1": 5.0}]
events = caplib.events_from_transcript(out_words)
check("multiple events created", len(events) >= 2)
check("events start at first word", abs(events[0]["start"] - 0.0) < 0.01)
for ev in events:
    for line in ev["text"].split(r"\N"):
        check(f"line <=42 chars ('{line[:20]}...')", len(line) <= 42)

with tempfile.TemporaryDirectory() as td:
    p = caplib.write_ass(events, os.path.join(td, "t.ass"))
    content = open(p).read()
    check("ass has header", "[Events]" in content and "Dialogue:" in content)

tl2 = Timeline([[0, 10]])
evs = caplib.events_from_items(
    [{"text": "shown", "start": 2, "end": 4},
     {"text": "cut away", "start": 12, "end": 14}], tl2)
check("explicit caption in cut region dropped", len(evs) == 1)
check("explicit caption kept + mapped", evs[0]["start"] == 2.0)

print("== Filtergraph builder ==")
edl = validate_edl({"keep": [[0, 10], [20, 30]],
                    "captions": {"mode": "from_transcript"},
                    "volume": [{"start": 2, "end": 4, "gain_db": -6}]},
                   60).model_dump()
tl3 = Timeline(edl["keep"])
index = {"video": {"duration": 60}, "words": [], "sentences":
         [{"t0": 1.0, "t1": 3.0}, {"t0": 22.0, "t1": 25.0}]}
g = build_filtergraph(edl, 60.0, True, tl3, "/tmp/x.ass", [], index,
                      preview=True)
check("graph has split", "split=2" in g and "asplit=2" in g)
check("graph trims both segments",
      "trim=start=0.000:end=10.000" in g and
      "trim=start=20.000:end=30.000" in g)
check("graph concats", "concat=n=2:v=1:a=1" in g)
check("graph burns subtitles", "subtitles=filename='/tmp/x.ass'" in g)
check("graph applies volume automation in source time",
      "volume=-6.0dB:enable='between(t,2.00,4.00)'" in g)
check("preview scales to 480", "min(480" in g)
check("yuv420p output", "format=yuv420p" in g)

g2 = build_filtergraph(edl, 60.0, True, tl3, None,
                       [(1, {"storage_key": "music/1/a.mp3", "start": 0.0,
                             "end": 15.0, "gain_db": -18, "duck": True})],
                       index, preview=False)
check("music at t=0 has no adelay (portability)", "adelay" not in g2)
check("ducking windows present", "volume=-12.0dB:enable=" in g2)
check("amix normalize off", "amix=inputs=2:duration=first:normalize=0" in g2)

g2b = build_filtergraph(edl, 60.0, True, tl3, None,
                        [(1, {"storage_key": "music/1/a.mp3", "start": 3.0,
                              "end": 15.0, "gain_db": -18, "duck": False})],
                        index, preview=False)
check("music at t=3 delayed into the output timeline",
      ",adelay=3000:all=1" in g2b)

edl_single = validate_edl({"keep": [[5, 25]]}, 60).model_dump()
g3 = build_filtergraph(edl_single, 60.0, False, Timeline(edl_single["keep"]),
                       None, [], index, preview=True)
check("single segment skips split", "split=" not in g3)
check("silent source uses lavfi input label", "[1:a]" in g3)

print("== Caption styling (issue 2) ==")
styled = validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript", "max_words_per_caption": 3,
                  "style": {"color": "#ff0000", "size": "l",
                            "position": "top"}}}, 60).model_dump()
check("style normalized to upper hex",
      styled["captions"]["style"]["color"] == "#FF0000")
expect_reject("bad hex color",
              {"keep": [[0, 60]],
               "captions": {"mode": "from_transcript",
                            "style": {"color": "red"}}}, 60)
expect_reject("max words out of range",
              {"keep": [[0, 60]],
               "captions": {"mode": "from_transcript",
                            "max_words_per_caption": 40}}, 60)

legacy = validate_edl({"keep": [[0, 60]],
                       "captions": {"mode": "from_transcript",
                                    "style": "default"}}, 60).model_dump()
check("legacy string style coerced to defaults",
      legacy["captions"]["style"] is None)

sty_words = [{"w": "one", "t0": 0.0, "t1": 0.3},
             {"w": "two", "t0": 0.4, "t1": 0.7},
             {"w": "three", "t0": 0.8, "t1": 1.1},
             {"w": "four", "t0": 1.2, "t1": 1.5},
             {"w": "five", "t0": 1.6, "t1": 1.9}]
sty_index = {"video": {"duration": 60}, "words": sty_words, "sentences": []}
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(styled, sty_index, Timeline(styled["keep"]),
                         os.path.join(td, "styled.ass"))
    content = open(p).read()
    # #FF0000 in ASS &H00BBGGRR order is &H000000FF
    style_row = next(l for l in content.splitlines()
                     if l.startswith("Style: Default"))
    fields = style_row.split(",")
    check("PrimaryColour is &H000000FF (red in BBGGRR)",
          fields[3] == "&H000000FF")
    check("Fontsize 58 for size 'l'", fields[2] == "58")
    check("Alignment 8 for position 'top'", fields[18] == "8")
    dialogues = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    check("chunking produced 2 events for 5 words @3",
          len(dialogues) == 2)
    for d in dialogues:
        n_words = len(d.split(",,0,0,0,,")[1].replace(r"\N", " ").split())
        check(f"event has <= 3 words ({n_words})", n_words <= 3)
    check("first event starts at the real first-word time",
          ",0:00:00.00," in dialogues[0])
    check("second event starts at word 4's real timestamp (1.2s)",
          ",0:00:01.20," in dialogues[1])

with tempfile.TemporaryDirectory() as td:
    # per-item override on manual captions
    items_edl = validate_edl(
        {"keep": [[0, 60]],
         "captions": [{"text": "plain", "start": 1, "end": 3},
                      {"text": "loud", "start": 5, "end": 7,
                       "style": {"color": "#00FF00", "size": "s"}}]},
        60).model_dump()
    p = caplib.build_ass(items_edl, sty_index, Timeline(items_edl["keep"]),
                         os.path.join(td, "items.ass"))
    content = open(p).read()
    check("override created a second named style",
          "Style: VS1," in content)
    check("override colour green in BBGGRR", "&H0000FF00" in content)
    check("plain item uses Default", ",Default,,0,0,0,,plain" in content)
    check("styled item uses VS1", ",VS1,,0,0,0,,loud" in content)

print("== Cut-before-caption remap (issue 4) ==")
cut_tl = Timeline([[10.0, 20.0]])
with tempfile.TemporaryDirectory() as td:
    remap_edl = validate_edl(
        {"keep": [[10, 20]],
         "captions": [
             {"text": "shifted", "start": 12, "end": 14},   # -> 2..4 out
             {"text": "straddles", "start": 5, "end": 15},  # clipped -> 0..5
             {"text": "gone", "start": 0, "end": 8}]},      # fully cut
        60).model_dump()
    p = caplib.build_ass(remap_edl, sty_index, cut_tl,
                         os.path.join(td, "remap.ass"))
    content = open(p).read()
    check("caption after a cut appears at remapped output time (2s not 12s)",
          "Dialogue: 0,0:00:02.00,0:00:04.00" in content)
    check("caption straddling the cut is clipped to the kept piece",
          "Dialogue: 0,0:00:00.00,0:00:05.00" in content)
    check("fully-cut caption dropped", "gone" not in content)
    check("no caption at source time 12s", "0:00:12" not in content)

# music + volume + ducking remap through the filtergraph (output vs source)
remap_edl2 = validate_edl(
    {"keep": [[10, 20], [40, 50]],
     "volume": [{"start": 12, "end": 14, "gain_db": -6}],
     "captions": None}, 60).model_dump()
tl_r = Timeline(remap_edl2["keep"])
speech_index = {"video": {"duration": 60}, "words": [],
                "sentences": [{"t0": 41.0, "t1": 43.0}]}   # out: 11..13
g_r = build_filtergraph(remap_edl2, 60.0, True, tl_r, None,
                        [(1, {"storage_key": "music/1/a.mp3", "start": 5.0,
                              "end": 18.0, "gain_db": -18, "duck": True})],
                        speech_index, preview=False)
check("volume stays in SOURCE time (pre-trim)",
      "volume=-6.0dB:enable='between(t,12.00,14.00)'" in g_r)
check("music positioned in OUTPUT time (adelay 5000)",
      ",adelay=5000:all=1" in g_r)
check("duck window remapped source 41-43 -> output 11-13",
      "between(t,11.00,13.00)" in g_r)

print("== Sentence caps (issue 6) ==")
run_on = [Word(w=f"w{i}", t0=i * 0.4, t1=i * 0.4 + 0.3) for i in range(13)]
sents = group_sentences(run_on)
check("13 unpunctuated words split into 2 sentences", len(sents) == 2)
check("no sentence over 12 words",
      max(s.wi1 - s.wi0 + 1 for s in sents) <= 12)

slow = [Word(w=f"s{i}", t0=i * 1.4, t1=i * 1.4 + 1.35) for i in range(6)]
sents = group_sentences(slow)
check("long-duration speech split by the 6s cap",
      all(s.t1 - s.t0 <= 6.0 for s in sents) and len(sents) >= 2)

gappy = [Word(w="a", t0=0.0, t1=0.3), Word(w="b", t0=1.05, t1=1.3),
         Word(w="c", t0=1.4, t1=1.7)]
sents = group_sentences(gappy)
check("0.75s pause splits a sentence",
      len(sents) == 2 and sents[0].text == "a")

print("== Word-boundary protection ==")
import audit                                                  # noqa: E402
import agent_tools                                            # noqa: E402
from agent_loop import _reply_violations                      # noqa: E402
from agent_tools import (get_words, get_transcript,           # noqa: E402
                         merge_caption_style, _parse_partial_style)


class StubCtx:
    def __init__(self, index, duration):
        self.index = index
        self.duration = duration

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)


check("subtract middle", audit.subtract_spans([[0, 10]], [[2, 3]]) ==
      [(0.0, 2.0), (3.0, 10.0)])
check("subtract across spans",
      audit.subtract_spans([[0, 10], [20, 30]], [[5, 25]]) ==
      [(0.0, 5.0), (25.0, 30.0)])
check("subtract everything", audit.subtract_spans([[2, 3]], [[0, 10]]) == [])

WORD = {"w": "ridiculous", "t0": 28.33, "t1": 29.21}
words_r3 = [{"w": "is", "t0": 27.9, "t1": 28.1}, WORD]
sil_r3 = [[26.5, 27.8]]
hits = audit.midword_boundaries([[0.0, 28.81]], words_r3, 60.0)
check("28.81 lands inside 'ridiculous'",
      len(hits) == 1 and hits[0]["word"] == "ridiculous")
check("word-edge boundary is clean",
      audit.midword_boundaries([[0.0, 28.33]], words_r3, 60.0) == [])
check("video-end boundary excluded",
      audit.midword_boundaries([[0.0, 60.0]], words_r3, 60.0) == [])
warn = audit.boundary_warning_lines([[0.0, 28.81]], words_r3, sil_r3, 60.0)
check("warning names the word and both edges",
      "ridiculous" in warn[0] and "28.33 (word start)" in warn[0]
      and "29.21 (word end)" in warn[0])
check("warning offers the silence midpoint", "27.15" in warn[0])

check("snap: keep end moves outward to word end",
      audit.snap_keep_to_words([[0.0, 28.81]], words_r3, 60.0) ==
      [[0.0, 29.21]])
check("snap: keep start moves outward to word start",
      audit.snap_keep_to_words([[28.81, 40.0]], words_r3, 60.0) ==
      [[28.33, 40.0]])
check("snap merges spans that now overlap",
      audit.snap_keep_to_words([[0.0, 28.81], [28.9, 40.0]], words_r3, 60.0)
      == [[0.0, 40.0]])

idx_r3 = {"silences": [[0.0, 2.83]],
          "sentences": [
              {"id": "s1", "t0": 0.5, "t1": 1.5, "text": "hello world"},
              {"id": "s5", "t0": 6.33, "t1": 8.13, "text": "hello world"}]}
w = audit.regression_warnings([[2.83, 60.0]], [[0.0, 60.0]], idx_r3)
check("re-included leading silence flagged",
      w and "re-includes 0.00-2.83" in w[0] and "leading silence" in w[0])
w = audit.regression_warnings([[0.0, 6.0]], [[0.0, 8.2]], idx_r3)
check("re-included duplicate sentence flagged",
      w and "s5 is a verbatim duplicate of s1" in w[0])
check("no warning when nothing re-included",
      audit.regression_warnings([[0.0, 60.0]], [[0.0, 30.0]], idx_r3) == [])

print("== get_words ==")
long_words = [{"w": f"w{i}", "t0": i, "t1": i + 0.4} for i in range(100)]
sctx = StubCtx({"words": long_words,
                "sentences": [{"id": "s1", "t0": 0, "t1": 3,
                               "text": "w0 w1 w2"}]}, 120.0)
r = get_words(sctx, 0, 120)
check("over-cap range rejected with guidance",
      r.startswith("REJECTED") and "60" in r)
r = get_words(sctx, 10, 20)
check("word timings returned", "10.00-10.40 w10" in r)
check("get_transcript points at get_words",
      "get_words" in get_transcript(sctx, 0, 5))

print("== Reply honesty detectors ==")
LIE = ("Cuts applied at the 23.91 silence midpoint and the final phrase is "
       "preserved. Captions are now red (#FF0000). Preview rendered.")
v = _reply_violations(LIE, wrote=False, previewed=False)
check("turn-4 lie trips both detectors", len(v) == 2)
v = _reply_violations("The EDL didn't change — captions were already "
                      "word-timed.", wrote=True, previewed=True)
check("denial after real writes trips the deny detector", len(v) == 1)
check("honest 'nothing was changed' is clean",
      _reply_violations("I couldn't find that phrase, so nothing was "
                        "changed.", wrote=False, previewed=False) == [])
check("honest 'no preview was rendered' is clean",
      _reply_violations("No preview was rendered because the EDL is "
                        "unchanged.", wrote=False, previewed=False) == [])
check("honest summary after real writes is clean",
      _reply_violations("Removed the dead air; preview rendered on the "
                        "right.", wrote=True, previewed=True) == [])

print("== set_caption_style merge ==")
p = _parse_partial_style({"color": "#ff0000"})
check("partial style keeps only provided keys", p == {"color": "#FF0000"})
check("unknown style field rejected",
      isinstance(_parse_partial_style({"font": "arial"}), str))
merged = merge_caption_style({"mode": "from_transcript",
                              "max_words_per_caption": 3,
                              "style": {"position": "top"}},
                             {"color": "#FF0000"})
check("merge preserves max_words + position",
      merged["max_words_per_caption"] == 3 and
      merged["style"] == {"position": "top", "color": "#FF0000"})
merged = merge_caption_style([{"text": "a", "start": 0, "end": 1,
                               "style": {"size": "l"}},
                              {"text": "b", "start": 2, "end": 3}],
                             {"color": "#00FF00"})
check("manual items each get the patch, overrides kept",
      merged[0]["style"] == {"size": "l", "color": "#00FF00"} and
      merged[1]["style"] == {"color": "#00FF00"})

print("== No-op detection (issue 3) ==")
a = validate_edl({"keep": [[0, 30], [40, 60]],
                  "captions": {"mode": "from_transcript"}}, 60).model_dump()
b = validate_edl({"captions": {"mode": "from_transcript"},
                  "keep": [[40, 60], [0, 30]]}, 60).model_dump()
check("identical EDLs (different key/segment order) have equal signatures",
      edl_signature(a) == edl_signature(b))
c = validate_edl({"keep": [[0, 30], [40, 60]],
                  "captions": {"mode": "from_transcript",
                               "max_words_per_caption": 3}}, 60).model_dump()
check("real change alters the signature",
      edl_signature(a) != edl_signature(c))

print(f"\nALL {PASS} CHECKS PASSED")
