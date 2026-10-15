"""Track 4C acceptance (ROADMAP 4C): the B-roll director end to end.

    python -m benchmarks.refbench.at4c AT-4C-1 [AT-4C-2 AT-4C-3 AT-4C-4]
    python -m benchmarks.refbench.at4c AT-4C-1 --judge judged.json

AT-4C-1  V01 thiel_3334 (a talking head with no B-roll): source_broll over
         eight story moments from the kept transcript, the real libraries
         (keyless lanes: Openverse, Wikimedia Commons, NASA), a final 9:16
         render. Pass: >= 80% on-topic (judged blind from a board that shows
         only the rendered cutaway frame and the words under it), dE2000 <=
         5 between each cutaway's mean colour and the hero's in the RENDER,
         every licence line complete, 0 watermarks in the rendered frames.
AT-4C-2  public-domain archival (NASA, the Internet Archive): PD licence
         lines present on every archival cutaway.
AT-4C-3  20 adversarial clips (10 watermarked, 10 starved encodes) made from
         fixture footage: every one rejected by the scorer (and the clean
         controls' false-rejection count reported).
AT-4C-4  no keys and no keyless library: the honest-off reply with a shoot
         list; nothing written.

Everything stays internal: downloads and renders go to the W4 scratch dir,
fixtures are internal-only, nothing is uploaded or published.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

from . import out_dir

SCRATCH = os.path.expanduser("~/Documents/Valmera/.valmera/engines/w4/at4c")
BOARDS = os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4/4C")


# ── a project around a fixture window ────────────────────────────────────

class FakeDB:
    def __init__(self):
        self.keys = {}
        self.assets = []

    def run(self, fn, *a, **kw):
        import db as dbx
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        if fn is dbx.insert_asset:
            _pid, kind, key = a[:3]
            self.assets.append({"kind": kind, "key": key, **kw})
            self.keys[key] = {"storage_key": key,
                              "duration_s": kw.get("duration_s")}
            return len(self.assets)
        if fn is dbx.latest_asset:
            return None
        raise AssertionError(f"unexpected db call {fn}")


class Store:
    """storage.* on a local directory (internal scratch only)."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.map = {}

    def install(self):
        import storage
        storage.upload_file = self.upload_file
        storage.download_to = self.download_to
        storage.object_bytes = self.object_bytes
        storage.exists = lambda k: k in self.map
        storage.delete_keys = lambda keys: [self.map.pop(k, None)
                                            for k in keys]

    def upload_file(self, path, key, ctype=None):
        dst = os.path.join(self.root, key.replace("/", "_"))
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy(path, dst)
        self.map[key] = dst

    def download_to(self, key, local):
        shutil.copy(self.map[key], local)

    def object_bytes(self, key):
        p = self.map.get(key)
        return os.path.getsize(p) if p and os.path.exists(p) else None


class Ctx:
    def __init__(self, workdir, edl, index, proxy, duration):
        self.db = FakeDB()
        self.project_id = 7
        self.workdir = workdir
        self.duration = duration
        self.has_main_video = True
        self._edl = edl
        self.version = 1
        self.writes = []
        self.index = index
        self._proxy = proxy
        self.pending_images = []
        self.job = {"payload": {"execution_policy": "legacy"}}
        self.user_message = None

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"

    def proxy_path(self):
        return self._proxy


def _project(vfl_id, name, keep, words_path=None):
    from . import vfl
    import qa_metrics
    from schemas import validate_edl
    w = vfl.window(vfl_id, name)
    info = qa_metrics.probe(w["path"])
    words = []
    if words_path and os.path.exists(words_path):
        for i, x in enumerate(json.load(open(words_path))):
            words.append({"w": x["w"], "t0": x["t0"], "t1": x["t1"],
                          "p": x.get("p")})
    index = {"words": words, "silences": [], "sentences": [],
             "video": {"width": info["width"], "height": info["height"],
                       "fps": info["fps"], "duration": info["duration"]}}
    edl = {"keep": [list(keep)], "frame": {"ratio": "9:16", "mode": "crop",
                                           "focus_x": 0.55}}
    edl = validate_edl(edl, float(info["duration"])).model_dump()
    return w, info, index, edl


