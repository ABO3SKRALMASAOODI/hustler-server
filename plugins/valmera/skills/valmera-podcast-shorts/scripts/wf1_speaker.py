"""Who is speaking in the take, by signal: every face tracked through each of the show's camera shots, the motion of
each mouth set against the voice's own envelope.

A podcast's two-shot holds the guest and the host side by side, and the vertical short shows one of them. The take
used to keep the biggest face of three looks a shot: whoever sat nearer the camera, talking or not (19 Nov 2026
rating: Zuckerberg spoke the hook while the short showed Wang drinking beside him; his face first appeared at
16.5 s). Here the whole take is read once at RATE samples a second:

  faces    YuNet on every sample (eyes, nose and mouth corners), joined into tracks by position inside each of the
           show's own camera shots;
  mouth    dense optical flow over each face from one sample to the next; the face's own median flow is taken out
           (a head that turns is not a mouth that talks) and the vertical flow left in the mouth, less the same in
           the eye band (blinks, the codec, a smile), is the mouth's activity;
  voice    the speech band's RMS envelope (ffmpeg band-limits it; nothing listens);
  speaker  in each two-second window the face whose mouth moves while the voice is on: in a picture of several faces
           the one that moves most, clearly over the next; a face alone when it moves with the voice.

Each shot comes back with spans labelled `speaker` (a face's mouth follows the voice: its tracked box frames the
take), `listener` (the voice is on and no face on screen follows it: the speaker is off camera) or `quiet`, and
`wide` when several faces share the picture. Nothing reads meaning and no model is called.

Measured on the 19 Nov run's two-shots (Zuckerberg and Wang, faces about 80 px at SIDE): the talker's mouth reads
0.04-0.48 while the voice is on, the listener's -0.22 to 0.01.
"""
import math
import os
import subprocess

RATE = 10.0          # samples a second: a syllable is about 0.2 s, a mouth's opening and closing reads at 10 Hz
SIDE = 960           # frames are read this wide: a two-shot's face is about 80 px
WINDOW_S = 2.0       # who speaks is decided over windows this long (a two-shot's turn is about a sentence)
VOICED_DB = 15.0     # a sample is voiced when its speech-band level is within this of the shot's loud speech (p90)
QUIET_DB = -45.0     # a shot whose loud speech is under this has no voice
TALK_LEVEL = 0.03    # a talking mouth's median activity while the voice is on (the listeners read -0.22 to 0.01)
TALK_OVER = 2.0      # in a picture of several faces, the talker's mouth moves this many times more than the next one's
TALK_R = 0.2         # a face alone talks when its mouth follows the voice's envelope this closely ...
SMOOTH_S = 0.6       # ... the two compared as envelopes over this many seconds
MIN_SPAN_S = 0.8     # a label shorter than this joins its neighbour (a moment of doubt is not a cut)
FLOW = (96, 128)     # each face is compared at this size (w, h)


def _yunet(size=(SIDE, SIDE)):
    import cv2
    model = os.environ.get("YUNET_MODEL")
    if not model:
        try:
            import pictures
            model = pictures.YUNET
        except Exception:  # noqa: BLE001 - the stories engine's own model, where the skills keep it
            model = "/Users/masaoodi/Documents/Valmera/.valmera/models/face_detection_yunet_2023mar.onnx"
    return cv2.FaceDetectorYN.create(model, "", size, 0.6, 0.3, 5000)


def _sface():
    """OpenCV's SFace (the footage engine's identity model), or None where it is not installed."""
    import cv2
    if not hasattr(cv2, "FaceRecognizerSF"):
        return None
    for p in (os.environ.get("FOOTAGE_SFACE"), "/Users/masaoodi/Documents/Valmera/.valmera/models/face_recognition_sface_2021dec.onnx"):
        if p and os.path.exists(p):
            try:
                return cv2.FaceRecognizerSF.create(p, "")
            except Exception:  # noqa: BLE001 - no identity model: each shot's faces stand alone
                return None
    return None


def plausible(f):
    """A detection a face's landmarks agree with: the eyes over the nose over the mouth, inside the box."""
    x0, y0, x1, y1 = f["box"]
    bw, bh = x1 - x0, y1 - y0
    ey = (f["eyes"][0][1] + f["eyes"][1][1]) / 2
    my = (f["mouth"][0][1] + f["mouth"][1][1]) / 2
    if bw <= 0 or bh <= 0:
        return False
    return (y0 < ey < f["nose"][1] < my < y1 and 0.25 <= (my - ey) / bh <= 0.65 and x0 - 0.1 * bw < f["nose"][0] < x1 + 0.1 * bw)


