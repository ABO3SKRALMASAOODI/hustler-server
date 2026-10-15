"""Wave 1 showcase pieces and boards (ROADMAP Wave 1 showcase W1-S2 "Lab
scan", W1-S3 "Silhouette sizzle"). LOCAL and INTERNAL: renders footage
library fixtures through the merged Wave 1 engine (the real program matte
measured locally, every device written as apply_edit_batch operations, the
stale-client path), concatenates the pieces, and builds boards against the
named reference moments. Nothing here touches product storage; the
references and boards never leave this machine.

    cd worker && PYTHONPATH=.. python tools/w1_boards.py --out <dir> \
        [--piece S2|S3 ...] [--boards-only]

Environment (set by main() when absent): VALMERA_SIDECAR_DIR (a LOCAL
sidecar store: never the product bucket), MATTE_RVM_MODEL, YUNET_MODEL.
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VALMERA = os.path.expanduser("~/Documents/Valmera/.valmera")
FIX = os.environ.get("VALMERA_FIXTURES_DIR") or os.path.join(VALMERA,
                                                             "fixtures")
REFS = os.environ.get("VALMERA_REFERENCES_DIR") or os.path.expanduser(
    "~/.openclaw/workspace/references")
W, H = 1080, 1920


def fx(*parts):
    return os.path.join(FIX, *parts)


def ref(prefix):
    for name in sorted(os.listdir(REFS)):
        if name.startswith(prefix) and name.endswith(".mp4"):
            return os.path.join(REFS, name)
    raise FileNotFoundError(prefix)


# ── a clip: its sidecars and its index ────────────────────────────────────

def setup(path, ranges, faces=False):
    """{path, info, index, sha} with matte.person (and faces.yunet) measured
    on the local store over ``ranges`` (source seconds)."""
    import matte_track
    import media
    import scenes
    import sidecars
    info = media.probe(path)
    sha = media.sha256_file(path)
    shots = [s.start if hasattr(s, "start") else s.get("start")
             for s in scenes.detect_shots(path, float(info["duration"]))]
    shots = [float(s) for s in shots if s and float(s) > 0.0]
    src = sidecars.source_from(sha, path, info, 0, shots)
    t0 = time.time()
    hs = [sidecars.ensure("matte.person", src, ranges,
                          params=matte_track.sidecar_params())]
    if faces:
        hs.append(sidecars.ensure("faces.yunet", src, ranges))
    sidecars._launcher().join()
    states = [sidecars.status(h) for h in hs]
    print(f"[boards] {os.path.basename(path)}: sidecars {states} in "
          f"{time.time() - t0:.0f}s", flush=True)
    index = {"words": [], "shots": [{"start": s} for s in shots],
             "video": {"width": info["width"], "height": info["height"],
                       "fps": info["fps"], "duration": info["duration"],
                       "sha256": sha}}
    return {"path": path, "info": info, "index": index, "sha": sha,
            "shots": shots}


def render(S, edl, ops, out, *, assets=None, preview=False, want_wm=False,
           outro=False):
    """Apply ``ops`` (apply_edit_batch, as a stale MCP client sends them) to
    ``edl`` and render it; returns (path, wall s, device report rows)."""
    import edit_batch
    import fxgraph
    import motion_layer
    import renderer
    from schemas import validate_edl
    dur = float(S["info"]["duration"])
    base = validate_edl(edl, dur).model_dump()
    allowed = set((assets or {}).keys())
    e = edit_batch.apply_batch(base, ops, dur, allowed) if ops else base
    e = validate_edl(e, dur).model_dump()
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    rows, warnings = [], []
    tok = fxgraph.collect(rows)
    wtok = motion_layer.collect_warnings(warnings)
    t0 = time.time()
    try:
        renderer.render_edl(e, S["index"], S["path"], out, work, preview,
                            want_wm=want_wm, asset_locals=dict(assets or {}),
                            suppress_outro=not outro)
    finally:
        fxgraph.stop_collecting(tok)
        motion_layer.stop_collecting(wtok)
    wall = round(time.time() - t0, 1)
    with open(out + ".edl.json", "w") as f:
        json.dump(e, f, indent=1)
    rep = fxgraph.merge_report(rows)
    with open(out + ".report.json", "w") as f:
        json.dump({"rows": rep, "warnings": warnings, "wall_s": wall}, f,
                  indent=1)
    return out, wall, rep


def _base(keep, focus_x=0.5, inserts=None):
    edl = {"keep": [list(k) for k in keep],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": focus_x,
                     "focus_y": 0.5},
           "captions": {"enabled": False}}
    if inserts:
        edl["inserts"] = inserts
    return edl


def up(item):
    return {"action": "upsert", "layer": "fx", "id": item["id"],
            "value": item}


def concat(paths, out, fps=30):
    """The pieces joined into one sizzle (re-encoded, same geometry), the
    end card of each piece left out (each piece renders without one)."""
    lst = out + ".txt"
    with open(lst, "w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe",
                    "0", "-i", lst, "-vf", f"fps={fps},format=yuv420p",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-c:a", "aac", out], check=True)
    return out


# ── W1-S3 "Silhouette sizzle" ─────────────────────────────────────────────
# V12 walk-on (DevDay, 29.97): the walk on stage 7.0-9.9 and the MCU on dark
# blue 15.0-18.5; V12 LED wall (Altman before 'intelligence') 11.5-14.5;
# V11 stage (Elon MCU on black, 25p) 2.0-5.0. Devices: white silhouette
# flashes, red ghost clones, a column wipe at the cut, the subject held
# while the world swaps through three V13 plates, a poster field.

PLATES = {"media/w1/starship.mp4": fx("V13", "starship_launch_4k60.mp4"),
          "media/w1/colossus.mp4": fx("V13", "colossus_aisle.mp4"),
          "media/w1/dragon.mp4": fx("V13", "dragon_unveil.mp4")}


def s3_walkon(out_dir):
    path = fx("V12", "walkon.mp4")
    S = setup(path, [(6.5, 10.4), (14.5, 19.0)])
    keep = [(7.0, 9.9), (15.0, 18.6)]
    # program: [0, 2.9) the walk, [2.9, 6.5) the MCU
    ops = [
        # twin / ghost clones as he walks (40@18.3-19.3 red ghosts; 22@6.0)
        up({"id": "s3_clones", "type": "subject", "stage": "world",
            "start": 0.4, "end": 1.9, "kind": "echo",
            "echo": {"mode": "clone", "offsets_f": [4, 8], "opacity": 0.6,
                     "decay": 0.25, "tint": "#e8202a"},
            "device": "subject_echo"}),
        # white silhouette flash on a stride (22@8.43)
        up({"id": "s3_flash1", "type": "subject", "stage": "world",
            "start": 2.2, "end": 2.27, "kind": "fill", "colour": "white",
            "decay_f": 3, "device": "subject_fill_flash"}),
        # column wipe at the cut (22@4.9-5.2)
        up({"id": "s3_cols", "type": "junction", "stage": "junction",
            "at": 2.9, "style": "columns", "count": 6, "direction": "left",
            "duration_s": 0.4, "device": "junction_columns"}),
        # the subject held while the world swaps (20@7.40, 22@8.1-8.43)
        up({"id": "s3_hold", "type": "background", "stage": "world",
            "start": 3.8, "end": 4.5, "kind": "strobe", "hold_f": 3,
            "hold_subject": True, "hold_t": 3.8,
            "sources": [{"kind": "asset", "asset_key": k, "loop": True}
                        for k in PLATES],
            "device": "background_subject_hold"}),
        # white silhouette flash on the MCU (40@4.67, 20@9.10)
        up({"id": "s3_flash2", "type": "subject", "stage": "world",
            "start": 5.4, "end": 5.47, "kind": "fill", "colour": "white",
            "decay_f": 3, "device": "subject_fill_flash"}),
    ]
    return render(S, _base(keep, 0.5), ops,
                  os.path.join(out_dir, "s3_a_walkon.mp4"), assets=PLATES)


def s3_ledwall(out_dir):
    path = fx("V12", "led_wall.mp4")
    S = setup(path, [(11.0, 15.0)])
    keep = [(11.5, 14.5)]
    ops = [
        # poster: the speaker kept, on the accent field (38@0.3-1.0)
        up({"id": "s3_poster", "type": "subject", "stage": "world",
            "start": 0.6, "end": 1.8, "kind": "poster", "field": "#e8202a",
            "keep_footage": True, "device": "subject_poster"}),
        # then the silhouette: black on cream for a beat (23@1.3-4.7)
        up({"id": "s3_sil", "type": "subject", "stage": "world",
            "start": 1.8, "end": 2.6, "kind": "poster", "field": "#f1ede4",
            "keep_footage": False, "colour": "black",
            "device": "subject_poster"}),
    ]
    return render(S, _base(keep, 0.55), ops,
                  os.path.join(out_dir, "s3_b_ledwall.mp4"))


def s3_stage(out_dir):
    path = fx("V11", "stage_two_figures.mp4")
    S = setup(path, [(1.5, 5.5)])
    keep = [(2.0, 4.6)]
    ops = [
        # a glow around the figure on black (33, 36@4-6)
        up({"id": "s3_glow", "type": "subject", "stage": "world",
            "start": 0.3, "end": 1.4, "kind": "glow", "colour": "#ffffff",
            "radius": 0.03, "intensity": 0.9, "device": "subject_glow"}),
        # and the white silhouette hit to close (22@12.33)
        up({"id": "s3_flash3", "type": "subject", "stage": "world",
            "start": 1.9, "end": 1.98, "kind": "fill", "colour": "white",
            "decay_f": 4, "device": "subject_fill_flash"}),
    ]
    return render(S, _base(keep, 0.62), ops,
                  os.path.join(out_dir, "s3_c_stage.mp4"))


# ── W1-S2 "Lab scan" ──────────────────────────────────────────────────────
# V13 Optimus carry (4K24) as the main footage, the Starship launch plume
# inserted; a registered variant strobe of the robot (opted in: a lab scan
# IS a strobe, ref 01 — the export is flagged contains_flashing), a glowing
# silhouette, a pixel-stretch junction into the launch, a thermal flip on
# the plume, a hold after the strobe.

def s2_lab(out_dir):
    import variants
    path = fx("V13", "optimus_carry.mp4")
    S = setup(path, [(1.5, 7.5)])
    work = os.path.join(out_dir, "s2_variants")
    os.makedirs(work, exist_ok=True)
    frame = os.path.join(work, "frame.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3.0", "-i", path,
                    "-frames:v", "1", frame], check=True)
    kinds = ["photo", "negative", "line", "blueprint", "thermal"]
    paths, _stats = variants.render_variants(frame, kinds, work, seed=7)
    assets = {f"media/w1/variant_{k}.png": paths[k] for k in kinds}
    assets["media/w1/starship.mp4"] = fx("V13", "starship_launch_4k60.mp4")
    keep = [(2.0, 7.0)]
    inserts = [{"id": "ins_launch", "asset_key": "media/w1/starship.mp4",
                "kind": "video", "at_output_s": 5.0, "duration_s": 3.0,
                "source_start_s": 7.0}]
    vkeys = [f"media/w1/variant_{k}.png" for k in kinds]
    ops = [
        # the registered 2-frame strobe (01@0.04-0.96)
        up({"id": "s2_strobe", "type": "strobe", "stage": "pre_captions",
            "start": 0.6, "end": 1.44,
            "frames": [{"source": {"kind": "asset", "asset_key": k},
                        "hold_f": 2} for k in vkeys],
            "pattern": "sequence", "fit": "cover",
            "device": "strobe_frames"}),
        # a glowing silhouette on the hold after it (01@1.708)
        up({"id": "s2_glow", "type": "subject", "stage": "world",
            "start": 1.8, "end": 3.4, "kind": "glow", "colour": "#7fd8ff",
            "radius": 0.035, "intensity": 0.95, "device": "subject_glow"}),
        # pixel stretch into the launch (36@0)
        up({"id": "s2_stretch", "type": "junction", "stage": "junction",
            "at": 5.0, "style": "pixel_stretch", "duration_s": 0.5,
            "device": "junction_pixel_stretch"}),
        # the plume flips to thermal for a breath (17@1.83, 33@6.0)
        up({"id": "s2_thermal", "type": "finish", "stage": "world",
            "start": 6.6, "end": 7.3, "kind": "thermal",
            "device": "finish_thermal"}),
    ]
    edl = _base(keep, 0.32, inserts)
    return render(S, edl, ops, os.path.join(out_dir, "s2_lab.mp4"),
                  assets=assets)


# ── boards ────────────────────────────────────────────────────────────────

def grab(path, t, out, w=360):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i",
                    path, "-frames:v", "1", "-vf", f"scale={w}:-2", out],
                   check=True)
    return out


def board(rows, out, title):
    """A board: each row = (label, ours_png, ref_png, ref_label)."""
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for fp in ("/System/Library/Fonts/Supplemental/Arial.ttf",
               os.path.join(HERE, "fonts", "InterDisplay-Bold.ttf")):
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, 22)
            break
    cells = []
    for label, ours, refp, rlabel in rows:
        a = Image.open(ours).convert("RGB")
        b = Image.open(refp).convert("RGB") if refp else None
        h = 640
        a = a.resize((int(a.width * h / a.height), h))
        if b is not None:
            b = b.resize((int(b.width * h / b.height), h))
        cells.append((label, a, b, rlabel))
    width = max(c[1].width + (c[2].width if c[2] else 0) + 30
                for c in cells)
    height = sum(700 for _ in cells) + 60
    im = Image.new("RGB", (width, height), (16, 16, 16))
    d = ImageDraw.Draw(im)
    d.text((10, 15), title, fill=(255, 220, 120), font=font)
    y = 60
    for label, a, b, rlabel in cells:
        im.paste(a, (10, y + 30))
        d.text((10, y + 4), "OURS  " + label, fill=(255, 255, 255),
               font=font)
        if b is not None:
            im.paste(b, (20 + a.width, y + 30))
            d.text((20 + a.width, y + 4), "REF  " + rlabel,
                   fill=(160, 200, 255), font=font)
        y += 700
    im.save(out, quality=90)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--piece", action="append")
    a = ap.parse_args(argv)
    os.environ.setdefault("VALMERA_SIDECAR_DIR",
                          os.path.join(VALMERA, "engines", "w1", "sidecars"))
    os.environ.setdefault("MATTE_RVM_MODEL", os.path.join(
        VALMERA, "models", "rvm_mobilenetv3_fp32.onnx"))
    os.environ.setdefault("YUNET_MODEL", os.path.join(
        VALMERA, "models", "face_detection_yunet_2023mar.onnx"))
    os.makedirs(a.out, exist_ok=True)
    pieces = a.piece or ["S2", "S3"]
    summary = {}
    if "S3" in pieces:
        r = [s3_walkon(a.out), s3_ledwall(a.out), s3_stage(a.out)]
        summary["S3"] = {os.path.basename(p): {"wall_s": w, "rows": rows}
                         for p, w, rows in r}
        concat([p for p, _w, _r in r], os.path.join(a.out, "W1-S3.mp4"))
    if "S2" in pieces:
        p, w, rows = s2_lab(a.out)
        summary["S2"] = {os.path.basename(p): {"wall_s": w, "rows": rows}}
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1)[:4000])


if __name__ == "__main__":
    main()
