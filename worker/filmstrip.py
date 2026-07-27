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
ROUND 54 — the same argument, applied to everything else on the timeline.

The main footage got its frames in round 51 and every OTHER lane stayed a
coloured rectangle: an inserted clip was a blue block, a song was a purple
block, a voiceover was a green block. So the timeline told you WHERE your
music was and nothing about WHAT it was — you cannot see a drop, a silent
tail, or that you dropped the wrong take, which is exactly the information an
editor reads a timeline for.

Two artifacts are added here, and the choice of transport is the interesting
part in both:

  * per-ASSET frames — one small sheet per inserted clip / image, built the
    same way as the main one and cached under the asset's own storage key, so
    the same clip used in ten projects is sampled once per project and never
    twice within one.

  * WAVEFORM PEAKS — for the main video's own audio and for every audio asset
    (uploads AND bundled library tracks). These do NOT become storage objects.
    A peak envelope at the resolution a 44px lane can actually draw is a few
    hundred bytes; storing it would mean an object, a presign and a round trip
    per track to deliver less data than the presigned URL itself. They ride
    inside the job result as base64 uint8, so the studio gets every waveform in
    the SAME poll that tells it the strip is ready — no extra request at all.
"""
import base64
import hashlib
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

# ── Per-asset artifacts ─────────────────────────────────────────────────────
# How many assets one job will look at. A project with 40 b-roll clips is real,
# and decoding all of them would turn a few seconds of decoration into minutes
# of the media lane — which is the lane people are waiting on for previews.
MAX_ASSETS = 14
# An inserted clip is normally a few seconds. Past this it is a second FEATURE,
# not an insert, and a linear decode of it is not worth a timeline block's
# background: those get a single poster frame from one seek instead.
ASSET_STRIP_MAX_S = 180.0
ASSET_MAX_TILES = 16

# Waveform resolution. The envelope is drawn into a lane 16-26px tall and at
# most ~1400px wide at 8x zoom, so more points than this cannot be seen; fewer
# starts hiding real silences. Main gets more because it spans the whole
# program and is the one people zoom into.
WAVE_POINTS_MAIN = 1400
WAVE_POINTS_ASSET = 320
# Decode rate for the envelope pass. 1kHz mono is 1/48th of the samples and
# still 700x denser than the finest bucket we draw, so the peak inside each
# bucket is the real peak — this is a downsample, not an approximation.
WAVE_RATE_HZ = 1000


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


def asset_storage_key(project_id, storage_or_ref, n):
    """Sheet key for ONE asset. Hashed rather than path-derived because the
    reference can be a storage path OR a bundled `library:slug`, and one of
    those has no path to derive from."""
    h = hashlib.sha1((storage_or_ref or "").encode("utf-8",
                                                   "replace")).hexdigest()[:16]
    return f"filmstrip/{project_id}/a-{h}-{n}.jpg"


# ── Waveform envelope ───────────────────────────────────────────────────────

def peaks(src, n_points, duration_s, workdir):
    """A peak envelope for a file's audio, as a list of 0-255 ints.

    Returns None when the file has no audio at all — which is a real answer,
    not a failure: a silent clip must draw as a flat lane, and inventing an
    envelope for it would be a picture of sound that isn't there.

    The decode goes to a FILE rather than a pipe on purpose. media.run reads
    its child's output as text with errors='replace', which would mangle every
    byte of raw PCM above 0x7f; a second, subtly different subprocess helper
    here is how the two would drift.
    """
    n_points = max(8, int(n_points))
    raw = os.path.join(workdir, f"wave_{abs(hash(src)) % 10_000_000}.raw")
    try:
        media.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn",
                   "-ac", "1", "-ar", str(WAVE_RATE_HZ),
                   "-f", "s16le", "-acodec", "pcm_s16le", raw], timeout=600)
    except Exception:
        return None
    try:
        with open(raw, "rb") as f:
            buf = f.read()
    except OSError:
        return None
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    total = len(buf) // 2
    if total < 4:
        return None
    # Bucket over the DECLARED duration, not over the sample count, so a file
    # whose audio stream is shorter than its picture (a video with a trailing
    # silent tail, or an audio stream that starts late) draws its envelope at
    # the times the sound actually happens instead of stretched to fill.
    dur = float(duration_s or 0.0)
    span = total
    if dur > 0.05:
        want = int(dur * WAVE_RATE_HZ)
        # Only ever pad — never crop. A decode longer than the probe said is
        # the probe being wrong, and truncating real audio to match it would
        # silently hide the tail of a track.
        span = max(total, want)
    import array
    pcm = array.array("h")
    pcm.frombytes(buf[:total * 2])
    out = []
    peak_all = 1
    for i in range(n_points):
        a = int(i * span / n_points)
        b = int((i + 1) * span / n_points)
        if b <= a:
            b = a + 1
        if a >= total:
            out.append(0)
            continue
        chunk = pcm[a:min(b, total)]
        if not chunk:
            out.append(0)
            continue
        m = max(-min(chunk), max(chunk))
        peak_all = max(peak_all, m)
        out.append(m)
    # Normalised to the file's own loudest moment. An absolute scale would draw
    # a quietly-recorded voiceover as a flat line next to a mastered song, and
    # the reason to look at a waveform is the SHAPE within one clip.
    return [min(255, int(round(v * 255.0 / peak_all))) for v in out]


def encode_peaks(vals):
    """Peaks as base64 uint8 — the form that rides inside the job result."""
    if not vals:
        return None
    return base64.b64encode(bytes(bytearray(vals))).decode("ascii")


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


def _local_for_ref(ref, workdir, tag):
    """A readable local path for one timeline reference, or None.

    Two kinds of reference reach the timeline and only one of them is a
    storage object: a bundled `library:`/`sfx:` track already lives in this
    image, so it is opened where it is rather than round-tripped through R2.
    """
    if not ref:
        return None
    try:
        import music_library
        if music_library.is_library_ref(ref):
            return music_library.local_path(ref)
    except Exception:
        pass
    try:
        import sfx_library
        if sfx_library.is_library_ref(ref):
            return sfx_library.local_path(ref)
    except Exception:
        pass
    ext = os.path.splitext(ref)[1].lower()[:6] or ".bin"
    local = os.path.join(workdir, f"{tag}{ext}")
    try:
        storage.download_to(ref, local)
    except Exception:
        return None
    return local


def _asset_artifacts(project_id, ref, kind, duration_s, workdir, tag):
    """Frames and/or a waveform for ONE timeline asset.

    Every failure here is swallowed into "this one gets no artifact". The
    whole feature is decoration on a panel that has drawn correctly since
    round 5, and one unreadable clip must not cost the other thirteen their
    thumbnails — let alone fail the job.
    """
    out = {"kind": kind}
    dur = float(duration_s or 0.0)
    if dur > 0.05:
        out["duration_s"] = round(dur, 3)

    # A still and a long clip both collapse to one tile — for opposite reasons.
    # An image has one frame; a 40-minute clip has too many to pay for, and one
    # poster frame answers "which clip is this?" just as well as sixteen.
    n_want = (1 if kind == "image" or dur <= 0.05 or dur > ASSET_STRIP_MAX_S
              else ASSET_MAX_TILES)
    want_sheet = kind in ("video", "image")
    cached_key = None
    if want_sheet and dur > 0.05:
        k = asset_storage_key(project_id, ref, n_want)
        try:
            if storage.exists(k):
                cached_key = k
        except Exception:
            cached_key = None
    # An image has no waveform, so a cached sheet means there is nothing left
    # to do — and nothing to download. That is the common case on a rebuild.
    if cached_key and kind == "image":
        return {**out, "key": cached_key, "tiles": n_want,
                "interval_s": max(0.05, dur or 1.0), "cols": 1, "rows": 1}

    local = _local_for_ref(ref, workdir, tag)
    if not local or not os.path.exists(local):
        # A cached sheet still beats nothing when the file itself is gone.
        return {**out, "key": cached_key, "tiles": n_want,
                "interval_s": max(0.05, dur or 1.0), "cols": 1,
                "rows": 1} if cached_key and kind == "image" else None
    try:
        if dur <= 0.05:
            try:
                dur = float(media.duration_of(local) or 0.0)
            except Exception:
                dur = 0.0
            if dur > 0.05:
                out["duration_s"] = round(dur, 3)
                n_want = (1 if kind == "image" or dur > ASSET_STRIP_MAX_S
                          else ASSET_MAX_TILES)

        if want_sheet:
            sheet = os.path.join(workdir, f"{tag}.jpg")
            try:
                if n_want <= 1:
                    # An IMAGE has exactly one frame, at t=0. Seeking 10% into
                    # a still finds nothing and media.frame_at correctly raises
                    # — which silently dropped every image insert back to a
                    # flat orange rectangle. Only a clip has a 10% to seek to.
                    at = 0.0 if kind == "image" else \
                        min(max(0.0, dur * 0.1), max(0.0, dur - 0.05))
                    media.frame_at(local, at, sheet, width=TILE_W)
                    # tile_w/tile_h deliberately absent: the client measures
                    # them off the loaded image (sheet size / cols x rows),
                    # so a still needs no probe at all.
                    m = {"tiles": 1, "interval_s": max(0.05, dur or 1.0),
                         "cols": 1, "rows": 1}
                else:
                    m = build(local, sheet, dur, max_tiles=n_want)
            except Exception:
                m = None
            if m:
                key = asset_storage_key(project_id, ref, m["tiles"])
                try:
                    storage.upload_file(sheet, key, "image/jpeg")
                    out.update({k: m[k] for k in
                                ("tiles", "interval_s", "cols", "rows")})
                    out["key"] = key
                except Exception:
                    pass
            try:
                os.remove(sheet)
            except OSError:
                pass
            if not out.get("key") and cached_key:
                # The re-sample failed but a sheet from a previous build is
                # still in storage — serve that rather than dropping this
                # asset back to a flat rectangle.
                n_c, iv_c, c_c, r_c = plan(max(0.1, dur), max_tiles=n_want)
                out.update({"key": cached_key, "tiles": n_c,
                            "interval_s": round(iv_c, 4),
                            "cols": c_c, "rows": r_c})

        # Sound belongs to video clips too — an inserted clip carries audio
        # into the edit, and a lane that shows its frames but hides its sound
        # is why "why is this clip loud?" has no answer on the timeline.
        if kind in ("audio", "video"):
            enc = encode_peaks(peaks(local, WAVE_POINTS_ASSET, dur, workdir))
            if enc:
                out["wave"] = enc
    finally:
        try:
            if local.startswith(workdir):
                os.remove(local)
        except OSError:
            pass
    return out if (out.get("key") or out.get("wave")) else None


# Bumped whenever this job's RESULT gains a field the studio draws from. The
# backend uses it to ask for one rebuild of a strip built by an older worker —
# and, per the round-53 download bug, it asks WITHOUT withholding what it
# already has: a pre-round-54 result still serves its frames while the
# waveforms are being built.
TIMELINE_MEDIA_VERSION = 1

_KIND_MAP = {"video_clip": "video", "image_ref": "image",
             "music": "audio", "audio": "audio"}


def run_filmstrip_job(worker_db, job):
    """Build (or find) everything the timeline draws itself from.

    The main sheet is cached in storage and short-circuits; the waveforms are
    not stored at all (see the module docstring), so a job whose sheet already
    exists still opens the proxy when it has envelopes to measure.
    """
    import db as dbx
    project_id = job["project_id"]
    payload = job.get("payload") or {}
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
            # tile_h is not stored anywhere, and it does not need to be: the
            # client knows the sheet's pixel size once the image loads and the
            # grid is cols x rows, so the tile height falls out of division.
            # Reporting 0 says "measure it" rather than a number that may be
            # wrong.
            "tile_h": 0,
            "duration_s": round(dur, 3),
            "tm_v": TIMELINE_MEDIA_VERSION,
            # Echoed back verbatim, never recomputed here. The backend decides
            # when the asset set has moved on, so the value it compares against
            # is the value it supplied — a gate can only be trusted when it is
            # not also the thing being gated.
            "sig": payload.get("sig")}

    workdir = os.path.join(config.TMP_DIR, f"strip_{project_id}")
    os.makedirs(workdir, exist_ok=True)
    local = os.path.join(workdir, "src" + os.path.splitext(
        row["storage_key"])[1])
    out = os.path.join(workdir, "strip.jpg")
    cached = storage.exists(key)
    try:
        try:
            storage.download_to(row["storage_key"], local)
        except Exception as e:
            # A REBUILD (the sheet already exists, we came back only for the
            # waveforms) must not fail the job over a download: the result
            # would then carry no key at all and the studio would lose the
            # frames it already had. Only a first build needs the file.
            if cached:
                print(f"[filmstrip] project {project_id}: proxy unreadable "
                      f"({e}) — keeping the cached sheet, no waveforms",
                      flush=True)
                return {"available": True, "key": key, "cached": True,
                        **meta, "assets": {}}
            raise
        if not cached:
            built = build(local, out, dur)
            storage.upload_file(out, key, "image/jpeg")
            meta.update({k: built[k] for k in
                         ("tiles", "interval_s", "cols", "rows", "tile_h")})
        meta["wave"] = encode_peaks(peaks(local, WAVE_POINTS_MAIN, dur,
                                          workdir))
    finally:
        for p in (local, out):
            try:
                os.remove(p)
            except OSError:
                pass

    # ── Everything else on the timeline ──────────────────────────────────
    assets = {}
    try:
        rows_ = worker_db.run(dbx.assets_by_kinds, project_id,
                              list(_KIND_MAP), limit=MAX_ASSETS * 3) or []
    except Exception:
        rows_ = []
    todo = []
    seen = set()
    for a in rows_:
        ref = a.get("storage_key")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        todo.append((ref, _KIND_MAP.get(a.get("kind"), "video"),
                     a.get("duration_s")))
    # Bundled tracks are not assets, so nothing above finds the library music
    # that is sitting on the user's timeline right now. Read the live EDL for
    # them — it is the only place a `library:` reference exists.
    try:
        edl = (worker_db.run(dbx.latest_edl, project_id) or {}).get("json") or {}
        for m in (edl.get("music") or []):
            ref = m.get("storage_key")
            if ref and ref not in seen:
                seen.add(ref)
                todo.append((ref, "audio", None))
    except Exception:
        pass

    for i, (ref, kind, adur) in enumerate(todo[:MAX_ASSETS]):
        try:
            art = _asset_artifacts(project_id, ref, kind, adur, workdir,
                                   f"as{i}")
        except Exception as e:
            print(f"[filmstrip] asset {ref} skipped: {e}", flush=True)
            art = None
        if art:
            assets[ref] = art
    if len(todo) > MAX_ASSETS:
        print(f"[filmstrip] project {project_id}: {len(todo) - MAX_ASSETS} "
              f"asset(s) past the {MAX_ASSETS} cap got no artwork", flush=True)
    meta["assets"] = assets
    return {"available": True, "key": key, "cached": cached, **meta}
