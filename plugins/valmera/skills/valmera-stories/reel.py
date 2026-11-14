"""Story reels: a narrated true story cut the way the @metromedia.house reels are cut (week-1112/judge/metromedia/TEARDOWN.md).

The owner rated our first story films 4-5 of 10 (lanes/RATINGS/2026-11-13-O.json, 2026-11-13-PROOF.json). Their gaps, and
what this builder does about each:

  too few pictures      a new picture about every 0.6 s (the judge: 16 cuts a 10 s, median shot 0.49 s): each line's real
                        footage first, then photographs of what it names, then footage of the same thing found for other
                        lines, then a clip cut inside itself on an unplayed part of its range
  one portrait reused   a photograph is shown once; a second time only twelve seconds later, on another part of it, and never
                        a third time; a clip's seconds are never played twice
  text-only stretches   every frame has a real picture under it; type sits on pictures
  narrator over a face  a clip of a person speaking on camera (their mouth moves, measured on the clip's own frames) never
                        plays under the narrator: the person speaks in their own voice (a real voice cut in) or not at all
  no collages, flashes, section finishes
                        lines that name several things land as tilted prints stacking over each other (three and more layers);
                        a flash frame opens each turn of the story (negative into the low point, white into the payoff); each
                        section carries the finish of the years it speaks from (black and white with a film gate for the
                        archive years, scanlines for the television years, colour for today; the low point in black and white)

Then the engines, as every film: the judge's pace and camera moves (scene.pace), one look solved on the film's own pictures
(scene.looks), a soft first frame on one new picture in three, captions inked word by word at one seat, the writer's display
strings landing large on the words that say them, the references' sounds on what lands (score.sound). The music is laid after
the render by the shell (MUSIC2). The four styles differ in how a picture is shown and in type and colour:

  reel       pictures fill the phone; yellow accent, heavy grotesk display words
  archive    the past in black and white with grain and a film gate, a projector flash into each run; cream serif italic
  window     each picture in a framed window over itself out of focus (three layers); cyan accent, condensed capitals
  pinboard   most lines land as prints pinned over each other on a dark board; orange accent, high-contrast serif

No model is read here: which segment is a turn, which year it speaks from and what it displays are the writer's
(writer.STYLE_DATA). Faces, mouths, stress and loudness are measured.
"""
import json
import math
import os
import subprocess

import film as F

FPS, W, H, LEAD_F = F.FPS, F.W, F.H, F.LEAD_F
Clock, r4, smooth = F.Clock, F.r4, F.smooth

STYLES = {
    "reel": dict(name="Story Reel", layout="bleed", ink="#F4EFE6", accent="#F2C14E", look="story_low",
                 dsp=dict(font="InterDisplay-Black.ttf", px=150, track=-2), upper=True, grain=0.014, vignette=0.40,
                 finish="era", collage=0.26, aim_s=0.6, cap_y=0.615, flash="sections"),
    "archive": dict(name="Archive", layout="bleed", ink="#F2EDE4", accent="#E9D8A6", look="story_low",
                    dsp=dict(font="DMSerifDisplay-Italic.ttf", px=150, track=0), upper=False, grain=0.03, vignette=0.46,
                    finish="past", collage=0.22, aim_s=0.65, cap_y=0.615, flash="runs"),
    "window": dict(name="Window", layout="window", ink="#F4F6F8", accent="#7FE3FF", look="story_cold",
                   dsp=dict(font="Anton-Regular.ttf", px=160, track=1), upper=True, grain=0.012, vignette=0.36,
                   finish="era", collage=0.22, aim_s=0.66, cap_y=0.665, flash="sections"),
    "pinboard": dict(name="Pinboard", layout="bleed", ink="#F6F1E7", accent="#FF7A3D", look="story_low",
                     dsp=dict(font="PlayfairDisplay-Black.ttf", px=128, track=-1), upper=False, grain=0.02, vignette=0.38,
                     finish="era", collage=0.55, aim_s=0.66, cap_y=0.675, flash="sections", paper=True),
}
ORDER = ["reel", "pinboard", "archive", "window"]

WINDOW_BOX = (0.04, 0.205, 0.96, 0.593)
PRINT_W = 0.60
PRINT_ROT = (-2.8, 2.1, -1.4, 2.6, -2.2)
PRINT_AT = ((0.36, 0.385), (0.64, 0.445), (0.40, 0.470), (0.62, 0.380), (0.50, 0.430))
BED = dict(lift=0, gamma=1.0, gain=0.34, sat=0.6, contrast=1, temp=0, tint=0)
DIM = dict(lift=0, gamma=1.0, gain=0.46, sat=0.8, contrast=1, temp=0, tint=0)
REUSE_AFTER_S = 12.0
MAX_USES = 2
TALK_FACE_H = 0.12         # a face smaller than this share of the frame's height does not read as speaking (see talking())
TALK_SCORE = 0.75          # lip-gap change against 0.02 (median) or 0.03 (p80); on FedEx's 16 face clips a closed, posing mouth
                           # read 0.41, interview close-ups 0.83-4.3 (14 Nov)


# ----------------------------------------------------------------- a person speaking on camera, by signal

