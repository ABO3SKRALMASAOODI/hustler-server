"""Contact sheets: PIL grids of numbered shot thumbnails with timestamps.
These are the ONLY thing the vision model ever sees during indexing — one
call per sheet, never per-frame."""

import os

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


def build_contact_sheets(shots, thumb_paths, out_dir, prefix="sheet"):
    """shots: [Shot]; thumb_paths: {shot_id: jpeg path}. Returns
    [(sheet_path, [shot_ids])]."""
    font = _font()
    sheets = []
    chunk = [s for s in shots if s.id in thumb_paths]
    for c0 in range(0, len(chunk), PER_SHEET):
        group = chunk[c0:c0 + PER_SHEET]
        rows = (len(group) + COLS - 1) // COLS
        canvas = Image.new("RGB", (COLS * TILE_W, rows * (TILE_H + LABEL_H)),
                           (12, 12, 12))
        draw = ImageDraw.Draw(canvas)
        for i, shot in enumerate(group):
            x = (i % COLS) * TILE_W
            y = (i // COLS) * (TILE_H + LABEL_H)
            try:
                img = Image.open(thumb_paths[shot.id]).convert("RGB")
                img.thumbnail((TILE_W, TILE_H))
                ox = x + (TILE_W - img.width) // 2
                oy = y + (TILE_H - img.height) // 2
                canvas.paste(img, (ox, oy))
            except Exception:
                pass
            draw.text((x + 6, y + TILE_H + 4),
                      f"#{shot.id}  {_ts(shot.start)}-{_ts(shot.end)}",
                      fill=(235, 235, 235), font=font)
        path = os.path.join(out_dir, f"{prefix}_{len(sheets) + 1}.jpg")
        canvas.save(path, "JPEG", quality=82)
        sheets.append((path, [s.id for s in group]))
    return sheets


CAPTION_PROMPT = """This is a contact sheet of shots from one video. Each tile is labeled "#<shot id>  <start>-<end>".
For EVERY tile, describe what you see. Reply with ONLY a JSON array, one object per tile:
[{"shot": <id>, "setting": "<where/background>", "people": "<who is visible, or 'none'>", "action": "<what is happening>", "on_screen_text": "<any visible text, or ''>"}]
Tiles present: %s"""


def caption_shots(sheets, shots_by_id):
    """Mutates shots in place with vision captions. Skips gracefully when the
    vision model is unset or a call fails."""
    if not llm.vision_available():
        return False
    any_ok = False
    for sheet_path, ids in sheets:
        reply = llm.ask_vision(CAPTION_PROMPT % ids, [sheet_path])
        rows = llm.extract_json_array(reply) or []
        for row in rows:
            try:
                sid = int(row.get("shot"))
            except (TypeError, ValueError):
                continue
            shot = shots_by_id.get(sid)
            if not shot:
                continue
            shot.caption = ShotCaption(
                setting=str(row.get("setting") or "")[:200],
                people=str(row.get("people") or "")[:200],
                action=str(row.get("action") or "")[:300],
                on_screen_text=str(row.get("on_screen_text") or "")[:300],
            )
            any_ok = True
    return any_ok


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
