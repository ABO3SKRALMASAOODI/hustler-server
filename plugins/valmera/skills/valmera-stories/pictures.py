"""Supply for a narrated film: the narrator's voice and real pictures of what it names, each with its licence.

Workflow 3 and workflow 4 share it. Everything lands under the run's supply folder.

    narrate(script, out, band, label)     the narrate tool (VoxCPM2, local) through heavy.sh, then its VOICE gate
    pictures(named, out, per)             each named subject: Wikimedia Commons (by Wikidata 'depicts', then text) and
                                          Openverse through the worker's pictures route (broll_source.find_named),
                                          checked by a vision model against what the picture must show, downloaded,
                                          licence and credit kept with every file
    commons_file(title, out)              one exact Commons file (the One Photograph style) with its licence
    faces(path)                           faces in a photograph (YuNet), as boxes in picture fractions
    sound_facts(ids)                      the library sounds a film may place, with their peaks

Never publishes, never picks music. A picture without a free licence never reaches the film.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(os.path.dirname(HERE), "_shell")
V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
HEAVY = f"{VV}/week-1106/heavy.sh"
NARRATE_LOCK = f"{VV}/week-1106/.narrate.lock"
NARRATE_PY = os.environ.get("NARRATE_PY", f"{VV}/venv-narrate/bin/python")
NARRATE = os.path.join(SHELL, "narrate", "narrate.py")
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")
YUNET = os.environ.get("YUNET_MODEL", f"{VV}/models/face_detection_yunet_2023mar.onnx")
UA = "Mozilla/5.0 (compatible; ValmeraStories/1.0)"


def worker_dir():
    """The worker beside this plugin (repo checkout), else the synced main checkout."""
    if os.environ.get("VALMERA_WORKER"):
        return os.environ["VALMERA_WORKER"]
    d = HERE
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.exists(os.path.join(d, "worker", "broll_source.py")):
            return os.path.join(d, "worker")
    return f"{V}/worktrees/skill-main/worker"


def _worker():
    w = worker_dir()
    if w not in sys.path:
        sys.path.insert(0, w)
    import broll_source
    return broll_source


# ----------------------------------------------------------------- the voice

def narrate(nscript, out, band="03", label="narrate", env=None, timeout=5400):
    """narration.wav and words.json in `out`. A stopped render resumes at its next chunk (the tool keeps _state)."""
    os.makedirs(out, exist_ok=True)
    sp = os.path.join(out, "narration_script.json")
    json.dump(nscript, open(sp, "w"), ensure_ascii=False, indent=1)
    if not os.path.exists(os.path.join(out, "words.json")):
        # One narration at a time on this Mac: VoxCPM2 shares the Apple GPU, and three at once measured a chunk in
        # 5-10 min against 20-50 s alone (13 Nov). The lock is the kernel's: a dead holder releases it.
        import fcntl
        with open(NARRATE_LOCK, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            r = subprocess.run(["zsh", HEAVY, label, NARRATE_PY, NARRATE, "render", sp, "--out", out, "--band", band],
                               cwd=os.path.dirname(NARRATE), capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0 or not os.path.exists(os.path.join(out, "words.json")):
            raise RuntimeError(f"narrate failed ({r.returncode}): {(r.stderr or r.stdout)[-800:]}")
    if not os.path.exists(os.path.join(out, "check.json")):
        subprocess.run([NARRATE_PY, NARRATE, "check", out, "--band", band], cwd=os.path.dirname(NARRATE),
                       capture_output=True, text=True, timeout=1800, env=env)
    chk = json.load(open(os.path.join(out, "check.json"))) if os.path.exists(os.path.join(out, "check.json")) else {}
    w = json.load(open(os.path.join(out, "words.json")))
    g, ib = chk.get("gate") or {}, chk.get("in_band") or {}
    return dict(wav=os.path.join(out, "narration.wav"), words=os.path.join(out, "words.json"), duration=w["duration"],
                voice=w.get("voice"), gate=dict(ok=g.get("pass"), wer=(chk.get("wer") or {}).get("wer"), checks=g,
                                                **{k: (ib.get(k) or {}).get("value") for k in ("wpm", "wpm_artic", "f0_range_st", "voice_lufs")}))


# ----------------------------------------------------------------- pictures of what is named

def _record(c, got, name):
    return dict(name=name, path=got["path"], w=int(got.get("width") or 0), h=int(got.get("height") or 0),
                provider=c.get("provider"), title=c.get("title"), licence=c.get("license"), credit=c.get("credit"),
                licence_url=c.get("license_url"), page=c.get("page_url") or c.get("page"), url=c.get("_url"), shows=c.get("shows"),
                note=c.get("license_note"))


def pictures(named, out, per=4, min_px=700):
    """{name: [picture records]} for every named subject, best first, and a report per subject."""
    B = _worker()
    os.makedirs(out, exist_ok=True)

    def one(subj):
        s = dict(name=subj["name"], searches=(subj.get("searches") or [subj["name"]])[:3],
                 must_show=subj.get("must_show") or subj["name"], **{"is": subj.get("is", "thing")})
        try:
            kept, rep = B.find_named(s, kind=B.KIND_PHOTO, count=per + 2, workdir=out)
        except Exception as e:  # noqa: BLE001 - one subject without pictures is a gap, not a stop
            return subj["name"], [], dict(subject=subj["name"], error=str(e)[:200])
        recs, fails = [], []
        for c in kept:
            if len(recs) >= per:
                break
            if max(int(c.get("width") or 0), int(c.get("height") or 0)) and \
                    max(int(c.get("width") or 0), int(c.get("height") or 0)) < min_px:
                fails.append(f"{c.get('id')}: {c.get('width')}x{c.get('height')} is too small")
                continue
            got = None
            for attempt in range(3):            # Commons answers a burst with 429: wait and ask again
                try:
                    got = B.fetch_local(c, os.path.join(out, "pics"), 1080, 1920)
                    break
                except Exception as e:  # noqa: BLE001
                    fails.append(f"{c.get('id')}: {str(e)[:120]}")
                    time.sleep(3 * (attempt + 1))
            if not got:
                continue
            if min(int(got.get("width") or 0), int(got.get("height") or 0)) < min_px * 0.6 or max(int(got.get("width") or 0), int(got.get("height") or 0)) < min_px:
                fails.append(f"{c.get('id')}: {got.get('width')}x{got.get('height')} is too small")
                continue
            recs.append(_record(c, got, subj["name"]))
        rep = {k: v for k, v in rep.items() if k in ("subject", "candidates", "kept", "say", "errors", "usd")}
        rep["downloaded"] = len(recs)
        if fails:
            rep["download_errors"] = fails[:6]
        return subj["name"], recs, rep

    res, reports, usd = {}, [], 0.0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for name, recs, rep in pool.map(one, named):
            res[name] = recs
            reports.append(rep)
            usd += float(rep.get("usd") or 0)
    return res, reports, round(usd, 4)


def commons_file(title, out, want_w=4000):
    """One exact Wikimedia Commons file, its licence and credit from the file's own metadata. Raises when the licence
    is not free (public domain, CC0, CC BY, CC BY-SA)."""
    B = _worker()
    title = title if title.startswith("File:") else "File:" + title
    q = urllib.parse.urlencode(dict(action="query", titles=title, prop="imageinfo", iiprop="url|size|extmetadata|mime",
                                    iiurlwidth=want_w, format="json"))
    req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + q, headers={"User-Agent": UA})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    page = next(iter(d["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise RuntimeError(f"{title} is not on Wikimedia Commons")
    ii = page["imageinfo"][0]
    md = ii.get("extmetadata") or {}
    raw = (md.get("LicenseShortName") or {}).get("value") or ""
    lic = B.canon_licence(raw, (md.get("LicenseUrl") or {}).get("value"))
    if not lic:
        raise RuntimeError(f"{title}: licence {raw!r} is not free")
    url = ii.get("thumburl") if ii.get("width", 0) > want_w and ii.get("thumburl") else ii["url"]
    os.makedirs(out, exist_ok=True)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
    path = os.path.join(out, "photo" + (ext if ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff") else ".jpg"))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    if not path.endswith((".jpg", ".jpeg", ".png")):
        jp = os.path.join(out, "photo.jpg")
        subprocess.run([FF, "-v", "error", "-y", "-i", path, "-q:v", "2", jp], check=True)
        path = jp
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    import html
    import re
    strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()   # noqa: E731 - markup off a metadata field
    return dict(name=title, path=path, w=w, h=h, provider="wikimedia", title=title, licence=lic[0], licence_url=lic[1],
                credit=strip((md.get("Artist") or {}).get("value"))[:160], page=ii.get("descriptionurl"), url=ii["url"],
                description=strip((md.get("ImageDescription") or {}).get("value"))[:1500])


def faces(path, max_side=2400, score=0.6):
    """Faces in a photograph, YuNet: [{box: [x0, y0, x1, y1] in picture fractions, eyes: [[x, y], [x, y]], score}]."""
    import cv2
    im = cv2.imread(path)
    h, w = im.shape[:2]
    k = min(1.0, max_side / max(w, h))
    small = cv2.resize(im, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else im
    det = cv2.FaceDetectorYN.create(YUNET, "", (small.shape[1], small.shape[0]), score, 0.3, 5000)
    _, f = det.detect(small)
    out = []
    for r in (f if f is not None else []):
        x, y, bw, bh = [float(v) / k for v in r[:4]]
        out.append(dict(box=[round(x / w, 5), round(y / h, 5), round((x + bw) / w, 5), round((y + bh) / h, 5)],
                        eyes=[[round(float(r[4]) / k / w, 5), round(float(r[5]) / k / h, 5)], [round(float(r[6]) / k / w, 5), round(float(r[7]) / k / h, 5)]],
                        score=round(float(r[14]), 3)))
    out.sort(key=lambda d: (round(d["box"][1] * 8), d["box"][0]))
    return out


def sound_facts(ids=("tick_1", "click_2", "pop_1", "whoosh_soft_1", "shutter_2", "impact_1", "cash_register_1")):
    """The library sounds a film may place: file, where its peak is, how loud its peak is."""
    lib_dir = os.path.join(worker_dir(), "sound_library")
    sounds = {}
    for m in json.load(open(os.path.join(lib_dir, "manifest.json"))):
        if m["id"] in ids:
            r = subprocess.run([FF, "-hide_banner", "-i", os.path.join(lib_dir, m["file"]), "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True)
            mx = next(float(ln.split("max_volume:")[1].split()[0]) for ln in r.stderr.splitlines() if "max_volume" in ln)
            sounds[m["id"]] = dict(file=m["file"], peak_s=float(m["peak_s"]), max_db=mx, path=os.path.join(lib_dir, m["file"]))
    return sounds


def locate_detail(main_path, detail_path, work_w=1600):
    """Where a detail crop of a photograph sits inside it: normalised cross-correlation over scales (signal only).
    Returns ([x0, y0, x1, y1] in fractions of the main photograph, score), score 1.0 for a perfect match."""
    import cv2
    import numpy as np
    m = cv2.imread(main_path, cv2.IMREAD_GRAYSCALE)
    t = cv2.imread(detail_path, cv2.IMREAD_GRAYSCALE)
    if m is None or t is None:
        return None, 0.0
    k = work_w / m.shape[1]
    m = cv2.resize(m, (work_w, int(round(m.shape[0] * k))), interpolation=cv2.INTER_AREA)
    m = cv2.equalizeHist(m)
    best = (None, -1.0)
    for frac in np.geomspace(0.03, 0.7, 36):                   # the detail's width as a share of the photograph's
        tw = int(round(work_w * frac))
        th = int(round(t.shape[0] * tw / t.shape[1]))
        if tw < 24 or th < 24 or th >= m.shape[0] or tw >= m.shape[1]:
            continue
        tt = cv2.equalizeHist(cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA))
        r = cv2.matchTemplate(m, tt, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx > best[1]:
            best = ([loc[0] / m.shape[1], loc[1] / m.shape[0], (loc[0] + tw) / m.shape[1], (loc[1] + th) / m.shape[0]], float(mx))
    return ([round(v, 5) for v in best[0]] if best[0] else None), round(best[1], 3)
