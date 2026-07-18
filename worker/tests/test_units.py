"""Pure-logic unit tests (no ffmpeg, no DB, no network).

Run from the worker/ directory:  python tests/test_units.py
"""

import os
import re
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
                       None, [], index, preview=True, silence_idx=1)
check("single segment skips split", "split=" not in g3)
check("silent source uses lavfi input label", "[1:a]" in g3)
check("plain cut keeps the cheap graph (no per-segment scaling)",
      "force_original_aspect_ratio" not in g3)

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
    check("Fontsize 52 for size 'l' at base res", fields[2] == "52")
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

print("== Output frame (round 4, issue 1) ==")
from renderer import frame_dims                               # noqa: E402

check("9:16 from 1080p landscape", frame_dims(1920, 1080, "9:16")
      == (1080, 1920))
check("16:9 from 1080p is identity", frame_dims(1920, 1080, "16:9")
      == (1920, 1080))
check("1:1 from 1080p", frame_dims(1920, 1080, "1:1") == (1080, 1080))
check("4:5 from 1080p", frame_dims(1920, 1080, "4:5") == (1080, 1350))
check("9:16 from a square source caps at the source long side",
      frame_dims(1000, 1000, "9:16") == (562, 1000))
check("source ratio untouched", frame_dims(1280, 720, None) == (1280, 720))
w_odd, h_odd = frame_dims(1281, 721, None)
check("odd dims rounded even", w_odd % 2 == 0 and h_odd % 2 == 0)

f_edl = validate_edl({"keep": [[0, 60]],
                      "frame": {"ratio": "9:16", "mode": "crop"}},
                     60).model_dump()
check("frame survives validation", f_edl["frame"]["ratio"] == "9:16")
check("explicit source frame normalizes to None",
      validate_edl({"keep": [[0, 60]],
                    "frame": {"ratio": "source"}}, 60).model_dump()["frame"]
      is None)
expect_reject("bad ratio", {"keep": [[0, 60]],
                            "frame": {"ratio": "3:7"}}, 60)
check("describe mentions the frame",
      "frame 9:16 (crop)" in describe_edl(f_edl, 60))

old_dump = {"keep": [[0.0, 30.0]], "captions": None, "music": [],
            "volume": []}   # what a pre-round-4 EDL row looks like
new_dump = validate_edl({"keep": [[0, 30]]}, 60).model_dump()
check("old EDL rows compare NO CHANGE against new dumps (frame/inserts keys)",
      edl_signature(old_dump) == edl_signature(new_dump))

print("== Inserts + voiceover validation (round 4, issue 4) ==")
ins_edl = validate_edl(
    {"keep": [[0, 10], [20, 30]],
     "inserts": [{"id": "ins1", "asset_key": "clips/1/a.mp4",
                  "kind": "video", "at_output_s": 10.0, "duration_s": 2.0}],
     "voiceover": [{"id": "vo1", "asset_key": "music/1/v.mp3",
                    "start_output_s": 5.0}]}, 60).model_dump()
check("insert at a boundary passes", ins_edl["inserts"][0]["at_output_s"]
      == 10.0)
check("voiceover default gain/duck",
      ins_edl["voiceover"][0]["gain_db"] == 0.0 and
      ins_edl["voiceover"][0]["duck_others"] is True)
check("describe mentions inserts + voiceover",
      "inserts x1 (+2.0s)" in describe_edl(ins_edl, 60) and
      "voiceover x1" in describe_edl(ins_edl, 60))
expect_reject("insert off-boundary",
              {"keep": [[0, 10], [20, 30]],
               "inserts": [{"id": "i", "asset_key": "k", "kind": "image",
                            "at_output_s": 5.0, "duration_s": 3.0}]}, 60)
expect_reject("duplicate insert ids",
              {"keep": [[0, 10]],
               "inserts": [
                   {"id": "i", "asset_key": "k", "kind": "image",
                    "at_output_s": 0.0, "duration_s": 3.0},
                   {"id": "i", "asset_key": "k2", "kind": "image",
                    "at_output_s": 10.0, "duration_s": 3.0}]}, 60)
expect_reject("voiceover past the program end",
              {"keep": [[0, 10]],
               "voiceover": [{"id": "v", "asset_key": "k",
                              "start_output_s": 55.0}]}, 60)
check("music validated against the PROGRAM duration (keep + inserts)",
      validate_edl({"keep": [[0, 10]],
                    "inserts": [{"id": "i", "asset_key": "k",
                                 "kind": "image", "at_output_s": 10.0,
                                 "duration_s": 5.0}],
                    "music": [{"storage_key": "m", "start": 0,
                               "end": 14.0}]}, 60)
      .model_dump()["music"][0]["end"] == 14.0)

print("== Timeline with inserts (both directions) ==")
tli = Timeline([[0, 10], [20, 30]],
               [{"at_output_s": 10.0, "duration_s": 5.0}])
check("insert extends the program", tli.out_duration == 25.0)
check("src before the insert unshifted", tli.src_to_out(5.0) == 5.0)
check("src after the insert shifted by its duration",
      tli.src_to_out(25.0) == 20.0)
check("out inside main maps back", tli.out_to_src(5.0) == 5.0)
check("out inside the INSERT maps to None", tli.out_to_src(12.0) is None)
check("out after the insert maps back shifted", tli.out_to_src(20.0) == 25.0)
check("insert final position", tli.insert_positions() == [(10.0, 5.0)])
tli0 = Timeline([[0, 10]], [{"at_output_s": 0.0, "duration_s": 3.0}])
check("insert at 0 shifts everything", tli0.src_to_out(0.0) == 3.0 and
      tli0.out_to_src(1.0) is None and tli0.out_to_src(4.0) == 1.0)
check("kept words shift around an insert",
      Timeline([[0, 10]], [{"at_output_s": 0.0, "duration_s": 3.0}])
      .kept_words([{"w": "hi", "t0": 1.0, "t1": 1.4}])[0]["t0"] == 4.0)

print("== Filtergraph with frame + insert + voiceover ==")
edl_i = validate_edl(
    {"keep": [[0, 10], [20, 30]],
     "frame": {"ratio": "9:16", "mode": "crop"},
     "inserts": [{"id": "ins1", "asset_key": "images/1/a.png",
                  "kind": "image", "at_output_s": 10.0,
                  "duration_s": 3.0}]}, 60).model_dump()
tl_i = Timeline(edl_i["keep"], edl_i["inserts"])
g_i = build_filtergraph(
    edl_i, 60.0, True, tl_i, None, [], index, preview=False,
    W=720, H=1280, fps=30.0, frame_mode="crop",
    insert_inputs=[(2, edl_i["inserts"][0], False)],
    vo_inputs=[(3, {"id": "vo1", "asset_key": "m", "start_output_s": 1.0,
                    "gain_db": 0.0, "duck_others": True}, 4.0)],
    silence_idx=1)
check("every block normalized to the frame",
      g_i.count("scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280") == 3)
check("insert spliced between the segments",
      "[v_seg0][a_seg0][v_ins0][a_ins0][v_seg1][a_seg1]concat=n=3:v=1:a=1"
      in g_i)
check("image insert audio comes from the anullsrc slice",
      "[sil0]atrim=start=0:end=3.000" in g_i)
check("program audio ducks -12dB under the voiceover window",
      "volume=-12.0dB:enable='between(t,1.00,5.00)'" in g_i)
check("voiceover delayed to its output position and mixed",
      ",adelay=1000:all=1" in g_i and "amix=inputs=2" in g_i)

g_pb = build_filtergraph(
    validate_edl({"keep": [[0, 10]],
                  "frame": {"ratio": "1:1", "mode": "pad_blur"}},
                 60).model_dump(),
    60.0, True, Timeline([[0, 10]]), None, [], index, preview=False,
    W=720, H=720, fps=30.0, frame_mode="pad_blur")
check("pad_blur builds the blurred-backdrop overlay",
      "boxblur=20" in g_pb and "overlay=(W-w)/2:(H-h)/2" in g_pb)
g_pad = build_filtergraph(
    validate_edl({"keep": [[0, 10]]}, 60).model_dump(),
    60.0, True, Timeline([[0, 10]]), None, [], index, preview=False,
    W=720, H=720, fps=30.0, frame_mode="pad")
check("pad mode letterboxes with centered black bars",
      "pad=720:720:(ow-iw)/2:(oh-ih)/2:color=black" in g_pad)

print("== Captions at 9:16 with middle position (issues 1+3) ==")
mid_edl = validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"position": "middle"}}}, 60).model_dump()
check("middle position accepted",
      mid_edl["captions"]["style"]["position"] == "middle")
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(mid_edl, sty_index, Timeline(mid_edl["keep"]),
                         os.path.join(td, "mid.ass"), play_res=(1080, 1920))
    content = open(p).read()
    check("PlayRes matches the 9:16 output frame",
          "PlayResX: 1080" in content and "PlayResY: 1920" in content)
    style_row = next(l for l in content.splitlines()
                     if l.startswith("Style: Default"))
    fields = style_row.split(",")
    check("Alignment 5 for middle", fields[18] == "5")
    check("middle ignores MarginV (0)", fields[21] == "0")
    # round 7: fonts scale with the LARGER frame factor so 9:16 verticals
    # get readable text (width-only scaling left 'm' at 34px on a 1920-tall
    # frame — ~1.8% of the height).
    check("font scales with frame height on verticals (40 -> 107 @1920/720)",
          fields[2] == "107")
with tempfile.TemporaryDirectory() as td:
    top_edl = validate_edl(
        {"keep": [[0, 60]],
         "captions": {"mode": "from_transcript",
                      "style": {"position": "top"}}}, 60).model_dump()
    p = caplib.build_ass(top_edl, sty_index, Timeline(top_edl["keep"]),
                         os.path.join(td, "top.ass"), play_res=(1080, 1920))
    fields = next(l for l in open(p).read().splitlines()
                  if l.startswith("Style: Default")).split(",")
    check("top MarginV scales with frame height (40 -> 107 @1920/720)",
          fields[21] == "107")

print("== Capabilities digest (issue 2) ==")
digest = agent_tools.capabilities_digest()
for tool_name in agent_tools.WRITE_TOOLS:
    if agent_tools._tool_disabled(tool_name):
        continue    # service-gated tools are covered in their own section
    check(f"digest covers {tool_name}", f"- {tool_name}(" in digest)
check("digest is write-tools only", "get_transcript(" not in digest)

print("== Round-4 honesty: 9:16 fabrication + fallback ==")
from agent_loop import (FALLBACK_REPLY, _nearest_alternative)  # noqa: E402
LIE916 = ("The video is now cropped to 9:16 (1080x1920) for TikTok with "
          "the subject centered. Preview attached.")
v = _reply_violations(LIE916, wrote=False, previewed=False)
check("9:16 fabrication trips edit + render detectors", len(v) == 2)
check("violation names the fabricated claim",
      any("is now cropped" in x for x in v))
v = _reply_violations("Captions are vertically centered now.",
                      wrote=False, previewed=False)
check("middle-position fabrication detected", len(v) == 1)
check("same sentence fine after a real write",
      _reply_violations("Captions are vertically centered now.",
                        wrote=True, previewed=True) == [])
check("fallback text never claims a change",
      _reply_violations(FALLBACK_REPLY, wrote=False, previewed=False) == [])
check("alternative hint for aspect requests",
      "output frame" in _nearest_alternative("make the video 9:16"))
check("alternative hint for caption requests",
      "podcast" in _nearest_alternative("move the captions to the middle")
      and "position" in _nearest_alternative("captions in a cool font"))
check("no hint when nothing matches",
      _nearest_alternative("do the thing") is None)

print("== Round-6 honesty: audio/volume claims ==")
LIEMUS = ("The music now plays only from 0.0 to 15.0 seconds in the output "
          "timeline and is cut thereafter. Captions remain large and "
          "word-chunked. Rendering preview now.")
v = _reply_violations(LIEMUS, wrote=False, previewed=False)
check("music fabrication trips edit + render detectors", len(v) == 2)
check("violation names the music claim",
      any("music now plays" in x for x in v))
v = _reply_violations("The captions are active and the music volume is "
                      "lowered by 6dB for better speech clarity.",
                      wrote=False, previewed=False)
check("volume-lowered fabrication detected", len(v) == 1)
check("'Rendering preview now' alone is a render claim",
      len(_reply_violations("Rendering preview now.",
                            wrote=True, previewed=False)) == 1)
check("honest offer to change music is clean",
      _reply_violations("I can make the music quieter or remove it — "
                        "which would you like?",
                        wrote=False, previewed=False) == [])
check("music claim fine after a real write",
      _reply_violations("The music is now lowered to -12dB.",
                        wrote=True, previewed=True) == [])
check("audio hint mentions gain control",
      "louder/quieter" in _nearest_alternative("lower the music volume"))

print("== Round-6 music tools ==")
import json                                                   # noqa: E402
import schemas                                                # noqa: E402
from agent_tools import (set_audio_gain, remove_music,        # noqa: E402
                         add_music, _frame_context)


class ToolCtx:
    def __init__(self, edl, asset=None, index=None):
        self._edl = {"version": 1, "json": edl}
        self.written = None
        self.db = self
        self.project_id = 1
        self._asset = asset
        # Real ctx always carries the video index; tests default to an empty
        # transcript (so caption honesty warnings fire — asserted below).
        self.index = index if index is not None else {"words": []}

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = edl
        return f"EDL v1 -> v2: {desc}"

    def run(self, fn, *a, **k):          # stands in for ctx.db.run
        return self._asset

MUS_EDL = {"keep": [[0.0, 30.0]],
           "music": [{"id": "mus1", "storage_key": "music/1/a.mp3",
                      "start": 0.0, "end": 30.0, "gain_db": -18.0,
                      "duck": True}],
           "voiceover": [{"id": "vo1", "asset_key": "music/1/a.mp3",
                          "start_output_s": 0.0, "gain_db": 0.0,
                          "duck_others": True}]}

tctx = ToolCtx(json.loads(json.dumps(MUS_EDL)))
r = set_audio_gain(tctx, "voiceover", "vo1", -12)
check("set_audio_gain lowers the voiceover item",
      tctx.written["voiceover"][0]["gain_db"] == -12.0 and
      "-12.0dB" in r)
tctx = ToolCtx(json.loads(json.dumps(MUS_EDL)))
r = set_audio_gain(tctx, "music", "mus1", -99)
check("set_audio_gain clamps to the gain floor",
      tctx.written["music"][0]["gain_db"] == -60.0)
r = set_audio_gain(ToolCtx(json.loads(json.dumps(MUS_EDL))),
                   "music", "nope", -6)
check("unknown id rejected listing existing ids",
      r.startswith("REJECTED") and "mus1" in r)
check("bad kind rejected",
      set_audio_gain(ToolCtx({}), "speech", "x", -6)
      .startswith("REJECTED"))

tctx = ToolCtx(json.loads(json.dumps(MUS_EDL)))
r = remove_music(tctx, "mus1")
check("remove_music removes the bed",
      tctx.written["music"] == [] and "removed music mus1" in r)
r = remove_music(ToolCtx(json.loads(json.dumps(MUS_EDL))), "musX")
check("remove_music unknown id lists existing",
      r.startswith("REJECTED") and "mus1" in r)

tctx = ToolCtx(json.loads(json.dumps(MUS_EDL)))
tctx._asset = {"kind": "music", "storage_key": "music/1/a.mp3", "meta": {}}
r = add_music(tctx, "music/1/a.mp3", 0, 15)
check("add_music assigns the next id",
      any(m.get("id") == "mus2" for m in tctx.written["music"]))
check("add_music warns when the file is also a voiceover",
      "WARNING" in r and "vo1" in r and "TWICE" in r)

print("== Round-6 letterbox-aware self-check ==")
check("pad frame context flags letterboxing as expected",
      "letterboxed" in _frame_context({"frame": {"ratio": "9:16",
                                                 "mode": "pad"}})
      and "EXPECTED" in _frame_context({"frame": {"ratio": "9:16",
                                                  "mode": "pad"}}))
check("pad_blur mentions blurred bars",
      "blurred" in _frame_context({"frame": {"ratio": "1:1",
                                             "mode": "pad_blur"}}))
check("crop frame context mentions the crop",
      "center-cropped to 9:16" in
      _frame_context({"frame": {"ratio": "9:16", "mode": "crop"}}))
check("no frame -> no context", _frame_context({}) == "")

print("== Round-6 MusicItem ids ==")
mus_ok = {"keep": [[0.0, 30.0]],
          "music": [{"storage_key": "m.mp3", "start": 0, "end": 10},
                    {"id": "mus1", "storage_key": "m.mp3",
                     "start": 10, "end": 20}]}
validated = schemas.validate_edl(mus_ok, 30.0)
check("legacy id-less music items still validate",
      validated.music[0].id is None and validated.music[1].id == "mus1")
try:
    schemas.validate_edl(
        {"keep": [[0.0, 30.0]],
         "music": [{"id": "mus1", "storage_key": "a", "start": 0, "end": 5},
                   {"id": "mus1", "storage_key": "b", "start": 5,
                    "end": 10}]}, 30.0)
    check("duplicate music ids rejected", False)
except schemas.EDLValidationError:
    check("duplicate music ids rejected", True)
old_dump = schemas.validate_edl(
    {"keep": [[0.0, 30.0]],
     "music": [{"storage_key": "m.mp3", "start": 0, "end": 10}]},
    30.0).model_dump()
check("id-less music dump stays signature-stable (id=None stripped)",
      schemas.edl_signature(old_dump) == schemas.edl_signature(
          {"keep": [[0.0, 30.0]],
           "music": [{"storage_key": "m.mp3", "start": 0.0, "end": 10.0,
                      "gain_db": -18.0, "duck": True}]}))

# ─── Round 7: caption sizes/dynamic, kept transcript, repetition audit ───────

