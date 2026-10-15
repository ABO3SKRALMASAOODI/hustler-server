"""Track 1B acceptance machinery (local only: footage library + references).

Builds each AT-1B case as apply_edit_batch operations on a VFL window
(the stale-client path, INTERFACES_W1.md §5), renders the control and the
case LOSSLESSLY through the real renderer with the track-1A stand-in
(``w1b_stub1a``: a source-space RVM matte, ``w1b_footage.rvm_matte``),
measures every bar of the ROADMAP's AT table, and writes 2-up boards
against the named reference moments plus a verdict JSON.

Internal only: references, fixtures, boards and verdicts never leave this
machine (``VALMERA_REFBENCH_OUT``, default the 1B scratch directory).
"""

import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import w1b_footage                                            # noqa: E402
import w1b_probe                                              # noqa: E402

FIX = w1b_footage.FIXTURES
REFS = os.environ.get("VALMERA_REFERENCES_DIR", os.path.expanduser(
    "~/.openclaw/workspace/references"))
OUT = os.environ.get("VALMERA_REFBENCH_OUT", "/private/tmp/claude-501/"
                     "-Users-masaoodi-Documents-Valmera/"
                     "3950ff90-9877-4a92-8ad5-9b6b2ec520c6/scratchpad/"
                     "w1-1b/accept")


# ── footage ─────────────────────────────────────────────────────────────

def window(vfl, name, path=None):
    """A VFL window (or, with ``path``, a house-corpus stand-in file)."""
    path = path or os.path.join(FIX, vfl, f"{name}.mp4")
    mask, info = w1b_footage.rvm_matte(path)
    pr = w1b_footage.probe(path)
    return {"vfl": vfl, "name": name, "path": path, "mask": mask,
            "mask_info": info, "probe": pr, "fps": pr["fps"]}


def words(win):
    """Word timings on the FILE clock: the 0D ASR fixture when there is
    one, else 1B's derived transcript (faster-whisper small.en)."""
    for p, shift in ((os.path.join(FIX, "asr", win["vfl"],
                                   f"{win['name']}.json"), True),
                     (os.path.join(FIX, "derived", "w1b_asr", win["vfl"],
                                   f"{win['name']}.json"), False)):
        if os.path.exists(p):
            data = json.load(open(p))
            off = 0.0
            if shift and data.get("clock", "source") == "source":
                import benchmarks.refbench.vfl as vflmod
                off = float(vflmod.window(win["vfl"], win["name"])
                            ["file_t0"])
            return [{"w": w["w"], "t0": round(float(w["t0"]) - off, 3),
                     "t1": round(float(w["t1"]) - off, 3),
                     "p": w.get("p", 1.0)} for w in data["words"]]
    return []


_PERC = {}


def perception(win):
    if win["path"] not in _PERC:
        import perception as perc
        _PERC[win["path"]] = perc.analyze_audio(win["path"])
    return _PERC[win["path"]]


def index(win, wds=None):
    pr = win["probe"]
    return {"words": wds if wds is not None else words(win),
            "silences": [], "sentences": [],
            "video": {"duration": round(pr["duration"], 3),
                      "fps": pr["fps"], "width": pr["width"],
                      "height": pr["height"]}}


def edl(keep, *, ratio="9:16", focus=None, captions=None):
    """The window's base EDL: ``keep`` on the FILE clock, framed to
    ``ratio`` (None: the source frame). No captions unless asked: they
    would sit over the device and blur its measurement."""
    e = {"keep": [list(k) for k in keep], "captions": captions}
    if ratio:
        e["frame"] = {"ratio": ratio}
        if focus:
            e["frame"]["focus_x"], e["frame"]["focus_y"] = focus
    return e


# A control render carries this: a fill at intensity 0 draws nothing (its
# alpha is 0, the overlay passes the program bit for bit) but puts the
# control on the same normalised frame clock as a render with devices
# (fx programs run CFR at the render rate; a 60p source previewed at 30 fps
# would otherwise drop different frames).
NEUTRAL = {"id": "ctl0", "type": "subject", "stage": "world", "start": 0.0,
           "end": 0.04, "kind": "fill", "colour": "white", "intensity": 0.0}


def case(name, win, base_edl, ops=(), *, preview=False, **kw):
    c = {"name": name, "src": win["path"], "src_fps": win["fps"],
         "src_start": win["probe"].get("start", 0.0),
         "idx": index(win), "edl": base_edl, "matte": win["mask"],
         "preview": preview}
    c["ops"] = list(ops) if ops else [upsert(dict(NEUTRAL))]
    c.update(kw)
    return c


