#!/usr/bin/env python3
"""Supply for a marketing film: real Valmera output, recorded with its receipts.

Marketing shows only what the product did. This reads a finished podcast-shorts run (the product's
own delivery: every final is an export of a Valmera project) and packs what a film needs:

    python supply.py build --run <podcast-shorts run folder> --out <run>/supply --hero s042 \
        [--salvo s032,s028,s061,s068] [--tile-t 1.2]

  hero      one delivered short, read in place (never re-encoded), with the raw source it was cut
            from registered to it: the rectangle of the frame that is the source picture, the crop
            of the source that fills it, and the source frame on screen (register.py)
  raw.mp4   the same seconds of the raw source, cut frame-exact, no sound
  salvo     other delivered shorts, read in place
  tiles/    one frame of every delivered short, in short-id order: the whole delivery as a wall
  receipts  for every number the film may show: the file it was read from and its sha256

Nothing here uploads, publishes or touches a project. Everything written goes under --out.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as F  # noqa: E402
import register as RG  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera"
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        n = 0
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n >= limit:
                break
    return h.hexdigest()


def run_record(run_dir):
    """A finished podcast-shorts run as facts: the source, and every delivered short with its file."""
    d = json.load(open(os.path.join(run_dir, "run.json")))
    folder = d.get("delivered_to")
    files = sorted(f for f in os.listdir(folder) if f.endswith(".mp4")) if folder and os.path.isdir(folder) else []
    by = {}
    for f in files:
        parts = f.split("__")
        if len(parts) >= 3:
            by[parts[1]] = f
    acq = {}
    for name in ("acquisition.json", "plan.json", "provenance.json"):
        p = os.path.join(run_dir, "source", name)
        if os.path.exists(p):
            try:
                acq.update(json.load(open(p)))
            except ValueError:
                pass
    shorts = []
    for sid in sorted(by):
        s = (d.get("shorts") or {}).get(sid) or {}
        c = (s.get("candidates") or [{}])[-1]
        hb = c.get("handback") or {}
        shorts.append(dict(sid=sid, file=os.path.join(folder, by[sid]), structure=by[sid].split("__")[0], look=s.get("look") or hb.get("look"),
                           title=s.get("title") or (s.get("brief") or {}).get("headline"), keeps=hb.get("kept_source_ranges_s") or [],
                           project_id=hb.get("project_id") or s.get("child_project_id"), edl_version=c.get("edl_version") or hb.get("edl_version"),
                           final_s=c.get("final_s"), rank=s.get("rank"), sfx=hb.get("sfx") or [], broll=hb.get("broll") or []))
    src = None
    for n in sorted(os.listdir(os.path.join(run_dir, "source"))):
        if n.endswith((".mp4", ".mkv", ".webm", ".mov")):
            src = os.path.join(run_dir, "source", n)
            break
    return dict(run_id=d.get("run_id"), run_json=os.path.join(run_dir, "run.json"), stage=d.get("stage"), version=d.get("version"),
                delivered_to=folder, delivered=len(by), declared=len(d.get("delivered_files") or {}), source=src,
                source_url=d.get("source") or acq.get("source_url"), source_s=acq.get("duration_s"), music=d.get("music"),
                started=d.get("created_at"), finished=d.get("updated_at"), shorts=shorts)


def clock(sec):
    sec = int(round(float(sec)))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def grid_for(n):
    """Columns and rows of 9:16 tiles that hold exactly n and sit nearest a square block; None if n has no such shape."""
    best = None
    for rows in range(1, n + 1):
        if n % rows:
            continue
        cols = n // rows
        aspect = (cols * 9) / (rows * 16)
        if 0.6 <= aspect <= 1.25 and (best is None or abs(aspect - 0.95) < abs(best[2] - 0.95)):
            best = (cols, rows, aspect)
    return best[:2] if best else None


def build(a):
    out = os.path.abspath(a.out)
    os.makedirs(os.path.join(out, "tiles"), exist_ok=True)
    rec = run_record(a.run)
    if rec["stage"] != "complete" or not rec["delivered"] or rec["delivered"] != rec["declared"]:
        raise SystemExit(f"run not usable as evidence: stage {rec['stage']}, {rec['delivered']} files of {rec['declared']} declared")
    by = {s["sid"]: s for s in rec["shorts"]}
    hero = by[a.hero]
    if not hero["keeps"]:
        raise SystemExit(f"{a.hero} has no kept source ranges on record: it cannot be registered to its source")
    k0, k1 = hero["keeps"][0]
    if k1 - k0 < 4.0:
        raise SystemExit(f"{a.hero} opens on a {k1 - k0:.1f} s keep: too short to wipe across")
    src_fps = F.probe(FP, rec["source"])["fps"]
    reg = RG.register(FF, hero["file"], rec["source"], a.reg_t, k0 + a.reg_t, raw_fps=src_fps)
    raw_t0 = k0 + reg["frame_offset"] / src_fps
    raw = os.path.join(out, "raw.mp4")
    subprocess.run([FF, "-v", "error", "-y", "-ss", f"{raw_t0:.4f}", "-i", rec["source"], "-t", f"{a.raw_s:.3f}", "-an", "-map", "0:v:0",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p", "-r", "30", raw], check=True)
    tiles = []
    for i, s in enumerate(rec["shorts"]):
        p = os.path.join(out, "tiles", f"{i:03d}.jpg")
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{a.tile_t:.2f}", "-i", s["file"], "-frames:v", "1",
                        "-vf", f"scale={a.tile_w}:{a.tile_w * 16 // 9}:flags=lanczos", "-q:v", "2", p], check=True)
        tiles.append(dict(sid=s["sid"], path=p, w=a.tile_w, h=a.tile_w * 16 // 9, look=s["look"]))
    salvo = []
    for spec in [x for x in (a.salvo or "").split(",") if x]:
        sid, _, t = spec.partition("@")
        s = by[sid]
        salvo.append(dict(sid=sid, path=s["file"], t=float(t or a.salvo_t), look=s["look"], probe=F.probe(FP, s["file"]),
                          project_id=s["project_id"], edl_version=s["edl_version"]))
    lib_dir = os.path.join(F.scene_worker(), "sound_library")
    sounds = {}
    for m in json.load(open(os.path.join(lib_dir, "manifest.json"))):
        if m["id"] in ("tick_1", "whoosh_soft_1", "riser_2", "click_1"):
            r = subprocess.run([FF, "-hide_banner", "-i", os.path.join(lib_dir, m["file"]), "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True)
            mx = next(float(ln.split("max_volume:")[1].split()[0]) for ln in r.stderr.splitlines() if "max_volume" in ln)
            sounds[m["id"]] = dict(file=m["file"], peak_s=float(m["peak_s"]), max_db=mx)
    grid = grid_for(len(tiles))
    sup = dict(
        sid=f"wf2-{rec['run_id']}", ffmpeg=FF, grid=list(grid) if grid else None, tiles=tiles, salvo=salvo, sounds=sounds,
        hero=dict(sid=hero["sid"], path=hero["file"], probe=F.probe(FP, hero["file"]), keep0=k0, keeps=hero["keeps"], reg=reg, look=hero["look"],
                  project_id=hero["project_id"], edl_version=hero["edl_version"], title=hero["title"],
                  raw=dict(path=raw, probe=F.probe(FP, raw), source_t0=round(raw_t0, 4))),
        facts=dict(delivered=rec["delivered"], source_s=rec["source_s"], source_clock=clock(rec["source_s"]) if rec["source_s"] else None,
                   source_url=rec["source_url"], music=rec["music"], run_started=rec["started"], run_finished=rec["finished"]),
        receipts=[
            dict(claim=f"{rec['delivered']} shorts delivered from one source", file=rec["run_json"], how="delivered_files count equals the .mp4 files in delivered_to",
                 sha256=sha256(rec["run_json"]), folder=rec["delivered_to"]),
            dict(claim="the source and its length", file=os.path.join(a.run, "source", "acquisition.json"), value=rec["source_s"], url=rec["source_url"]),
            dict(claim="the hero is a Valmera export, shown unaltered", file=hero["file"], sha256=sha256(hero["file"]), project_id=hero["project_id"],
                 edl_version=hero["edl_version"]),
            dict(claim="the raw side is the same seconds of the source, in the export's own framing", file=rec["source"], source_t0=round(raw_t0, 4),
                 registration=reg, sha256_first_64mb=sha256(rec["source"], 64 << 20)),
        ])
    with open(os.path.join(out, "pack.json"), "w") as f:
        json.dump(sup, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(pack=os.path.join(out, "pack.json"), delivered=rec["delivered"], grid=grid, reg=reg, salvo=[s["sid"] for s in salvo])))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--run", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--hero", required=True)
    b.add_argument("--salvo", default="")
    b.add_argument("--salvo-t", type=float, default=3.0)
    b.add_argument("--tile-t", type=float, default=1.2)
    b.add_argument("--tile-w", type=int, default=108)
    b.add_argument("--reg-t", type=float, default=1.6)
    b.add_argument("--raw-s", type=float, default=4.0)
    r = sub.add_parser("runs")
    r.add_argument("--root", default=f"{V}/.tmp/valmera-podcast-shorts")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a)
    else:
        import evidence
        print(json.dumps(evidence.ledger(a.root), indent=1))


if __name__ == "__main__":
    main()