print("== Caption xl + vertical sizing (round 7) ==")
xl_edl = schemas.validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"size": "xl"}}}, 60).model_dump()
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(xl_edl, sty_index, Timeline(xl_edl["keep"]),
                         os.path.join(td, "xl.ass"))
    fields = next(l for l in open(p).read().splitlines()
                  if l.startswith("Style: Default")).split(",")
    check("size xl accepted and 68px at base res", fields[2] == "68")
    p = caplib.build_ass(xl_edl, sty_index, Timeline(xl_edl["keep"]),
                         os.path.join(td, "xl916.ass"),
                         play_res=(1080, 1920))
    fields = next(l for l in open(p).read().splitlines()
                  if l.startswith("Style: Default")).split(",")
    check("xl on 9:16 1080x1920 is 181px (~9.5% of height)",
          fields[2] == "181")
check("line width budget shrinks on narrow frames (l @1080x1920)",
      caplib.line_chars_for({"size": "l"}, (1080, 1920)) == 13)
check("line width budget stays 42 at base res",
      caplib.line_chars_for({"size": "m"}, (1280, 720)) == 42)

print("== Dynamic word-pop captions (round 7) ==")
dyn_edl = schemas.validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"size": "xl", "dynamic": True}}}, 60).model_dump()
check("dynamic:true survives validation",
      dyn_edl["captions"]["style"]["dynamic"] is True)
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(dyn_edl, sty_index, Timeline(dyn_edl["keep"]),
                         os.path.join(td, "dyn.ass"), play_res=(1080, 1920))
    dialogues = [l for l in open(p).read().splitlines()
                 if l.startswith("Dialogue:")]
    check("dynamic renders one event per word",
          len(dialogues) == len(sty_words))
    check("karaoke: the spoken word pops and lights up (default yellow)",
          all(r"\1c&H4DE1FF&" in d and r"\t(0,90" in d for d in dialogues))
    # xl on a 9:16 frame only fits ~2 short words per line — the word group
    # must shrink to the frame instead of wrapping mid-pop (round-8 fix), so
    # the >=3-word group check renders at the base 16:9 res where they fit
    p = caplib.build_ass(dyn_edl, sty_index, Timeline(dyn_edl["keep"]),
                         os.path.join(td, "dyn_wide.ass"))
    wide_dialogues = [l for l in open(p).read().splitlines()
                      if l.startswith("Dialogue:")]
    check("karaoke shows the word group, not bare single words",
          any(len(re.sub(r"\{[^}]*\}", " ", d.split(",,0,0,0,,")[1])
                  .split()) >= 3 for d in wide_dialogues))
sig_a = schemas.edl_signature(schemas.validate_edl(
    {"keep": [[0.0, 30.0]],
     "captions": {"mode": "from_transcript",
                  "style": {"size": "l", "color": "#FFFFFF",
                            "position": "top"}}}, 30.0).model_dump())
sig_b = schemas.edl_signature(
    {"keep": [[0.0, 30.0]],
     "captions": {"mode": "from_transcript", "max_words_per_caption": None,
                  "style": {"size": "l", "color": "#FFFFFF",
                            "position": "top"}}})
check("pre-round-7 caption styles stay signature-stable (dynamic=None "
      "stripped)", sig_a == sig_b)
check("style parser accepts dynamic",
      agent_tools._parse_partial_style({"dynamic": True}) ==
      {"dynamic": True})
check("style parser accepts xl",
      agent_tools._parse_partial_style({"size": "xl"}) == {"size": "xl"})
check("style parser still rejects unknown fields",
      isinstance(agent_tools._parse_partial_style({"font": "Arial"}), str))

print("== Repeated-phrase detection (round 7) ==")
def _mk_words(text, t0=0.0):
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "t0": round(t, 2), "t1": round(t + 0.3, 2)})
        t += 0.4
    return out

rep_words = _mk_words(
    "we just built the ultimate ai pipeline tool and then "
    "we just built the ultimate ai pipeline tool where you can drop")
reps = agent_tools.find_repeated_phrases(rep_words)
check("repeated phrase detected once (merged, not per-shingle)",
      len(reps) == 1)
check("repeat reports both program times",
      len(reps[0][1]) == 2 and reps[0][1][0] < reps[0][1][1])
check("repeat text is the merged long phrase",
      "we just built the ultimate ai pipeline tool" in reps[0][0])
check("unique text has no repeats",
      agent_tools.find_repeated_phrases(_mk_words(
          "every word here is different from all of the other ones "
          "nothing repeats in this sentence at all today")) == [])

print("== get_kept_transcript (round 7) ==")
class KeptCtx(ToolCtx):
    def __init__(self, edl, words):
        super().__init__(edl)
        self.index = {"words": words,
                      "video": {"duration": 60.0}}

kctx = KeptCtx({"keep": [[0.0, 10.0], [20.0, 30.0]], "inserts": []},
               rep_words)
out = agent_tools.get_kept_transcript(kctx)
check("kept transcript header names the EDL version",
      out.startswith("Program transcript of EDL v1"))
check("kept transcript lines carry program + source spans",
      "| src " in out)
check("kept transcript flags surviving repetitions",
      "POSSIBLE REPETITIONS" in out and
      "we just built the ultimate ai pipeline tool" in out)
kctx2 = KeptCtx({"keep": [[0.0, 4.0]], "inserts": []}, rep_words)
out2 = agent_tools.get_kept_transcript(kctx2)
check("no false repetition flag when only one take survives",
      "No repeated phrases detected" in out2)
check("kept transcript maps cut source times away",
      "20.00" not in out2)

print("== Transcript budget (round 7) ==")
check("default tool cap still truncates at 12k",
      len(agent_tools._cap("x" * 20000)) < 13000)
check("transcript budget keeps 20k chars intact",
      agent_tools._cap("x" * 20000,
                       budget=agent_tools.config.TRANSCRIPT_CHAR_BUDGET)
      == "x" * 20000)

# ─── Round 8: source-audio guard, mid-take inserts, effects, karaoke ────────

print("== Round-8 source-audio can never masquerade as music ==")
tctx = ToolCtx(json.loads(json.dumps(MUS_EDL)))
tctx._asset = {"kind": "audio", "storage_key": "audio/1/deadbeef.wav",
               "meta": {}, "id": 5}
r = add_music(tctx, "audio/1/deadbeef.wav", 0, 15)
check("add_music rejects the extracted source audio, explaining why",
      r.startswith("REJECTED") and "OWN extracted audio" in r
      and tctx.written is None)
r = agent_tools._resolve_media_asset(tctx, "audio/1/deadbeef.wav",
                                     ("music",))[1]
check("voiceover/insert resolution rejects it too",
      r and "OWN extracted" in r)

print("== Round-8 insert_media: mid-take split + clip window ==")


class InsCtx(ToolCtx):
    def __init__(self, edl, asset, words):
        super().__init__(edl, asset)
        self.index = {"words": words, "video": {"duration": 60.0}}
        self.workdir = "/tmp"


CLIP = {"kind": "video_clip", "storage_key": "clips/1/rec.mp4",
        "duration_s": 522.5, "meta": {"filename": "rec.mp4"}, "id": 9}
ins_words = [{"w": "mid", "t0": 5.5, "t1": 5.8}]
ictx = InsCtx({"keep": [[2.67, 9.29]], "inserts": []}, CLIP, ins_words)
r = agent_tools.insert_media(ictx, "clips/1/rec.mp4", 3.0,
                             duration_s=2.5, clip_start_s=120.0)
check("mid-take insert splits the keep segment instead of snapping to 0",
      ictx.written["keep"] == [[2.67, 5.8], [5.8, 9.29]])
check("insert sits on the new mid-take boundary",
      ictx.written["inserts"][0]["at_output_s"] == 3.13)
check("clip window recorded (source_start_s)",
      ictx.written["inserts"][0]["source_start_s"] == 120.0)
check("diff explains the split at a word edge",
      "split the take at source 5.8s" in r and "clip 120.0-122.5s" in r)
check("the split EDL passes full validation (boundary backstop)",
      schemas.validate_edl(ictx.written, 60.0)
      .inserts[0].at_output_s == 3.13)

ictx2 = InsCtx({"keep": [[2.67, 9.29]], "inserts": []}, CLIP, ins_words)
r = agent_tools.insert_media(ictx2, "clips/1/rec.mp4", 3.0)
check("long clips without a window are refused with guidance",
      r.startswith("REJECTED") and "look_at_asset" in r
      and "clip_start_s" in r and ictx2.written is None)
r = agent_tools.insert_media(ictx2, "clips/1/rec.mp4", 3.0,
                             duration_s=5.0, clip_start_s=520.0)
check("window past the end of the clip is refused with the max offset",
      r.startswith("REJECTED") and "517.5" in r)
ictx3 = InsCtx({"keep": [[2.67, 9.29]], "inserts": []}, CLIP, ins_words)
agent_tools.insert_media(ictx3, "clips/1/rec.mp4", 0.1, duration_s=2.0,
                         clip_start_s=10.0)
check("positions near a boundary use it (no needless split)",
      ictx3.written["keep"] == [[2.67, 9.29]] and
      ictx3.written["inserts"][0]["at_output_s"] == 0.0)

check("validate strips source_start_s from images and zero offsets",
      schemas.validate_edl(
          {"keep": [[0, 10]],
           "inserts": [{"id": "i1", "asset_key": "k", "kind": "image",
                        "at_output_s": 0.0, "duration_s": 3.0,
                        "source_start_s": 4.0},
                       {"id": "i2", "asset_key": "k", "kind": "video",
                        "at_output_s": 10.0, "duration_s": 3.0,
                        "source_start_s": 0.0}]}, 60)
      .model_dump()["inserts"][0]["source_start_s"] is None)
expect_reject("negative source_start_s",
              {"keep": [[0, 10]],
               "inserts": [{"id": "i", "asset_key": "k", "kind": "video",
                            "at_output_s": 0.0, "duration_s": 3.0,
                            "source_start_s": -2.0}]}, 60)

print("== Round-8 effects: schema + tools ==")
fx_edl = schemas.validate_edl(
    {"keep": [[0, 20]],
     "effects": {"grade": "vibrant",
                 "zooms": [{"id": "zm1", "start": 2, "end": 4,
                            "strength": 0.3}],
                 "fade_out_s": 0.8}}, 60).model_dump()
check("effects survive validation",
      fx_edl["effects"]["grade"] == "vibrant" and
      fx_edl["effects"]["zooms"][0]["strength"] == 0.3)
check("describe mentions the effects",
      "grade vibrant" in describe_edl(fx_edl, 60) and
      "zoom x1" in describe_edl(fx_edl, 60) and
      "fade out" in describe_edl(fx_edl, 60))
expect_reject("zoom strength out of range",
              {"keep": [[0, 20]],
               "effects": {"zooms": [{"id": "z", "start": 0, "end": 2,
                                      "strength": 3.0}]}}, 60)
expect_reject("fade too long",
              {"keep": [[0, 20]], "effects": {"fade_in_s": 30}}, 60)
check("all-empty effects normalize away (signature-stable with old EDLs)",
      schemas.edl_signature(schemas.validate_edl(
          {"keep": [[0.0, 20.0]], "effects": {"zooms": []}}, 60)
          .model_dump())
      == schemas.edl_signature(schemas.validate_edl(
          {"keep": [[0.0, 20.0]]}, 60).model_dump()))

tctx = ToolCtx({"keep": [[0.0, 20.0]]})
r = agent_tools.set_color_grade(tctx, "warm")
check("set_color_grade writes the preset",
      tctx.written["effects"]["grade"] == "warm" and "warm" in r)
check("set_color_grade rejects unknown presets listing the real ones",
      agent_tools.set_color_grade(ToolCtx({}), "sepia")
      .startswith("REJECTED"))
tctx = ToolCtx({"keep": [[0.0, 20.0]]})
agent_tools.add_zoom(tctx, 2, 4, strength=5.0)
check("add_zoom clamps strength and assigns an id",
      tctx.written["effects"]["zooms"][0]["strength"] == 1.0 and
      tctx.written["effects"]["zooms"][0]["id"] == "zm1")
zctx = ToolCtx({"keep": [[0.0, 20.0]],
                "effects": {"zooms": [{"id": "zm1", "start": 2.0,
                                       "end": 4.0, "strength": 0.25}]}})
r = agent_tools.remove_zoom(zctx, "zm9")
check("remove_zoom unknown id lists existing",
      r.startswith("REJECTED") and "zm1" in r)
agent_tools.remove_zoom(zctx, "zm1")
check("remove_zoom removes it", zctx.written["effects"]["zooms"] == [])
tctx = ToolCtx({"keep": [[0.0, 20.0]]})
agent_tools.set_fades(tctx, fade_in_s=0.5, fade_out_s=99)
check("set_fades clamps to the 5s ceiling",
      tctx.written["effects"]["fade_in_s"] == 0.5 and
      tctx.written["effects"]["fade_out_s"] == 5.0)
check("set_fades with nothing to do is rejected",
      agent_tools.set_fades(ToolCtx({})).startswith("REJECTED"))

print("== Round-8 effects: filtergraph ==")
fx_tl = Timeline(fx_edl["keep"], [])
g_fx = build_filtergraph(fx_edl, 60.0, True, fx_tl, None, [], index,
                         preview=False, W=720, H=1280, fps=30.0,
                         frame_mode=None)
check("grade filter lands before captions",
      "eq=saturation=1.35:contrast=1.08" in g_fx)
check("zoom becomes a zoompan window in program time",
      "zoompan=z='1+0.30*between(on/30.000,2.000,4.000)'" in g_fx)
check("zooms force per-segment normalization to exact frames",
      "scale=720:1280" in g_fx)
check("video fades out at the end of the program",
      "fade=t=out:st=19.20:d=0.80" in g_fx)
check("audio fades with the video",
      "afade=t=out:st=19.20:d=0.80" in g_fx)
g_plain = build_filtergraph(
    validate_edl({"keep": [[0, 10]]}, 60).model_dump(),
    60.0, True, Timeline([[0, 10]]), None, [], index, preview=False,
    W=720, H=720, fps=30.0, frame_mode=None)
check("no effects -> no zoompan/fade in the graph",
      "zoompan" not in g_plain and "fade=" not in g_plain)

print("== Round-8 insert window rendering ==")
win_edl = validate_edl(
    {"keep": [[0, 5], [5, 10]],
     "inserts": [{"id": "ins1", "asset_key": "clips/1/rec.mp4",
                  "kind": "video", "at_output_s": 5.0, "duration_s": 2.5,
                  "source_start_s": 120.0}]}, 60).model_dump()
win_tl = Timeline(win_edl["keep"], win_edl["inserts"])
g_win = build_filtergraph(win_edl, 60.0, True, win_tl, None, [], index,
                          preview=False, W=720, H=720, fps=30.0,
                          frame_mode=None,
                          insert_inputs=[(2, win_edl["inserts"][0], True)],
                          silence_idx=1)
check("insert video window starts at clip_start_s",
      "trim=start=120.000:end=122.500" in g_win)
check("insert audio window matches",
      "atrim=start=120.000:end=122.500" in g_win)

print("== Round-8 karaoke style knobs ==")
check("style parser accepts highlight_color",
      agent_tools._parse_partial_style({"highlight_color": "#FF00AA"}) ==
      {"highlight_color": "#FF00AA"})
check("style parser rejects bad highlight_color hex",
      isinstance(agent_tools._parse_partial_style(
          {"highlight_color": "reddish"}), str))
hl_edl = schemas.validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"dynamic": True,
                            "highlight_color": "#FF0000"}}}, 60).model_dump()
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(hl_edl, sty_index, Timeline(hl_edl["keep"]),
                         os.path.join(td, "hl.ass"))
    check("custom highlight color reaches the karaoke tag (BGR order)",
          r"\1c&H0000FF&" in open(p).read())
check("manual items never get dynamic/highlight written into them",
      agent_tools.merge_caption_style(
          [{"text": "t", "start": 0, "end": 2, "style": None}],
          {"dynamic": True, "highlight_color": "#FF0000",
           "color": "#00FF00"})[0]["style"] == {"color": "#00FF00"})

print("== Round-8 capabilities cover the new tools ==")
digest = agent_tools.capabilities_digest()
for t in ("set_color_grade", "add_zoom", "set_fades", "remove_insert",
          "insert_media"):
    check(f"digest lists {t}", t + "(" in digest)
check("insert_media digest advertises any-position splicing",
      "ANY position" in digest)

print("== Round-8 review fixes: karaoke overlap / width / cap honesty ==")
# fast speech (word starts < 80ms apart) must not produce stacked captions
fast = [{"w": "a", "t0": 1.00, "t1": 1.04},
        {"w": "b", "t0": 1.05, "t1": 1.09},
        {"w": "c", "t0": 1.10, "t1": 1.60}]
evs = caplib.events_dynamic(fast)
check("fast-speech karaoke events never overlap the next",
      all(evs[i]["end"] <= evs[i + 1]["start"] + 1e-9
          for i in range(len(evs) - 1)))
# degenerate zero-duration chunk-final word must not bleed into next chunk
deg = [{"w": "u", "t0": 0.0, "t1": 0.2}, {"w": "v", "t0": 0.2, "t1": 0.4},
       {"w": "x", "t0": 0.5, "t1": 0.5}, {"w": "y", "t0": 0.5, "t1": 0.9}]
evs = caplib.events_dynamic(deg)
check("degenerate chunk-final word never overlaps the next chunk at all",
      all(evs[i]["end"] <= evs[i + 1]["start"] + 1e-9
          for i in range(len(evs) - 1)))
check("events still have positive duration",
      all(e["end"] > e["start"] for e in evs))
# two words at the identical output t0 (cut-seam clamping) -> the sliver
# event is DROPPED, not left overlapping for a stacked frame
same = [{"w": "a", "t0": 3.0, "t1": 3.0}, {"w": "b", "t0": 3.0, "t1": 3.5}]
evs = caplib.events_dynamic(same)
check("identical-t0 words drop the sliver instead of stacking",
      len(evs) == 1 and evs[0]["start"] == 3.0 and
      all(evs[i]["end"] <= evs[i + 1]["start"] + 1e-9
          for i in range(len(evs) - 1)))
