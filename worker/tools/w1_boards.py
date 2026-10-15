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
    cache = os.path.join(VALMERA, "engines", "w1", "boards_work",
                         f"shots_{sha[:16]}.json")
    if os.path.exists(cache):
        shots = json.load(open(cache))
    else:                       # a long podcast source: minutes; once
        shots = [s.start if hasattr(s, "start") else s.get("start")
                 for s in scenes.detect_shots(path, float(info["duration"]))]
        shots = [float(s) for s in shots if s and float(s) > 0.0]
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as f:
            json.dump(shots, f)
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
    # the camera cuts to the MCU at 15.115 s (scenes: 15.12); a keep that
    # starts before it shows 3 frames of the wide (keep tools snap off it)
    keep = [(7.0, 9.9), (15.12, 18.62)]
    # program: [0, 2.9) the walk, [2.9, 6.4) the MCU
    ops = [
        # twin / ghost clones as he walks (40@18.3-19.3 red ghosts; 22@6.0)
        # the world goes grey while he walks (one colour kept: 40@0-23)...
        up({"id": "s3_mono", "type": "subject", "stage": "world",
            "start": 0.4, "end": 1.9, "kind": "isolate", "scope": "subject",
            "device": "subject_isolate"}),
        # ...and he leaves solid red clones (40@18.3-19.3)
        up({"id": "s3_clones", "type": "subject", "stage": "world",
            "start": 0.4, "end": 1.9, "kind": "echo",
            "echo": {"mode": "clone", "offsets_f": [8, 16],
                     "opacity": 0.95, "decay": 0.2, "tint": "#e8202a"},
            "device": "subject_echo"}),
        # white silhouette flash on a stride (22@8.43): 2 frames + 3 decay
        up({"id": "s3_flash1", "type": "subject", "stage": "world",
            "start": 2.2, "end": 2.37, "kind": "fill", "colour": "white",
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
            "start": 5.4, "end": 5.57, "kind": "fill", "colour": "white",
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
    # the speaker stands at x 0.41 of the 16:9 frame: centre the 9:16 on him
    return render(S, _base(keep, 0.42), ops,
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
            "start": 1.9, "end": 2.14, "kind": "fill", "colour": "white",
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
        # the robot becomes light on black after it (01@1.708): the
        # light_figure recipe's three items
        up({"id": "s2_black", "type": "background", "stage": "world",
            "start": 1.8, "end": 3.4, "kind": "solid", "colour": "black",
            "device": "background_swap"}),
        up({"id": "s2_lit", "type": "subject", "stage": "world",
            "start": 1.8, "end": 3.4, "kind": "fill", "colour": "#ff2a1a",
            "intensity": 0.55, "device": "subject_fill_flash"}),
        up({"id": "s2_glow", "type": "subject", "stage": "world",
            "start": 1.8, "end": 3.4, "kind": "glow", "colour": "#ff2a1a",
            "radius": 0.035, "intensity": 1.0, "device": "subject_glow"}),
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


# ── W1-S1 "Showcase v6" ───────────────────────────────────────────────────
# The three v5 showcase shorts (same windows, same cut, same type) re-edited
# with Wave 1 devices through the AGENT VERBS a mid-tier model calls
# (agent_tools over a local ToolContext: apply_recipe, set_composite,
# add_accent, add_finish, set_picture_card, add_subject_effect), plus the
# one motion move a verb-less stale client writes through apply_edit_batch.
# Not the chat agent itself (that is the agent-mode AT, 1F's lane): the
# same calls, chosen by hand from the ROADMAP's W1-S1 device list.

V5 = os.path.join(FIX, "baselines", "v5")
V5_RENDERS = os.path.expanduser(
    "~/Documents/Valmera/Showcase Shorts 2026-10-10")
S1 = {"thiel": ("thiel_final8.json", "idx_thiel_v3.json",
                "v5 01 Thiel - They promised us flying cars.mp4"),
      "jobs": ("jobs_final8.json", "idx_jobs_v3.json",
               "v5 02 Jobs - Computer fonts were garbage.mp4"),
      "elon": ("elon_final8.json", "idx_elon_v4.json",
               "v5 03 Elon and Rogan - Gamers make better surgeons.mp4")}


def _s1_load(name):
    spec_f, idx_f, _v5 = S1[name]
    spec = json.load(open(os.path.join(V5, spec_f)))
    index = json.load(open(os.path.join(V5, idx_f)))
    return spec["edl"], index, spec["source"]


def _verbs(edl, index, dur, message):
    """A real agent ToolContext (FakeDb, the real write path) over ``edl``;
    returns (call, final_edl)."""
    tests = os.path.join(HERE, "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    import agent_tools
    from w0c_support import tool_context
    ctx, fake = tool_context(edl, duration=dur, user_message=message)
    ctx.index = index
    log = []

    def call(verb, **kw):
        out = agent_tools.TOOLS[verb][0](ctx, **kw)
        log.append((verb, kw, out))
        print(f"[S1] {verb}({kw}) -> {out.splitlines()[0][:220]}",
              flush=True)
        return out

    def final():
        return copy.deepcopy(fake.rows[-1]["json"]) if fake.rows else edl
    return call, final, log


def s1_plan(name):
    """(edl after the verbs, ops for apply_edit_batch, verb log)."""
    import device_planner
    from schemas import validate_edl
    edl, index, src = _s1_load(name)
    dur = float(index["video"]["duration"])
    edl = validate_edl(edl, dur).model_dump()
    ops = []
    if name == "thiel":
        # the line the hook teased lands BEHIND him as he says it (05@0.23:
        # the giant lockup behind the head): FLYING, the short's one hero
        # word (one word: two wrapped onto the face), 30.12-31.2, the crown
        # crossing its letters, ghost outlines where they hide, above the
        # eyes and under the corner mark's floor (y 0.128); then the white
        # silhouette on "140" with the payoff lockup.
        ops.append({"action": "upsert", "layer": "motion", "id": "h_fly",
                    "value": {
                        "id": "h_fly", "template": "word_slam",
                        "start": 30.12, "end": 31.2,
                        "layer": "behind_subject",
                        "composite": {"ghost": "auto"},
                        "params": {"text": "FLYING", "tier": "hero",
                                   "y": 0.195, "entrance": "slam",
                                   "color": "#111114"}}})
        call, final, log = _verbs(edl, index, dur,
                                  "make this a premium short")
        # (a difference-blend 'enough' was tried: over the black shirt
        # difference reads exactly as normal, so it is left out — a device
        # that changes nothing visible does not earn its place)
        call("apply_recipe", name="white_silhouette")
    elif name == "jobs":
        call, final, log = _verbs(edl, index, dur,
                                  "make this a premium short")
        # v5 already holds the archival in a card: round it further
        card = edl["effects"]["picture_cards"][0]
        call("set_picture_card", id=card["id"], start=card["start"],
             end=card["end"], radius=0.06)
        call("add_accent", kind="white_hit", on="stress", density="pulse",
             region="card", count=6)
        # the archival is re-photographed off a tube: the CRT inside the
        # card only (the backdrop and the type stay clean)
        call("add_finish", kind="crt", strength=0.55,
             region={"shape": "card"})
    else:
        call, final, log = _verbs(edl, index, dur,
                                  "make this a premium short")
        # the close ('end': "You should be required in medical school...")
        # is this edit's payoff line; v5 declares none, so the verb is told
        close = next(m for m in edl["motion"] if m["id"] == "end")
        call("apply_recipe", name="colour_kept_payoff",
             at=round(float(close["start"]) + 0.3, 2))
        # the turn: from the evidence (three stats) to the prescription,
        # on Rogan's reaction "That's INCREDIBLE" — a 2-frame negative of
        # the speaker only, then the drained world for the payoff line
        plan = device_planner.moments(final(), index)
        turn = next((m for m in plan["moments"]
                     if (m.get("word") or "").strip(".,!?").lower()
                     == "incredible"), None)
        if turn is not None:
            t = round(float(turn["t"]) + 0.06, 2)
            call("add_subject_effect", kind="negate", scope="subject",
                 start=t, end=round(t + 2 / 30.0 + 0.004, 3))
    return final(), ops, log, index, src


def s1_short(out_dir, name):
    edl, ops, log, index, src = s1_plan(name)
    keep = edl["keep"]
    S = setup(src, [(max(0.0, a - 1.0), b + 1.0) for a, b in keep],
              faces=True)
    S["index"] = dict(index, video=dict(index["video"],
                                        **S["index"]["video"]),
                      shots=S["index"]["shots"])
    out = os.path.join(out_dir, f"s1_{name}.mp4")
    with open(out + ".verbs.json", "w") as f:
        json.dump([{"verb": n, "args": kw, "result": r.splitlines()[:6]}
                   for n, kw, r in log], f, indent=1, default=str)
    # v5's own media, as its render_showcase.py maps them: the payoff-hold
    # stills (kept beside the v5 EDLs) and the approved-library sound
    import sound_library
    assets = {}
    for ins in edl.get("inserts") or []:
        key = str(ins.get("asset_key") or "")
        if key.startswith("local:"):
            assets[key] = os.path.join(V5, os.path.basename(key[6:]))
    for sx in edl.get("sfx") or []:
        sid = sound_library.id_for_key(sx["storage_key"]) or \
            sx["storage_key"].split("/")[-1].rsplit(".", 1)[0]
        assets[sx["storage_key"]] = sound_library.path(sid)
        # v5 wrote ``at`` as the hit moment (sfx_at_is_hit), placed as
        # add_sfx places it
        pl = sound_library.place(sid, sx["at"], dur_s=sx.get("dur_s"))
        sx["at"] = pl["at"]
        if pl["offset_s"]:
            sx["offset_s"] = pl["offset_s"]
        if pl["dur_s"]:
            sx["dur_s"] = pl["dur_s"]
    return render(S, edl, ops, out, want_wm=True, assets=assets)


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
    ap.add_argument("--s1", action="append", choices=sorted(S1),
                    help="only these W1-S1 shorts")
    ap.add_argument("--boards", help="build the boards from --out into this "
                    "directory (no renders)")
    a = ap.parse_args(argv)
    os.environ.setdefault("VALMERA_SIDECAR_DIR",
                          os.path.join(VALMERA, "engines", "w1", "sidecars"))
    os.environ.setdefault("MATTE_RVM_MODEL", os.path.join(
        VALMERA, "models", "rvm_mobilenetv3_fp32.onnx"))
    os.environ.setdefault("YUNET_MODEL", os.path.join(
        VALMERA, "models", "face_detection_yunet_2023mar.onnx"))
    os.makedirs(a.out, exist_ok=True)
    if a.boards:
        print(build_boards(a.out, a.boards))
        return
    pieces = a.piece or ["S2", "S3"]
    spath = os.path.join(a.out, "summary.json")
    summary = json.load(open(spath)) if os.path.exists(spath) else {}
    if "S3" in pieces:
        r = [s3_walkon(a.out), s3_ledwall(a.out), s3_stage(a.out)]
        summary["S3"] = {os.path.basename(p): {"wall_s": w, "rows": rows}
                         for p, w, rows in r}
        concat([p for p, _w, _r in r], os.path.join(a.out, "W1-S3.mp4"))
    if "S1" in pieces:
        summary.setdefault("S1", {})
        for nm in (a.s1 or S1):
            p, w, rows = s1_short(a.out, nm)
            summary["S1"][os.path.basename(p)] = {"wall_s": w, "rows": rows}
    if "S2" in pieces:
        p, w, rows = s2_lab(a.out)
        summary["S2"] = {os.path.basename(p): {"wall_s": w, "rows": rows}}
    with open(spath, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1)[:4000])



# ── the boards: ours against the named reference moments ──────────────────

BOARD_ROWS = {
    "W1-S3": [
        ("s3_a_walkon.mp4", 30, "red ghost clones on the walk",
         "40", 18.8, "40@18.8 red ghost clones"),
        ("s3_a_walkon.mp4", 66, "white silhouette flash (2 frames + decay)",
         "22", 8.43, "22@8.43 white silhouette flash"),
        ("s3_a_walkon.mp4", 120, "the speaker held, the world swaps",
         "20", 7.40, "20@7.40 subject held, background swaps"),
        ("s3_b_ledwall.mp4", 30, "poster: the speaker on the accent field",
         "38", 0.6, "38@0.6 poster field"),
        ("s3_b_ledwall.mp4", 66, "silhouette on a cream field",
         "23", 2.5, "23@2.5 head silhouette poster"),
        ("s3_c_stage.mp4", 20, "a rim of light on black",
         "01", 1.8, "01@1.8 glowing silhouette"),
        ("s3_c_stage.mp4", 48, "white silhouette hit",
         "40", 4.67, "40@4.67 silhouette flash"),
    ],
    "W1-S2": [
        ("s2_lab.mp4", 16, "registered variant strobe: negative",
         "01", 0.5, "01@0.5 registered strobe"),
        ("s2_lab.mp4", 20, "registered variant strobe: blueprint",
         "01", 0.5, "01@0.5 registered strobe"),
        ("s2_lab.mp4", 22, "registered variant strobe: thermal",
         "17", 1.83, "17@1.83 thermal flip"),
        ("s2_lab.mp4", 60, "glowing silhouette (the robot)",
         "01", 1.8, "01@1.8 glowing silhouette"),
        ("s2_lab.mp4", 120, "pixel-stretch junction into the launch",
         "36", 0.6, "36@0.6 pixel stretch"),
        ("s2_lab.mp4", 166, "thermal on the plume",
         "17", 1.83, "17@1.83 thermal"),
    ],
}


def frame_png(path, n, out, w=360):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                    f"select='eq(n\\,{int(n)})',scale={w}:-2",
                    "-fps_mode", "passthrough", "-frames:v", "1", out],
                   check=True)
    return out


# W1-S1: each device moment of a v6 short against v5 at the same program
# second (same window, same cut) and against the named reference moment.
S1_ROWS = [
    ("thiel", 30.67, "FLYING behind the head, ghost outlines", "05", 0.5),
    ("thiel", 31.27, "white silhouette on '140'", "40", 4.67),
    ("thiel", 25.62, "difference-blend ENOUGH", "12", 2.542),
    ("jobs", 3.97, "white hit clipped to the card", "02", 13.7),
    ("jobs", 2.0, "CRT re-photography inside the card", "14", 0.6),
    ("elon", 18.29, "subject-only negative on the turn", "13", 6.92),
    ("elon", 20.2, "grey world, speaker in colour on the payoff", "40",
     10.0),
]


def build_s1_board(out_dir, dest):
    from PIL import Image
    rows = []
    for name, t, label, rid, rt in S1_ROWS:
        ours = os.path.join(out_dir, f"s1_{name}.mp4")
        if not os.path.exists(ours):
            continue
        k = len(rows)
        a = grab(ours, t, os.path.join(dest, f"W1-S1_{k}_ours.png"))
        v5 = grab(os.path.join(V5_RENDERS, S1[name][2]), t,
                  os.path.join(dest, f"W1-S1_{k}_v5.png"))
        r = grab(ref(rid + "_"), rt, os.path.join(dest, f"W1-S1_{k}_ref.png"))
        # ours | v5 side by side as one tile, the reference beside them
        ia, ib = Image.open(a).convert("RGB"), Image.open(v5).convert("RGB")
        pair = Image.new("RGB", (ia.width + ib.width + 8, max(ia.height,
                                                            ib.height)),
                         (16, 16, 16))
        pair.paste(ia, (0, 0))
        pair.paste(ib, (ia.width + 8, 0))
        pp = os.path.join(dest, f"W1-S1_{k}_pair.png")
        pair.save(pp)
        rows.append((f"{name} v6 | v5 @{t:.2f}s: {label}", pp, r,
                     f"{rid}@{rt:g}"))
    if not rows:
        return None
    return board(rows, os.path.join(dest, "W1-S1_board.jpg"),
                 "W1-S1 v6 vs v5 (left pair) and the reference (internal: "
                 "references never leave this machine)")


def build_boards(out_dir, dest):
    os.makedirs(dest, exist_ok=True)
    made = []
    s1 = build_s1_board(out_dir, dest)
    if s1:
        made.append(s1)
    for name, rows in BOARD_ROWS.items():
        cells = []
        for k, (clip, n, label, rid, rt, rlabel) in enumerate(rows):
            src = os.path.join(out_dir, clip)
            if not os.path.exists(src):
                continue
            ours = frame_png(src, n, os.path.join(dest, f"{name}_{k}_ours.png"))
            theirs = grab(ref(rid + "_"), rt,
                          os.path.join(dest, f"{name}_{k}_ref.png"))
            cells.append((label, ours, theirs, rlabel))
        if cells:
            made.append(board(cells, os.path.join(dest, f"{name}_board.jpg"),
                              f"{name} (internal: references never leave "
                              "this machine)"))
    return made


if __name__ == "__main__":
    main()
