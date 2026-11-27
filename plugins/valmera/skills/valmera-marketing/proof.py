"""Workflow 2, proof reels: a marketing film for editors, narrated and cut at the references' pace, around what Valmera
really did with one clip and one typed sentence (the one-command path; market.py starts it).

ideas   one model call reads what editors answer to (the 02-editing-tutorials references' own captions and counts), the
        clips the owner already works from (podcast sources with their transcripts, their measured picture and frames of
        them, so the clip whose export will look premium is chosen) and the owner's few words, if any,
        and returns ranked ideas: the editor's pain or technique, the true claim, the clip and the seconds, two sentences
        to type into Valmera and what each must visibly do, the style
script  records the real output first: the clip is uploaded to a local stack of the product (the Gauntlet's runner, its
        own database, storage and worker; nothing of production is written, no customer is involved), the sentence is
        typed, the export is downloaded (studio.py). A vision model then checks the export against the sentence, element by
        element, while the session stays open; a result it finds short gets a follow-up typed in the same session (up
        to FOLLOW_UPS, written by a model from the check), the way a customer asks again, and only then does the next
        idea get its turn. Then the narration is written around the recording, every claim audited against its receipts
        by a second call, and every typed sentence is spoken word for word
supply  the narrator (Phonon Easton, the stories read), real footage per line (worker/footage; a line whose only picture
        is the recording gets footage of what its words name too), free photographs of what it names, and the recording
        cut in with its own sound between two narrator lines: the clip as uploaded (the before) and the export (the after);
        a frame of the export can open the film. The recording is supplied as its own shots (cut at its edit list's events
        and its measured picture changes), ranked for every line's words by a model that sees them, each file carrying
        the recording's sha256 and first second so no second plays twice under two names
score   the story reel builder (valmera-stories/reel.py) at a pace set from this film's own narration against the marketing
        references' medians; the recording is shown as delivered (no section finish, flash or look over it), full frame
        for the real-voice before and after and the hook's landing, and a moving print on the style's board under the
        narration's captions on the narrated lines; the
        film's editing device (devices.py: the timeline pull-back, the wipe and its count-up grid, or the triptych) is laid
        on its own beats; then
        every picture is checked against the exact words it sits under (gpt-6-sol, low) and a picture that does not fit
        is dropped and the plan built again
render  Valmera's engine, then the shell's brand finish and the music it chooses (wf3.render, wf3.check)

Model calls run on OpenAI; nothing is spent on Anthropic. Nothing is published.
"""
import base64
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
import importlib.util
_inventory_spec = importlib.util.spec_from_file_location("_wf2_visual_inventory", os.path.join(HERE, "visual_inventory.py"))
VI = importlib.util.module_from_spec(_inventory_spec)
_inventory_spec.loader.exec_module(VI)
_devices_spec = importlib.util.spec_from_file_location("_wf2_devices", os.path.join(HERE, "devices.py"))
D = importlib.util.module_from_spec(_devices_spec)
_devices_spec.loader.exec_module(D)
SKILLS = os.path.dirname(HERE)
STORIES = os.path.join(SKILLS, "valmera-stories")
# this folder's own film.py (the three built styles) must not shadow the stories' film.py that reel.py and wf3.py import
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
for p in (os.path.join(SKILLS, "_shell"), STORIES):
    if p not in sys.path:
        sys.path.insert(0, p)
# a `film` already imported from another folder (this one's) steps aside while the stories' modules bind theirs
_held = {k: sys.modules.pop(k) for k in ("film",) if k in sys.modules
         and os.path.dirname(os.path.abspath(getattr(sys.modules[k], "__file__", "") or "")) != STORIES}
import story_chain
import chain  # noqa: E402
import delivered as DL  # noqa: E402
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import reel as R  # noqa: E402
import wf3 as W3  # noqa: E402
import writer  # noqa: E402
sys.modules.update(_held)

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
G = f"{VV}/week-1106/gauntlet"
HEAVY = f"{VV}/week-1106/heavy.sh"
PY = f"{VV}/venv-worker/bin/python"
SRC_ROOT = os.environ.get("WF2_SOURCES", f"{V}/.tmp/valmera-podcast-shorts")
REFS = f"{V}/reference-library/2026-11-05/02-editing-tutorials-and-app-marketing"
ATLAS = f"{VV}/week-1106/atlas/numbers.json"
CHECK_MODEL = "gpt-6-sol"           # the picture checks: every picture against its words, the export against the sentence
FORM = dict(words=(55, 85), segments=(7, 12), seconds=(24, 38))
CAP_PX = 72               # the narration's captions: 56 px read small on a phone beside the references' (owner, 16 Nov: "very not clear")
# the recording's stack: this week's main on a synthetic Pro account. Anthropic spend is $0 for this lane, so the Studio
# runs on Pro's own OpenAI lane (gpt-6.1-sol, llm.py) with the Claude switch for new subscribers off (studio_for_all_plans),
# as production runs it for every subscriber the switch does not cover; OPENAI_EDITS lets that lane edit on a stack.
PLAN = "pro_2026"
STACK = {"be": "main", "fe": "main", "plan_models": {}, "planner_model": "off", "env": {"OPENAI_EDITS": "1"},
         "kv": {"studio_for_all_plans": {"on": False}}, "openai_key": True, "trial_per_account_usd": 3.0, "spend_cap_usd": 3,
         "transcriber": "deepgram", "stock": True, "allow_free_stock": True,
         "slots": {"agent": 2, "index": 1, "media": 2}}
STACK_V = 5           # a recording made on other stack settings is never reused (4, 17 Nov: the phone view, the Studio photographed;
                      # 5, 20 Nov: the session stays open after each result for a follow-up typed from the export's check)
FOLLOW_UPS = 2        # follow-up requests typed in the same Studio session before an idea gives way to the next (20 Nov: the
                      # first export of i0 had every asked element and three defects; the run refused it instead of asking again)
