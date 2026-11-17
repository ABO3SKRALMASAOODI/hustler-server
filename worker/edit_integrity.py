"""Measurements at the write boundary, shared by every editor and batch tool.

The editing model interprets the request and decides what to cut. Face intersection is a measurement:
it is returned with the saved write as information, never a refusal (owner, 16 Nov 2026). A face blur
or a sticker over a face is a legitimate request; the editor reads where the face is and decides.
"""
import os


def removed_spans(before, after):
    spans = [(float(a), float(b)) for a, b in before or []]
    for c, d in after or []:
        spans = [(x, y) for a, b in spans for x, y in
                 ((a, min(b, float(c))), (max(a, float(d)), b)) if y > x]
    return [(a, b) for a, b in spans if b - a > .08]


def _intersects(rect, face):
    return min(rect[2], face[2]) > max(rect[0], face[0]) and min(rect[3], face[3]) > max(rect[1], face[1])


def patch_face_note(ctx, regions):
    """Where a repaint meets a measured face, as information for the editor (a repaint smears an
    eye it was not meant for; a face blur is meant for it). Detected on the untouched source, not on
    a patched render where the damaged face may no longer be detectable. "" when no face is met."""
    if not regions:
        return ""
    import media
    import spatial
    samples = list(((getattr(ctx, "index", None) or {}).get("spatial") or {}).get("samples") or [])
    duration = float(getattr(ctx, "duration", 0) or 0)
    # Sparse/old indexes are not permission. Sample each proposed window again.
    if hasattr(ctx, "proxy_path"):
        root = os.path.join(ctx.workdir, "patch_faces")
        os.makedirs(root, exist_ok=True)
        try:
            source = ctx.proxy_path()
            for reg in regions:
                a = float(reg.get("start") or 0)
                b = float(reg.get("end") if reg.get("end") is not None else duration)
                for t in (a + (b-a)*.1, (a+b)/2, a+(b-a)*.9):
                    path = os.path.join(root, f"{t:.3f}.jpg")
                    media.frame_at(source, t, path, width=640)
                    samples.append(dict(spatial.analyze_frame(path) or {}, t=t))
        except Exception as exc:
            print(f"[edit-integrity] face measurement unavailable: {type(exc).__name__}", flush=True)
            # The repaint saves; the faces already in the index are still checked below.
    for reg in regions:
        a, b = float(reg.get("start") or 0), float(reg.get("end") if reg.get("end") is not None else duration)
        rect = (float(reg["x"]), float(reg["y"]), float(reg["x"])+float(reg["w"]), float(reg["y"])+float(reg["h"]))
        for sample in samples:
            if a <= float(sample.get("t", -1)) <= b:
                for face in sample.get("faces") or []:
                    if len(face) == 4 and _intersects(rect, face):
                        return (f"FACE MEASURED (information; the repaint is saved): the repaint at "
                                f"x {rect[0]:.2f}-{rect[2]:.2f}, y {rect[1]:.2f}-{rect[3]:.2f} meets a face at "
                                f"x {face[0]:.2f}-{face[2]:.2f}, y {face[1]:.2f}-{face[3]:.2f} (source "
                                f"{sample['t']:.2f}s). A repaint smears features it covers: when the face is "
                                "not what should go, move the region off that box.")
    return ""


def write_note(ctx, before, after):
    """Measured facts about faces the write's new repaints and local layers meet: information the
    saved write carries ("" when none)."""
    old = {str(p.get("id")): p for p in before.get("patches") or []}
    regions = []
    for patch in after.get("patches") or []:
        if patch != old.get(str(patch.get("id"))):
            regions += [dict(r, start=patch["src_start"], end=patch["src_end"])
                        for r in patch.get("regions") or []]
    old_clean = (before.get("source_clean") or {}).get("regions") or []
    regions += [r for r in (after.get("source_clean") or {}).get("regions") or [] if r not in old_clean]
    notes = [patch_face_note(ctx, regions), face_cover_note(ctx, before, after)]
    # 16 Nov (owner): no second model vetoes the editor's cuts and no measurement refuses a write.
    return "\n".join(n for n in notes if n)