def detect(det, img):
    """[{box [x0, y0, x1, y1], eyes [[x, y], [x, y]], nose [x, y], mouth [[x, y], [x, y]], score}] in frame fractions."""
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(img)
    out = []
    for r in (f if f is not None else []):
        x, y, bw, bh = [float(v) for v in r[:4]]
        if bw <= 4 or bh <= 4:
            continue
        p = [float(v) for v in r[4:14]]
        d = dict(box=[x / w, y / h, (x + bw) / w, (y + bh) / h], eyes=[[p[0] / w, p[1] / h], [p[2] / w, p[3] / h]],
                 nose=[p[4] / w, p[5] / h], mouth=[[p[6] / w, p[7] / h], [p[8] / w, p[9] / h]], score=float(r[14]), row=r.copy())
        if plausible(d):
            out.append(d)
    return out


def mouth_motion(g0, g1, f):
    """(mouth, eyes): the mean vertical optical flow left in the mouth and in the eye band of one face between two
    grey samples, once the face's own median flow is taken out. None when the face runs off the frame."""
    import cv2
    import numpy as np
    H, W = g1.shape
    x0, y0, x1, y1 = f["box"]
    bw, bh = x1 - x0, y1 - y0
    X0, X1 = int((x0 - 0.1 * bw) * W), int((x1 + 0.1 * bw) * W)
    Y0, Y1 = int((y0 - 0.05 * bh) * H), int((y1 + 0.15 * bh) * H)
    if X0 < 0 or Y0 < 0 or X1 > W or Y1 > H or X1 - X0 < 12 or Y1 - Y0 < 12:
        return None
    fw, fh = FLOW
    a = cv2.resize(g0[Y0:Y1, X0:X1], (fw, fh), interpolation=cv2.INTER_AREA)
    b = cv2.resize(g1[Y0:Y1, X0:X1], (fw, fh), interpolation=cv2.INTER_AREA)
    fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 2, 11, 3, 5, 1.1, 0)
    res = fl - np.median(fl.reshape(-1, 2), axis=0)

    def row(fy):
        return int((fy * H - Y0) / (Y1 - Y0) * fh)

    def col(fx):
        return int((fx * W - X0) / (X1 - X0) * fw)
    my = row((f["mouth"][0][1] + f["mouth"][1][1]) / 2)
    ey = row((f["eyes"][0][1] + f["eyes"][1][1]) / 2)
    mx0, mx1 = sorted((col(f["mouth"][0][0]), col(f["mouth"][1][0])))
    m = res[max(my - 10, 0):my + 14, max(mx0 - 4, 0):mx1 + 4]
    e = res[max(ey - 8, 0):ey + 8, fw // 6:fw - fw // 6]
    if m.size == 0 or e.size == 0:
        return None
    return float(np.abs(m[..., 1]).mean()), float(np.abs(e[..., 1]).mean())


def _ffprobe(ff):
    d, b = os.path.split(ff)
    return os.path.join(d, b.replace("ffmpeg", "ffprobe"))


def _size(ff, src):
    r = subprocess.run([_ffprobe(ff), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", src], capture_output=True, text=True, timeout=120)
    w, h = [int(v) for v in r.stdout.strip().split("\n")[0].split(",")[:2]]
    return w, h


def samples(ff, src, t0=0.0, dur=None, rate=RATE, width=SIDE):
    """The take's samples one by one, (k, BGR image) with sample k at t0 + k / rate: one streamed ffmpeg read, no
    frame kept."""
    import numpy as np
    sw, sh = _size(ff, src)
    w = min(width, sw)
    h = int(round(sh * w / sw / 2)) * 2
    cmd = [ff, "-v", "error", "-ss", f"{t0:.3f}", "-i", src] + (["-t", f"{dur:.3f}"] if dur else []) + \
          ["-an", "-vf", f"fps={rate},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n, k = w * h * 3, 0
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield k, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            k += 1
    finally:
        p.stdout.close()
        p.wait(timeout=60)


def envelope(ff, src, n, t0=0.0, rate=RATE):
    """The speech band's level (dB) at each of n samples: RMS of the 1/rate s from the sample on."""
    import numpy as np
    sr = 16000
    r = subprocess.run([ff, "-v", "error", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{n / rate + 0.2:.3f}", "-vn", "-ac", "1",
                        "-ar", str(sr), "-af", "highpass=f=300,lowpass=f=3400", "-f", "s16le", "-"], capture_output=True, timeout=600)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    hop = int(sr / rate)
    out = np.full(n, -90.0, np.float32)
    for k in range(n):
        seg = x[k * hop:(k + 1) * hop]
        if len(seg):
            out[k] = 20 * math.log10(float(np.sqrt((seg ** 2).mean())) + 1e-6)
    return out


def voice_mask(db):
    """(voiced samples, the envelope 0..1): within VOICED_DB of the stretch's loud speech, none in near silence."""
    import numpy as np
    db = np.asarray(db, np.float32)
    if not len(db):
        return np.zeros(0, bool), np.zeros(0, np.float32)
    loud = float(np.percentile(db, 90))
    if loud < QUIET_DB:
        return np.zeros(len(db), bool), np.zeros(len(db), np.float32)
    return db >= loud - VOICED_DB, _smooth(np.clip((db - (loud - 30.0)) / 30.0, 0, 1), int(SMOOTH_S * RATE))


def _smooth(x, n):
    import numpy as np
    x = np.asarray(x, np.float32)
    if n <= 1 or len(x) < 2:
        return x
    pad = np.pad(x, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(pad, np.ones(n) / n, mode="valid").astype(np.float32)


def _corr(a, b):
    import numpy as np
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 6:
        return 0.0
    x, y = a[ok] - a[ok].mean(), b[ok] - b[ok].mean()
    d = float(np.sqrt((x ** 2).sum() * (y ** 2).sum()))
    return float((x * y).sum() / d) if d > 1e-9 else 0.0


def who_speaks(acts, voiced, env):
    """Over one window of samples: (the index of the track whose mouth follows the voice, or None; the measures).
    In a picture of several faces the talker's mouth moves most while the voice is on, TALK_OVER times the next
    one's or more; a face alone talks when it moves well (twice TALK_LEVEL), or moves and follows the voice's
    envelope, or moves more while the voice is on than while it rests. None with the voice on: the speaker is off
    camera."""
    import numpy as np
    voiced = np.asarray(voiced, bool)
    rows = []
    for ti, act in enumerate(acts):
        act = np.asarray(act, np.float32)
        seen = ~np.isnan(act)
        on = act[voiced & seen]
        if len(on) < max(3, int(0.3 * voiced.sum())):
            continue                                    # not on screen while the voice is
        off = act[~voiced & seen]
        r = _corr(_smooth(np.nan_to_num(act, nan=0.0), int(SMOOTH_S * RATE)), np.asarray(env, np.float32)) if seen.sum() >= 6 else 0.0
        rows.append(dict(track=ti, on=round(float(np.median(on)), 4), off=round(float(np.median(off)), 4) if len(off) >= 3 else None,
                         r=round(r, 3)))
    rows.sort(key=lambda x: -x["on"])
    if not rows or rows[0]["on"] < TALK_LEVEL:
        return None, rows
    best = rows[0]
    if len(rows) > 1:
        return (best["track"] if best["on"] >= TALK_OVER * max(rows[1]["on"], 0.01) else None), rows
    alone = best["on"] >= 2 * TALK_LEVEL or best["r"] >= TALK_R or (best["off"] is not None and best["on"] > best["off"] + 0.02)
    return (best["track"] if alone else None), rows


def runs_of(labels, short):
    """Per-sample labels as runs [a, b, label]; a run shorter than `short` samples joins its longer neighbour."""
    runs = []
    for k, lab in enumerate(labels):
        if runs and runs[-1][2] == lab:
            runs[-1][1] = k + 1
        else:
            runs.append([k, k + 1, lab])
    while len(runs) > 1:
        i = next((i for i, r in enumerate(runs) if r[1] - r[0] < short), None)
        if i is None:
            break
        if i == 0:
            j = 1
        elif i == len(runs) - 1:
            j = i - 1
        else:
            j = i - 1 if runs[i - 1][1] - runs[i - 1][0] >= runs[i + 1][1] - runs[i + 1][0] else i + 1
        runs[j][0], runs[j][1] = min(runs[j][0], runs[i][0]), max(runs[j][1], runs[i][1])
        del runs[i]
        merged = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    return runs


def track_box(track, a=None, b=None):
    """A track's face over samples [a, b), in frame fractions: the box its head moves within (the 10th to 90th
    percentile of its edges, so the crop keeps a head that turns or leans whole; its median box when it moves
    further than 2.2 face widths), its eyes, and where its centre goes (a dozen points)."""
    import numpy as np
    fs = [f for k, f in track if (a is None or a <= k) and (b is None or k < b)] or [f for _, f in track]
    med = [float(np.median([f["box"][i] for f in fs])) for i in range(4)]
    box = [float(np.percentile([f["box"][i] for f in fs], q)) for i, q in ((0, 10), (1, 10), (2, 90), (3, 90))]
    if box[2] - box[0] > 2.2 * (med[2] - med[0]):
        box = med
    box = [round(v, 5) for v in box]
    eyes = [[round(float(np.median([f["eyes"][e][c] for f in fs])), 5) for c in (0, 1)] for e in (0, 1)]
    path = [[round((f["box"][0] + f["box"][2]) / 2, 4), round((f["box"][1] + f["box"][3]) / 2, 4)] for f in fs]
    return dict(box=box, eyes=eyes, path=path[::max(1, len(path) // 12)])


class _Tracker:
    """Faces joined into tracks inside one camera shot: a face joins the track whose last face (seen within a second)
    sits nearest, under 60% of a face's width away; otherwise it starts one. Each track keeps its mouth's activity
    and up to three identity embeddings (SFace) of its clearest looks."""

    def __init__(self, rec=None):
        self.tracks = []        # [{"faces": [(k, face)], "act": {k: activity}, "emb": [vector]}]
        self.rec = rec

    def add(self, k, faces, img, gray, prev_gray):
        taken = set()
        for f in sorted(faces, key=lambda f: -(f["box"][2] - f["box"][0])):
            cx, cy, fw = (f["box"][0] + f["box"][2]) / 2, (f["box"][1] + f["box"][3]) / 2, f["box"][2] - f["box"][0]
            best, bd = None, None
            for ti, tr in enumerate(self.tracks):
                kk, g = tr["faces"][-1]
                if ti in taken or k - kk > RATE:
                    continue
                gx, gy, gw = (g["box"][0] + g["box"][2]) / 2, (g["box"][1] + g["box"][3]) / 2, g["box"][2] - g["box"][0]
                d = math.hypot(cx - gx, cy - gy) / max(gw, fw, 1e-3)
                if d < 0.6 and (bd is None or d < bd):
                    best, bd = ti, d
            if best is None:
                self.tracks.append(dict(faces=[], act={}, emb=[]))
                best = len(self.tracks) - 1
            tr = self.tracks[best]
            taken.add(best)
            if prev_gray is not None and tr["faces"] and tr["faces"][-1][0] == k - 1:
                mm = mouth_motion(prev_gray, gray, f)
                if mm:
                    tr["act"][k] = mm[0] - mm[1]
            if self.rec is not None and len(tr["emb"]) < 3 and len(tr["faces"]) % 5 == 0 and f.get("row") is not None:
                try:
                    tr["emb"].append(self.rec.feature(self.rec.alignCrop(img, f["row"])).ravel().copy())
                except Exception:  # noqa: BLE001 - a look that cannot be embedded: the track has others
                    pass
            tr["faces"].append((k, {q: v for q, v in f.items() if q != "row"}))


def identities(shot_tracks, match=0.363):
    """One person across the show's camera shots: [[identity id per track] per shot]. Tracks are joined by their
    SFace embeddings (cosine of the means at or over `match`, SFace's published threshold for one identity); two
    tracks of one shot are never one person. A track with no embedding is a person of its own."""
    import numpy as np

    def unit(v):
        v = np.asarray(v, np.float32).ravel()
        return v / (np.linalg.norm(v) + 1e-9)
    people, out = [], []                                   # people: [mean unit embedding]
    order = sorted(((si, ti) for si, trs in enumerate(shot_tracks) for ti in range(len(trs))),
                   key=lambda st: -len(shot_tracks[st[0]][st[1]]["faces"]))
    ids = {}
    for si, ti in order:
        tr = shot_tracks[si][ti]
        if not tr.get("emb"):
            ids[(si, ti)] = len(people)
            people.append(None)
            continue
        v = unit(np.mean([unit(e) for e in tr["emb"]], axis=0))
        used = {ids[(si, tj)] for tj in range(len(shot_tracks[si])) if (si, tj) in ids}
        best, bs = None, match
        for pi, c in enumerate(people):
            if c is None or pi in used:
                continue
            sim = float(c @ v)
            if sim >= bs:
                best, bs = pi, sim
        if best is None:
            ids[(si, ti)] = len(people)
            people.append(v)
        else:
            ids[(si, ti)] = best
            people[best] = unit(people[best] + v)
    for si, trs in enumerate(shot_tracks):
        out.append([ids[(si, ti)] for ti in range(len(trs))])
    return out


def turn_speakers(windows, turns, long_run=3):
    """Who carries each turn: {window index: person or None}. windows: [{"t", "who" (person or None), "on"
    ({person: activity}), "voiced"}] in order; turns: [(t0, t1)] (the captions' own speaker changes; one turn when
    they mark none). In a turn the person whose mouth won the most windows speaks it (ties: the most activity), on
    every window where he is on screen; a run of `long_run` windows or more won by someone else is his own turn
    (a question the captions did not mark). Windows of a turn whose speaker is not on screen get None."""
    out = {}
    for t0, t1 in turns:
        cur = [i for i, w in enumerate(windows) if t0 <= w.get("tc", w["t"] + WINDOW_S / 2) < t1 and w.get("voiced")]
        if not cur:
            continue
        # runs of one winner, to find a long run won by another person
        runs, run = [], []
        for i in cur:
            if run and windows[i]["who"] != windows[run[-1]]["who"]:
                runs.append(run)
                run = []
            run.append(i)
        if run:
            runs.append(run)

        def main_of(ix):
            tally = {}
            for i in ix:
                p = windows[i]["who"]
                if p is not None:
                    w = tally.setdefault(p, [0, 0.0])
                    w[0] += 1
                    w[1] += float(windows[i]["on"].get(p, 0.0))
            return max(tally, key=lambda p: (tally[p][0], tally[p][1])) if tally else None
        main = main_of(cur)
        own = [r for r in runs if len(r) >= long_run and windows[r[0]]["who"] is not None and windows[r[0]]["who"] != main]
        for i in cur:
            r = next((r for r in own if i in r), None)
            p = windows[r[0]]["who"] if r else main
            out[i] = p if p is not None and p in windows[i]["on"] else None
    return out


def read_take(ff, src, shots, dur, turns=None, det=None):
    """The take's camera shots, each {t0, t1, faces, spans, wide, tracks, measures} (seconds of the take):
    faces    the speaker's tracked box over his longest span (else the face on screen longest), the framing;
    spans    [{t0, t1, who: speaker|listener|quiet, box, track, person}]: who carries each stretch of the shot;
    wide     several faces share the picture.
    shots: [(t0, t1)] the show's own cuts; turns: [(t0, t1)] stretches one person speaks (the captions' speaker
    changes), None for one turn."""
    import cv2
    import numpy as np
    n_all = int(math.ceil(float(dur) * RATE)) + 1
    db = envelope(ff, src, n_all)
    bounds = [(int(round(a * RATE)), max(int(round(b * RATE)), int(round(a * RATE)) + 1)) for a, b in shots]
    rec_model = _sface()
    trackers = [_Tracker(rec_model) for _ in bounds]
    prev, which, n = None, 0, 0
    for k, img in samples(ff, src):
        if det is None:
            det = _yunet((img.shape[1], img.shape[0]))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        while which < len(bounds) - 1 and k >= bounds[which][1]:
            which, prev = which + 1, None              # a new camera shot: no track and no flow cross a cut
        if bounds and bounds[which][0] <= k < bounds[which][1]:
            trackers[which].add(k, detect(det, img), img, gray, prev)
        prev, n = gray, k + 1
    shot_tracks = []
    for (a, b), tk in zip(bounds, trackers):
        m = max(min(b, n) - a, 1)
        shot_tracks.append([tr for tr in tk.tracks if len(tr["faces"]) >= max(3, int(0.2 * m))])
    people = identities(shot_tracks)
    win = max(int(WINDOW_S * RATE), 6)
    windows = []                                        # every window of every shot, in order
    for si, ((a, b), (t0, t1)) in enumerate(zip(bounds, shots)):
        b = min(b, max(n, a + 1))
        m = b - a
        tracks = shot_tracks[si]
        voiced, env = voice_mask(db[a:b])
        acts = [np.array([tr["act"].get(k, np.nan) for k in range(a, b)], np.float32) for tr in tracks]
        # windows tile each stretch between the shot's edges and the captions' speaker changes, so a cut to the
        # person who answers lands on his first word
        edges = sorted({0, m, *[int(round((tt - t0) * RATE)) for tt, _ in (turns or []) if 0 < (tt - t0) * RATE < m]})
        spans_w = []
        for e0, e1 in zip(edges, edges[1:]):
            for w0 in range(e0, e1, win):
                w1 = min(e1, w0 + win)
                if w1 - w0 < win // 2 and spans_w and spans_w[-1][1] == w0 and spans_w[-1][0] >= e0:
                    spans_w[-1] = (spans_w[-1][0], w1)      # a short last window reads with the one before it
                else:
                    spans_w.append((w0, w1))
        for w0, w1 in spans_w:
            v = voiced[w0:w1]
            row = dict(shot=si, w0=w0, w1=w1, t=round(t0 + w0 / RATE, 2), tc=round(t0 + (w0 + w1) / 2 / RATE, 2),
                       voiced=bool(len(v) and v.mean() >= 0.25), who=None, on={}, tracks={})
            if row["voiced"]:
                ti, rows = who_speaks([x[w0:w1] for x in acts], v, env[w0:w1])
                row["faces"] = rows
                row["who"] = people[si][ti] if ti is not None else None
                for r in rows:
                    row["on"][people[si][r["track"]]] = r["on"]
                    row["tracks"][people[si][r["track"]]] = r["track"]
            windows.append(row)
    # one person across the camera shots needs the identity model; without it each window's own reading stands
    carry = turn_speakers(windows, turns or [(0.0, float(dur) + 1.0)]) if rec_model is not None else \
        {i: w["who"] for i, w in enumerate(windows)}
    out = []
    for si, ((a, b), (t0, t1)) in enumerate(zip(bounds, shots)):
        b = min(b, max(n, a + 1))
        m = b - a
        tracks = shot_tracks[si]
        rec = dict(t0=round(float(t0), 3), t1=round(float(t1), 3), faces=[], spans=[], tracks=len(tracks),
                   wide=sum(1 for tr in tracks if len(tr["faces"]) >= 0.5 * m) >= 2, measures=[])
        per = []
        for i, w in enumerate(windows):
            if w["shot"] != si:
                continue
            if not w["voiced"]:
                lab = ("quiet", None)
            else:
                p = carry.get(i)
                lab = ("speaker", w["tracks"][p]) if p is not None else ("listener", None)
                rec["measures"].append(dict(t=w["t"], who=lab[0], track=lab[1], person=p, won=w["who"], faces=w.get("faces")))
            per.extend([lab] * (w["w1"] - w["w0"]))
        per = per[:m] + [per[-1] if per else ("quiet", None)] * max(0, m - len(per))
        longest = max(range(len(tracks)), key=lambda ti: len(tracks[ti]["faces"])) if tracks else None
        for r0, r1, (who, ti) in runs_of(per, int(MIN_SPAN_S * RATE)):
            tj = ti if ti is not None else longest
            rec["spans"].append(dict(t0=round(t0 + r0 / RATE, 3), t1=round(min(t0 + r1 / RATE, t1), 3) if r1 < m else rec["t1"],
                                     who=who, track=ti, person=people[si][ti] if ti is not None else None,
                                     box=track_box(tracks[tj]["faces"], a + r0, a + r1) if tj is not None else None))
        if not rec["spans"]:
            rec["spans"] = [dict(t0=rec["t0"], t1=rec["t1"], who="quiet", track=None, person=None, box=None)]
        talk = [s for s in rec["spans"] if s["who"] == "speaker" and s["box"]]
        main = max(talk, key=lambda s: s["t1"] - s["t0"]) if talk else None
        bx = main["box"] if main else (track_box(tracks[longest]["faces"]) if longest is not None else None)
        if bx:
            rec["faces"] = [dict(box=bx["box"], eyes=bx["eyes"], score=1.0, speaker=bool(main))]
        out.append(rec)
    return out


def on_camera(cuts):
    """The share of the take's voiced time whose speaker is on camera (data for whoever ranks windows)."""
    talk = sum(s["t1"] - s["t0"] for c in cuts for s in c.get("spans") or [] if s.get("who") == "speaker")
    voiced = sum(s["t1"] - s["t0"] for c in cuts for s in c.get("spans") or [] if s.get("who") in ("speaker", "listener"))
    return round(talk / voiced, 3) if voiced else None


def listener_spans(cuts, min_s=0.0):
    """[(t0, t1)] of the take where the voice is on and no face on screen speaks it, longer than min_s."""
    out = []
    for c in cuts:
        for s in c.get("spans") or []:
            if s.get("who") == "listener" and s["t1"] - s["t0"] > min_s:
                if out and abs(out[-1][1] - s["t0"]) < 1e-3:
                    out[-1] = (out[-1][0], s["t1"])
                else:
                    out.append((s["t0"], s["t1"]))
    return out
