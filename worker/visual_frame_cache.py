"""Reuse exact original-source pixels across overlapping looks and worker turns.

The cache stores JPEGs, not a claim that the editor has seen them. Callers still
deliver every requested image into the current model's context. Keys include
immutable source identity, seek time and resolution; failed seeks aren't cached.
"""
import hashlib
import json
import os
import shutil

from PIL import Image

import storage


def _valid(path):
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def frames(ctx, asset, times, width, tag, measure_motion, extract):
    # Without immutable content identity, or when a new motion measurement is
    # required, preserve the original extraction/measurement contract.
    if not asset.get("sha256") or measure_motion:
        return extract(ctx, asset, times, width, tag, measure_motion)
    requested = [round(float(t), 3) for t in times]
    paths, keys, missing = {}, {}, []
    for t in dict.fromkeys(requested):
        identity = [1, ctx.project_id, asset["storage_key"], asset["sha256"], t, int(width)]
        digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        # Project deletion already removes thumbs/<project>/ recursively.
        keys[t] = f"thumbs/{ctx.project_id}/source-frames-v1/{digest}.jpg"
        path = os.path.join(ctx.workdir, f"source_frame_{digest}.jpg")
        if not _valid(path):
            try:
                storage.download_to(keys[t], path, check_capacity=False)
            except Exception:
                pass
        if _valid(path):
            paths[t] = path
        else:
            missing.append(t)
    reused = len(requested) - len(missing)
    if reused:
        metrics = getattr(ctx, "editing_metrics", None)
        if metrics is None:
            metrics = {}
            ctx.editing_metrics = metrics
        metrics["source_frames_reused"] = metrics.get("source_frames_reused", 0) + reused
    error = None
    if missing:
        decoded, error = extract(ctx, asset, missing, width, tag, False)
        for index, decoded_path in decoded:
            t = missing[index]
            if not _valid(decoded_path):
                continue
            path = os.path.join(ctx.workdir, "source_frame_" + keys[t].rsplit("/", 1)[-1])
            # The extractor's numbered scratch paths are overwritten by later
            # looks; keep an immutable local copy even if object storage fails.
            try:
                shutil.copyfile(decoded_path, path)
            except OSError:
                paths[t] = decoded_path
                continue
            paths[t] = path
            try:
                storage.upload_file(path, keys[t], "image/jpeg")
            except Exception:
                pass
    pairs = [(i, paths[t]) for i, t in enumerate(requested) if t in paths]
    return pairs, (None if pairs else error or "no frames came back")
