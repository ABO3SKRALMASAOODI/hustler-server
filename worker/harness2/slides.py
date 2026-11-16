"""render(output='slides'): a deck of timestamped notes with the frames at
those times (HARNESS_V2.md 2.5; p5613's PowerPoint).

Times are on the original file's clock (source seconds, or an asset's own
seconds with ``source: "asset:<key>"``). The deck is written with
python-pptx; when that cannot load, the same slides are written as a PDF with
Pillow. It is stored as a project asset (kind 'render', variant 'slides') and
linked, and the model gets thumbnails of its own slides.
"""
import os
import uuid

import storage

from harness2 import envelope
from harness2 import measure as measure_mod

MAX_SLIDES = 60
MAX_FRAMES_PER_SLIDE = 4
SLIDE_W, SLIDE_H = 1600, 900
LINK_TTL_S = 7 * 24 * 3600


def _source_path(ctx, source):
    """A local media file for ``source`` ('source' or 'asset:<key>')."""
    if source and str(source).startswith("asset:"):
        import agent_tools
        key = str(source).split(":", 1)[1].strip()
        asset, _err = agent_tools._resolve_media_asset(
            ctx, key, ("video", "image", "clip"))
        if asset:
            return agent_tools._asset_local_path(ctx, asset)
        return None
    return ctx.proxy_path()


def _frames(ctx, slides, workdir, default_source):
    """{(slide index, t): jpeg path} from one decode per source."""
    import media
    wanted = {}
    for i, slide in enumerate(slides):
        source = slide.get("source") or default_source
        times = list(slide.get("frames") or [])
        if not times:
            times = [item.get("t") for item in slide.get("items") or []
                     if isinstance(item, dict)]
        for t in times[:MAX_FRAMES_PER_SLIDE]:
            try:
                t = float(t)
            except (TypeError, ValueError):
                continue
            wanted.setdefault(source, []).append(
                (round(max(0.0, t), 3),
                 os.path.join(workdir, f"s{i:03d}_{int(t * 1000):09d}.jpg"),
                 i))
    out = {}
    for source, rows in wanted.items():
        try:
            src = _source_path(ctx, source)
        except Exception:  # noqa: BLE001
            src = None
        if not src:
            continue
        try:
            written = media.frames_at(src, [(t, p) for t, p, _i in rows],
                                      width=640)
        except Exception:  # noqa: BLE001 - one at a time instead
            written = set()
            for t, p, _i in rows:
                try:
                    media.frame_at(src, t, p, width=640)
                    written.add(p)
                except Exception:  # noqa: BLE001
                    continue
        for t, p, i in rows:
            if p in written or os.path.exists(p):
                out.setdefault(i, []).append((t, p))
    return out


def _clean(slides):
    rows = []
    for s in slides if isinstance(slides, list) else []:
        if not isinstance(s, dict):
            continue
        items = []
        for it in s.get("items") or []:
            if isinstance(it, dict):
                items.append({"t": it.get("t"), "note": str(it.get("note")
                                                            or "")[:300]})
            elif isinstance(it, str):
                items.append({"t": None, "note": it[:300]})
        rows.append({"title": str(s.get("title") or "")[:120],
                     "items": items[:12], "frames": s.get("frames") or [],
                     "source": s.get("source")})
    return rows[:MAX_SLIDES]


def _bullet(item):
    t = item.get("t")
    try:
        stamp = measure_mod.clock_text(float(t)) + "  "
    except (TypeError, ValueError):
        stamp = ""
    return f"{stamp}{item.get('note') or ''}".strip()


def write_pptx(path, slides, frames):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    blank = deck.slide_layouts[6]
    for i, slide in enumerate(slides):
        page = deck.slides.add_slide(blank)
        title = page.shapes.add_textbox(Inches(0.5), Inches(0.3),
                                        Inches(12.3), Inches(0.9))
        title.text_frame.text = slide["title"]
        title.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
        title.text_frame.paragraphs[0].runs[0].font.bold = True
        body = page.shapes.add_textbox(Inches(0.5), Inches(1.3),
                                       Inches(5.6), Inches(5.8))
        tf = body.text_frame
        tf.word_wrap = True
        for k, item in enumerate(slide["items"]):
            para = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            para.text = _bullet(item)
            for run in para.runs:
                run.font.size = Pt(16)
        shots = frames.get(i) or []
        for k, (_t, path_jpg) in enumerate(shots[:MAX_FRAMES_PER_SLIDE]):
            col, row = k % 2, k // 2
            page.shapes.add_picture(path_jpg, Inches(6.4 + col * 3.4),
                                    Inches(1.3 + row * 2.9),
                                    width=Inches(3.2))
    deck.save(path)


