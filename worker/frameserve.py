"""Still frames from a stored media object, extracted WHERE THE MEMORY IS.

Round 62. Job 1452, 2026-07-30 16:36 UTC: a user asked for a transition into
the laptop in their second scene, the agent called look_at_asset on the clip
they had dropped onto the timeline — a 4K HEVC .mov straight off a phone —
and the dispatcher died mid-decode. The customer read "I lost my connection
while working on that request", one hour after round 61b shipped for exactly
this class of death.

The pattern is the round-61b one and the rule is the same: an agent turn runs
on the dispatcher because it is network-bound, so any TOOL that does real
compute inside one does it on the smallest box in the fleet. Decoding one 4K
frame costs ~240 MB resident even single-threaded (media.frame_at's own
docstring, measured), the asset has no proxy — the only copy of a dropped-in
clip is the user's original — and a look samples four to six frames while a
filmstrip build may be decoding the same original beside it. The executor has
8 vCPU, 32 Gi and a fat pipe to storage; the dispatcher has none of those.

So the extraction is a `frames` job on the executor's /run, shaped exactly
like round 61's `capture`: not a queued job, no row, no id — one synchronous
call from inside the agent turn. The source object never crosses to the
dispatcher; only the jpegs come back, each a few tens of KB, via storage.

A remote FAILURE must not fall back to decoding locally (round 61b): that
reproduces the crash on the box we know is too small, on the retry, when the
user has waited longest. The local path in agent_tools survives only for the
deployment with no executor configured, where there is no other box.
"""

import os
import shutil
import uuid

import config
import media
import storage

# More than a look ever asks for (look_at_asset samples at most 6). A bound,
# not a tuning knob: the payload is model-shaped input and this is the line
# between "a look" and "a decode of the whole file one frame at a time".
MAX_FRAMES = 12


def run_frames_job(worker_db, job):
    """Executor-side runner. Returns {keys, errors, duration_s}.

    `keys[i]` is the storage key of the frame at `times[i]`, or None where
    that seek failed (`errors` says why, in frame_at's words). Per-frame
    failure is not job failure: a look that gets four of six frames is a
    look, and the caller already handles missing frames — the same contract
    the local loop in look_at_asset has always had.

    `worker_db` is unused — like a capture, a frames job writes no rows. The
    caller owns the ToolContext and any DB writes; this side only makes
    bytes.
    """
    payload = job.get("payload") or {}
    project_id = job.get("project_id")
    key = payload.get("storage_key")
    times = list(payload.get("times") or [])[:MAX_FRAMES]
    width = int(payload.get("width") or 640)
    if not key or not times:
        raise ValueError("frames job needs storage_key and times")
    workdir = os.path.join(config.TMP_DIR, f"frm_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        local = os.path.join(workdir, "src" + os.path.splitext(key)[1])
        storage.download_to(key, local)
        out_keys, errors = [], []
        for i, t in enumerate(times):
            fp = os.path.join(workdir, f"f{i}.jpg")
            try:
                media.frame_at(local, float(t), fp, width=width)
            except (media.MediaError, ValueError, TypeError) as ex:
                errors.append(f"@{float(t):.2f}s: {ex}")
                out_keys.append(None)
                continue
            k = (f"scratch/{project_id}/frames/"
                 f"{uuid.uuid4().hex[:10]}_{i}.jpg")
            storage.upload_file(fp, k, "image/jpeg")
            out_keys.append(k)
        dur = None
        try:
            dur = float(media.probe(local)["duration"])
        except Exception:
            pass
        return {"keys": out_keys, "errors": errors, "duration_s": dur}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
