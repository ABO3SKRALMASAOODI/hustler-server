"""The Variant Forge: registered stills from one frame (track 1E).

``make_variant_set(tool_ctx, source_t, kinds) -> [asset_key]`` (the pinned
peer, INTERFACES_W1 §1.5) pulls ONE frame of the main video at source second
``source_t`` and derives the asked variants from it — photo, negative, line
art, blueprint, thermal, X-ray, solarise, posterise, kaleidoscope, pixel
stretch, night vision, halftone — as project image assets. They come from
the same pixels, so they are registered by construction: a 2-frame strobe of
them (1D) never jumps, and an edge in one is an edge in all.

It runs inside the verb on the agent lane: ffmpeg and numpy, no model, no
browser, one ffmpeg process for every kind (the frame is decoded once). The
frame is the original's when a ranged read of it lands within the budget,
else the proxy's. The looks are finish.py's own graphs on one frame, so a
strobe of variants matches the live finish of the same kind.
"""

import hashlib
import json
import os
import subprocess
import time

import finish

VARIANT_KINDS = ("photo", "negative", "line", "blueprint", "thermal",
                 "xray", "solarise", "posterise", "kaleidoscope",
                 "pixel_stretch", "night_vision", "halftone")
# The variant set a product strobe uses when none is named (ref 01).
DEFAULT_KINDS = ("photo", "negative", "line", "blueprint", "thermal")
# variant kind -> finish kind (photo and negative are their own)
FINISH_OF = {"line": "line_art"}
MAX_SIDE = 1920
FRAME_TIMEOUT_S = 25


def finish_kind(kind):
    return FINISH_OF.get(kind, kind)


def max_kinds():
    try:
        import config
        return int(getattr(config, "VARIANT_MAX_KINDS", 6))
    except Exception:  # noqa: BLE001
        return 6


def check_kinds(kinds):
    """The kinds as a clean tuple, or ValueError naming the problem."""
    if kinds is None:
        return DEFAULT_KINDS
    if isinstance(kinds, str):
        kinds = [k.strip() for k in kinds.split(",") if k.strip()]
    out = []
    for k in kinds:
        k = str(k).strip().lower()
        if k not in VARIANT_KINDS:
            raise ValueError(f"unknown variant {k!r}; kinds: "
                             + ", ".join(VARIANT_KINDS))
        if k not in out:
            out.append(k)
    if not out:
        raise ValueError("name at least one variant kind")
    cap = max_kinds()
    if len(out) > cap:
        raise ValueError(f"at most {cap} variants per set (asked for "
                         f"{len(out)})")
    return tuple(out)


def variant_graph(kinds, W, H, workdir, seed=0):
    """(filter_complex, map_paths, out_labels): every kind from input 0 in
    one graph. Map stills are inputs 1..n."""
    parts = []
    paths = []
    n = len(kinds)
    srcs = [f"vs{i}" for i in range(n)]
    parts.append(f"[0:v]scale={W}:{H},format=yuv420p,setsar=1,split={n}"
                 + "".join(f"[{s}]" for s in srcs))
    outs = []
    for i, kind in enumerate(kinds):
        item = {"id": f"variant_{kind}", "kind": finish_kind(kind),
                "seed": seed}
        out = f"vo{i}"
        if kind == "photo":
            parts.append(f"[{srcs[i]}]format=rgb24[{out}]")
        elif kind == "negative":
            parts.append(f"[{srcs[i]}]negate,format=rgb24[{out}]")
        else:
            maps = {}
            for nm in finish.maps_for(item):
                paths.append(finish.map_path(workdir, nm, W, H,
                                             finish._scale(item)))
                maps[nm] = finish._map_getter(parts, f"{len(paths)}:v", 1,
                                              25.0, f"m{i}{nm[:3]}")
            g = finish.Graph(parts, f"v{i}", W, H, 25.0, 1, maps=maps,
                             seed=seed)
            last = finish.BUILDERS[finish_kind(kind)](g, srcs[i], item)
            parts.append(f"[{last}]format=rgb24[{out}]")
        outs.append(out)
    return ";".join(parts), paths, outs


