"""Contact sheets: PIL grids of numbered frame thumbnails with timestamps.
These are the ONLY thing the vision model ever sees during indexing — one
call per sheet, never per-frame.

Round 69: the tiles are SAMPLE POINTS on a clock, not one-per-shot. See
worker/visual.py for why. Nothing about the cost shape changed — still 25
tiles to a sheet, still one vision call per sheet, still capped by
MAX_VISION_SHEETS."""

import os
import threading
from concurrent import futures

from PIL import Image, ImageDraw, ImageFont

import llm
from schemas import ShotCaption

TILE_W, TILE_H = 320, 180
LABEL_H = 24
PER_SHEET = 25          # 5x5
COLS = 5


def _font(size=14):
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
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_contact_sheets(points, frame_paths, out_dir, prefix="sheet"):
    """points: [{t, ...}] in time order; frame_paths: {point index: jpeg}.
    Returns [(sheet_path, [point indexes])].

    A tile is labelled with its own index and its SOURCE timestamp, and the
    index is what comes back from the vision model — so a tile can always be
    tied to the exact instant it was pulled from, which is the whole point of
    sampling on a clock.
    """
    font = _font()
    sheets = []
    usable = [i for i in range(len(points)) if frame_paths.get(i)]
    for c0 in range(0, len(usable), PER_SHEET):
        group = usable[c0:c0 + PER_SHEET]
        rows = (len(group) + COLS - 1) // COLS
        canvas = Image.new("RGB", (COLS * TILE_W, rows * (TILE_H + LABEL_H)),
                           (12, 12, 12))
        draw = ImageDraw.Draw(canvas)
        for slot, pi in enumerate(group):
            x = (slot % COLS) * TILE_W
            y = (slot // COLS) * (TILE_H + LABEL_H)
            try:
                img = Image.open(frame_paths[pi]).convert("RGB")
                img.thumbnail((TILE_W, TILE_H))
                ox = x + (TILE_W - img.width) // 2
                oy = y + (TILE_H - img.height) // 2
                canvas.paste(img, (ox, oy))
            except Exception:
                pass
            draw.text((x + 6, y + TILE_H + 4),
                      f"#{pi}  {_ts(points[pi]['t'])}",
                      fill=(235, 235, 235), font=font)
        path = os.path.join(out_dir, f"{prefix}_{len(sheets) + 1}.jpg")
        canvas.save(path, "JPEG", quality=82)
        sheets.append((path, group))
    return sheets


CAPTION_PROMPT = """This is a contact sheet of frames sampled from one video, in time order. Each tile is labeled "#<tile id>  <timestamp>".
For EVERY tile, describe what you see. Consecutive tiles are often near-identical — describe each one on its own terms anyway, and be specific about anything that CHANGES between them (someone enters or leaves, a graphic appears, the camera moves, the screen content changes).
Reply with ONLY a JSON array, one object per tile:
[{"tile": <id>, "setting": "<where/background>", "people": "<who is visible, or 'none'>", "action": "<what is happening>", "on_screen_text": "<any visible text, or ''>", "subtitles": <true if the tile shows SUBTITLE-STYLE captions burned into the footage (styled spoken-word text, usually lower/center third) — signs, UI, watermarks and usernames do NOT count; else false>}]
Tiles present: %s"""


def _caption_one(sheet_path, ids, recorder):
    """One sheet -> {tile index: ShotCaption}. Runs on a pool thread, so it
    installs the recorder itself: llm's recorder is THREAD-LOCAL (agent lanes
    run turns concurrently), and without this every index vision call made off
    the main thread would go unrecorded — the exact blind spot round 67 fixed
    for the sequential version."""
    if recorder is not None:
        llm.set_recorder(recorder)
    try:
        reply = llm.ask_vision(CAPTION_PROMPT % ids, [sheet_path],
                               purpose="vision_index")
        rows = llm.extract_json_array(reply) or []
    finally:
        if recorder is not None:
            llm.set_recorder(None)
    out = {}
    for row in rows:
        try:
            # "shot" accepted too: the key was called that until round 69 and
            # a model that has seen the old shape must not silently produce
            # an index with no captions in it.
            ti = int(row.get("tile", row.get("shot")))
        except (TypeError, ValueError):
            continue
        if ti not in ids:
            continue
        out[ti] = ShotCaption(
            setting=str(row.get("setting") or "")[:200],
            people=str(row.get("people") or "")[:200],
            action=str(row.get("action") or "")[:300],
            on_screen_text=str(row.get("on_screen_text") or "")[:300],
            subtitles=bool(row.get("subtitles")),
        )
    return out


def caption_points(sheets, recorder=None, parallelism=1):
    """Caption every sheet. Returns {point index: ShotCaption}.

    Sheets are independent network calls, so they overlap: going from one
    sheet to as many as twelve would otherwise add minutes of pure round-trip
    wait to an index. One sheet failing costs that sheet's captions and
    nothing else — the same degrade-don't-fail contract the sequential
    version had.
    """
    if not llm.vision_available() or not sheets:
        return {}
    n = max(1, min(int(parallelism or 1), len(sheets)))
    out, lock = {}, threading.Lock()

    def _run(job):
        sheet_path, ids = job
        try:
            got = _caption_one(sheet_path, ids, recorder)
        except Exception as e:
            print(f"[sheets] captioning failed for {os.path.basename(sheet_path)}"
                  f": {str(e)[:160]}", flush=True)
            return
        with lock:
            out.update(got)

    if n == 1:
        for job in sheets:
            _run(job)
    else:
        with futures.ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(_run, sheets))
    return out


