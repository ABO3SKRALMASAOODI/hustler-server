"""Filmstrip tiles: the index's visual record, as PICTURES the agent reads
itself.

One tile = a 2x2 grid of four frames sampled at a fixed step (1s apart on
short footage), each frame labeled with its source timestamp. A 30-second
video becomes ~8 tiles; the whole strip IS the visual index — there is no
prose description layer any more. The agent looks at the frames and knows
the footage the way an editor does: by having seen it.

On long footage the step stretches so the strip stays a bounded number of
tiles (TILE_MAX_TILES); the agent can always look_at any exact second for
more. Frames are pulled concurrently straight from the source/proxy file —
no vision model, no captions, no per-tile network calls.
"""

import os
from concurrent import futures

from PIL import Image, ImageDraw, ImageFont

import media

# Frame geometry. 480x270 per frame -> a 960x600ish JPEG per tile: big
# enough to read UI text and faces, small enough that a whole strip rides
# in the agent's context every turn.
FRAME_W, FRAME_H = 480, 270
LABEL_H = 26
GRID = 2                      # 2x2 = 4 frames per tile
PER_TILE = GRID * GRID

# Sampling. 1 frame per second up to TILE_MAX_TILES tiles; beyond that the
# step stretches (duration / budget) so every video fits the same bound.
STEP_S = 1.0
TILE_MAX_TILES = 36           # 144 frames — ~2.4 min of footage at 1s/frame


def _font(size=15):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _ts(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    frac = int(round((t - int(t)) * 10))
    if frac >= 10:
        frac = 9
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"{base}.{frac}" if frac else base


def plan_times(duration, step_s=None, max_tiles=None):
    """The sample instants for a video of `duration` seconds.

    Returns (times, step). Frames land at step/2, 3*step/2, ... so a tile
    covers its span from the middle out and the last frame stays decodable
    (never exactly at the tail).
    """
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return [], STEP_S
    step = max(STEP_S, float(step_s or STEP_S))
    budget = max(1, int(max_tiles or TILE_MAX_TILES)) * PER_TILE
    n = int(duration / step) + 1
    if n > budget:
        step = duration / budget
        n = budget
    times = []
    t = step / 2.0
    while t < duration and len(times) < budget:
        times.append(round(t, 2))
        t += step
    if not times:
        times = [round(min(duration / 2.0, max(0.0, duration - 0.05)), 2)]
    return times, round(step, 3)


def extract_frames(src_path, times, out_dir, seek_ceiling=None,
                   parallelism=4, width=FRAME_W):
    """{time index: jpeg path} — concurrent independent ffmpeg seeks.
    A missing frame is skipped, never fatal (same contract as thumbnails)."""
    os.makedirs(out_dir, exist_ok=True)

    def _one(i):
        t = times[i]
        if seek_ceiling is not None:
            t = min(t, seek_ceiling)
        fp = os.path.join(out_dir, f"tf_{i:05d}.jpg")
        try:
            media.frame_at(src_path, t, fp, width=width)
            return i, fp
        except Exception:
            return i, None

    out = {}
    if not times:
        return out
    with futures.ThreadPoolExecutor(
            max_workers=max(1, min(parallelism, len(times)))) as pool:
        for i, fp in pool.map(_one, range(len(times))):
            if fp:
                out[i] = fp
    return out


def build_tiles(times, frame_paths, out_dir, prefix="tile"):
    """Assemble frames into labeled 2x2 tiles.

    Returns [(tile_path, t_start, t_end)] in time order. Every frame's own
    timestamp is burned under it, so a tile is self-describing wherever it
    is later shown.
    """
    os.makedirs(out_dir, exist_ok=True)
    font = _font()
    usable = [i for i in range(len(times)) if frame_paths.get(i)]
    tiles = []
    for c0 in range(0, len(usable), PER_TILE):
        group = usable[c0:c0 + PER_TILE]
        rows = (len(group) + GRID - 1) // GRID
        canvas = Image.new("RGB",
                           (GRID * FRAME_W, rows * (FRAME_H + LABEL_H)),
                           (10, 10, 10))
        draw = ImageDraw.Draw(canvas)
        for slot, fi in enumerate(group):
            x = (slot % GRID) * FRAME_W
            y = (slot // GRID) * (FRAME_H + LABEL_H)
            try:
                img = Image.open(frame_paths[fi]).convert("RGB")
                img.thumbnail((FRAME_W, FRAME_H))
                canvas.paste(img, (x + (FRAME_W - img.width) // 2,
                                   y + (FRAME_H - img.height) // 2))
            except Exception:
                pass
            draw.text((x + 8, y + FRAME_H + 5), _ts(times[fi]),
                      fill=(235, 235, 235), font=font)
        path = os.path.join(out_dir, f"{prefix}_{len(tiles) + 1:03d}.jpg")
        canvas.save(path, "JPEG", quality=82)
        tiles.append((path, times[group[0]], times[group[-1]]))
    return tiles


def build_for_video(src_path, duration, out_dir, seek_ceiling=None,
                    parallelism=4, step_s=None, max_tiles=None):
    """The whole strip for one video file. Returns (tiles, step) where
    tiles = [(path, t_start, t_end)]."""
    built, step, _times, _frames = build_for_video_with_frames(
        src_path, duration, out_dir, seek_ceiling=seek_ceiling,
        parallelism=parallelism, step_s=step_s, max_tiles=max_tiles)
    return built, step


def build_for_video_with_frames(src_path, duration, out_dir,
                                seek_ceiling=None, parallelism=4,
                                step_s=None, max_tiles=None):
    """Tile build plus its already-decoded frames for structured perception.

    Keeping this as a second API preserves every existing caller while the
    indexer avoids decoding the same 144 instants twice for filmstrip and
    face/text tracks.
    """
    times, step = plan_times(duration, step_s=step_s, max_tiles=max_tiles)
    frames = extract_frames(src_path, times, out_dir,
                            seek_ceiling=seek_ceiling,
                            parallelism=parallelism)
    return build_tiles(times, frames, out_dir), step, times, frames
