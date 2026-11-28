"""Reference-guided edits of a moving programme window on the configured FAL route."""
import base64
import hashlib
import json
import math
import os
import tempfile

import config
import media
import videogen


def rate():
    try:
        price = float(config.VIDEO_RESTYLE_USD_PER_SECOND)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price >= 0 else None


def available():
    return bool(videogen.video_gen_available() and config.VIDEO_RESTYLE_MODEL and rate() is not None)


def source_window(ctx, edl, timeline, start, end, workdir):
    import renderer
    path = ctx.proxy_path()
    info = media.probe(path)
    fps = min(60.0, max(24.0, float(info.get("fps") or 30)))
    base = {key: edl[key] for key in ("keep", "speed", "inserts", "frame", "canvas") if edl.get(key)}
    # The delivery overlays this picture behind the programme's existing titles.
    window = renderer._stitch_window(base, timeline, start, end,
                                     (info["width"], info["height"], fps))
    source = os.path.join(workdir, "programme.mp4")
    renderer.render_edl(window, ctx.index, path, source, workdir, preview=True,
                        want_wm=False, suppress_outro=True, render_fragment=True,
                        discard_audio=True, cap_ass_override="")
    seconds = max(end - start, 3.0)
    encoded = os.path.join(workdir, "reference.mp4")
    media.run(["ffmpeg", "-y", "-i", source, "-an", "-vf",
               f"fps={fps},scale='if(gte(iw,ih),-2,720)':'if(gte(iw,ih),720,-2)',"
               "tpad=stop_mode=clone:stop_duration=3,setsar=1", "-t", f"{seconds:.6f}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "2", encoded])
    return encoded, fps, seconds


def submit(path, image, instruction, seconds, out, submit_once):
    with open(path, "rb") as fp:
        video = "data:video/mp4;base64," + base64.b64encode(fp.read()).decode("ascii")
    model = config.VIDEO_RESTYLE_MODEL
    body = {"video_url": video, "prompt": instruction}
    if "/wan/" in model:
        body["reference_image_url"] = image
    else:
        body.update(prompt=f"Edit @Video1 using @Image1 as the appearance reference. {instruction}",
                    image_urls=[image], keep_audio=False)
    return videogen.run_queue(model, body, seconds, out, submit=submit_once)


def _reserve(conn, receipt):
    """One concurrent submitter. Poll an acknowledged submission on retry."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO app_kv(key,value) VALUES (%s,%s) "
                    "ON CONFLICT (key) DO NOTHING RETURNING key", (receipt, "{}"))
        if cur.fetchone():
            return True, {}
        cur.execute("SELECT value FROM app_kv WHERE key=%s", (receipt,))
        row = cur.fetchone()
        if row is not None:
            return False, json.loads(row["value"])
    # A definitive provider error released the prior attempt during the read.
    return _reserve(conn, receipt)


def _release(conn, receipt):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app_kv WHERE key=%s", (receipt,))


def _submit_once(ctx, key, model, body):
    import db
    receipt = "video-restyle:" + key
    fresh, state = ctx.db.run(_reserve, receipt)
    if not fresh:
        if state.get("request_id") or state.get("status_url"):
            return state, None
        return None, "The provider has not confirmed the previous submission yet."
    sub, why = videogen.submit_request(model, body)
    if why:
        status = getattr(why, "status_code", None)
        if status == 0 or status in (400, 401, 403, 404, 413, 422, 429):
            ctx.db.run(_release, receipt)
        return None, why
    saved = {k: sub[k] for k in ("request_id", "status_url", "response_url") if sub.get(k)}
    ctx.db.run(db.kv_put, receipt, json.dumps(saved))
    return sub, None


def _charge_once(conn, project, job, key, model, prompt, cost):
    """Billing and its durable receipt commit together even if storing the clip fails."""
    import db
    receipt = "video-restyle:" + key
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key=%s FOR UPDATE", (receipt,))
        state = json.loads(cur.fetchone()["value"])
    if state.get("charged"):
        return False
    db.insert_llm_call(conn, project, job, "video_gen", model,
                       {"model": model, "prompt": prompt[:500]}, {"storage_key": key, "cost_usd": cost}, None, None)
    db.kv_put(conn, receipt, json.dumps(dict(state, charged=True)))
    return True


def _save_asset(conn, project, key, clip_bytes, duration, model, start, end):
    import db
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM app_kv WHERE key=%s FOR UPDATE", ("video-restyle:" + key,))
        if db.asset_by_key(conn, project, key):
            return
        db.insert_asset(conn, project, "video_clip", key, bytes_=clip_bytes, duration_s=duration,
                        meta={"filename": "restyled-shot.mp4", "model": model,
                              "generated": True, "source_window": [start, end]})


def restyle(ctx, instruction, reference_asset_key, start, end):
    import agent_tools as tools
    import db
    import storage
    from timeline import Timeline

    if not available():
        return ("Use generate_video with the reference image and add_overlay over the original "
                "window to keep its speech and duration. This makes a new animation.")
    image, error = tools._resolve_media_asset(ctx, reference_asset_key, ("image_ref",))
    if error:
        return error
    instruction = str(instruction or "").strip()
    edl = ctx.latest_edl()["json"]
    timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [], edl.get("speed"))
    a, b = max(0.0, float(start)), min(float(end), timeline.out_duration)
    if not instruction or b <= a:
        return "Supply a visual change and a window with moving footage on the programme."
    ref = videogen.reference_image(tools._asset_local_path(ctx, image))
    count = max(1, math.ceil((b - a) / 10.0))
    model, price = config.VIDEO_RESTYLE_MODEL, rate()
    results = []
    for n in range(count):
        left, right = a + (b - a) * n / count, a + (b - a) * (n + 1) / count
        with tempfile.TemporaryDirectory(prefix="restyle_", dir=ctx.workdir) as work:
            source, fps, billed = source_window(ctx, edl, timeline, left, right, work)
            digest = hashlib.sha256()
            for value in (model, ref, instruction, media.sha256_file(source)):
                digest.update(value.encode())
                digest.update(b"\0")
            key = f"generated_video/{ctx.project_id}/restyle-{digest.hexdigest()}.mp4"
            existing = ctx.db.run(db.asset_by_key, ctx.project_id, key)
            if not existing:
                cost = round(billed * price, 6)
                receipt = ctx.db.run(db.kv_get, "video-restyle:" + key)
                if receipt and json.loads(receipt).get("charged") and storage.exists(key):
                    recovered = os.path.join(work, "previous.mp4")
                    storage.download_to(key, recovered)
                    ctx.db.run(_save_asset, ctx.project_id, key, os.path.getsize(recovered),
                               media.duration_of(recovered), model, left, right)
                    existing = ctx.db.run(db.asset_by_key, ctx.project_id, key)
            if not existing:
                if receipt is None:
                    over = tools._gen_budget_reject(ctx, cost, "restyle this video window")
                    if over:
                        return "\n".join([*results, over])
                generated = os.path.join(work, "returned.mp4")
                ok, why, seconds = submit(source, ref, instruction, billed, generated,
                                          lambda m, body: _submit_once(ctx, key, m, body))
                if not ok:
                    if isinstance(why, videogen.TerminalVideoFailure):
                        ctx.db.run(_release, "video-restyle:" + key)
                    return "\n".join([*results, f"Video restyle for {left:g}–{right:g}s: {why}. "
                                      "The reference can also be animated with generate_video and placed over this window."])
                cost = round(seconds * price, 6)
                if ctx.db.run(_charge_once, ctx.project_id, ctx.job["id"], key, model, instruction, cost):
                    tools.llm.charge_generation("video_gen", model, cost)
                    ctx.gen_extra_cost_usd += cost
                clip = os.path.join(work, "placed.mp4")
                returned = float(media.probe(generated)["duration"])
                # The input may include a short held tail for the provider. Remove
                # that tail and conform the returned frames to the programme clock.
                media.run(["ffmpeg", "-y", "-i", generated, "-an", "-vf",
                           f"setpts=(PTS-STARTPTS)*{billed / returned:.9f},fps={fps},"
                           "tpad=stop_mode=clone:stop_duration=1", "-t", f"{right - left:.6f}",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "2", clip])
                storage.upload_file(clip, key, "video/mp4")
                ctx.db.run(_save_asset, ctx.project_id, key, os.path.getsize(clip),
                           media.duration_of(clip), model, left, right)
                ctx.videos_generated.append({"storage_key": key, "prompt": instruction[:200],
                                             "seconds": seconds})
            placed = next((o for o in ctx.latest_edl()["json"].get("overlays") or []
                           if o.get("asset_key") == key and abs(float(o["start"]) - left) < .02
                           and abs(float(o["duration_s"]) - (right - left)) < .05), None)
            if placed:
                results.append(f"{placed['id']} carries the restyle at {left:g}–{right:g}s.")
            else:
                results.append(tools.add_overlay(ctx, key, left, duration_s=right - left, fit="cover"))
    return "\n".join(results)