# chunks respect the line char budget so pops never move a wrap point
wide = [{"w": "wwwwww", "t0": i * 0.5, "t1": i * 0.5 + 0.3}
        for i in range(6)]
evs = caplib.events_dynamic(wide, line_chars=14)
plain = [re.sub(r"\{[^}]*\}", "", e["text"]) for e in evs]
check("karaoke chunks stay within the line char budget",
      all(len(t) <= 14 for t in plain))
narrow_edl = schemas.validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"dynamic": True, "size": "xl"}}},
    60).model_dump()
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(narrow_edl, sty_index, Timeline(narrow_edl["keep"]),
                         os.path.join(td, "n.ass"), play_res=(608, 1080))
    lines = [ln for ln in open(p) if ln.startswith("Dialogue:")]
    budget = caplib.line_chars_for({"dynamic": True, "size": "xl"},
                                   (608, 1080))
    plain = [re.sub(r"\{[^}]*\}", "", ln.rsplit(",,", 1)[-1]).strip()
             for ln in lines]
    check("9:16 xl karaoke chunks sized to the narrow frame",
          lines and all(len(t) <= budget for t in plain))

# the 4-word karaoke cap is applied to stored state and disclosed
kctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = agent_tools.add_captions(kctx, mode="from_transcript",
                             style={"dynamic": True},
                             max_words_per_caption=8)
check("add_captions clamps karaoke group size in the stored EDL",
      kctx.written["captions"]["max_words_per_caption"] == 4)
check("add_captions discloses the karaoke cap",
      "at most 4 words" in r and "instead of 8" in r)
kctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = agent_tools.add_captions(kctx, mode="from_transcript",
                             max_words_per_caption=8)
check("non-dynamic captions keep the requested group size",
      kctx.written["captions"]["max_words_per_caption"] == 8 and
      "Note" not in r)
kctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": 8, "style": None}})
r = agent_tools.set_caption_style(kctx, {"dynamic": True})
check("set_caption_style clamps stored group size when enabling karaoke",
      kctx.written["captions"]["max_words_per_caption"] == 4)
check("set_caption_style discloses the clamp", "lowered from 8" in r)

print("== Round-8 review fixes: honesty vocabulary + hint routing ==")
import agent_loop as al                                       # noqa: E402
for s in ("Added a vibrant grade, a punch-in on the opening line and a "
          "closing fade to black.",
          "The captions now light up word by word in green.",
          "I've color-graded the video warm and added a fade-out.",
          "The whole video is now graded cinematic.",
          "A punch-in zoom was applied at 0:05 for emphasis.",
          "Karaoke captions are enabled with a green highlight."):
    check(f"EDIT_CLAIM catches effects fabrication: {s[:44]!r}",
          al.EDIT_CLAIM.search(s))
for s in ("No music was added — that file is the video's own voice "
          "recording; attach a real music file and I'll mix it in.",
          "I can set a fade-out at the end and add a vibrant color grade "
          "— want me to go ahead?",
          "I can make the captions karaoke-style where each spoken word "
          "pops and lights up. Want that?",
          # negated / status phrasings the verify pass flagged
          "No grade was applied — the timeline is empty.",
          "No color grade was applied this turn.",
          "No zoom was added this turn because the write failed.",
          "I haven't added a grade yet — want me to?",
          "The tool call failed, so it never applied the grade.",
          "Nothing was added — karaoke captions need a transcript first.",
          "The captions are not karaoke yet — say the word and I'll "
          "switch them.",
          "The captions are still static, not dynamic.",
          "The captions are already karaoke-style, so there was nothing "
          "to change."):
    check(f"honest phrasing passes: {s[:44]!r}",
          not al._reply_violations(s, wrote=False, previewed=False) or
          al.DENY_CLAIM.search(s))
check("bare-fade fabrication caught",
      al._reply_violations("The fade was added at the end.",
                           wrote=False, previewed=False))
check("negation rescue is per-sentence, not per-draft",
      al._reply_violations("No zoom was added earlier. I've now applied "
                           "a vibrant grade.", wrote=False, previewed=False))
for q in ("add an animated zoom on my face",
          "can you zoom into the middle of the shot at 0:05",
          "make it a viral tiktok edit with zooms and filters"):
    check(f"effects hint wins for {q[:36]!r}",
          "color-grade" in (al._nearest_alternative(q) or ""))
check("caption hint still owns caption asks",
      "captions" in (al._nearest_alternative("animated captions please")
                     or ""))
check("insert hint says any point, not between segments",
      "ANY" in al._nearest_alternative("splice my logo into the video") and
      "between segments" not in
      al._nearest_alternative("splice my logo into the video"))

print("== Round-8 review fixes: attachment context skips 'audio' kind ==")


class _AttDB:
    def __init__(self, asset):
        self._a = asset

    def run(self, fn, *a, **k):
        return self._a


class _AttCtx:
    project_id = 1
    workdir = "/tmp"


aud = {"id": 5, "project_id": 1, "kind": "audio", "meta": {},
       "storage_key": "audio/1/x.wav", "duration_s": 10.4, "bytes": 100}
msg = {"meta": {"attachments": [5]}}
check("extracted-audio attachment produces no music context line",
      al._attachment_context(_AttDB(aud), _AttCtx(), msg) == "")
mus = dict(aud, kind="music", storage_key="music/1/song.mp3",
           meta={"filename": "song.mp3"})
check("real music attachment still produces the context line",
      "User attached music" in
      al._attachment_context(_AttDB(mus), _AttCtx(), msg))

# ─── Round 9: transitions, zoom modes, Ken Burns inserts, caption anim ──────

print("== Round-9 schema: transitions / zoom modes / insert motion ==")
tr_edl = validate_edl(
    {"keep": [[0, 5], [10, 20]],
     "effects": {"transition": {"style": "dip_black",
                                "duration_s": 0.3}}}, 60).model_dump()
check("transition survives validation",
      tr_edl["effects"]["transition"] == {"style": "dip_black",
                                          "duration_s": 0.3})
check("describe mentions transitions",
      "transitions dip_black 0.3s" in describe_edl(tr_edl, 60))
expect_reject("transition too long",
              {"keep": [[0, 20]],
               "effects": {"transition": {"style": "dip_black",
                                          "duration_s": 3.0}}}, 60)
expect_reject("unknown transition style",
              {"keep": [[0, 20]],
               "effects": {"transition": {"style": "crossfade",
                                          "duration_s": 0.3}}}, 60)
zm_edl = validate_edl(
    {"keep": [[0, 20]],
     "effects": {"zooms": [{"id": "z1", "start": 1, "end": 4,
                            "strength": 0.3, "mode": "ease"},
                           {"id": "z2", "start": 5, "end": 8,
                            "strength": 0.3, "mode": "punch"}]}},
    60).model_dump()
check("zoom mode survives validation and 'punch' normalizes to None",
      zm_edl["effects"]["zooms"][0]["mode"] == "ease" and
      zm_edl["effects"]["zooms"][1]["mode"] is None)
expect_reject("unknown zoom mode",
              {"keep": [[0, 20]],
               "effects": {"zooms": [{"id": "z", "start": 0, "end": 2,
                                      "strength": 0.3,
                                      "mode": "wobble"}]}}, 60)
km_edl = validate_edl(
    {"keep": [[0, 10]],
     "inserts": [{"id": "i1", "asset_key": "images/1/a.jpg",
                  "kind": "image", "at_output_s": 0.0, "duration_s": 3.0,
                  "motion": "zoom_in"}]}, 60).model_dump()
check("image insert motion survives validation",
      km_edl["inserts"][0]["motion"] == "zoom_in")
expect_reject("motion on a video insert",
              {"keep": [[0, 10]],
               "inserts": [{"id": "i1", "asset_key": "clips/1/a.mp4",
                            "kind": "video", "at_output_s": 0.0,
                            "duration_s": 3.0, "motion": "zoom_in"}]}, 60)
check("caption animation survives validation",
      validate_edl({"keep": [[0, 10]],
                    "captions": {"mode": "from_transcript",
                                 "style": {"animation": "pop"}}}, 60)
      .model_dump()["captions"]["style"]["animation"] == "pop")
expect_reject("unknown caption animation",
              {"keep": [[0, 10]],
               "captions": {"mode": "from_transcript",
                            "style": {"animation": "spin"}}}, 60)
check("old EDLs keep their signatures (new fields all optional)",
      schemas.edl_signature(validate_edl(
          {"keep": [[0.0, 20.0]],
           "effects": {"zooms": [{"id": "z1", "start": 1.0, "end": 4.0,
                                  "strength": 0.3}]}}, 60).model_dump())
      == schemas.edl_signature(validate_edl(
          {"keep": [[0.0, 20.0]],
           "effects": {"zooms": [{"id": "z1", "start": 1.0, "end": 4.0,
                                  "strength": 0.3, "mode": "punch"}]}},
          60).model_dump()))

print("== Round-9 tools: set_transitions / add_zoom modes / KB inserts ==")
tctx = ToolCtx({"keep": [[0.0, 5.0], [10.0, 20.0]]})
r = agent_tools.set_transitions(tctx, "dip_black")
check("set_transitions writes style + default duration",
      tctx.written["effects"]["transition"] == {"style": "dip_black",
                                                "duration_s": 0.3} and
      "dip-black" in r)
check("set_transitions counts the junctions", "1 junction" in r)
check("set_transitions rejects crossfade asks with guidance",
      agent_tools.set_transitions(ToolCtx({"keep": [[0.0, 5.0]]}),
                                  "crossfade").startswith("REJECTED"))
check("clearing transitions when none exist is a NO CHANGE",
      agent_tools.set_transitions(
          ToolCtx({"keep": [[0.0, 5.0]]}), "none").startswith("NO CHANGE"))
tctx2 = ToolCtx({"keep": [[0.0, 5.0]],
                 "effects": {"transition": {"style": "dip_white",
                                            "duration_s": 0.5}}})
agent_tools.set_transitions(tctx2, "none")
check("set_transitions 'none' clears an existing transition",
      tctx2.written["effects"]["transition"] is None)
tctx3 = ToolCtx({"keep": [[0.0, 20.0]]})
r = agent_tools.add_zoom(tctx3, 2, 6, strength=0.3, mode="push_in")
check("add_zoom stores non-default modes",
      tctx3.written["effects"]["zooms"][0]["mode"] == "push_in" and
      "Ken Burns push-in" in r)
tctx4 = ToolCtx({"keep": [[0.0, 20.0]]})
agent_tools.add_zoom(tctx4, 2, 6, strength=0.3, mode="punch")
check("add_zoom omits mode for the punch default",
      "mode" not in tctx4.written["effects"]["zooms"][0])
check("add_zoom rejects unknown modes listing the real ones",
      agent_tools.add_zoom(ToolCtx({"keep": [[0.0, 20.0]]}), 2, 6,
                           mode="wobble").startswith("REJECTED"))
IMG = {"kind": "image_ref", "storage_key": "images/1/logo.jpg",
       "duration_s": None, "meta": {"filename": "logo.jpg"}, "id": 7}
ictx_kb = InsCtx({"keep": [[2.67, 9.29]], "inserts": []}, IMG, ins_words)
r = agent_tools.insert_media(ictx_kb, "images/1/logo.jpg", 0.0,
                             motion="zoom_in")
check("image insert stores the Ken Burns motion",
      ictx_kb.written["inserts"][0]["motion"] == "zoom_in" and
      "Ken Burns zoom_in" in r)
ictx_kb2 = InsCtx({"keep": [[2.67, 9.29]], "inserts": []}, CLIP, ins_words)
r = agent_tools.insert_media(ictx_kb2, "clips/1/rec.mp4", 0.0,
                             duration_s=2.0, clip_start_s=1.0,
                             motion="zoom_in")
check("motion on a video clip is refused",
      r.startswith("REJECTED") and "IMAGE" in r and
      ictx_kb2.written is None)
check("style parser accepts animation",
      agent_tools._parse_partial_style({"animation": "slide_up"})
      == {"animation": "slide_up"})
check("style parser still rejects unknown fields, listing animation",
      "animation" in agent_tools._parse_partial_style({"font": "Arial"}))

print("== Round-9 filtergraph: transitions / zoom modes / insert motion ==")
tr_tl = Timeline(tr_edl["keep"], [])
g_tr = build_filtergraph(tr_edl, 60.0, True, tr_tl, None, [], index,
                         preview=False, W=720, H=720, fps=30.0,
                         frame_mode=None)
check("first block fades out only (no fade-in at program start)",
      "fade=t=out:st=4.70:d=0.30:c=black" in g_tr and
      "fade=t=in:st=0:d=0.30:c=black[vtr0]" not in g_tr)
check("second block fades in only (no fade-out at program end)",
      "fade=t=in:st=0:d=0.30:c=black" in g_tr)
check("audio is untouched by transitions (no afade at junctions)",
      g_tr.count("afade") == 0)
wt_edl = validate_edl(
    {"keep": [[0, 5], [10, 20]],
     "effects": {"transition": {"style": "dip_white",
                                "duration_s": 0.4}}}, 60).model_dump()
g_wt = build_filtergraph(wt_edl, 60.0, True,
                         Timeline(wt_edl["keep"], []), None, [], index,
                         preview=False, W=720, H=720, fps=30.0,
                         frame_mode=None)
check("dip_white uses white fades", "c=white" in g_wt)
zm_tl = Timeline(zm_edl["keep"], [])
g_zm = build_filtergraph(zm_edl, 60.0, True, zm_tl, None, [], index,
                         preview=False, W=720, H=720, fps=30.0,
                         frame_mode=None)
check("eased zoom uses clip ramps",
      "clip((on/30.000-1.000)/" in g_zm and
      "clip((4.000-on/30.000)/" in g_zm)
check("punch zoom keeps the between step",
      "0.30*between(on/30.000,5.000,8.000)" in g_zm)
pi_edl = validate_edl(
    {"keep": [[0, 20]],
     "effects": {"zooms": [{"id": "z1", "start": 2, "end": 10,
                            "strength": 0.4, "mode": "push_in"}]}},
    60).model_dump()
g_pi = build_filtergraph(pi_edl, 60.0, True, Timeline(pi_edl["keep"], []),
                         None, [], index, preview=False, W=720, H=720,
                         fps=30.0, frame_mode=None)
check("push_in zoom ramps across the window",
      "0.40*((on/30.000-2.000)/8.000)*between(on/30.000,2.000,10.000)"
      in g_pi)
g_kb = build_filtergraph(km_edl, 60.0, True,
                         Timeline(km_edl["keep"], km_edl["inserts"]),
                         None, [], index, preview=False, W=720, H=720,
                         fps=30.0, frame_mode=None,
                         insert_inputs=[(2, km_edl["inserts"][0], False)],
                         silence_idx=1)
check("image insert motion adds a per-block zoompan",
      "[v_insn0]zoompan=z='1+0.25*(on/90)'" in g_kb)
check("motion zoompan feeds the concat block",
      "[v_ins0]" in g_kb)

print("== Round-9 captions: entrance animations ==")
anim_events = [{"start": 0.0, "end": 2.0, "text": "HELLO"}]
with tempfile.TemporaryDirectory() as td:
    p = caplib.write_ass([dict(e) for e in anim_events],
                         os.path.join(td, "a.ass"),
                         {"animation": "fade"}, play_res=(720, 1280))
    body = open(p).read()
    check("fade animation emits \\fad", r"\fad(160,120)" in body)
    p = caplib.write_ass([dict(e) for e in anim_events],
                         os.path.join(td, "b.ass"),
                         {"animation": "pop"}, play_res=(720, 1280))
    body = open(p).read()
    check("pop animation emits scale transforms",
          r"\fscx70\fscy70" in body and r"\t(0,120," in body)
    p = caplib.write_ass([dict(e) for e in anim_events],
                         os.path.join(td, "c.ass"),
                         {"animation": "slide_up", "position": "bottom"},
                         play_res=(720, 1280))
    body = open(p).read()
    check("slide_up animation emits \\move at the bottom anchor",
          r"\move(360," in body)
    p = caplib.write_ass([dict(e) for e in anim_events],
                         os.path.join(td, "d.ass"),
                         {"animation": "fade", "dynamic": True},
                         play_res=(720, 1280))
    body = open(p).read()
    check("dynamic style suppresses entrance animation",
          r"\fad(" not in body)
    ov_events = [{"start": 0.0, "end": 2.0, "text": "TITLE",
                  "item_style": {"animation": "pop"}}]
    p = caplib.write_ass(ov_events, os.path.join(td, "e.ass"),
                         None, play_res=(720, 1280))
    body = open(p).read()
    check("per-item animation override reaches the event",
          r"\fscx70" in body)

print("== Round-9 tool notes: animation vs karaoke disclosure ==")
tctx5 = ToolCtx({"keep": [[0.0, 20.0]]})
r = agent_tools.add_captions(
    tctx5, mode="from_transcript",
    style={"dynamic": True, "animation": "pop"})
check("add_captions discloses animation is ignored in karaoke mode",
      "ignored" in r)
tctx6 = ToolCtx({"keep": [[0.0, 20.0]],
                 "captions": {"mode": "from_transcript",
                              "style": {"dynamic": True}}})
r = agent_tools.set_caption_style(tctx6, {"animation": "fade"})
check("set_caption_style discloses the karaoke-ignores-animation rule",
      "ignored" in r)

print("== Round-9 honesty: new effect claims are caught ==")
for s in ("Added smooth transitions between all the cuts.",
          "The captions now fade in at the bottom.",
          "A Ken Burns zoom was applied to the intro.",
          "Transitions are now added at every cut.",
          "Animations were applied to your captions."):
    m = next((mm for mm in al.EDIT_CLAIM.finditer(s)
              if not al._negated_claim(s, mm)), None)
    check(f"EDIT_CLAIM catches: {s[:44]!r}", m is not None)