def upsert(item):
    return {"action": "upsert", "layer": "fx", "id": item["id"],
            "value": item}


def run(cases, sub, **kw):
    return w1b_probe.run(cases, os.path.join(OUT, "renders", sub), **kw)


# ── moments (the verb's own solver, on the window) ─────────────────────

def stresses(win, keep, count=3, min_gap=1.0):
    """(program t, word index, strength) of the strongest stresses, solved
    the way add_subject_effect(on='stress') solves them."""
    import prosody_grid
    from timeline import Timeline
    from tools_ext import subject_tools as st
    wds = words(win)
    rmap = prosody_grid.build(wds, perception(win))
    tl = Timeline([list(k) for k in keep])
    by_t0 = {round(float(w["t0"]), 4): i for i, w in enumerate(wds)}
    moments = []
    for o in rmap["onsets"]:
        if o["cls"] != "stress":
            continue
        i = by_t0.get(round(float(o["t"]), 4))
        t = tl.src_to_out(float(o["t"]))
        if i is not None and t is not None:
            moments.append((t, i, float(o["strength"])))
    return st._pick(moments, 0.0, tl.out_duration, count, min_gap), wds


def frame_of(t, fps):
    return int(math.floor(float(t) * round(fps, 3) + 0.5 + 1e-9))


def span(frame, n, fps):
    f3 = round(fps, 3)
    return round(frame / f3, 2), round((frame + n) / f3, 2)


# ── frames and mattes in program space ──────────────────────────────────

def frames_rgb(path, frames):
    """{frame: (H, W, 3) int16} for the requested frame indexes."""
    import qa_metrics
    idx, fr = qa_metrics.read_frames(path, frames=set(frames))
    return {i: f.astype(np.int16) for i, f in zip(idx, fr)}


def colour_space(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams",
                          "v:0", "-show_entries", "stream=color_space",
                          "-of", "csv=p=0", path], capture_output=True,
                         text=True).stdout.strip()
    return out or "unknown"


def yuv_of(rgb, space):
    """TV-range Y'CbCr of an sRGB triple in the render's own matrix (an
    untagged render reads as BT.601, as libavfilter negotiates it)."""
    import subject_fx
    return subject_fx.rgb_to_yuv(rgb, 1080 if space == "bt709" else 480)


def frames_yuv(path, frames, W, H):
    out = {}
    for k in frames:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-vf",
             f"select=eq(n\\,{int(k)})", "-frames:v", "1", "-pix_fmt",
             "yuv420p", "-f", "rawvideo", "-"], capture_output=True,
            check=True).stdout
        a = np.frombuffer(raw, np.uint8).astype(np.int16)
        y = a[:W * H].reshape(H, W)
        u = a[W * H:W * H + W * H // 4].reshape(H // 2, W // 2)
        v = a[W * H + W * H // 4:].reshape(H // 2, W // 2)
        out[k] = (y, u, v)
    return out


def program_matte(win, base_edl, W, H, fps, dest):
    """The program-space matte the stand-in feeds the compiler, as a
    lossless gray video (same keep spans, frame fit and rate)."""
    import hashlib
    key = hashlib.sha256(json.dumps(
        [win["mask"], base_edl.get("keep"), base_edl.get("frame"), W, H,
         round(fps, 3), win["probe"].get("start", 0.0), "pm2"],
        sort_keys=True).encode()).hexdigest()[:12]
    root, ext = os.path.splitext(dest)
    dest = f"{root}.{key}{ext}"
    if os.path.exists(dest):
        return dest
    import renderer
    fr = base_edl.get("frame") or {}
    focus = None
    if fr.get("focus_x") is not None:
        focus = (fr.get("focus_x"), fr.get("focus_y"))
    fit = renderer.frame_fit_filter("crop", W, H, focus) \
        if fr.get("ratio") else f"scale={W}:{H}"
    import w1b_stub1a
    sfps, st = win["fps"], win["probe"].get("start", 0.0)
    parts, labels = [], []
    for i, (a, b) in enumerate(base_edl["keep"]):
        parts.append(f"[0:v]trim=start_frame="
                     f"{w1b_stub1a._first_frame(a, sfps, st)}:end_frame="
                     f"{w1b_stub1a._first_frame(b, sfps, st)},"
                     f"setpts=PTS-STARTPTS[k{i}]")
        labels.append(f"[k{i}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,"
                 f"fps={round(fps, 3):.3f},format=yuv420p,{fit},"
                 f"scale={W}:{H},format=gray[o]")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", win["mask"],
                    "-filter_complex", ";".join(parts), "-map", "[o]",
                    "-c:v", "ffv1", dest], check=True)
    return dest


