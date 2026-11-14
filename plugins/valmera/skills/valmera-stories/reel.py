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

14 Nov, when the pack carries per-line verdicts (pack["fit"], fit.py; the rater found 19% of a film under words its
pictures contradicted): the cut (cut_fit) puts on a line only a picture whose verdict passes for that line and never
recycles one across lines (its own clips, the most moving first; spare clips of other lines; photographs; a designed
card from the line's own display item; a reframe of the current take, at most 1.2 s); shot lengths follow the judge's
distribution; finishes are the writer's devices only (colour base, 12-25% of the voice, a ramp into colour at the
turn); prints keep moving; white frames are 1-2 frames at full strength; names land as supers on their first mention.
Without verdicts (a marketing film, an older pack) the cut is the one above, unchanged.
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
                 collage=0.26, aim_s=0.6, cap_y=0.615, flash="sections"),
    "archive": dict(name="Archive", layout="bleed", ink="#F2EDE4", accent="#E9D8A6", look="story_low",
                    dsp=dict(font="DMSerifDisplay-Italic.ttf", px=150, track=0), upper=False, grain=0.03, vignette=0.46,
                    collage=0.22, aim_s=0.65, cap_y=0.615, flash="runs"),
    "window": dict(name="Window", layout="window", ink="#F4F6F8", accent="#7FE3FF", look="story_cold",
                   dsp=dict(font="Anton-Regular.ttf", px=160, track=1), upper=True, grain=0.012, vignette=0.36,
                   collage=0.22, aim_s=0.66, cap_y=0.665, flash="sections"),
    "pinboard": dict(name="Pinboard", layout="bleed", ink="#F6F1E7", accent="#FF7A3D", look="story_low",
                     dsp=dict(font="PlayfairDisplay-Black.ttf", px=128, track=-1), upper=False, grain=0.02, vignette=0.38,
                     collage=0.55, aim_s=0.66, cap_y=0.675, flash="sections", paper=True),
}
ORDER = ["reel", "pinboard", "archive", "window"]

WINDOW_BOX = (0.04, 0.205, 0.96, 0.593)
PRINT_W = 0.60
PRINT_ROT = (-2.8, 2.1, -1.4, 2.6, -2.2)
PRINT_AT = ((0.36, 0.385), (0.64, 0.445), (0.40, 0.470), (0.62, 0.380), (0.50, 0.430))
BED = dict(lift=0, gamma=1.0, gain=0.34, sat=0.6, contrast=1, temp=0, tint=0)
BED_FIT = dict(lift=0, gamma=1.0, gain=0.75, sat=1.0, contrast=1, temp=0, tint=0)    # the ground under prints and windows: the picture
                                                                                    # itself, lightly blurred, not a dark void
GROUND_BLUR, GROUND_BLUR_FIT = 30, 12
PRINT_W_FIT = (0.78, 0.86)
WINDOW_SHARE = 0.15        # windows of landscape footage: a budget the build sets (the rest bleeds on a cover crop the driver moves)
DIM = dict(lift=0, gamma=1.0, gain=0.46, sat=0.8, contrast=1, temp=0, tint=0)
REUSE_AFTER_S = 12.0
MAX_USES = 2
TALK_FACE_H = 0.12         # a face smaller than this share of the frame's height does not read as speaking (see talking())
TALK_SCORE = 0.75          # lip-gap change against 0.02 (median) or 0.03 (p80); on FedEx's 16 face clips a closed, posing mouth
                           # read 0.41, interview close-ups 0.83-4.3 (14 Nov)


# ----------------------------------------------------------------- a person speaking on camera, by signal