for s in ("No transitions were added this turn.",
          "I haven't added any animations yet.",
          "I can add dip-to-black transitions at every cut if you like."):
    m = next((mm for mm in al.EDIT_CLAIM.finditer(s)
              if not al._negated_claim(s, mm)), None)
    check(f"honest phrasing passes: {s[:44]!r}", m is None)
check("effects hint mentions transitions and Ken Burns",
      "transitions" in al._nearest_alternative("add transitions please") and
      "Ken Burns" in al._nearest_alternative("animate the photo"))
check("capabilities digest lists set_transitions",
      "set_transitions(" in agent_tools.capabilities_digest())

print("== Round-9 triage: offers are not claims; hints include animation ==")
for s in ("I can make the captions fade in if you'd like.",
          "Want the captions to pop in? I could add that.",
          "I could have the captions slide in from the bottom."):
    m = next((mm for mm in al.EDIT_CLAIM.finditer(s)
              if not al._negated_claim(s, mm)
              and not al._offered_claim(s, mm)), None)
    check(f"caption-anim offer passes: {s[:44]!r}", m is None)
for s in ("The captions now fade in at the bottom.",
          "Captions pop in on every cut.",
          "The captions slide in from below now."):
    m = next((mm for mm in al.EDIT_CLAIM.finditer(s)
              if not al._negated_claim(s, mm)
              and not al._offered_claim(s, mm)), None)
    check(f"caption-anim fabrication caught: {s[:40]!r}", m is not None)
check("violations: honest alternative offer survives",
      al._reply_violations("I can't add stickers, but I can make the "
                           "captions fade in — want that?",
                           wrote=False, previewed=False) == [])
v = al._reply_violations("Done — the captions now fade in.",
                         wrote=False, previewed=False)
check("violations: caption-anim fabrication still flagged", len(v) == 1)
check("partial-style empty hint mentions animation",
      "animation" in agent_tools._parse_partial_style({}))
check("partial-style pydantic hint mentions animation",
      "animation" in agent_tools._parse_partial_style({"animation": "bounce"}))

print("== Concierge round: LLM greetings guard their honesty ==")
import ast                                                    # noqa: E402
import config as wconfig                                      # noqa: E402
import indexer                                                # noqa: E402
import llm as wllm                                            # noqa: E402

for s in ("I've cut the silences and it's much tighter now.",
          "I have already trimmed the intro for you.",
          "I just edited your video and rendered a preview.",
          "I cut the dead air while analyzing."):
    check(f"greet claim caught: {s[:44]!r}",
          indexer._GREET_CLAIM.search(s) is not None)
for s in ("Your video is ready to edit — 5.0 min, 9 shots.",
          "Tell me what you'd like — for example: cut the dead air.",
          "I'll cut the silences as soon as you say the word.",
          "I'm starting on the request you sent while I was analyzing."):
    check(f"greet honest draft passes: {s[:44]!r}",
          indexer._GREET_CLAIM.search(s) is None)

_key_backup = wconfig.OPENAI_API_KEY
wconfig.OPENAI_API_KEY = ""
check("ask_text without key returns None",
      wllm.ask_text("s", "u") is None)
check("greet without key skips LLM (no DB touched)",
      indexer._greet_via_llm(None, 1, "5.0 min, 9 shots", None, False,
                             {"words": []}) is None)
wconfig.OPENAI_API_KEY = _key_backup

# The backend concierge guard lives in Flask-land (heavy imports) — test
# the SHIPPED pattern by extracting it from the source with ast.
_vid_src = os.path.join(os.path.dirname(__file__), "..", "..",
                        "backend", "routes", "video.py")
_pattern = None
for node in ast.walk(ast.parse(open(_vid_src).read())):
    if (isinstance(node, ast.Assign) and
            any(getattr(t, "id", "") == "_CONCIERGE_CLAIM"
                for t in node.targets)):
        _pattern = node.value.args[0].value
check("concierge claim pattern found in video.py", _pattern is not None)
_cc = re.compile(_pattern)
for s in ("I've already edited your clip.",
          "I just analyzed the footage you sent.",
          "Your video is ready — tell me what to change."):
    check(f"concierge claim caught: {s[:40]!r}", _cc.search(s) is not None)
for s in ("Hi! Upload a video on the right and I'll get to work.",
          "Your request is saved — I'll start once analysis finishes.",
          "I can cut silences, add captions, music and zooms."):
    check(f"concierge honest draft passes: {s[:40]!r}",
          _cc.search(s) is None)

# The stage mapper decides what the concierge may claim about the user's
# upload — a failed index must never be presented as "no video yet".
# Pure function: extract and exec it from the source.
_stage_fn = None
for node in ast.walk(ast.parse(open(_vid_src).read())):
    if isinstance(node, ast.FunctionDef) and node.name == "_concierge_stage":
        _ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<video.py>", "exec"), _ns)
        _stage_fn = _ns["_concierge_stage"]
check("concierge stage mapper found in video.py", _stage_fn is not None)
for st, want in (("queued", "indexing"), ("running", "indexing"),
                 ("failed", "index_failed"), (None, "no_video"),
                 ("done", "ready")):
    check(f"concierge stage {st!r} -> {want}", _stage_fn(st) == want)
# post_message must fetch the error for the failed stage, and the reply
# builder must have a dedicated failed-index branch.
_src_text = open(_vid_src).read()
check("post_message selects index error",
      "SELECT state, error FROM video_jobs" in _src_text)
check("reply builder handles index_failed",
      'stage == "index_failed"' in _src_text)
check("capability facts declare themselves exhaustive",
      "lists are exhaustive" in _src_text)

print("== generate_image ==")
import config as cfg                                          # noqa: E402
import llm as llmmod                                          # noqa: E402
import agent_tools as at                                      # noqa: E402

# Endpoint derivation: DashScope bases yield the native image endpoint,
# anything else disables image features unless IMAGE_API_URL overrides.
_old = (cfg.IMAGE_API_URL, cfg.OPENAI_BASE_URL, cfg.IMAGE_GEN_MODEL,
        cfg.OPENAI_API_KEY)
cfg.IMAGE_API_URL = ""
cfg.OPENAI_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
check("image endpoint derived from dashscope-intl base",
      llmmod.image_api_url() == "https://dashscope-intl.aliyuncs.com"
      "/api/v1/services/aigc/multimodal-generation/generation")
cfg.OPENAI_BASE_URL = "https://api.openai.com/v1"
check("non-dashscope base disables image endpoint",
      llmmod.image_api_url() is None)
cfg.IMAGE_API_URL = "https://example.com/img"
check("IMAGE_API_URL overrides derivation",
      llmmod.image_api_url() == "https://example.com/img")
cfg.IMAGE_API_URL = ""
cfg.OPENAI_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
cfg.OPENAI_API_KEY = "test-key"
cfg.IMAGE_GEN_MODEL = "qwen-image-plus"
check("image_available true with key+model+dashscope",
      llmmod.image_available())

check("v1 model gets v1 sizes",
      llmmod.image_size_for("9:16", "qwen-image-plus") == "928*1664")
check("2.x model gets 2.x sizes",
      llmmod.image_size_for("9:16", "qwen-image-2.0-pro") == "1536*2688")
check("unknown aspect -> None (model default)",
      llmmod.image_size_for("21:9", "qwen-image-plus") is None)

# Tool visibility: generate_image is hidden everywhere when unavailable.
check("digest advertises generate_image when available",
      "generate_image" in at.capabilities_digest())
check("openai_tools includes generate_image when available",
      any(t["function"]["name"] == "generate_image"
          for t in at.openai_tools()))
cfg.IMAGE_GEN_MODEL = ""
check("digest hides generate_image when disabled",
      "generate_image" not in at.capabilities_digest())
check("openai_tools hides generate_image when disabled",
      all(t["function"]["name"] != "generate_image"
          for t in at.openai_tools()))
cfg.IMAGE_GEN_MODEL = "qwen-image-plus"


class GenCtx(ToolCtx):
    def __init__(self, edl=None):
        super().__init__(edl or {"keep": [[0.0, 30.0]]})
        self.images_generated = []
        self.workdir = tempfile.mkdtemp()
        self.index = {"video": {"duration": 30.0, "width": 1920,
                                "height": 1080, "fps": 30.0,
                                "has_audio": True}}
        self.duration = 30.0

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)

    def run(self, fn, *a, **k):          # actually dispatch, unlike ToolCtx
        return fn(None, *a, **k)


check("empty prompt rejected",
      at.generate_image(GenCtx(), "  ").startswith("REJECTED"))
check("both sources rejected",
      at.generate_image(GenCtx(), "x", from_video_time_s=1,
                        from_asset_key="images/1/a.png")
      .startswith("REJECTED"))
check("bad aspect rejected",
      at.generate_image(GenCtx(), "x", aspect="21:9")
      .startswith("REJECTED"))
_full = GenCtx()
_full.images_generated = [{}] * cfg.MAX_GENERATED_IMAGES_PER_TURN
check("per-turn image cap enforced",
      at.generate_image(_full, "x").startswith("REJECTED"))
cfg.IMAGE_GEN_MODEL = ""
check("honest unavailable message when disabled",
      "unavailable" in at.generate_image(GenCtx(), "x"))
cfg.IMAGE_GEN_MODEL = "qwen-image-plus"

# Success path with the network + storage stubbed out.
_calls = {}


def _fake_gen(prompt, out_path, aspect=None):
    _calls["gen"] = (prompt, aspect)
    with open(out_path, "wb") as f:
        f.write(b"\x89PNG fake")
    return True, None


def _fake_upload(path, key, content_type):
    _calls["upload"] = (key, content_type)


def _fake_insert_asset(conn, project_id, kind, storage_key, **kw):
    _calls["asset"] = (project_id, kind, storage_key, kw.get("meta"))
    return 42


_g0 = (llmmod.generate_image, at.storage.upload_file, at.dbx.insert_asset)
llmmod.generate_image = _fake_gen
at.storage.upload_file = _fake_upload
at.dbx.insert_asset = _fake_insert_asset
try:
    gctx = GenCtx()
    r = at.generate_image(gctx, "a cat wearing a crown")
    check("success result names the storage_key",
          "storage_key=generated/1/" in r)
    check("success result says it is NOT in the video yet",
          "NOT in the video yet" in r)
    check("success result explains the still-frame mechanics",
          "still moment" in r)
    check("aspect defaults to nearest source ratio (16:9 for 1920x1080)",
          _calls["gen"][1] == "16:9")
    check("asset row is image_ref with generated meta",
          _calls["asset"][1] == "image_ref"
          and _calls["asset"][3]["generated"] is True)
    check("uploaded as png", _calls["upload"][1] == "image/png")
    check("ctx tracks the generated image",
          len(gctx.images_generated) == 1
          and gctx.images_generated[0]["storage_key"].startswith(
              "generated/1/"))

    gctx2 = GenCtx({"keep": [[0.0, 30.0]], "frame": {"ratio": "9:16",
                                                     "mode": "crop"}})
    at.generate_image(gctx2, "vertical poster")
    check("aspect defaults to the output frame when set",
          _calls["gen"][1] == "9:16")

    def _fake_gen_fail(prompt, out_path, aspect=None):
        return False, "DataInspectionFailed: content policy"
    llmmod.generate_image = _fake_gen_fail
    r = at.generate_image(GenCtx(), "something rejected")
    check("failure result forbids claiming success",
          "FAILED" in r and "do NOT claim" in r)
finally:
    llmmod.generate_image, at.storage.upload_file, at.dbx.insert_asset = _g0
    (cfg.IMAGE_API_URL, cfg.OPENAI_BASE_URL, cfg.IMAGE_GEN_MODEL,
     cfg.OPENAI_API_KEY) = _old

# Honesty wiring: a truthful "I made an image" on a zero-EDL-write turn is
# not a fabrication when acted=True; the denial check still keys on the EDL.
check("image-only turn: edit-verb sentence passes with acted",
      _reply_violations("I made a new image of the character and can "
                        "insert it wherever you like.",
                        wrote=False, previewed=False, acted=True) == [])
check("image-only turn: honest 'EDL unchanged' is not a false denial",
      _reply_violations("The edit is unchanged — I only generated an "
                        "image so far.",
                        wrote=False, previewed=False, acted=True) == [])
check("no action at all still catches fabricated edits",
      len(_reply_violations("I've added the image at 5s.",
                            wrote=False, previewed=False, acted=False)) == 1)

# ─── Round 12: censor regions + honesty echo guard (user-76 failures) ──────

print("== Round-12 regions: schema validation ==")
rg_ok = validate_edl(
    {"keep": [[0, 30]],
     "effects": {"regions": [{"id": "rg1", "x": 0.6, "y": 0.02,
                              "w": 0.38, "h": 0.1}]}}, 60)
check("region EDL passes with blur default and no window",
      rg_ok.effects.regions[0].mode == "blur" and
      rg_ok.effects.regions[0].start is None)
rg_clamp = validate_edl(
    {"keep": [[0, 30]],
     "effects": {"regions": [{"id": "rg1", "x": 0.9, "y": -0.2,
                              "w": 0.5, "h": 0.3}]}}, 60)
check("region rect is clamped into the frame",
      rg_clamp.effects.regions[0].y == 0.0 and
      abs(rg_clamp.effects.regions[0].w - 0.1) < 1e-9)
expect_reject("region too small",
              {"keep": [[0, 30]],
               "effects": {"regions": [{"id": "rg1", "x": 0.5, "y": 0.5,
                                        "w": 0.005, "h": 0.2}]}}, 60)
expect_reject("region start without end",
              {"keep": [[0, 30]],
               "effects": {"regions": [{"id": "rg1", "x": 0.1, "y": 0.1,
                                        "w": 0.2, "h": 0.2,
                                        "start": 1.0}]}}, 60)
expect_reject("region window beyond the program",
              {"keep": [[0, 30]],
               "effects": {"regions": [{"id": "rg1", "x": 0.1, "y": 0.1,
                                        "w": 0.2, "h": 0.2,
                                        "start": 0.0, "end": 45.0}]}}, 60)