def face_cover_note(ctx, before, after):
    """Where a local layer covers a measured face, as information ("" when none). Full-frame cutaways
    replace a shot intentionally; local patches/rectangles cannot pretend to be
    a background-only operation. Geometry is checked on the output clock."""
    from score import asks
    index = getattr(ctx, "index", None) or {}
    samples = _output_faces(after, index)
    if not samples:
        return ""
    candidates = []
    prior = {str(it.get("id")): it for it in before.get("fx") or []}
    for it in after.get("fx") or []:
        if it == prior.get(str(it.get("id"))) or it.get("type") != "region":
            continue
        reg = it.get("region") or {}
        box = reg.get("box")
        if reg.get("shape") == "rect" and isinstance(box, list) and len(box) == 4:
            # A global grade/flash is not a local covering patch.
            if (box[2]-box[0]) * (box[3]-box[1]) < .95:
                candidates.append((it['id'], float(it.get('start') or 0), float(it.get('end') or 0), box))
    prior = {str(it.get("id")): it for it in before.get("overlays") or []}
    assets = None
    for it in after.get("overlays") or []:
        if it == prior.get(str(it.get("id"))) or it.get("fit") in ("cover", "picture"):
            continue
        if not all(isinstance(it.get(k, v), (int, float)) for k, v in (("x",.5),("y",.5),("scale",.4))):
            continue  # animated overlays are checked on rendered frames
        if assets is None:
            assets = {a.get('storage_key'): a for a in asks.project_assets(ctx)}
        asset = assets.get(it.get('asset_key')) or {}
        width, height = asset.get('width'), asset.get('height')
        if not width or not height:
            continue
        ow, oh = asks.output_size(after, index)
        w = float(it.get('scale', .4))
        h = w * ow / oh * float(height) / float(width)
        x, y = float(it.get('x', .5)), float(it.get('y', .5))
        # A centered, contained original-video shot replaces the picture
        # just as a cover cutaway does. It is not a local face-cover patch.
        # Require actual original asset identity and full-frame geometry;
        # a small picture-in-picture or a cropped source still gets checked.
        if (asset.get("kind") == "original" and it.get("kind") == "video"
                and not it.get("source_crop") and abs(x - .5) < 1e-6
                and abs(y - .5) < 1e-6 and w <= 1.001 and h <= 1.001
                and max(w, h) >= .999):
            continue
        candidates.append((it['id'], float(it.get('start') or 0),
                           float(it.get('start') or 0)+float(it.get('duration_s') or 0),
                           [x-w/2,y-h/2,x+w/2,y+h/2]))
    # A designed split replaces the source with an opaque backdrop, then seats
    # its windows on that backdrop. The source face is no longer on screen.
    # Only measured opaque generated images qualify: a transparent sticker or
    # a dimming layer must still be checked against the source face.
    if assets is None:
        assets = {a.get('storage_key'): a for a in asks.project_assets(ctx)} if after.get('overlays') else {}
    backdrops = []
    for it in after.get("overlays") or []:
        meta = (assets.get(it.get('asset_key')) or {}).get('meta') or {}
        if (it.get('fit') == 'cover' and meta.get('made_from') == 'design' and meta.get('pixel_opaque') is True
                and it.get('opacity') in (None, 1, 1.0) and not it.get('entrance') and not it.get('exit')
                and not it.get('motion_motif') and not it.get('rotation') and not it.get('screen')):
            a = float(it.get('start') or 0)
            backdrops.append((a, a + float(it.get('duration_s') or 0)))
    for ident, a, b, rect in candidates:
        for t, heads in samples:
            if any(start <= t < end for start, end in backdrops):
                continue
            if a <= t < b:
                for head in heads:
                    if _intersects(rect, head):
                        return (f"FACE MEASURED (information; the layer is saved): local layer {ident} covers "
                                f"a face at {t:.2f}s on the film (face x {head[0]:.2f}-{head[2]:.2f}, y "
                                f"{head[1]:.2f}-{head[3]:.2f}). When the face should stay visible, move the "
                                "layer off that box or use a measured subject mask for background work.")
    return ""


def _output_faces(edl, index):
    """Use the renderer's crop/pad mapping; source coordinates are not screen coordinates."""
    from types import SimpleNamespace
    from score import asks
    import matte_track
    from timeline import Timeline
    video = index.get("video") or {}
    if not video.get("width") or not video.get("height"):
        return asks.face_samples(edl, index)
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [], edl.get("speed") or [])
    width, height = asks.output_size(edl, index)
    source = SimpleNamespace(width=video["width"], height=video["height"])
    out = []
    for sample in (index.get("spatial") or {}).get("samples") or []:
        t = float(sample.get("t", -1))
        at = tl.src_to_out(t)
        if at is None:
            continue
        heads = []
        for face in sample.get("faces") or []:
            if len(face) != 4:
                continue
            mapped = matte_track.map_box(edl, tl, t, asks.head_box(face), width, height, source)
            if mapped:
                heads.append(mapped)
        if heads:
            out.append((at, heads))
    return out