FOLLOW_UP_WAIT_S = 1800   # how long the open session waits for the check and the follow-up's words
VIEW = "phone"        # the Studio as a phone shows it: the film shows the product's own screen at phone size (studio.py ui/)


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    with open(p + ".tmp", "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "reels.json")))["styles"]}


def registry_row(card):
    return dict(id=card["id"], name=card.get("name", card["id"]), spine=card.get("spine") or [], pace=card.get("pace") or [10, 18],
                engine="proof-reel")


def ledger_rows(ctx):
    return (jload(os.path.join(ctx.state_root, "ledger.json"), {}) or {}).get(ctx.workflow, [])


def able_cards(ctx):
    """The styles outside the last three delivered (each style rests three films)."""
    last = [r.get("style") for r in ledger_rows(ctx)[-3:]]
    c = [s for s in cards().values() if s.get("buildable") and s["id"] in R.STYLES]
    if ctx.args.get("style"):
        c = [s for s in c if s["id"] == ctx.args["style"]]
    return [s for s in c if s["id"] not in last] or c


def model_json(text):
    return writer.json_block(text)


def ask(system, content, *, what, effort="medium", model=None, max_tokens=16000):
    text, use = writer.ask_openai(system, content, effort=effort, max_tokens=max_tokens, what=what, model=model)
    return model_json(text), dict(stage=what, **use)


# ----------------------------------------------------------------- what the film can stand on

def json3_words(path):
    """[(t, word)] from a YouTube json3 caption file, in seconds."""
    out = []
    for e in (jload(path, {}) or {}).get("events") or []:
        t0 = float(e.get("tStartMs") or 0) / 1000
        for s in e.get("segs") or []:
            w = (s.get("utf8") or "").strip()
            if w and not (w.startswith("[") and w.endswith("]")):
                out.append((round(t0 + float(s.get("tOffsetMs") or 0) / 1000, 2), w))
    return out


def numbered_sentences(words):
    """Group punctuated, timed speech. A long breath never finishes a sentence."""
    rows, current = [], []
    for word in words:
        current.append(word)
        text = word["w"].rstrip('”’"')
        if text.endswith((".", "!", "?")):
            rows.append(dict(i=len(rows), t0=current[0]["t0"], t1=word["t1"],
                             text=" ".join(w["w"] for w in current)))
            current = []
    return rows


def sources():
    out = []
    for folder in sorted(os.listdir(SRC_ROOT)) if os.path.isdir(SRC_ROOT) else []:
        root = os.path.join(SRC_ROOT, folder)
        src = os.path.join(root, "source")
        video = next((os.path.join(src, n) for n in ("podcast.mp4", "source.mp4")
                      if os.path.isfile(os.path.join(src, n))), None)
        if video:
            meta = jload(os.path.join(root, "selection", "source-info.json"), {})
            out.append(dict(id=meta.get("id") or folder.split("-")[0], title=meta.get("title"),
                            file=video, captions=os.path.join(src, "captions.en-orig.json3")))
    return out


def podcast_clock(media, lo, hi):
    """Load existing word-timed podcast speech if the export covers the requested excerpt."""
    from pathlib import Path
    folder = Path(media).parent
    for path in (folder / "words.json", folder / "hearing_full.json", folder.parent / "words.json"):
        data = jload(path)
        if isinstance(data, dict):
            data = data.get("words")
        if not data or not isinstance(data, list):
            continue
        words = []
        for row in data:
            if not isinstance(row, dict):
                break
            start, end = row.get("t0", row.get("start")), row.get("t1", row.get("end"))
            word = row.get("w") or row.get("word") or row.get("text")
            if start is None or end is None or not word:
                break
            words.append(dict(w=word.strip(), t0=float(start), t1=float(end)))
        else:
            if words and words[0]["t0"] < hi and words[-1]["t1"] >= hi-1:
                return words
    return []


def source_sentences(ctx, media, tag, lo, hi):
    """Prefer the podcast's completed transcript. Its timed fragments end at real word edges."""
    from pathlib import Path

    def in_window(rows):
        selected = (r for r in rows if r["t1"] >= lo and r["t0"] <= hi)
        return [dict(r, i=i) for i, r in enumerate(selected)]

    segments = jload(Path(media).parent / "valmera-segments.json")
    if isinstance(segments, list) and segments:
        fragments = []
        for row in segments:
            if not isinstance(row, dict) or not {"text", "start", "end"} <= row.keys():
                break
            words = row["text"].split()
            if not words:
                continue
            fragments.append(dict(w=row["text"], t0=float(row["start"]), t1=float(row["end"])))
        else:
            # When a timestamped fragment contains an interior sentence end, a fresh word clock
            # supplies that internal edge. Never interpolate a word time from character positions.
            rows = in_window(numbered_sentences(fragments))
            if rows and not any(w.rstrip('”’"').endswith((".", "!", "?"))
                                for f in fragments if rows[0]["t0"] <= f["t0"] <= rows[-1]["t1"]
                                for w in f["w"].split()[:-1]):
                return rows, fragments
    words = podcast_clock(media, lo, hi)
    rows = in_window(numbered_sentences(words))
    if rows:
        return rows, words

    # A transcription starting mid-file can give a clipped phrase punctuation at either edge.
    # Hear the neighboring sentences too. Extend the read if a long thought fills the window.
    duration = probe_wh(media)[2]
    margin = 15.0
    while True:
        start, end = max(0.0, lo-margin), min(duration, hi+margin)
        words = hear_clock(ctx, media, tag, start, end)
        rows = numbered_sentences(words)
        if start > 0:
            rows = rows[1:]
        if end < duration:
            rows = rows[:-1]
        selected = in_window(rows)
        if selected or (start == 0 and end == duration):
            return selected, words
        margin *= 2


def hear_clock(ctx, media, tag, lo=0.0, hi=None):
    """The actual file's punctuation and both word edges, on its own clock. Cache only in the run."""
    import tempfile
    signature = [media, os.stat(media).st_size, os.stat(media).st_mtime_ns, lo, hi]
    cache = os.path.join(ctx.tmp, f"speech-{tag}.json")
    prior = jload(cache, {})
    if prior.get("signature") == signature:
        return prior["words"]
    with tempfile.TemporaryDirectory(dir=ctx.tmp) as folder:
        wav = os.path.join(folder, "speech.wav")
        output = os.path.join(folder, "heard.json")
        args = ["-t", str(hi-lo)] if hi is not None else []
        subprocess.run([PX.FF, "-v", "error", "-ss", str(lo), "-i", media, *args, "-vn", "-ac", "1",
                        "-ar", "16000", wav], check=True)
        code = ("import json,sys,mlx_whisper; "
                "r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo=sys.argv[2],word_timestamps=True,"
                "condition_on_previous_text=False); "
                "json.dump([w for s in r['segments'] for w in s.get('words',[])],open(sys.argv[3],'w'))")
        atlas = os.path.join(VV, "week-1106", "atlas")
        subprocess.run(["zsh", HEAVY, "wf2-hear-podcast", os.path.join(atlas, "venv/bin/python"), "-c",
                        code, wav, os.path.join(atlas, "models/whisper-large-v3-mlx"), output], check=True)
        words = [dict(w=w["word"].strip(), t0=round(lo+w["start"], 3), t1=round(lo+w["end"], 3))
                 for w in jload(output) if w["word"].strip()]
    jsave(cache, dict(signature=signature, words=words))
    return words


def snap_window(rows, choice, path, duration, *, clock=None, silence=None):
    """Cut in the two gaps bordering these sentences. A word clock bounds both searches."""
    first, last = choice
    start, end = rows[first]["t0"], rows[last]["t1"]
    left = rows[first-1]["t1"] if first else 0.0
    right = rows[last+1]["t0"] if last+1 < len(rows) else duration
    if clock:
        # Some ASR word times overlap at sentence boundaries. The word clock can
        # narrow a gap around the selection, never widen it across its neighbor.
        left = max((w["t1"] for w in clock if left < w["t1"] <= start), default=left)
        right = min((w["t0"] for w in clock if end <= w["t0"] < right), default=right)

    def edge(a, b, word_edge):
        if a >= b:
            return word_edge
        gaps = (silence or pauses)(path, max(0, a-0.5), min(duration, b+0.5), min_s=0.04)
        quiet = [(max(a, x), min(b, y)) for x, y in gaps if max(a, x) < min(b, y)]
        if not quiet:
            # A continuous noise floor can mask a breath. Measure the lowest-energy samples
            # strictly inside the inter-word gap, then cut at the nearest zero crossing.
            return quiet_gap(path, a, b) if silence is None else word_edge
        start, stop = min(quiet, key=lambda pair: abs(sum(pair)/2-word_edge))
        return round((start+stop)/2, 3)

    return [edge(left, start, start), edge(end, right, end)]


LOOK_V = 1                # the measures below; a cached look of another version is measured again
LOOK_CACHE = os.environ.get("WF2_LOOK_CACHE", f"{VV}/workflow/marketing/source_look.json")
LOOK_FRAMES = 12          # frames a look is measured on, spread over the file
IDEA_FRAMES = 6           # frames of each source the ideas call sees (at the windows it is offered, spread over them)


def probe_wh(path):
    """(width, height) of a video's first picture stream, or None."""
    try:
        r = subprocess.run([PX.FP, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", path], capture_output=True, text=True, timeout=60)
        j = json.loads(r.stdout)
        st = j["streams"][0]
        return int(st["width"]), int(st["height"]), float((j.get("format") or {}).get("duration") or 0.0)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def grey_frame(path, t, w, h):
    """One frame of a video at second t as a grey numpy array (h, w) at its native size, or None."""
    import numpy as np
    try:
        r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None                       # a slow or broken frame is a frame not measured, never a stopped run
    if r.returncode != 0 or len(r.stdout) < w * h:
        return None
    return np.frombuffer(r.stdout[:w * h], np.uint8).reshape(h, w).astype(np.float32)


def look_measures(frames, w, h):
    """How a clip will look as a vertical film, measured on its frames (grey, native size), no model:
      crop_px     the width of its 9:16 crop in source pixels (the product's reframe takes the full height of a wider
                  picture); upscale is 1080 over it: 640x480 VHS crops 270 px wide and is blown up 4x
      acuity      how crisp its edges are on that centre crop shown at phone size (540x960): the 99.5th percentile of
                  the Laplacian over that of the gradient, which does not depend on contrast or on how busy the picture
                  is (20 Nov, the owner's three sources: the 1920x1080 podcast 1.04, the 654x480 and the 640x480 VHS
                  clips 0.57 and 0.61)
      overlay     a burned-in band (a timecode, a logo, captions) in the top fifth or the bottom quarter: three or more
                  neighbouring grid cells whose edges stay dense in every frame (frames spread over the whole source,
                  so the picture under them changes) while the frame's middle does not, [x0, y0, x1, y1] in frame
                  fractions, or None (the VHS clip's 'R12:23:28:18' measured at y 0.875-0.958; the HD and the other SD
                  source: none)"""
    import numpy as np
    from PIL import Image
    crop_w = min(float(w), h * 9.0 / 16.0)
    acu = []
    for g in frames:
        x0 = int((w - crop_w) / 2)
        a = np.asarray(Image.fromarray(g[:, x0:x0 + int(crop_w)].astype(np.uint8)).resize((540, 960), Image.BICUBIC), np.float32)
        lap = np.abs(a[1:-1, 1:-1] * -4 + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
        grad = np.hypot(a[1:-1, 2:] - a[1:-1, :-2], a[2:, 1:-1] - a[:-2, 1:-1]) / 2
        top = float(np.percentile(grad, 99.5))
        if top >= 2.0:                                    # a black or flat frame says nothing about edges
            acu.append(float(np.percentile(lap, 99.5)) / top)
    overlay = None
    if len(frames) >= 4:
        cols, rows = 16, 24
        dens = []
        for g in frames:
            a = np.asarray(Image.fromarray(g.astype(np.uint8)).resize((cols * 20, rows * 12), Image.BILINEAR), np.float32)
            e = (np.abs(np.diff(a, axis=1))[:-1, :] + np.abs(np.diff(a, axis=0))[:, :-1]) > 48
            dens.append(np.pad(e.astype(np.float32), ((0, 1), (0, 1))).reshape(rows, 12, cols, 20).mean(axis=(1, 3)))
        steady = np.percentile(np.stack(dens), 20, axis=0) >= 0.12
        if steady[int(rows * 0.2):int(rows * 0.75)].mean() > 0.25:
            steady[:] = False             # texture everywhere in every frame is the picture itself, not a band laid over it
        cells = []
        for r in list(range(int(rows * 0.2))) + list(range(int(rows * 0.75), rows)):
            run = []
            for c in range(cols + 1):
                if c < cols and steady[r, c]:
                    run.append(c)
                    continue
                if len(run) >= 3:
                    cells += [(r, q) for q in run]
                run = []
        if cells:
            rs, cs = [r for r, _ in cells], [c for _, c in cells]
            overlay = [round(min(cs) / cols, 3), round(min(rs) / rows, 3), round((max(cs) + 1) / cols, 3), round((max(rs) + 1) / rows, 3)]
    return dict(w=int(w), h=int(h), crop_px=int(round(crop_w)), upscale=round(1080.0 / max(crop_w, 1.0), 2),
                acuity=round(float(np.median(acu)), 3) if acu else None, overlay=overlay)


def source_look(path, t0=None, t1=None, keep=True):
    """look_measures of a video file (or of its seconds t0-t1), measured once and, with keep (the owner's sources),
    kept in LOOK_CACHE by the file's path, size and time; a run's own files (keep=False) are measured and not kept.
    None when the file cannot be read or measured: the look only informs a model, so a failed measure (a timeout, a
    broken frame, a bad cache) is 'not measured' and the ideas stage and the export check go on."""
    try:
        return _source_look(path, t0, t1, keep)
    except Exception:  # noqa: BLE001 - a measure that informs a model never stops the run
        return None


def _source_look(path, t0, t1, keep):
    try:
        stt = os.stat(path)
    except OSError:
        return None
    key = f"{LOOK_V}|{path}|{stt.st_size}|{int(stt.st_mtime)}|{t0}|{t1}"
    cache = (jload(LOOK_CACHE, {}) or {}) if keep else {}
    if key in cache:
        return cache[key]
    wh = probe_wh(path)
    if not wh:
        return None
    w, h, dur = wh
    a, b = (float(t0), float(t1)) if t0 is not None and t1 is not None else (0.0, dur)
    times = [a + (b - a) * (k + 0.5) / LOOK_FRAMES for k in range(LOOK_FRAMES)]
    frames = [g for g in (grey_frame(path, t, w, h) for t in times) if g is not None]
    if not frames:
        return None
    out = look_measures(frames, w, h)
    if not keep:
        return out
    try:
        cache = jload(LOOK_CACHE, {}) or {}
        cache[key] = out
        os.makedirs(os.path.dirname(LOOK_CACHE), exist_ok=True)
        jsave(LOOK_CACHE, cache)
    except OSError:
        pass
    return out


def look_line(look):
    """A look as one plain line a model reads beside the frames."""
    if not look:
        return "not measured"
    ov = look.get("overlay")
    return (f"{look['w']}x{look['h']}; its vertical crop is {look['crop_px']} px wide, shown {look['upscale']}x at 1080; "
            f"edge acuity at phone size {look.get('acuity')} (a sharp HD picture measures about 1.0, a soft 4x upscale about 0.6); "
            + (f"a burned-in band (timecode, logo or text) stays in every frame at x {ov[0]}-{ov[2]}, y {ov[1]}-{ov[3]} of the frame"
               if ov else "no burned-in band measured"))


def references():
    """The marketing references' own captions, lengths and how people answered; their pace in numbers (atlas)."""
    out = []
    for folder in sorted(os.listdir(REFS)) if os.path.isdir(REFS) else []:
        d = os.path.join(REFS, folder)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".info.json"):
                j = jload(os.path.join(d, f), {}) or {}
                out.append(dict(id=f.split("__")[0], kind=folder, seconds=round(float(j.get("duration") or 0), 1), likes=j.get("like_count"),
                                comments=j.get("comment_count"), caption=(j.get("description") or j.get("title") or "")[:300]))
    out.sort(key=lambda x: -(x.get("comments") or 0))
    return out


def ref_pace():
    """The marketing references' medians (atlas numbers, folder 02): seconds, median shot, cuts a 10 s, spoken words a second."""
    rows = [x for x in jload(ATLAS, []) or [] if str(x.get("wf", "")).startswith("02")]

    def med(k, d):
        v = [float(x[k]) for x in rows if isinstance(x.get(k), (int, float)) and x[k] > 0]
        return round(statistics.median(v), 3) if v else d
    return dict(n=len(rows), seconds=med("dur", 29.7), shot_med_s=med("shot_med", 0.58), cuts10=med("cuts10", 12.0), wps=med("wps", 3.44),
                sfx10=med("sfx10", 13.6))


def recorded_pool(srcs):
    """Explicit reuse of synthetic Studio receipts, never a customer project."""
    root = os.environ.get("WF2_REUSE_RECORDINGS")
    if not root:
        return {}
    by_file = {s["file"]: s for s in srcs}
    out = {}
    for sid in sorted(os.listdir(root)):
        path = os.path.join(root, sid, "record.json")
        rec = jload(path, {}) or {}
        source = rec.get("source") or {}
        asks = rec.get("asks") or []
        exports = rec.get("exports") or []
        # A one-sentence film needs a one-sentence before-first-result recording.
        if (source.get("file") not in by_file or rec.get("state") != "done" or rec.get("failures")
                or len(asks) != 1 or asks[0].get("phase") != "before_first_result"
                or not exports or exports[-1].get("h", 0) <= exports[-1].get("w", 0)):
            continue
        if not all(os.path.isfile(x.get("file", "")) for x in [rec.get("upload") or {}, *exports]):
            continue
        out[sid] = dict(record=path, source=by_file[source["file"]]["id"], file=source["file"],
                        t0=source["t0"], t1=source["t1"], typed=asks[0]["text"],
                        recorded_at=rec.get("recorded_at"), be_sha=rec.get("be_sha"))
    if not out:
        raise RuntimeError("no eligible synthetic Studio recordings in WF2_REUSE_RECORDINGS")
    return out


def reuse_record(path, destination):
    """Verify immutable receipts before copying them into this run's cleanup boundary."""
    rec = json.load(open(path))
    root = os.path.realpath(os.path.dirname(path))
    for media in [rec["upload"], *rec["exports"]]:
        source = os.path.realpath(media["file"])
        if os.path.commonpath([root, source]) != root:
            raise RuntimeError("Studio recording media escaped its recording folder")
        digest = hashlib.sha256()
        with open(source, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if not media.get("sha256") or digest.hexdigest() != media["sha256"]:
            raise RuntimeError("Studio recording receipt hash mismatch: " + os.path.basename(source))
    shutil.copytree(root, destination)

    def remap(value):
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, str) and value.startswith(root + os.sep):
            return destination + value[len(root):]
        return value

    rec = remap(rec)
    rec["reused_from"] = dict(record=path, recorded_at=rec.get("recorded_at"), be_sha=rec.get("be_sha"))
    jsave(os.path.join(destination, "record.json"), rec)
    return rec


# ----------------------------------------------------------------- ideas

FORMAT_MENU = "showcase (narration over the recording's own pictures) or tutorial (a narrated editing demonstration)"
DEVICE_MENU = ("timeline (on the line that says the typed sentence, the frame pulls back onto the project's own Valmera "
               "timeline drawn from the recording's edit list, the sentence is typed as it is said, and on send the cut "
               "blocks leave, the pictures and captions land, the receipt prints and the frame pushes into the export: "
               "best when the edit cuts, adds pictures or adds captions), wipe (on that line the raw clip plays whole "
               "while the sentence appears, then a leaning wipe line crosses the frame and the export is behind it, "
               "with a count-up grid of the films the product made from this clip afterwards: best when the change "
               "reads in one frame, such as a reframe, a look or captions)")


def format_of(script, knobs=None):
    idea = script.get("idea_full") or {}
    f = (knobs or {}).get("format") or idea.get("format")
    return f if f in D.FORMATS else "tutorial"


def device_of(script, knobs=None):
    """The device the film lays on its typed line (and the hook, for the triptych): the direction's, else the idea's,
    else the timeline (the owner's favourite of the nine, 13 Nov)."""
    d = (knobs or {}).get("device") or (script.get("idea_full") or {}).get("device")
    return d if d in D.DEVICES else "timeline"



def idea_frames(menu, files, per=IDEA_FRAMES, w=384):
    """What the ideas call sees of each clip: up to `per` frames at the middles of the windows it is offered, spread over
    them, each labelled with its clip and second. [] when no frame can be read."""
    out = []
    for m in menu:
        wins = m.get("sentences") or m.get("windows") or []
        if not wins or not files.get(m["id"]):
            continue
        count = min(per, len(wins))
        pick = [wins[round(k * (len(wins) - 1) / max(count - 1, 1))] for k in range(count)]
        for win in pick:
            t = (float(win["t0"]) + float(win["t1"])) / 2
            b = frames(files[m["id"]], [t], w)[0]
            if b:
                out += [dict(type="text", text=f"clip {m['id']} at {t:.0f} s (window {win['t0']}-{win['t1']} s)"), img(b)]
    return out


def ideas(ctx):
    able = able_cards(ctx)
    srcs = sources()
    old = None
    if ctx.args.get("remake"):
        path = ctx.args["remake"]
        record = jload(os.path.join(path, "film.json") if os.path.isdir(path) else path)
        old = record["script"]["idea_full"]
        srcs = [src for src in srcs if src["id"] == old["source"]]
    if not srcs:
        raise FileNotFoundError(f"source video unavailable under {SRC_ROOT}")
    recordings = recorded_pool(srcs) if not old else {}
    clocks, sentences, menu = {}, {}, []
    for src in srcs:
        info = probe_wh(src["file"])
        if not info:
            continue
        dur = info[2]
        lo, hi = (max(0, old["t0"]-45), min(dur, old["t1"]+45)) if old else (0., dur)
        rows, words = source_sentences(ctx, src["file"], src["id"], lo, hi)
        if not rows:
            continue
        clocks[src["id"]], sentences[src["id"]] = words, rows
        menu.append(dict(id=src["id"], title=src["title"], seconds=dur, sentences=rows,
                         excerpt_edges=[lo, hi], picture=look_line(source_look(src["file"], lo, hi))))
    system = """Read the numbered, punctuated speech first. Find one complete thought from one speaker.
Name who speaks and the actual claim. Then derive an editor's concrete problem and an edit that lets the
viewer hear and see the whole claim. The ask must cover its entire spoken unit, including a list's last item.
Write one natural instruction the editor could say in a breath: name the visual change and how long it should stay.
The first and last indices already specify the clip. Put the boundary quotations and transcription notes in the
research fields. The sentence typed in Studio should be the sentence the narrator can speak.
Choose inclusive first_sentence and last_sentence indices; include setup and resolution and exclude clipped
excerpt edges. For a remake preserve the supplied idea, source, style and requested format while expanding the ask
to cover the complete claim. Return JSON {"ideas":[{"title","speaker","claim","pain","technique","source",
"first_sentence","last_sentence","asks":[the actual sentence to type],"must_see":[[visible proof]],
"style","format","device"}]}. Use the source title and words to attribute the speech. The selected picture should look premium at phone size: consider the supplied crop, sharpness and burned-in graphics."""
    content = dict(clips=menu, remake=old, count=1 if old else ctx.args.get("batch_candidates", 3),
                   brief=ctx.args.get("line"), formats=FORMAT_MENU, devices=DEVICE_MENU,
                   styles=[c["id"] for c in able], previous=ctx.args.get("batch_previous", []))
    if recordings:
        content["recordings"] = recordings
        system += " When choosing an existing recording, include its recording_id, exact typed ask, and source thought."
    if not old:
        content["delivered"] = DL.prompt_block(ctx.workflow)
    by = {src["id"]: src for src in srcs}
    pictures = idea_frames(menu, {key: src["file"] for key, src in by.items()})
    prompt = json.dumps(content, ensure_ascii=False)
    repair = ""
    for attempt in range(3):
        got, use = ask(system, [dict(type="text", text=prompt+repair), *pictures],
                       what="ideas on spoken claims", effort="high")
        try:
            choices = got["ideas"]
            if not choices:
                raise ValueError("return one complete thought from the supplied transcript")
            for item in choices:
                rows = sentences[item["source"]]
                first, last = item["first_sentence"], item["last_sentence"]
                if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(rows):
                    raise ValueError(f"return inclusive indexes between 0 and {len(rows)-1}")
                if not item.get("asks"):
                    raise ValueError("include the actual typed sentence")
            break
        except (KeyError, TypeError, ValueError) as exc:
            if attempt == 2:
                raise
            repair = f"\nComplete the idea record using the supplied fields and sources: {exc}"
    ideas_out = []
    styles = {card["id"] for card in able}
    for item in choices:
        src = by[item["source"]]
        choice = [item["first_sentence"], item["last_sentence"]]
        rows = sentences[src["id"]]
        duration = next(m["seconds"] for m in menu if m["id"] == src["id"])
        a, b = snap_window(rows, choice, src["file"], duration, clock=clocks[src["id"]])
        prior = recordings.get(item.get("recording_id"))
        if prior and prior["source"] == item["source"] and prior["t0"] <= rows[choice[0]]["t0"] and prior["t1"] >= rows[choice[1]]["t1"]:
            previous = rows[choice[0]-1]["t1"] if choice[0] else 0
            following = rows[choice[1]+1]["t0"] if choice[1]+1 < len(rows) else duration
            if previous <= prior["t0"] and prior["t1"] <= following:
                item.update(recording=prior["record"], asks=[prior["typed"]])
                a, b = prior["t0"], prior["t1"]
        if item.get("style") not in styles:
            item["style"] = able[0]["id"]
        selected = rows[choice[0]:choice[1]+1]
        idea = dict(item, file=src["file"], source_title=src["title"], t0=a, t1=b, sentences=selected,
                    source_sentence_range=choice)
        if old:
            idea.update({key:old[key] for key in ("title","format","style","device") if key in old})
        ideas_out.append(idea)
    return dict(source=dict(kind="podcast speech", sources=[src["file"] for src in srcs]), ideas=ideas_out,
                chosen=ideas_out[0]["title"], spend=[use])


def restore_window(ctx, idea):
    src = idea["file"]
    duration = probe_wh(src)[2]
    lo, hi = max(0, idea["t0"]-45), min(duration, idea["t1"]+45)
    rows, words = source_sentences(ctx, src, "prior-idea", lo, hi)
    got, _ = ask("Locate the complete claim for this existing edit in the numbered sentences. "
                 "Give first_sentence,last_sentence,speaker and ask. Preserve the editing idea and cover the "
                 "whole claim in one concise, natural instruction; the indices carry its exact boundaries.", json.dumps(dict(idea=idea, sentences=rows)),
                 what="restore complete thought", effort="high")
    choice = [got["first_sentence"], got["last_sentence"]]
    a, b = snap_window(rows, choice, src, duration, clock=words)
    return dict(t0=a, t1=b, source_sentence_range=choice, sentences=rows[choice[0]:choice[1]+1],
                speaker=got["speaker"], asks=[got["ask"]])


def record_settings(run_id):
    # The gauntlet builds isolated environments for its web and worker processes.
    tags = dict(VALMERA_RUN=run_id, CODEX_PROXY_PROOF=run_id)
    return dict(STACK, transcriber="whisper" if os.getenv("CODEX_PROXY_LOCAL") else STACK["transcriber"],
                env={**STACK["env"], **tags}, backend_env={**STACK.get("backend_env", {}), **tags})


def record(ctx, idea, k, ask_text, follow_ups=None):
    """One session on a local stack of the product: upload the clip, type the sentence before the first result, export.
    While the session stays open each result is exported and checked against everything typed so far (answer_turn);
    a result the check finds short gets a follow-up typed in the same session, the way a customer asks again, up to
    `follow_ups` (FOLLOW_UPS) of them. The record (upload, every export, EDLs, journey, every typed ask with its phase,
    sha256s) is kept in this run's folder and deleted with it. A kept record.json is reused: its follow-ups are never
    typed again."""
    studio_root = os.path.join(ctx.tmp, "studio")
    sid = f"m{ctx.run_id[-6:]}-{k}-s{STACK_V}"
    rec_p = os.path.join(studio_root, "records", sid, "record.json")
    if os.path.exists(rec_p):
        return json.load(open(rec_p))
    if idea.get("recording"):
        original = json.load(open(idea["recording"]))
        if original["asks"][0]["text"] != ask_text:
            raise RuntimeError("the reused recording's instruction changed")
        destination = os.path.join(studio_root, "records", original["sid"])
        if os.path.exists(os.path.join(destination, "record.json")):
            return json.load(open(os.path.join(destination, "record.json")))
        ctx.log(f"reusing verified synthetic recording {original['sid']} from {original.get('recorded_at')}")
        return reuse_record(idea["recording"], destination)
    n_follow = FOLLOW_UPS if follow_ups is None else int(follow_ups)
    live = live_dir(ctx, sid)
    shutil.rmtree(live, ignore_errors=True)          # a session that did not finish left turns of a project now gone
    if not idea.get("source_sentence_range"):
        # Older batch plans carry only approximate seconds. Resolve their thought before recording.
        idea.update(restore_window(ctx, idea))
        ask_text = idea["asks"][0]
    t0, t1 = idea["t0"], idea["t1"]
    asks = [dict(text=ask_text, phase="before_first_result")] + [dict(live=True, phase="after_result")] * n_follow
    row = dict(sid=sid, source=idea["file"], t0=t0, t1=t1, plan=PLAN, view=VIEW, asks=asks, note=idea.get("title"))
    if n_follow:
        row["follow_ups"] = dict(dir=live, wait_s=FOLLOW_UP_WAIT_S)
    spec = dict(settings=record_settings(ctx.run_id), sessions=[row])
    sp = os.path.join(ctx.run_dir, f"studio_spec_{k}.json")
    jsave(sp, spec)
    shared_stack = os.environ.get("VALMERA_BATCH_STUDIO")
    run = shared_stack or f"wf2-{ctx.run_id}-{k}"
    env = dict(os.environ, VALMERA_STUDIO=studio_root)
    env.pop("ANTHROPIC_API_KEY", None)
    ctx.log(f"recording {sid}: {idea['source']} {t0}-{t1} s, typed {ask_text!r} (stack {run}, up to {n_follow} follow-ups)")
    t = time.time()
    command = ["zsh", HEAVY, f"wf-studio-{ctx.run_id}", PY, os.path.join(HERE, "studio.py"), "record", "--spec", sp, "--run", run,
               "--parallel", "1"]
    if shared_stack:
        command = [PY, os.path.join(HERE, "studio.py"), "record", "--spec", sp, "--run", run, "--attached"]
    elif os.environ.get("GAUNTLET_LOCK_HELD") != "1":
        command = ["zsh", f"{VV}/ops-loop/tools/gauntlet_lock.sh", *command]
    out_p = os.path.join(studio_root, f"{sid}.out.txt")
    os.makedirs(studio_root, exist_ok=True)
    must = (idea.get("must_see") or [[]])[0] or []
    limit = 5400 + n_follow * (3600 + FOLLOW_UP_WAIT_S)
    with open(out_p, "w") as out:
        proc = subprocess.Popen(command, cwd=HERE, env=env, stdout=out, stderr=subprocess.STDOUT, text=True)
        while proc.poll() is None:
            if n_follow:
                answer_turns(ctx, idea, live, must, n_follow)
            if time.time() - t > limit:
                proc.kill()
                proc.wait()
                break
            time.sleep(5)
    rc = proc.returncode
    tail = open(out_p, errors="replace").read()[-4500:]
    ctx.log_raw(tail)
    rd = os.path.join(G, "runs", run)
    if not shared_stack:
        try:          # the stack is down after the recording; its database and its folder (the clip, the browser's files) go too
            subprocess.run([PY, os.path.join(G, "runner", "stack.py"), "down", run, "--drop-db"], cwd=os.path.join(G, "runner"),
                           capture_output=True, text=True, timeout=300)
        except subprocess.SubprocessError:
            pass
        shutil.rmtree(rd, ignore_errors=True)
    shutil.rmtree(os.path.join(studio_root, "corpus", sid), ignore_errors=True)
    for name in os.listdir(live) if os.path.isdir(live) else []:     # the turns' export copies: the record keeps every export
        shutil.rmtree(os.path.join(live, name, "exports"), ignore_errors=True)
    if not os.path.exists(rec_p):
        raise RuntimeError(f"the recording {sid} kept nothing (exit {rc}): {tail[-600:]}")
    rec = json.load(open(rec_p))
    rec["recorded_in_s"] = round(time.time() - t)
    jsave(rec_p, rec)
    return rec


# ----------------------------------------------------------------- follow-ups in the same session

def live_dir(ctx, sid):
    """Where an open session hands its results to the workflow and reads the next request (studio.py Recording.turn)."""
    return os.path.join(ctx.tmp, "studio", "live", sid)


def asks_of(rec):
    """Every sentence typed in a recording, in order: [text]; the first is the idea's, the rest its follow-ups."""
    return [str(a.get("text")) for a in rec.get("asks") or [] if a.get("text")]


def check_text(asks):
    """export_check's sentence: the one sentence as it was, or every sentence typed in the session, in order."""
    return asks[0] if len(asks) == 1 else list(asks)


def answer_turns(ctx, idea, live, must, n_follow):
    """Every result the open session is waiting on (turn_<i>.json without answer_<i>.json): its export is checked
    against everything typed so far (check_<i>.json, kept for the script); when the check finds it short and the
    session has follow-ups left, a follow-up is written from the check and typed next; else the session ends on it."""
    if not os.path.isdir(live):
        return
    for name in sorted(os.listdir(live)):
        if not (name.startswith("turn_") and name.endswith(".json")):
            continue
        i = name[5:-5]
        ans_p = os.path.join(live, f"answer_{i}.json")
        if os.path.exists(ans_p):
            continue
        turn = jload(os.path.join(live, name), {}) or {}
        asks = [str(a.get("text")) for a in turn.get("asks") or [] if a.get("text")]
        answer = dict(stop=True)
        try:
            if not turn.get("exports") or not asks:
                answer["why"] = "the result was not exported"
            else:
                chk = jload(os.path.join(live, f"check_{i}.json"))
                if not (isinstance(chk, dict) and chk.get("v") == CHECK_V):
                    chk = dict(export_check(ctx, turn, check_text(asks), must), stack_v=STACK_V)
                    jsave(os.path.join(live, f"check_{i}.json"), chk)
                ctx.log(f"result of request {i} ({len(asks)} typed): {'shown' if chk.get('ok') else 'not shown'}: "
                        f"{chk.get('why') or chk.get('looks')}")
                if chk.get("ok"):
                    answer["why"] = "the check found everything asked, visibly"
                elif len(asks) - 1 >= n_follow:
                    answer["why"] = f"{n_follow} follow-ups typed"
                elif not chk.get("elements"):
                    answer["why"] = f"nothing to ask again: {chk.get('why')}"
                else:
                    text, use = follow_up(ctx, idea, asks, chk)
                    chk.setdefault("spend", []).extend(use)
                    jsave(os.path.join(live, f"check_{i}.json"), chk)
                    if text:
                        answer = dict(text=text)
                        ctx.log(f"follow-up {len(asks)} typed in the same session: {text!r}")
                    else:
                        answer["why"] = "no follow-up came back"
        except Exception as e:  # noqa: BLE001 - the session never hangs on a check; it ends on this result and the script checks it
            answer["why"] = f"the check failed: {repr(e)[:300]}"
            ctx.log(f"result of request {i}: {answer['why']}")
        jsave(ans_p, answer)


FOLLOW_UP_WORDS = 24     # a narrator reads the follow-up word for word in the film


def follow_up(ctx, idea, asks, chk):
    """The next message a customer types after seeing the result: written by a model from the check's findings (what
    was not visible, the defects), in plain words. (text or None, [spend])."""
    system = ("You are a video editor using Valmera, an AI video editor: you uploaded a clip, typed what you wanted into its chat, "
              "and looked at the result it returned. A careful look at that result found what is listed below. Write the next "
              "message you would type into the same chat so the product fixes it: plain words a customer types, one or two short "
              f"sentences, at most {FOLLOW_UP_WORDS} words, saying what to change and how it should look instead; cover every "
              "defect and every asked thing that is not visible, most important first. If the right shot appears briefly, ask to hold that same shot through the named words and leave good cuts in place. Never "
              "name a tool, a setting or a timecode, never thank or greet. A narrator will read it aloud word for word in a film, "
              "so write numbers as words and use no digits or symbols.")
    facts = dict(typed_so_far=asks, technique=idea.get("technique"), not_visible=[dict(asked=e.get("asked"), observed=e.get("how"), seconds=e.get("seconds")) for e in chk.get("elements") or []
                                                                             if isinstance(e, dict) and not e.get("visible")],
                 defects=chk.get("defects") or [], looks=chk.get("looks"))
    note, spend = "", []
    for _ in range(3):
        got, use = ask(system, "# The result's check\n" + json.dumps(facts, ensure_ascii=False) + note
                       + "\n\nReturn JSON only: {\"say\": the message}", what="follow-up", effort="low", max_tokens=2000)
        spend.append(use)
        text = " ".join(str((got if isinstance(got, dict) else {}).get("say") or "").split())
        n = len(text.split())
        if text and n <= FOLLOW_UP_WORDS + 6 and not any(c.isdigit() for c in text):
            return text, spend
        note = (f"\n\nYour last message ({text!r}) had {n} words" + (" and digits" if any(c.isdigit() for c in text) else "")
                + f": at most {FOLLOW_UP_WORDS} words, numbers as words.")
    return None, spend


def turn_check(ctx, rec):
    """The check made while the session was open of the result the session ended on (the same export, by sha256),
    pointed at the record's own files; None when there is none."""
    ex = next((e for e in rec.get("exports") or [] if e.get("tag") == "final"), (rec.get("exports") or [None])[-1])
    live = live_dir(ctx, rec.get("sid") or "")
    if not ex or not os.path.isdir(live):
        return None
    for name in sorted(os.listdir(live), key=lambda n: (len(n), n), reverse=True):
        if not (name.startswith("check_") and name.endswith(".json")):
            continue
        chk = jload(os.path.join(live, name), {}) or {}
        if chk.get("v") == CHECK_V and (chk.get("export") or {}).get("sha256") == ex.get("sha256"):
            return dict(chk, export=ex, upload=rec.get("upload"))
    return None


def turns_of(ctx, rec):
    """Each typed request's checked result, for script_state: [{asked, ok, why, export_sha256}]."""
    live, out = live_dir(ctx, rec.get("sid") or ""), []
    for i, a in enumerate(rec.get("asks") or [], 1):
        chk = jload(os.path.join(live, f"check_{i}.json"), {}) or {}
        out.append(dict(asked=a.get("text"), phase=a.get("phase"), follow_up=bool(a.get("follow_up")), ok=chk.get("ok"),
                        why=chk.get("why"), export_sha256=(chk.get("export") or {}).get("sha256")))
    return out


def frames(path, times, w=288):
    """Base64 JPEG frames of a video at the given seconds."""
    out = []
    for t in times:
        r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2",
                            "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60)
        out.append(base64.b64encode(r.stdout).decode() if r.returncode == 0 and r.stdout else None)
    return out


def img(b64):
    return dict(type="image", source=dict(type="base64", media_type="image/jpeg", data=b64))


CHECK_V = 8           # the export check reads the spoken words too (EDL keep ranges on the source's captions); the end card is the brand's;
                      # 6 (20 Nov): its "looks" is read on 720 px frames beside the measured picture (it called a soft 4x VHS upscale
                      # "a clean vertical cut" from 288 px frames). A record, never a reason to refuse the recording
                      # 7 (20 Nov): the after is the stretch that holds the landings the sentence names (the after held the trash
                      # can and missed the black hole the payoff then named)
LOOK_CHECK_FRAMES = 4     # full-size frames of the export the check reads its look from


def export_edl(ex):
    """The export's own edit list (records/<sid>/edl/v<NNN>.json beside its file's folder), or None."""
    d = os.path.join(os.path.dirname(os.path.dirname(ex["file"])), "edl")
    if ex.get("edl_version") is not None and os.path.exists(os.path.join(d, f"v{int(ex['edl_version']):03d}.json")):
        return jload(os.path.join(d, f"v{int(ex['edl_version']):03d}.json"), {})
    if os.path.isdir(d) and os.listdir(d):
        return jload(os.path.join(d, sorted(os.listdir(d))[-1]), {})
    return None


def upload_words(rec):
    """The source's caption words on the upload's own clock: [(upload second, word)], or None without the source."""
    src = next((s for s in sources() if s["file"] == rec["source"]["file"]), None)
    if not src:
        return None
    t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
    return [(t - t0, w) for t, w in json3_words(src["captions"]) if t0 <= t <= t1]


def spoken(rec, ex):
    """What the upload and the export say: the source's caption words on the upload's seconds, and on the seconds the
    export's EDL keeps, in the export's order (each kept range with its export start)."""
    ws = [(float(w["t0"]), w["w"]) for w in rec.get("words", [])] or upload_words(rec)
    if ws is None:
        return None, None
    up = " ".join(w for _, w in ws)
    rows, at = [], 0.0
    for k in (export_edl(ex) or {}).get("keep") or []:
        try:
            a, b = float(k[0]), float(k[1])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append(f"export {at:.1f}-{at + b - a:.1f} s (upload {a:.1f}-{b:.1f} s): " + " ".join(w for t, w in ws if a <= t < b))
        at += b - a
    return up, rows


def export_words(rec, ex, edl=None):
    """The export's spoken words on its own clock, [(export second, word)]: the source's captions inside each range
    its EDL keeps (`edl`, else the export's own), in the export's order (the captions' clock is about a third of a
    second off the voice)."""
    ws, out, at = upload_words(rec) or [], [], 0.0
    for k in (edl or export_edl(ex) or {}).get("keep") or []:
        try:
            a, b = float(k[0]), float(k[1])
        except (TypeError, ValueError, IndexError):
            continue
        out += [(round(at + t - a, 3), w) for t, w in ws if a <= t < b]
        at += b - a
    return out


def matching_speech_clock(upload, export, seconds):
    """Measure sample alignment throughout the two actual audio tracks, including gaps between words."""
    from contextlib import closing
    from itertools import zip_longest
    import numpy as np

    rate = 16000
    window = rate // 10

    def blocks(path):
        command = [PX.FF, "-nostdin", "-v", "error", "-i", path, "-t", str(seconds), "-map", "0:a:0",
                   "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as decoder:
            try:
                while data := decoder.stdout.read(window * 4):
                    yield np.frombuffer(data, dtype="<f4")
                if decoder.wait():
                    raise ValueError("Audio decode failed")
            finally:
                decoder.stdout.close()
                if decoder.poll() is None:
                    decoder.kill()

    count, minimum = 0, 1.0
    silence = .001  # -60 dBFS RMS. Any audible addition in an upload's silent interval must be transcribed.
    try:
        with closing(blocks(upload)) as left, closing(blocks(export)) as right:
            for a, b in zip_longest(left, right, fillvalue=np.empty(0, dtype=np.float32)):
                size = max(len(a), len(b))
                if len(a) < size:
                    a = np.pad(a, (0, size-len(a)))
                if len(b) < size:
                    b = np.pad(b, (0, size-len(b)))
                power_a, power_b = float(a @ a), float(b @ b)
                if max(power_a, power_b) < size * silence**2:
                    continue
                agreement = float(a @ b) / math.sqrt(max(1e-24, power_a * power_b))
                if agreement < .97:
                    return None
                minimum = min(minimum, agreement)
                count += 1
    except (OSError, ValueError):
        return None
    return dict(audible_windows=count, sample_rate=rate, window_samples=window,
                minimum_correlation=minimum) if count else None


def export_speech_clock(ctx, upload, ex, body, up_words):
    # A picture-only edit can retain the studio transcript exactly. A trim, retime, replacement
    # or additional voice takes the ordinary transcription path on the export itself.
    if abs(float(upload["dur"]) - body) <= .05 and up_words and max(w["t1"] for w in up_words) <= body:
        aligned = matching_speech_clock(upload["file"], ex["file"], body)
        if aligned:
            file = ex["file"]
            signature = [file, os.stat(file).st_size, os.stat(file).st_mtime_ns, 0.0, body]
            jsave(os.path.join(ctx.tmp, "speech-export.json"),
                  dict(signature=signature, words=up_words, waveform_match=aligned))
            return up_words
    return hear_clock(ctx, ex["file"], "export", hi=body)


def proof_plan(ctx, idea, rec, rc, ex):
    """Number the actual upload and authored export separately. Edits may reorder or remove speech."""
    body = export_program_seconds(ex, export_edl(ex))
    up = rec["upload"]
    up_words = rec.get("words") or []
    if not numbered_sentences(up_words):
        up_words = hear_clock(ctx, up["file"], "upload")
    ex_words = export_speech_clock(ctx, up, ex, body, up_words)
    clocks = {"upload": up_words, "export": ex_words}
    rows = {key: numbered_sentences(words) for key, words in clocks.items()}
    rc.update(upload_says=" ".join(w["w"] for w in up_words), export_says=" ".join(w["w"] for w in ex_words),
              source_title=idea.get("source_title"), speaker=idea.get("speaker"))
    sheet, use = story_chain.plan("marketing", idea, dict(receipts=rc, sentences=rows))
    spans = {}
    for key in ("split", "before", "after", "stretch"):
        own = "upload" if key == "before" else "export"
        media, duration = (up, up["dur"]) if own == "upload" else (ex, body)
        spans[key] = snap_window(rows[own], sheet["spans"][key], media["file"], duration, clock=clocks[own])
    proof = dict(spans=spans, sentences=rows, sentence_ranges=sheet["spans"])
    return sheet, proof, use


def hook_beat(rec, ex, proof):
    first, last = proof["sentence_ranges"]["split"]
    rows = proof["sentences"]["export"][first:last+1]
    a, b = proof["spans"]["split"]
    edl = export_edl(ex)
    return dict(t=[a, b], says=" ".join(r["text"] for r in rows), landing=rows[0]["t0"],
                upload_at=D.src_at(D.program(edl)[0], a) if edl else a)


def review_times(ex, edl, words):
    """Review the entering and outgoing frame of actual picture cuts and their spoken cues."""
    import math
    duration, fps = ex["dur"], ex.get("fps") or 30
    last = max(0, math.ceil(duration * fps - .05) - 1)
    def frame(t):
        return max(0, min(last, math.ceil(t * fps - 1e-6)))
    def spread(values, limit):
        values = sorted(set(values))
        if len(values) <= limit:
            return values
        return [values[round(i * (len(values)-1) / (limit-1))] for i in range(limit)]
    cuts, cues = set(), set()
    for shot in D.MF.pictures_of(edl or {}) + D.MF.titles_of(edl or {}):
        a = shot["start"]
        b = a + shot["duration_s"]
        for t in (a, b):
            edge = frame(t)
            cuts.update([max(0, edge - 1), edge])
        for w in words:
            if w["t0"] <= b + .6 and w["t1"] >= a:
                cues.update([frame(w["t0"]), frame(w["t1"])])
    # Cover the rest of the film too. A frame already present spends no extra image.
    n = min(40, max(1, int(duration)))
    overview = [frame(min(duration / 2, .5) + i * max(0, duration-1) / max(n-1, 1)) for i in range(n)]
    indices = set(spread(cuts, 24))
    indices.update(spread(cues - indices, 16))
    indices.update(spread(set(overview) - indices, min(len(overview), max(4, 44-len(indices)))))
    return [math.floor(i * 1000 / fps) / 1000 for i in sorted(indices)]


def export_check(ctx, rec, ask_text, must_see):
    """The export against the sentence, element by element, on its actual shot and word boundaries and the upload's frames."""
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1] if rec.get("exports") else None)
    if not ex:
        return dict(ok=False, v=CHECK_V, why="no export")
    if rec.get("failures") or rec.get("state") != "done":
        return dict(ok=False, v=CHECK_V, why="the session failed: " + "; ".join(str(f.get("kind")) for f in rec.get("failures") or []), export=ex)
    up = rec["upload"]
    if ex["w"] >= ex["h"]:
        return dict(ok=False, v=CHECK_V, why=f"the export is {ex['w']}x{ex['h']}, not vertical", export=ex)
    edl = export_edl(ex) or {}
    words = D.program(edl)[0].kept_words(rec["words"]) if rec.get("words") and edl else []
    et = review_times(ex, edl, words)
    ut = [round(0.5 + i * (up["dur"] - 1) / 9, 2) for i in range(10)]
    if isinstance(ask_text, (list, tuple)):     # follow-ups typed in the same session after seeing the result before
        typed = ("The sentences typed into the product, in order, in one session: the first before its first result, each later "
                 "one a follow-up typed after seeing the result before it:\n" + "\n".join(f"{n}. {t!r}" for n, t in enumerate(ask_text, 1))
                 + "\nThe elements are the parts of these sentences (where a follow-up changes an earlier ask, the follow-up's "
                   "words win), and only those: what the film's planner hoped to see as well (a note, never a reason to fail): ")
    else:
        typed = (f"The sentence typed into the product: {ask_text!r}\nThe elements are the parts of this sentence, "
                 "and only those: what the film's planner hoped to see as well (a note, never a reason to fail): ")
    content = [dict(type="text", text=typed +
                                      f"{json.dumps(must_see)}\n"
                                      f"First, the clip as uploaded ({up['w']}x{up['h']}, {up['dur']} s), ten frames:")]
    for t, b in zip(ut, frames(up["file"], ut)):
        if b:
            content += [dict(type="text", text=f"upload at {t} s"), img(b)]
    up_words, ex_words = spoken(rec, ex)
    if words:
        content.append(dict(type="text", text="Studio indexed speech on the export clock: " +
                            " ".join(f"{w['t0']:.2f}-{w['t1']:.2f} {w['w']}" for w in words)))
    if up_words:
        content.append(dict(type="text", text=f"What the upload says (its captions): {up_words}"))
    if ex_words:
        content.append(dict(type="text", text="What the export says, kept range by kept range (its EDL on the same captions; the export's "
                                              "speed changes, if any, are not counted):\n" + "\n".join(ex_words)))
    content.append(dict(type="text", text=f"Then the export the product returned ({ex['w']}x{ex['h']}, {ex['dur']} s), including the frames at its picture cuts and spoken cues:"))
    for t, b in zip(et, frames(ex["file"], et)):
        if b:
            content += [dict(type="text", text=f"export at {t} s"), img(b)]
    body_s = max(export_program_seconds(ex, edl), 1.0)
    measured = dict(upload=source_look(up["file"], keep=False), export=source_look(ex["file"], 0.0, body_s, keep=False))
    lt = [round(body_s * (k + 0.5) / LOOK_CHECK_FRAMES, 2) for k in range(LOOK_CHECK_FRAMES)]
    content.append(dict(type="text", text="How the export looks, for the looks line: the upload measured " + look_line(measured["upload"])
                                          + "; the export measured " + look_line(measured["export"])
                                          + ". The export at full phone size (720 px wide):"))
    for t, b in zip(lt, frames(ex["file"], lt, w=720)):
        if b:
            content += [dict(type="text", text=f"export at {t} s, full size"), img(b)]
    system = ("You check a product's real output before a marketing film shows it. Be strict and literal: a thing asked for counts as "
              "delivered only if it is plainly visible in the export's frames, or, for what is said and where the cut starts or ends, "
              "plainly in the export's spoken words as given. The spoken words come from automatic captions whose times are "
              "about a third of a second off, so a word at the very edge of a kept range may be listed or missing: judge a cut point by "
              "the sentence, not by one edge word. Every Valmera export ends on Valmera's own end card and carries its small watermark: "
              "that is the brand, never a defect, and never part of the after you choose. You also choose the seconds the film will show.")
    content.append(dict(type="text", text=(
        "Return JSON only: {\"elements\": [{\"asked\": a part of the typed sentence, \"visible\": true|false, \"seconds\": [export seconds where "
        "it shows], \"how\": what is seen, \"caption_line\": for an asked caption change, transcribe one complete visible line verbatim, else null, \"caption_y\": its vertical centre as a fraction of frame height}], \"all_visible\": true|false, \"after\": [start, end] export seconds, 2 to 4 s, before the end card: "
        "the stretch that holds the most of the asked change landing (each picture, title, caption or cut the sentence asks for, "
        "entered a breath before the first one lands and ended once the last is seen; when not all fit in 4 s, the ones that read "
        "best), so the film's after shows every landing its narration will name, \"tease_s\": the export second of its most striking "
        "frame, \"spoken_thought\": for a selective grade, [start, end] on the export clock of a complete spoken thought that shows "
        "the grade, from its setup through the last word of the payoff, else null, \"before\": [start, end] upload seconds, "
        "1.5 to 3 s, where the raw clip looks most like raw footage, \"looks\": one "
        "plain, true line on how the export looks to an editor at full size (sharp or soft, clean or carrying a burned-in timecode "
        "or logo, its colour and light, inserted pictures matching it or not), \"defects\": [anything an editor would call broken: a cut face, wrong "
        "framing, unreadable or misspelled captions, a black frame; never the end card, never a planner's note]}")))
    got, use = ask(system, content, what="export check", effort="low", model=CHECK_MODEL, max_tokens=6000)
    got = got if isinstance(got, dict) else {}
    vis = [e for e in got.get("elements") or [] if isinstance(e, dict)]
    ok = bool(vis) and all(e.get("visible") for e in vis) and bool(got.get("all_visible")) and not got.get("defects")
    return dict(ok=ok, v=CHECK_V, why=None if ok else ("not visible: " + "; ".join(str(e.get("asked")) for e in vis if not e.get("visible"))
                                             + ("; defects: " + "; ".join(map(str, got.get("defects") or [])) if got.get("defects") else "")),
                export=ex, upload=up, **got, look_measured=measured, spend=[use])


# ----------------------------------------------------------------- the script

def receipts(rec, chk, ask_text):
    """The facts a film may state about its recording. Every sentence typed in the session is here: `typed` is the first
    (before the first result), `follow_ups` the ones typed after seeing a result, `asks` all of them with their phase,
    and `exports` every export the session kept with its sha256; the film says and shows them all (typed_line)."""
    ex = chk["export"]
    up_words, ex_words = spoken(rec, ex)
    asks = [a for a in rec.get("asks") or [] if a.get("text")] or [dict(text=ask_text, phase="before_first_result")]
    extra = {}
    if len(asks) > 1:
        extra = dict(follow_ups=[dict(typed=a["text"], phase="after seeing the result before it, in the same Studio session",
                                      written_from=a.get("written_from")) for a in asks[1:]],
                     sentences_typed=len(asks), result_shown="the export after the last sentence")
    return dict(typed=asks[0]["text"], **extra, asks=[dict(text=a["text"], phase=a.get("phase"), follow_up=bool(a.get("follow_up"))) for a in asks],
                exports=[dict(tag=e.get("tag"), sha256=e.get("sha256"), edl_version=e.get("edl_version"), seconds=e.get("dur"))
                         for e in rec.get("exports") or []],
                upload_says=up_words, export_says=ex_words, phase="before the first result", first_result_s=rec.get("first_result_s"), session_s=rec.get("seconds"),
                upload=dict(seconds=rec["upload"]["dur"], w=rec["upload"]["w"], h=rec["upload"]["h"], sha256=rec["upload"]["sha256"]),
                export=dict(seconds=ex["dur"], w=ex["w"], h=ex["h"], sha256=ex["sha256"], edl_version=ex.get("edl_version")),
                visible=[dict(asked=e.get("asked"), how=e.get("how"), seconds=e.get("seconds"),
                              **{k: e[k] for k in ("caption_line", "caption_y") if e.get(k) is not None}) for e in chk.get("elements") or []],
                looks=chk.get("looks"), product=dict(name="Valmera", url="valmera.io", be_sha=rec.get("be_sha"), fe_sha=rec.get("fe_sha"),
                                                     edit_model=rec.get("models"), recorded_at=rec.get("recorded_at"),
                                                     stack="local Gauntlet stack, synthetic account", account=rec.get("account")),
                source=rec.get("source"), cost_usd=rec.get("cost_usd"), reused_from=rec.get("reused_from"))


def norm_words(s):
    return [x for x in (F.norm_word(w) for w in str(s).split()) if x]


def typed_sentences(rc):
    """Every sentence typed in the recording's session, in order (the first, then its follow-ups)."""
    return [rc.get("typed")] + [f["typed"] for f in rc.get("follow_ups") or []]


def typed_line(rc):
    """What the film shows as typed: the sentence, or every sentence typed in the session, in order, so the film never
    shows one sentence for an edit two made."""
    return " / ".join(str(t) for t in typed_sentences(rc) if t)


SHOW_ROLES = ("figure", "key", "name")       # the display roles of a marketing line (the stories cutter's own names)


def show_text(x):
    return str((x.get("text") if isinstance(x, dict) else x) or "")


FOLD_WORDS = 6     # two items of a line fewer words apart than this would land under 1.8 s apart, the reel's display gap
                   # (reel.py, at a fast narrator's three words a second): the cutter keeps the first and drops the second


def _at(toks, words, taken):
    """The first position in toks, not yet taken, where `words` (normalised) start; None if they are not there."""
    n = len(words)
    return next((k for k in range(len(toks) - n + 1) if n and toks[k:k + n] == words and k not in taken), None)


def fill_display_phrases(sc):
    """Borrow the line's authored display copy or emphasis when its fallback field is missing."""
    for segment in sc.get("segments") or []:
        if 1 <= len(str(segment.get("fallback_text") or "").split()) <= 5:
            continue
        candidates = [show_text(item) for item in segment.get("show") or []]
        candidates += [segment.get("say") or "", *(segment.get("emphasis") or [])]
        for candidate in candidates:
            if isinstance(candidate, str) and 1 <= len(candidate.split()) <= 5:
                segment["fallback_text"] = candidate.strip()
                break


def anchor_show(sc):
    """Every display item as {text, word_i, role, big, small}: anchored to the index of the word of `say` it lands on,
    the way writer.annotate anchors a story's items. 20 Nov: the marketing writer returned plain strings, and the
    cutter, which places an item only on its word_i when the film is cut by verdicts, dropped every one (display_words
    0). Structure only, never a reading of the words, and nothing is dropped:
      - a string becomes an item; a role outside SHOW_ROLES becomes a key word; a `big` that is not part of the text is
        left out, and a `big` that is a proper part of it gets the rest of the text as its `small` when it has none
        (the cutter sets a large part alone and prints the rest only from `small`: '+5 PICTURES' lost 'PICTURES');
      - an item whose word_i is missing or outside `say` gets a word of its own, in order: where its own words stand
        in `say`, else the line's next stressed (`emphasis`) word not yet used, else the next free word;
      - a second item anchored fewer than FOLD_WORDS words after the one before it (and not a figure, which the cutter
        never holds back) becomes that item's small line, so both reach the screen (20 Nov: 'Trash can. Black hole.'
        are said a second apart; the cutter kept TRASH CAN and dropped BLACK HOLE as too close).
    Returns a note per repair, for the log and for the writer's next attempt."""
    notes = []
    for i, s in enumerate(sc.get("segments") or []):
        toks = [F.norm_word(w) for w in str(s.get("say") or "").split()]
        stress = []
        for e in s.get("emphasis") or []:
            k = _at(toks, [w for w in (F.norm_word(x) for x in str(e).split()) if w], set(stress))
            if k is not None:
                stress.append(k)
        items, loose = [], []
        for x in s.get("show") or []:
            item = dict(x) if isinstance(x, dict) else dict(text=str(x))
            item["text"] = str(item.get("text") or "").strip()
            if not item["text"]:
                continue
            if item.get("role") not in SHOW_ROLES:
                item["role"] = "key"
            big = item.get("big").strip() if isinstance(item.get("big"), str) else ""
            if big and big in item["text"]:
                item["big"] = big
                small = item.get("small").strip() if isinstance(item.get("small"), str) else ""
                if not small and big != item["text"]:
                    small = item["text"].replace(big, " ", 1).strip(" ,;:-\u2013\u2014\u00b7/|")
                    small = " ".join(small.split())
                if small:
                    item["small"] = small
                else:
                    item.pop("small", None)
            else:
                item.pop("big", None)
                item.pop("small", None)
            wi = item.get("word_i")
            if not (isinstance(wi, int) and not isinstance(wi, bool) and 0 <= wi < len(toks)):
                loose.append(item)
            items.append(item)
        taken = {x["word_i"] for x in items if not any(x is y for y in loose)}
        last = -1
        for item in loose:
            if not toks:
                continue
            own = _at(toks, [w for w in (F.norm_word(x) for x in item["text"].split()) if w], taken)
            wi = own if own is not None else next((k for k in stress if k not in taken and k > last), None)
            if wi is None:
                wi = next((k for k in range(last + 1, len(toks)) if k not in taken), None)
            if wi is None:
                wi = next((k for k in range(len(toks)) if k not in taken), 0)
            item["word_i"], last = wi, wi
            taken.add(wi)
            notes.append(f"segment {i}: show item {item['text']!r} had no word_i in say; it was set to {wi} "
                         f"({str(s.get('say')).split()[wi]!r}); give every show item its own word_i")
        anchored = sorted((x for x in items if isinstance(x.get("word_i"), int)), key=lambda x: x["word_i"])
        out = [x for x in items if not isinstance(x.get("word_i"), int)]
        for item in anchored:
            prev = out[-1] if out and isinstance(out[-1].get("word_i"), int) else None
            if prev is not None and item["role"] != "figure" and item["word_i"] - prev["word_i"] < FOLD_WORDS:
                prev["big"] = prev.get("big") or prev["text"]
                prev["small"] = " ".join(t for t in (prev.get("small"), item["text"]) if t)
                prev["text"] = f"{prev['text']} {item['text']}"
                notes.append(f"segment {i}: show items {prev['big']!r} and {item['text']!r} land "
                             f"{item['word_i'] - prev['word_i']} words apart, under two seconds; they were made one "
                             "item with big and small")
                continue
            out.append(item)
        s["show"] = out
    return notes


def form_words(rc):
    """The narration's most words: the form's, plus the follow-ups it says word for word."""
    return FORM["words"][1] + sum(len(str(f.get("typed") or "").split()) for f in (rc or {}).get("follow_ups") or [])


def script_problems(sc, ask_text, rc):
    segs = sc.get("segments") or []
    p = []
    pr = sc.get("proof") or {}
    b, a = pr.get("before_after"), pr.get("after_after")
    if not (isinstance(b, int) and isinstance(a, int) and 0 <= b < a <= len(segs) - 2):
        p.append("proof.before_after and proof.after_after must be segment indexes with before < after < the last segment")
    if any(ch.isdigit() for s in segs for ch in str(s.get("say", ""))):
        p.append("digits in say: write numbers as words")
    readings = []
    for seg in segs:
        say = seg.get("say", "")
        readings.append(" ".join(norm_words(say)))
        # Compare the actual typed characters against the writer's explicit spoken-to-print pairs.
        printed = say.casefold()
        for spoken, display in sorted((seg.get("print") or {}).items(), key=lambda pair: -len(pair[0])):
            printed = printed.replace(spoken.casefold(), str(display))
        readings.append(" ".join(norm_words(printed)))
    for typed in [ask_text, *(f["typed"] for f in (rc or {}).get("follow_ups") or [])]:
        if not any(" ".join(norm_words(typed)) in reading for reading in readings):
            p.append(f"the narration needs the typed sentence in spoken form: {typed!r}")
    have = json.dumps(rc)
    for s in segs:
        if not 1 <= len(str(s.get("fallback_text") or "").split()) <= 5:
            p.append("every segment needs viewer-facing fallback_text of one to five words")
        for x in s.get("show") or []:
            for d in [w for w in show_text(x).replace(",", "").split() if any(c.isdigit() for c in w)]:
                if "".join(c for c in d if c.isdigit() or c == ".").strip(".") not in have:
                    p.append(f"the number {d!r} on screen is not in the receipts")
    if not (segs and (segs[-1].get("show") or [])):
        p.append("the last segment is the call to action and shows it (e.g. VALMERA.IO)")
    return p


def audit(sc, rc):
    """A second call: every claim the narration makes about Valmera or this recording, against the receipts."""
    system = ("You audit a marketing script for truth before it is narrated. You get the script and the receipts of the one real "
              "recording it shows. List every claim about Valmera, the edit or the recording (speed, what it did, what was typed, what "
              "is visible or said in the before and the after) and whether the receipts support it: what the upload and the export say "
              "is in upload_says and export_says, what the export shows is in visible and looks; every sentence typed is in asks, and when "
              "follow_ups is present more than one sentence was typed in the session (the after is the result of all of them), so "
              "a claim that one sentence or one try made it is unsupported. General statements about editing, and "
              "plain descriptions of the before and the after that these receipts bear out, are supported. Return JSON only: {\"claims\": [{\"text\": the words, \"supported\": true|false, \"why\": a few words}]}")
    copy = [dict(say=s.get("say"), show=[show_text(x) for x in s.get("show") or []], fallback_text=s.get("fallback_text"))
            for s in sc.get("segments") or []]
    got, use = ask(system, f"# Receipts\n{json.dumps(rc, ensure_ascii=False)}\n\n# Viewer-facing script\n{json.dumps(copy, ensure_ascii=False)}",
                   what="claims audit", effort="low", model=CHECK_MODEL, max_tokens=4000)
    bad = [c for c in (got.get("claims") if isinstance(got, dict) else []) or [] if isinstance(c, dict) and not c.get("supported")]
    return bad, use


DEVICE_BRIEF = {
    "timeline": "The actual typed instruction appears on the recorded project's timeline.",
    "wipe": "The raw clip gives way to the verified export on the typed instruction.",
    "triptych": "The recorded exports illustrate the same moment of the upload.",
}


def write(ctx, idea, card, rc, sheet, proof, inventory, notes=None):
    mode = format_of(dict(idea_full=idea), ctx.args)
    system = story_chain.WRITE + """
Write the six narrated beats of this editing film. Say the speaker's name, role and actual claim
and show why the editor needs this particular change. Place the whole before after its introduction;
the speaker's recording plays once. Say what was actually typed (including each follow-up), then
let the export make its point in the speaker's own voice. Keep the CTA to one line over the end card.
Use the source title, upload_says and verified receipts. Attribute the claim to the speaker.
Write from THE UPLOAD, THE EXPORT and the supplied picture inventory. Show the real Studio composer, sent request, result and site photographs on their corresponding words.
For showcase use the recording's own pictures, Studio and site. Speak all numbers in words.

Return JSON with title, voice, pace, energy, names, named and segments, one segment per sheet row.
Each segment: say, print, fallback_text (one to five words), show (one optional item with text,
word_i and role key|figure|name, anchored on the word spoken; print VALMERA.IO on the CTA),
pictures (names of available sources), see (thing,as_of,must_show,moment,still_only), melody,
pace, emphasis, pause_after, direction. named lists only external subjects used, each with name,
is, searches, video_searches and must_show. Include a brief lead-in for the after before the
export plays. The voice leaves the recorded passages clear."""
    content = dict(idea=idea, receipts=rc, sheet=sheet, proof=proof, format=mode, style=card,
                   device=DEVICE_BRIEF.get(device_of(dict(idea_full=idea),ctx.args)), revision=notes)
    content = [dict(type="text", text=json.dumps(content, ensure_ascii=False))]
    if inventory["pictures"]:
        content.extend(VI.writer_content(inventory, still))
    def request(repair):
        prompt = content + [dict(type="text", text=repair)] if repair else content
        return ask(system, prompt, what="write recorded story", effort="high")
    sc, use = story_chain.write_segments(sheet, request)
    sc["recording_pictures_only"] = mode == "showcase" or not inventory["pictures"]
    roles = {seg["role"]: i for i, seg in enumerate(sc["segments"])}
    sc.update(format=mode, hook_line=sc["segments"][0]["say"], close_line=sc["segments"][-1]["say"],
              proof=dict(proof, before_after=roles["before"], after_after=roles["after"], typed=roles["typed"]))
    return sc, use


RERECORD = 2          # a recording the product itself failed is made again this many times before its idea gives way


def product_failed(rec):
    """The product produced no export and its own run recorded a failure (a failed job, an error reply): the stack
    failed, not the idea. Machine fields of the record only."""
    return bool(rec) and not rec.get("exports") and bool(rec.get("failures"))


def set_aside(ctx, rec, n):
    """A failed recording's folder is moved aside so the next one is made fresh (record() reuses a kept record.json)."""
    sid = rec.get("sid")
    if not sid:
        return
    src = os.path.join(ctx.tmp, "studio", "records", sid)
    if os.path.isdir(src):
        dst = f"{src}.failed{n}"
        shutil.rmtree(dst, ignore_errors=True)
        os.rename(src, dst)


def script(ctx, ideas_out):
    keep = os.path.join(ctx.run_dir, "script_state.json")
    stt = jload(keep, {}) or {}
    spend = []
    able = {s["id"]: s for s in able_cards(ctx)}
    # the first sentence of each idea, best idea first, three ideas at most a film: a result the check finds short gets
    # follow-ups typed in the same Studio session (record, answer_turns; FOLLOW_UPS), the way a customer asks again; an
    # idea whose last follow-up is still short gives way to the next idea
    tries = [(ii, idea, (idea.get("asks") or [""])[0], ((idea.get("must_see") or [[]])[0] or [])) for ii, idea in enumerate(ideas_out["ideas"][:3])]
    chosen = None
    for ii, idea, ask_text, must in tries:
        tag = k = f"i{ii}"
        old = stt.get(tag, {}).get("check") or {}
        if old.get("v") == CHECK_V and old.get("stack_v") == STACK_V and stt[tag].get("ok") is False:
            prior = jload(stt[tag].get("record") or "", {}) or {}
            if product_failed(prior) and int(stt[tag].get("product_failures") or 0) < RERECORD:
                # the product's own job failed (no export, a failure on record): an outage of the local stack, not the
                # idea's verdict. The recording is set aside and made again (20 Nov: every index of the stack failed on
                # a Whisper model missing offline; the run then refused the three ideas for good)
                n = int(stt[tag].get("product_failures") or 0) + 1
                ctx.log(f"recording {tag}: the product failed ({', '.join(map(str, prior.get('failures') or []))}); recording it again ({n})")
                set_aside(ctx, prior, n)
                stt[tag] = dict(product_failures=n)
                jsave(keep, stt)
                old = {}
            else:
                continue                 # checked by this check and not shown: never shown
        rec = record(ctx, idea, k, ask_text)
        asks = asks_of(rec) or [ask_text]
        ask_text = asks[0]
        if old.get("v") == CHECK_V and old.get("stack_v") == STACK_V:
            chk = old
        else:
            # the check the open session made of the result it ended on, else a check of the final export now; both read
            # every sentence typed in the session
            chk = turn_check(ctx, rec) or dict(export_check(ctx, rec, check_text(asks), must), stack_v=STACK_V)
            for name in sorted(os.listdir(live_dir(ctx, rec["sid"]))) if os.path.isdir(live_dir(ctx, rec["sid"])) else []:
                c = jload(os.path.join(live_dir(ctx, rec["sid"]), name), {}) if name.startswith("check_") else {}
                if isinstance(c, dict) and (c.get("export") or {}).get("sha256") != (chk.get("export") or {}).get("sha256"):
                    spend += c.get("spend") or []
        spend += chk.pop("spend", []) if "spend" in chk else []
        stt[tag] = dict(ok=chk["ok"], check=chk, record=os.path.join(ctx.tmp, "studio", "records", rec["sid"], "record.json"),
                        product_failures=int(stt.get(tag, {}).get("product_failures") or 0), asks=asks, turns=turns_of(ctx, rec))
        jsave(keep, stt)
        ctx.log(f"recording {tag}: {'shown' if chk['ok'] else 'not shown'} after {len(asks)} typed: {chk.get('why') or chk.get('looks')}")
        if chk["ok"]:
            if len(asks) > 1:       # the film says and shows every sentence; the studio photographs show only the first
                idea = dict(idea, follow_ups=asks[1:])
            chosen = (idea, ask_text, rec, chk, tag)
            break
    if not chosen:
        raise RuntimeError("no recording delivered what its sentence asked, visibly: " + "; ".join(
            f"{v['check'].get('why')}" for v in stt.values() if isinstance(v, dict) and v.get("check")))
    idea, ask_text, rec, chk, tag = chosen
    card = able.get(idea["style"]) or next(iter(able.values()))
    rc = receipts(rec, chk, ask_text)
    sibs = []
    if device_of(dict(idea_full=idea), ctx.args) == "triptych":
        sibs = siblings(ctx, idea, rec, tag, stt, keep)
        rc["siblings"] = [dict(typed=s["typed"], export=s["export_sha256"]) for s in sibs]
        if len(sibs) < 2:
            ctx.log(f"triptych: {len(sibs)} other export of this upload; the film opens on its own export and keeps the timeline")
    sheet, proof, use = proof_plan(ctx, idea, rec, rc, chk["export"])
    spend.append(dict(stage="sheet", **use))
    hook = None if len(sibs) >= 2 else hook_beat(rec, chk["export"], proof)
    inventory = dict(subjects=[], pictures={}, reports=[], spend=[])
    if format_of(dict(idea_full=idea), ctx.args) != "showcase":
        try:
            inventory = VI.prepare(ctx, idea, rc, ask, PX.pictures, still, studio=VI.studio_photos(rec))
        except (RuntimeError, OSError) as exc:
            ctx.log(f"Picture acquisition: {exc}. Writing from the upload, Studio and export.")
    spend.extend(inventory.get("spend", []))
    sc, use = write(ctx, idea, card, rc, sheet, proof, inventory, ctx.args.get("notes"))
    spend.append(use)
    anchor_show(sc)
    fill_display_phrases(sc)
    problems = script_problems(sc, ask_text, rc) + VI.script_problems(sc, inventory)
    claims, use = audit(sc, rc)
    spend.append(use)
    if problems or claims:
        sc, use = write(ctx, idea, card, rc, sheet, proof, inventory,
                        dict(draft=sc, findings=problems, claims=claims))
        spend.append(use)
        anchor_show(sc)
        fill_display_phrases(sc)
    for s in sc["segments"]:
        s["era"] = 2026
    window = [rec["source"]["t0"], rec["source"]["t1"]]
    return dict(title=sc.get("title") or idea.get("title"), style=registry_row(card), window=window, hook_line=sc.get("hook_line"),
                close_line=sc.get("close_line"), direction=f"{card['name']}: {card['look']}", idea=idea.get("title"), idea_full=idea,
                script=sc, receipts=rc, export_check={k: v for k, v in chk.items() if k not in ("export", "upload")},
                record=stt[tag]["record"], siblings=sibs, hook=hook,
                cta=dict(say=sc["segments"][-1]["say"], show=[show_text(x) for x in sc["segments"][-1].get("show") or []], needs_fulfilment=False),
                words=writer.words_of(sc), spend=spend)


def same_upload(rec, roots):
    """Real exports the product made from this exact upload (same sha256) in other synthetic Studio sessions under
    `roots`: [{sid, record, typed, export, export_sha256, edl}], vertical, finished, never this one."""
    sha, out, seen = rec["upload"]["sha256"], [], {rec.get("sid")}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for sid in sorted(os.listdir(root)):
            path = os.path.join(root, sid, "record.json")
            r = jload(path, {}) or {}
            if r.get("sid") in seen or (r.get("upload") or {}).get("sha256") != sha or r.get("state") != "done" or r.get("failures"):
                continue
            ex = next((e for e in r.get("exports") or [] if e.get("tag") == "final"), None)
            edl = os.path.join(root, sid, "edl", f"v{int((ex or {}).get('edl_version') or 0):03d}.json")
            if not ex or ex.get("h", 0) <= ex.get("w", 0) or not os.path.isfile(ex.get("file", "")) or not os.path.isfile(edl):
                continue
            seen.add(r.get("sid"))
            out.append(dict(sid=r.get("sid"), record=path, typed=" / ".join(a.get("text", "") for a in r.get("asks") or []),
                            export=ex["file"], export_sha256=ex.get("sha256"), edl=edl, dur=ex.get("dur")))
    return out


def siblings(ctx, idea, rec, tag, stt, keep):
    """Two more real exports of the same upload for the triptych: the run's own Studio sessions of the idea's other
    sentences (recorded on the same seconds), or, when the run reuses recordings, the reused folder's other films of
    the same upload. Each is a finished vertical export; the vision check is the film's own recording's."""
    roots = [os.path.join(ctx.tmp, "studio", "records"), os.environ.get("WF2_REUSE_RECORDINGS")]
    have = same_upload(rec, roots)
    if len(have) < 2 and not idea.get("recording"):
        for j, ask_text in enumerate((idea.get("asks") or [])[1:4]):
            if len(have) >= 2:
                break
            try:
                record(ctx, idea, f"{tag}t{j}", ask_text, follow_ups=0)    # a sibling export is shown as made
            except RuntimeError as e:          # a session that kept nothing: the next sentence, then the film without it
                ctx.log(f"triptych sentence {j + 1}: {str(e)[:200]}")
            have = same_upload(rec, roots)
    stt.setdefault(tag, {})["siblings"] = [h["record"] for h in have[:2]]
    jsave(keep, stt)
    return have[:2]


# The music step's brief for a showcase (chain.music_version hands it to the chooser, a model when one is available).
# The references under a speaker and a product screen (marketing-rebuild/SPEC.md section 4): a steady beat low under the
# voice and forward where nobody speaks; the 16 Nov showcase got an epic orchestral cue under a podcast ("Turkish series").
def pauses(path, lo, hi, min_s=0.15, below_db=28.0):
    """The speaker's pauses in a file's sound between lo and hi seconds, measured: [(start, end)] where the 20 ms
    energy stays below_db under the stretch's loud level (its 95th percentile) for min_s or longer."""
    import numpy as np
    r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{max(hi - lo, 0.1):.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "f32le", "-"], capture_output=True, timeout=120)
    x = np.frombuffer(r.stdout, np.float32)
    n = 320
    if len(x) < 10 * n:
        return []
    e = 10 * np.log10(np.maximum((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1), 1e-12))
    quiet = e < np.percentile(e, 95) - below_db
    out, k = [], 0
    while k < len(quiet):
        if quiet[k]:
            a = k
            while k < len(quiet) and quiet[k]:
                k += 1
            if (k - a) * 0.02 >= min_s:
                out.append((round(lo + a * 0.02, 3), round(lo + k * 0.02, 3)))
        k += 1
    return out


def proof_span(visible, window):
    """Measured picture receipts inside the chosen spoken comparison, in export seconds."""
    seen = [t for row in visible for t in row.get("seconds", []) if window[0] <= t <= window[1]]
    return (min(seen), max(seen)) if seen else None


def proof_push_at(span, hook):
    """Export second the split's push onto the proof starts from: inside the proof's whole stretch, with room for the
    push and a hold before the split ends (devices.split)."""
    if not span:
        return None
    f_room = (D.PROOF_PUSH_F + 8) / F.FPS
    return round(min(max(float(span[0]), float(hook[0]) + 6 / F.FPS), float(hook[1]) - f_room), 3)


def caption_example(edl, words, visible, body):
    """Find a checked line of burned captions in the kept speech, on the export clock.

    Older inspectors quote the line in their description. Only use a literal transcription
    containing a term styled or corrected by this export, at a frame the inspector saw.
    """
    import difflib

    caps = edl.get("captions") or {}
    if not caps or not words:
        return None
    spoken = D.program(edl)[0].kept_words(words)
    tokens = [F.norm_word(w["w"]) for w in spoken]
    terms = set(norm_words(" ".join(caps.get("emphasis_words") or [])))
    for change in caps.get("corrections") or []:
        if isinstance(change, dict):
            terms.update(norm_words(change.get("to") or ""))
    examples = []
    for row in visible:
        line = row.get("caption_line")
        printed = norm_words(line or row.get("how") or "")
        for seen in sorted(t for t in row.get("seconds") or [] if isinstance(t, (int, float)) and 0 <= t < body):
            # Match each observed occurrence locally, even when the speaker repeats the same words.
            near = [k for k, w in enumerate(spoken) if w["t0"] <= seen + 4. and w["t1"] >= seen - 4.]
            if not near:
                continue
            matches = difflib.SequenceMatcher(None, tokens[near[0]:near[-1]+1], printed, autojunk=False)
            candidates = [(near[0]+a, n) for a, _, n in matches.get_matching_blocks() if n]
            for i, n in candidates:
                if (line and n != len(printed)) or (not line and (n < 2 or not terms.intersection(tokens[i:i+n]))):
                    continue
                j = i + n - 1
                first, last = spoken[i]["t0"], spoken[j]["t1"]
                if not first - .12 <= seen <= last + .12:
                    continue
                # Include the uninterrupted lead into the quoted line ("the NVIDIA processors").
                left = i
                while left and first - spoken[left-1]["t0"] <= 1.5 and spoken[left]["t0"] - spoken[left-1]["t1"] < .28:
                    left -= 1
                start = spoken[left]["t0"]
                prev = spoken[left-1]["t1"] if left else 0.
                following = spoken[j+1]["t0"] if j+1 < len(spoken) else body
                context_start = round(max(0., start - min(.2, max(0., start-prev)/2)), 3)
                hook = [round(max(0., first - F.LEAD_F/F.FPS), 3),
                        round(min(body, max(last, seen) + min(.15, max(0., following-max(last, seen))/2)), 3)]
                examples.append(dict(hook=hook, context_start=context_start, seen_s=seen, caption_y=row.get("caption_y"),
                                     words=" ".join(w["w"] for w in spoken[i:j+1])))
    return min(examples, key=lambda e: e["seen_s"]) if examples else None


def ui_shots(rec):
    """The recording's photographs of the Studio (phone view): the typing, a photograph per word with the empty
    composer first, the sent screen, and the site's front page. None when the recording has none (made before 17 Nov,
    or in the desktop view)."""
    u = rec.get("ui") or {}
    d = u.get("dir") or os.path.join(os.path.dirname(rec["upload"]["file"]), "ui")
    if not os.path.isdir(d):
        d = os.path.join(os.path.dirname(rec["upload"]["file"]), "ui")
    files = sorted(os.listdir(d)) if os.path.isdir(d) else []
    typing = [f for f in files if f.startswith("type_00_") and f[8:10].isdigit()]
    sent = next((f for f in files if f == "type_00_sent.png"), None)
    if not typing or not sent:
        return None
    from PIL import Image
    with Image.open(os.path.join(d, typing[0])) as im:
        w, h = im.size
    out = dict(dir=d, w=w, h=h, typing=typing, sent=sent, view=u.get("view"), player=player_rect(os.path.join(d, typing[0]), rec["upload"]))
    out["bubble"] = sent_bubble(os.path.join(d, sent), out["player"])
    board = next((f for f in ("board_01.png", "board_00.png") if f in files), None)
    if board:
        with Image.open(os.path.join(d, board)) as im:
            out["board"] = dict(path=os.path.join(d, board), w=im.size[0], h=im.size[1])
    out["field"] = jload(os.path.join(d, "type_00_field.json"), {})
    if "site.png" in files:
        with Image.open(os.path.join(d, "site.png")) as im:
            out["site"] = dict(path=os.path.join(d, "site.png"), w=im.size[0], h=im.size[1])
    return out


def showcase_ui(rec):
    """Play every submitted instruction in order, from the recording's own typing and Send photographs."""
    ui = ui_shots(rec)
    asks = rec.get("asks") or []
    if not ui or len(asks) <= 1:
        return ui
    folder = ui["dir"]
    sequence = []
    for i, ask in enumerate(asks):
        prefix = f"type_{i:02d}_"
        shots = [prefix + f"{word:02d}.png" for word in range(len(ask["text"].split()) + 1)]
        sent = prefix + "sent.png"
        if not all(os.path.isfile(os.path.join(folder, name)) for name in [*shots, sent]):
            return None
        # Between two instructions, typed_line's separator holds the real preceding Send screen.
        sequence.extend(shots if i == 0 else shots[1:])
        if i + 1 < len(asks):
            sequence.append(sent)
    return dict(ui, typing=sequence, sent=sent)


def sent_bubble(png, player=None, composer_y=0.942):
    """[top, bottom] of the sent sentence's bubble on the sent photograph, as fractions of its height: the first run of
    rows below the Studio's player (and its controls) where a filled panel spans most of the width, measured against
    the screen's own ground. None when no such panel is found. The Studio frames it under the watermark with it
    (devices.studio_screen)."""
    import numpy as np
    from PIL import Image
    try:
        with Image.open(png) as im:
            g = np.asarray(im.convert("L"), np.float32)
    except (OSError, ValueError):
        return None
    h, w = g.shape
    ground = float(np.median(g[:, : max(w // 40, 2)]))        # the screen's own ground, at its left margin
    filled = (np.abs(g[:, w // 10: w - w // 10] - ground) > 4.0).mean(1)
    lo = int(((player or [0.0, 0.0])[1] + 0.035) * h) if player else int(0.04 * h)
    hi = int((composer_y - 0.06) * h)
    run, start = int(0.015 * h), None
    for y in range(max(lo, 0), max(hi, 0)):
        if filled[y] >= 0.45:
            start = y if start is None else start
        elif start is not None:
            if y - start >= run:
                return [round(start / h, 4), round(y / h, 4)]
            start = None
    return None


def player_rect(png, upload):
    """[top, bottom] of the Studio player's picture on a photograph of the screen, as fractions of its height: the
    first flat band (the controls' ground) below the top quarter is the picture's bottom, and the upload's own shape
    over the screen's width gives its top. None when no flat band is found."""
    import numpy as np
    from PIL import Image
    try:
        with Image.open(png) as im:
            g = np.asarray(im.convert("L"), np.float32)
        h, w = g.shape
        std = g[:, int(0.13 * w):int(0.87 * w)].std(1)
        flat = [r for r in range(int(0.22 * h), int(0.5 * h)) if std[r] < 1.0]
        if not flat:
            return None
        bottom = flat[0]
        top = bottom - w * float(upload["h"]) / float(upload["w"])
        return [round(max(top, 0) / h, 4), round(bottom / h, 4)] if top > 0 else None
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return None


def later_label(rec):
    """The recording's own time from the upload to its first result, as the push's label ("3 MIN"), or None."""
    s = rec.get("processing_s")
    try:
        s = float(s)
    except (TypeError, ValueError):
        return None
    return f"{int(round(s))} SEC" if s > 0 else None


def cut_times(path, lo=0.0, hi=None, jump=18.0, fps=8):
    """The picture's own cuts in a file (seconds): frames whose mean luma change from the frame before (64x36, 0-255)
    is `jump` or more, sampled at fps."""
    import numpy as np
    cmd = [PX.FF, "-v", "error", "-ss", f"{max(lo, 0.0):.3f}", "-i", path] + (["-t", f"{hi - lo:.3f}"] if hi else []) + \
        ["-vf", f"fps={fps},scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=300).stdout, np.uint8)
    if len(x) < 2 * 64 * 36:
        return []
    x = x[: len(x) // (64 * 36) * 64 * 36].reshape(-1, 36, 64).astype(np.float32)
    d = np.abs(np.diff(x, axis=0)).mean(axis=(1, 2))
    return [round(max(lo, 0.0) + (k + 1) / fps, 3) for k in np.nonzero(d >= jump)[0]]


HERO_S = (8.0, 11.0)           # the hero short's own stretch in the scale film: a short that plays whole in it, else the busiest
                               # stretch of the most-cut one (17 Nov: the board's ten reels had almost no cuts; the whole one won)


def scale_plan(rec):
    """The scale film's facts from a recording whose Shorts board was carried to films (studio.py finish_shorts): the
    vertical films with their bodies (end card off), the hero (the film whose picture changes most often: the most
    visible editing, measured) and its window (the busiest HERO_S stretch, entered and ended between words). None when
    the board gave fewer than four films."""
    films = []
    for f in ((rec.get("shorts") or {}).get("films") or []):
        if f.get("file") and os.path.exists(f["file"]) and f.get("h", 0) > f.get("w", 1):
            body = max(float(f["dur"]) - endcard_s(), 3.0)
            cuts = cut_times(f["file"], 0.0, body)
            films.append(dict(f, body=round(body, 3), cuts=cuts, rate=len(cuts) / body))
    if len(films) < 4:
        return None
    films.sort(key=lambda f: -f["rate"])
    lo, hi = HERO_S
    whole = [f for f in films if lo <= f["body"] <= hi + 1.0]
    if whole:                     # a film that plays complete in the hero's seconds: its title, its whole thought, its end
        hero = max(whole, key=lambda f: (f["rate"], f["body"]))
        t0, t1 = 0.0, hero["body"]
        films.remove(hero)
        films.insert(0, hero)
        return dict(films=films, hero=hero, window=[0.0, round(t1, 3)])
    hero = films[0]
    return dict(films=films, hero=hero, window=[round(t0, 3), round(t1, 3)])


def scale_supply(ctx, script, sup_dir, pk, rec, plan):
    """The scale film's pack: its grid's films, the hero and the voice track cut to devices.showcase_marks (the grid and
    the Studio carry their own sounds; the hero's own sound from where it grows out of the board)."""
    ui = showcase_ui(rec)
    typed = typed_line(script["receipts"])
    win = [float(x) for x in (W3.direction_of(ctx).get("export_window") or plan["window"])]
    mk = D.showcase_marks([0.0, D.SCALE_HOOK_S], win, len(typed.split()), lead_f=F.LEAD_F, board_f=D.BOARD_F if ui and ui.get("board") else 0)
    parts = []
    # the grid is heard as its outlined film (the hero, playing from its start in the grid): one voice under ten films
    for name, src, a, n_f in (("grid", plan["hero"]["file"], win[0] + mk["lead_f"] / F.FPS, mk["studio"][0] - mk["lead_f"]),
                              ("studio", None, 0.0, mk["export"][0] - mk["studio"][0]),
                              ("export", plan["hero"]["file"], win[0], mk["export_voice"][1] - mk["export_voice"][0]),
                              ("site", None, 0.0, mk["dur_f"] - mk["export_voice"][1])):
        p_ = os.path.join(sup_dir, f"voice_{name}.wav")
        n = n_f / F.FPS
        if src:
            subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{n:.3f}", "-vn", "-ac", "1", "-ar", "48000", "-af",
                            f"afade=t=in:d=0.03,afade=t=out:st={max(n - 0.06, 0):.3f}:d=0.06,apad=whole_dur={n:.3f}", p_], check=True, timeout=120)
        else:
            subprocess.run([PX.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{n:.3f}", p_], check=True, timeout=60)
        parts.append(p_)
    voice = os.path.join(sup_dir, "voice.wav")
    lst = os.path.join(sup_dir, "voice.txt")
    open(lst, "w").write("".join(f"file '{q}'\n" for q in parts))
    subprocess.run([PX.FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-ar", "48000", voice], check=True, timeout=120)
    words = os.path.join(sup_dir, "words.json")
    jsave(words, dict(voice="the recording", duration=round((mk["dur_f"] - mk["lead_f"]) / F.FPS, 3), words=[]))
    tiles = [dict(path=f["file"], w=int(f["w"]), h=int(f["h"]), fps=float(f.get("fps") or 30.0), dur=float(f["dur"]),
                  t0=(win[0] if f is plan["hero"] else 0.3), project_id=f.get("project_id"), title=f.get("title"))
             for f in plan["films"]]
    mins = int(round(float(rec["upload"]["dur"]) / 60.0))
    pack = dict(sid=ctx.sid, words=words, narration=voice, ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[], footage=[], voices=[],
                work=os.path.join(sup_dir, "finish"), fit=[],
                # the label over the push is the recording's own time from Send to the finished films (the board's turn and the editors)
                product=dict(paths=[plan["hero"]["file"]], window=win, marks=mk, ui=ui, variant="scale", tiles=tiles,
                             later=later_label(rec),
                             hero=tiles[0], sub=f"from a {mins}-min podcast", upload_at=0.0, cta_s=(mk["site"][1]-mk["site"][0])/F.FPS, cta_said=None))
    rec_ = dict(narration_s=None, voice=None, gate=None, pictures=0, subjects=0, missing=[], spend=[], product=pack["product"], footage=None,
                format="showcase", variant="scale", shorts=len(tiles), hero=dict(project_id=plan["hero"].get("project_id"),
                                                                                  cuts_per_10s=round(plan["hero"]["rate"] * 10, 1)))
    jsave(pk, dict(pack=pack, record=rec_))
    return rec_


def showcase_supply(ctx, script, sup_dir, pk):
    """Compose the recorded speaker and narrated instruction on their measured windows."""
    rec = json.load(open(script["record"]))
    plan = scale_plan(rec)
    if plan:                      # a Shorts board carried to films: the scale film
        return scale_supply(ctx, script, sup_dir, pk, rec, plan)
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    prod = os.path.join(sup_dir, "footage", "product")
    os.makedirs(prod, exist_ok=True)
    body = export_program_seconds(ex, edl)    # 27 Nov: a Pro export ends on the edit, not on a card (41.2 s cut to 35.6)
    cut(ex["file"], 0.0, body, os.path.join(prod, "export_body.mp4"))
    visible = (script.get("receipts") or {}).get("visible") or ()
    story = script.get("script") or {}
    proof = story.get("proof") or {}
    spans = proof.get("spans") or {}
    if not all(spans.get(key) for key in ("split", "before", "after", "stretch")):
        _, proof, _ = proof_plan(ctx, script["idea_full"], rec, script["receipts"], ex)
        spans = proof["spans"]
    hook, before, after, win = (spans[key] for key in ("split", "before", "after", "stretch"))
    words = rec.get("words") or hear_clock(ctx, rec["upload"]["file"], "upload")
    caption = caption_example(edl, words, visible, body)
    if caption and not hook[0] <= caption["seen_s"] < hook[1]:
        caption = None
    ui = showcase_ui(rec)
    typed = typed_line(script["receipts"])
    typed_segment = next((seg for seg in story.get("segments") or [] if seg.get("role") == "typed"), {})
    read = W3.narrate_script(ctx, dict(story, segments=[dict(typed_segment, say=typed_segment.get("say") or typed)]),
                             os.path.join(sup_dir, "studio_voice"))
    spoken = jload(read["words"])["words"]
    # The narrator may introduce the instruction or speak a printed figure as several words.
    # Match the photographed composer tokens against the take with its authored print map.
    from difflib import SequenceMatcher
    take = F.Clock(read["words"], dict(segments=[typed_segment]))
    written = F.written_addresses(take, dict(segments=[typed_segment]))
    timed_tokens = [(F.norm_word(token), max(0, round(w["t0"]*F.FPS)-F.LEAD_F))
                    for w in written.words for token in w["w"].split()]
    target = [F.norm_word(word) for word in typed.split()]
    marks = {i+k: timed_tokens[j+k][1] for i,j,n in SequenceMatcher(
        None, target, [w for w,_ in timed_tokens], autojunk=False).get_matching_blocks() for k in range(n)}
    end_f = round(read["duration"]*F.FPS)
    offsets = [end_f] * (len(target)+1)
    for i in reversed(range(len(target))):
        offsets[i] = marks.get(i, offsets[i+1])
    mk = D.showcase_marks(hook, win, len(typed.split()), lead_f=F.LEAD_F, typing_frames=offsets,
                          before=before, after=after)
    voice = os.path.join(sup_dir, "voice.wav")
    lead = F.LEAD_F / F.FPS
    parts = []

    def piece(name, src, a, n_f):
        if n_f <= 0:
            return
        p = os.path.join(sup_dir, f"voice_{name}.wav")
        n = n_f / F.FPS
        if src:
            subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{n:.3f}", "-vn", "-ac", "1", "-ar", "48000",
                            "-af", f"afade=t=in:d=0.03,afade=t=out:st={max(n - 0.06, 0):.3f}:d=0.06,apad=whole_dur={n:.3f}", p],
                           check=True, timeout=120)
        else:
            subprocess.run([PX.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{n:.3f}", p], check=True, timeout=60)
        parts.append(p)

    piece("split", ex["file"], hook[0] + lead, mk["split"][1] - mk["lead_f"])
    if before:
        piece("before", rec["upload"]["file"], before[0], mk["before"][1] - mk["before"][0])
    piece("studio_in", None, 0., mk["typing"][0] - mk["studio"][0])
    piece("typed", read["wav"], 0., offsets[-1])
    piece("send", None, 0., mk["studio"][1] - mk["typing"][0] - offsets[-1])
    if after:
        piece("after", ex["file"], after[0], mk["after"][1] - mk["after"][0])
    piece("export", ex["file"], win[0], mk["stretch"][1] - mk["stretch"][0])
    piece("site", None, 0., mk["site"][1] - mk["site"][0])
    lst = os.path.join(sup_dir, "voice.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    subprocess.run([PX.FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-ar", "48000", voice], check=True, timeout=120)
    words_on_upload = words
    words = os.path.join(sup_dir, "words.json")
    upload_words_timed = words_on_upload
    cursor = 0.
    export_words_timed = []
    for lo, hi in edl.get("keep") or [[0., body]]:
        for row in upload_words_timed:
            if lo <= row["t0"] < hi:
                export_words_timed.append(dict(w=row["w"], t0=cursor+row["t0"]-lo, t1=cursor+min(row["t1"], hi)-lo))
        cursor += hi-lo
    turns, clock_rows, voices = [], [], []
    who = (script.get("idea_full") or {}).get("speaker") or "speaker"
    for kind, window, timeline in (("split",hook,export_words_timed), ("before",before,upload_words_timed),
                                   ("typing",None,spoken), ("after",after,export_words_timed), ("stretch",win,export_words_timed)):
        a, b = mk[kind]
        if b <= a:
            continue
        start = (a-mk["lead_f"])/F.FPS
        if kind == "typing":
            rows = [dict(w=row["w"], start=start+row["start"], end=start+row["end"]) for row in timeline]
        elif window:
            lo, hi = window
            rows = [dict(w=row["w"], start=max(0., start+row["t0"]-lo), end=start+min(hi,row["t1"])-lo)
                    for row in timeline if lo <= row["t0"] < hi and start+row["t1"]-lo > 0.]
            voices.append(dict(person=who, role=kind, start=start, end=(b-mk["lead_f"])/F.FPS))
        else:
            continue
        clock_rows.extend(dict(row, seg=len(turns)) for row in rows)
        turns.append(dict(say=" ".join(row["w"] for row in rows), role=kind,
                          print=typed_segment.get("print", {}) if kind=="typing" else {}))
    jsave(words, dict(voice=read["voice"], duration=(mk["dur_f"] - mk["lead_f"]) / F.FPS, words=clock_rows))
    pack = dict(sid=ctx.sid, words=words, narration=voice, ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[], footage=[],
                voices=[], screen_voices=voices, programme_segments=turns, work=os.path.join(sup_dir, "finish"), fit=[],
                product=dict(paths=[os.path.join(prod, "export_body.mp4")], window=win, hook=hook, marks=mk, ui=ui, before=before, after=after, later=later_label(rec) if len(rec.get("asks") or []) <= 1 else None,
                             upload_at=round(D.src_at(D.program(edl)[0], hook[0]), 3), cta_s=(mk["site"][1]-mk["site"][0])/F.FPS, cta_said=None,
                             caption_proof=caption, proof_at=proof_push_at(proof_span(visible, hook), hook)))
    rec_ = dict(narration_s=None, voice=None, gate=None, pictures=0, subjects=0, missing=[], spend=[], product=pack["product"], footage=None,
                format="showcase", studio_ui=bool(ui), site=bool(ui and ui.get("site")))
    jsave(pk, dict(pack=pack, record=rec_))
    return rec_




# ----------------------------------------------------------------- supply

def cut(src, t0, t1, out, audio=True):
    cmd = [PX.FF, "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}", "-map", "0:v:0"]
    cmd += (["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"] if audio else ["-an"])
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p", "-r", "30", out]
    subprocess.run(cmd, check=True, timeout=300)
    if audio:
        subprocess.run([PX.FF, "-v", "error", "-y", "-i", out, "-vn", "-ac", "1", "-ar", "48000", out[:-4] + ".wav"], check=True, timeout=120)


def quiet_gap(path, start, end):
    import numpy as np
    rate = 16000
    middle = (start + end) / 2
    if end-start < .002:
        return middle
    result = subprocess.run([PX.FF, "-v", "error", "-ss", str(start), "-i", path, "-t", str(end-start),
                             "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, timeout=60)
    samples = np.frombuffer(result.stdout, np.float32)
    if len(samples) < 2:
        return middle
    width = min(len(samples), max(2, min(rate//100, len(samples)//4)))
    power = np.convolve(samples**2, np.ones(width)/width, mode="valid")
    first = int(np.argmin(power))
    window = samples[first:first+width]
    crossings = np.flatnonzero(np.signbit(window[1:]) != np.signbit(window[:-1]))
    offset = int(min(crossings, key=lambda k: abs(k-width/2))) if len(crossings) else int(np.argmin(abs(window)))
    return min(end, max(start, round(start+(first+offset)/rate, 4)))


def endcard_s():
    """Seconds at the end of every export that are Valmera's end card (worker/brand/endcard.mp4), with a settle margin:
    the film shows the export's own content, never its end card (the shell adds the film's own)."""
    try:
        p = os.path.join(F.scene_worker() if hasattr(F, "scene_worker") else "", "brand", "endcard.mp4")
        r = subprocess.run([PX.FF.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) + 0.6
    except (OSError, ValueError, subprocess.SubprocessError):
        return 5.6


def export_program_seconds(ex, edl):
    """Use the authored programme, whether this particular export appended a brand card or ended on the edit."""
    if edl and edl.get("keep"):
        try:
            duration = D.program(edl)[1]
            if duration > 0:
                return min(float(ex["dur"]), duration)
        except (ValueError, KeyError, TypeError, ImportError):
            pass
    return float(ex["dur"])


def recorded_titles(script):
    """Bind authored display anchors to identical titles in the checked export, using its EDL and viewed seconds."""
    receipt = script.get("receipts") or {}
    record = jload(script.get("record") or "", {}) or {}
    exports = record.get("exports") or []
    ex = next((e for e in reversed(exports) if e.get("sha256") == (receipt.get("export") or {}).get("sha256")
               and e.get("file") and os.path.isfile(e["file"])), None)
    if not ex or not ex.get("sha256"):
        return {}
    viewed = [t for row in receipt.get("visible") or [] for t in row.get("seconds") or [] if isinstance(t, (float, int))]
    titles = {}
    for title in (export_edl(ex) or {}).get("texts") or []:
        text = " ".join(str(title.get("text") or "").split()).casefold()
        a, b = title.get("start"), title.get("end")
        if text and isinstance(a, (int, float)) and isinstance(b, (int, float)) and any(a <= t < b for t in viewed):
            titles.setdefault(text, (a, min(b, ex["dur"])))
    out = {}
    for i, seg in enumerate(script["script"]["segments"]):
        if "THE EXPORT" not in (seg.get("pictures") or []):
            continue
        for item in seg.get("show") or []:
            if not isinstance(item, dict) or type(item.get("word_i")) != int:
                continue
            text = str(item.get("text") or "")
            span = titles.get(" ".join(text.split()).casefold())
            if span and span[1] > span[0]:
                out.setdefault(i, []).append(dict(export=ex, text=text, word_i=item["word_i"], src=span))
    return out


def lay_recorded_titles(l1, clock, titles, reserved):
    """On the narrated title cue, play its original frames including the Studio's lettering."""
    rows = []
    for line, titles_on_line in titles.items():
        words = clock.seg_words(line)
        anchors = sorted((t for t in titles_on_line if 0 <= t["word_i"] < len(words)), key=lambda t: t["word_i"])
        for j, t in enumerate(anchors):
            on = clock.f(words[t["word_i"]]["t0"])
            until = clock.f(words[anchors[j + 1]["word_i"]]["t0"] if j + 1 < len(anchors) else words[-1]["t1"])
            ex = t["export"]
            fps = ex.get("fps") or F.FPS
            start = math.ceil(t["src"][0] * fps - 1e-6) / fps
            end = min(until, on + math.floor((t["src"][1] - start) * F.FPS + 1e-6))
            for a, b in _minus(on, end, reserved):
                if a != on:
                    continue  # the recording at the addressed word already performs this title
                src = [round(start + (f - on) / F.FPS, 5) for f in (a, b)]
                layer = D.Layer(l1, {}, f"studio-title-{a}")
                key = layer.P.video("studio-title-export", dict(ex, path=ex["file"], fps=fps))
                layer.P.items.append(dict(id="recording", k="shot", f=[a, b], pic=key, crop=[0, 0, 1, 1], src=src))
                layer.lay([a, b])
                rows.append(dict(device="Studio title", frames=[a, b], src=src, title=t["text"], line=line, path=ex["file"]))
    return rows


def product_crop(record_path, rec, t):
    """The export's crop of the upload at upload second t, [x0, y0, x1, y1] in upload fractions, read from the final
    export's own edit list (frame mode crop: the ratio's largest window, centred on the focus, the focus track
    overriding it in its seconds). None when the product did not crop (a fit, a canvas) or the list is missing."""
    ex = next((e for e in rec.get("exports") or [] if e.get("tag") == "final"), (rec.get("exports") or [{}])[-1])
    try:
        edl = json.load(open(os.path.join(os.path.dirname(record_path), "edl", f"v{int(ex['edl_version']):03d}.json")))
        fr = edl.get("frame") or {}
        if fr.get("mode") != "crop":
            return None
        a_w, a_h = [float(x) for x in str(fr.get("ratio") or "9:16").split(":")]
        pw, ph = float(rec["upload"]["w"]), float(rec["upload"]["h"])
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None
    aspect, pa = a_w / a_h, pw / ph
    cw, ch = (aspect / pa, 1.0) if pa > aspect else (1.0, pa / aspect)
    fx = float(fr.get("focus_x") if fr.get("focus_x") is not None else 0.5)
    fy = float(fr.get("focus_y") if fr.get("focus_y") is not None else 0.5)
    for k in fr.get("focus_track") or []:
        if float(k.get("t0", 0)) <= t < float(k.get("t1", 0)) and (k.get("mode") or "crop") == "crop":
            fx = float(k.get("x", fx))
            fy = float(k.get("y", fy))
    x0 = min(max(fx - cw / 2, 0.0), 1.0 - cw)
    y0 = min(max(fy - ch / 2, 0.0), 1.0 - ch)
    return [round(x0, 4), round(y0, 4), round(x0 + cw, 4), round(y0 + ch, 4)]


def blend_pauses(audio, rate, words, boundaries):
    """Blend the two sides of measured joins within their inter-word silence; preserve the word clock."""
    import numpy as np
    changed = []
    for boundary in boundaries:
        if not boundary.get("artefacts"):
            continue
        t = boundary["t"]
        if any(w["start"] < t < w["end"] for w in words):
            continue
        left = max((w["end"] for w in words if w["end"] <= t), default=t)
        right = min((w["start"] for w in words if w["start"] >= t), default=t)
        mid = int(round(t*rate))
        radius = min(int(.006*rate), int((t-left)*rate/3), int((right-t)*rate/3))
        if radius < 2:
            continue
        probe = audio[mid-radius:mid+radius].mean(axis=1)
        crossings = np.flatnonzero(np.signbit(probe[1:]) != np.signbit(probe[:-1]))
        k = min(crossings, key=lambda x: abs(x-radius)) if len(crossings) else np.argmin(abs(probe))
        center = mid-radius+int(k)
        half = min(int(.018*rate), center-int(np.ceil(left*rate)), int(right*rate)-center)
        if half < 2:
            continue
        before, after = audio[center-half:center].copy(), audio[center:center+half].copy()
        axis = np.linspace(0,half-1,2*half)
        phase = (.5-.5*np.cos(np.linspace(0,np.pi,2*half)))
        for channel in range(audio.shape[1]):
            a = np.interp(axis, np.arange(half), before[:,channel])
            b = np.interp(axis, np.arange(half), after[:,channel])
            audio[center-half:center+half,channel] = (1-phase)*a+phase*b
        changed.append(dict(second=round(center/rate,5), crossfade_s=round(2*half/rate,5)))
    return changed


def repair_narration_joins(ctx, narration, env):
    """Re-take pitch and level mismatches, then blend remaining waveform seams inside the measured pauses."""
    import fcntl
    import tempfile
    import wave
    import numpy as np
    from pathlib import Path
    parent = Path(narration["wav"]).parent
    check = jload(parent/"check.json", {})
    if not (check.get("joins") or {}).get("with_artefact"):
        return narration
    out = parent/"joined"
    if not (out/"check.json").is_file():
        with tempfile.TemporaryDirectory(dir=parent) as temporary, open(PX.NARRATE_LOCK, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            dest = Path(temporary)/"master"
            dest.mkdir()
            cmd = ["zsh", HEAVY, f"wf2-voice-joins-{ctx.run_id}", PX.NARRATE_PY]
            subprocess.run([*cmd, os.path.join(STORIES, "voice.py"), "match-joins", str(parent), str(dest)],
                           cwd=os.path.dirname(PX.NARRATE), env=env, stdout=subprocess.DEVNULL)
            native = jload(dest/"check.json", {})
            use = dest if native else parent
            remaining = (native or check)["joins"]
            if remaining["with_artefact"]:
                with wave.open(str(use/"narration.wav"), "rb") as wav:
                    rate, channels = wav.getframerate(), wav.getnchannels()
                raw = subprocess.check_output([PX.FF,"-v","error","-i",str(use/"narration.wav"),"-f","f32le","-"])
                audio = np.frombuffer(raw, dtype="<f4").reshape(-1, channels).copy()
                changes = blend_pauses(audio, rate, jload(use/"words.json")["words"], remaining["boundaries"])
                if changes:
                    if use != dest:
                        for name in ("words.json", "render.json", "narration_script.json"):
                            shutil.copy2(use/name, dest/name)
                    subprocess.run([PX.FF,"-v","error","-y","-f","f32le","-ar",str(rate),"-ac",str(channels),
                                    "-i","pipe:0","-c:a","pcm_s24le",str(dest/"narration.wav")],
                                   input=audio.tobytes(), check=True)
                    jsave(str(dest/"crossfades.json"), changes)
                    subprocess.run([*cmd, PX.NARRATE, "check", str(dest), "--band", "03"],
                                   cwd=os.path.dirname(PX.NARRATE), env=env, check=True, stdout=subprocess.DEVNULL)
            if (dest/"check.json").is_file():
                os.replace(dest,out)
    repaired = jload(out/"check.json", {})
    if not repaired:
        return narration
    words = str(out/"words.json")
    return dict(narration, words=words, wav=str(out/"narration.wav"),
                duration=jload(words)["duration"], gate=dict(narration["gate"],
                ok=repaired["gate"]["pass"], checks=repaired["gate"], wer=repaired["wer"]["wer"]))


def product_clips(script, out):
    """Cut the checked upload and export at their independently measured sentence boundaries."""
    rec = json.load(open(script["record"]))
    chk = script["export_check"]
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    up = rec["upload"]
    os.makedirs(out, exist_ok=True)
    body = export_program_seconds(ex, export_edl(ex))
    spans = script["script"]["proof"].get("spans")
    if not spans:
        # Resume an already written legacy tutorial at its recorded proof, on that record's clocks.
        spans = dict(before=chk["before"], after=chk["after"])
    a, b = spans["after"]
    ba, bb = spans["before"]
    cut(up["file"], ba, bb, os.path.join(out, "before.mp4"))
    cut(ex["file"], a, b, os.path.join(out, "after.mp4"))
    hook = None
    if script.get("hook"):
        hook = spans.get("split", script["hook"]["t"])
        cut(ex["file"], *hook, os.path.join(out, "hook.mp4"))
    # the whole export, silent: a line the cut has no picture for plays the real result, never a type page
    cut(ex["file"], 0.0, body, os.path.join(out, "export_body.mp4"), audio=False)
    return dict(before=dict(src=up["file"], t=[ba, bb], sha256=up["sha256"], crop=product_crop(script["record"], rec, (ba + bb) / 2)),
                after=dict(src=ex["file"], t=[a, b], sha256=ex["sha256"]),
                hook=dict(src=ex["file"], t=hook, at=float(script["hook"]["t"][0]), landing=script["hook"].get("landing")) if hook else None,
                stretch=dict(src=ex["file"], t=spans["stretch"], sha256=ex["sha256"]) if "stretch" in spans else None,
                body=dict(src=ex["file"], t=[0.0, round(body, 3)]))


def export_landings(script):
    """What the final export's own edit lands over its speaker, on the export's seconds: every picture (overlays and
    b-roll) and every designed title of its EDL, [{t, kind: picture|title}]. [] when the edit list cannot be read."""
    try:
        rec = json.load(open(script["record"]))
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return []
    rows = [dict(t=round(float(o["start"]), 3), kind="picture") for o in D.MF.pictures_of(edl)]
    rows += [dict(t=round(float(o["start"]), 3), kind="title") for o in D.MF.titles_of(edl)]
    return sorted(rows, key=lambda r: (r["t"], r["kind"]))


def landings_in(lands, t0, t1):
    """The export's landings inside one cut of it [t0, t1) on that cut's own seconds (the sound design's events)."""
    return [dict(r, t=round(float(r["t"]) - float(t0), 3)) for r in lands or [] if float(t0) <= float(r["t"]) < float(t1)]


def splice(ctx, narr_dir, foot_dir, voices):
    """The recording cut into the narration between two lines (_shell/narrate/realvoice.py), at the narrator's level."""
    vp = os.path.join(ctx.run_dir, "splice_voices.json")
    jsave(vp, voices)
    code = ("import json,sys; sys.path.insert(0, sys.argv[4]); import realvoice; "
            "print(json.dumps(realvoice.splice(sys.argv[1], json.load(open(sys.argv[3])), sys.argv[2])))")
    nd = os.path.dirname(PX.NARRATE)
    r = subprocess.run([PX.NARRATE_PY, "-c", code, narr_dir, foot_dir, vp, nd], cwd=nd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"splice failed: {r.stderr[-600:]}")
    placed = json.loads(r.stdout.strip().splitlines()[-1])
    jsave(os.path.join(narr_dir, "footage_voices.json"), placed)
    lv = subprocess.run([PX.NARRATE_PY, os.path.join(STORIES, "voice.py"), "level-voices", narr_dir], capture_output=True, text=True, timeout=600)
    ctx.log(f"cut-ins levelled: {((lv.stdout or lv.stderr).strip().splitlines() or [''])[-1][:200]}")
    return placed


def photo_briefs(ctx, named):
    """Image libraries need subject queries, not the documentary video's search sentence."""
    path = os.path.join(ctx.supply, "photo_plan.json")
    cached = jload(path, {}) or {}
    if cached.get("subjects") == named:
        return cached["briefs"], []
    result, use = ask(
        "Translate these exact visual subjects into image-library search queries for Wikimedia Commons and Openverse. "
        "Use two or three concrete queries per subject, two to four words each. Preserve the actual object or action; "
        "never replace the requested thing with a generic mood or change its identity. Do not use video-platform terms "
        "such as tutorial, stock footage or 4K. Do not claim these searches guarantee a match: the existing must_show "
        "and per-line frame checks remain binding. Return JSON {subjects:[{name:exact original name, searches:[...]}]}.",
        json.dumps(named, ensure_ascii=False), what="photo search plan", effort="low", max_tokens=3000)
    by_name = {r.get("name"): r.get("searches") for r in result.get("subjects", []) if isinstance(r, dict)}
    briefs = []
    for subject in named:
        queries = by_name.get(subject["name"])
        if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
            raise RuntimeError(f"photo search plan omitted {subject['name']}")
        briefs.append(dict(subject, searches=queries[:3]))
    jsave(path, dict(subjects=named, briefs=briefs))
    return briefs, [use]


def recording_source(script, role):
    """Carry the authenticated recording's identity into the ordinary visual matcher."""
    receipts = script["receipts"]
    title = "THE UPLOAD — original clip uploaded to Valmera" if role == "upload" else "THE EXPORT — actual Valmera export"
    return dict(url="valmera studio recording", title=title,
                description=f"Recorded synthetic Studio session. {role} SHA256: {receipts[role]['sha256']}. "
                            f"Exact submitted request: {typed_line(receipts)}")


SHOT_MIN_S, SHOT_MAX_S = 0.45, 3.0     # the recording's own shots: never shorter (a flicker), never longer (one picture a line)
PRODUCT_WINDOWS_V = 2                  # 20 Nov: the recording as its own shots, ranked per line (v1: 1 s slices on a grid)


def shot_list(dur, hard=(), soft=(), busy=(), min_s=SHOT_MIN_S, max_s=SHOT_MAX_S):
    """[(a, b)] a recording's shots on its own clock: [0, dur] outside `busy` (seconds shown whole elsewhere: the after,
    the opening beat, the before), split at its `hard` events (an edit-list join, a picture or a title landing or leaving) and
    at its `soft` ones (a caption group starting, a measured picture change) that lie min_s or more from every edge
    already there; a piece still shorter than min_s joins its neighbour, one longer than max_s is split evenly."""
    free, at = [], 0.0
    for a, b in sorted((max(float(a), 0.0), min(float(b), dur)) for a, b in busy):
        if a > at:
            free.append((at, a))
        at = max(at, b)
    if dur > at:
        free.append((at, dur))
    out = []
    for a0, b0 in free:
        if b0 - a0 < min_s:
            continue
        edges = sorted({a0, b0} | {round(float(t), 3) for t in hard if a0 + 1e-3 < float(t) < b0 - 1e-3})
        for t in sorted(round(float(t), 3) for t in soft):
            if a0 < t < b0 and all(abs(t - e) >= min_s for e in edges):
                edges = sorted(edges + [t])
        pieces = []
        for a, b in zip(edges, edges[1:]):
            if pieces and (b - a < min_s or pieces[-1][1] - pieces[-1][0] < min_s):
                pieces[-1] = (pieces[-1][0], b)          # a piece too short joins the one before it
            else:
                pieces.append((a, b))
        for a, b in pieces:
            if b - a < min_s:
                continue
            k = max(1, int(math.ceil((b - a) / max_s - 1e-9)))
            step = (b - a) / k
            out += [(round(a + j * step, 3), round(a + (j + 1) * step, 3)) for j in range(k)]
    return out


def export_events(record_path, rec, ex, body):
    """The export's own events on its clock, (hard, soft) seconds. Hard: where its kept ranges join (the edit list's
    programme), where its pictures and titles land and leave. Soft: where its caption groups start (caption_spans) and
    where its picture measurably changes (cut_times). Read from the recording's files; what cannot be read is left out."""
    hard, soft = [], []
    try:
        edl = json.load(open(os.path.join(os.path.dirname(record_path), "edl", f"v{int(ex['edl_version']):03d}.json")))
        tl, _ = D.program(edl)
        hard += [float(x) for x in list(tl.offsets)[1:]]
        for o in D.MF.pictures_of(edl) + D.MF.titles_of(edl):
            hard += [float(o["start"]), float(o["start"]) + float(o["duration_s"])]
        soft += [a for a, _ in caption_spans(rec, edl)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        pass
    soft += cut_times(ex["file"], 0.0, body)
    return hard, soft


def upload_events(rec):
    """The upload's own events on its clock, (hard, soft): its measured picture changes and where each group of three
    of its caption words starts (the source's captions on the upload's seconds)."""
    soft = list(cut_times(rec["upload"]["file"], 0.0, float(rec["upload"]["dur"])))
    src = next((s for s in sources() if s["file"] == (rec.get("source") or {}).get("file")), None)
    if src:
        t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
        ws = [t - t0 for t, _ in json3_words(src["captions"]) if t0 <= t <= t1]
        soft += ws[::3]
    return [], soft


def rank_shots(lines, candidates, result):
    """{line: [candidate ids, best first]} from the model's ranking: known lines and candidates only, each id once a line."""
    by_line = {ln["i"] for ln in lines}
    out = {}
    for row in (result.get("lines") if isinstance(result, dict) else None) or []:
        try:
            i = int(row.get("line"))
        except (AttributeError, TypeError, ValueError):
            continue
        if i not in by_line:
            continue
        ids = []
        for k in row.get("shots") or []:
            try:
                k = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= k < len(candidates) and k not in ids:
                ids.append(k)
        out[i] = ids
    return out


def owed_windows(lines, candidates, ranked, used, demand):
    """Each line that names THE UPLOAD or THE EXPORT takes the recording's shots the model ranked best for its words, as
    its own takes, each shot to one line only: round by round one shot a line, the lines with the fewest ranked shots
    left first, up to the line's demand. A line left with none still shows its recording: the unused shot of its named
    source farthest from those already given (20 Nov: this rule, alone and on a 1 s grid, gave each line one slice and
    the cut looped it)."""
    out, by_i = [], {ln["i"]: ln for ln in lines}
    want = {i: max(1, int(demand.get(i, demand.get(str(i), 1)) or 1)) for i in by_i}

    def left(i):
        return [k for k in ranked.get(i) or [] if k not in used and candidates[k]["source"] in by_i[i]["sources"]]
    more = True
    while more:
        more = False
        for i in sorted(by_i, key=lambda i: (len(left(i)), i)):
            if sum(1 for o in out if o["line"] == i) >= want[i]:
                continue
            ks = left(i)
            if ks:
                used.add(ks[0])
                out.append(dict(candidates[ks[0]], line=i, why="ranked for this line's words"))
                more = True
    for i, ln in by_i.items():
        if any(o["line"] == i for o in out):
            continue
        free = [c for c in candidates if c["source"] in ln["sources"] and c["id"] not in used]
        if free:
            shown = [o["center"] for o in out if o["source"] in ln["sources"]]
            c = max(free, key=lambda c: (min((abs(c["center"] - t) for t in shown), default=1e9), -c["center"]))
            used.add(c["id"])
            out.append(dict(c, line=i, why="the line names this recording; its shot owed to it"))
    return out


def product_windows(ctx, script, sources, demand=None):
    """The recording's own shots chosen for the words by a model that sees them. Each source (THE UPLOAD, THE EXPORT:
    {name: dict(path, dur, busy, hard, soft)}) is offered as its shots (shot_list: split at its edit list's events and
    its measured picture changes, never a 1 s grid), one frame each; the model ranks, for every line, the shots whose
    picture carries that line's words, best first, as many as the line's shot demand ({line: shots}), with no cap. The
    lines that name the recording take their best-ranked shots (owed_windows); every line's ranking is returned for the
    cut's proof fill. ([windows], {line: [shot ids]}, [candidates], spend); the ranking is None when the call went
    unanswered."""
    plan_path = os.path.join(ctx.supply, "product_windows.json")
    old = jload(plan_path)
    if old and old.get("v") == PRODUCT_WINDOWS_V:
        return old["windows"], {int(k): v for k, v in (old.get("ranked") or {}).items()}, old.get("candidates") or [], old.get("spend", [])
    segs = script["script"]["segments"]
    demand = demand or {}
    lines = [dict(i=i, say=seg["say"], sources=[n for n in seg.get("pictures", []) if n in sources],
                  demand=max(1, int(demand.get(i, demand.get(str(i), 1)) or 1)))
             for i, seg in enumerate(segs)]
    candidates = []
    frames_ = []
    for name, src in sources.items():
        for a, b in shot_list(float(src["dur"]), src.get("hard") or (), src.get("soft") or (), src.get("busy") or ()):
            t = round((a + b) / 2, 3)
            frame = still(src["path"], t, w=540)
            if not frame:
                continue
            c = dict(id=len(candidates), source=name, center=t, start=a, end=b)
            candidates.append(c)
            frames_ += [dict(type="text", text=f"Shot {c['id']}: {name} {a:.2f}-{b:.2f} s"), img(frame)]
    if not candidates:
        return [], {}, [], []
    content = [dict(type="text", text=json.dumps(dict(lines=lines, receipts=script["receipts"]), ensure_ascii=False))] + frames_
    content.append(dict(type="text", text='Return JSON {"lines": [{"line": index, "shots": [shot ids, best first]}]} with every line.'))
    try:
        result, use = ask_shots(content)
    except Exception as e:  # noqa: BLE001 - an outage after the retries: each recording line still gets its own shot, unranked
        log = getattr(ctx, "log", None)
        if log:
            log(f"product shots not ranked ({repr(e)[:200]}); each recording line gets its farthest unused shot")
        out = owed_windows([ln for ln in lines if ln["sources"]], candidates, {}, set(), {ln["i"]: ln["demand"] for ln in lines})
        return out, None, candidates, []          # None: not ranked (the fill plays unshown shots), not 'ranked nothing
    ranked = rank_shots(lines, candidates, result)
    out = owed_windows([ln for ln in lines if ln["sources"]], candidates, ranked, set(), {ln["i"]: ln["demand"] for ln in lines})
    jsave(plan_path, dict(v=PRODUCT_WINDOWS_V, windows=out, ranked={str(k): v for k, v in ranked.items()}, candidates=candidates, spend=[use]))
    return out, ranked, candidates, [use]


def ask_shots(content):
    return ask(
        "You choose the real product recording's pictures for a marketing film's narration. You see the recording (the "
        "clip as uploaded, THE UPLOAD, and the export Valmera returned, THE EXPORT) cut at its own events into shots, one "
        "labelled frame each, and the film's lines with their words and how many shots each line's cut draws (demand). "
        "For EVERY line, rank the shots whose visible picture carries that line's words, best first, up to its demand: "
        "the picture the export laid over what the speaker names for the line that names it, its captions for a line about "
        "captions, the speaker for a line about him or his clip, the raw upload for a line about the raw clip. Leave out a "
        "shot whose picture does not carry the words; a line the recording cannot carry gets an empty list. Shots that "
        "look the same (the same framing, no new picture or caption) are one picture: rank the one that fits best. Prefer "
        "a different shot for each line where one fits. These are the product's real files; never an interface. The "
        "real-voice before and after are reserved already and not offered.",
        content, what="product source windows", model=CHECK_MODEL, effort="low", max_tokens=4000)


def footage_subjects(ctx, sc, lines, context=None):
    """Footage for the lines whose only picture is the recording (20 Nov: seven of nine lines named only THE UPLOAD or THE
    EXPORT, the footage search took one line and the cut looped the export's first seconds): a model reads each such
    line's words beside the film's named subjects and gives the line up to two subjects a camera filmed that show what its
    words name, a named one or a new one with its video searches; none where its words name nothing visible. Cached in
    the supply folder. An unanswered call leaves the lines to the recording and the run goes on. ({line: [named rows]},
    spend)."""
    path = os.path.join(ctx.supply, "footage_subjects.json")
    old = jload(path, {}) or {}
    if old.get("context") == context and set(lines).issubset(old.get("lines") or []):
        return {int(k): v for k, v in (old.get("subjects") or {}).items() if int(k) in lines}, []
    have = {n.get("name"): n for n in sc.get("named") or [] if n.get("name")}
    if (context or {}).get("studio_frames"):
        have["THE STUDIO"] = dict(name="THE STUDIO",must_show="The recorded composer, result or site", **{"is":"product"})
    content = json.dumps(dict(story=context, narration=[s.get("say") for s in sc["segments"]], lines=[dict(line=i, say=sc["segments"][i].get("say")) for i in lines],
                              named=[dict(name=n.get("name"), is_a=n.get("is"), must_show=n.get("must_show")) for n in have.values()]),
                         ensure_ascii=False)
    try:
        got, use = ask(
            "Read the whole story, the speaker's claim and the upload transcript. For each line select subjects "
            "that depict its action or evidence in that story. Use the real people, service, location or document "
            "where it matters. When the Studio is the subject select THE STUDIO with studio_frames listing "
            "the exact frame names that show that action. Select the recording in place of any generic stock "
            "substitute. Cite additional external subjects only if another part of the line depicts them. Give the archive "
            "queries that find this specific action. "
            'Return JSON {"lines": [{"line": index, "subjects": [a named name, or {"name", "is": thing|place|person, '
            '"must_show": what a frame shows, "studio_frames":[frame names if THE STUDIO], "video_searches":[queries]}]}]}.',
            content, what="footage subjects", effort="low", max_tokens=3000)
    except Exception as e:  # noqa: BLE001 - an outage after the retries: the lines keep the recording, the film goes on
        ctx.log(f"footage subjects not chosen ({repr(e)[:200]}); the recording lines keep the recording")
        return {}, []
    out = {}
    for row in (got.get("lines") if isinstance(got, dict) else None) or []:
        if not isinstance(row, dict) or row.get("line") not in lines:
            continue
        subs = []
        for x in row.get("subjects") or []:
            name = x if isinstance(x, str) else (x.get("name") if isinstance(x, dict) else None)
            if not name or any(n["name"] == name for n in subs):
                continue
            if name in have:
                subject = dict(have[name])
                if isinstance(x,dict) and x.get("studio_frames"):
                    subject["studio_frames"] = x["studio_frames"]
                subs.append(subject)
            elif isinstance(x, dict) and [q for q in x.get("video_searches") or [] if isinstance(q, str) and q.strip()]:
                subs.append(dict(name=str(name), **{"is": x.get("is") if x.get("is") in ("thing", "place", "person") else "thing"},
                                 must_show=str(x.get("must_show") or name),
                                 video_searches=[str(q) for q in x["video_searches"] if isinstance(q, str) and q.strip()][:3]))
        out[int(row["line"])] = subs
    jsave(path, dict(context=context, lines=list(lines), subjects={str(k): v for k, v in out.items()}, spend=[use]))
    return out, [use]


def lend_subjects(fsc, subjects):
    """The footage script's lines take their subjects (footage_subjects): each named in the line's pictures, a new one
    added to the named list."""
    have = {n.get("name") for n in fsc.get("named") or []}
    for i, subs in subjects.items():
        seg = fsc["segments"][int(i)]
        seg["pictures"] = list(seg.get("pictures") or []) + [n["name"] for n in subs if n["name"] not in (seg.get("pictures") or [])]
        for n in subs:
            if n["name"] not in have:
                fsc.setdefault("named", []).append(dict(n))
                have.add(n["name"])


def proof_fill_clip(body, after_t, sha, shots, ranked, owed=()):
    """The export as the cut's proof fill: the whole body (its after seconds busy, keyed by the recording's sha256), its
    own shots (`shots`, [[t0, t1, id]]: a line short of pictures plays one no file has shown yet) and, only when the
    ranking call answered, each line's ranked export shots (`ranked`), best first. An unanswered call leaves `ranked`
    out, so the fill still plays the export's unshown shots instead of nothing (20 Nov: ranked={} turned the fill off
    for the whole film and every recording line looped its one shot). `owed`: the shot ids a line already takes as its
    own file, played last by another line's fill."""
    fill = dict(body, busy=[list(after_t)], origin=dict(key=sha, t0=0.0), owed=sorted({int(k) for k in owed}),
                shots=[[c["start"], c["end"], c["id"]] for c in shots if c["source"] == "THE EXPORT"])
    if ranked is not None:
        by_id = {c["id"]: c for c in shots}
        fill["ranked"] = {str(i): [[by_id[k]["start"], by_id[k]["end"], k] for k in ks if k in by_id and by_id[k]["source"] == "THE EXPORT"]
                          for i, ks in ranked.items()}
    return fill


def external_footage(ctx, script, pack, durs, anchors, reel_kw, spend):
    """Search uncovered external subjects and retain every proven clip and source observation across passes."""
    sc, sup_dir = script["script"], ctx.supply
    own = ("THE UPLOAD", "THE EXPORT", "THE STUDIO")
    fscript = json.loads(json.dumps(script))         # the footage engine searches the web; the recording's own names are ours
    receipts = script.get("receipts") or {}
    idea = script.get("idea_full") or {}
    context = dict(title=sc.get("title"), claim=idea.get("claim"), upload_says=receipts.get("upload_says"),
                   speaker=idea.get("speaker") or sc.get("speaker"), clip=idea.get("why"), topic=idea.get("technique"))
    studio = (pack.get("pictures") or {}).get("THE STUDIO") or []
    context.update(premise=idea.get("pain"),source=idea.get("source"),clip=idea.get("why") or idea.get("claim"),
                   studio_frames=[r["title"] for r in studio])
    fscript["script"]["title"] = json.dumps({k:v for k,v in context.items() if k!="studio_frames"}, ensure_ascii=False)
    for seg in fscript["script"]["segments"]:
        seg["pictures"] = [n for n in seg.get("pictures") or [] if n not in own]
        seg["see"] = [v for v in seg.get("see") or [] if v.get("thing") not in own]
    # a line whose only picture is the recording gets footage of what its words name too (the recording is a few shots,
    # never a whole line); the typed line and the call to action carry the film's devices
    pr = sc["proof"]
    title_lines = recorded_titles(script)
    candidates = [i for i in range(len(sc["segments"])) if i not in title_lines and
                  i not in (int(pr["after_after"]),len(sc["segments"])-1)]
    if candidates and not sc.get("recording_pictures_only"):
        subjects, uses = footage_subjects(ctx,fscript["script"],candidates,context)
        spend += uses
        for i, rows in subjects.items():
            if not rows:
                continue
            selected = next((r for r in rows if r["name"]=="THE STUDIO"), None)
            if selected and studio:
                frames = [r for r in studio if r["title"] in (selected.get("studio_frames") or [])]
                for rec in frames or studio:
                    rec.setdefault("placement_lines",[]).append(i)
            external = [r for r in rows if r["name"] not in own]
            seg = fscript["script"]["segments"][i]
            seg["pictures"] = []
            seg["see"] = [b for b in seg.get("see") or [] if b.get("thing") in {r["name"] for r in external}]
            lend_subjects(fscript["script"],{i:external})
        ctx.log(f"Footage subjects from the film and upload: {[(i,[r['name'] for r in rows]) for i,rows in subjects.items()]}")
    search_lines = [i for i, seg in enumerate(fscript["script"]["segments"]) if seg.get("pictures")]
    foot = chain.footage(ctx, fscript, durs, anchors=anchors, lines=search_lines, **W3.footage_kw(reel_kw)) if search_lines else None
    passes = [("footage", foot)] if foot else []
    covered = {ln["i"] for ln in (foot or {}).get("lines") or [] if ln.get("clips")}
    missing = [i for i in search_lines if i not in covered]
    if foot and len(missing) > len(search_lines) / 2:
        # too little passed: once more with each subject as a plain thing to see (the writer's must_show can be too narrow)
        ctx.log(f"footage: {foot['counts']['lines_with_footage']} of {len(search_lines)} external-footage lines; searching again with plain subjects")
        for n in fscript["script"].get("named") or []:
            queries = n.get("video_searches") or []
            n["video_searches"] = list(dict.fromkeys([*queries, n.get("must_show") or n["name"]]))
        foot2 = chain.footage(ctx, fscript, durs, anchors=anchors, lines=missing, out_name="footage_plain",
                              avoid=[os.path.join(sup_dir, "footage", "footage.json")], **W3.footage_kw(reel_kw))
        if foot2:
            passes.append(("footage_plain", foot2))
    clips = {}
    for folder, result in passes:
        for ln in result.get("lines") or []:
            clips.setdefault(ln["i"], []).extend(W3.clip_facts(dict(c, path=os.path.join(sup_dir, folder, c["file"])))
                                                 for c in ln["clips"])
        pack["credits"] += [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)", credit=c["channel"],
                                 page=c["url"], time_range=c["time_range"], line=c["line"]) for c in result["credits"]]
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what=folder, usd=result["spend"]["usd"]))
    pack["footage"] = [dict(i=i, clips=rows) for i, rows in sorted(clips.items())]
    report = dict(clips=sum(map(len, clips.values())), lines=sum(bool(v) for v in clips.values()),
                  credits=[c for _, result in passes for c in result["credits"]])
    ctx.log(f"footage: {report['clips']} clips on {report['lines']} of {len(search_lines)} external lines")
    return fscript, report if passes else None


def supply(ctx, script):
    sc = script["script"]
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
    if sc.get("format") == "showcase" and not sc.get("segments"):
        return showcase_supply(ctx, script, sup_dir, pk)
    spend = []
    env = dict(os.environ, TMPDIR=ctx.tmp)
    narr_dir = os.path.join(sup_dir, "narration")
    nar = PX.narrate(writer.narration_script(sc, "03"), narr_dir, band="03", label=f"wf2-narrate-{ctx.run_id}", env=env,
                     reader=os.path.join(STORIES, "voice.py"), repair_fast_read=True)
    nar = repair_narration_joins(ctx, nar, env)
    narr_dir = os.path.dirname(nar["wav"])
    ctx.log(f"narration {nar['duration']} s, gate {nar['gate']}")
    pack = dict(sid=ctx.sid, words=nar["words"], narration=nar["wav"], ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[],
                work=os.path.join(sup_dir, "finish"))
    used = {n for s in sc["segments"] for n in s.get("pictures") or []}
    named = [n for n in sc.get("named") or [] if n.get("name") in used]
    if named:                    # free photographs are the fallback; the film is cut from footage, so too few is no stop
        inventory = jload(os.path.join(sup_dir, "visual_inventory", "inventory.json"))
        if inventory:
            got = {n: rows for n, rows in inventory["pictures"].items() if n in used}
            reports, usd = inventory["reports"], 0.0
        else:  # a resumable script written before inventory-first acquisition
            photo_named, photo_spend = photo_briefs(ctx, named)
            spend += photo_spend
            got, reports, usd = PX.pictures(photo_named, os.path.join(sup_dir, "pics"), per=4)
        jsave(os.path.join(sup_dir, "photo_reports.json"), reports)
        ctx.log(f"photographs: {sum(len(v) for v in got.values())} across {len(named)} subjects")
        spend.append(dict(stage="supply", provider="openai", model="picture check", what="photographs", usd=usd))
        for recs in got.values():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
        pack["pictures"] = {k: v for k, v in got.items() if v}
        pack["credits"] = [dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
                           for v in got.values() for r in v]
        pack["missing"] = [n["name"] for n in named if not got.get(n["name"])]
    recording = jload(script.get("record"), {}) if script.get("record") else {}
    if recording:
        studio = VI.studio_photos(recording)
        if studio:
            pack["pictures"]["THE STUDIO"] = studio
    words_all = json.load(open(nar["words"]))["words"]
    durs = chain.seg_durs(words_all, len(sc["segments"]))
    anchors = {k: [r["path"] for r in v if r.get("path")] for k, v in (pack.get("pictures") or {}).items()}
    # Illustrative footage is silent. The two real-voice passages come only
    # from our verified Studio recording, so source transcripts are unused.
    reel_kw = dict(preserve_source=True, shot_s=0.55, line_max=12, per=4, hear_s=0.0)
    fscript, footage_report = external_footage(ctx, script, pack, durs, anchors, reel_kw, spend)
    fdir = os.path.join(sup_dir, "footage")
    os.makedirs(fdir, exist_ok=True)
    pr = sc["proof"]
    # the recording: the before and the after cut in with their own sound; a frame of the after may open the film
    pc = product_clips(script, os.path.join(fdir, "product"))
    voices = [dict(after=int(pr["before_after"]), person="before", wav="product/before.wav", file="product/before.mp4", said=None,
                   source=recording_source(script, "upload"), time_range=pc["before"]["t"]),
              dict(after=int(pr["after_after"]), person="after", wav="product/after.wav", file="product/after.mp4", said=None,
                   source=recording_source(script, "export"), time_range=pc["after"]["t"])]
    if pc.get("hook"):            # the opening beat: the export's landing on the speaker's own voice, before the narrator
        voices.append(dict(after=-1, person="hook", wav="product/hook.wav", file="product/hook.mp4", said=None,
                           source=recording_source(script, "export"), time_range=pc["hook"]["t"]))
    placed = splice(ctx, narr_dir, fdir, voices)
    if len(placed) != len(voices):
        raise RuntimeError(f"the recording was not cut into the narration: {placed}")
    lands = export_landings(script)               # the export's own pictures and titles: sound events where a cut of it shows them
    pack["voices"] = [dict(p, clip=W3.clip_facts(dict(path=p["file"], file=os.path.basename(p["file"]), subject=p["person"],
                                                      seconds=round(p["end"] - p["start"], 3),
                                                      **({"landings": landings_in(lands, *pc["after"]["t"])} if p["person"] == "after" else {}))))
                      for p in placed]
    ex_sha, up_sha = script["receipts"]["export"]["sha256"], script["receipts"]["upload"]["sha256"]
    # THE UPLOAD / THE EXPORT: the recording as its own shots (split at its edit list's events and its measured picture
    # changes), ranked for every line's words by a model that sees them; the lines that name it take their best shots as
    # takes, each file carrying the recording's sha256 and its first second, so no second plays twice under two names
    lines = {int(ln["i"]): ln for ln in pack["footage"]}
    rec_ = json.load(open(script["record"]))
    ex_ = next((e for e in rec_["exports"] if e["tag"] == "final"), rec_["exports"][-1])
    body_s = float(pc["body"]["t"][1])
    up_hard, up_soft = upload_events(rec_)
    ex_hard, ex_soft = export_events(script["record"], rec_, ex_, body_s)
    srcs = {"THE UPLOAD": dict(path=rec_["upload"]["file"], dur=float(rec_["upload"]["dur"]), busy=[pc["before"]["t"]], hard=up_hard, soft=up_soft,
                               sha=up_sha),
            "THE EXPORT": dict(path=ex_["file"], dur=body_s, busy=[pc["after"]["t"]] + ([pc["hook"]["t"]] if pc.get("hook") else []), hard=ex_hard, soft=ex_soft, sha=ex_sha)}
    demand = W3.line_demand(dict(words=nar["words"]), sc, durs)
    chosen, ranked, shots, uses = product_windows(ctx, script, srcs, demand)
    spend += uses
    crops = {os.path.join(fdir, "product", "before.mp4"): pc["before"].get("crop")}
    for k, window in enumerate(chosen):
        name, i = window["source"], window["line"]
        path = srcs[name]["path"]
        t, end = window["start"], window["end"]
        fn = f"product/{'upload' if name == 'THE UPLOAD' else 'export'}_{k}.mp4"
        cut(path, t, end, os.path.join(fdir, fn), audio=False)
        if name == "THE UPLOAD":
            crops[os.path.join(fdir, fn)] = product_crop(script["record"], rec_, (t + end) / 2)
        c = W3.clip_facts(dict(path=os.path.join(fdir, fn), file=fn, subject=name, seconds=end-t,
                               source=recording_source(script, "upload" if name == "THE UPLOAD" else "export"),
                               time_range=[t, end], origin=dict(key=srcs[name]["sha"], t0=t), shot=window["id"],
                               **({"landings": landings_in(lands, t, end)} if name == "THE EXPORT" else {})))
        lines.setdefault(i, dict(i=i, clips=[]))["clips"].append(c)
    pack["footage"] = [lines[k] for k in sorted(lines)]
    talk = {}
    for ln in pack["footage"]:
        for c in ln["clips"]:
            t, score = R.talking(c, PX.FF)
            talk[c["path"]] = dict(talking=t, score=score)
    pack["talking"] = talk
    # Use verified takes, not arbitrary slices reused across narration lines.
    W3.measure_clips(pack)
    usd, calls = W3.judge_supply(ctx, sc, pack)
    spend.append(dict(stage="supply", provider="openai", model=CHECK_MODEL,
                      what="per-line picture identity", usd=usd, calls=calls))
    rows = W3.fit_report(pack, sc, durs)
    for attempt in range(2):
        short = [r["i"] for r in rows if r["short"] and fscript["script"]["segments"][r["i"]].get("pictures")]
        if not short:
            break
        deficit = sum(r["deficit"] for r in rows)
        added = W3.topup_once(ctx, fscript, pack, durs, short, spend, f"footage-proof-{attempt}",
                              anchors, reel_kw, line_max=max(r["deficit"] for r in rows if r["i"] in short))
        rows = W3.fit_report(pack, sc, durs)
        if not added or sum(r["deficit"] for r in rows) >= deficit:
            break
    pack["fit_report"] = dict(lines=rows, deficit=sum(r["deficit"] for r in rows))
    # The export is an opening candidate, not an unconditional override of
    # the hook's meaning. The verified cutter chooses its fitting picture;
    # the full-frame, real-voice payoff remains mandatory above.
    product_dir = os.path.join(fdir, "product")
    pack["product"] = dict(pc, paths=[os.path.join(product_dir, n) for n in sorted(os.listdir(product_dir)) if n.endswith(".mp4")],
                           crops={k: v for k, v in crops.items() if v})
    body = W3.clip_facts(dict(path=os.path.join(product_dir, "export_body.mp4"), file="product/export_body.mp4", subject="THE EXPORT",
                              seconds=pc["body"]["t"][1], source=recording_source(script, "export"), time_range=pc["body"]["t"],
                              landings=landings_in(export_landings(script), *pc["body"]["t"])))
    # the after plays whole on its own voice; a line short of pictures plays the export's shots ranked for its words
    pack["proof_fill"] = [proof_fill_clip(body, pc["after"]["t"], ex_sha, shots, ranked, [w["id"] for w in chosen])]
    if pc.get("hook"):
        pack["proof_fill"][0]["busy"].append(list(pc["hook"]["t"]))     # the opening beat plays whole on its own voice
    pack["product_shots"] = dict(shots=len(shots), taken=len(chosen),
                                 ranked=None if ranked is None else {str(i): len(v) for i, v in ranked.items()})
    wj = json.load(open(nar["words"]))
    rec = dict(narration_s=wj["duration"], voice=nar.get("voice"), gate=nar["gate"], pictures=sum(len(v) for v in pack["pictures"].values()),
               subjects=len(pack["pictures"]), missing=pack.get("missing"), spend=spend, product=pc,
               footage=footage_report)
    jsave(pk, dict(pack=pack, record=rec))
    return rec


# ----------------------------------------------------------------- score

def content_pace(pack, sc):
    """Describe the available shots and the narration to the compositor."""
    words = json.load(open(pack["words"]))["words"]
    span = max((w["end"] for w in words),default=0) - min((w["start"] for w in words),default=0)
    assets = {c["path"] for ln in pack.get("footage") or [] for c in ln.get("clips") or []}
    assets.update(r["path"] for photos in (pack.get("pictures") or {}).values() for r in photos)
    return max(span/max(1,len(assets)),1/F.FPS), dict(supply=len(assets), seconds=span)


CAPTION_PIECE_F = 3        # a caption word trimmed around a full-frame recording keeps a piece this long or longer
DISPLAY_OVER_RECORDING_Y = 0.40   # a display word over the recording rests here: clear of its own burned-in captions (about
                                  # 0.70-0.76 of the height) and of the watermark (to 0.19); the reel's high seat for wide type
NAME_OVER_RECORDING_Y = 0.27      # a name super over the recording: the reel's own high name seat (reel.name_supers), never
                                  # its low one at 0.76, which sits on the recording's captions


def is_display_word(it):
    """The reel's designed display type, film.display_words' own voices only: its large part (voice dsp<f0>, one copy a
    landing) and its small line (dsps<f0>). Never a caption, a name super (dsp_name<f0>) or a designed card's type
    (dsp_card<f0>, dsp_cards<f0>)."""
    if it.get("k") != "word" or not it.get("f"):
        return False
    f0 = int(it["f"][0])
    return it.get("v") in (f"dsp{f0}", f"dsps{f0}")


def is_name_super(it):
    """reel.name_supers' type (voice dsp_name<f0>): a role=name item set at 0.27 or 0.76."""
    return it.get("k") == "word" and bool(it.get("f")) and it.get("v") == f"dsp_name{int(it['f'][0])}"


def rest_y(it):
    y = it.get("y")
    y = y[-1] if isinstance(y, list) and y else y
    return float(y) if isinstance(y, (int, float)) else None


def lift(it, d):
    """The item moved by d of the height, its landing's per-frame rise kept."""
    y = it.get("y")
    if not d or rest_y(it) is None:
        return it
    return dict(it, y=[round(float(v) + d, 4) for v in y] if isinstance(y, list) else round(float(y) + d, 4))


def as_delivered(l1, product_paths, spans=None):
    """The recording as the product made it. Where a product picture fills the frame (the real-voice before and after,
    the hook's landing, the whole upload: sealed spans) nothing is drawn over it: no flash, no finish, no look, and a
    caption word is trimmed to the frames outside the span, never deleted whole (20 Nov: all 83 narration words the
    reel built were deleted, even over the film's one photograph). Where the reel lays a product clip as a print on its
    board (its items marked proof_board), narration captions occupy the board below the print.
    spans: only these frame spans, sealed (a repaired window that now plays the recording), else every span the recording
    plays. Returns the sealed spans."""
    keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") in product_paths}
    mine = [it for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys and it.get("z", 0) <= 1]
    if spans is not None:
        spans, boards = sorted(tuple(s) for s in spans), []
    else:
        spans = sorted({tuple(it["f"]) for it in mine if not it.get("proof_board")})
        boards = sorted({tuple(it["f"]) for it in mine if it.get("proof_board")})
    for it in l1["items"]:
        if it.get("pic") in keys:
            it.pop("look", None)

    def outside(it, cover):
        """The item's pieces outside the spans `cover`: [(a, b)]."""
        at, out = it["f"][0], []
        for s0, s1 in sorted(cover):
            if s0 > at:
                out.append((at, min(s0, it["f"][1])))
            at = max(at, s1)
        if it["f"][1] > at:
            out.append((at, it["f"][1]))
        return [(a, b) for a, b in out if b > a]
    out = []
    for it in l1["items"]:
        over = [s for s in spans if it["f"][0] < s[1] and it["f"][1] > s[0]]
        board = [s for s in boards if it["f"][0] < s[1] and it["f"][1] > s[0]]
        if not (over or board) or it.get("pic") in keys or it.get("proof_board") or it.get("keep_over_proof"):
            out.append(it)                                 # the product's crop drawn on its own upload stays with it
            continue
        if it["k"] == "word":
            if not over:
                out.append(it)                             # on the board, under the print: the narration's own seat
                continue
            for a, b in outside(it, over):                 # neither caption the raw upload nor double-caption its export
                if b - a >= CAPTION_PIECE_F:
                    piece = D.slice_item(it, a, b)
                    if piece:
                        piece["id"] = f"{it['id']}t{a}"
                        out.append(piece)
            continue
        if it["k"] == "flash" and it.get("mode") != "pool":
            continue                                       # a flash frame inside the recording, full frame or on its print
        if it["k"] == "flash" or (it["k"] == "card" and it.get("z", 0) >= 1):
            cover = over if it["k"] == "flash" else over + (board if float(it.get("z", 0)) >= 40 else [])
            if not cover:
                out.append(it)                             # a pool on the board's caption seat; a card beside the print
                continue
            for a, b in outside(it, cover):                # the pool and the finish stop at the recording and start after it
                if b - a >= 2:
                    piece = D.slice_item(it, a, b)
                    if piece:
                        piece["id"] = f"{it['id']}{'a' if a == it['f'][0] else 'b'}{a}"
                        out.append(piece)
            continue
        out.append(it)
    l1["items"] = out
    return spans


def still(path, t, w=320):
    if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        r = subprocess.run([PX.FF, "-v", "error", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "5", "-f", "image2pipe",
                            "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60)
        return base64.b64encode(r.stdout).decode() if r.stdout else None
    return frames(path, [t], w)[0]


def words_under(clock, f0, f1):
    ws = [w["w"] for w in clock.words if F.Clock.f(w["t1"]) > f0 and F.Clock.f(w["t0"]) < f1]
    return " ".join(ws)


def picture_key(p, words):
    return json.dumps([6, p["path"], p.get("src"), words], ensure_ascii=False)


def picture_check(ctx, beats, clock, product_paths, cache, recording=None, roles=None):
    """Every played source window checked; absent frames/verdicts never pass. roles: {line: beat} (hook, setup, low,
    turn, payoff, close), so the check reads a pain line's real recording as the pain it is."""
    todo = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, *p["f"])
        key = picture_key(p, ws)
        if key not in cache:
            todo.append((key, p, ws))
    spend = []
    system = ("Check each picture against the EXACT words under it in a marketing film for editors. "
              "Return fit=yes only if those words are carried by what the frames actually show. "
              "A celebrity talking head does not illustrate an editing operation or another named company. "
              "A timeline of another app is not Valmera. The uploaded clip and real Valmera export are "
              "valid evidence when the words describe those exact states or their visible editing. "
              "An intentional metaphor must clearly carry these words; generic thematic filler is loose. "
              "Blank, broken, contradictory or unidentified pictures fail. Judge all pictures including "
              "product clips. Authenticated before/after passages speak in the source clip's own voice; "
              "blank narrator words in those passages are not evidence of a blank or mismatched picture. "
              "Use the recording receipts to identify the state and the adjacent narration only to resolve "
              "pronouns or incomplete phrase boundaries. The shown picture must still carry the exact words. "
              "Each picture names its line's role in the story: hook, setup and low are the editor's pain, turn is the "
              "typed request, payoff and close are the result. On a pain line the real recording that shows the problem "
              "itself (the raw upload, or the export where nothing on screen yet illustrates the words) carries those "
              "words: what is missing from the screen is the pain the line names. "
              "Only yes passes; loose and wrong both need another picture.")
    for k in range(0, len(todo), 8):
        batch = todo[k:k + 8]
        content, readable = [], set()
        for n, (key, p, ws) in enumerate(batch, 1):
            src = p.get("src") or [0.0, 0.0]
            times = [src[0] + (src[-1] - src[0]) * q for q in (0.15, 0.5, 0.85)]
            samples = [still(p["path"], t, w=720) for t in times]
            if not all(samples):
                cache[key] = dict(fits=False, why="source window could not be inspected")
                continue
            readable.add(n)
            a, b = p["f"][0] / F.FPS, p["f"][1] / F.FPS
            segs = {w.get("seg") for w in clock.words if w["t1"] > a and w["t0"] < b}
            context = " ".join(w["w"] for w in clock.words if w.get("seg") in segs)
            role = (roles or {}).get(p.get("spoken_line", p.get("line")))   # the line whose words sit under it
            content.append(dict(type="text", text=f"Picture {n}; words: {ws!r}; source role: {p.get('name')!r}; "
                                + (f"line role: {role}; " if role else "") +
                                f"real product recording: {p['path'] in product_paths}; full line for resolving "
                                f"phrase fragments only: {context!r}"))
            if recording and p["path"] in product_paths:
                content.append(dict(type="text", text="Authenticated recording receipts: " + json.dumps(recording, ensure_ascii=False)))
            content.extend(img(b) for b in samples)
        if not readable:
            continue
        content.append(dict(type="text", text='Return JSON: {"pictures": [{"n": number, "fit": "yes"|"loose"|"wrong", "why": "reason"}]}'))
        got, use = ask(system, content, what="picture check", effort="low", model=CHECK_MODEL, max_tokens=3000)
        spend.append(use)
        for v in (got.get("pictures") if isinstance(got, dict) else []) or []:
            try:
                n = int(v["n"])
                if n not in readable:
                    continue
                key = batch[n - 1][0]
            except (KeyError, ValueError, TypeError):
                continue
            fit = str(v.get("fit") or "").lower()
            cache[key] = dict(fits=fit == "yes", fit=fit, why=v.get("why"))
    bad = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, *p["f"])
        v = cache.get(picture_key(p, ws)) or dict(fits=False, why="missing picture verdict")
        if not v["fits"]:
            bad.append((os.path.basename(p["path"]), ws, v.get("why")))
    return bad, spend


# ----------------------------------------------------------------- a window the check judged loose or wrong is repaired

REPAIR_TRIES = 3          # ranked replacements a misfit window tries in turn, each checked on its own frames only
REPAIR_CANDIDATES = 48    # the film's own pictures offered for the misfits in one look
REPAIR_STEP_S = 0.25      # the free export seconds are searched at this step for windows no shown second overlaps


def roles_of(sc):
    return {i: s.get("beat") for i, s in enumerate(sc.get("segments") or [])}


def words_line(p):
    """The line whose words sit under a picture (reel.note's spoken_line), else the line it was picked for."""
    return p.get("spoken_line", p.get("line"))


def checked(ctx, beats, clock, product_paths, cache, recording=None, roles=None):
    """picture_check, asked again once when it gets no answer (an outage, not a verdict); after that the windows it could
    not read stay unverified and the film carries on. (bad, spend, fault or None)."""
    spend, fault = [], None
    for _ in range(2):
        try:
            bad, sp = picture_check(ctx, beats, clock, product_paths, cache, recording, roles=roles)
            return bad, spend + sp, None
        except Exception as e:  # noqa: BLE001 - an unanswered look is an outage: asked again, then recovered
            fault = repr(e)[:300]
            spend.append(dict(stage="picture check", fault=fault))
    bad = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, *p["f"])
        v = cache.get(picture_key(p, ws)) or dict(fits=False, why="missing picture verdict")
        if not v.get("fits"):
            bad.append((os.path.basename(p["path"]), ws, v.get("why")))
    return bad, spend, fault


def recording_timelines(pack):
    """{path: (recording, offset)}: each cut of the recording the pack holds, on the recording's own seconds (the
    export's or the upload's): a cut's src second s is the recording's offset + s."""
    pc = pack.get("product") or {}
    out = {}

    def role_of(c):
        title = str((c.get("source") or {}).get("title") or "")
        name = str(c.get("subject") or "")
        if title.startswith("THE EXPORT") or name == "THE EXPORT":
            return "export"
        if title.startswith("THE UPLOAD") or name == "THE UPLOAD":
            return "upload"
        return None
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            r, tr = role_of(c), c.get("time_range")
            if r and isinstance(tr, (list, tuple)) and tr:
                out[str(c.get("path"))] = (r, float(tr[0]))
    for c in pack.get("proof_fill") or []:              # the proof fill is the export by construction
        out[str(c.get("path"))] = ("export", float((c.get("time_range") or [0.0])[0]))
    by_name = dict(before=("upload", "before"), after=("export", "after"), hook=("export", "hook"), export_body=("export", "body"))
    for p in pc.get("paths") or []:
        stem = os.path.splitext(os.path.basename(str(p)))[0]
        if stem in by_name and str(p) not in out:
            r, k = by_name[stem]
            out[str(p)] = (r, float(((pc.get(k) or {}).get("t") or [0.0])[0]))
    for v in pack.get("voices") or []:
        c, k = v.get("clip") or {}, v.get("person")
        if c.get("path") and k in ("before", "after", "hook") and str(c["path"]) not in out:
            out[str(c["path"])] = ("upload" if k == "before" else "export", float(((pc.get(k) or {}).get("t") or [0.0])[0]))
    return out


def played_seconds(l1, timelines):
    """Every recording second the film already shows: {recording: [(a, b)]}, each played item's src mapped through its
    cut's offset (an item with no src: its whole asset)."""
    out = {"export": [], "upload": []}
    if not l1:
        return out
    assets = l1.get("assets") or {}
    for it in l1.get("items") or []:
        if it.get("k") not in ("shot", "card"):
            continue
        a = assets.get(str(it.get("pic"))) or {}
        tl = timelines.get(str(a.get("path")))
        if not tl:
            continue
        src = it.get("src")
        r, off = tl
        if isinstance(src, (list, tuple)) and len(src) >= 2:
            out[r].append((off + float(src[0]), off + float(src[-1])))
        else:
            out[r].append((off, off + float(a.get("dur") or 0.0)))
    return out


def recording_words(script):
    """What the recording says on its own seconds: {upload: [(t, word)], export: [(t, word)]}, the source's caption words
    on the upload's seconds and, through the export's EDL keep ranges, on the export's. {} when they cannot be read."""
    try:
        rec = json.load(open(script["record"]))
        src = next((s for s in sources() if s["file"] == rec["source"]["file"]), None)
        if not src:
            return {}
        t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
        up = [(round(t - t0, 3), w) for t, w in json3_words(src["captions"]) if t0 <= t <= t1]
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration):
        return {}
    out, at = [], 0.0
    for k in edl.get("keep") or []:
        try:
            a, b = float(k[0]), float(k[1])
        except (TypeError, ValueError, IndexError):
            continue
        out += [(round(at + t - a, 3), w) for t, w in up if a <= t < b]
        at += b - a
    return dict(upload=up, export=out)


def said_in(said, tl, t, win):
    """The words the recording speaks over [t, t + win] of one cut of it ('' when not known)."""
    if not tl or not said:
        return ""
    r, off = tl
    a, b = off + float(t), off + float(t) + float(win)
    return " ".join(w for s, w in said.get(r) or [] if a - 0.15 <= s < b)


def repair_candidates(pack, sc, lines, cap=REPAIR_CANDIDATES, l1=None, said=None, win_s=1.0):
    """The pack's own pictures a misfit window may take: the footage and photographs of the misfits' lines first, then
    the real export's free seconds (its timeline minus every export second the film already plays, and the after and
    opening beat spans), then the recording's other cuts at seconds not yet shown. A recording candidate names the words the
    recording speaks there, so the ranking can find the instant a line names.
    [{id, path, kind (clip|photo), name, t (window start in the file), seconds, w, h, fps, faces, landings, line, said}]."""
    out, seen = [], set()
    tls = recording_timelines(pack)
    played = played_seconds(l1, tls)
    win_s = max(float(win_s or 1.0), 0.2)

    def shown(path, t):
        tl = tls.get(str(path))
        if not tl:
            return False
        a, b = tl[1] + float(t), tl[1] + float(t) + win_s
        return any(b > x + 1e-3 and a < y - 1e-3 for x, y in played[tl[0]])

    def add(c, kind, name, t, line=None):
        path = str(c.get("path"))
        k = (path, round(float(t), 2))
        if k in seen or not os.path.exists(path) or (kind == "clip" and shown(path, t)):
            return
        seen.add(k)
        out.append(dict(path=path, kind=kind, name=name, t=round(float(t), 3), seconds=float(c.get("seconds") or 0.0),
                        w=int(c.get("w") or F.W), h=int(c.get("h") or F.H), fps=float(c.get("fps") or F.FPS), faces=c.get("faces") or [],
                        landings=c.get("landings"), line=line, product=name in ("THE UPLOAD", "THE EXPORT", "THE STUDIO"),
                        said=said_in(said, tls.get(path), t, win_s) if kind == "clip" else ""))
    segs = sc.get("segments") or []
    near, rest = [], []
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            sec = float(c.get("seconds") or 0.0)
            row = (c, "clip", c.get("subject") or c.get("title") or os.path.basename(str(c.get("path"))),
                   max(0.0, sec / 2 - 0.5) if sec > 1.5 else 0.0, int(ln["i"]))
            (near if int(ln["i"]) in lines else rest).append(row)
    for name, recs in (pack.get("pictures") or {}).items():
        on = [i for i, s in enumerate(segs) if name in (s.get("pictures") or [])]
        for r in recs:
            (near if set(on) & set(lines) else rest).append((r, "photo", name, 0.0, on[0] if on else None))
    for rec in (pack.get("pictures") or {}).get("THE STUDIO", []):
        add(rec,"photo","THE STUDIO",0.0)
    for row in near:
        add(*row)
    for body in pack.get("proof_fill") or []:
        busy = [tuple(b) for b in body.get("busy") or []] + [tuple(((pack.get("product") or {}).get("hook") or {}).get("t") or (0, 0))]
        tl = tls.get(str(body.get("path")))
        off = tl[1] if tl else 0.0
        busy += [(x - off, y - off) for x, y in played["export"]]          # every export second the film already plays
        sec = float(body.get("seconds") or 0.0)
        t = 0.0
        while t + win_s <= sec + 1e-6:
            if not any(t + win_s > a + 1e-3 and t < b - 1e-3 for a, b in busy):
                add(dict(body), "clip", "THE EXPORT", t)
                busy.append((t, t + win_s))                               # the next candidate is another instant
                t += win_s
            else:
                t += REPAIR_STEP_S
    for row in rest:
        if row[2] in ("THE UPLOAD", "THE EXPORT"):
            add(*row)
    out = out[:cap]
    for i, c in enumerate(out):
        c["id"] = i
    return out


def choose_replacements(misfits, cands, clock, roles, cache):
    """One look at the misfit windows and the film's own pictures: for each window the candidates that show its exact
    words, best first (up to REPAIR_TRIES). {window index: [candidate ids]}. An unanswered look is asked again once."""
    system = ("Some windows of a marketing film for video editors show a picture that does not carry the exact words under it. "
              "For each window, rank up to three of the film's own candidate pictures that show what those words say, best first. "
              "On a line about the editor's pain (role hook, setup or low) the real recording showing the problem itself "
              "(the raw upload, or the export where nothing on screen yet illustrates the words) carries the words. The real "
              "Valmera export carries every line about the result. A recording candidate names what its speaker says at that "
              "instant: the instant whose words the line names is the one to show. A candidate that shows something unrelated "
              "to the words is left out; an empty list is right when none carries them.")
    content = [dict(type="text", text="The candidates (the film's own footage, photographs and its real recording):")]
    for c in cands:
        b = still(c["path"], c["t"] + min(0.5, max(c["seconds"] - c["t"], 0.0) / 2), w=360)
        if b:
            content += [dict(type="text", text=f"Candidate {c['id']}: {c['name']}" + (" (the real Valmera recording)" if c["product"] else "")
                             + (f" at {c['t']:.1f} s" if c["kind"] == "clip" else "")
                             + (f"; the speaker says there: {c['said']!r}" if c.get("said") else "")), img(b)]
    for n, p in enumerate(misfits):
        ws = words_under(clock, *p["f"])
        v = cache.get(picture_key(p, ws)) or {}
        src = p.get("src") or [0.0, 0.0]
        b = still(p["path"], (src[0] + src[-1]) / 2, w=360)
        content.append(dict(type="text", text=f"Window {n}: words {ws!r}; line role: {roles.get(words_line(p))}; it shows "
                                              f"{p.get('name')!r}, judged {v.get('fit')}: {v.get('why')}"))
        if b:
            content.append(img(b))
    content.append(dict(type="text", text='Return JSON: {"windows": [{"n": window, "ranked": [candidate ids, best first]}]}'))
    got, spend = {}, []
    for _ in range(2):
        try:
            got, use = ask(system, content, what="picture repair", effort="low", model=CHECK_MODEL, max_tokens=3000)
            spend.append(use)
            break
        except Exception as e:  # noqa: BLE001 - an unanswered look is asked again; then the windows keep their pictures
            spend.append(dict(stage="picture repair", fault=repr(e)[:200]))
    ok_ids = {c["id"] for c in cands}
    out = {}

    def same_window(c, p):
        """The candidate plays the very seconds the misfit already shows (same file, overlapping source window)."""
        if c["path"] != p["path"]:
            return False
        if c["kind"] != "clip" or not p.get("src"):
            return True
        n = (p["f"][1] - p["f"][0]) / F.FPS
        return c["t"] < float(p["src"][-1]) - 1e-3 and c["t"] + n > float(p["src"][0]) + 1e-3
    for row in (got.get("windows") if isinstance(got, dict) else []) or []:
        try:
            n = int(row["n"])
            ids = [int(x) for x in row.get("ranked") or [] if int(x) in ok_ids]
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= n < len(misfits):
            out[n] = [i for i in ids if not same_window(cands[i], misfits[n])][:REPAIR_TRIES]
    return out, spend


def _asset_key(l1, c):
    """The L1 asset that plays candidate c: the one already on its file, else a new one."""
    assets = l1.setdefault("assets", {})
    for k, a in assets.items():
        if a.get("path") == c["path"]:
            return k
    k = f"rp{len(assets):02d}"
    if c["kind"] == "photo":
        assets[k] = dict(kind="image", path=c["path"], w=c["w"], h=c["h"])
    else:
        assets[k] = dict(kind="video", path=c["path"], w=c["w"], h=c["h"], fps=c["fps"], dur=c["seconds"])
        if c.get("landings"):
            assets[k]["landings"] = [dict(x) for x in c["landings"]]
    return k


def swap_in(l1, p, items, c, looks=None):
    """The window's own items (`items`: the originals as built) playing candidate c over the same frames: same frames,
    same layer and finish, the new picture covering the item's box (its framing moving when the original's moved).
    looks: {item id: the section look it had before the recording was shown as delivered}, given back to a picture that
    is not the recording. Returns the src span the swapped items really play ([first src start, last src end]) or None."""
    key = _asset_key(l1, c)
    f0 = int(p["f"][0])
    (fx, fy), _ = F.focus_of(c)
    out = []
    for o in items:
        it = json.loads(json.dumps(o))
        it["pic"], it["repair"] = key, True
        a, b = int(it["f"][0]), int(it["f"][1])
        if c["kind"] == "clip":
            t0 = c["t"] + (a - f0) / F.FPS
            t1 = c["t"] + (b - f0) / F.FPS
            if c["seconds"] and t1 > c["seconds"]:
                t0, t1 = max(0.0, t0 - (t1 - c["seconds"])), c["seconds"]
            it["src"] = [round(t0, 4), round(t1, 4)]
        else:
            it.pop("src", None)
        if it["k"] == "card":
            w, h = it.get("w"), it.get("h")
            w = w[-1] if isinstance(w, list) else w
            h = h[-1] if isinstance(h, list) else h
            asp = float(w or 1.0) * F.W / max(float(h or 1.0) * F.H, 1e-6)
        else:
            asp = F.W / F.H
        base = [F.r4(v) for v in F.cover_crop(float(c["w"]), float(c["h"]), asp, (fx, fy), (0.5, 0.42))]
        crop = it.get("crop")
        it["crop"] = F.push(base, b - a, 0.05, (fx, fy)) if isinstance(crop, list) and crop and isinstance(crop[0], (list, tuple)) else base
        if looks is not None and not c.get("product"):
            if looks.get(o["id"]):
                it["look"] = looks[o["id"]]
            else:
                it.pop("look", None)
        out.append(it)
    ids = {o["id"] for o in items}
    l1["items"] = [it for it in l1["items"] if it.get("id") not in ids] + out
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))
    srcs = [it["src"] for it in sorted(out, key=lambda it: it["f"][0]) if it.get("src")]
    return [srcs[0][0], srcs[-1][1]] if srcs else None


def _minus(lo, hi, spans):
    """[lo, hi) without the given spans: [(a, b)]."""
    parts = [(lo, hi)]
    for s0, s1 in sorted(spans):
        nxt = []
        for a, b in parts:
            if s1 <= a or s0 >= b:
                nxt.append((a, b))
                continue
            if s0 > a:
                nxt.append((a, s0))
            if s1 < b:
                nxt.append((s1, b))
        parts = nxt
    return [(a, b) for a, b in parts if b > a]


def shown_off_recording(l1, pre, windows, product_paths, hold=()):
    """A window the repair moved off the real recording gets back what as_delivered took off it while the recording
    played there: the narration's captions and flash frames (whole, when no recording or device still plays under
    them) and the caption pool and the section finish over that window's frames. pre: the items as built, before
    as_delivered. Returns the ids given back."""
    if not windows or not pre:
        return []
    keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") in product_paths}
    prod_now = [tuple(it["f"]) for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys and it.get("z", 0) <= 1]
    blocked = prod_now + [tuple(s) for s in hold or ()]
    have = {str(it.get("id")) for it in l1["items"]}
    back = []
    for it in pre:
        k, iid = it.get("k"), str(it.get("id"))
        if iid in have or it.get("pic") in keys:
            continue
        over = [w for w in windows if it["f"][0] < w[1] and it["f"][1] > w[0]]
        if not over:
            continue
        a, b = int(it["f"][0]), int(it["f"][1])
        if k == "word" or (k == "flash" and it.get("mode") != "pool"):
            if not any(a < s1 and b > s0 for s0, s1 in blocked):
                l1["items"].append(json.loads(json.dumps(it)))
                back.append(iid)
            continue
        if k == "flash" or (k == "card" and it.get("z", 0) >= 1):
            for w0, w1 in over:
                for lo, hi in _minus(max(a, int(w0)), min(b, int(w1)), blocked):
                    piece = D.slice_item(json.loads(json.dumps(it)), lo, hi)
                    if piece:
                        piece["id"] = f"{iid}r{lo}"
                        if piece["id"] not in have:
                            l1["items"].append(piece)
                            have.add(piece["id"])
                            back.append(piece["id"])
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))
    return back


def repair_misfits(ctx, l1, seen, clock, product_paths, cache, recording, pack, sc, pre=None, hold=(), said=None):
    """Every window the check judged loose or wrong takes the best of the film's own pictures that shows its words, in
    place over the same frames (never dropped, never a type page): the candidates ranked by one look, each tried in
    turn and checked on its own frames only, the first that fits kept. A window none of them fits keeps the best it
    had. An unanswered check is asked again once; then the trial windows go back to their best verified picture (or
    their own) and the film carries on. What the recording shows is shown as delivered after the swaps, both ways:
    pre (the items as built) gives captions, flashes, pool and finish back to a window moved off the recording.
    Returns (record, spend)."""
    roles = roles_of(sc)

    def verdict(p):
        return cache.get(picture_key(p, words_under(clock, *p["f"]))) or {}
    misfits = [p for p in seen.get("pictures") or [] if verdict(p).get("fit") in ("wrong", "loose")]
    if not misfits:
        return dict(misfits=0, repaired=[]), []
    win_s = max((p["f"][1] - p["f"][0]) / F.FPS for p in misfits)
    cands = repair_candidates(pack, sc, {words_line(p) for p in misfits}, l1=l1, said=said, win_s=win_s)
    ranked, spend = choose_replacements(misfits, cands, clock, roles, cache) if cands else ({}, [])
    rank = {"yes": 2, "loose": 1, "wrong": 0}
    pre_by = {str(it.get("id")): it for it in pre or []}
    state = []
    for p in misfits:
        keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") == p["path"]}
        items = [json.loads(json.dumps(it)) for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys
                 and p["f"][0] <= it["f"][0] < p["f"][1]]
        looks = {o["id"]: (pre_by.get(str(o["id"])) or o).get("look") for o in items}
        state.append(dict(orig=dict(p), items=items, looks=looks, best=(rank.get(verdict(p).get("fit"), 0), None, None), tried=[], now=None))
    faults = []
    for attempt in range(REPAIR_TRIES):
        trial = []
        taken = {st["best"][1]["id"] for st in state if st["best"][1] is not None}     # one candidate fills one window
        for n, p in enumerate(misfits):
            st, ids = state[n], ranked.get(n) or []
            if verdict(p).get("fits") or attempt >= len(ids) or not st["items"] or ids[attempt] in taken:
                continue
            c = cands[ids[attempt]]
            taken.add(c["id"])
            src = swap_in(l1, p, st["items"], c, st["looks"])     # over the window's original items, never over the last try
            p.update(path=c["path"], name=c["name"], kind="clip" if c["kind"] == "clip" else "photo", tier="repair",
                     src=src if c["kind"] == "clip" else None)
            st["tried"].append(c["id"])
            st["now"] = c["id"]
            trial.append((n, c, src))
        if not trial:
            break
        _bad, sp, fault = checked(ctx, dict(pictures=[misfits[n] for n, _, _ in trial]), clock, product_paths, cache, recording, roles=roles)
        spend += sp
        for n, c, src in trial:
            r = rank.get(verdict(misfits[n]).get("fit"), 0)
            if r > state[n]["best"][0]:
                state[n]["best"] = (r, c, src)
        if fault:                          # no answer twice: the trial windows go back below, and the film carries on
            faults.append(dict(attempt=attempt, windows=[list(misfits[n]["f"]) for n, _, _ in trial], fault=fault))
            break
    repaired, product_spans, off_spans = [], [], []
    for n, p in enumerate(misfits):
        st = state[n]
        r, c, src = st["best"]
        ids = {o["id"] for o in st["items"]}
        if c is None:                      # nothing the film has carried these words better: the window keeps its picture
            if st["tried"]:
                l1["items"] = [it for it in l1["items"] if it.get("id") not in ids] + [json.loads(json.dumps(o)) for o in st["items"]]
                p.clear()
                p.update(st["orig"])
            continue
        if st["now"] != c["id"]:           # the best try was not the last one
            src = swap_in(l1, p, st["items"], c, st["looks"])
            p.update(path=c["path"], name=c["name"], kind="clip" if c["kind"] == "clip" else "photo", tier="repair",
                     src=src if c["kind"] == "clip" else None)
        repaired.append(dict(frames=list(p["f"]), was=os.path.basename(st["orig"]["path"]), now=os.path.basename(c["path"]),
                             src=p.get("src"), fit={2: "yes", 1: "loose", 0: "wrong"}[r], words=words_under(clock, *p["f"])))
        now_rec = c["path"] in product_paths or c.get("product")
        if now_rec:
            product_spans.append(tuple(p["f"]))
        elif st["orig"]["path"] in product_paths:
            off_spans.append(tuple(p["f"]))
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))
    rec_paths = set(product_paths) | {c["path"] for c in cands if c.get("product")}
    given = shown_off_recording(l1, pre, off_spans, rec_paths, hold) if off_spans else []
    if product_spans:                      # the real recording now plays there: shown as delivered, as everywhere else
        as_delivered(l1, rec_paths, spans=product_spans)
    out = dict(misfits=len(misfits), candidates=len(cands), repaired=repaired)
    if off_spans:
        out["off_recording"] = dict(windows=[list(s) for s in off_spans], given_back=len(given))
    if faults:
        out["faults"] = faults
    return out, spend


def resound(l1, pack, knobs, turn_f, clock, windows):
    """The sound design follows the repaired pictures: a sound planned for what a repaired window showed before goes,
    and the sound the design plans for what it shows now comes in (the same score.sound.design pass, same turn and
    supply, run on the final plan; only the repaired windows take from it). Device sounds stay. Returns a record."""
    if not windows:
        return dict(windows=0)
    rep0 = l1.get("sound_design") or {}

    def inside(s):
        f = int(s.get("event_f", -1))
        return any(a <= f < b for a, b in windows)
    spoken = [(w["t0"] - F.LEAD_F / F.FPS, w["t1"] - F.LEAD_F / F.FPS) for w in clock.words] if clock is not None else None
    fresh, rep = F.design_sound(l1, pack, knobs, turn_f=turn_f, spoken=spoken, np10=rep0.get("np10"))
    if rep.get("fault"):
        return dict(windows=len(windows), fault=rep["fault"])        # the film keeps the sounds it has
    old = l1.get("sound") or []
    gone = [s for s in old if inside(s) and not s.get("device")]
    new = [dict(s, repair=True) for s in fresh if inside(s)]
    l1["sound"] = [s for s in old if not (inside(s) and not s.get("device"))] + new
    l1["sound"].sort(key=lambda s: int(s.get("event_f", 0)))
    return dict(windows=len(windows), dropped=len(gone), added=len(new))


def instruction_panel(l1, clock, sc, typed):
    """The exact submitted sentence remains readable before its real result."""
    i = int(sc["proof"].get("typed", sc["proof"]["after_after"]))
    words = clock.seg_words(i)
    if not words:
        raise RuntimeError("the typed instruction has no spoken timing")
    start, end = F.Clock.f(words[0]["t0"]), F.Clock.f(words[-1]["t1"])
    # the sentence at 64 px under the face; a longer typed line (a follow-up after it) steps down to 46 px and
    # rises, so every typed word stays on screen instead of the panel refusing the film
    for px in (64, 58, 52, 46):
        pitch = round(px * 1.22)
        voices = dict(l1["voices"], receipt=dict(font="InterDisplay-Bold.ttf", px=px, track=0),
                      receipt_label=dict(font="InterDisplay-Bold.ttf", px=56 if px >= 58 else 48, track=1))
        panel = F.Plan(voices, {})
        lines, line = [], []
        for word in typed.split():
            candidate = " ".join(line + [word])
            if line and panel.R.width("receipt", candidate) > 0.74 * F.W:
                lines.append(" ".join(line))
                line = []
            line.append(word)
        if line:
            lines.append(" ".join(line))
        height = (90 + len(lines) * pitch) / F.H
        top = min(0.53, 0.80 - height)
        if top >= 0.30:
            break
    if top < 0.30:
        top = 0.30          # the longest line still shows whole, above the lower third
    bottom = top + height
    panel.block([start, end], [0.08, top, 0.92, bottom], "#121A27", z=20, op=0.96)
    panel.word([start, end], D.TYPED_LABEL, "receipt_label", 0.12, top + 54 / F.H,
               ink="#7FE3FF", z=21)
    for k, line in enumerate(lines):
        panel.word([start, end], line, "receipt", 0.12, top + (126 + k * pitch) / F.H, z=21)
    for it in panel.items:
        it["id"] = "receipt." + it["id"]
    l1["voices"] = voices
    # The exact sentence takes the caption seat; never stack both versions.
    l1["items"] = [it for it in l1["items"] if not (
        it.get("k") == "word" and it["f"][0] < end and it["f"][1] > start)]
    l1["items"].extend(panel.items)
    return dict(typed=typed, frames=[start, end], lines=lines, minimum_px=min(px, voices["receipt_label"]["px"]))


def caption_spans(rec, edl, per=3):
    """The export's caption groups on its own clock: the source's caption words inside each kept range, in the
    export's order, `per` words a group. [(start, end)] programme seconds; [] when the source has no captions."""
    src = next((s for s in sources() if s["file"] == (rec.get("source") or {}).get("file")), None)
    if not src:
        return []
    t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
    words = [t - t0 for t, _ in json3_words(src["captions"]) if t0 <= t <= t1]
    out, at = [], 0.0
    for k in edl.get("keep") or []:
        a, b = float(k[0]), float(k[1])
        ts = [at + t - a for t in words if a <= t < b]
        for j in range(0, len(ts), per):
            g = ts[j:j + per]
            out.append((round(g[0], 3), round(min(g[-1] + 0.35, at + b - a), 3)))
        at += b - a
    return out


def face_focus(ff, path, t, work, fallback):
    """The centre of the largest face in a video at second t, in picture fractions (YuNet on one frame); fallback
    when no face is found."""
    try:
        st = D.stills(ff, path, [t], work, h=720)
        fs = PX.faces(st[0]["path"]) if st else []
    except Exception:  # noqa: BLE001 - a frame that cannot be read keeps the product's own crop centre
        fs = []
    if not fs:
        return fallback
    b = max(fs, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))["box"]
    return round((b[0] + b[2]) / 2, 4), round((b[1] + b[3]) / 2, 4)


def exports_of_upload(rec, roots):
    return [dict(sid=s["sid"], export=s["export"], dur=s.get("dur")) for s in same_upload(rec, roots)]


def lay_devices(ctx, l1, sc, pack, clock, script, knobs):
    """The film's editing devices on its own beats (devices.py): the typed line as the timeline pull-back or the wipe,
    the triptych on the hook, the count-up grid after the export. A device that cannot be laid leaves the reel as it
    was and the run goes on (the typed line then gets the plain instruction panel); the reason is recorded."""
    out = dict(device=device_of(script, knobs), laid=[], faults=[])
    try:
        rec = json.load(open(script["record"]))
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl_b = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
        up = rec["upload"]
        upload = dict(path=up["file"], w=int(up["w"]), h=int(up["h"]), fps=float(up.get("fps") or 30.0), dur=float(up["dur"]))
        edl_a = dict(keep=[[0.0, round(float(up["dur"]), 3)]])        # the upload as it came in: the sentence was typed before any cut
        product_dir = os.path.dirname(pack["product"]["paths"][0])
        body = D.media(pack["ffmpeg"], os.path.join(product_dir, "export_body.mp4"))
        lib = dict(pack.get("sounds") or {})
        lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
        crop = (pack["product"].get("before") or {}).get("crop")
        focus = ((crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2) if crop else (0.5, 0.42)
        after_keys = {k for k, a in (l1.get("assets") or {}).items() if os.path.basename(str(a.get("path"))) == "after.mp4"}
        after_items = sorted((it for it in l1["items"] if it.get("pic") in after_keys and it.get("k") in ("shot", "card")),
                             key=lambda it: it["f"][0])
        if after_items:                     # the reel's own after: its first frame and the export second it shows there
            f_after = int(after_items[0]["f"][0])
            after_end = int(max(it["f"][1] for it in after_items))
            export_at = float(pack["product"]["after"]["t"][0]) + float((after_items[0].get("src") or [0.0])[0])
        else:
            payoff = next(v for v in pack.get("voices") or [] if v.get("person") == "after")
            f_after = F.Clock.f(float(payoff["start"]) + F.LEAD_F / F.FPS)
            after_end = F.Clock.f(float(payoff["end"]) + F.LEAD_F / F.FPS)
            export_at = float(pack["product"]["after"]["t"][0])
        i = int(sc["proof"].get("typed", sc["proof"]["after_after"]))
        words = clock.seg_words(i)
        typed = typed_line(script["receipts"])           # every sentence typed in the session, in order
        wf = D.word_frames(words, typed, F.norm_word)
        f_send = min(max(b for _, b in wf), f_after) if wf else f_after
        f0 = max(F.Clock.f(words[0]["t0"]) - 6, 0) if words else max(f_after - 90, 0)
        work = os.path.join(ctx.supply, "devices")
        # the pull starts on the speaker: the largest face in the upload at the device's first second, measured (YuNet)
        t_first = max(D.src_at(D.program(edl_b)[0], export_at) - (f_after - f0) / F.FPS, 0.0)
        focus = face_focus(pack["ffmpeg"], upload["path"], t_first, work, focus)
        kind = out["device"]
        if kind == "wipe":
            rec_d, f_end = D.wipe(l1, f0=f0, f_send=f_send, f_after=f_after, upload=upload, export=body, edl_b=edl_b, typed=typed,
                                  word_f=wf, export_at=export_at, lib=lib, focus=focus)
        else:
            rec_d, f_end = D.timeline(l1, f0=f0, f_send=f_send, f_after=f_after, upload=upload, export=body, edl_a=edl_a, edl_b=edl_b,
                                      typed=typed, word_f=wf, export_at=export_at, lib=lib, ff=pack["ffmpeg"], work=work, focus=focus,
                                      caption_spans=caption_spans(rec, edl_b), versions=(1, ex.get("edl_version")), ui=ui_shots(rec))
        out["typed_line"] = rec_d["device"]
        out["laid"].append(rec_d)
        ctx.log(f"device {rec_d['device']}: frames {rec_d['frames']}, typed at {rec_d.get('typed_px')} px, {len(rec_d['sounds'])} sounds")
        pw = clock.seg_words(i + 1) if i + 1 < len(sc["segments"]) - 1 else []
        if kind == "wipe" and pw:          # the count-up of the films made from this clip, on the line after the export
            roots = [os.path.dirname(os.path.dirname(script["record"])), os.environ.get("WF2_REUSE_RECORDINGS"),
                     os.path.join(V, ".tmp", "valmera-studio", "records")]
            films = [dict(sid=rec.get("sid"), export=ex["file"], dur=ex["dur"])] + exports_of_upload(rec, roots)
            g0 = max(F.Clock.f(pw[0]["t0"]) - 2, after_end, f_end)
            g1 = min(F.Clock.f(pw[-1]["t1"]) + 4, g0 + 66)
            if len(films) >= 4 and g1 - g0 >= 36:
                tiles = []
                for fm in films[:12]:
                    st = D.stills(pack["ffmpeg"], fm["export"], [max(float(fm.get("dur") or 6.0) - endcard_s(), 1.0) * 0.4], work, h=480)
                    if st:
                        tiles.append(dict(st[0], sid=fm["sid"]))
                g = D.grid(l1, f0=g0, f1=g1, tiles=tiles, lib=lib, upload=upload)
                g["films"] = [dict(sid=fm["sid"], export=os.path.basename(fm["export"])) for fm in films]
                out["laid"].append(g)
        if kind == "triptych" and len(script.get("siblings") or []) >= 2:
            sibs = script["siblings"][:2]
            exs = [dict(media=D.media(pack["ffmpeg"], ex["file"]), edl=edl_b)] + [
                dict(media=D.media(pack["ffmpeg"], s["export"]), edl=json.load(open(s["edl"]))) for s in sibs]
            hw = clock.seg_words(0)
            t1 = max(min((F.Clock.f(hw[-1]["t1"]) + 3) if hw else 75, 90), 45)
            s_at = D.common_second([e["edl"] for e in exs], t1 / F.FPS + 0.2, prefer=D.src_at(D.program(edl_b)[0], export_at), dur=upload["dur"])
            if s_at is not None:
                tr = D.triptych(l1, f0=0, f1=t1, exports=exs, upload=upload, lib=lib, src_s=s_at)
                tr["sentences"] = [typed] + [s["typed"] for s in sibs]
                out["laid"].append(tr)
            else:
                out["faults"].append("triptych: the three exports share no uncut stretch of the upload; the hook stays the reel's")
    except Exception as e:  # noqa: BLE001 - a device never costs the film: the reel stays as built
        out["faults"].append(repr(e)[:400])
        ctx.log(f"device not laid: {repr(e)[:300]}")
    return out


def lay_hook(ctx, l1, pack, script):
    """The tutorial's opening beat (script["hook"], hook_beat): devices.split over the seconds the speaker's own voice
    opens the film (the "hook" voice the supply cut in before the narrator), the clip as uploaded beside Valmera's
    export at the same instant, a hit where the export's picture lands. The raw clip and the export it became, the
    words and the picture agreeing, before the narrator says a word. Returns the device's record, None without a hook
    beat; a split that cannot be laid leaves the export full frame on its voice (the pinned cut-in) and is recorded."""
    h = (pack.get("product") or {}).get("hook")
    v = next((v for v in pack.get("voices") or [] if v.get("person") == "hook"), None)
    if not h or not v:
        return None
    try:
        rec = json.load(open(script["record"]))
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl_b = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
        up = rec["upload"]
        upload = dict(path=up["file"], w=int(up["w"]), h=int(up["h"]), fps=float(up.get("fps") or 30.0), dur=float(up["dur"]))
        body = D.media(pack["ffmpeg"], os.path.join(os.path.dirname(pack["product"]["paths"][0]), "export_body.mp4"))
        lib = dict(pack.get("sounds") or {})
        lib.update(PX.sound_facts(("whoosh_soft_1", "impact_1")))
        at = float(h["at"])                       # the split's picture at frame f shows at + f / FPS; its voice starts on LEAD_F there
        f1 = F.Clock.f(float(v["end"]) + F.LEAD_F / F.FPS)
        land = h.get("landing")
        land_f = int(round((float(land) - at) * F.FPS)) if land is not None else None
        work = os.path.join(ctx.supply, "devices")
        focus = face_focus(pack["ffmpeg"], upload["path"], D.src_at(D.program(edl_b)[0], at), work, (0.5, 0.42))
        out = D.split(l1, f0=0, f1=f1, upload=upload, export=body, edl_b=edl_b, export_at=at, lib=lib, focus=focus, land_f=land_f)
        ctx.log(f"opening beat: split frames {out['frames']}, export from {at} s, lands on frame {land_f}")
        return out
    except Exception as e:  # noqa: BLE001 - the export still opens the film full frame on the speaker's voice
        ctx.log(f"opening split not laid: {repr(e)[:300]}")
        return dict(device="split", fault=repr(e)[:300])


def showcase_score(ctx, script, pack):
    """The showcase's plan: devices.showcase over the recording (no narrator, no picture check: every picture is the
    product's own export, upload, Studio screen or site)."""
    rec = json.load(open(script["record"]))
    up = rec["upload"]
    upload = dict(path=up["file"], w=int(up["w"]), h=int(up["h"]), fps=float(up.get("fps") or 30.0), dur=float(up["dur"]))
    if pack["product"].get("variant") == "scale":
        return scale_score(ctx, script, pack, upload)
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    body = D.media(pack["ffmpeg"], pack["product"]["paths"][0])
    lib = dict(pack.get("sounds") or {})
    lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
    work = os.path.join(ctx.supply, "devices")
    prod = pack["product"]
    focus = face_focus(pack["ffmpeg"], upload["path"], prod["upload_at"], work, (0.5, 0.42))
    ui = prod.get("ui") or showcase_ui(rec)
    if not ui:
        raise RuntimeError("the showcase needs the recording's Studio photographs (studio.py, phone view): record it again")
    page = ui.get("site") or dict(path=os.path.join(ui["dir"], ui["typing"][0]), w=ui["w"], h=ui["h"])
    l1, beats = D.showcase(upload=upload, export=body, edl_b=edl, typed=typed_line(script["receipts"]), window=prod["window"], hook=prod["hook"],
                           ui=ui, page=page, lib=lib, sid=ctx.sid, focus=focus, lead_f=F.LEAD_F, later=prod.get("later"), proof_at=prod.get("proof_at"), caption_proof=prod.get("caption_proof"),
                           marks=prod.get("marks"), before=prod.get("before"), after=prod.get("after"))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    taste = dict(ok=True, problems=[], min_text_px=min(d["min_text_px"] for d in beats["devices"] if d.get("min_text_px")), sounds=len(l1["sound"]))
    devices = dict(device="studio", format="showcase", laid=beats["devices"], faults=[], typed_line="studio")
    return dict(l1=out, l1_audio=l1_audio, payoff_s=round(beats["export"][0] / F.FPS, 3), story_turn_s=round(beats["studio"][0] / F.FPS, 3), beats={k: v for k, v in beats.items() if k != "look"},
                taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=dict(format="showcase"), pace=beats["pace"],
                recording_spans=[span for key in ("before", "after", "stretch") if (span := (beats.get("marks") or {}).get(key)) and span[0] < span[1]] or [beats["export"]], instruction_panel=dict(typed=typed_line(script["receipts"]), device="studio"), devices=devices,
                picture_check=dict(model=None, rounds=[], misfits_left=[], note="showcase: every picture is the recording's own"), spend=[])


def scale_score(ctx, script, pack, upload):
    """The scale film's plan (devices.scale_showcase): every picture is the recording's own (its films, its Studio, its
    site)."""
    prod = pack["product"]
    lib = dict(pack.get("sounds") or {})
    lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
    ui = prod["ui"]
    page = ui.get("site") or dict(path=os.path.join(ui["dir"], ui["typing"][0]), w=ui["w"], h=ui["h"])
    tiles = [dict(t, path=t["path"]) for t in prod["tiles"]]
    hero = dict(path=tiles[0]["path"], w=tiles[0]["w"], h=tiles[0]["h"], fps=tiles[0]["fps"], dur=tiles[0]["dur"])
    l1, beats = D.scale_showcase(upload=upload, hero=hero, tiles=tiles, typed=typed_line(script["receipts"]), window=prod["window"], ui=ui, page=page,
                                 lib=lib, sid=ctx.sid, lead_f=F.LEAD_F, later=prod.get("later"), sub=prod.get("sub"), site_s=prod.get("cta_s"))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    taste = dict(ok=True, problems=[], min_text_px=min(d["min_text_px"] for d in beats["devices"] if d.get("min_text_px")), sounds=len(l1["sound"]))
    return dict(l1=out, l1_audio=l1_audio, payoff_s=round(beats["export"][0] / F.FPS, 3), story_turn_s=round(beats["studio"][0] / F.FPS, 3), beats={k: v for k, v in beats.items() if k != "look"},
                taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=dict(format="showcase", variant="scale"), pace=beats["pace"],
                recording_spans=[span for key in ("before", "after", "stretch") if (span := (beats.get("marks") or {}).get(key)) and span[0] < span[1]] or [beats["export"]], instruction_panel=dict(typed=typed_line(script["receipts"]), device="studio"),
                devices=dict(device="studio", format="showcase", variant="scale", laid=beats["devices"], faults=[], typed_line="studio"),
                picture_check=dict(model=None, rounds=[], misfits_left=[], note="showcase: every picture is the recording's own"), spend=[])


def lay_cta(ctx, l1, sc, pack, clock, script):
    """The call to action on the film's last line, held CTA_S or to the end of the programme: the website itself
    (devices.site, the recording's photograph of valmera.io) when the recording has it, else devices.cta."""
    try:
        end = int(l1["dur_f"])
        f0 = max(0, end - D.card_join_frames())
        body = os.path.join(os.path.dirname(pack["product"]["paths"][0]), "export_body.mp4")
        m = D.media(pack["ffmpeg"], body)
        lib = dict(pack.get("sounds") or {})
        lib.update(PX.sound_facts(("click_1", "whoosh_soft_1", D.CTA_RISER)))
        src0 = max(m["dur"] - (l1["dur_f"] - f0) / F.FPS - 0.2, 0.0)
        record = jload(script.get("record") or "", {})
        ui = ui_shots(record) if record else None
        if ui and ui.get("site"):
            return D.site(l1, f0=f0, f1=end, page=ui["site"], export=m, lib=lib, src0=src0)
        # Keep the moving proof through the card's reveal when the recording has no site.
        return dict(device="cta", frames=[f0,end], lines=[], brand_join=True)
    except Exception as e:  # noqa: BLE001 - the film keeps its spoken call to action
        ctx.log(f"call to action not laid: {repr(e)[:300]}")
        return dict(device="cta", fault=repr(e)[:300])


def upload_reveals(paths, footage=()):
    """The upload files that get the whole-upload reveal (the wide clip on its blur, then the push into the export's
    crop): one per line that takes the upload, its best-ranked shot (the line's first upload file); with no lines known,
    the film's first. The line's other upload shots lie on the board under its narration (20 Nov review: a line with
    demand 6 took five upload shots and would have played the same wide-then-push reveal five times running, every
    caption on it trimmed away)."""
    have = {p for p in paths if os.path.basename(p).startswith("upload_")}
    out = []
    for ln in footage or ():
        first = next((c["path"] for c in ln.get("clips") or [] if c.get("path") in have), None)
        if first and first not in out:
            out.append(first)
    if not out and have:
        out = [min(have, key=lambda p: (len(os.path.basename(p)), os.path.basename(p)))]
    return out


def product_layout(paths, footage=()):
    """(full frame, on the board): the recording's files the reel shows full frame (the before and the after on their
    own voices, the hook's landing, one whole-upload reveal per upload line: upload_reveals) and those it lays as a
    print on the style's board under the narration (the export's shots, its proof fill, a line's other upload shots)."""
    reveal = set(upload_reveals(paths, footage))
    whole = [p for p in paths if os.path.basename(p) in ("before.mp4", "after.mp4", "hook.mp4") or p in reveal]
    return whole, [p for p in paths if p not in whole]


def keep_checked_layout(l1, seen, shown):
    """Keep the scored picture clock and resolve caption contrast against the final composited shot."""
    keys = {asset.get("path"): key for key, asset in l1["assets"].items()}
    checked_cuts = [dict(f=p["f"], path=p["path"], pic=keys[p["path"]], source_slice=p.get("src"))
                    for p, verdict in zip(seen.get("pictures") or [], shown) if verdict.get("fit") == "yes" and p["path"] in keys]
    if checked_cuts:
        l1["documentary_cuts"] = checked_cuts
    for it in l1["items"]:
        if it.get("role") == "cap":
            it["contrast_shadow"] = True
            it.setdefault("stroke", dict(w=1.6, c="#0B0A09"))


def score(ctx, script):
    pack = W3.load_pack(ctx)
    if script["script"].get("format") == "showcase" and not script["script"].get("segments"):
        return showcase_score(ctx, script, pack)
    d = W3.direction_of(ctx)
    sc = json.loads(json.dumps(script["script"]))
    aim, pace_rec = content_pace(pack, sc)
    knobs = {k: v for k, v in d.items() if k not in ("script", "why", "alien")}
    knobs.setdefault("aim_s", aim)
    knobs.setdefault("bed", chain.wants_music(ctx))
    knobs.setdefault("require_fit", True)
    knobs["prefer_new_pictures"] = True
    knobs.setdefault("layout", "bleed")
    # full frame only where the recording speaks for itself (the before and the after on their own voices), the hook's
    # opening beat (hook.mp4) and the whole upload pushing into the export's crop; on the narrated lines the export plays as
    # a print on the style's board under the narration's own captions (20 Nov: every recording clip was a sealed static
    # full-frame plate, 90% of the film, its captions deleted)
    knobs["full_frame_paths"], knobs["board_paths"] = product_layout(pack["product"]["paths"], pack.get("footage") or ())
    knobs.setdefault("collage_any_line", True)          # the style's collage budget takes lines the writer did not mark
    knobs["whole_frame_paths"] = [p for p in pack["product"]["paths"] if os.path.basename(p) == "before.mp4"] + \
        upload_reveals(pack["product"]["paths"], pack.get("footage") or ())
    knobs["whole_frame_crop"] = dict(pack["product"].get("crops") or {})   # the before pushes into the export's own crop
    knobs["proof_fill"] = list(pack.get("proof_fill") or [])               # a line short of pictures plays the export
    knobs.setdefault("cap_px", CAP_PX)
    after_v = next((v for v in pack.get("voices") or [] if v.get("person") == "after"), None)
    if after_v:   # the payoff is the after: the drop lands on its first picture landing (the export's own), else on its arrival
        knobs["turn_span_s"] = [round(float(after_v["start"]) + F.LEAD_F / F.FPS, 3), round(float(after_v["end"]) + F.LEAD_F / F.FPS, 3)]
    knobs.setdefault("tail_f", D.card_join_frames())
    sys.path[:0] = [p for p in W3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    build = R.builder(script["style"]["id"])
    product = set(pack["product"]["paths"])
    clock = F.Clock(pack["words"], sc)
    cache_p = os.path.join(ctx.run_dir, "picture_check.json")
    cache = jload(cache_p, {}) or {}
    drop, spend, rounds = list(knobs.get("drop") or []), [], []
    # One cut. The picture check below reads what plays under which words; a window judged loose or wrong is filled in
    # place with the film's own picture that shows them (repair_misfits). It never drops a picture and rebuilds (16 Nov:
    # four rounds of drops turned 35-47% of two films into flat type pages and still left 25 misfits).
    for rnd in range(1):
        l1, beats = build(sc, pack, dict(knobs, drop=drop))
        taste = F.taste_check(l1, beats)
        if not taste["ok"]:
            for safer in (dict(display=False), dict(display=False, collage=0.0)):
                ctx.log("taste: " + "; ".join(taste["problems"][:4]) + f"; built again with {safer}")
                l1, beats = build(sc, pack, dict(knobs, drop=drop, **safer))
                taste = F.taste_check(l1, beats)
                if taste["ok"]:
                    break
        pre = json.loads(json.dumps(l1["items"]))          # as built: what a window the repair moves off the recording gets back
        spans = as_delivered(l1, product)
        devices = lay_devices(ctx, l1, sc, pack, clock, script, knobs)
        devices["hook"] = lay_hook(ctx, l1, pack, script)
        if (devices["hook"] or {}).get("frames"):
            devices.setdefault("laid", []).insert(0, devices["hook"])
        devices["cta"] = lay_cta(ctx, l1, sc, pack, clock, script)
        if devices["cta"].get("frames"):
            devices.setdefault("laid", []).append(devices["cta"])
        receipt_panel = (instruction_panel(l1, clock, sc, typed_line(script["receipts"])) if not devices.get("typed_line")
                         else dict(typed=typed_line(script["receipts"]), device=devices["typed_line"]))
        spans_dev = [d["frames"] for d in devices.get("laid") or []]
        reserved = list(spans) + spans_dev + ([receipt_panel["frames"]] if receipt_panel.get("frames") else [])
        title_shots = lay_recorded_titles(l1, clock, recorded_titles(script), reserved)
        devices["laid"].extend(title_shots)
        title_spans = [t["frames"] for t in title_shots]
        visible = []
        for pic in beats.get("pictures") or []:
            if any(a <= pic["f"][0] and pic["f"][1] <= b for a, b in spans_dev):
                continue
            for a, b in _minus(*pic["f"], title_spans):
                visible.append(pic if [a, b] == list(pic["f"]) else D.slice_item(pic, a, b))
        for t in title_shots:
            product.add(t["path"])
            visible.append(dict(f=t["frames"], src=t["src"], line=t["line"], path=t["path"], name="THE EXPORT", kind="clip"))
        spans_dev += title_spans
        seen = dict(beats, pictures=sorted(visible, key=lambda p: p["f"][0]))
        bad, sp, fault = checked(ctx, seen, clock, product, cache, script.get("receipts"), roles=roles_of(sc))
        spend += sp
        if fault:
            ctx.log(f"picture check: no answer twice ({fault[:120]}); the unread windows stay unverified and the film carries on")
        # a window judged loose or wrong is filled in place with the film's own picture that shows its words (round1
        # stock-inventory-wrong-picture-kept: a Marine at a laptop shipped under "my edit is still his face", recorded only)
        repair, sp = repair_misfits(ctx, l1, seen, clock, product, cache, script.get("receipts"), pack, sc,
                                    pre=pre, hold=spans_dev + ([receipt_panel["frames"]] if receipt_panel.get("frames") else []),
                                    said=recording_words(script))
        spend += sp
        if fault:
            repair.setdefault("faults", []).insert(0, dict(stage="picture check", fault=fault))
        if repair["repaired"]:
            ctx.log(f"picture repair: {len(repair['repaired'])} of {repair['misfits']} loose or wrong windows took another of the film's pictures")
            # the sound design follows the new pictures (a stale shutter goes, the new picture's landings sound)
            repair["sound"] = resound(l1, pack, dict(knobs, drop=drop), beats.get("turn_f"), clock, [tuple(r["frames"]) for r in repair["repaired"]])
            bad = [(os.path.basename(p["path"]), words_under(clock, *p["f"]), (cache.get(picture_key(p, words_under(clock, *p["f"]))) or
                    dict(why="missing picture verdict")).get("why")) for p in seen.get("pictures") or []
                   if not (cache.get(picture_key(p, words_under(clock, *p["f"]))) or {}).get("fits")]
        jsave(cache_p, cache)
        shown = []
        for picture in seen.get("pictures") or []:
            words = words_under(clock, *picture["f"])
            verdict = cache.get(picture_key(picture, words)) or dict(fits=False, why="missing picture verdict")
            shown.append(dict(file=os.path.basename(picture["path"]), frames=picture["f"], source=picture.get("src"),
                              words=words, **verdict))
        keep_checked_layout(l1, seen, shown)
        rounds.append(dict(round=rnd, pictures=len(shown), shown=shown, repair=repair,
                           misfits=[dict(file=b, words=w, why=y) for b, w, y in bad]))
        ctx.log(f"picture check: {len(bad)} of {len(seen.get('pictures') or [])} judged loose against their words (recorded)")
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    payoff = next((v for v in pack.get("voices") or [] if v.get("person") == "after"), None)
    turn_s = round(float(payoff["start"]) + F.LEAD_F / F.FPS, 3) if payoff else (round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None)
    if beats.get("turn_by") == "landing" and beats.get("turn_f") is not None:
        turn_s = round(beats["turn_f"] / F.FPS, 3)         # the music's drop and the sound's turn on the same landing
    return dict(l1=out, l1_audio=l1_audio, payoff_s=turn_s, beats={k: v for k, v in beats.items() if k != "look"}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction=dict(knobs, drop=drop), pace=pace_rec, recording_spans=spans,
                instruction_panel=receipt_panel, devices=devices,
                picture_check=dict(model=CHECK_MODEL, effort="low", rounds=rounds, misfits_left=rounds[-1]["misfits"]), spend=spend)


def render(ctx, script, sc):
    return W3.render(ctx, script, sc)


def check(ctx, rd, sc):
    r = W3.check(ctx, rd, sc)
    c = r["checks"]
    c["marketing"] = dict(receipts=json.load(open(os.path.join(ctx.run_dir, "script.json"))).get("receipts"),
                          picture_check=sc.get("picture_check"), pace=sc.get("pace"), recording_spans=sc.get("recording_spans"),
                          instruction_panel=sc.get("instruction_panel"), devices=sc.get("devices"))
    left = (sc.get("picture_check") or {}).get("misfits_left") or []
    c["marketing"]["pictures_fit"] = not left
    c["ok"] = bool(c["ok"] and not left)
    return r
