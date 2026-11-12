"""E7: closed-GOP segments (DESIGN 4.10).

Boundaries fall at shots, chosen by a hash of the shot id, 1.5 to 4 s apart, so a ripple edit leaves
later boundaries, and later hashes, where they were. A segment's hash covers everything that decides
its pixels: its slice of the plan on its own clock, the sources' identities, the shaders and the
runtime version. Rendered segments are kept by hash, verified, and joined by concat with stream copy.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

from .faults import SceneFault
from .plan import ANIM, ANIM_RECT, canon

MIN_S, MAX_S = 1.5, 4.0
# what an item carries for logs and lowering, not for pixels: left out of a segment's hash (``on`` is
# an absolute frame, and would move every hash after a ripple)
NOT_DRAWN = ("f", "on", "chunk", "role")
CAP_BYTES = 20 * 1024 ** 3
MAX_AGE_S = 7 * 86400
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
FP = os.environ.get("FFPROBE_BIN", "ffprobe")


def _h(s):
    return hashlib.sha256(str(s).encode()).hexdigest()


def cut(d):
    """[{i, f:[a,b], key}] covering 0..dur_f. ``key`` names the boundary by what starts there: a shot
    id, or a shot id and an offset when a long shot has to be cut inside."""
    fps, dur = float(d["fps"]), int(d["dur_f"])
    lo, hi = max(int(round(MIN_S * fps)), 1), max(int(round(MAX_S * fps)), 2)
    shots = sorted((i for i in d["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    start_of = {s["f"][0]: str(s["id"]) for s in shots}

    def shot_at(f):
        for s in shots:
            if s["f"][0] <= f < s["f"][1]:
                return s
        return None

    def key_at(f):
        if f in start_of:
            return start_of[f]
        s = shot_at(f)
        return f"{s['id']}+{f - s['f'][0]}" if s else f"@{f}"

    bounds = [0]
    at = 0
    while dur - at > hi:
        window = [c for c in start_of if at + lo <= c <= at + hi and dur - c >= lo]
        if window:
            pick = min(window, key=lambda c: (_h(start_of[c]), c))
        else:
            pick = at + hi if dur - (at + hi) >= lo else at + (dur - at) // 2
        bounds.append(pick)
        at = pick
    bounds.append(dur)
    return [{"i": i, "f": [a, b], "key": key_at(a)} for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]


def _slice(v, lo, hi, f0):
    """The part of a per-frame list a segment can see, plus one sample either side: a word knows it
    is at rest by looking at its neighbours."""
    if isinstance(v, list) and v and (isinstance(v[0], list) or not isinstance(v[0], dict)):
        a, b = max(lo - 1 - f0, 0), max(hi + 1 - f0, 0)
        return v[a:b]
    return v


def stamp(d):
    """Hash each segment of a compiled plan document, in place."""
    for s in d["segments"]:
        a, b = s["f"]
        its, pics, voices, masks, under = [], set(), set(), set(), False
        for it in d["items"]:
            lo, hi = max(it["f"][0], a), min(it["f"][1], b)
            if hi <= lo:
                continue
            o = {k: v for k, v in it.items() if k not in NOT_DRAWN}
            o["f"] = [it["f"][0] - a, it["f"][1] - a]
            for k in ANIM.get(it["k"], ()):
                if isinstance(o.get(k), list):
                    o[k] = _slice(o[k], lo, hi, it["f"][0])
            for k in ANIM_RECT.get(it["k"], ()):
                if isinstance(o.get(k), list) and o[k] and isinstance(o[k][0], list):
                    o[k] = _slice(o[k], lo, hi, it["f"][0])
            if isinstance(o.get("fill"), dict):
                pics.add(o["fill"].get("pic"))
                if isinstance(o["fill"].get("crop"), list) and o["fill"]["crop"] and isinstance(o["fill"]["crop"][0], list):
                    o["fill"] = dict(o["fill"], crop=_slice(o["fill"]["crop"], lo, hi, it["f"][0]))
            pics.add(it.get("pic"))
            if it["k"] == "word" or it.get("v"):
                voices.add(it.get("v"))
            if it.get("mask"):
                masks.add(it["mask"])
            under = under or bool(it.get("behind"))
            its.append(o)
        if under:
            pics.add("matte")
        assets = {k: {x: v.get(x) for x in ("sha", "rate", "w", "h", "kind")} for k, v in d["assets"].items() if k in pics}
        blob = canon({"engine": d["engine"], "shaders": d["shaders"], "fps": d["fps"], "frame": d["frame"], "seed": d["seed"],
                      "film": d["film"], "n": b - a, "key": s["key"], "items": its, "assets": assets,
                      "voices": {k: d["voices"].get(k) for k in sorted(v for v in voices if v)}, "masks": sorted(masks)})
        s["sha"] = hashlib.sha256(blob.encode()).hexdigest()[:32]
        s["matte"] = under
        s["hook"] = s["i"] == 0
    return d["segments"]


def order(segs):
    """Render order: the hook first, then by time, then the segments that wait for a matte."""
    return sorted(segs, key=lambda s: (bool(s.get("matte")) and not s.get("hook"), s["i"]))


# ------------------------------------------------------------------ the cache

def cache_dir():
    """VALMERA_SCENE_CACHE, else the box's cache folder, else this user's, else a temp folder."""
    home = os.path.expanduser("~/Library/Caches") if sys.platform == "darwin" else os.path.expanduser("~/.cache")
    for d in (os.environ.get("VALMERA_SCENE_CACHE"), "/var/lib/valmera/cache/scene", os.path.join(home, "valmera", "scene"),
              os.path.join(tempfile.gettempdir(), "valmera-scene")):
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            continue
    raise SceneFault("segment", "no writable folder for the segment cache")


def seg_path(cache, sha, enc):
    name = hashlib.sha256(f"{sha}|{enc}".encode()).hexdigest()[:40]
    return os.path.join(cache, "seg", name[:2], name + ".mp4")


def have(path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            os.utime(path, None)            # used now: the age-out counts from the last use
        except OSError:
            pass
        return True
    return False


def keep(tmp, path):
    """Move a verified segment into the cache. Atomic, so a killed render leaves whole files only."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.replace(tmp, path)


