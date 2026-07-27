"""Round 51 — the strip of real frames behind the timeline.

The studio timeline drew every clip as a flat red gradient block labelled with
its source times. That is a diagram of an edit, not a view of one: deciding
where to cut means seeing WHAT is there, and the only way to see it was to
scrub the player and lose your place.

The constraint is the browser, not the pixels. Two obvious implementations are
both wrong on the machine that matters:

  * seek a hidden <video> and drawImage each thumbnail — dozens of decoder
    seeks on the main thread, which is exactly the jank the user asked not to
    have;
  * one HTTP request per thumbnail — a hundred requests for a two-minute clip.

So: ONE sprite sheet, built here with ONE ffmpeg call, fetched by the browser
as ONE image, and drawn as `background-position` offsets. Every tile after the
first costs the page nothing — no request, no decode, no JavaScript. Scrolling
and zooming the timeline is then pure compositing.

It is deliberately NOT part of the index. Adding an artifact to the index would
mean bumping PIPELINE_VERSION, which re-indexes every project in the fleet —
and index capacity is this product's live ceiling (WORKER_INDEX_SLOTS is 1; a
real customer once queued 63 minutes behind someone else's upload). A filmstrip
is worth a few seconds of ffmpeg, not a re-analysis of everyone's footage. It
is built on demand, from the PROXY, and cached in storage under the source's
own sha — so it survives re-indexes and is never rebuilt for the same video.
"""
import math
import os

import config
import media
import storage

# Tile geometry. 160px wide is the largest a tile is ever drawn on a timeline
# lane (44px tall at 16:9 is 78px wide; 2x for retina is 156), and a 10-wide
# grid keeps the sheet inside the 4096px texture limit that older mobile GPUs
# enforce — a wider sheet decodes to a blank image on exactly the devices this
# product has already been bitten by.
TILE_W = 160
COLS = 10
MAX_TILES = 200
# Never denser than this. A tile every 0.25s of a 20-minute video is 4800
# thumbnails; the cap is what bounds both the ffmpeg pass and the sheet size,
# and the interval is reported so the client can address tiles by time.
MIN_INTERVAL_S = 0.5


def plan(duration_s, max_tiles=MAX_TILES):
    """(n_tiles, interval_s, cols, rows) for a video of this length."""
    dur = max(0.1, float(duration_s))
    n = min(max_tiles, max(2, int(math.floor(dur / MIN_INTERVAL_S))))
    interval = dur / n
    cols = min(COLS, n)
    rows = int(math.ceil(n / float(cols)))
    return n, interval, cols, rows


def storage_key(project_id, sha, n):
    # Keyed on the source sha, not the project's current state: a re-index, an
    # EDL rewrite or a hundred renders do not change which frames are in the
    # file, so none of them should rebuild this.
    return f"filmstrip/{project_id}/{(sha or 'nosha')[:16]}-{n}.jpg"


def build(src, out_path, duration_s, tile_h=None, max_tiles=MAX_TILES):
    """One ffmpeg call: sample, scale, tile. Returns the sheet's metadata.

    fps= sampling rather than N seeks: seeking 200 times through a proxy costs
    200 keyframe searches, while a single linear decode at a low output rate
    reads the file once.
    """
    n, interval, cols, rows = plan(duration_s, max_tiles)
    info = media.probe(src)
    sw, sh = int(info["width"] or 16), int(info["height"] or 9)
    th = tile_h or max(2, int(round(TILE_W * sh / max(1, sw) / 2)) * 2)
    # `-frames:v 1` on the tiled output: tile emits one frame per full grid, so
    # this stops the moment the grid is complete instead of decoding the tail
    # of the video into sheets nobody asked for.
    media.run([
        "ffmpeg", "-y", "-v", "error", "-i", src,
        "-vf", (f"fps=1/{interval:.6f},scale={TILE_W}:{th}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={TILE_W}:{th},tile={cols}x{rows}"),
        "-frames:v", "1", "-q:v", "4", out_path])
    return {"tiles": n, "interval_s": round(interval, 4), "cols": cols,
            "rows": rows, "tile_w": TILE_W, "tile_h": th,
            "duration_s": round(float(duration_s), 3)}


def run_filmstrip_job(worker_db, job):
    """Job runner: build (or find) this project's sheet and return its key.

    Idempotent and cheap on a repeat: an existing object short-circuits before
    anything is downloaded, so the studio can ask for this on every open.
    """
    import db as dbx
    project_id = job["project_id"]
    row = worker_db.run(dbx.latest_asset, project_id, "proxy") or \
        worker_db.run(dbx.latest_asset, project_id, "original")
    if not row:
        return {"available": False,
                "reason": "this project has no video to sample yet"}
    src_row = worker_db.run(dbx.latest_asset, project_id, "original") or row
    dur = float(row.get("duration_s") or src_row.get("duration_s") or 0.0)
    if dur <= 0.2:
        return {"available": False, "reason": "the video has no duration"}
    n, interval, cols, rows = plan(dur)
    key = storage_key(project_id, src_row.get("sha256"), n)
    meta = {"tiles": n, "interval_s": round(interval, 4), "cols": cols,
            "rows": rows, "tile_w": TILE_W,
            "duration_s": round(dur, 3)}
    if storage.exists(key):
        # tile_h is not stored anywhere, and it does not need to be: the client
        # knows the sheet's pixel size once the image loads and the grid is
        # cols x rows, so the tile height falls out of division. Reporting 0
        # says "measure it" rather than reporting a number that might be wrong.
        meta["tile_h"] = 0
        return {"available": True, "key": key, "cached": True, **meta}

    workdir = os.path.join(config.TMP_DIR, f"strip_{project_id}")
    os.makedirs(workdir, exist_ok=True)
    local = os.path.join(workdir, "src" + os.path.splitext(
        row["storage_key"])[1])
    out = os.path.join(workdir, "strip.jpg")
    try:
        storage.download_to(row["storage_key"], local)
        meta = build(local, out, dur)
        storage.upload_file(out, key, "image/jpeg")
    finally:
        for p in (local, out):
            try:
                os.remove(p)
            except OSError:
                pass
    return {"available": True, "key": key, "cached": False, **meta}