def overlay_coord_grid(src_path, dst_path):
    """Burn a faint tenths grid + edge labels onto a copy of a frame.

    Round 72: every aimed tool (add_zoom cx/cy/rect, add_zoom_path,
    blur_region, text placement) takes 0-1 fractions of the frame, and the
    agent was reading those numbers off an unmarked picture by eye — a real
    launch-video edit aimed a zoom at (0.13, 0.48) for a message that sat at
    y=0.78, and the render framed empty UI with the subject clipped at the
    edge. The grid turns that guess into a measurement: the line labeled .8
    IS 0.8, and a coordinate read off the picture is the coordinate the tool
    receives. Drawn on the delivered COPY only — sources are never touched."""
    img = Image.open(src_path).convert("RGB")
    w, h = img.size
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for i in range(1, 10):
        a = 130 if i == 5 else 70          # the midline reads at a glance
        d.line([(round(w * i / 10), 0), (round(w * i / 10), h)],
               fill=(255, 255, 255, a), width=1)
        d.line([(0, round(h * i / 10)), (w, round(h * i / 10))],
               fill=(255, 255, 255, a), width=1)
    font = _font(max(11, round(h * 0.032)))
    for i in (2, 4, 6, 8):
        for pos, txt in (((round(w * i / 10) + 3, 2), f".{i}"),
                         ((3, round(h * i / 10) + 2), f".{i}")):
            # shadow first so the label survives any background
            d.text((pos[0] + 1, pos[1] + 1), txt, fill=(0, 0, 0, 220),
                   font=font)
            d.text(pos, txt, fill=(255, 255, 255, 235), font=font)
    Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB").save(
        dst_path, "JPEG", quality=88)
    return dst_path


