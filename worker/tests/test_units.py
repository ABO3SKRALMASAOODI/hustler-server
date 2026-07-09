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
    check("dynamic events carry the pop animation tag",
          all(r"\fscx60" in d and r"\t(0,110" in d for d in dialogues))
    check("dynamic events are single words",
          all(len(d.split(",,0,0,0,,")[1].split("}")[-1].split()) == 1
              for d in dialogues))
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

print(f"\nALL {PASS} CHECKS PASSED")