def talking(c, ffmpeg=None, windows=False):
    """True when the clip shows a person speaking on camera: a face of at least TALK_FACE_H of the frame's height whose
    lips part and close. Per frame (15 a second) YuNet finds the face and its mouth corners; the darkest row between the
    lips against the face's median tone is the lip gap; speech is that gap changing from frame to frame (score: the median
    change over 0.02 or its 80th percentile over 0.03, whichever is larger; TALK_SCORE or more speaks). (bool, score);
    with windows=True also the stretches where the lips are still ([(t0, t1)] seconds: the gap changes under 0.012 a
    frame for 0.3 s or more), where the clip may play full frame under the narrator."""
    import cv2
    import numpy as np
    path = c.get("path")
    none = (False, 0.0, []) if windows else (False, 0.0)
    if not path or not os.path.exists(path):
        return none
    ff = ffmpeg or os.environ.get("SHELL_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"
    w = 480
    try:
        h = int(round(w * int(c.get("h") or H) / max(int(c.get("w") or W), 1) / 2)) * 2
        r = subprocess.run([ff, "-v", "error", "-i", path, "-vf", f"fps=15,scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           capture_output=True, timeout=60)
        n = len(r.stdout) // (w * h * 3)
        if n < 5:
            return none
        frames = np.frombuffer(r.stdout[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    except Exception:  # noqa: BLE001
        return none
    yunet = os.environ.get("YUNET_MODEL", "/Users/masaoodi/Documents/Valmera/.valmera/models/face_detection_yunet_2023mar.onnx")
    det = cv2.FaceDetectorYN.create(yunet, "", (w, h), 0.6, 0.3, 50)
    gap, sizes, at = [], [], []
    for fi, fr in enumerate(frames):
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
        at.append(fi / 15.0)
    if len(gap) < 5 or len(sizes) < 0.6 * len(frames) or float(np.median(sizes)) < TALK_FACE_H:
        return none
    d = np.abs(np.diff(np.array(gap)))
    mad, p80 = float(np.median(d)), float(np.percentile(d, 80))
    score = max(mad / 0.02, p80 / 0.03)
    if not windows:
        return bool(score >= TALK_SCORE), round(score, 2)
    still, run0 = [], None
    for j, v in enumerate(list(d) + [1.0]):
        if v < 0.012 and run0 is None:
            run0 = at[j]
        elif v >= 0.012 and run0 is not None:
            if at[j] - run0 >= 0.3:
                still.append([round(run0, 3), round(at[j], 3)])
            run0 = None
    return bool(score >= TALK_SCORE), round(score, 2), still


# ----------------------------------------------------------------- how much a clip moves, by signal

def clip_motion(path, ffmpeg=None, landscape=False, fps=15.0, cut_luma=40.0):
    """A clip's content motion as the rater measures it: dense optical flow (Farneback) at 15 fps on 270x480 (480x270
    for a landscape clip), the global similarity (a camera move) removed by a RANSAC fit, the 90th percentile of what is
    left per frame. {"p90": its median over the clip, "curve": per frame, "global_px": the median camera move, "cuts":
    seconds where the picture changes (mean luma change over cut_luma: a cut inside the clip)}. None when unreadable."""
    import cv2
    import numpy as np
    ff = ffmpeg or os.environ.get("SHELL_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"
    w, h = (480, 270) if landscape else (270, 480)
    try:
        pr = subprocess.run([ff, "-v", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    n = len(pr.stdout) // (w * h)
    if n < 1:
        return None
    fr = np.frombuffer(pr.stdout[:n * w * h], np.uint8).reshape(n, h, w)
    if n < 2:
        return dict(p90=0.0, curve=[0.0], global_px=0.0, cuts=[])
    ys, xs = np.mgrid[8:fr.shape[1]:16, 8:fr.shape[2]:16]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    curve, glob, cuts = [], [], []
    for k in range(n - 1):
        a, b = fr[k], fr[k + 1]
        if float(np.abs(a.astype(np.float32) - b.astype(np.float32)).mean()) > cut_luma:
            cuts.append(round((k + 1) / fps, 3))
            curve.append(0.0)
            continue
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        d = flow[pts[:, 1].astype(int), pts[:, 0].astype(int)]
        M, _ = cv2.estimateAffinePartial2D(pts, pts + d, method=cv2.RANSAC, ransacReprojThreshold=1.0)
        if M is None:
            res = np.linalg.norm(d, axis=1)
            g = 0.0
        else:
            pred = pts @ M[:, :2].T + M[:, 2] - pts
            res = np.linalg.norm(d - pred, axis=1)
            g = float(np.median(np.linalg.norm(pred, axis=1)))
        curve.append(round(float(np.percentile(res, 90)), 3))
        glob.append(g)
    curve.append(curve[-1] if curve else 0.0)
    live = [v for v in curve if v > 0] or [0.0]
    return dict(p90=round(float(np.median(live)), 3), curve=curve, global_px=round(float(np.median(glob)) if glob else 0.0, 3), cuts=cuts)


# ----------------------------------------------------------------- the pictures, never twice

class Supply:
    """What is left to show: photographs by name, clips by line, each clip's seconds already played."""

    def __init__(self, pics, footage, talk=(), allow=(), fit=None, deny=(), cards=None):
        self.pics = pics
        self.footage = footage
        self.talk = set(talk)      # clips of a person speaking on camera: only ever a print in a collage line (never full frame
        self.allow = set(allow)    # under the narrator); allow: those lines
        self.fit = None if fit is None else {p: set(v) for p, v in fit.items()}     # path -> lines it carries (fit.fits)
        self.deny = set(deny)      # (file name, line): a shot the audit failed there
        self.cards = cards or {}   # line -> the line's own display items a designed card can carry
        self.cards_used = set()
        self.takes = {}            # take group -> take indices played (clips of one take share a group: reel.take_groups)
        self.group = {c["path"]: c.get("take_group") or c["path"] for ln in footage.values() for c in ln}
        self.shortfall, self.reframed = {}, {}
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
        if (os.path.basename(str(c.get("path"))), line) in self.deny:
            return False
        return c["path"] not in self.talk or line in self.allow

    def fits(self, path, line):
        """With verdicts: the picture carries this line (and the audit did not fail it there)."""
        if (os.path.basename(str(path)), line) in self.deny:
            return False
        return self.fit is None or line in (self.fit.get(path) or ())

    def take_used(self, path, ti):
        return ti in (self.takes.get(self.group.get(path, path)) or set())

    def use_take(self, path, ti):
        self.takes.setdefault(self.group.get(path, path), set()).add(ti)

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


# ----------------------------------------------------------------- the cut when every picture has a verdict for its line

# The judge's shot lengths (TEARDOWN.json, 1512 shots of the 28 reels): quantiles 5..95 of a shot's seconds
SHOT_Q = ((0.05, 0.08), (0.10, 0.10), (0.20, 0.20), (0.30, 0.32), (0.40, 0.40), (0.50, 0.50), (0.60, 0.60), (0.70, 0.72),
          (0.80, 0.958), (0.90, 1.267), (0.95, 1.68))
PACE_K = {"fast": 0.8, "brisk": 0.9, "steady": 1.0, "slow": 1.2}
MOTION_FLOOR = 1.0          # px a frame of content motion at 270x480 (the rater's residual): under it a clip reads as a still
REFRAME_MAX_S = 1.2         # a fitting take held on a camera reframe when a line's supply is spent: never longer
PHOTO_SHARE = 0.10          # photographs on lines that have footage: at most this share of shots
BURST_F = (2, 5)            # a list burst: pictures of 2-5 frames


def shot_draw(rng, k=1.0, lo_s=0.1, hi_s=1.9):
    """One target length (s) from the judge's distribution (inverse of SHOT_Q, linear between quantiles), scaled by k."""
    u = rng.random()
    q = SHOT_Q
    if u <= q[0][0]:
        v = q[0][1] * u / q[0][0] + 0.05 * (1 - u / q[0][0])
    elif u >= q[-1][0]:
        v = q[-1][1] + (u - q[-1][0]) / (1 - q[-1][0]) * 0.9
    else:
        for (u0, v0), (u1, v1) in zip(q, q[1:]):
            if u0 <= u <= u1:
                v = v0 + (v1 - v0) * (u - u0) / (u1 - u0)
                break
    return min(max(v * k, lo_s), hi_s)


def seg_k(seg):
    """How a segment's shots are scaled from the judge's distribution: its pace, and room on the low point and the turn."""
    k = PACE_K.get(seg.get("pace"), 1.0)
    if seg.get("beat") in ("hook", "setup") and len(seg.get("see") or []) >= 3:
        k *= 0.85
    return k, (2.6 if seg.get("beat") in ("low", "turn") else 1.9)


class Takes:
    """A clip's takes: the stretches between the cuts measured inside it (clip["cuts"], seconds). A take is one picture;
    played once, it is spent (a second cut inside the same take is a repeat)."""

    @staticmethod
    def of(c):
        dur = float(c.get("seconds") or 0)
        cuts = sorted(t for t in (c.get("cuts") or []) if 0.15 < float(t) < dur - 0.15)
        edges = [0.0] + [float(t) for t in cuts] + [dur]
        return [(edges[j], edges[j + 1]) for j in range(len(edges) - 1) if edges[j + 1] - edges[j] >= 0.12]

    @staticmethod
    def best(c, take, n_s):
        """(t0, t1) inside a take: the most moving n_s seconds by the clip's measured motion curve (15 a second)."""
        a, b = take
        n_s = min(n_s, b - a)
        curve = ((c.get("motion") or {}).get("curve")) or []
        if not curve or b - a <= n_s + 1e-3:
            t0 = a + max((b - a - n_s) / 2, 0.0)
            return t0, t0 + n_s
        step = 1 / 15.0
        best, at = -1.0, a
        t = a
        while t + n_s <= b + 1e-6:
            i0, i1 = int(t / step), max(int((t + n_s) / step), int(t / step) + 1)
            seg = curve[i0:i1] or [0.0]
            m = sum(seg) / len(seg)
            if m > best:
                best, at = m, t
            t += step
        return at, at + n_s


def moving(c):
    return float((c.get("motion") or {}).get("p90") or 0.0) >= MOTION_FLOOR


def cut_fit(script, clock, sp, f_end, stress, seed=1114):
    """[[f0, f1, kind, name, rec]] when every picture carries a verdict for its line (sp.fit): only pictures that fit
    their line, never one recycled across lines. Per line, once its own fitting clips are spent: fitting clips found
    for other lines and not yet played, then fitting photographs, then a designed card from the line's own display item,
    then the current take held on a camera reframe (at most REFRAME_MAX_S, not a new picture). Shot lengths are drawn
    from the judge's distribution, contoured by the segment's pace and beat; a list of three or more things bursts.
    rec["tier"] says which rung each shot came from; sp.shortfall records frames no fitting picture could fill."""
    import random
    rng = random.Random(seed)
    out = []
    nseg = len(script["segments"])
    has_foot = {i for i, v in sp.footage.items() if v}
    sp.shortfall, sp.reframed = {}, {}
    n_photo = [0, 0]                  # photographs, shots, on lines with footage

    def cands(line):
        see = [x.get("thing") for x in script["segments"][line].get("see") or []]
        rank = {n: k for k, n in enumerate(see)}
        own, other = [], []
        for ln, lst in sp.footage.items():
            for c in lst:
                if not sp.fits(c["path"], line) or not sp.ok(c, line):
                    continue
                for ti, take in enumerate(Takes.of(c)):
                    if sp.take_used(c["path"], ti):
                        continue
                    (own if ln == line else other).append((c, ti, take, ln))
        key = lambda q: (not moving(q[0]), rank.get(q[0].get("subject"), 9), -float((q[0].get("motion") or {}).get("p90") or 0))  # noqa: E731
        own.sort(key=key)
        # spare clips of lines already cut first: a later line's own clip is that line's, unless nothing else fits here
        other.sort(key=lambda q: (q[3] > line,) + key(q))
        own = [q[:3] for q in own]
        other = [q[:3] for q in other]
        photos = [r for lst in sp.pics.values() for r in lst if sp.fits(r["path"], line) and not sp.uses.get(r["path"])]
        photos.sort(key=lambda r: rank.get(r.get("name"), 9))
        cards = [x for x in sp.cards.get(line) or [] if x["text"] not in sp.cards_used]
        return own, other, photos, cards

    def pick(line, n_s, last):
        """The next fitting picture for a shot of n_s seconds: (kind, name, rec, tier, take) or None."""
        own, other, photos, cards = cands(line)
        last_still = bool(out) and out[-1][2] == "clip" and not moving(out[-1][4])
        for tier, lst in (("line", own), ("other_line", other)):
            for c, ti, take in lst:
                if c["path"] == last or (last_still and not moving(c)):
                    continue
                return "clip", c.get("subject"), c, tier, (ti, take)
        on_foot = line in has_foot
        if photos and (not on_foot or n_photo[0] + 1 <= PHOTO_SHARE * (n_photo[1] + 1) + 1):
            r = next((r for r in photos if r["path"] != last), None)
            if r is not None:
                return "photo", r.get("name"), r, "photo", None
        if cards:
            x = cards[0]
            return "designed", None, dict(path=f"designed:{line}:{x['text']}", text=x["text"], role=x.get("role"), line=line, w=W, h=H), "designed", None
        for tier, lst in (("line", own), ("other_line", other)):          # a still clip after a still, rather than nothing
            for c, ti, take in lst:
                if c["path"] != last:
                    return "clip", c.get("subject"), c, tier, (ti, take)
        return None

    def reframe(line, b, pos):
        """The current take held on a reframe (the pacing driver moves the camera on it): at most REFRAME_MAX_S."""
        if not out:
            return pos
        hold = min(b, pos + int(REFRAME_MAX_S * FPS))
        r = out[-1]
        r[1] = hold
        if r[2] == "clip":
            take = r[4].get("take") or (0.0, float(r[4].get("seconds") or 0))
            t0 = r[4]["src"][0]
            r[4] = dict(r[4], src=[t0, round(min(take[1], t0 + (hold - r[0]) / FPS), 3)])
        sp.reframed[line] = sp.reframed.get(line, 0) + (hold - pos)
        return hold

    for line, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[line][0]) if line else 0
        b = min(Clock.f(clock.segs[line + 1][0]) if line + 1 < nseg else f_end, f_end)
        if b - a < 3:
            continue
        k, hi_s = seg_k(s)
        burst_at = a + int(0.25 * (b - a)) if len(s.get("see") or []) >= 3 else None
        burst_left = rng.randint(4, 8) if burst_at is not None else 0
        pos = a
        while pos < b - 1:
            left = b - pos
            in_burst = burst_at is not None and pos >= burst_at and burst_left > 0
            if in_burst:
                n = rng.randint(*BURST_F)
            else:
                n = int(round(shot_draw(rng, k, hi_s=hi_s) * FPS))
                n = max(n, 3)
                # the cut lands on the voice's stress within 0.15 s of the target
                w0, w1 = pos + n - int(0.15 * FPS), pos + n + int(0.15 * FPS)
                st = [(v, f) for f, v in stress if w0 <= f <= w1 and f > pos + 2]
                if st:
                    n = max(max(st)[1] - pos, 3)
            if left - n < 3:
                n = left                    # the tail becomes part of this shot, never a stretched hold of the last
            last = out[-1][4]["path"] if out else None
            got = pick(line, n / FPS, last)
            if got is None:
                end = reframe(line, b, pos)
                if end < b:
                    sp.shortfall[line] = sp.shortfall.get(line, 0) + (b - end)
                    if out:
                        out[-1][1] = b          # nothing that fits is left: the hold is logged as the line's shortfall
                break
            kind, name, rec, tier, take = got
            end = pos + n
            if kind == "clip":
                ti, tk = take
                t0, t1 = Takes.best(rec, tk, n / FPS)
                if t1 - t0 < n / FPS * 0.8 - 1e-3:
                    end = pos + max(int((t1 - t0) * FPS / 0.8), 2)
                sp.use_take(rec["path"], ti)
                sp.take_clip(rec, t0, t1, pos)
                rec = dict(rec, src=[round(t0, 3), round(t1, 3)], take=list(tk), take_i=ti, use=1, tier=tier)
            elif kind == "photo":
                sp.take_photo(rec, pos)
                rec = dict(rec, use=len(sp.uses[rec["path"]]), tier=tier)
                if line in has_foot:
                    n_photo[0] += 1
            else:
                sp.cards_used.add(rec["text"])
                rec = dict(rec, tier=tier)
                end = pos + max(n, int(1.2 * FPS)) if left >= int(1.2 * FPS) else pos + left
            if line in has_foot:
                n_photo[1] += 1
            if in_burst:
                burst_left -= 1
            end = min(end, b)
            out.append([pos, end, kind, name, rec])
            pos = end
    out.sort(key=lambda r: r[0])
    for j in range(len(out) - 1):
        out[j][1] = out[j + 1][0]
    if out:
        out[0][0] = 0
        out[-1][1] = f_end
    return [r for r in out if r[1] - r[0] >= 1]


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


FINISH_OF = {"bw": "mono", "gate": "gate", "crt": "crt", "none": None, None: None}


def section_finish(style, seg, i=None, script=None):
    """mono (black and white), gate (black and white in a film gate), crt (scanlines) or None (colour, under the film's
    look) for one segment: the writer's device (seg["finish"], writer.annotate), never a mapping from its years. The
    payoff and the close are always colour, and so is every segment from the script's colour_turn on."""
    if seg.get("beat") in ("payoff", "close"):
        return None
    ct = (script or {}).get("colour_turn")
    if isinstance(ct, int) and i is not None and i >= ct:
        return None
    return FINISH_OF.get(seg.get("finish"))


def finish_budget(fin, seg_len, script, top=0.25):
    """The devices held to the judge's budget on the film's own frames: above `top` of the voice only the writer's best
    ranked (finish_rank) stay. Returns (finishes, share)."""
    total = max(sum(seg_len), 1)
    segs = script["segments"]
    dev = sorted((i for i, f in enumerate(fin) if f), key=lambda i: (segs[i].get("finish_rank") if isinstance(segs[i].get("finish_rank"), (int, float)) else 99, i))
    out, used = list(fin), 0
    for i in dev:
        if (used + seg_len[i]) / total <= top + 1e-9:
            used += seg_len[i]
        else:
            out[i] = None
    return out, round(used / total, 3)


# ----------------------------------------------------------------- the film

def caption_pool(plan, end_frame, y, amount=0.70, holes=()):
    """Keep caption contrast over bright footage and section flashes; `holes`: white frames the pool leaves whole."""
    at = 0
    for h0, h1 in sorted(holes):
        if h0 > at:
            plan.flash([at, h0], mode="pool", a=amount, rect=[0.06, y - 0.105, 0.94, y + 0.075], z=9)
        at = max(at, h1)
    if end_frame > at:
        plan.flash([at, end_frame], mode="pool", a=amount, rect=[0.06, y - 0.105, 0.94, y + 0.075], z=9)


def take_groups(clips, ffmpeg=None, corr=0.85, near_s=20.0):
    """Clips of one source that show the same take (their middle frames correlate at `corr` or more and they sit within
    near_s seconds of each other in the source): {path: group id}. A signal on the clips' own stills."""
    import numpy as np
    from PIL import Image
    by_url = {}
    for c in clips:
        by_url.setdefault((c.get("source") or {}).get("url") or c["path"], []).append(c)
    out = {}
    for url, lst in by_url.items():
        vecs = {}
        for c in lst:
            try:
                with Image.open(c.get("still")) as im:
                    v = np.asarray(im.convert("L").resize((32, 32)), np.float32).ravel()
                vecs[c["path"]] = (v - v.mean()) / (v.std() + 1e-6)
            except Exception:  # noqa: BLE001 - no still: its own group
                pass
        parent = {c["path"]: c["path"] for c in lst}

        def root(p):
            while parent[p] != p:
                p = parent[p]
            return p
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                ta, tb = a.get("time_range") or [0, 0], b.get("time_range") or [0, 0]
                if a["path"] in vecs and b["path"] in vecs and abs(float(ta[0]) - float(tb[0])) <= near_s and \
                        float(np.mean(vecs[a["path"]] * vecs[b["path"]])) >= corr:
                    parent[root(b["path"])] = root(a["path"])
        for c in lst:
            out[c["path"]] = root(c["path"])
    return out


def take_stats(pictures, footage):
    """Distinct takes among the film's pictures: a photograph or a designed card by its path, a clip by its take group
    (take_groups) and the take inside it (its measured cuts). Repeats and consecutive repeats by that count."""
    group = {}
    for lst in footage.values():
        for c in lst:
            group[c["path"]] = c.get("take_group") or c["path"]
    seen, rep, consec, last = set(), 0, 0, None
    for p in sorted(pictures, key=lambda q: q["f"][0]):
        k = (group.get(p["path"], p["path"]), p.get("take")) if p["kind"] == "clip" else (p["path"], None)
        if k in seen:
            rep += 1
            if last in seen and last == k:
                consec += 1
        seen.add(k)
        last = k
    n = len(pictures)
    return dict(new_pictures_10s_n=len(seen), repeats=rep, repeat_share=round(rep / max(n, 1), 3), consecutive_repeats=consec)


def fit_measures(pictures, timeline, sp, takes, secs, fin, seg_len, device_share, talk_fit, collage, seg_at, supers, dropped,
                 turn_ramp, white, flashes, f_voice_end):
    """What the fit-gated cut measured of itself: seconds by tier, each line's shortfall, distinct takes and repeats,
    shot lengths against the judge's distribution, photographs and still clips, devices and type."""
    tiers = {}
    for r in timeline:
        t = r[4].get("tier") or ("voice" if r[4].get("voice") else "other")
        tiers[t] = round(tiers.get(t, 0) + (r[1] - r[0]) / FPS, 2)
    lens = sorted(r[1] - r[0] for r in timeline)
    n = max(len(lens), 1)
    base = [p for p in pictures if p["kind"] in ("clip", "photo", "designed")]
    foot_lines = {i for i, v in sp.footage.items() if v}
    on_foot = [p for p in base if p.get("line") in foot_lines]
    still = [p for p in base if p["kind"] == "clip" and float(p.get("motion") or 0) < MOTION_FLOOR]
    voice_f = max(sum(seg_len), 1)
    return dict(tier_s=tiers, shortfall_s={str(k): round(v / FPS, 2) for k, v in sorted(sp.shortfall.items())},
                reframed_s={str(k): round(v / FPS, 2) for k, v in sorted(sp.reframed.items())},
                new_pictures_10s=round(takes["new_pictures_10s_n"] / max(secs, 1) * 10, 2), repeat_share=takes["repeat_share"],
                consecutive_repeats=takes["consecutive_repeats"],
                shots_under_0_5s=round(sum(1 for x in lens if x < 0.5 * FPS) / n, 3), shots_1_2f=round(sum(1 for x in lens if x <= 2) / n, 3),
                shots_over_2s=round(sum(1 for x in lens if x >= 2 * FPS) / n, 3),
                photo_share_on_footage_lines=round(sum(1 for p in on_foot if p["kind"] == "photo") / max(len(on_foot), 1), 3),
                content_motion_share=round(1 - len(still) / max(sum(1 for p in base if p["kind"] == "clip"), 1), 3),
                device_share=device_share, device_segments=[i for i, f in enumerate(fin) if f],
                colour_turn_ramp_f=turn_ramp, white_frames=len(white), negatives=sum(1 for _, m in flashes if m == "neg"),
                talking_live=len(talk_fit), talking_prints_outside_collage=sum(1 for p in pictures if p["path"] in talk_fit and p["mode"] == "print"
                                                                                and p.get("line") not in collage),
                name_supers=len(supers), designed_cards=sum(1 for p in pictures if p["kind"] == "designed"))


def name_supers(P, clock, sc, timeline, st, pal, f_end, shown, dropped):
    """Each person's name, large and left-aligned, landing on the first word of their name at their first mention
    (writer.annotate's name items, word_i), held 1.5 s; set above or below the middle where the picture under it has no
    face, never at once with a display word. Returns [(f0, f1, text)]."""
    _, _, bake = F._engines()
    out = []
    for i, s in enumerate(sc["segments"]):
        for x in s.get("show") or []:
            if not isinstance(x, dict) or x.get("role") != "name" or not x.get("text"):
                continue
            ws = clock.seg_words(i)
            wi = x.get("word_i")
            if not (isinstance(wi, int) and 0 <= wi < len(ws)):
                dropped.append(dict(seg=i, text=x["text"], role="name", why="no anchor"))
                continue
            a = max(Clock.f(ws[wi]["t0"]) - 1, 1)
            b = min(a + int(1.5 * FPS), f_end - 2)
            if b - a < 14 or any(a < d1 and d0 < b for d0, d1, _ in shown + out):
                dropped.append(dict(seg=i, text=x["text"], role="name", why="no room"))
                continue
            faces_hi = any(((f["box"][1] + f["box"][3]) / 2) < 0.45 for r in timeline if r[0] < b and a < r[1] for f in (r[4].get("faces") or []))
            y = 0.76 if faces_hi else 0.27
            text = x["text"].upper() if st["upper"] else x["text"]
            vk = f"dsp_name{a}"
            P.voices[vk] = dict(P.voices["dsp"])
            P.voices[vk]["px"] = P.R.fit_px(vk, text, 0.66 * W, float(st["dsp"]["px"]) * 0.8)
            n = b - a
            k = min(int(bake.BOUNCE["n"]), n)
            ys = [r4(y + float(q)) for q in bake.bounce(k, rest=0.0)] + [r4(y)] * (n - k)
            bl = [round(float(q), 3) for q in bake.bounce_blur(k)] + [0.0] * (n - k)
            P.word([a, b], text, vk, 0.08, ys, pal["ink"], [min(1.0, (q + 1) / 2) for q in range(n)], z=3, blur=bl, role="big",
                   stroke=dict(w=3.0, c="#0B0A09"))
            P.events.append(a)
            out.append((a, b, text))
    return out


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
    drop_all = set(d.get("drop") or [])
    deny = set()
    for x in drop_all:                    # "file@line": a shot the audit failed on that line only
        nm, _, ln = str(x).rpartition("@")
        if nm and ln.isdigit():
            deny.add((nm, int(ln)))
    drop = {x for x in drop_all if "@" not in str(x) or not str(x).rpartition("@")[2].isdigit()}
    fit_on = sup.get("fit") is not None
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
    cards = {}
    for i, s_ in enumerate(script["segments"]):
        cards[i] = [dict(text=str(x["text"]), role=x.get("role")) for x in s_.get("show") or []
                    if isinstance(x, dict) and x.get("text") and x.get("role") in ("figure", "key", "chapter")]
    talk_fit = set()
    if fit_on:
        # the talking rule only for live action: the verdict's booleans (a model's reading), never animation or drawings
        ver = sup.get("fit_verdicts") or {}
        talk_fit = {p for p in talk if any(v.get("live_action") is True and v.get("speaking_on_camera") is True for v in (ver.get(p) or {}).values())}
        sp = Supply(pics, footage, talk=(), allow=collage, fit={p: set(v) for p, v in (sup.get("fit") or {}).items()}, deny=deny, cards=cards)
        timeline = cut_fit(script, clock, sp, f_end, stress, seed=int(d.get("seed", 1114)))
    else:
        sp = Supply(pics, footage, talk=talk, allow=collage, deny=deny)
        timeline = cut_story(script, clock, sp, f_end, stress, aim_s=float(d["aim_s"]))
    timeline = F.pin_voices(timeline, sup.get("voices") or [])
    fin = [section_finish(st, s, i, script) for i, s in enumerate(script["segments"])]
    fin, device_share = finish_budget(fin, seg_len, script, top=0.25 * float(d.get("device_budget", 1.0)))
    by_seg = {}
    for k, r in enumerate(timeline):
        by_seg.setdefault(seg_at(r[0]), []).append(k)
    gate_png, crt_png = overlays(sup.get("work") or os.path.join(os.path.dirname(sup["narration"]), "..", "finish"))
    keys["gate"] = P.image("finish_gate", dict(path=gate_png, w=W, h=H))
    keys["crt"] = P.image("finish_crt", dict(path=crt_png, w=W, h=H))
    cut_pts, drive_ids, look_pics, mono_spans, layer_spans = [], set(), [], [], []

    def is_mono(f0):
        return fin[seg_at(f0)] in ("mono", "gate")

    cards_shown = []

    def note(f0, f1, kind, name, rec, mode):
        P.pictures.append(dict(f=[f0, f1], kind=kind, name=name, path=rec["path"], shows=rec.get("shows"), mode=mode,
                               finish=fin[seg_at(f0)], use=rec.get("use"),
                               source=(rec.get("source") or {}).get("url") if kind == "clip" else rec.get("page"), time_range=rec.get("time_range"),
                               src=rec.get("src"), tier=rec.get("tier"), take=rec.get("take_i"), line=seg_at(f0),
                               motion=(rec.get("motion") or {}).get("p90")))
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
        kw = dict(blur=GROUND_BLUR_FIT, grade=dict(BED_FIT)) if fit_on else dict(blur=GROUND_BLUR, grade=dict(BED))
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

    n_win = [0, 0]

    def bleed(f0, f1, kind, name, rec, key):
        n_win[1] += 1
        if kind == "clip" and int(rec.get("w") or W) > int(rec.get("h") or H):
            # a landscape clip: a window within the build's budget, else it bleeds on a cover crop the driver moves
            if not fit_on or n_win[0] + 1 <= WINDOW_SHARE * n_win[1] + 1:
                n_win[0] += 1
                return window(f0, f1, kind, name, rec, key)
        if kind == "clip" and (rec.get("voice") or F.too_close(rec) and not (fit_on and not moving(rec))):
            # a broadcast close-up plays whole; a still one (no content motion) is driven instead, so it never sits static
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

    def prints_moving(run, paper=False):
        """A run of pictures as prints that keep moving: each lands tilted at its own aspect (0.78-0.86 of the width),
        then drifts in at the references' short-shot push and slides and turns a little toward the next print's seat; a
        clip print keeps playing under the prints that land over it."""
        r1 = timeline[run[-1]][1]
        drift = 0.149
        try:
            _, _, bake_ = F._engines()
            drift = float(bake_.DRIFT_RATE[2])
        except Exception:  # noqa: BLE001
            pass
        seats = ((0.47, 0.40), (0.54, 0.45), (0.46, 0.47), (0.55, 0.39), (0.50, 0.43))
        for q, k in enumerate(run):
            f0, f1, kind, name, rec = timeline[k]
            key = keys[rec["path"]]
            mono = is_mono(f0)
            ground(f0, f1, key, kind, rec, mono, paper=paper)
            n = r1 - f0
            asp = max(float(rec.get("w") or W), 1.0) / max(float(rec.get("h") or H), 1.0)
            pw = PRINT_W_FIT[0] + (PRINT_W_FIT[1] - PRINT_W_FIT[0]) * ((q * 37) % 10) / 9.0
            ph = pw * W / (asp * H)
            if ph > 0.62:
                pw, ph = pw * 0.62 / ph, 0.62
            (cx, cy), (nx, ny) = seats[q % len(seats)], seats[(q + 1) % len(seats)]
            rot0 = PRINT_ROT[q % len(PRINT_ROT)]
            s_ = [r4(1.0 + 0.10 * (1 - smooth(j / 6.0))) if j < 7 else r4(1.0 + drift * (j - 7) / FPS) for j in range(n)]
            xs = [r4(cx + 0.3 * (nx - cx) * smooth(min(j / max(n - 1, 1), 1.0))) for j in range(n)]
            ys = [r4(cy + 0.3 * (ny - cy) * smooth(min(j / max(n - 1, 1), 1.0))) for j in range(n)]
            rots = [round(rot0 * (1.0 - 0.35 * smooth(min(j / max(n - 1, 1), 1.0))), 3) for j in range(n)]
            kw = dict(z=1 + q, rot=rots, crop=[0.0, 0.0, 1.0, 1.0], radius=0.006, shadow=dict(a=0.62, blur=0.03, dx=0.004, dy=0.014),
                      s=s_, op=[min(1.0, 0.4 + 0.3 * j) for j in range(n)])
            if mono:
                kw["look"] = "mono_silver"
            if kind == "clip":
                take = rec.get("take") or (0.0, float(rec.get("seconds") or 0))
                t0 = float(rec["src"][0])
                kw["src"] = [round(t0, 4), round(max(min(float(take[1]), t0 + n / FPS), float(rec["src"][1])), 4)]
            it = P.card([f0, r1], key, [cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], **kw)
            it["x"], it["y"] = xs, ys
            layer_spans.append((f0, f1, 2 + q))
            note(f0, f1, kind, name, rec, "print")

    def designed(f0, f1, rec):
        """A designed type card from the line's own display item: the figure, term or date on a plate, landing with the
        bake bounce; a picture of its own, never another line's photograph."""
        P.page([f0, f1], "#141210", "#0B0A09", grain=0.05)
        text = str(rec.get("text") or "")
        text = text.upper() if st["upper"] else text
        vk = f"dsp_card{f0}"
        P.voices[vk] = dict(P.voices["dsp"])
        P.voices[vk]["px"] = P.R.fit_px(vk, text, 0.80 * W, float(st["dsp"]["px"]) * 1.6)
        n = f1 - f0
        try:
            _, _, bake_ = F._engines()
            kk = min(int(bake_.BOUNCE["n"]), n)
            ys = [r4(0.44 + float(q)) for q in bake_.bounce(kk, rest=0.0)] + [r4(0.44)] * (n - kk)
            bl = [round(float(q), 3) for q in bake_.bounce_blur(kk)] + [0.0] * (n - kk)
        except Exception:  # noqa: BLE001
            ys, bl = 0.44, 0.0
        P.word([f0, f1], text, vk, 0.5, ys, pal["accent"] if rec.get("role") == "figure" else pal["ink"], 1.0, align="c", z=3, blur=bl,
               role="big", stroke=dict(w=3.0, c="#0B0A09"))
        cards_shown.append(text)
        P.pictures.append(dict(f=[f0, f1], kind="designed", name=rec.get("role"), path=rec["path"], shows=text, mode="designed",
                               finish=fin[seg_at(f0)], use=1, source=None, time_range=None, src=None, tier="designed"))

    def prints(run, paper=False):
        """A run of pictures as prints: each lands tilted, beside and over the one before, and stays to the run's end."""
        if fit_on:
            return prints_moving(run, paper)
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
        if i in collage and not timeline[k][4].get("voice") and timeline[k][2] != "designed":
            # a run of prints: consecutive pictures of the collage line, five at most (a pinned voice breaks it)
            run, j = [k], k + 1
            while j < len(timeline) and len(run) < len(PRINT_AT) and seg_at(timeline[j][0]) == i and not timeline[j][4].get("voice") \
                    and timeline[j][2] != "designed":
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
            if kind == "designed":
                designed(f0, f1, rec)
            elif fit_on and kind == "clip" and rec["path"] in talk_fit and not rec.get("voice") and seg_at(f0) not in collage:
                # a person speaking on camera (live action, by the verdict): full frame only on a stretch where the lips are
                # still (measured), else in a window, never as a print outside a collage line
                still = [w for w in ((sup.get("talking") or {}).get(rec["path"]) or {}).get("still") or [] if w[1] - w[0] >= (f1 - f0) / FPS]
                if still:
                    t0 = float(still[0][0])
                    rec = dict(rec, src=[round(t0, 3), round(t0 + (f1 - f0) / FPS, 3)])
                    timeline[run[0]][4] = rec
                    bleed(f0, f1, kind, name, rec, keys[rec["path"]])
                else:
                    window(f0, f1, kind, name, rec, keys[rec["path"]])
            elif kind == "clip" and rec["path"] in talk and not rec.get("voice") and not fit_on:
                prints(run, paper=bool(st.get("paper")))       # a speaking face is never full frame under the narrator
            elif st["layout"] == "window" and not rec.get("voice"):
                window(f0, f1, kind, name, rec, keys[rec["path"]])
            else:
                bleed(f0, f1, kind, name, rec, keys[rec["path"]])
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
    if fit_on:
        # negatives on about one cut in twenty, on the voice's strongest stresses; white frames inside the bursts
        st_at = dict(stress)
        scored = sorted(((max([st_at.get(c + q, 0.0) for q in (-1, 0, 1)]), c) for c in cut_pts if 6 < c < f_end - 8), reverse=True)
        for v, c in scored[:max(1, int(0.05 * len(cut_pts)))]:
            if v > 0 and all(abs(c - g) >= 2 * FPS for g, _ in flashes):
                flashes.append((c, "neg"))
        short = [r[0] for r in timeline if r[1] - r[0] <= 5]
        for c in short[::3]:
            if all(abs(c - g) >= 2 * FPS for g, _ in flashes):
                flashes.append((c, "fill"))
    flashes.sort()
    white = []
    for f, mode in flashes:
        if 3 < f < f_end - 6:
            if mode == "neg":
                P.flash([f, f + 2], mode="neg", a=1.0, z=8)
            elif fit_on:
                n_w = 2 if (f, mode) in beats_at else 1          # a white frame, 1-2 frames at full strength (the judge's)
                P.flash([f, f + n_w], mode="fill", c="#FFFFFF", a=1.0, z=10)
                white.append((f, f + n_w))
            else:
                P.flash([f, f + 3], mode="fill", c="#FFFFFF", a=[0.8, 0.4, 0.12], z=8)
            P.events.append(f)
    ct = script.get("colour_turn")
    turn_ramp = None
    if isinstance(ct, int) and 0 < ct < nseg and any(fin[:ct]):
        # the turn is seen: its first shot comes up from black and white to the look's colour over seven frames
        f = min((c for c in cut_pts if c >= seg_f[ct] - 4), default=seg_f[ct])
        if f < f_end - 8:
            P.flash([f, f + 7], mode="mono", a=[r4(1.0 - q / 6.0) for q in range(7)], z=2)
            turn_ramp = f
    # ---- display words, then the captions at one seat
    sc = json.loads(json.dumps(script))
    if st["upper"]:
        for s in sc["segments"]:
            s["show"] = [(dict(x, text=str(x.get("text") or "").upper()) if isinstance(x, dict) else str(x).upper()) for x in s.get("show") or []]
    dropped, supers = [], []
    shown = F.display_words(P, clock, sc, "dsp", cap_y + 0.012, f_voice_end, skip_text=tuple(cards_shown), ink=pal["ink"], accent=pal["accent"],
                            gap_s=1.8, hold_s=1.05, px_max=float(st["dsp"]["px"]) * 1.25, dropped=dropped,
                            roles_skip=("name",) if fit_on else ()) if d.get("display", True) else []
    if fit_on and d.get("display", True):
        supers = name_supers(P, clock, sc, timeline, st, pal, f_voice_end, shown, dropped)
    lines = F.caption_build(P, clock, "cap", cap_y, f_end=f_voice_end + 8, hold=[(a - 1, b) for a, b, _ in shown], z=5,
                            script=sc if fit_on else None)
    # Bright archive drawings and white transition flashes need the caption
    # seat protected after the flashes, just before the screen-plane type.
    caption_pool(P, f_end, cap_y, float(d.get("pool", 0.70)), holes=white if fit_on else ())
    pay = next((i for i, s in enumerate(script["segments"]) if s.get("beat") == "payoff"), None)
    turn_f = min((c for c in cut_pts if c >= seg_f[pay] - 4), default=seg_f[pay]) if pay is not None else None
    film = dict(grain=float(st["grain"]), vignette=float(st["vignette"]), over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1114)), film)
    secs = f_voice_end / FPS
    np10 = len({(p["path"], tuple(p.get("src") or [])) for p in P.pictures}) / max(secs, 1) * 10     # prints and windows count
    takes = take_stats(P.pictures, footage) if fit_on else None
    if takes:
        np10 = takes["new_pictures_10s_n"] / max(secs, 1) * 10        # a picture is new only by distinct take
        d = dict(d, band=dict(d.get("band") or {}, punch_first=True, ease_min_s=0.4))
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
                 measured={**dict(new_pictures_10s=round(new_pics / max(secs, 1) * 10, 2), base_shots=len(timeline),
                               median_shot_s=round(sorted(r[1] - r[0] for r in timeline)[len(timeline) // 2] / FPS, 2) if timeline else None,
                               photo_most_uses=max(photo_uses.values()) if photo_uses else 0,
                               collage_lines=sorted(collage), layered_3plus_share=round(layered / max(f_voice_end, 1), 3),
                               flashes=len(flashes), finishes={s: sum(1 for x in fin if x == s) for s in ("mono", "gate", "crt", None) if any(x == s for x in fin)},
                               talking_clips=len(talk), talking_as_prints=sum(1 for p in P.pictures if p["path"] in talk and p["mode"] == "print"),
                               talking_full_frame=sum(1 for p in P.pictures if p["path"] in talk and p["mode"] != "print"), display_words=len(shown)),
                           **(fit_measures(P.pictures, timeline, sp, takes, secs, fin, seg_len, device_share, talk_fit, collage, seg_at,
                                           supers, dropped, turn_ramp, white, flashes, f_voice_end) if fit_on else {})},
                 display_dropped=[x for x in dropped if not (x["why"] == "printed elsewhere" and x["text"] in cards_shown)] if fit_on else None,
                 display_as_card=[x["text"] for x in dropped if x["why"] == "printed elsewhere" and x["text"] in cards_shown] if fit_on else None,
                 sound_design={k: v for k, v in (l1.get("sound_design") or {}).items() if k != "dropped"},
                 look=[f for f in look_f if 0 <= f < f_end])
    return l1, beats


def builder(style_id):
    def b(script, sup, direction=None):
        return build(script, sup, style_id, direction)
    return b