def build_timestamp_sheet(frames, out_path):
    """Assemble already-extracted frames into ONE labeled sheet — the
    round-67 direct-sight look_at. frames: [(label, jpeg_path)], in order.

    Tiles are deliberately larger than the indexing sheets' (480x270 vs
    320x180): the AGENT reads this picture itself now, and it is answering
    fine-grained questions ("is the subject left or right of frame at 12s?"),
    not batch-captioning 25 shots. Two columns keep an 8-frame request under
    ~1000px wide, which image-input providers downscale gracefully."""
    tile_w, tile_h, label_h, cols = 480, 270, 30, 2
    font = _font(17)
    rows = (len(frames) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * tile_w if len(frames) > 1 else tile_w,
                               rows * (tile_h + label_h)), (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    for i, (label, fp) in enumerate(frames):
        x = (i % cols) * tile_w
        y = (i // cols) * (tile_h + label_h)
        try:
            img = Image.open(fp).convert("RGB")
            img.thumbnail((tile_w, tile_h))
            canvas.paste(img, (x + (tile_w - img.width) // 2,
                               y + (tile_h - img.height) // 2))
        except Exception:
            pass
        draw.text((x + 8, y + tile_h + 5), label,
                  fill=(235, 235, 235), font=font)
    canvas.save(out_path, "JPEG", quality=85)
    return out_path


def build_frames_sheet(video_path, out_path, times, cols=3):
    """Numbered tiles at EXPLICIT times — round 81's verify sheet.

    build_result_sheet samples the whole render evenly, which is the right
    shape for "is anything broken anywhere". This one is the other question
    — "did the thing I just changed land" — so the caller picks the moments
    (the output seconds its edit touched) and each tile is numbered so a
    claims list can address it ("tile 2 at 3.6s should show ...")."""
    import media
    font = _font()
    times = [float(t) for t in (times or [])][:9]
    if not times:
        raise ValueError("no times")
    tmp_frames = []
    for i, t in enumerate(times):
        fp = out_path + f".vframe{i}.jpg"
        try:
            media.frame_at(video_path, t, fp, width=426)
            tmp_frames.append((t, fp))
        except Exception:
            tmp_frames.append((t, None))
    cols = max(1, min(int(cols), len(tmp_frames)))
    rows = (len(tmp_frames) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * TILE_W, rows * (TILE_H + LABEL_H)),
                       (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    for i, (t, fp) in enumerate(tmp_frames):
        x = (i % cols) * TILE_W
        y = (i // cols) * (TILE_H + LABEL_H)
        if fp and os.path.exists(fp):
            try:
                img = Image.open(fp).convert("RGB")
                img.thumbnail((TILE_W, TILE_H))
                canvas.paste(img, (x + (TILE_W - img.width) // 2,
                                   y + (TILE_H - img.height) // 2))
            except Exception:
                pass
            os.unlink(fp)
        draw.text((x + 6, y + TILE_H + 4), f"{i + 1} · {_ts(t)}",
                  fill=(235, 235, 235), font=font)
    canvas.save(out_path, "JPEG", quality=85)
    return out_path


def build_result_sheet(video_path, out_path, duration, grid=3):
    """3x3 evenly-sampled sheet of a RENDER, for the agent's self-check."""
    import media
    font = _font()
    n = grid * grid
    tmp_frames = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        fp = out_path + f".frame{i}.jpg"
        try:
            media.frame_at(video_path, t, fp, width=426)
            tmp_frames.append((t, fp))
        except Exception:
            tmp_frames.append((t, None))
    canvas = Image.new("RGB", (grid * TILE_W, grid * (TILE_H + LABEL_H)),
                       (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    for i, (t, fp) in enumerate(tmp_frames):
        x = (i % grid) * TILE_W
        y = (i // grid) * (TILE_H + LABEL_H)
        if fp and os.path.exists(fp):
            try:
                img = Image.open(fp).convert("RGB")
                img.thumbnail((TILE_W, TILE_H))
                canvas.paste(img, (x + (TILE_W - img.width) // 2,
                                   y + (TILE_H - img.height) // 2))
            except Exception:
                pass
            os.unlink(fp)
        draw.text((x + 6, y + TILE_H + 4), _ts(t),
                  fill=(235, 235, 235), font=font)
    canvas.save(out_path, "JPEG", quality=82)
    return out_path