def _slide_image(slide, shots):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), "white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 30), slide["title"], fill="black")
    y = 120
    for item in slide["items"]:
        draw.text((50, y), _bullet(item)[:90], fill="black")
        y += 36
    for k, (_t, path_jpg) in enumerate(shots[:MAX_FRAMES_PER_SLIDE]):
        try:
            frame = Image.open(path_jpg).convert("RGB")
        except OSError:
            continue
        frame.thumbnail((500, 300))
        col, row = k % 2, k // 2
        img.paste(frame, (820 + col * 380, 120 + row * 330))
    return img


def write_pdf(path, slides, frames):
    pages = [_slide_image(s, frames.get(i) or [])
             for i, s in enumerate(slides)]
    if not pages:
        raise ValueError("no slides")
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])


def thumbnails(slides, frames, workdir, limit=6):
    out = []
    for i, slide in enumerate(slides[:limit]):
        path = os.path.join(workdir, f"slide_thumb_{i + 1:02d}.jpg")
        img = _slide_image(slide, frames.get(i) or [])
        img.thumbnail((800, 450))
        img.save(path, "JPEG", quality=80)
        out.append((f"slide {i + 1}: {slide['title'][:60]}", path))
    return out


def build(ctx, slides, workdir, default_source="source"):
    """(path, kind 'pptx'|'pdf', frames, notes). Never raises on a deck
    python-pptx cannot write: the PDF is written instead."""
    os.makedirs(workdir, exist_ok=True)
    frames = _frames(ctx, slides, workdir, default_source)
    notes = []
    path = os.path.join(workdir, f"deck_{uuid.uuid4().hex[:8]}.pptx")
    try:
        write_pptx(path, slides, frames)
        return path, "pptx", frames, notes
    except Exception as exc:  # noqa: BLE001 - the PDF of the same slides
        notes.append(f"the PowerPoint writer failed ({type(exc).__name__}: "
                     f"{str(exc)[:120]}); the same slides are a PDF")
    pdf = path[:-5] + ".pdf"
    write_pdf(pdf, slides, frames)
    return pdf, "pdf", frames, notes


def _store(ctx, path, kind):
    import db as dbx
    key = (f"projects/{ctx.project_id}/slides/"
           f"{uuid.uuid4().hex[:12]}.{kind}")
    ctype = ("application/vnd.openxmlformats-officedocument."
             "presentationml.presentation" if kind == "pptx"
             else "application/pdf")
    storage.upload_file(path, key, ctype)
    asset_id = ctx.db.run(dbx.insert_asset, ctx.project_id, "render", key,
                          bytes_=os.path.getsize(path),
                          meta={"variant": "slides", "format": kind,
                                "edl_version": ctx.latest_edl()["version"],
                                "harness": "v2"})
    link = None
    try:
        link = storage.presign_get(key, expires=LINK_TTL_S)
    except Exception:  # noqa: BLE001
        link = None
    return key, asset_id, link


def render_slides(ctx, slides, times=None):
    rows = _clean(slides)
    if not rows and times:
        rows = [{"title": "Moments", "items": [{"t": t, "note": ""}
                                               for t in times][:12],
                 "frames": list(times)[:MAX_FRAMES_PER_SLIDE],
                 "source": None}]
    if not rows:
        return envelope.failed(
            "render", "slides takes slides=[{title, items: [{t, note}]}]",
            "one slide per pattern, items at the times you looked at")
    workdir = os.path.join(ctx.workdir, "slides")
    path, kind, frames, notes = build(ctx, rows, workdir)
    env = envelope.Envelope(tool="render")
    for n in notes:
        env.repair(n)
    try:
        key, asset_id, link = _store(ctx, path, kind)
    except Exception as exc:  # noqa: BLE001
        env.failed = True
        env.skip(0, f"the deck was written but not stored "
                    f"({str(exc)[:160]})", "render the slides again")
        return env
    for label, thumb in thumbnails(rows, frames, workdir):
        env.image(label, thumb)
    missing = sum(1 for i, s in enumerate(rows)
                  if (s["items"] or s["frames"]) and not frames.get(i))
    if missing:
        env.finding(f"{missing} slide(s) have no frame: their times were "
                    "outside the footage")
    env.measured = {"slides": len(rows), "format": kind, "asset_id": asset_id,
                    "frames": sum(len(v) for v in frames.values())}
    env.changed = f"deck written: {len(rows)} slide(s), {kind}"
    env.applied = 1
    env.body = (f"deck: {link or key}\n(over MCP: download_url(kind="
                "'slides'))")
    env.data = {"storage_key": key, "link": link, "format": kind}
    return env