def render(edl, src, index, out, asset_locals, preview=False):
    import fxgraph
    import gradewin
    import renderer
    from schemas import validate_edl
    import qa_metrics
    dur = float(qa_metrics.probe(src)["duration"])
    e = validate_edl(copy.deepcopy(edl), dur).model_dump()
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    gradewin.ASSET_LOCALS.update(asset_locals)
    rows = []
    tok = fxgraph.collect(rows)
    t = time.time()
    try:
        renderer.render_edl(e, index, src, out, work, preview,
                            asset_locals=dict(asset_locals))
    finally:
        fxgraph.stop_collecting(tok)
    return {"wall_s": round(time.time() - t, 2),
            "report": fxgraph.merge_report(rows), "edl": e}


def frames_at(path, idx):
    import qa_metrics
    got, fr = qa_metrics.read_frames(path, idx=sorted(set(idx)))
    return {k: np.asarray(f) for k, f in zip(got, fr)}


def mean_de(a_frames, b_frames):
    import lutlib
    a = np.concatenate([np.asarray(f, float).reshape(-1, 3)[::7]
                        for f in a_frames]) / 255.0
    b = np.concatenate([np.asarray(f, float).reshape(-1, 3)[::7]
                        for f in b_frames]) / 255.0
    la, lb = lutlib.rgb_to_lab(a), lutlib.rgb_to_lab(b)
    return float(lutlib.delta_e2000(la.mean(axis=0), lb.mean(axis=0)))


def _thumb(img, h=360):
    from PIL import Image
    im = Image.fromarray(np.asarray(img, np.uint8))
    return im.resize((max(1, int(im.width * h / im.height)), h))


def board(path, rows, title):
    """rows: [(label, [(caption, PIL image)])]."""
    from PIL import Image, ImageDraw
    pad, lab = 10, 36
    h = max((im.height for _l, tiles in rows for _c, im in tiles),
            default=360)
    w = max((sum(im.width + pad for _c, im in tiles) + pad
             for _l, tiles in rows), default=400)
    sheet = Image.new("RGB", (max(w, 900), 50 + len(rows) * (h + lab + 30)),
                      (16, 16, 18))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), title, fill=(240, 240, 240))
    y = 50
    for label, tiles in rows:
        d.text((pad, y), label[:150], fill=(233, 180, 76))
        x = pad
        for cap, im in tiles:
            sheet.paste(im, (x, y + 18))
            d.text((x, y + 22 + im.height), cap[:48], fill=(220, 220, 220))
            x += im.width + pad
        y += h + lab + 30
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=88)
    return path


def verdict(case, metrics, extra=None):
    od = os.path.join(out_dir(), case)
    os.makedirs(od, exist_ok=True)
    v = {"case": case, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "metrics": metrics, **(extra or {})}
    v["pass"] = all(m.get("pass") is True for m in metrics.values())
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, default=str)
    print(json.dumps({k: v[k] for k in ("case", "metrics", "pass")},
                     indent=1, default=str))
    return v


def _m(value, op, bar, detail=None):
    ok = None
    if value is not None:
        ok = value <= bar if op == "<=" else value >= bar
    return {"value": None if value is None else round(float(value), 4),
            "bar": f"{op}{bar}", "pass": ok, "detail": detail or {}}


# ── AT-4C-1 ──────────────────────────────────────────────────────────────

MOMENTS_1 = [
    {"id": "kids", "word": "kids", "query": "children running in a park",
     "query_variants": ["kids playing outdoors"],
     "purpose": "his four young kids", "duration_s": 2.2},
    {"id": "horizon", "word": "horizons", "query": "sunrise over the ocean "
     "horizon", "query_variants": ["sunrise horizon"],
     "purpose": "time horizons on the future", "duration_s": 2.4},
    {"id": "tech", "word": "accelerating", "query": "circuit board macro",
     "query_variants": ["data center server racks"],
     "purpose": "an accelerating technological world", "duration_s": 2.4},
    {"id": "world", "word": "2030", "query": "Earth from the International "
     "Space Station", "query_variants": ["planet Earth from orbit"],
     "purpose": "how the world gets to 2030", "duration_s": 2.4},
    {"id": "adults", "word": "adults", "query": "commuters walking in a "
     "city", "query_variants": ["office workers crossing a street"],
     "purpose": "a world of only adults", "duration_s": 2.2},
    {"id": "calendar", "word": "year", "query": "wall calendar",
     "query_variants": ["calendar pages"],
     "purpose": "thinking year to year", "duration_s": 2.0},
    {"id": "daughter", "word": "daughter", "query": "little girl portrait",
     "query_variants": ["young girl smiling"],
     "purpose": "my oldest daughter", "duration_s": 2.2},
    {"id": "future", "word": "century", "query": "futuristic city skyline "
     "at night", "query_variants": ["city lights at night aerial"],
     "purpose": "what the 22nd century will be like", "duration_s": 2.4},
]