expect_reject("duplicate region ids",
              {"keep": [[0, 30]],
               "effects": {"regions": [
                   {"id": "rg1", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                   {"id": "rg1", "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}]}},
              60)
check("empty regions list is the absence of regions",
      validate_edl({"keep": [[0, 30]],
                    "effects": {"regions": []}}, 60).effects is None)
rg_old = validate_edl({"keep": [[0, 30]],
                       "effects": {"grade": "warm"}}, 60).model_dump()
rg_stored = json.loads(json.dumps(rg_old))
del rg_stored["effects"]["regions"]     # a pre-round-12 stored row
check("pre-round-12 effects EDLs keep their signature",
      edl_signature(rg_stored) == edl_signature(rg_old))

print("== Round-12 regions: filtergraph ==")
rgn_edl = validate_edl(
    {"keep": [[0, 20]],
     "effects": {"regions": [{"id": "rg1", "x": 0.6, "y": 0.02,
                              "w": 0.38, "h": 0.1}]}}, 60).model_dump()
g_rg = build_filtergraph(rgn_edl, 60.0, True, Timeline(rgn_edl["keep"]),
                         None, [], index, preview=False,
                         W=720, H=720, fps=30.0, frame_mode=None)
check("blur region crops the rectangle at exact pixels",
      "crop=274:72:432:14" in g_rg)
check("blur region blurs and overlays back onto the segment",
      "gblur=sigma=12:steps=2" in g_rg and "overlay=432:14[v_seg0]" in g_rg)
check("regions alone do not force normalization",
      "scale=720:720" not in g_rg)
rgb_edl = validate_edl(
    {"keep": [[0, 20]],
     "effects": {"regions": [{"id": "rg1", "mode": "black", "x": 0.0,
                              "y": 0.0, "w": 0.5, "h": 0.2,
                              "start": 1.0, "end": 5.0}]}}, 60).model_dump()
g_rgb = build_filtergraph(rgb_edl, 60.0, True, Timeline(rgb_edl["keep"]),
                          None, [], index, preview=False,
                          W=720, H=720, fps=30.0, frame_mode=None)
check("black region is a filled drawbox limited to its window",
      "drawbox=x=0:y=0:w=360:h=144:color=black:t=fill"
      ":enable='between(t,1.00,5.00)'" in g_rgb)
rgp_edl = validate_edl(
    {"keep": [[0, 20]],
     "effects": {"regions": [{"id": "rg1", "mode": "pixelate", "x": 0.25,
                              "y": 0.25, "w": 0.5, "h": 0.5}]}},
    60).model_dump()
g_rgp = build_filtergraph(rgp_edl, 60.0, True, Timeline(rgp_edl["keep"]),
                          None, [], index, preview=False,
                          W=720, H=720, fps=30.0, frame_mode=None)
check("pixelate region downscales then neighbor-upscales",
      "flags=neighbor" in g_rgp)

# review round: regions are SOURCE-frame coords applied per segment BEFORE
# the reframe — a 9:16 pad must not smear the letterbox bar, and inserted
# material must never be censored
g_rgf = build_filtergraph(rgn_edl, 60.0, True, Timeline(rgn_edl["keep"]),
                          None, [], index, preview=False,
                          W=1080, H=1920, fps=30.0, frame_mode="pad",
                          src_w=1920, src_h=1080)
check("region pixels come from the SOURCE dims, not the output canvas",
      "crop=730:108:1152:22" in g_rgf)
check("region is burned in before the pad normalization",
      g_rgf.index("overlay=1152:22") < g_rgf.index("pad=1080:1920"))
rgw_edl = validate_edl(
    {"keep": [[0, 2], [3, 5]],
     "effects": {"regions": [{"id": "rg1", "mode": "black", "x": 0.0,
                              "y": 0.0, "w": 0.5, "h": 0.2,
                              "start": 0.5, "end": 3.0}]}}, 60).model_dump()
g_rgw = build_filtergraph(rgw_edl, 60.0, True, Timeline(rgw_edl["keep"]),
                          None, [], index, preview=False,
                          W=720, H=720, fps=30.0, frame_mode=None)
check("windowed region maps program time into each segment's local time",
      "enable='between(t,0.50,2.00)'" in g_rgw and
      "enable='between(t,0.00,1.00)'" in g_rgw)
rgi_edl = validate_edl(
    {"keep": [[0, 5], [5, 10]],
     "inserts": [{"id": "ins1", "asset_key": "clips/1/rec.mp4",
                  "kind": "video", "at_output_s": 5.0, "duration_s": 2.5}],
     "effects": {"regions": [{"id": "rg1", "x": 0.6, "y": 0.02,
                              "w": 0.38, "h": 0.1}]}}, 60).model_dump()
rgi_tl = Timeline(rgi_edl["keep"], rgi_edl["inserts"])
g_rgi = build_filtergraph(rgi_edl, 60.0, True, rgi_tl, None, [], index,
                          preview=False, W=720, H=720, fps=30.0,
                          frame_mode=None,
                          insert_inputs=[(2, rgi_edl["inserts"][0], True)],
                          silence_idx=1)
check("whole-video region censors each source segment once",
      g_rgi.count("gblur") == 2)
check("inserted clips are never censored",
      "[insv0]scale=" in g_rgi)

print("== Round-12 blur_region / remove_blur tools ==")
tctx = ToolCtx({"keep": [[0.0, 20.0]]})
r = agent_tools.blur_region(tctx, 0.6, 0.02, 0.38, 0.1)
check("blur_region writes a region with an id, blur mode implicit",
      tctx.written["effects"]["regions"][0]["id"] == "rg1" and
      "mode" not in tctx.written["effects"]["regions"][0])
check("blur_region success demands sheet verification",
      "CHECK the sheet" in r)
check("blur_region rejects pixel coordinates with guidance",
      agent_tools.blur_region(ToolCtx({}), 300, 20, 200, 50)
      .startswith("REJECTED"))
check("blur_region rejects an unknown mode",
      agent_tools.blur_region(ToolCtx({}), 0.1, 0.1, 0.2, 0.2,
                              mode="rainbow").startswith("REJECTED"))
check("blur_region rejects start without end",
      agent_tools.blur_region(ToolCtx({}), 0.1, 0.1, 0.2, 0.2, start=1.0)
      .startswith("REJECTED"))
RG_EDL = {"keep": [[0.0, 20.0]],
          "effects": {"regions": [{"id": "rg1", "x": 0.1, "y": 0.1,
                                   "w": 0.2, "h": 0.2}]}}
rmctx = ToolCtx(json.loads(json.dumps(RG_EDL)))
agent_tools.remove_blur(rmctx)
check("remove_blur with no id clears all regions",
      rmctx.written["effects"]["regions"] == [])
check("remove_blur unknown id lists existing ones",
      "rg1" in agent_tools.remove_blur(
          ToolCtx(json.loads(json.dumps(RG_EDL))), "zz"))
check("remove_blur with none present is NO CHANGE",
      agent_tools.remove_blur(ToolCtx({"keep": [[0.0, 5.0]]}))
      .startswith("NO CHANGE"))
check("capabilities digest advertises the censor tool",
      "blur_region" in agent_tools.capabilities_digest())
# review round: x=1.0 would clamp to a useless 1% sliver reported as
# success; an empty-string id must not silently mean 'remove all'
check("blur_region rejects a box that falls off the frame edge",
      "TOP-LEFT" in agent_tools.blur_region(ToolCtx({}), 1.0, 0.4,
                                            0.3, 0.1))
check("remove_blur rejects an empty-string id",
      agent_tools.remove_blur(ToolCtx(json.loads(json.dumps(RG_EDL))),
                              "").startswith("REJECTED"))
check("schema coerces mode null to blur (TS mirror allows null)",
      validate_edl({"keep": [[0, 30]],
                    "effects": {"regions": [{"id": "rg1", "mode": None,
                                             "x": 0.6, "y": 0.02,
                                             "w": 0.38, "h": 0.1}]}},
                   60).effects.regions[0].mode == "blur")

print("== Round-12 honesty: versioned render claims + echo guard ==")
PROD_ECHO = ("The preview is now clean: no black frames, no distortion, no "
             "overexposure — just a consistent desert scene with the text "
             "overlay and an intentional costume change at 0:01–0:02.\n\n"
             "Final edit:\n- Kept [0.0–1.8s]: stable opening shot\n"
             "- Dip-black transition masks the jump\n"
             "- Total duration: 4.06 seconds\n\n"
             "Preview v6 is attached and ready.")
check("versioned render claim is caught ('Preview v6 is attached')",
      al.RENDER_CLAIM.search("Preview v6 is attached and ready."))
check("the job-135 echoed reply now violates on a zero-render turn",
      len(al._reply_violations(PROD_ECHO, wrote=False, previewed=False))
      >= 1)
check("honest 'no preview was rendered' still passes",
      al._reply_violations("No preview was rendered because nothing "
                           "changed.", wrote=False, previewed=False) == [])
echo_msgs = [{"role": "system", "content": "facts"},
             {"role": "assistant", "content": PROD_ECHO},
             {"role": "user", "content": "The edit failed. Start over."}]
check("verbatim echo of the previous reply is detected",
      al._echo_violation(PROD_ECHO, echo_msgs) is not None)
check("near-verbatim echo (whitespace/case drift) is detected",
      al._echo_violation(PROD_ECHO.upper().replace("\n", "  "),
                         echo_msgs) is not None)
check("a fresh reply is not an echo",
      al._echo_violation(
          "Understood — I restored the full 15-second video, removed the "
          "dip-black transitions, and rendered a new preview so you can "
          "confirm the original footage is back untouched.",
          echo_msgs) is None)
check("short repeated answers are exempt",
      al._echo_violation("Yes.", [{"role": "assistant",
                                   "content": "Yes."}]) is None)
check("this turn's tool-call carrier messages are ignored",
      al._echo_violation(PROD_ECHO,
                         [{"role": "assistant", "content": PROD_ECHO,
                           "tool_calls": [{"id": "t1"}]}]) is None)
# review round: a longer fresh reply sharing a formulaic opener with a
# short earlier reply must not be clipped to the opener's length and
# flagged; only a truncated history copy justifies prefix comparison
OPENER = ("I placed a blur region over the username in the top-right "
          "corner and rendered a fresh preview for you to check.")
check("longer fresh reply sharing an opener is NOT an echo",
      al._echo_violation(
          OPENER + " This time I widened the box to cover the last two "
          "characters that were still visible, re-rendered, and the sheet "
          "now shows the whole tag hidden across every frame I sampled — "
          "the gameplay around it is untouched and everything else in the "
          "edit stays exactly as it was.",
          [{"role": "assistant", "content": OPENER}]) is None)
LONGPREV = ("the preview shows the full edit with every requested change "
            "applied and nothing else modified anywhere in the timeline. "
            ) * 20
check("echo of a truncation-length reply is still caught by prefix",
      al._echo_violation(LONGPREV + "and this tail differs completely "
                         "from anything stored in the history copy",
                         [{"role": "assistant",
                           "content": LONGPREV[:2000]}]) is not None)

print("== Round-12 keep_segments large-drop warning ==")


class DropCtx(ToolCtx):
    def __init__(self, edl):
        super().__init__(edl)
        self.index = {"words": [], "silences": [],
                      "video": {"duration": 60.0}}
        self.duration = 60.0

    def clamp(self, t):
        return round(min(max(float(t), 0.0), 60.0), 2)


r = agent_tools.keep_segments(DropCtx({"keep": [[0.0, 60.0]]}), [[0.0, 4.0]])
check("dropping 93% of the kept footage warns loudly",
      "WARNING (large drop)" in r and "EXPLICITLY" in r)
r2 = agent_tools.keep_segments(DropCtx({"keep": [[0.0, 60.0]]}),
                               [[0.0, 40.0]])
check("a modest trim does not warn", "large drop" not in r2)
# review round: cut_range can destroy just as much footage as keep_segments
r3 = agent_tools.cut_range(DropCtx({"keep": [[0.0, 60.0]]}), 0, 45)
check("an equally destructive cut_range warns too",
      "WARNING (large drop)" in r3)

print("== Round-12 review: shortening clamps region windows ==")
RGWIN_EDL = {"keep": [[0.0, 60.0]],
             "effects": {"regions": [{"id": "rg1", "x": 0.6, "y": 0.02,
                                      "w": 0.38, "h": 0.1,
                                      "start": 10.0, "end": 40.0}]}}
wctx = DropCtx(json.loads(json.dumps(RGWIN_EDL)))
r = agent_tools.keep_segments(wctx, [[0.0, 20.0]])
check("shortening below a region window succeeds and clamps it",
      r.startswith("EDL v") and "now ends at 20.0s" in r and
      wctx.written["effects"]["regions"][0]["end"] == 20.0)
RGOUT_EDL = {"keep": [[0.0, 60.0]],
             "effects": {"regions": [{"id": "rg1", "x": 0.6, "y": 0.02,
                                      "w": 0.38, "h": 0.1,
                                      "start": 30.0, "end": 50.0}]}}
octx = DropCtx(json.loads(json.dumps(RGOUT_EDL)))
r = agent_tools.keep_segments(octx, [[0.0, 20.0]])
check("a region whose window vanishes is dropped with a note",
      r.startswith("EDL v") and "was removed" in r and
      octx.written["effects"]["regions"] == [])
check("the stored previous version was not mutated in place",
      wctx._edl["json"]["effects"]["regions"][0]["end"] == 40.0)

print("== Round-13: caption size_scale (continuous magnitude) ==")
from schemas import CaptionStyle                              # noqa: E402
check("size_scale 1.0 collapses to None (neutral no-op)",
      CaptionStyle(size_scale=1.0).size_scale is None)
check("size_scale clamps above 3.0",
      CaptionStyle(size_scale=9).size_scale == 3.0)
check("size_scale clamps below 0.5",
      CaptionStyle(size_scale=0.1).size_scale == 0.5)


def _font_of(style):
    return int(caplib.style_line("Default", style).split(",")[2])


base_font = _font_of({"size": "m"})
scaled_font = _font_of({"size": "m", "size_scale": 1.5})
check("size_scale 1.5 makes the font ~50% bigger",
      scaled_font == round(base_font * 1.5))
check("size_scale changes line_chars wrapping too",
      caplib.line_chars_for({"size": "m", "size_scale": 2.0}) <
      caplib.line_chars_for({"size": "m"}))
# size_scale survives validate_edl and an edl_signature round-trip
_ss_edl = validate_edl({"keep": [[0, 60]], "captions": {
    "mode": "from_transcript", "style": {"size_scale": 1.4}}}, 60).model_dump()
check("size_scale persists through EDL validation",
      _ss_edl["captions"]["style"]["size_scale"] == 1.4)

print("== Round-13: compound editing tools ==")
import agent_tools                                            # noqa: E402

# cut_silences: cuts every gap >= threshold, keeping padding around speech
_cs = DropCtx({"keep": [[0.0, 60.0]]})
_cs.index = {"words": [], "video": {"duration": 60.0},
             "silences": [[10.0, 12.0], [20.0, 25.0], [30.0, 30.3]]}
r = agent_tools.cut_silences(_cs, min_silence_s=0.5, padding_s=0.12)
check("cut_silences cut the two long gaps, kept the sub-padding one",
      r.startswith("EDL v") and "cut 2 silence" in r)
_kept = output_duration([list(x) for x in _cs.written["keep"]])
check("cut_silences kept ~53.5s (60 - 1.76 - 4.76)",
      53.0 < _kept < 54.0)
_cs2 = DropCtx({"keep": [[0.0, 60.0]]})
_cs2.index = {"words": [], "video": {"duration": 60.0}, "silences": []}
check("cut_silences reports nothing to cut when there are no silences",
      "No silences" in agent_tools.cut_silences(_cs2))

# remove_filler_words: cuts exact word spans for um/uh/etc.
_fw = DropCtx({"keep": [[0.0, 60.0]]})
_fw.index = {"video": {"duration": 60.0}, "silences": [], "words": [
    {"w": "So", "t0": 0.0, "t1": 0.4}, {"w": "um,", "t0": 0.4, "t1": 0.9},
    {"w": "this", "t0": 0.9, "t1": 1.3}, {"w": "uh", "t0": 5.0, "t1": 5.4},
    {"w": "works", "t0": 5.4, "t1": 6.0}]}
r = agent_tools.remove_filler_words(_fw)
check("remove_filler_words cut the um and uh spans",
      r.startswith("EDL v") and "'uh'×1" in r and "'um'×1" in r)
_fw2 = DropCtx({"keep": [[0.0, 60.0]]})
_fw2.index = {"video": {"duration": 60.0}, "silences": [],
              "words": [{"w": "clean", "t0": 0.0, "t1": 0.5}]}
check("remove_filler_words reports none found when transcript is clean",
      "No filler words" in agent_tools.remove_filler_words(_fw2))

check("both compound tools are registered write tools",
      {"cut_silences", "remove_filler_words"} <= agent_tools.WRITE_TOOLS and
      "cut_silences" in agent_tools.capabilities_digest())

print("== Round-13: add_music clamps to FULL program duration ==")
_mus_asset = {"kind": "music", "storage_key": "music/1/song.mp3"}
_am_edl = {"keep": [[0.0, 30.0]], "inserts": [
    {"id": "ins1", "asset_key": "clips/1/b.mp4", "kind": "video",
     "at_output_s": 30.0, "duration_s": 10.0}]}
_am = ToolCtx(json.loads(json.dumps(_am_edl)), asset=_mus_asset)
r = agent_tools.add_music(_am, "music/1/song.mp3", 0, 40)
check("music end reaches 40s (30s kept + 10s insert), not clamped to 30",
      _am.written["music"][-1]["end"] == 40.0)

print("== Round-13: full index in context (Q1) ==")
import agent_loop                                             # noqa: E402
_short_index = {
    "video": {"duration": 120.0, "fps": 30, "width": 1920, "height": 1080,
              "has_audio": True},
    "sentences": [{"id": "s1", "t0": 0.0, "t1": 3.0, "text": "Hello there."},
                  {"id": "s2", "t0": 3.0, "t1": 6.0, "text": "Second line."}],
    "words": [{"w": "Hello", "t0": 0.0, "t1": 0.5}],
    "shots": [{"id": 1, "start": 0.0, "end": 6.0,
               "caption": {"setting": "office", "people": "one man",
                           "action": "talking", "on_screen_text": ""}}],
    "silences": [], "language": "en"}
_full = agent_loop._index_summary(_short_index)
check("short video inlines the COMPLETE transcript + shots + language",
      "COMPLETE" in _full and "Second line." in _full and
      "office" in _full and "LANGUAGE (detected): en" in _full)
_long_index = dict(_short_index, video=dict(_short_index["video"],
                                            duration=3600.0))
_elided = agent_loop._index_summary(_long_index)
check("long video falls back to elided summary + retrieval pointers",
      "COMPLETE" not in _elided and "get_transcript" in _elided)

print("== Round-13: render verification (duration check) ==")
import media                                                 # noqa: E402
import renderer                                               # noqa: E402
_ok_edl = {"keep": [[0.0, 10.0]]}
_orig_black = media.black_seconds
media.black_seconds = lambda *a, **k: 0.0        # keep the check pure (no ffmpeg)
try:
    renderer._verify_render(_ok_edl, "x.mp4", 10.0, 1, "preview")
    check("a correct-length render passes verification", True)
    try:
        renderer._verify_render(_ok_edl, "x.mp4", 4.0, 1, "preview")
        check("a wrong-length render is rejected", False)
    except media.MediaError as e:
        check("a wrong-length render raises MediaError",
              "wrong length" in str(e))
finally:
    media.black_seconds = _orig_black

print("== Round-13 review fixes ==")
# remove_filler_words: multi-word phrases now match consecutive words
_fwp = DropCtx({"keep": [[0.0, 60.0]]})
_fwp.index = {"video": {"duration": 60.0}, "silences": [], "words": [
    {"w": "So", "t0": 0.0, "t1": 0.4}, {"w": "you", "t0": 0.4, "t1": 0.7},
    {"w": "know", "t0": 0.7, "t1": 1.0}, {"w": "this", "t0": 1.0, "t1": 1.3}]}
r = agent_tools.remove_filler_words(_fwp, words=["you know"])
check("remove_filler_words matches a multi-word phrase",
      r.startswith("EDL v") and "'you know'×1" in r)
# default list no longer cuts affirmations (uh-huh / mm / hmm)
_fwa = DropCtx({"keep": [[0.0, 60.0]]})
_fwa.index = {"video": {"duration": 60.0}, "silences": [], "words": [
    {"w": "uh-huh", "t0": 0.0, "t1": 0.5}, {"w": "yeah", "t0": 0.5, "t1": 1.0},
    {"w": "mm", "t0": 1.0, "t1": 1.3}]}
check("default filler list no longer cuts affirmations",
      "No filler words" in agent_tools.remove_filler_words(_fwa))

# render verification: black check is relative to the SOURCE, duration clamps
import media                                                  # noqa: E402
import renderer                                               # noqa: E402
_ob = media.black_seconds
try:
    media.black_seconds = lambda p, d=None: d      # every frame black
    try:
        renderer._verify_render({"keep": [[0.0, 10.0]]}, "out.mp4", 10.0, 1,
                                "final", src_path="src.mp4", src_dur=10.0)
        check("mostly-black output passes when the SOURCE is also black", True)
    except media.MediaError:
        check("mostly-black output passes when the SOURCE is also black", False)
    media.black_seconds = lambda p, d=None: (d if "out" in p else 0.0)
    try:
        renderer._verify_render({"keep": [[0.0, 10.0]]}, "out.mp4", 10.0, 1,
                                "final", src_path="src.mp4", src_dur=10.0)
        check("newly-black output (black where source wasn't) is rejected", False)
    except media.MediaError as e:
        check("newly-black output (black where source wasn't) is rejected",
              "black" in str(e))
    media.black_seconds = lambda p, d=None: 0.0
    try:
        # keep claims 120s but real source is 90s; a 90s output must PASS
        renderer._verify_render({"keep": [[0.0, 120.0]]}, "out.mp4", 90.0, 1,
                                "final", src_path="s", src_dur=90.0)
        check("duration check clamps keep ends to the real source duration", True)
    except media.MediaError:
        check("duration check clamps keep ends to the real source duration", False)
finally:
    media.black_seconds = _ob

print("== Grok/xAI provider switch ==")
import config as _cfg                                         # noqa: E402
import llm as _llm                                           # noqa: E402
_save = (_cfg.OPENAI_BASE_URL, _cfg.OPENAI_API_KEY,
         _cfg.IMAGE_GEN_MODEL, _cfg.IMAGE_API_URL)
try:
    _cfg.OPENAI_API_KEY = "k"
    _cfg.IMAGE_GEN_MODEL = "grok-2-image"
    _cfg.IMAGE_API_URL = ""
    _cfg.OPENAI_BASE_URL = "https://api.x.ai/v1"
    check("xAI base -> openai image provider (generate only, no editing)",
          _llm.image_provider() == "openai" and _llm.image_available()
          and not _llm.image_edit_available())
    _cfg.OPENAI_BASE_URL = \
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    check("dashscope base -> dashscope provider (editing available)",
          _llm.image_provider() == "dashscope" and _llm.image_available()
          and _llm.image_edit_available())
    _cfg.IMAGE_GEN_MODEL = ""
    check("empty IMAGE_GEN_MODEL disables image gen everywhere",
          not _llm.image_available())
finally:
    (_cfg.OPENAI_BASE_URL, _cfg.OPENAI_API_KEY,
     _cfg.IMAGE_GEN_MODEL, _cfg.IMAGE_API_URL) = _save

print("== Round-13: timeline golden vectors (cross-repo drift tripwire) ==")
import json as _json                                          # noqa: E402
with open(os.path.join(os.path.dirname(__file__),
                       "timeline_golden.json")) as _f:
    _golden = _json.load(_f)
for _c in _golden["cases"]:
    _tl = Timeline(_c["keep"], _c["inserts"])
    check(f"golden[{_c['name']}] out_duration",
          _tl.out_duration == _c["out_duration"])
    check(f"golden[{_c['name']}] src_to_out",
          all(_tl.src_to_out(t) == exp for t, exp in _c["src_to_out"]))
    check(f"golden[{_c['name']}] out_to_src",
          all(_tl.out_to_src(t) == exp for t, exp in _c["out_to_src"]))

print("== Round-16: frame_at never lies about writing a frame ==")
# Regression: an index died with "[Errno 2] ... thumbs/shot_1.jpg" and the
# user was told "I couldn't analyze that video. Try a different format like
# mp4" — for a video that HAD been analyzed fine. Cause: through ffmpeg 6 a
# seek past the last frame exits 0 without writing the file, so frame_at
# reported success, the caller kept the path, and the upload blew up. These
# stub media.run to play each ffmpeg behaviour without invoking ffmpeg.
import media as mediamod                                      # noqa: E402

_real_run = mediamod.run
_tmpd = tempfile.mkdtemp()


def _stub_run(behaviours):
    """behaviours: list of 'nothing' | 'empty' | 'frame' | 'fail', one per
    ffmpeg invocation, so seek-fallback ordering is testable."""
    calls = {"n": 0}

    def _run(cmd, timeout=None, progress_cb=None, expected_out_s=None):
        b = behaviours[min(calls["n"], len(behaviours) - 1)]
        calls["n"] += 1
        dst = cmd[-1]
        if b == "fail":
            raise mediamod.MediaError("ffmpeg failed: synthetic")
        if b == "empty":
            open(dst, "wb").close()
        elif b == "frame":
            with open(dst, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0jpegbytes")
        return ""
    return _run, calls


try:
    # ffmpeg 5.x flavour: exit 0, no file. MUST raise, not return a bad path.
    mediamod.run, _c = _stub_run(["nothing", "nothing"])
    _dst = os.path.join(_tmpd, "a.jpg")
    try:
        mediamod.frame_at("p.mp4", 99.0, _dst)
        check("exit-0-but-no-file raises instead of returning a phantom path",
              False)
    except mediamod.MediaError as e:
        check("exit-0-but-no-file raises instead of returning a phantom path",
              "no frame at 99.000s" in str(e) and "wrote no frame" in str(e))
    check("both seek modes are tried before giving up", _c["n"] == 2)

    # A zero-byte file must not pass as a frame, and must not be left behind
    # to fool the next existence check.
    mediamod.run, _c = _stub_run(["empty", "empty"])
    _dst = os.path.join(_tmpd, "b.jpg")
    try:
        mediamod.frame_at("p.mp4", 1.0, _dst)
        check("zero-byte output is rejected", False)
    except mediamod.MediaError:
        check("zero-byte output is rejected", True)
    check("zero-byte leftover is cleaned up", not os.path.exists(_dst))

    # The happy path still returns, and stops after the fast seek.
    mediamod.run, _c = _stub_run(["frame"])
    _dst = os.path.join(_tmpd, "c.jpg")
    check("a real frame returns the path", mediamod.frame_at("p.mp4", 1.0,
                                                             _dst) == _dst)
    check("fast input seek alone is enough on the happy path", _c["n"] == 1)

    # Sparse keyframes / edit lists (phone screen recordings): input seek
    # yields nothing, output seek lands the frame — must recover, not fail.
    mediamod.run, _c = _stub_run(["nothing", "frame"])
    _dst = os.path.join(_tmpd, "d.jpg")
    check("output seek recovers a frame input seek missed",
          mediamod.frame_at("p.mp4", 5.0, _dst) == _dst and _c["n"] == 2)

    # A hard ffmpeg error on the first mode still gets the second chance.
    mediamod.run, _c = _stub_run(["fail", "frame"])
    _dst = os.path.join(_tmpd, "e.jpg")
    check("a failed input seek still tries output seek",
          mediamod.frame_at("p.mp4", 5.0, _dst) == _dst)
finally:
    mediamod.run = _real_run

# The indexer must treat thumbnails as cosmetic: shipped source is checked
# so the isolation can't be refactored away silently.
_idx_src = open(os.path.join(os.path.dirname(__file__), "..",
                             "indexer.py")).read()
_thumb_fn = None
for node in ast.walk(ast.parse(_idx_src)):
    if isinstance(node, ast.FunctionDef) and node.name == "run_index_job":
        _thumb_fn = node
check("index job found", _thumb_fn is not None)
# every storage.upload_file for a thumb/sheet sits inside a try
_guarded = []
for node in ast.walk(_thumb_fn):
    if isinstance(node, ast.Try):
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and
                    getattr(sub.func, "attr", "") == "upload_file"):
                _guarded.append(sub.lineno)
check("thumb + sheet uploads are inside try blocks", len(_guarded) >= 2)
check("a thumb failure degrades to a warning",
      "shot thumbnails could not be" in _idx_src)
check("thumbnail seeks are clamped to the proxy's real duration",
      "seek_ceiling" in _idx_src and "min(mid, seek_ceiling)" in _idx_src)

print("== Round-16b: probe describes the video a PLAYER shows ==")
# Regression: an index claimed 16.654s / 1284x2778 portrait for a clip whose
# own proxy came out 2.374s / 1558x720 landscape. The agent announced "0.3 min"
# while the player showed 0:02, and every shot pointed past the last frame —
# which is what made shot_1's thumbnail unmakeable in the first place.
_probe_json = {
    "format": {"duration": "16.654"},
    "streams": [
        {"codec_type": "video", "width": 1284, "height": 2778,
         "duration": "2.374", "r_frame_rate": "600/1",
         "avg_frame_rate": "14517/250",
         "side_data_list": [{"side_data_type": "Display Matrix",
                             "rotation": 90}]},
        {"codec_type": "audio", "duration": "16.654"},
    ],
}
_real_run2 = mediamod.run
try:
    mediamod.run = lambda *a, **k: _json.dumps(_probe_json)
    _p = mediamod.probe("phone.mp4")
    # display size, not coded size: ffmpeg auto-rotates before -vf, so the
    # 1558x720 proxy it produced was landscape while the index said portrait
    check("rotated source reports DISPLAY size (landscape)",
          (_p["width"], _p["height"]) == (2778, 1284))
    check("container duration is still what a player shows",
          _p["duration"] == 16.654)
    check("picture track's own length is reported separately",
          _p["video_duration"] == 2.374)
    check("fps + vfr unchanged", _p["fps"] == 58.068 and _p["vfr"] is True)

    # 180 must NOT swap; 0/absent must not swap
    _probe_json["streams"][0]["side_data_list"] = [{"rotation": 180}]
    check("180 rotation does not swap width/height",
          (mediamod.probe("x")["width"], mediamod.probe("x")["height"])
          == (1284, 2778))
    _probe_json["streams"][0]["side_data_list"] = [{"rotation": -90}]
    check("-90 rotation swaps too",
          mediamod.probe("x")["width"] == 2778)
    del _probe_json["streams"][0]["side_data_list"]
    _probe_json["streams"][0]["tags"] = {"rotate": "90"}
    check("older ffprobe 'rotate' tag is honoured",
          mediamod.probe("x")["width"] == 2778)
    del _probe_json["streams"][0]["tags"]
    check("unrotated source keeps coded size",
          (mediamod.probe("x")["width"], mediamod.probe("x")["height"])
          == (1284, 2778))
    # a container with no per-stream duration must not crash or invent one
    del _probe_json["streams"][0]["duration"]
    check("missing per-stream duration -> video_duration None",
          mediamod.probe("x")["video_duration"] is None)
finally:
    mediamod.run = _real_run2

# make_proxy holds the last frame when the picture track runs out early.
_enc_calls = []


def _fake_encode(src, dst, fps, vfr, has_audio, pad_s=0.0, progress_cb=None,
                 expected_out_s=None):
    _enc_calls.append(round(pad_s, 3))


_real_enc, _real_probe = mediamod._encode_proxy, mediamod.probe
try:
    mediamod._encode_proxy = _fake_encode
    # picture ends at 2.374 of a 16.654s recording -> pad the 14.28s remainder
    mediamod.probe = lambda p: {"duration": 16.672, "video_duration": 2.374}
    _enc_calls.clear()
    mediamod.make_proxy("s", "d", 58.068, True, True, duration=16.654)
    check("short picture track triggers a second, padded encode",
          len(_enc_calls) == 2 and _enc_calls[0] == 0.0)
    check("pad exactly covers the gap (14.28s)", _enc_calls[1] == 14.28)

    # a normal video must never be re-encoded
    mediamod.probe = lambda p: {"duration": 16.67, "video_duration": 16.67}
    _enc_calls.clear()
    mediamod.make_proxy("s", "d", 30.0, False, True, duration=16.654)
    check("normal video encodes once, unpadded", _enc_calls == [0.0])

    # rounding on the last frame must not trip it
    mediamod.probe = lambda p: {"duration": 16.65, "video_duration": 16.42}
    _enc_calls.clear()
    mediamod.make_proxy("s", "d", 30.0, False, True, duration=16.654)
    check("last-frame rounding is not mistaken for a short track",
          _enc_calls == [0.0])

    # no duration passed -> caller opted out, single encode, no probe
    mediamod.probe = lambda p: (_ for _ in ()).throw(AssertionError("probed"))
    _enc_calls.clear()
    mediamod.make_proxy("s", "d", 30.0, False, True)
    check("without an expected duration make_proxy stays a single encode",
          _enc_calls == [0.0])
finally:
    mediamod._encode_proxy, mediamod.probe = _real_enc, _real_probe

# The final render (from the ORIGINAL) must hold the frame exactly like the
# proxy, or an approved preview and its export disagree.
_pad_tl = Timeline([[0.0, 16.654]], [])
_g = build_filtergraph({"keep": [[0.0, 16.654]], "captions": {"enabled": False}},
                       16.654, True, _pad_tl, None, [], {"words": []}, True,
                       W=720, H=1280, fps=30.0, src_w=720, src_h=1280,
                       src_pad=14.28)
check("render holds the last frame across a short picture track",
      "tpad=stop_mode=clone:stop_duration=14.280" in _g and "[vpad]" in _g)
_g0 = build_filtergraph({"keep": [[0.0, 16.654]], "captions": {"enabled": False}},
                        16.654, True, _pad_tl, None, [], {"words": []}, True,
                        W=720, H=1280, fps=30.0, src_w=720, src_h=1280)
check("a normal render graph is untouched (no tpad)",
      "tpad" not in _g0 and "[vpad]" not in _g0)
_gs = build_filtergraph({"keep": [[0.0, 5.0], [8.0, 16.0]],
                         "captions": {"enabled": False}},
                        16.654, True, Timeline([[0.0, 5.0], [8.0, 16.0]], []),
                        None, [], {"words": []}, True,
                        W=720, H=1280, fps=30.0, src_w=720, src_h=1280,
                        src_pad=14.28)
check("multi-segment renders split from the padded source, not the raw one",
      "[vpad]split=2" in _gs)


# ---------------------------------------------------------------- #
#  Round-18: a stale program-time effect can't reject a cut          #
# ---------------------------------------------------------------- #
# Regression from production (project 39, EDL v8): the agent tried to trim the
# screen-recording UI off both ends and BOTH cuts were rejected by a zoom that
# the cut itself invalidated —
#   cut_range{"start": 0.0, "end": 2.0}   -> REJECTED: effects.zooms[2]: end
#                                            14.5 exceeds the limit 13.4s.
#   cut_range{"start": 13.4, "end": 15.4} -> REJECTED: (same)
# It then burned 4 calls deleting and re-adding the zooms by hand. Worse, the
# surviving zoom silently kept its old output time and so landed on DIFFERENT
# footage after the shift.
print("== round-18: cuts are not blocked by their own side effects ==")

from agent_tools import _write_keep, cut_range              # noqa: E402
from timeline import remap_program_span                     # noqa: E402


class EdlStubCtx(StubCtx):
    """Exercises the real _write_keep -> validate_edl path."""

    def __init__(self, index, duration, edl):
        super().__init__(index, duration)
        self._rows = [{"version": 8, "json": edl}]
        self.versions_written = []

    def latest_edl(self):
        return self._rows[-1]

    def write_edl(self, new_edl_dict, change_desc):
        prev = self.latest_edl()
        try:
            normalized = validate_edl(new_edl_dict, self.duration).model_dump()
        except EDLValidationError as e:
            return f"REJECTED (EDL v{prev['version']} unchanged): {e}"
        if edl_signature(normalized) == edl_signature(prev["json"]):
            return "NO CHANGE"
        v = prev["version"] + 1
        self._rows.append({"version": v, "json": normalized})
        self.versions_written.append(v)
        return f"EDL v{prev['version']} -> v{v}: {change_desc}."


# The real v8, byte for byte.
def _v8():
    return {"keep": [[0.0, 15.4]], "music": [], "volume": [],
            "effects": {"grade": "cinematic", "zooms": [
                {"id": "zm1", "start": 0.3, "end": 2.0, "strength": 0.2,
                 "mode": "push_in"},
                {"id": "zm2", "start": 8.5, "end": 11.0, "strength": 0.25,
                 "mode": "ease"},
                {"id": "zm3", "start": 12.0, "end": 14.5, "strength": 0.2,
                 "mode": "push_in"}]}}


_idx18 = {"words": [], "silences": []}
ctx18 = EdlStubCtx(_idx18, 16.654, _v8())
r18 = cut_range(ctx18, 0.0, 2.0)
check("the production cut that used to be rejected now succeeds",
      r18.startswith("EDL v8 -> v9") and "REJECTED" not in r18)
_z18 = {z["id"]: (z["start"], z["end"])
        for z in ctx18.latest_edl()["json"]["effects"]["zooms"]}
check("zm1 is dropped — the footage it zoomed on was cut away",
      "zm1" not in _z18)
check("zm2 follows its footage 2s earlier (8.5-11.0 -> 6.5-9.0)",
      _z18["zm2"] == (6.5, 9.0))
check("zm3 shifts with the cut too (12.0-14.5 -> 10.0-12.5)",
      _z18["zm3"] == (10.0, 12.5))
check("every surviving zoom fits the new 13.4s program",
      all(e <= 13.4 + 0.01 for _, e in _z18.values()))
check("the agent is told what moved and why",
      "zm1" in r18 and "no longer in the edit" in r18
      and "stays on the same footage" in r18)

# The other real call: trimming the TAIL must not disturb what precedes it.
ctx18b = EdlStubCtx(_idx18, 16.654, _v8())
r18b = cut_range(ctx18b, 13.4, 15.4)
_z18b = {z["id"]: (z["start"], z["end"])
         for z in ctx18b.latest_edl()["json"]["effects"]["zooms"]}
check("the tail cut that used to be rejected now succeeds",
      r18b.startswith("EDL v8 -> v9"))
check("a tail cut leaves earlier zooms exactly where they were",
      _z18b["zm1"] == (0.3, 2.0) and _z18b["zm2"] == (8.5, 11.0))
check("a zoom straddling the cut keeps only the surviving part",
      _z18b["zm3"] == (12.0, 13.4))

# Program-anchored collections clamp instead of following content.
_mv = {"keep": [[0.0, 15.4]],
       "music": [{"id": "mus1", "storage_key": "music/1/a.mp3", "start": 0.0,
                  "end": 15.4, "gain_db": -18.0, "duck": True},
                 {"id": "mus2", "storage_key": "music/1/b.mp3", "start": 14.0,
                  "end": 15.4, "gain_db": -18.0, "duck": True}],
       "voiceover": [{"id": "vo1", "asset_key": "music/1/v.mp3",
                      "start_output_s": 14.5, "gain_db": 0.0}],
       "volume": []}
ctx18c = EdlStubCtx(_idx18, 16.654, _mv)
r18c = cut_range(ctx18c, 0.0, 2.0)
_j = ctx18c.latest_edl()["json"]
check("music under the whole video still covers the shortened program",
      r18c.startswith("EDL v8 -> v9")
      and _j["music"][0]["start"] == 0.0 and _j["music"][0]["end"] == 13.4)
check("music starting past the new end is dropped, not left to reject the cut",
      all(m["id"] != "mus2" for m in _j["music"]))
check("voiceover starting past the new end is dropped too",
      _j["voiceover"] == [])
check("music/voiceover removals are disclosed",
      "mus2" in r18c and "vo1" in r18c)

# A cut that touches nothing must stay byte-identical.
ctx18d = EdlStubCtx(_idx18, 16.654, _v8())
r18d = cut_range(ctx18d, 15.0, 15.4)
check("a cut clear of every effect leaves the zooms untouched",
      r18d.startswith("EDL v8 -> v9")
      and [(z["start"], z["end"])
           for z in ctx18d.latest_edl()["json"]["effects"]["zooms"]]
      == [(0.3, 2.0), (8.5, 11.0), (12.0, 14.5)])

# The mapping itself.
_ot, _nt = Timeline([[0.0, 15.4]]), Timeline([[0.0, 5.0], [10.0, 15.4]])
check("a span straddling an internal cut maps to one contiguous span",
      remap_program_span(_ot, _nt, 3.0, 12.0) == (3.0, 7.0))
check("a span wholly inside a removed region maps to nothing",
      remap_program_span(_ot, _nt, 6.0, 9.0) is None)

# ---------------------------------------------------------------- #
#  Round-18: Deepgram transcription (parsing/dispatch/fallback)      #
# ---------------------------------------------------------------- #
# The live call needs a real key and is NOT covered here — everything below is
# the logic around it, which is where the bugs would be.
print("== round-18: deepgram transcription ==")

import tempfile                                             # noqa: E402
import types                                                # noqa: E402
import transcribe as tr                                     # noqa: E402


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


_dg_ok = {"results": {"channels": [{
    "detected_language": "en",
    "alternatives": [{"words": [
        {"word": "damn", "punctuated_word": "Damn.", "start": 8.1, "end": 8.5},
        {"word": "ok", "punctuated_word": "OK", "start": 9.0, "end": 9.24},
    ]}]}]}}
_w, _lang = tr._parse_deepgram(_dg_ok)
check("deepgram words keep the punctuation group_sentences splits on",
      [x.w for x in _w] == ["Damn.", "OK"])
check("deepgram word timestamps survive", (_w[0].t0, _w[0].t1) == (8.1, 8.5))
check("deepgram detected language is used", _lang == "en")
check("punctuated deepgram words group into sentences",
      [s.text for s in group_sentences(_w)] == ["Damn.", "OK"])
check("a deepgram word with no timestamps raises, never a silent gap",
      _raises(lambda: tr._parse_deepgram({"results": {"channels": [{
          "alternatives": [{"words": [{"word": "hi"}]}]}]}})))
# An empty transcript is what the agent turns into "this video has no speech" —
# a shape we don't understand must never masquerade as that.
check("an unrecognised deepgram shape raises instead of 'no speech'",
      _raises(lambda: tr._parse_deepgram({"results": {"channels": []}}))
      and _raises(lambda: tr._parse_deepgram({})))
check("deepgram falling back to the bare 'word' still works",
      [x.w for x in tr._parse_deepgram({"results": {"channels": [{
          "alternatives": [{"words": [
              {"word": "hi", "start": 0.0, "end": 0.2}]}]}]}})[0]] == ["hi"])

# --- dispatch + fallback ---
_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
_wav.write(b"RIFF....WAVEfmt "); _wav.close()

_orig = (wconfig.TRANSCRIBER, wconfig.DEEPGRAM_API_KEY, tr.requests,
         tr._transcribe_whisper)
_whisper_hits = []
tr._transcribe_whisper = lambda p: (_whisper_hits.append(p) or
                                    ([Word(w="local", t0=0.0, t1=0.1)], "en"))

wconfig.TRANSCRIBER = "whisper"
_warns = []
tr.transcribe(_wav.name, _warns)
check("whisper stays the default when no deepgram key is set",
      len(_whisper_hits) == 1 and _warns == [])


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload or {}, text

    def json(self):
        return self._p


def _stub_post(responses):
    calls = []

    def post(url, params=None, data=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    tr.requests = types.SimpleNamespace(
        post=post, RequestException=_orig[2].RequestException)
    return calls


wconfig.TRANSCRIBER = "deepgram"
wconfig.DEEPGRAM_API_KEY = "test-key"
tr.time.sleep = lambda s: None      # no real backoff in tests

_calls = _stub_post([_Resp(200, _dg_ok)])
_whisper_hits.clear(); _warns = []
_words, _lg = tr.transcribe(_wav.name, _warns)
check("deepgram is used when configured, whisper untouched",
      [x.w for x in _words] == ["Damn.", "OK"] and _whisper_hits == []
      and _warns == [])
check("the request carries the model, key and word-timestamp formatting",
      _calls[0]["params"]["model"] == wconfig.DEEPGRAM_MODEL
      and _calls[0]["headers"]["Authorization"] == "Token test-key"
      and _calls[0]["params"]["smart_format"] == "true")
check("brand hotwords ride along as deepgram keyterms",
      "Valmera" in _calls[0]["params"]["keyterm"])

# 5xx is transient -> retry, then fall back rather than fail the index.
_calls = _stub_post([_Resp(503, text="upstream"), _Resp(200, _dg_ok)])
_whisper_hits.clear(); _warns = []
_words, _lg = tr.transcribe(_wav.name, _warns)
check("a 503 is retried and the retry's transcript is used",
      len(_calls) == 2 and [x.w for x in _words] == ["Damn.", "OK"]
      and _whisper_hits == [])

# 4xx (bad key / bad audio) fails identically forever — don't burn retries.
_calls = _stub_post([_Resp(401, text="bad key")])
_whisper_hits.clear(); _warns = []
_words, _lg = tr.transcribe(_wav.name, _warns)
check("a 401 is not retried — it would fail identically forever",
      len(_calls) == 1)
check("deepgram failing falls back to local whisper, index still succeeds",
      len(_whisper_hits) == 1 and [x.w for x in _words] == ["local"])
check("the fallback is disclosed, not passed off as the good transcript",
      len(_warns) == 1 and _warns[0].startswith(tr._FALLBACK_PREFIX)
      and "less accurate" in _warns[0])

# The indexer retries transcribe() once — one index, one notice.
_calls = _stub_post([_Resp(401, text="bad key")])
tr.transcribe(_wav.name, _warns)
check("the fallback notice is not repeated when the indexer retries",
      len(_warns) == 1)

_calls = _stub_post([_orig[2].RequestException("connection reset")])
_whisper_hits.clear(); _warns = []
tr.transcribe(_wav.name, _warns)
check("a dead connection retries, then falls back",
      len(_calls) == tr.DEEPGRAM_RETRIES + 1 and len(_whisper_hits) == 1)

check("transcribe still works with no warnings list to write to",
      tr.transcribe(_wav.name)[0][0].w == "local")

(wconfig.TRANSCRIBER, wconfig.DEEPGRAM_API_KEY, tr.requests,
 tr._transcribe_whisper) = _orig
os.unlink(_wav.name)

# ------------------------------------------------------------------ #
# Round-19: a long index must not look frozen (or silently vanish)
# ------------------------------------------------------------------ #
# A real 19.3-min upload spent 894s inside the proxy encode reporting NOTHING:
# the job sat at 12% for 15 minutes while the customer watched a dead bar.

import indexer as idx                                        # noqa: E402

_writes = []


class _ProgDb:
    def run(self, fn, *a, **kw):
        _writes.append(a)


# The throttle is time-based; drive the clock instead of sleeping.
_clock = [1000.0]
_real_time = idx.time
idx.time = types.SimpleNamespace(monotonic=lambda: _clock[0])

_cb = idx._stage_progress(_ProgDb(), 77, 12, 30)
_cb(0.0)
check("proxy progress maps 0.0 onto the band's floor",
      _writes and _writes[-1] == (77, 12))


def _force(cb, frac):
    """Let the throttle through: pretend PROGRESS_EVERY_S elapsed."""
    _clock[0] += idx.PROGRESS_EVERY_S + 1
    cb(frac)


_force(_cb, 0.5)
check("proxy progress maps 0.5 to the band's midpoint",
      _writes[-1] == (77, 21))
_force(_cb, 1.0)
check("proxy progress maps 1.0 onto the band's ceiling (never past it)",
      _writes[-1] == (77, 30))
_force(_cb, 2.5)
check("a frac past 1.0 still cannot exceed the band",
      _writes[-1] == (77, 30))

_n = len(_writes)
_cb(0.6), _cb(0.7), _cb(0.8)
check("ffmpeg's ~2/sec progress lines are throttled, not one DB write each",
      len(_writes) == _n)


class _DeadDb:
    def run(self, fn, *a, **kw):
        raise RuntimeError("connection reset")


_dead = idx._stage_progress(_DeadDb(), 77, 12, 30)
try:
    _force(_dead, 0.5)
    check("a progress write that fails never kills the encode", True)
except Exception:
    check("a progress write that fails never kills the encode", False)

idx.time = _real_time

# The proxy is an analysis/preview artifact — encoding it at source resolution
# was 894s of pure transcode for no resolution change at all.
_pcmd = []
_real_run3 = mediamod.run
try:
    mediamod.run = lambda cmd, **kw: _pcmd.append((cmd, kw))
    mediamod._encode_proxy("s", "d", 30.0, False, True,
                           progress_cb=lambda f: None, expected_out_s=10.0)
    _cmd, _kw = _pcmd[-1]
    check("proxy scales to PROXY_HEIGHT, not the source's height",
          f"min({wconfig.PROXY_HEIGHT}\\," in " ".join(_cmd))
    check("proxy encode asks ffmpeg for progress when a callback is given",
          "-progress" in _cmd and "pipe:1" in _cmd)
    check("proxy encode hands run() the callback (so it gets the stall "
          "watchdog)", _kw.get("progress_cb") and _kw.get("expected_out_s") == 10.0)
    _pcmd.clear()
    mediamod._encode_proxy("s", "d", 30.0, False, True)
    check("no callback -> no -progress plumbing", "-progress" not in _pcmd[-1][0])
finally:
    mediamod.run = _real_run3

# Project 42: index failed, and the reaper said NOTHING because 'index' was
# missing from its notes. The customer waited 88 minutes on a spinner.
import main as workermain                                    # noqa: E402

check("a reaper-killed index tells the user (it used to say nothing)",
      bool(workermain.REAPER_NOTES.get("index")))
check("a reaper-killed preview tells the user too",
      bool(workermain.REAPER_NOTES.get("preview")))
check("every job type the reaper can fail has a note",
      all(t in workermain.REAPER_NOTES
          for t in ("index", "preview", "final", "agent_turn")))
check("the reaper's index note does not blame the user's file",
      "format" not in workermain.REAPER_NOTES["index"]
      and "wasn't a problem with your file"
      in workermain.REAPER_NOTES["index"])

# ------------------------------------------------------------------ #
# Round-19: a deploy must not spend a job's retry budget
# ------------------------------------------------------------------ #
# Job 205's third and final death was the redeploy from setting an env var.
import db as wdb                                             # noqa: E402


class _RelCur:
    def __init__(self, sink): self.sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.sink.append((sql, params))
    def fetchall(self): return [{"id": 1}, {"id": 2}]


class _RelConn:
    def __init__(self): self.sql = []
    def cursor(self): return _RelCur(self.sql)


_rc = _RelConn()
check("release_jobs hands back 'n' jobs", wdb.release_jobs(_rc, [1, 2]) == 2)
_sql = _rc.sql[0][0]
check("released jobs go back to 'queued', not 'failed'",
      "state = 'queued'" in _sql and "failed" not in _sql)
check("a deploy REFUNDS the attempt it cost (this is the whole point)",
      "attempts = GREATEST(0, attempts - 1)" in _sql)
check("an agent_turn is NOT released — replaying it would re-apply side effects",
      "type <> 'agent_turn'" in _sql)
check("only a job we still hold is released",
      "state = 'running'" in _sql)
check("no ids -> no query at all", wdb.release_jobs(_RelConn(), []) == 0)

# The whisper cache is a liability once whisper is only the fallback: 'medium'
# holds ~1.5GB for the process's life and that resident memory is what
# OOM-killed the worker mid-customer.
_ot = (wconfig.TRANSCRIBER, tr._transcribe_whisper, tr._transcribe_deepgram,
       tr._model)
_loaded = []
tr._model = "PRETEND-1.5GB-MODEL"


def _fake_whisper(_p):
    _loaded.append(tr._model)
    return [Word(w="x", t0=0.0, t1=0.1)], "en"


tr._transcribe_whisper = _fake_whisper
tr._transcribe_deepgram = lambda _p: (_ for _ in ()).throw(RuntimeError("down"))

wconfig.TRANSCRIBER = "deepgram"
tr.transcribe("/tmp/x.wav")
check("deepgram deployment: the fallback frees the model after use",
      tr._model is None)

tr._model = "PRETEND-1.5GB-MODEL"
wconfig.TRANSCRIBER = "whisper"
tr.transcribe("/tmp/x.wav")
check("whisper deployment: the model stays cached (it is used every index)",
      tr._model == "PRETEND-1.5GB-MODEL")

(wconfig.TRANSCRIBER, tr._transcribe_whisper, tr._transcribe_deepgram,
 tr._model) = _ot

# ══ Round 20: premium caption presets ════════════════════════════════════
print("== premium caption presets ==")
from agent_tools import add_captions, set_caption_style       # noqa: E402

PWORDS = [{"w": "So", "t0": 0.5, "t1": 0.62},
          {"w": "I'm", "t0": 0.74, "t1": 0.9},
          {"w": "22", "t0": 1.0, "t1": 1.2},
          {"w": "I", "t0": 1.3, "t1": 1.35},
          {"w": "want", "t0": 1.45, "t1": 1.65},
          {"w": "to", "t0": 1.75, "t1": 1.85},
          {"w": "create", "t0": 1.95, "t1": 2.3},
          {"w": "opportunities.", "t0": 2.4, "t1": 3.0}]
V916 = (1080, 1920)   # 9:16 vertical; frame factor = 1920/720

pevs = caplib.events_premium(PWORDS, style={"preset": "podcast"},
                             play_res=V916,
                             emphasis_words=["create", "Opportunities"])
check("reveal: one event per word", len(pevs) == len(PWORDS))
check("reveal: events start on REAL word times (never invented)",
      abs(pevs[0]["start"] - 0.5) < 1e-6 and
      abs(pevs[2]["start"] - 1.0) < 1e-6)
check("reveal: text accumulates word by word",
      [e["text"].count(r"\fnInter Display ExtraBold") for e in pevs[:3]]
      == [1, 2, 3])
check("reveal: explicit anchored geometry (no jumping)",
      pevs[0]["text"].startswith(r"{\an7\pos("))
check("reveal: the appearing word pops in",
      r"\t(0,100,\fscx108\fscy108)" in pevs[0]["text"])
check("reveal: digits render HUGE in the accent color",
      r"\1c&H4DE1FF&\fs216" in pevs[2]["text"])
check("reveal: emphasis matches case-insensitively through punctuation",
      r"\1c&H4DE1FF&\fs150" in pevs[6]["text"] and    # create -> accent
      r"\xbord26" in pevs[7]["text"])                 # opportunities. -> box
check("reveal: box text goes dark on the accent",
      r"\1c&H101010&\3c&H4DE1FF&" in pevs[7]["text"])
check("reveal: sentence punctuation is dropped from display",
      "opportunities." not in pevs[7]["text"].replace(r"\fs", ""))
check("reveal: last event holds after the final word",
      abs(pevs[-1]["end"] - (3.0 + 0.9)) < 1e-6)
check("reveal: events never overlap",
      all(pevs[i]["end"] <= pevs[i + 1]["start"] + 1e-9
          for i in range(len(pevs) - 1)))
check("premium events are flagged (skip legacy anim prefix)",
      all(e.get("premium") for e in pevs))

bevs = caplib.events_premium(PWORDS, style={"preset": "beast"},
                             play_res=V916, emphasis_words=["create"])
check("beast: uppercase by default", "CREATE" in bevs[6]["text"])
check("beast: centered anchored geometry",
      bevs[0]["text"].startswith(r"{\an5\pos("))
check("beast: whole chunk visible from the first event of the chunk",
      bevs[0]["text"].count(r"\fnAnton") >= 2)
check("beast karaoke: ONLY the spoken word carries the accent",
      all(e["text"].count("&H4DE1FF&") <= 1 for e in bevs))
first_22 = next(e for e in bevs if e["start"] == 1.0)
check("beast karaoke: inactive digits keep size but not color",
      r"\fs" in first_22["text"])

kevs = caplib.events_premium(PWORDS, style={"preset": "karaoke"},
                             play_res=V916)
active_k = next(e for e in kevs if e["start"] == 1.95)
check("karaoke preset: the accent box FOLLOWS the spoken word",
      r"\1c&H101010&\3c&H4DE1FF&\xbord" in active_k["text"])

eevs = caplib.events_premium(PWORDS, style={"preset": "elegant"},
                             play_res=V916, emphasis_words=["create"])
check("elegant: static chunks, not per-word events",
      1 <= len(eevs) < len(PWORDS))
check("elegant: fade entrance", r"\fad(180,140)" in eevs[0]["text"])
check("elegant: serif italic accent",
      any(rf"\fn{caplib.SERIF_FONT}\i1" in e["text"] for e in eevs))

# style overrides
uevs = caplib.events_premium(PWORDS[:2],
                             style={"preset": "beast", "uppercase": False,
                                    "position": "bottom",
                                    "highlight_color": "#FF0000"},
                             play_res=V916)
check("uppercase override respected", "So" in uevs[0]["text"])
check("highlight_color drives the accent", "&H0000FF&" in uevs[0]["text"])

# premium style lines use the real fonts, no synthetic bold
sline = caplib.style_line("Default", {"preset": "podcast"}, V916)
check("podcast style line: Inter Display ExtraBold, Bold=0",
      "Inter Display ExtraBold" in sline and ",0,0,0,0,100,100," in sline)
check("legacy style line unchanged without a preset",
      "DejaVu Sans" in caplib.style_line("Default", None, V916))
check("'classic' preset = the legacy look",
      "DejaVu Sans" in caplib.style_line("Default", {"preset": "classic"},
                                         V916))

# manual premium items: preset look, VERBATIM text (no invented emphasis)
tl_p = Timeline([[0, 10]])
mevs = caplib.events_from_items(
    [{"text": "Chapter 22 begins", "start": 2, "end": 4,
      "style": {"preset": "beast"}}], tl_p, V916)
check("manual premium item: uppercase + geometry + flagged",
      "CHAPTER 22" in mevs[0]["text"] and "BEGINS" in mevs[0]["text"] and
      r"{\an5\pos(" in mevs[0]["text"] and mevs[0]["premium"])
check("manual premium item: dictated digits NOT auto-emphasized",
      "&H4DE1FF&" not in mevs[0]["text"])

# build_ass dispatches presets end-to-end
with tempfile.TemporaryDirectory() as td:
    pa = caplib.build_ass(
        {"captions": {"mode": "from_transcript",
                      "style": {"preset": "podcast"},
                      "emphasis_words": ["create"]}},
        {"words": PWORDS}, Timeline([[0, 10]]),
        os.path.join(td, "p.ass"), play_res=V916)
    pcontent = open(pa).read()
    check("build_ass premium: preset font in styles",
          "Inter Display ExtraBold" in pcontent)
    check("build_ass premium: one Dialogue per word",
          pcontent.count("Dialogue:") == len(PWORDS))

# schema: validation, normalization, signature stability
okp = validate_edl({"keep": [[0, 10]],
                    "captions": {"mode": "from_transcript",
                                 "style": {"preset": "podcast",
                                           "uppercase": False},
                                 "emphasis_words": [" create ", "", "22"]}},
                   60)
capd = okp.model_dump()["captions"]
check("schema: preset + uppercase survive validation",
      capd["style"]["preset"] == "podcast" and
      capd["style"]["uppercase"] is False)
check("schema: emphasis_words trimmed and emptied entries dropped",
      capd["emphasis_words"] == ["create", "22"])
check("schema: position default is None (presets may place)",
      capd["style"]["position"] is None)
sig1 = edl_signature(okp.model_dump())
sig2 = edl_signature(validate_edl(okp.model_dump(), 60).model_dump())
check("schema: premium EDL signature stable across re-validation",
      sig1 == sig2)
expect_reject("bad preset name",
              {"keep": [[0, 10]],
               "captions": {"mode": "from_transcript",
                            "style": {"preset": "hollywood"}}}, 60)
ok_empty = validate_edl({"keep": [[0, 10]],
                         "captions": {"mode": "from_transcript",
                                      "emphasis_words": []}}, 60)
check("schema: empty emphasis_words collapses to None (signature-safe)",
      ok_empty.model_dump()["captions"]["emphasis_words"] is None)

# fontsdir reaches the burn filter
gfp = build_filtergraph(edl, 60.0, True, tl3, "/tmp/x.ass", [], index,
                        preview=True)
check("filtergraph: fontsdir points at the bundled fonts",
      ":fontsdir='" in gfp and "worker/fonts" in gfp)

# agent tools: presets + emphasis flow through, disclosures fire
tctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = add_captions(tctx, mode="from_transcript",
                 style={"preset": "podcast"},
                 emphasis_words=["money", "22"])
check("add_captions: preset + emphasis stored",
      tctx.written["captions"]["style"]["preset"] == "podcast" and
      tctx.written["captions"]["emphasis_words"] == ["money", "22"] and
      "preset podcast" in r)
tctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = add_captions(tctx, mode="from_transcript", emphasis_words=["money"])
check("add_captions: emphasis without a preset disclosed",
      "only take effect with a premium preset" in r)
tctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = add_captions(tctx, mode="from_transcript",
                 style={"preset": "beast", "dynamic": True},
                 max_words_per_caption=8)
check("add_captions: preset+dynamic disclosed, no legacy karaoke clamp",
      "'dynamic' flag is ignored" in r and
      tctx.written["captions"]["max_words_per_caption"] == 8)
check("add_captions: bad emphasis_words rejected",
      add_captions(ToolCtx({"keep": [[0.0, 30.0]]}),
                   mode="from_transcript",
                   emphasis_words="money").startswith("REJECTED"))
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": None,
                             "style": None}})
r = set_caption_style(tctx, {"preset": "podcast"},
                      emphasis_words=["future"])
check("set_caption_style: preset patch + emphasis replace",
      tctx.written["captions"]["style"]["preset"] == "podcast" and
      tctx.written["captions"]["emphasis_words"] == ["future"])
check("_parse_partial_style: preset+uppercase accepted",
      agent_tools._parse_partial_style({"preset": "beast",
                                        "uppercase": True})
      == {"preset": "beast", "uppercase": True})
check("_parse_partial_style: unknown field still rejected",
      "ERR" in agent_tools._parse_partial_style({"font": "Arial"}))

# review-round fixes
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": None,
                             "style": {"color": "#FFFFFF", "size": "m",
                                       "position": "bottom"}}})