def matte_frames(path, frames):
    import qa_metrics
    idx, fr = qa_metrics.read_frames(path, frames=set(frames), gray=True)
    return {i: f.astype(np.int16) for i, f in zip(idx, fr)}


def grow(b, r):
    import cv2
    if r <= 0:
        return b.copy()
    k = np.ones((3, 3), np.uint8)
    return cv2.dilate(b.astype(np.uint8), k, iterations=int(r)) > 0


def shrink(b, r):
    import cv2
    if r <= 0:
        return b.copy()
    k = np.ones((3, 3), np.uint8)
    return cv2.erode(b.astype(np.uint8), k, iterations=int(r)) > 0


def centroid(b):
    ys, xs = np.nonzero(b)
    if not len(xs):
        return None
    return float(xs.mean()), float(ys.mean())


# ── boards (internal: they show reference frames) ───────────────────────

def ref_path(ref):
    for f in sorted(os.listdir(REFS)):
        if f.startswith(f"{ref}_") and f.endswith(".mp4"):
            return os.path.join(REFS, f)
    raise FileNotFoundError(ref)


def board(at, ours, t_ours, ref, t_ref, footer=(), tag=""):
    """A 2-up still: the reference at ``t_ref`` seconds, ours at ``t_ours``
    — an int is an exact FRAME index (a seek by seconds can land a frame
    off, and a 1-frame device then shows its neighbour: AT-1B-1's board
    showed a decay frame, not the hit)."""
    from benchmarks.refbench import boards
    out = os.path.join(OUT, "boards", f"{at}{tag}.png")
    if not isinstance(t_ours, int):
        return boards.still_2up((ref_path(ref), t_ref, f"ref {ref}"),
                                (ours, t_ours, f"Valmera {at}"), out,
                                height=720, title=f"{at} vs {ref}@{t_ref:g}",
                                footer=footer)
    from PIL import Image
    height = 720
    a = boards.grab(ref_path(ref), t_ref, height)
    b = grab_frame(ours, t_ours, height)
    gap, top = 24, 70
    bottom = 40 + 30 * max(1, len(footer))
    img = Image.new("RGB", (a.shape[1] + b.shape[1] + 3 * gap,
                            top + height + bottom), boards.BG)
    img.paste(Image.fromarray(a), (gap, top))
    img.paste(Image.fromarray(b), (2 * gap + a.shape[1], top))
    boards._caption(img, [f"{at} vs {ref}@{t_ref:g}"], 12, x=gap, size=26,
                    color=boards.ACCENT)
    boards._caption(img, [f"ref {ref}  @{t_ref:.2f}s"], 44, x=gap, size=18)
    boards._caption(img, [f"Valmera {at}  frame {t_ours}"], 44,
                    x=2 * gap + a.shape[1], size=18)
    boards._caption(img, list(footer), top + height + 14, x=gap, size=20)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    return out


def grab_frame(path, frame, height):
    """Frame number ``frame`` of ``path`` (exact, by index), RGB."""
    import qa_metrics
    info = qa_metrics.probe(path)
    w = max(2, int(round(info["width"] * height / info["height"] / 2)) * 2)
    idx, fr = qa_metrics.read_frames(path, size=(w, height),
                                     frames={int(frame)})
    return fr[0] if fr else np.zeros((height, w, 3), np.uint8)


def sheet(at, items, tag="", height=640):
    """A row of frames: [(path, t, label)] (a float t is seconds, seeked;
    an int is an exact frame index), references or ours."""
    from PIL import Image
    from benchmarks.refbench import boards
    imgs = [((grab_frame(p, t, height) if isinstance(t, int)
              else boards.grab(p, t, height)), lab) for p, t, lab in items]
    gap, top = 16, 46
    W = sum(i.shape[1] for i, _ in imgs) + gap * (len(imgs) + 1)
    img = Image.new("RGB", (W, height + top + 10), boards.BG)
    x = gap
    for arr, lab in imgs:
        img.paste(Image.fromarray(arr), (x, top))
        boards._caption(img, [lab], 12, x=x, size=18)
        x += arr.shape[1] + gap
    out = os.path.join(OUT, "boards", f"{at}{tag}_sheet.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    return out


def write_verdict(at, data):
    p = os.path.join(OUT, "verdicts", f"{at}.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(data, fh, indent=1, default=str)
    return p