def run_director(case, vfl_id, name, keep, moments, words_path=None):
    import storage  # noqa: F401
    from tools_ext import broll_tools
    od = os.path.join(SCRATCH, case)
    os.makedirs(od, exist_ok=True)
    w, info, index, edl = _project(vfl_id, name, keep, words_path)
    store = Store(os.path.join(od, "store"))
    store.install()
    ctx = Ctx(od, edl, index, w["path"], float(info["duration"]))
    ctx._broll_debug = []
    t = time.time()
    reply = broll_tools.source_broll(ctx, moments=moments)
    wall = round(time.time() - t, 1)
    with open(os.path.join(od, "reply.txt"), "w") as f:
        f.write(reply)
    with open(os.path.join(od, "judged.json"), "w") as f:
        json.dump(ctx._broll_debug, f, indent=1, default=str)
    print(reply)
    return w, info, index, ctx, store, reply, wall


def _windows(edl):
    return [it for it in edl.get("fx") or [] if it.get("type") == "broll"]


def at_4c_1(judged=None):
    case = "AT-4C-1"
    keep = (6.3, 71.3)
    words = os.path.join(SCRATCH, "thiel_3334_words.json")
    w, info, index, ctx, store, reply, wall = run_director(
        case, "V01", "thiel_3334", keep, MOMENTS_1, words)
    edl = ctx._edl
    items = _windows(edl)
    out = os.path.join(SCRATCH, case, "final.mp4")
    locs = dict(store.map)
    r = render(edl, w["path"], index, out, locs)
    import qa_metrics
    pi = qa_metrics.probe(out)
    fps = pi["fps"]
    import broll_score
    rows_blind, des, wm_hits, lic_bad = [], {}, [], []
    want = []
    for it in items:
        f0, f1 = int(round(it["start"] * fps)), int(round(it["end"] * fps))
        want += [f0 + 2, (f0 + f1) // 2, f1 - 3, max(0, f0 - 8),
                 min(int(pi["duration"] * fps) - 1, f1 + 8)]
    fr = frames_at(out, want)
    for it in items:
        f0, f1 = int(round(it["start"] * fps)), int(round(it["end"] * fps))
        inside = [fr[k] for k in (f0 + 2, (f0 + f1) // 2, f1 - 3)
                  if k in fr]
        hero = [fr[k] for k in (max(0, f0 - 8), f1 + 8) if k in fr]
        if inside and hero:
            des[it["id"]] = round(mean_de(inside, hero), 2)
        sc, box, why = broll_score.watermark(inside, still=False)
        if sc >= 0.35:
            wm_hits.append({"id": it["id"], "box": box, "why": why})
        c = it.get("credit") or {}
        if not (c.get("license") and c.get("provider") and c.get("page_url")
                and (c.get("author") or not c.get("attribution_required"))):
            lic_bad.append(it["id"])
        words_under = " ".join(
            x["w"] for x in index["words"]
            if it["start"] - 0.6 <= x["t0"] - keep[0] <= it["end"] + 0.3)
        mid = fr.get((f0 + f1) // 2)
        if mid is not None:
            rows_blind.append((f"{it['id']}: \"{words_under}\"",
                               [("rendered cutaway", _thumb(mid, 400))]))
    bpath = os.path.join(BOARDS, case, "blind_board.jpg")
    board(bpath, rows_blind, "AT-4C-1 blind on-topic board: the rendered "
          "cutaway and the words under it (no source, score or query)")
    # the director's own board (hero | fetched | matched)
    dboard = None
    for _lab, p in ctx.pending_images:
        dboard = os.path.join(BOARDS, case, "director_board.jpg")
        shutil.copy(p, dboard)
    on_topic = None
    if judged and os.path.exists(judged):
        j = json.load(open(judged))
        votes = [bool(j.get(it["id"])) for it in items if it["id"] in j]
        on_topic = sum(votes) / float(len(votes)) if votes else None
    worst_de = max(des.values()) if des else None
    return verdict(case, {
        "placed": _m(len(items), ">=", 6, {"moments": len(MOMENTS_1)}),
        "on_topic_share": _m(on_topic, ">=", 0.8,
                             {"judged": judged, "board": bpath}),
        "worst_dE_to_hero": _m(worst_de, "<=", 5.0, des),
        "licence_incomplete": _m(len(lic_bad), "<=", 0, {"ids": lic_bad}),
        "watermarks_in_render": _m(len(wm_hits), "<=", 0,
                                   {"hits": wm_hits}),
    }, {"wall_s": {"director": wall, "render": r["wall_s"]},
        "report": [x for x in r["report"]
                   if str(x.get("id", "")).startswith("br")],
        "ledger": [it.get("credit") for it in items],
        "items": [{k: it.get(k) for k in ("id", "start", "end", "media",
                                          "fit", "device", "match", "grain",
                                          "soften", "move", "focus_x")}
                  for it in items],
        "boards": {"blind": bpath, "director": dboard},
        "render": out})


# ── AT-4C-2: public-domain archival ──────────────────────────────────────

MOMENTS_2 = [
    {"id": "moon", "at": 2.0, "query": "Apollo moon landing",
     "purpose": "the moon landing", "archival": True, "kind": "video",
     "duration_s": 2.6},
    {"id": "launch", "at": 8.0, "query": "Saturn V launch",
     "purpose": "a 1960s rocket launch", "archival": True, "kind": "video",
     "duration_s": 2.6},
    {"id": "factory", "at": 14.0, "query": "assembly line factory 1950s",
     "query_variants": ["automobile factory"],
     "purpose": "industry of the last century", "archival": True,
     "kind": "video", "duration_s": 2.6},
    {"id": "computer", "at": 20.0, "query": "early computer",
     "query_variants": ["mainframe computer"],
     "purpose": "the first computers", "archival": True, "duration_s": 2.6},
]


def at_4c_2():
    case = "AT-4C-2"
    keep = (6.3, 36.3)
    w, info, index, ctx, store, reply, wall = run_director(
        case, "V01", "thiel_3334", keep, MOMENTS_2)
    items = _windows(ctx._edl)
    pd_ok, rows = [], []
    for it in items:
        c = it.get("credit") or {}
        good = bool(c.get("license")) and (
            "public domain" in c["license"].lower()
            or c["license"].upper().startswith(("CC0", "CC BY")))
        pd_ok.append({"id": it["id"], "provider": c.get("provider"),
                      "license": c.get("license"), "line": c.get("line"),
                      "page_url": c.get("page_url"), "ok": good and bool(
                          c.get("page_url"))})
    out = os.path.join(SCRATCH, case, "final.mp4")
    r = render(ctx._edl, w["path"], index, out, dict(store.map))
    import qa_metrics
    fps = qa_metrics.probe(out)["fps"]
    fr = frames_at(out, [int(round((it["start"] + it["end"]) / 2 * fps))
                         for it in items])
    for it in items:
        k = int(round((it["start"] + it["end"]) / 2 * fps))
        if k in fr:
            c = it.get("credit") or {}
            rows.append((f"{it['id']}: {c.get('line')}",
                         [(f"{c.get('provider')} {it.get('fit')}",
                           _thumb(fr[k], 400))]))
    bpath = board(os.path.join(BOARDS, case, "board.jpg"), rows,
                  "AT-4C-2 public-domain archival cutaways (rendered)")
    archival = [x for x in pd_ok if x["provider"] in ("nasa", "archive",
                                                      "wikimedia")]
    return verdict(case, {
        "archival_placed": _m(len(archival), ">=", 2),
        "pd_lines_missing": _m(sum(not x["ok"] for x in pd_ok), "<=", 0,
                               {"ledger": pd_ok}),
    }, {"board": bpath, "wall_s": {"director": wall, "render": r["wall_s"]},
        "report": [x for x in r["report"]
                   if str(x.get("id", "")).startswith("br")]})


# ── AT-4C-3: the adversarial set ─────────────────────────────────────────

LOGOS = [
    # (kind, drawtext/drawbox spec, where)
    ("handle", "@stockclips", "x=w-tw-28:y=h-th-24", 0.85, 30),
    ("brand", "SHUTTERSTOCK", "x=(w-tw)/2:y=(h-th)/2", 0.35, 90),
    ("corner", "© NEWS 24", "x=24:y=24", 0.9, 34),
    ("sample", "SAMPLE", "x=(w-tw)/2:y=h*0.62", 0.4, 110),
    ("url", "www.getclips.example", "x=w-tw-20:y=20", 0.75, 26),
    ("bug", "LIVE HD", "x=w-tw-30:y=30", 1.0, 40),
    ("faint", "iStock", "x=(w-tw)/2:y=(h-th)/2", 0.25, 140),
    ("lower", "preview only", "x=30:y=h-th-30", 0.8, 32),
    ("box", None, "x=w-180:y=h-90", 0.9, 0),
    ("centre", "watermark", "x=(w-tw)/2:y=(h-th)/3", 0.5, 70),
]


def _font():
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(p):
            return p
    return None


def _make_adversarial(od):
    """(path, label, expect_reject) for 10 watermarked, 10 starved and 10
    clean clips cut from the B-roll pool fixtures (V13; internal only) —
    the material source_broll actually judges."""
    from . import vfl
    import qa_metrics
    srcs = []
    for name in ("starship_launch_4k60", "dragon_stage_walk",
                 "colossus_aisle", "nasa_prominence_60p", "dragon_unveil",
                 "starship_descent_4k60", "nasa_earth_orbit"):
        try:
            p = vfl.window("V13", name)["path"]
            if os.path.exists(p):
                srcs.append(p)
        except Exception:  # noqa: BLE001
            continue
    durs = {p: float(qa_metrics.probe(p)["duration"]) for p in srcs}

    def at(src, t):
        return round(1.0 + (t % max(1.0, durs[src] - 7.0)), 2)
    font = _font()
    out = []
    os.makedirs(od, exist_ok=True)
    enc = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an"]
    for i, (kind, text, pos, alpha, size) in enumerate(LOGOS):
        src = srcs[i % len(srcs)]
        ss = at(src, 3.0 + 2.0 * i)
        p = os.path.join(od, f"wm_{i:02d}_{kind}.mp4")
        if text is None:
            vf = ("scale=1920:1080,drawbox=x=iw-200:y=ih-110:w=150:h=70:"
                  f"color=white@{alpha}:t=fill,drawbox=x=iw-190:y=ih-100:"
                  "w=130:h=50:color=red@0.9:t=fill")
        else:
            esc = text.replace(":", "\\:").replace("'", "")
            vf = (f"scale=1920:1080,drawtext=fontfile='{font}':text='{esc}'"
                  f":fontsize={size}:fontcolor=white@{alpha}:{pos}:"
                  f"borderw=2:bordercolor=black@{alpha * 0.6:.2f}")
        if not os.path.exists(p):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}",
                            "-i", src, "-t", "5", "-vf", vf] + enc + [p],
                           check=True)
        out.append((p, f"watermark:{kind}", True))
    for i in range(10):
        src = srcs[(i + 2) % len(srcs)]
        ss = at(src, 4.0 + 1.7 * i)
        kbps = (250, 350, 450, 300, 600, 200, 500, 400, 700, 280)[i]
        p = os.path.join(od, f"lo_{i:02d}_{kbps}k.mp4")
        if not os.path.exists(p):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}",
                            "-i", src, "-t", "5", "-an", "-vf",
                            "scale=1920:1080", "-c:v", "libx264", "-b:v",
                            f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize",
                            f"{kbps}k", "-pix_fmt", "yuv420p", p],
                           check=True)
        out.append((p, f"starved:{kbps}kbps", True))
    for i in range(10):
        src = srcs[(i + 1) % len(srcs)]
        ss = at(src, 2.0 + 2.3 * i)
        p = os.path.join(od, f"ok_{i:02d}.mp4")
        if not os.path.exists(p):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}",
                            "-i", src, "-t", "5", "-vf", "scale=1920:1080"]
                           + enc + [p], check=True)
        out.append((p, "clean", False))
    return out