r = set_caption_style(tctx, {"preset": "podcast"})
check("preset apply DROPS the stale auto-filled bottom position",
      "position" not in tctx.written["captions"]["style"])
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": None,
                             "style": {"position": "bottom"}}})
r = set_caption_style(tctx, {"preset": "podcast", "position": "bottom"})
check("explicitly patched position survives a preset apply",
      tctx.written["captions"]["style"]["position"] == "bottom")
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": 8,
                             "style": {"preset": "classic"}}})
r = set_caption_style(tctx, {"dynamic": True})
check("preset 'classic' still gets the legacy karaoke clamp",
      tctx.written["captions"]["max_words_per_caption"] == 4 and
      "at most 4" in r)
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": None,
                             "style": {"preset": "podcast"}}})
r = set_caption_style(tctx, emphasis_words=["future", "wealth"])
check("emphasis-only update works without a style arg",
      tctx.written["captions"]["emphasis_words"] == ["future", "wealth"] and
      "emphasis words set (2)" in r)
check("style-less, emphasis-less call rejected helpfully",
      set_caption_style(ToolCtx({"keep": [[0.0, 30.0]],
                                 "captions": {"mode": "from_transcript"}}))
      .startswith("REJECTED"))
tctx = ToolCtx({"keep": [[0.0, 30.0]],
                "captions": {"mode": "from_transcript",
                             "max_words_per_caption": None, "style": None}})