def sweep(cache=None, cap=CAP_BYTES, max_age_s=MAX_AGE_S, now=None):
    """Age-out, then a size cap: oldest use goes first. Returns (files removed, bytes left)."""
    root = os.path.join(cache or cache_dir(), "seg")
    now = time.time() if now is None else now
    files = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    files.sort()
    total, gone = sum(s for _, s, _ in files), 0
    for m, size, p in files:
        if now - m > max_age_s or total > cap:
            try:
                os.remove(p)
                total -= size
                gone += 1
            except OSError:
                pass
    return gone, total


# ------------------------------------------------------------------ verify and join

def count(path):
    """(frames, seconds) of a video file, from its packets."""
    r = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets,duration", "-of", "json", path], capture_output=True, text=True, timeout=120)
    try:
        s = json.loads(r.stdout)["streams"][0]
        return int(s["nb_read_packets"]), float(s.get("duration") or 0)
    except (KeyError, IndexError, ValueError, TypeError):
        return 0, 0.0


def verify(path, n, fps, mean_out=None, mean_src=None, planned_dark=False):
    """A segment is whole: its frame count, its length, and not black unless the plan is dark there
    or the sources were. Raises SceneFault('segment')."""
    got, dur = count(path)
    if got != n:
        raise SceneFault("segment", f"{os.path.basename(path)}: {got} frames, planned {n}")
    if abs(dur - n / fps) > 1.5 / fps:
        raise SceneFault("segment", f"{os.path.basename(path)}: {dur:.3f} s, planned {n / fps:.3f} s")
    if mean_out is not None and mean_out < 3.0 and not planned_dark and (mean_src is None or mean_src > 12.0):
        raise SceneFault("segment", f"{os.path.basename(path)}: black where neither the plan nor the source is")


def concat(paths, out):
    """Join by concat with stream copy. The segments share one encoder setup and each starts on a
    key frame, so nothing is re-encoded and no frame is lost."""
    lst = out + ".concat.txt"
    with open(lst, "w") as f:
        for p in paths:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    try:
        r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c", "copy", "-movflags", "+faststart", out], capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise SceneFault("segment", f"concat failed: {r.stderr[-300:]}")
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass
    return out