def render_variants(frame_path, kinds, out_dir, *, seed=0, threads=None):
    """Write one PNG per kind from ``frame_path``. Returns
    ({kind: path}, {"W", "H", "wall_s", "cpu_s"})."""
    from PIL import Image
    kinds = check_kinds(kinds)
    with Image.open(frame_path) as im:
        w, h = im.size
    k = min(1.0, MAX_SIDE / float(max(w, h)))
    W = max(2, int(round(w * k / 2)) * 2)
    H = max(2, int(round(h * k / 2)) * 2)
    os.makedirs(out_dir, exist_ok=True)
    graph, maps, outs = variant_graph(kinds, W, H, out_dir, seed=seed)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", frame_path]
    for m in maps:
        cmd += ["-i", m]
    if threads:
        cmd += ["-filter_threads", str(int(threads))]
    script = os.path.join(out_dir, "variants.ffgraph")
    with open(script, "w") as f:
        f.write(graph)
    cmd += ["-/filter_complex", script]
    paths = {}
    for kind, out in zip(kinds, outs):
        p = os.path.join(out_dir, f"variant_{kind}.png")
        cmd += ["-map", f"[{out}]", "-frames:v", "1", "-update", "1", p]
        paths[kind] = p
    t0 = time.time()
    c0 = _child_cpu()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"variant render failed: {r.stderr[-400:]}")
    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"variant render wrote nothing for {missing}")
    return paths, {"W": W, "H": H, "wall_s": round(time.time() - t0, 2),
                   "cpu_s": round(_child_cpu() - c0, 2)}


def _child_cpu():
    t = os.times()
    return t.children_user + t.children_system


def grab_frame(tool_ctx, source_t, dst):
    """One frame of the main video at ``source_t`` into ``dst`` (PNG):
    the original through a ranged read when it answers in time, else the
    proxy. Returns 'original' or 'proxy'."""
    import media
    try:
        import db as dbx
        import storage
        orig = tool_ctx.db.run(dbx.latest_asset, tool_ctx.project_id,
                               "original")
        key = (orig or {}).get("storage_key")
        if key:
            url = storage.presign_get(key, expires=900)
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss",
                 f"{max(0.0, float(source_t)):.3f}", "-i", url,
                 "-frames:v", "1", "-update", "1", dst],
                capture_output=True, timeout=FRAME_TIMEOUT_S)
            if r.returncode == 0 and os.path.exists(dst) and \
                    os.path.getsize(dst) > 0:
                return "original"
    except Exception:  # noqa: BLE001 — the proxy is the honest fallback
        pass
    media.frame_at(tool_ctx.proxy_path(), float(source_t), dst)
    return "proxy"


def make_variant_set(tool_ctx, source_t, kinds=None):
    """[asset_key] of registered variant stills of the main video at SOURCE
    second ``source_t`` (INTERFACES_W1 §1.5). Raises ValueError for bad
    kinds or a time outside the video; RuntimeError when a frame or a
    render fails."""
    import db as dbx
    import storage
    kinds = check_kinds(kinds)
    if not getattr(tool_ctx, "has_main_video", False):
        raise ValueError("variants are made from the main video, and this "
                         "project has none")
    t = float(source_t)
    if not 0.0 <= t < float(tool_ctx.duration or 0.0):
        raise ValueError(f"source second {t:g} is outside the video "
                         f"(0-{float(tool_ctx.duration or 0):g}s)")
    work = os.path.join(tool_ctx.workdir, f"variants_{int(t * 1000)}")
    os.makedirs(work, exist_ok=True)
    frame = os.path.join(work, "frame.png")
    origin = grab_frame(tool_ctx, t, frame)
    seed = int(hashlib.sha256(f"{tool_ctx.project_id}:{t:.3f}".encode())
               .hexdigest()[:6], 16)
    paths, stats = render_variants(frame, kinds, work, seed=seed)
    keys = []
    batch = hashlib.sha256(json.dumps([tool_ctx.project_id, t, kinds])
                           .encode()).hexdigest()[:10]
    for kind in kinds:
        p = paths[kind]
        key = f"generated/{tool_ctx.project_id}/variant-{batch}-{kind}.png"
        storage.upload_file(p, key, "image/png")
        tool_ctx.db.run(
            dbx.insert_asset, tool_ctx.project_id, "image_ref", key,
            bytes_=os.path.getsize(p), width=stats["W"], height=stats["H"],
            meta={"filename": f"variant-{kind}.png",
                  "caption": f"{kind} variant of the frame at {t:.2f}s",
                  "generated": True, "model": "local:variant_forge",
                  "variant": kind, "variant_set": batch,
                  "variant_of": {"source_t": round(t, 3), "frame": origin}})
        keys.append(key)
    tool_ctx._last_variant_stats = dict(stats, origin=origin, kinds=kinds)
    return keys