r = set_caption_style(tctx, emphasis_words=["x"])
check("emphasis without a preset disclosed in set_caption_style too",
      "only take effect with a premium preset" in r)
tctx = ToolCtx({"keep": [[0.0, 30.0]]})
r = add_captions(tctx, mode="from_transcript",
                 style={"uppercase": True})
check("uppercase without a preset disclosed in add_captions",
      "uppercase only applies with a premium preset" in r)

print("== Round-21 reliability: caption honesty + word-time clamp + "
      "pipeline version ==")
# A real music-heavy upload transcribed to ONE hallucinated word that the
# edit then cut; the agent claimed captions were on and the user saw nothing.
r = add_captions(ToolCtx({"keep": [[0.0, 30.0]]}),
                 mode="from_transcript", style={"preset": "podcast"})
check("empty transcript => explicit no-captions warning",
      "WARNING" in r and "EMPTY" in r and "NO text" in r)
_IDX_OUT = {"words": [{"w": "hey", "t0": 20.0, "t1": 20.4}]}
r = add_captions(ToolCtx({"keep": [[0.0, 10.0]]}, index=_IDX_OUT),
                 mode="from_transcript", style={"preset": "podcast"})
check("all words cut => 'not be visible' warning",
      "WARNING" in r and "not be visible" in r)