def talking(c, ffmpeg=None):
    """True when the clip shows a person speaking on camera: a face of at least TALK_FACE_H of the frame's height whose
    lips part and close. Per frame (15 a second) YuNet finds the face and its mouth corners; the darkest row between the
    lips against the face's median tone is the lip gap; speech is that gap changing from frame to frame (score: the median
    change over 0.02 or its 80th percentile over 0.03, whichever is larger; TALK_SCORE or more speaks). (bool, score)"""
    import cv2
    import numpy as np
    path = c.get("path")
    if not path or not os.path.exists(path):
        return False, 0.0
    ff = ffmpeg or os.environ.get("SHELL_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"
    w = 480
    try:
        h = int(round(w * int(c.get("h") or H) / max(int(c.get("w") or W), 1) / 2)) * 2
        r = subprocess.run([ff, "-v", "error", "-i", path, "-vf", f"fps=15,scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           capture_output=True, timeout=60)
        n = len(r.stdout) // (w * h * 3)
        if n < 5:
            return False, 0.0
        frames = np.frombuffer(r.stdout[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    except Exception:  # noqa: BLE001
        return False, 0.0
    yunet = os.environ.get("YUNET_MODEL", "/Users/masaoodi/Documents/Valmera/.valmera/models/face_detection_yunet_2023mar.onnx")
    det = cv2.FaceDetectorYN.create(yunet, "", (w, h), 0.6, 0.3, 50)
    gap, sizes = [], []
    for fr in frames:
        _, f = det.detect(fr)
        if f is None or not len(f):
            continue
        b = max(f, key=lambda q: q[2] * q[3])
        sizes.append(b[3] / h)
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        nx, ny = b[8:10]
        rmx, rmy, lmx, lmy = b[10:14]
        my, mw = (rmy + lmy) / 2, abs(lmx - rmx)
        y0, y1 = int(ny + 0.45 * (my - ny)), int(my + 0.45 * (my - ny) + 0.15 * mw)
        x0, x1 = int(min(rmx, lmx) + 0.15 * mw), int(max(rmx, lmx) - 0.15 * mw)
        m = g[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)]
        face = g[max(int(b[1]), 0):int(b[1] + b[3]), max(int(b[0]), 0):int(b[0] + b[2])]
        if m.size < 20 or face.size < 100:
            continue
        med = max(float(np.median(face)), 1.0)
        gap.append((med - float(np.sort(m.mean(1))[0])) / med)
    if len(gap) < 5 or len(sizes) < 0.6 * len(frames) or float(np.median(sizes)) < TALK_FACE_H:
        return False, 0.0
    d = np.abs(np.diff(np.array(gap)))
    mad, p80 = float(np.median(d)), float(np.percentile(d, 80))
    score = max(mad / 0.02, p80 / 0.03)
    return bool(score >= TALK_SCORE), round(score, 2)


# ----------------------------------------------------------------- the pictures, never twice

class Supply:
    """What is left to show: photographs by name, clips by line, each clip's seconds already played."""

    def __init__(self, pics, footage, talk=(), allow=()):
        self.pics = pics
        self.footage = footage
        self.talk = set(talk)      # clips of a person speaking on camera: only ever a print in a collage line (never full frame
        self.allow = set(allow)    # under the narrator); allow: those lines
        self.uses = {}             # photo path -> [frame shown]
        self.played = {}           # clip path -> [(t0, t1)] seconds of the clip already on screen
        self.shown_at = {}         # clip path -> [frame] each time it was cut in
        self.subject = {}
        for ln in footage.values():
            for c in ln:
                self.subject[c["path"]] = c.get("subject")

    def photo(self, name, f, last_path=None):
        """An unused photograph of `name`, else one shown once at least REUSE_AFTER_S ago (on another part of it)."""
        lst = self.pics.get(name) or []
        fresh = [r for r in lst if not self.uses.get(r["path"]) and r["path"] != last_path]
        if fresh:
            return fresh[0], 0
        old = [r for r in lst if len(self.uses.get(r["path"]) or []) < MAX_USES and r["path"] != last_path
               and f - max(self.uses[r["path"]]) >= REUSE_AFTER_S * FPS]
        if old:
            return min(old, key=lambda r: max(self.uses[r["path"]])), len(self.uses[old[0]["path"]])
        return None, 0

    def take_photo(self, rec, f):
        self.uses.setdefault(rec["path"], []).append(f)

    def clip_room(self, c, n_s):
        """The part of a clip a shot of n_s seconds may play: never seconds already played. (t0, t1) or None."""
        dur = float(c["seconds"])
        took = sorted(self.played.get(c["path"]) or [])
        if not took:
            if n_s >= dur:
                return 0.0, dur
            t0 = max((dur - n_s) / 2, 0.0)
            return t0, t0 + n_s
        free, at = [], 0.0
        for a, b in took:
            if a - at >= 0.4:
                free.append((at, a))
            at = max(at, b)
        if dur - at >= 0.4:
            free.append((at, dur))
        if not free:
            return None
        a, b = max(free, key=lambda q: q[1] - q[0])
        if b - a < 0.4:
            return None
        return (a, min(b, a + n_s))

    def take_clip(self, c, t0, t1, f=None):
        self.played.setdefault(c["path"], []).append((t0, t1))
        if f is not None:
            self.shown_at.setdefault(c["path"], []).append(f)

    def replay(self, f, line, last_path=None, after_s=REUSE_AFTER_S, uses=MAX_USES):
        """The last resort before a hold: a clip already shown, at least after_s ago, shown again (fewer than `uses`)."""
        cands = [c for ln in self.footage.values() for c in ln if self.ok(c, line) and c["path"] != last_path
                 and 0 < len(self.shown_at.get(c["path"]) or []) < uses and f - max(self.shown_at[c["path"]]) >= after_s * FPS]
        return min(cands, key=lambda c: (len(self.shown_at[c["path"]]), max(self.shown_at[c["path"]]))) if cands else None

    def ok(self, c, line):
        return c["path"] not in self.talk or line in self.allow

    def clips(self, line, name=None, fresh_only=True):
        out = []
        for c in self.footage.get(line) or []:
            if name is not None and c.get("subject") != name or not self.ok(c, line):
                continue
            if fresh_only and self.played.get(c["path"]):
                continue
            out.append(c)
        return out

    def spare(self, name, line):
        """Clips of `name` found for any line and not yet played."""
        return [c for ln in self.footage.values() for c in ln if c.get("subject") == name and not self.played.get(c["path"]) and self.ok(c, line)]

    def inner(self, name, n_s, line):
        """Clips of `name` already played that still have unplayed seconds: the clip cut inside itself."""
        out = []
        for ln in self.footage.values():
            for c in ln:
                if c.get("subject") == name and self.played.get(c["path"]) and self.ok(c, line) and self.clip_room(c, n_s):
                    out.append(c)
        return out


def cut_story(script, clock, sp, f_end, stress, aim_s=0.6, lo_s=0.4, hi_s=1.25, slow=0.8):
    """[[f0, f1, kind, name, rec]]: the film's base pictures, a new one about every aim_s seconds, never one twice in a row,
    a photograph never twice within REUSE_AFTER_S. A thing's pictures begin with the clause that says its name; inside a
    thing's share its clips play first, then its photographs, then its other footage; cuts land on the voice's stress."""
    lo, hi, aim = int(round(lo_s * FPS)), int(round(hi_s * FPS)), aim_s * FPS
    out = []
    nseg = len(script["segments"])
    names_all = list(dict.fromkeys([n for s in script["segments"] for n in s.get("pictures") or [] if sp.pics.get(n)]
                                   + [c.get("subject") for ln in sp.footage.values() for c in ln if c.get("subject")]))

    def last_path():
        return out[-1][4]["path"] if out else None

    def next_source(name, pos, n_frames, others):
        """The next picture for `name` at frame pos: (kind, name, rec) or None."""
        n_s = n_frames / FPS
        for c in sp.clips(line_i, name):
            return "clip", name, c
        rec, _ = sp.photo(name, pos, last_path())
        if rec is not None and not sp.uses.get(rec["path"]):
            return "photo", name, rec
        for c in sp.spare(name, line_i):
            return "clip", name, c
        for c in sp.inner(name, n_s, line_i):
            if c["path"] != last_path():
                return "clip", name, c
        for o in others:
            for c in sp.clips(line_i, o) + sp.spare(o, line_i):
                return "clip", o, c
            r2, _ = sp.photo(o, pos, last_path())
            if r2 is not None and not sp.uses.get(r2["path"]):
                return "photo", o, r2
        if rec is not None:
            return "photo", name, rec          # shown once, long ago: another part of it
        for o in others:
            for c in sp.inner(o, n_s, line_i):
                if c["path"] != last_path():
                    return "clip", o, c
        # the supply is spent: a photograph of anything this film shows, shown once long ago (another part of it)
        for o in [name] + list(others):
            r3, _ = sp.photo(o, pos, last_path())
            if r3 is not None:
                return "photo", o, r3
        # a picture held for seconds is worse than one seen again: a clip a second time, then (thin supply) a third
        c = sp.replay(pos, line_i, last_path()) or sp.replay(pos, line_i, last_path(), after_s=6.0, uses=MAX_USES + 1)
        if c is not None:
            return "replay", c.get("subject") or name, c
        return None

    def lay(a, b, name, others):
        pos = a
        while pos < b - 1:
            left = b - pos
            if left < lo + 2 and out:
                out[-1][1] = b
                return
            want_n = max(1, int(round(left / aim)))
            tgt = pos + left / want_n
            src = next_source(name, pos, int(min(max(tgt - pos, lo), hi)), others)
            if src is None:
                if out:
                    out[-1][1] = b         # nothing new to show: the last picture runs on (a clip plays on, never freezes)
                return
            kind, nm, rec = src
            replay = kind == "replay"
            kind = "clip" if replay else kind
            cap = int(float(rec["seconds"]) * FPS / slow) if kind == "clip" else hi
            room = None
            if replay:
                room = (0.0, min(float(rec["seconds"]), hi / FPS))
            elif kind == "clip":
                room = sp.clip_room(rec, min(cap, hi) / FPS)
                if room is None:
                    sp.take_clip(rec, 0.0, float(rec["seconds"]))
                    continue
                cap = min(cap, int((room[1] - room[0]) * FPS / slow))
            w0 = pos + lo
            w1 = min(pos + min(cap, hi), b)
            if w1 <= w0:
                end = min(pos + max(cap, 3), b)
            else:
                cands = [(st - abs(f - tgt) / FPS * 0.8, -f) for f, st in stress if w0 <= f <= w1]
                end = -max(cands)[1] if cands else int(round(min(max(tgt, w0), w1)))
            if b - end < lo:
                end = b if b - pos <= cap + lo else end
            if kind == "clip":
                n_s = (end - pos) / FPS
                t0 = room[0]
                t1 = min(t0 + n_s, float(rec["seconds"]))
                if t1 - t0 < n_s * slow - 1e-3:
                    end = pos + int((t1 - t0) * FPS / slow)
                sp.take_clip(rec, t0, t1, pos)
                rec = dict(rec, src=[round(t0, 3), round(t1, 3)], use=len(sp.shown_at.get(rec["path"]) or []))
            else:
                sp.take_photo(rec, pos)
                k = len(sp.uses[rec["path"]])
                rec = dict(rec, use=k)
            if end <= pos:
                end = pos + 2
            out.append([pos, end, kind, nm, rec])
            pos = end

    for line_i, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[line_i][0]) if line_i else 0
        b = min(Clock.f(clock.segs[line_i + 1][0]) if line_i + 1 < nseg else f_end, f_end)
        if b - a < 3:
            continue
        clips = sp.clips(line_i)
        names = [n for n in s.get("pictures") or [] if sp.pics.get(n) or any(c.get("subject") == n for c in clips) or sp.spare(n, line_i)]
        names += [c["subject"] for c in clips if c.get("subject") and c["subject"] not in names]
        names = list(dict.fromkeys(names))
        if not names:
            # nothing found for this line's own words: the things the lines around it show, then anything left
            near = [n for k in (line_i - 1, line_i + 1) if 0 <= k < nseg for n in (script["segments"][k].get("pictures") or [])]
            near += [c.get("subject") for k in (line_i - 1, line_i + 1) for c in sp.footage.get(k) or []]
            names = [n for n in dict.fromkeys(near + names_all) if n and (sp.pics.get(n) or sp.spare(n, line_i) or sp.inner(n, 0.5, line_i))][:2]
            names = names or [n for n in dict.fromkeys(near + names_all) if n][:1]      # spent: the last resorts in next_source
        if not names:
            continue
        marks = [a]
        for j, n in enumerate(names[1:], 1):
            keys = {F.norm_word(w) for w in str(n).split() if len(F.norm_word(w)) >= 4}
            said = next((Clock.f(w["t0"]) - 1 for w in clock.seg_words(line_i) if F.norm_word(w["w"]) in keys), None)
            floor = marks[-1] + lo
            ceil = b - lo * (len(names) - j)
            even = a + (b - a) * j / len(names)
            at = min(max(said if said is not None else even, floor), ceil)
            marks.append(int(max(at, marks[-1] + 2)))
        marks.append(b)
        prev = [n for n in (script["segments"][line_i - 1].get("pictures") or [])] if line_i else []
        nxt = [n for n in (script["segments"][line_i + 1].get("pictures") or [])] if line_i + 1 < nseg else []
        for j, n in enumerate(names):
            if marks[j + 1] - marks[j] >= 3:
                others = [o for o in names if o != n] + [o for o in prev + nxt if o not in names] + [o for o in names_all if o not in names]
                lay(marks[j], marks[j + 1], n, others)
    out.sort(key=lambda r: r[0])
    for j in range(len(out) - 1):
        out[j][1] = out[j + 1][0]
    if out:
        out[0][0] = 0
        out[-1][1] = f_end
    return [r for r in out if r[1] - r[0] >= 2]


# ----------------------------------------------------------------- finishes by section

def overlays(work):
    """Two full-frame RGBA layers drawn once per run: a film gate (black edges, soft corners, faint scratches) and
    television scanlines (a dark line every fourth row, darker edges)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    os.makedirs(work, exist_ok=True)
    gate, crt = os.path.join(work, "finish_gate.png"), os.path.join(work, "finish_crt.png")
    if not os.path.exists(gate):
        a = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(a)
        d.rounded_rectangle([int(0.035 * W), int(0.022 * H), int(0.965 * W), int(0.978 * H)], radius=int(0.05 * W), fill=0)
        a = a.filter(ImageFilter.GaussianBlur(9))
        al = np.asarray(a, np.float32)
        rng = np.random.default_rng(3)
        rgb = np.zeros((H, W, 3), np.uint8)
        for x in rng.integers(int(0.1 * W), int(0.9 * W), 3):             # scratches: thin, faint, light
            al[:, x:x + 2] = np.maximum(al[:, x:x + 2], 26)
            rgb[:, x:x + 2] = 230
        Image.fromarray(np.dstack([rgb, al.clip(0, 255).astype(np.uint8)]), "RGBA").save(gate)
    if not os.path.exists(crt):
        al = np.zeros((H, W), np.float32)
        al[::4, :] = 64
        al[1::4, :] = 28
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        al = np.maximum(al, np.clip((r - 0.78) * 260, 0, 150))
        Image.fromarray(np.dstack([np.zeros((H, W, 3), np.uint8), al.clip(0, 255).astype(np.uint8)]), "RGBA").save(crt)
    return gate, crt


def section_finish(style, seg):
    """mono (black and white), gate (black and white in a film gate), crt (scanlines) or None, for one segment: the
    writer's era (the year the segment speaks from) and beat. The low point is always black and white."""
    beat = seg.get("beat")
    try:
        era = int(seg.get("era")) if seg.get("era") not in (None, "") else None
    except (TypeError, ValueError):
        era = None
    if beat == "low":
        return "mono"
    if beat in ("payoff", "close") and (era is None or era >= 2000):
        return None
    if style["finish"] == "past":
        return "gate" if era is not None and era < 2000 else None
    if era is not None and era < 1980:
        return "gate"
    if era is not None and era < 2006:
        return "crt"
    return None


# ----------------------------------------------------------------- the film

def build(script, sup, style_id, direction=None):
    """(l1, beats) for one story. script: the writer's (segments with say, pictures, beat, era, collage, show, emphasis);
    sup: the pack (wf3.supply: words, narration, pictures, footage, voices, talking); direction: the style's knobs."""
    st = STYLES[style_id]
    d = dict(aim_s=st["aim_s"], look=st["look"], family="story", bed=True, collage=st["collage"])
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "shadow": {"a": 0.85, "blur": 0.006}},
        "dsp": dict(st["dsp"], shadow={"a": 0.85, "blur": 0.010}),
    }
    P = F.Plan(voices, sup.get("sounds") or {})
    pal = dict(ink=st["ink"], accent=d.get("accent") or st["accent"])
    drop = set(d.get("drop") or [])
    pics = {k: [r for r in v if r.get("title") not in drop and os.path.basename(str(r.get("path"))) not in drop and os.path.exists(str(r.get("path")))]
            for k, v in (sup.get("pictures") or {}).items()}
    pics = {k: v for k, v in pics.items() if v}
    talk = {p for p, v in (sup.get("talking") or {}).items() if v.get("talking")}
    footage = {}
    for ln in sup.get("footage") or []:
        footage[int(ln["i"])] = [c for c in ln.get("clips") or [] if os.path.basename(str(c.get("file") or c.get("path"))) not in drop
                                 and os.path.exists(str(c.get("path")))]
    keys, meta = {}, {}
    for lst in pics.values():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    for v in sup.get("voices") or []:
        c = v.get("clip") or {}
        if c.get("path") and os.path.exists(c["path"]) and c["path"] not in keys:
            keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    f_voice_end = Clock.f(clock.dur)
    f_end = f_voice_end + int(d.get("tail_f", 15))
    stress = F.voice_stress(sup)
    nseg = len(script["segments"])
    cap_y = float(st["cap_y"])
    seg_f = [Clock.f(clock.segs[i][0]) if i else 0 for i in range(nseg)]

    def seg_at(f):
        return max([i for i in range(nseg) if seg_f[i] <= f + 1] or [0])

    # ---- collage lines, chosen before the cut: the writer's, then lines that name several things and are long enough for
    # three prints, up to the style's share of the film; never the hook, never two in a row (the frame breathes between)
    budget = float(d["collage"]) * f_voice_end
    seg_len = [(Clock.f(clock.segs[i + 1][0]) if i + 1 < nseg else f_voice_end) - seg_f[i] for i in range(nseg)]
    shots_in = [seg_len[i] / (float(d["aim_s"]) * FPS) for i in range(nseg)]
    want = [i for i, s in enumerate(script["segments"]) if s.get("collage")]
    want += sorted((i for i, s in enumerate(script["segments"]) if i not in want and len(s.get("pictures") or []) >= 2), key=lambda i: -shots_in[i])
    want += sorted((i for i in range(nseg) if i not in want), key=lambda i: -shots_in[i])
    collage, used_f = set(), 0
    for i in want:
        s_ = script["segments"][i]
        if i == 0 or shots_in[i] < 2.6 or used_f >= budget or (s_.get("beat") == "payoff" and not s_.get("collage")):
            continue
        if (i - 1 in collage or i + 1 in collage) and style_id != "pinboard":
            continue
        collage.add(i)
        used_f += seg_len[i]
    sp = Supply(pics, footage, talk=talk, allow=collage)
    timeline = cut_story(script, clock, sp, f_end, stress, aim_s=float(d["aim_s"]))
    timeline = F.pin_voices(timeline, sup.get("voices") or [])
    fin = [section_finish(st, s) for s in script["segments"]]
    by_seg = {}
    for k, r in enumerate(timeline):
        by_seg.setdefault(seg_at(r[0]), []).append(k)
    gate_png, crt_png = overlays(sup.get("work") or os.path.join(os.path.dirname(sup["narration"]), "..", "finish"))
    keys["gate"] = P.image("finish_gate", dict(path=gate_png, w=W, h=H))
    keys["crt"] = P.image("finish_crt", dict(path=crt_png, w=W, h=H))
    cut_pts, drive_ids, look_pics, mono_spans, layer_spans = [], set(), [], [], []

    def is_mono(f0):
        return fin[seg_at(f0)] in ("mono", "gate")

    def note(f0, f1, kind, name, rec, mode):
        P.pictures.append(dict(f=[f0, f1], kind=kind, name=name, path=rec["path"], shows=rec.get("shows"), mode=mode,
                               finish=fin[seg_at(f0)], use=rec.get("use"),
                               source=(rec.get("source") or {}).get("url") if kind == "clip" else rec.get("page"), time_range=rec.get("time_range"),
                               src=rec.get("src")))
        if rec.get("still") or kind == "photo":
            look_pics.append(rec.get("still") or rec["path"])

    def settle_push(n, land=0.035, push_=0.04):
        return [r4(1.0 + land - land * smooth(q / 7.0)) if q < 8 else r4(1.0 + push_ * (q - 8) / max(n - 9, 1)) for q in range(n)]

    def card_crop(rec, kind, asp):
        pw_, ph_ = float(rec.get("w") or W), float(rec.get("h") or H)
        if kind == "clip":
            rw, rh = min(asp * ph_ / pw_, 1.0), min(pw_ / (asp * ph_), 1.0)
            fs_ = rec.get("faces") or []
            cx = 0.5
            if fs_:
                big = max(fs_, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))
                cx = (big["box"][0] + big["box"][2]) / 2
            x0 = min(max(cx - rw / 2, 0.0), 1.0 - rw)
            y0 = min(max(0.42 - rh / 2, 0.0), 1.0 - rh)
            return [r4(x0), r4(y0), r4(x0 + rw), r4(y0 + rh)]
        (fx, fy), _ = F.focus_of(rec)
        crop = F.cover_crop(pw_, ph_, asp, (fx, fy), (0.5, 0.42))
        if rec.get("use", 1) > 1:                          # a photograph's second showing: another part of it
            cw, ch = crop[2] - crop[0], crop[3] - crop[1]
            k = 0.62
            cx, cy = (crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2
            cx = min(max(1 - cx, cw * k / 2), 1 - cw * k / 2) if not rec.get("faces") else cx
            crop = [r4(cx - cw * k / 2), r4(cy - ch * k / 2), r4(cx + cw * k / 2), r4(cy + ch * k / 2)]
        return crop

    def ground(f0, f1, key, kind, rec, mono, paper=False):
        if paper:
            P.page([f0, f1], "#2E2822", "#191512", grain=0.07)        # a dark board: white captions read on it
            return
        pw_, ph_ = float(rec.get("w") or W), float(rec.get("h") or H)
        kw = dict(blur=30, grade=dict(BED))
        if kind == "clip":
            kw["src"] = list(rec["src"])
        if mono:
            kw["look"] = "mono_silver"
        P.shot([f0, f1], key, F.cover_crop(pw_, ph_, W / H, (0.5, 0.45), (0.5, 0.40)), **kw)

    def window(f0, f1, kind, name, rec, key):
        n = f1 - f0
        mono = is_mono(f0)
        ground(f0, f1, key, kind, rec, mono)
        x0, y0, x1, y1 = WINDOW_BOX
        asp = (x1 - x0) * W / ((y1 - y0) * H)
        kw = dict(z=1, s=settle_push(n), crop=card_crop(rec, kind, asp), radius=0.010, op=[min(1.0, 0.5 + 0.25 * q) for q in range(n)],
                  shadow=dict(a=0.55, blur=0.03, dx=0, dy=0.012))
        if kind == "clip":
            kw["src"] = list(rec["src"])
        if mono:
            kw["look"] = "mono_silver"
        P.card([f0, f1], key, [x0, y0, x1, y1], **kw)
        layer_spans.append((f0, f1, 3))
        note(f0, f1, kind, name, rec, "window")

    def bleed(f0, f1, kind, name, rec, key):
        if kind == "clip" and int(rec.get("w") or W) > int(rec.get("h") or H):
            return window(f0, f1, kind, name, rec, key)
        if kind == "clip" and (F.too_close(rec) or rec.get("voice")):
            P.shot([f0, f1], key, [0.0, 0.0, 1.0, 1.0], src=list(rec["src"]), **({"look": "mono_silver"} if is_mono(f0) else {}))
            note(f0, f1, kind, name, rec, "held")
            return
        if kind == "clip":
            it = P.shot([f0, f1], key, None, src=list(rec["src"]))
            it.pop("crop")
            fm = F.face_meta(rec)
            meta[key] = dict(pw=int(rec.get("w") or W), ph=int(rec.get("h") or H), faces=fm, max_up=1.0 if any(f[3] >= 0.24 for f in fm) else 1.12)
        else:
            if not (F.bleed_ok(rec) or rec.get("faces")):
                return window(f0, f1, kind, name, rec, key)
            crop = card_crop(rec, kind, W / H)
            it = P.shot([f0, f1], key, crop)
            if key not in meta:
                m = dict(pw=int(rec["w"]), ph=int(rec["h"]), faces=[f for f in F.face_meta(rec) if f[3] * rec["h"] >= 60], max_up=F.MAX_UP)
                if not m["faces"]:
                    try:
                        import numpy as np
                        from PIL import Image
                        pace, _, _ = F._engines()
                        with Image.open(rec["path"]) as im_:
                            m["regions"] = pace.regions_of(np.asarray(im_.convert("L")), n=3, size=0.5)
                    except Exception:  # noqa: BLE001
                        pass
                meta[key] = m
        drive_ids.add(it["id"])
        if is_mono(f0):
            mono_spans.append((f0, f1))
        note(f0, f1, kind, name, rec, "bleed")

    def prints(run, paper=False):
        """A run of pictures as prints: each lands tilted, beside and over the one before, and stays to the run's end."""
        r1 = timeline[run[-1]][1]
        hw, hh = PRINT_W / 2, PRINT_W * W / H / 2
        for q, k in enumerate(run):
            f0, f1, kind, name, rec = timeline[k]
            key = keys[rec["path"]]
            mono = is_mono(f0)
            ground(f0, f1, key, kind, rec, mono, paper=paper)
            n, m = f1 - f0, r1 - f1
            cx, cy = PRINT_AT[q % len(PRINT_AT)]
            box = [cx - hw, cy - hh, cx + hw, cy + hh]
            land = [r4(1.0 + 0.10 * (1 - smooth(j / 6.0))) if j < 7 else r4(1.0 + 0.018 * (j - 7) / max(n - 8, 1)) for j in range(n)]
            kw = dict(z=1 + q, rot=PRINT_ROT[q % len(PRINT_ROT)], crop=card_crop(rec, kind, 1.0), radius=0.006,
                      shadow=dict(a=0.62, blur=0.03, dx=0.004, dy=0.014))
            if mono:
                kw["look"] = "mono_silver"
            live = dict(kw, s=land, op=[min(1.0, 0.4 + 0.3 * j) for j in range(n)])
            if kind == "clip":
                live["src"] = list(rec["src"])
            P.card([f0, f1], key, box, **live)
            if m > 0:
                rest = dict(kw, s=land[-1])
                if kind == "clip":
                    rest["src"] = [round(max(rec["src"][1] - 1.0 / FPS, 0.0), 4), round(rec["src"][1], 4)]
                P.card([f1, r1], key, box, **rest)
            layer_spans.append((f0, f1, 2 + q))
            note(f0, f1, kind, name, rec, "print")

    flashes = []
    runs, k = [], 0
    while k < len(timeline):
        i = seg_at(timeline[k][0])
        if i in collage and not timeline[k][4].get("voice"):
            # a run of prints: consecutive pictures of the collage line, five at most (a pinned voice breaks it)
            run, j = [k], k + 1
            while j < len(timeline) and len(run) < len(PRINT_AT) and seg_at(timeline[j][0]) == i and not timeline[j][4].get("voice"):
                run.append(j)
                j += 1
            if len(run) >= 3:
                runs.append(("prints", run))
                k = run[-1] + 1
                continue
        runs.append(("one", [k]))
        k += 1
    for kind_, run in runs:
        if kind_ == "prints":
            prints(run, paper=bool(st.get("paper")))
            f0 = timeline[run[0]][0]
            if st["flash"] == "runs" and (not flashes or f0 - flashes[-1][0] >= 4 * FPS):
                flashes.append((f0, "fill"))
        else:
            f0, f1, kind, name, rec = timeline[run[0]]
            key = keys[rec["path"]]
            if kind == "clip" and rec["path"] in talk and not rec.get("voice"):
                prints(run, paper=bool(st.get("paper")))       # a speaking face is never full frame under the narrator
            elif st["layout"] == "window" and not rec.get("voice"):
                window(f0, f1, kind, name, rec, key)
            else:
                bleed(f0, f1, kind, name, rec, key)
        for j in run:
            cut_pts.append(timeline[j][0])
            P.events.append(timeline[j][0])
    # ---- section finishes: the film gate and the scanlines over their sections, under the type
    spans = []
    for f0, f1, *_ in timeline:
        fz = fin[seg_at(f0)]
        if fz in ("gate", "crt"):
            if spans and spans[-1][2] == fz and spans[-1][1] >= f0 - 1:
                spans[-1][1] = f1
            else:
                spans.append([f0, f1, fz])
    rng_ = __import__("random").Random(1114)
    for f0, f1, fz in spans:
        if f1 - f0 < 6:
            continue
        op = [r4(0.86 + 0.14 * rng_.random()) for _ in range(f1 - f0)] if fz == "gate" else 0.9
        P.card([f0, f1], keys[fz], [0.0, 0.0, 1.0, 1.0], z=40, crop=[0.0, 0.0, 1.0, 1.0], op=op)
    # ---- flash frames: into each turn of the story, and into a change of finish
    beats_at = []
    for i in range(1, nseg):
        b_prev, b_now = script["segments"][i - 1].get("beat"), script["segments"][i].get("beat")
        f = min((c for c in cut_pts if c >= seg_f[i] - 4), default=seg_f[i])
        if b_now != b_prev and b_now in ("turn", "low", "payoff"):
            beats_at.append((f, "neg" if b_now == "low" else "fill"))
        elif fin[i] != fin[i - 1] and (fin[i] or fin[i - 1]):
            beats_at.append((f, "neg" if fin[i] in ("mono", "gate") else "fill"))
    for f, mode in beats_at:
        if all(abs(f - g) >= 3 * FPS for g, _ in flashes):
            flashes.append((f, mode))
    flashes.sort()
    for f, mode in flashes:
        if 3 < f < f_end - 6:
            if mode == "neg":
                P.flash([f, f + 2], mode="neg", a=1.0, z=8)
            else:
                P.flash([f, f + 3], mode="fill", c="#FFFFFF", a=[0.8, 0.4, 0.12], z=8)
            P.events.append(f)
    # ---- display words, then the captions at one seat
    sc = json.loads(json.dumps(script))
    if st["upper"]:
        for s in sc["segments"]:
            s["show"] = [(dict(x, text=str(x.get("text") or "").upper()) if isinstance(x, dict) else str(x).upper()) for x in s.get("show") or []]
    shown = F.display_words(P, clock, sc, "dsp", cap_y + 0.012, f_voice_end, ink=pal["ink"], accent=pal["accent"], gap_s=1.8, hold_s=1.05,
                            px_max=float(st["dsp"]["px"]) * 1.25) if d.get("display", True) else []
    lines = F.caption_build(P, clock, "cap", cap_y, f_end=f_voice_end + 8, hold=[(a - 1, b) for a, b, _ in shown], z=5)
    P.flash([0, f_end], mode="pool", a=float(d.get("pool", 0.42)), rect=[0.06, cap_y - 0.105, 0.94, cap_y + 0.075], z=-1)
    pay = next((i for i, s in enumerate(script["segments"]) if s.get("beat") == "payoff"), None)
    turn_f = min((c for c in cut_pts if c >= seg_f[pay] - 4), default=seg_f[pay]) if pay is not None else None
    film = dict(grain=float(st["grain"]), vignette=float(st["vignette"]), over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1114)), film)
    secs = f_voice_end / FPS
    np10 = len({(p["path"], tuple(p.get("src") or [])) for p in P.pictures}) / max(secs, 1) * 10     # prints and windows count
    l1 = F.engines_finish(l1, sup, stress, drive_ids, meta, dict(d, look_pictures=look_pics), turn_f=turn_f, mono=mono_spans,
                          clear=[0.10, cap_y - 0.085, 0.90, cap_y + 0.02],
                          spoken=[(w["t0"] - LEAD_F / FPS, w["t1"] - LEAD_F / FPS) for w in clock.words], np10=np10)
    changes = sorted({it["f"][0] for it in l1["items"] if it["k"] == "shot"})
    for s_ in l1.get("sound") or []:
        P.events.append(int(s_["event_f"]))
    secs = f_voice_end / FPS
    photo_uses = {}
    for p in P.pictures:
        if p["kind"] == "photo":
            photo_uses[p["path"]] = photo_uses.get(p["path"], 0) + 1
    new_pics = len({(p["path"], tuple(p.get("src") or [])) for p in P.pictures})
    layered = sum(b - a for a, b, n in layer_spans if n >= 3)
    look_f = sorted({0, 12, 40, *[c + 5 for c in cut_pts[::3]], *[a + 10 for a, _, _ in shown], *([turn_f + 10] if turn_f is not None else []), f_end - 3})
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], cuts=sorted(set(cut_pts)), changes=changes,
                 events=sorted(set(P.events) | set(cut_pts) | set(changes)),
                 pictures=P.pictures, on_screen=P.shown, caption_y=cap_y, caption_lines=lines, display=[dict(f=[a, b], text=t) for a, b, t in shown],
                 turn_f=turn_f, pace=l1.get("pace"), style=style_id, layout=st["layout"],
                 measured=dict(new_pictures_10s=round(new_pics / max(secs, 1) * 10, 2), base_shots=len(timeline),
                               median_shot_s=round(sorted(r[1] - r[0] for r in timeline)[len(timeline) // 2] / FPS, 2) if timeline else None,
                               photo_most_uses=max(photo_uses.values()) if photo_uses else 0,
                               collage_lines=sorted(collage), layered_3plus_share=round(layered / max(f_voice_end, 1), 3),
                               flashes=len(flashes), finishes={s: sum(1 for x in fin if x == s) for s in ("mono", "gate", "crt", None) if any(x == s for x in fin)},
                               talking_clips=len(talk), talking_as_prints=sum(1 for p in P.pictures if p["path"] in talk and p["mode"] == "print"),
                               talking_full_frame=sum(1 for p in P.pictures if p["path"] in talk and p["mode"] != "print"), display_words=len(shown)),
                 sound_design={k: v for k, v in (l1.get("sound_design") or {}).items() if k != "dropped"},
                 look=[f for f in look_f if 0 <= f < f_end])
    return l1, beats


def builder(style_id):
    def b(script, sup, direction=None):
        return build(script, sup, style_id, direction)
    return b
