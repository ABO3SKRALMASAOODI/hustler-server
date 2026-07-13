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
      "middle" in _nearest_alternative("move the captions to the middle"))
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
    def __init__(self, edl, asset=None):
        self._edl = {"version": 1, "json": edl}
        self.written = None
        self.db = self
        self.project_id = 1
        self._asset = asset

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

print(f"\nALL {PASS} CHECKS PASSED")