_IDX_SPARSE = {"words": [{"w": "so", "t0": 1.0, "t1": 1.2},
                         {"w": "yeah", "t0": 2.0, "t1": 2.3}]}
r = add_captions(ToolCtx({"keep": [[0.0, 30.0]]}, index=_IDX_SPARSE),
                 mode="from_transcript", style={"preset": "podcast"})
check("sparse transcript => sparse note, not a false success",
      "very sparse" in r)
_IDX_RICH = {"words": [{"w": f"w{i}", "t0": i * 1.0, "t1": i * 1.0 + 0.4}
                       for i in range(12)]}
r = add_captions(ToolCtx({"keep": [[0.0, 30.0]]}, index=_IDX_RICH),
                 mode="from_transcript", style={"preset": "podcast"})
check("healthy transcript => no caption-visibility warning",
      "WARNING" not in r and "very sparse" not in r)

# clamp_word_times: ASR on music produced a 'word' spanning 15.36-34.72s on a
# 16.65s file — ends clamp to the video, words past the end drop entirely.
_cw = schemas.clamp_word_times(
    [{"w": "ok", "t0": 15.36, "t1": 34.72},
     {"w": "ghost", "t0": 17.0, "t1": 18.0},
     {"w": "fine", "t0": 1.0, "t1": 1.5}], 16.65)
check("word ending past the video is clamped to the duration",
      any(w["w"] == "ok" and w["t1"] == 16.65 for w in _cw))
check("word starting past the video is dropped",
      not any(w["w"] == "ghost" for w in _cw))
check("in-range words come through untouched",
      any(w["w"] == "fine" and w["t0"] == 1.0 and w["t1"] == 1.5
          for w in _cw))
_cwm = schemas.clamp_word_times(
    [schemas.Word(w="tail", t0=10.0, t1=99.0)], 16.65)
check("Word models clamp too and stay models",
      _cwm[0].t1 == 16.65 and isinstance(_cwm[0], schemas.Word))
check("no duration => words returned unchanged",
      schemas.clamp_word_times([{"w": "x", "t0": 0, "t1": 99}], None)
      [0]["t1"] == 99)

# The pipeline version is a single shared constant — the env-per-service
# split re-indexed every project on every open for a day when the two
# drifted. config must re-export schemas' value verbatim.
import config as wconfig                                       # noqa: E402
check("pipeline version: config re-exports the schemas constant",
      wconfig.PIPELINE_VERSION == schemas.PIPELINE_VERSION
      and isinstance(schemas.PIPELINE_VERSION, int))

print("== Round-22: the opening frame carries a caption (paused player) ==")
# A mobile player loads paused at t=0 and never autoplays. Reveal captions
# otherwise start at the first spoken word, so that frame was blank and the
# user concluded "captions didn't apply". build_ass now carries the first
# from_transcript caption back to t=0 when speech starts within the lead-in.
_lead_edl = validate_edl(
    {"keep": [[0, 60]],
     "captions": {"mode": "from_transcript",
                  "style": {"preset": "podcast"}}}, 60).model_dump()
_lead_index = {"video": {"duration": 60}, "sentences": [],
               "words": [{"w": "hey", "t0": 0.16, "t1": 0.5},
                         {"w": "there", "t0": 0.5, "t1": 0.9},
                         {"w": "friends", "t0": 0.9, "t1": 1.4}]}
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(_lead_edl, _lead_index,
                         Timeline(_lead_edl["keep"]),
                         os.path.join(td, "lead.ass"), play_res=(1080, 1920))
    first = next(l for l in open(p).read().splitlines()
                 if l.startswith("Dialogue:"))
    check("first caption starts at 0:00:00.00 (no blank opening frame)",
          first.split(",")[1].strip() == "0:00:00.00")

# A genuine silent intro (first word well past the lead-in) is NOT pulled to
# 0 — a caption held over real silence would misrepresent the timing.
_intro_index = {"video": {"duration": 60}, "sentences": [],
                "words": [{"w": "later", "t0": 5.0, "t1": 5.4},
                          {"w": "words", "t0": 5.4, "t1": 5.9}]}
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(_lead_edl, _intro_index,
                         Timeline(_lead_edl["keep"]),
                         os.path.join(td, "intro.ass"), play_res=(1080, 1920))
    first = next(l for l in open(p).read().splitlines()
                 if l.startswith("Dialogue:"))
    check("a long silent intro keeps the real first-word time",
          first.split(",")[1].strip() != "0:00:00.00")

# An inserted clip opening the program is NOT overwritten: the clamp must not
# pull a main-footage word back over an untranscribed title card / b-roll.
_ins_edl = validate_edl(
    {"keep": [[0, 60]],
     "inserts": [{"id": "i0", "kind": "image", "asset_key": "img/x.png",
                  "at_output_s": 0, "duration_s": 1.0}],
     "captions": {"mode": "from_transcript", "style": {"preset": "podcast"}}},
    60).model_dump()
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(_ins_edl, _lead_index,
                         Timeline(_ins_edl["keep"], _ins_edl["inserts"]),
                         os.path.join(td, "ins.ass"), play_res=(1080, 1920))
    first = next(l for l in open(p).read().splitlines()
                 if l.startswith("Dialogue:"))
    check("clamp skipped when an inserted clip opens the program",
          first.split(",")[1].strip() != "0:00:00.00")

# Non-preset (legacy transcript) captions get the same opening-frame fix.
_lead_legacy = validate_edl(
    {"keep": [[0, 60]], "captions": {"mode": "from_transcript"}}, 60
).model_dump()
with tempfile.TemporaryDirectory() as td:
    p = caplib.build_ass(_lead_legacy, _lead_index,
                         Timeline(_lead_legacy["keep"]),
                         os.path.join(td, "leg.ass"), play_res=(1080, 1920))
    first = next(l for l in open(p).read().splitlines()
                 if l.startswith("Dialogue:"))
    check("legacy transcript captions also fill the opening frame",
          first.split(",")[1].strip() == "0:00:00.00")

print("== Round-22: render cache invalidates on transcript change ==")
# from_transcript captions burn from the mutable index, so the render cache
# must key on a caption fingerprint too — otherwise a caption-less render is
# served forever after the index gains words (a re-render becomes a no-op).
import renderer as _rnd                                        # noqa: E402
_fp_edl = {"keep": [[0, 10]],
           "captions": {"mode": "from_transcript", "style": {"preset": "podcast"}}}
_fp_a = _rnd._caption_index_fp(
    _fp_edl, {"words": [{"w": "hi", "t0": 0.1, "t1": 0.4}]})
_fp_b = _rnd._caption_index_fp(
    _fp_edl, {"words": [{"w": "hi", "t0": 0.1, "t1": 0.4},
                        {"w": "there", "t0": 0.5, "t1": 0.9}]})
check("caption fingerprint is stable for identical words",
      _fp_a == _rnd._caption_index_fp(
          _fp_edl, {"words": [{"w": "hi", "t0": 0.1, "t1": 0.4}]}))
check("caption fingerprint changes when the transcript changes",
      _fp_a != _fp_b and _fp_a and _fp_b)
check("caption-off render keeps the cheap (version, sha) cache — no fingerprint",
      _rnd._caption_index_fp({"keep": [[0, 10]], "captions": None},
                             {"words": [{"w": "hi", "t0": 0.1, "t1": 0.4}]})
      is None)
check("explicit-item captions don't get a transcript fingerprint",
      _rnd._caption_index_fp(
          {"keep": [[0, 10]], "captions": [{"text": "x", "start": 1, "end": 2}]},
          {"words": [{"w": "hi", "t0": 0.1, "t1": 0.4}]}) is None)

print("== Round-23: a render the user can't play must be re-creatable ==")
# The incident: a captioned render sat correct in R2 but would not play in the
# user's browser. Every recovery path led back to the SAME object — the cache
# served it, and a re-render overwrote the same key — so the studio's Retry
# button was structurally incapable of changing anything.

# (a) Render keys are unique per render and free of blocker-bait words.
_k1 = _rnd._render_stamp(252)
_k2 = _rnd._render_stamp(252)
check("two renders of the same job never share a key", _k1 != _k2)
check("the key is traceable to its job", _k1.startswith("252-") and _k2.startswith("252-"))
_full = f"media/51/{_k1}.mp4"
check("no 'preview'/'render' substrings for a content blocker to match",
      "preview" not in _full and "render" not in _full)

# (b) force=1 bypasses the render cache. Proven by where the job DIES: with a
# cache hit available it returns the cached asset; forced, it walks past the
# cache and fails at the next step (index lookup stubbed missing).
class _FakeDB:
    def __init__(self, calls): self.calls = calls
    def run(self, fn, *a, **kw):
        name = getattr(fn, "__name__", str(fn))
        self.calls.append(name)
        if name == "get_edl_version":
            return {"json": {"keep": [[0, 10]], "captions": None}, "version": 2}
        if name == "latest_asset":
            return {"sha256": "abc123", "storage_key": "originals/51/x.mp4"}
        if name == "find_render_asset":
            return {"id": 248, "storage_key": "media/51/252-deadbeef.mp4",
                    "duration_s": 56.8,
                    "meta": {"src_sha256": "abc123", "variant": "preview",
                             "edl_version": 2, "sheet_key": None}}
        if name == "get_index_by_sha":
            return None          # forces the post-cache RuntimeError
        return None

_orig_exists = _rnd.storage.exists
_rnd.storage.exists = lambda k: True
try:
    _calls = []
    _job = {"id": 300, "project_id": 51, "type": "preview",
            "payload": {"edl_version": 2}}
    _res = _rnd.run_render_job(_FakeDB(_calls), _job)
    check("without force, the cached render is served",
          _res.get("cached") is True and _res.get("render_asset_id") == 248)
    check("without force, the cache is consulted", "find_render_asset" in _calls)

    _calls2 = []
    _job2 = {"id": 301, "project_id": 51, "type": "preview",
             "payload": {"edl_version": 2, "force": True}}
    try:
        _rnd.run_render_job(_FakeDB(_calls2), _job2)
        _forced_past_cache = False
    except RuntimeError as e:
        _forced_past_cache = "index missing" in str(e).lower()
    check("force walks PAST the cache instead of serving the dead render",
          _forced_past_cache)
    check("force never even queries the cache",
          "find_render_asset" not in _calls2)
finally:
    _rnd.storage.exists = _orig_exists

print(f"\nALL {PASS} CHECKS PASSED")