def at_4c_3():
    case = "AT-4C-3"
    import broll_score
    import broll_source
    import media
    import motion_judge
    od = os.path.join(SCRATCH, case)
    rows, results = [], []
    for path, label, expect in _make_adversarial(od):
        info = media.probe(path)
        fr = broll_source.frames_of(path, "video", n=4)
        try:
            prof = motion_judge.analyze_video(path,
                                              duration_s=info["duration"])
        except Exception:  # noqa: BLE001
            prof = None
        meta = {"kind": "video", "width": info["width"],
                "height": info["height"], "fps": info["fps"],
                "duration_s": info["duration"],
                "bytes": os.path.getsize(path), "motion_profile": prof}
        # judged at the executor's review size (768 px), like production
        small = [broll_score._small(f, 768) for f in fr]
        res = broll_score.score(small, meta, hero=None, W=1080, H=1920,
                                fit="cover")
        # what this AT judges: a mark or a starved encode (a clip refused
        # for its crop is refused for another reason)
        rej = any(r.startswith(("watermark", "low-bitrate", "visible "
                                "compression")) for r in res["reject"])
        results.append({"clip": os.path.basename(path), "label": label,
                        "expect_reject": expect, "rejected": rej,
                        "why": res["reject"],
                        "watermark": res["measures"].get("watermark"),
                        "bpp": res["measures"].get("bpp")})
        if expect != rej or label.startswith("watermark"):
            rows.append((f"{os.path.basename(path)} — {label} — "
                         f"{'REJECTED' if rej else 'kept'}: "
                         f"{'; '.join(res['reject'])[:90]}",
                         [("frame", _thumb(fr[len(fr) // 2], 240))]))
    bad = [r for r in results if r["expect_reject"] and not r["rejected"]]
    fp = [r for r in results if not r["expect_reject"] and r["rejected"]]
    bpath = board(os.path.join(BOARDS, case, "board.jpg"), rows,
                  "AT-4C-3 adversarial set: watermarks and starved encodes")
    return verdict(case, {
        "adversarial_kept": _m(len(bad), "<=", 0,
                               {"n": sum(r["expect_reject"]
                                         for r in results),
                                "missed": bad}),
        "clean_false_rejects": _m(len(fp), "<=", 1,
                                  {"n": sum(not r["expect_reject"]
                                            for r in results),
                                   "fp": fp}),
    }, {"results": results, "board": bpath})


# ── AT-4C-4: honest-off ──────────────────────────────────────────────────

def at_4c_4():
    case = "AT-4C-4"
    import config
    import stock
    from tools_ext import broll_tools
    keys = (stock.PEXELS_KEY, stock.PIXABAY_KEY)
    prev = getattr(config, "BROLL_PROVIDERS", None)
    stock.PEXELS_KEY = stock.PIXABAY_KEY = ""
    config.BROLL_PROVIDERS = "pexels,pixabay"   # the keyless lanes off too
    od = os.path.join(SCRATCH, case)
    os.makedirs(od, exist_ok=True)
    try:
        w, info, index, edl = _project("V01", "thiel_3334", (6.3, 71.3),
                                       os.path.join(SCRATCH,
                                                    "thiel_3334_words.json"))
        ctx = Ctx(od, edl, index, w["path"], float(info["duration"]))
        reply = broll_tools.source_broll(ctx, moments=MOMENTS_1[:3])
    finally:
        stock.PEXELS_KEY, stock.PIXABAY_KEY = keys
        config.BROLL_PROVIDERS = prev
    with open(os.path.join(od, "reply.txt"), "w") as f:
        f.write(reply)
    print(reply)
    shoot = [ln for ln in reply.splitlines() if ln.strip().startswith(
        ("kids:", "horizon:", "tech:"))]
    return verdict(case, {
        "honest_off": _m(float(reply.startswith("B-ROLL OFF")), ">=", 1.0),
        "shoot_list_lines": _m(len(shoot), ">=", 3, {"lines": shoot}),
        "writes": _m(len(ctx.writes), "<=", 0),
    }, {"reply": reply})


CASES = {"AT-4C-1": at_4c_1, "AT-4C-2": at_4c_2, "AT-4C-3": at_4c_3,
         "AT-4C-4": at_4c_4}


def main(argv):
    judged = None
    if "--judge" in argv:
        judged = argv[argv.index("--judge") + 1]
        argv = [a for a in argv if a not in ("--judge", judged)]
    for case in argv or list(CASES):
        if case == "AT-4C-1":
            CASES[case](judged)
        else:
            CASES[case]()


if __name__ == "__main__":
    main(sys.argv[1:])
