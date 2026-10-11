"""Executor-side sidecar analysis: the runner behind the ``sidecar`` job kind.

Wave 0 track 0A (worker/devices/INTERFACES.md §2, ROADMAP A1). One job kind
measures every sidecar kind; the payload names the kind, the source, the
chunks and the model the CALLER keyed them with:

    {kind, source: {SourceRef}, chunks: [{key, t0, t1}], params,
     model_id, model_version, format, attempt, slot, priority}

WHAT IT GUARANTEES
  * The version handshake (the matte job's, generalised): a payload keyed
    for another model id, version or data format is refused before any
    work, so this lane can never write a manifest the caller's code would
    misread. Every chunk key is recomputed here and must match.
  * Manifest LAST: data first, then the manifest, whose presence means
    ready. A chunk this lane finishes is usable even if a later chunk of the
    same call dies.
  * The model runs in a CHILD process. An OOM-killed or crashed child costs
    only its unfinished chunks (reported retryable; the launcher retries
    them once on another shard), never the executor.
  * A model file is used only if its sha256 matches the pin in
    models/<name>.json; anything else reads as model_missing.

KINDS
  matte.person  RVM (rvm_mobilenetv3_fp32) on the proxy, <=960 px wide, at
                min(source fps, 30). Recurrent state resets at each chunk; a
                chunk that starts inside a shot first runs a <=1 s warm-up
                that is discarded. Raw soft alpha, gray H.264 CRF 20. QA per
                chunk: edge-band flicker, edge chatter, coverage curve. RVM
                absent -> u2net per frame under its OWN key (the fallback
                rung), the RVM key marked failed with a pointer to it.
  faces.yunet   YuNet (cv2.FaceDetectorYN) at 320 px wide, 10 fps: boxes and
                5 landmarks as frame fractions, with scores.

Production runs this on the batch lane (the vision lane is dark).
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import sidecars

CHILD_RUNNERS = ("matte.person", "faces.yunet")
QA_BAND_PX = 6                 # matte_flicker edge band (ROADMAP §4.5)
CHATTER_TOGGLES_PER_S = 6.0    # edge_chatter definition (ROADMAP §4.5)
U2NET_ID = "u2net_human_seg"
_HERE = os.path.dirname(os.path.abspath(__file__))


# ── models ───────────────────────────────────────────────────────────────

_VERIFIED = {}


def model_path(name):
    import config
    return {"rvm_mobilenetv3": config.MATTE_RVM_MODEL,
            "yunet": config.YUNET_MODEL}.get(name)


def _file_sha(path):
    st = os.stat(path)
    tag = (path, st.st_size, int(st.st_mtime))
    if tag not in _VERIFIED:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _VERIFIED[tag] = h.hexdigest()
    return _VERIFIED[tag]


def model_ready(name):
    """(path | None, why). The pinned file, present and byte-identical."""
    info = sidecars.model_info(name)
    path = model_path(name)
    if not path or not os.path.exists(path):
        return None, f"{info['file']} is not on this lane"
    try:
        if os.path.getsize(path) != int(info["bytes"]) \
                or _file_sha(path) != info["sha256"]:
            return None, (f"{info['file']} on this lane does not match its "
                          "models.lock pin")
    except OSError as e:
        return None, f"{info['file']} unreadable: {e}"
    return path, None


def u2net_version():
    """The u2net fallback's version: its own content hash (the file is not
    pinned by a §7 official ONNX source, so the bytes ARE the version)."""
    try:
        import personseg
        if not personseg.available():
            return None
        return "sha256:" + _file_sha(personseg.model_path())[:16]
    except Exception:  # noqa: BLE001
        return None


# ── the job ──────────────────────────────────────────────────────────────

def _failed(reason, retryable, why=None, **extra):
    out = {"status": "failed", "reason": reason, "retryable": retryable}
    if why:
        out["why"] = str(why)[:300]
    out.update(extra)
    return out


def _warmups(source, chunks, warmup_s):
    """Seconds of discarded warm-up before each chunk: only inside a shot
    (never across a cut, where the state must reset) and never before 0."""
    starts = sorted({0.0} | {float(s) for s in source.shots or ()})
    out = {}
    for c in chunks:
        prev = max((s for s in starts if s <= c.t0 + 1e-3), default=0.0)
        out[c.key] = 0.0 if abs(prev - c.t0) <= 1e-3 else round(
            min(float(warmup_s), c.t0 - prev), 3)
    return out


def run_sidecar_job(worker_db, job):
    """Executor runner (synchronous, no row; ``worker_db`` unused) ->
    {ok, kind, chunks: {key: {status, reason?, retryable?, ...}}, timings}."""
    import faultinject
    t_start = time.monotonic()
    payload = job.get("payload") or {}
    kind = payload.get("kind")
    raw_chunks = payload.get("chunks") or []
    if kind not in CHILD_RUNNERS or not raw_chunks:
        raise ValueError("sidecar job needs a known kind and chunks")
    sp = sidecars.spec(kind)
    want = (payload.get("model_id"), str(payload.get("model_version")),
            payload.get("format"))
    have = (sp["model_id"], sp["model_version"], sp["format"])
    if want != have or faultinject.fault("executor_skew"):
        why = (f"this lane measures {kind} as {have} but the caller keyed "
               f"{want} — the lanes are mid-deploy")
        return {"ok": False, "skew": True, "kind": kind, "why": why,
                "chunks": {c.get("key"): _failed("executor_skew", False, why)
                           for c in raw_chunks}}
    store = sidecars._store()
    if store is None:
        raise RuntimeError("no sidecar store is configured on this lane")
    src = payload.get("source") or {}
    source = sidecars.SourceRef(
        sha256=str(src["sha256"]), storage_key=str(src["storage_key"]),
        duration_s=float(src["duration_s"]), fps=float(src["fps"]),
        width=int(src["width"]), height=int(src["height"]),
        project_id=src.get("project_id"),
        shots=tuple(float(s) for s in src.get("shots") or ()))
    params = sidecars.resolve_params(kind, payload.get("params"))
    attempt = int(payload.get("attempt") or 1)
    results, todo = {}, []
    listing = sidecars._classify(
        store.list(sidecars.kind_dir(kind, source.sha256)),
        sidecars.kind_dir(kind, source.sha256))
    for c in raw_chunks:
        span = (float(c["t0"]), float(c["t1"]))
        key = sidecars.sidecar_key(kind, source.sha256, sp["model_id"],
                                   sp["model_version"], params, span)
        if key != c.get("key"):
            results[c.get("key")] = _failed(
                "bad_key", False, "chunk key does not match its range/params")
            continue
        if "manifest" in (listing.get(key) or {}):
            m = sidecars._read_json(
                store, sidecars.manifest_key(kind, source.sha256, key), 3600)
            if sidecars.valid_manifest(m, kind, key):
                results[key] = {"status": "ready", "cached": True}
                continue
        todo.append(sidecars.ChunkRef(key, *span))
    if not todo:
        return {"ok": True, "kind": kind, "chunks": results,
                "timings": {"total_s": round(time.monotonic() - t_start, 2)}}

    engine = _engine(kind, faultinject)
    if engine is None:
        why = f"no {kind} model on this lane"
        for c in todo:
            results[c.key] = _mark_failed(store, kind, source, c,
                                          "model_missing", False, why,
                                          attempt)
        return {"ok": False, "kind": kind, "chunks": results, "why": why}

    workdir = os.path.join(_tmp_dir(), f"sidecar_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    timings = {}
    try:
        src_path, how = _stage_source(store, source, workdir)
        timings["source"] = how
        warm = _warmups(source, todo, params.get("warmup_s", 0.0)) \
            if kind == "matte.person" else {c.key: 0.0 for c in todo}
        out = _run_child(kind, engine, source, params, todo, warm, src_path,
                         workdir, store, attempt, payload, faultinject)
        if how == "stream" and out and all(
                r.get("reason") == "decode" for r in out.values()
                if r.get("status") != "ready") and any(
                r.get("status") != "ready" for r in out.values()):
            # The stream read nothing: stage the proxy once and re-run.
            src_path, how = _stage_source(store, source, workdir, stage=True)
            timings["source"] = how
            again = [c for c in todo if out[c.key].get("status") != "ready"]
            out.update(_run_child(kind, engine, source, params, again, warm,
                                  src_path, workdir, store, attempt, payload,
                                  faultinject))
        results.update(out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    timings["total_s"] = round(time.monotonic() - t_start, 2)
    ok = all(r.get("status") == "ready" for r in results.values())
    return {"ok": ok, "kind": kind, "chunks": results, "timings": timings,
            "engine": engine["name"]}


def _tmp_dir():
    try:
        import config
        return config.TMP_DIR
    except Exception:  # noqa: BLE001
        return "/tmp/valmera"


def _engine(kind, faultinject):
    """{name, model_id, model_version, path} for this lane, or None."""
    import config
    missing = faultinject.fault(f"model_missing:{kind}")
    sp = sidecars.spec(kind)
    name = sp["model"]
    path, _why = (None, "injected") if missing else model_ready(name)
    if path:
        return {"name": name, "model_id": sp["model_id"],
                "model_version": sp["model_version"], "path": path,
                "threads": config.SIDECAR_ORT_THREADS, "fallback": False}
    if kind == "matte.person" and not missing:
        ver = u2net_version()
        if ver:
            import personseg
            return {"name": "u2net", "model_id": U2NET_ID,
                    "model_version": ver, "path": personseg.model_path(),
                    "threads": config.SIDECAR_ORT_THREADS, "fallback": True}
    return None


def _stage_source(store, source, workdir, stage=False):
    """(input for ffmpeg, how): a local dev path, a container-cached proxy,
    a presigned range-read stream, or a staged download."""
    key = source.storage_key
    if isinstance(store, sidecars.LocalStore):
        if os.path.isabs(key) and os.path.exists(key):
            return key, "local"
        local = os.path.join(workdir, "source" + os.path.splitext(key)[1])
        if store.get_file(key, local):
            return local, "local-store"
        raise FileNotFoundError(f"source {key} is not in the local store")
    import media_cache
    import storage
    if not stage:
        local = media_cache.resident(key, workdir, "proxy.mp4")
        if local:
            return local, "cached"
        try:
            return storage.presign_get(key, expires=3600), "stream"
        except Exception:  # noqa: BLE001 — stage instead
            pass
    local = media_cache.lease(key, workdir, "proxy.mp4")
    if local:
        return local, "staged-cache"
    local = os.path.join(workdir, "proxy.mp4")
    storage.download_to(key, local)
    return local, "staged"


def _mark_failed(store, kind, source, chunk, reason, retryable, why, attempt,
                 fallback=None):
    try:
        store.put_bytes(
            sidecars.failed_key(kind, source.sha256, chunk.key),
            json.dumps(sidecars.failed_marker(
                reason, attempt if not retryable else sidecars.MAX_ATTEMPTS,
                retryable=retryable, why=why, fallback=fallback)
            ).encode("utf-8"))
        store.delete(sidecars.pending_key(kind, source.sha256, chunk.key))
        marked = True
    except Exception:  # noqa: BLE001 — the launcher writes it instead
        marked = False
    return _failed(reason, retryable, why, marked=marked,
                   **({"fallback": fallback} if fallback else {}))


def child_command(spec_path):
    """The child process that measures a group (tests substitute a child
    with a stand-in model; production runs this module)."""
    return [sys.executable, os.path.join(_HERE, "analysis_jobs.py"),
            "--child", spec_path]


def _run_child(kind, engine, source, params, chunks, warm, src_path, workdir,
               store, attempt, payload, faultinject):
    """Measure ``chunks`` in a child process, publishing each finished chunk
    (data, then manifest) as soon as the child reports it."""
    import config
    sp = sidecars.spec(kind)
    run_id = uuid.uuid4().hex[:8]
    results_path = os.path.join(workdir, f"results_{run_id}.jsonl")
    keyed = []
    for c in chunks:
        if engine["fallback"]:
            fkey = sidecars.sidecar_key(kind, source.sha256,
                                        engine["model_id"],
                                        engine["model_version"], params,
                                        (c.t0, c.t1))
        else:
            fkey = c.key
        keyed.append({"key": c.key, "out_key": fkey, "t0": c.t0, "t1": c.t1,
                      "warmup": warm.get(c.key, 0.0)})
    child_spec = {"kind": kind, "engine": engine, "params": params,
                  "source": {"fps": source.fps, "width": source.width,
                             "height": source.height,
                             "duration_s": source.duration_s},
                  "src": src_path, "chunks": keyed, "outdir": workdir,
                  "results": results_path}
    spec_path = os.path.join(workdir, f"spec_{run_id}.json")
    with open(spec_path, "w") as f:
        json.dump(child_spec, f)
    budget = float(payload.get("budget_s") or
                   max(60.0, config.executor_timeout_for("sidecar") - 60.0))
    deadline = time.monotonic() + budget
    err_path = os.path.join(workdir, f"child_{run_id}.err")
    with open(err_path, "wb") as err_file:
        proc = subprocess.Popen(child_command(spec_path), cwd=_HERE,
                                stdout=subprocess.DEVNULL, stderr=err_file)
    if faultinject.fault("oom"):
        # The same path as the kernel's OOM killer: SIGKILL, no goodbye.
        proc.kill()
    out, seen, offset = {}, set(), 0
    timed_out = False

    def drain():
        nonlocal offset
        try:
            with open(results_path) as f:
                f.seek(offset)
                while True:
                    line = f.readline()
                    if not line or not line.endswith("\n"):
                        break
                    offset = f.tell()
                    row = json.loads(line)
                    if row["key"] in seen:
                        continue
                    seen.add(row["key"])
                    out[row["key"]] = _publish(store, kind, sp, source,
                                               params, row, engine, attempt)
        except FileNotFoundError:
            pass

    while proc.poll() is None:
        drain()
        if time.monotonic() > deadline:
            proc.kill()
            timed_out = True
            break
        time.sleep(0.2)
    proc.wait()
    drain()
    try:
        with open(err_path, "rb") as f:
            err = f.read().decode("utf-8", "replace")[-400:]
    except OSError:
        err = ""
    rc = proc.returncode
    if rc != 0 or len(out) < len(chunks):
        reason = ("timeout" if timed_out else "oom" if rc in (-9, 137)
                  else "crash")
        why = (f"sidecar child {'exceeded its budget' if timed_out else f'exited {rc}'}"
               + (f": {err.strip()[-200:]}" if err.strip() else ""))
        for c in chunks:
            if c.key not in out:
                out[c.key] = _failed(reason, True, why)
    return out


def _publish(store, kind, sp, source, params, row, engine, attempt):
    """Upload one finished chunk: data, then the manifest (LAST)."""
    key, out_key = row["key"], row.get("out_key") or row["key"]
    if not row.get("ok"):
        reason = row.get("reason") or "error"
        return _failed(reason, reason != "bad_input", row.get("why"))
    meta = row["meta"]
    ext = sp["ext"]
    path = row["path"]
    try:
        size = os.path.getsize(path)
        store.put_file(sidecars.data_key(kind, source.sha256, out_key, ext),
                       path, sp["content_type"])
        manifest = {
            "manifest_version": sidecars.MANIFEST_VERSION, "key": out_key,
            "kind": kind, "model_id": engine["model_id"],
            "model_version": engine["model_version"], "params": params,
            "src_range": [row["t0"], row["t1"]], "fps": meta["fps"],
            "frames": meta["frames"], "width": meta["width"],
            "height": meta["height"], "format": sp["format"], "ext": ext,
            "bytes": size, "created_at": time.time(), "qa": meta["qa"],
            "engine": engine["name"], "source_sha": source.sha256,
            "warmup_s": row.get("warmup", 0.0),
            "ms_per_frame": meta.get("ms_per_frame"),
            "cpu_ms_per_frame": meta.get("cpu_ms_per_frame"),
            "padded_frames": meta.get("padded", 0), "lane": _lane(),
        }
        store.put_bytes(sidecars.manifest_key(kind, source.sha256, out_key),
                        json.dumps(manifest, separators=(",", ":"))
                        .encode("utf-8"), "application/json")
    except Exception as e:  # noqa: BLE001 — retryable: storage hiccup
        return _failed("upload", True, e)
    if engine["fallback"]:
        fb = {"key": out_key, "model_id": engine["model_id"],
              "model_version": engine["model_version"]}
        return _mark_failed(store, kind, source,
                            sidecars.ChunkRef(key, row["t0"], row["t1"]),
                            "model_missing", False,
                            f"{sp['model_id']} is not on this lane; "
                            f"{engine['model_id']} measured the window",
                            attempt, fallback=fb)
    store.delete_many([sidecars.pending_key(kind, source.sha256, key),
                       sidecars.failed_key(kind, source.sha256, key)])
    return {"status": "ready", "frames": meta["frames"],
            "ms_per_frame": meta.get("ms_per_frame")}


def _lane():
    try:
        import config
        return getattr(config, "CLOUDFLARE_CONTAINER_PROFILE", None) or \
            os.getenv("CLOUDFLARE_CONTAINER_PROFILE") or config.WORKER_ROLE
    except Exception:  # noqa: BLE001
        return None


# ── the child process ────────────────────────────────────────────────────

def _decode(src, start, dur, w, h, fps, n_max):
    """BGR frames of [start, start+dur) at ``fps``, scaled to w x h. A
    decode that ends in an ffmpeg error raises DecodeError once the frames
    it did produce are consumed; stopping early is never an error."""
    import tempfile
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if str(src).startswith(("http://", "https://")):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i",
            str(src), "-an", "-sn", "-dn",
            "-vf", f"fps={fps:.6f},scale={w}:{h}:flags=area",
            "-frames:v", str(int(n_max)), "-pix_fmt", "bgr24",
            "-f", "rawvideo", "pipe:1"]
    # stderr to a file, never a pipe nobody drains: a damaged stream can log
    # far more than a pipe buffer holds, which would stall the decoder.
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
    size = w * h * 3
    eof = False
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                eof = True
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        if not eof:
            proc.kill()
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        rc = proc.wait()
        err.seek(0)
        tail = err.read().decode("utf-8", "replace")[-300:]
        err.close()
    if eof and rc != 0:
        raise DecodeError(tail or f"ffmpeg exited {rc}")


class DecodeError(RuntimeError):
    pass


def _even(v):
    v = max(2, int(round(v)))
    return v - v % 2


class _MatteQA:
    """matte_flicker (mean |delta alpha| in a 6 px band round the previous
    edge), edge_chatter (share of pixels toggling >= 6 times per second)
    and the coverage curve, accumulated frame by frame."""

    def __init__(self, cv2, w, h):
        import numpy as np
        self.np, self.cv2 = np, cv2
        self.toggles = np.zeros((h, w), np.uint16)
        self.kernel = np.ones((3, 3), np.uint8)
        self.prev = self.prev_bin = None
        self.flicker = []
        self.coverage = []

    def add(self, a):
        np, cv2 = self.np, self.cv2
        b = a > 0.5
        self.coverage.append(round(float(b.mean()), 4))
        if self.prev is not None:
            pb = self.prev_bin.astype(np.uint8)
            band = (cv2.dilate(pb, self.kernel, iterations=QA_BAND_PX)
                    != cv2.erode(pb, self.kernel, iterations=QA_BAND_PX))
            if band.any():
                self.flicker.append(float(np.abs(a - self.prev)[band].mean()))
            self.toggles += (b != self.prev_bin)
        self.prev, self.prev_bin = a, b

    def result(self, seconds):
        np = self.np
        rate = self.toggles.astype(np.float32) / max(1e-3, seconds)
        return {"flicker": round(float(np.mean(self.flicker)), 5)
                if self.flicker else 0.0,
                "edge_chatter": round(float((rate >= CHATTER_TOGGLES_PER_S)
                                            .mean()), 6),
                "coverage": self.coverage}


def _child_matte(spec, ch):
    import cv2
    import numpy as np
    import personseg
    params, srcinfo, engine = spec["params"], spec["source"], spec["engine"]
    sw, sh = int(srcinfo["width"]), int(srcinfo["height"])
    w = _even(min(sw, int(params.get("max_w") or sw)))
    h = _even(sh * w / float(sw))
    fps = min(float(params.get("fps_max") or 30.0), float(srcinfo["fps"]))
    warm = float(ch.get("warmup") or 0.0)
    n_warm = int(round(warm * fps))
    n_out = max(1, int(round((ch["t1"] - ch["t0"]) * fps)))
    if engine["name"] == "u2net":
        def alpha_of(frame):
            m = personseg.segment(frame, cv2)
            return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        sess = personseg.rvm_session_at(engine["path"],
                                        engine.get("threads") or 0)
        stream = personseg.rvm_stream_on(sess, w, h)

        def alpha_of(frame):
            return stream.step(frame, cv2)
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.mp4")
    import tempfile
    enc_err = tempfile.TemporaryFile()
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=enc_err)
    qa = _MatteQA(cv2, w, h)
    written, last, t_model, i = 0, None, 0.0, 0
    cpu0 = time.process_time()
    try:
        for frame in _decode(spec["src"], ch["t0"] - warm,
                             ch["t1"] - ch["t0"] + warm, w, h, fps,
                             n_warm + n_out):
            t = time.perf_counter()
            a = alpha_of(frame)
            t_model += time.perf_counter() - t
            i += 1
            if i <= n_warm:
                continue
            qa.add(a)
            last = (a * 255.0 + 0.5).astype(np.uint8)
            enc.stdin.write(last.tobytes())
            written += 1
            if written >= n_out:
                break
        padded = 0
        if written == 0:
            raise DecodeError("no frames decoded in the chunk window")
        while written < n_out:            # a short read at the source end
            enc.stdin.write(last.tobytes())
            written += 1
            padded += 1
    finally:
        try:
            enc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        rc = enc.wait()
    enc_err.seek(0)
    tail = enc_err.read().decode("utf-8", "replace")[-200:]
    enc_err.close()
    if rc != 0:
        raise RuntimeError("mask encoder failed: " + tail)
    return out, {"fps": round(fps, 6), "frames": written, "width": w,
                 "height": h, "padded": padded,
                 "ms_per_frame": round(1000.0 * t_model / max(1, i), 2),
                 # CPU time across the model's threads: load-independent cost
                 "cpu_ms_per_frame": round(
                     1000.0 * (time.process_time() - cpu0) / max(1, i), 2),
                 "qa": qa.result(ch["t1"] - ch["t0"])}


def _child_faces(spec, ch):
    import cv2
    params, srcinfo, engine = spec["params"], spec["source"], spec["engine"]
    sw, sh = int(srcinfo["width"]), int(srcinfo["height"])
    w = _even(min(sw, int(params.get("det_w") or 320)))
    h = _even(sh * w / float(sw))
    fps = float(params.get("fps") or 10.0)
    n = max(1, int(round((ch["t1"] - ch["t0"]) * fps)))
    det = cv2.FaceDetectorYN.create(
        engine["path"], "", (w, h), float(params.get("score") or 0.6),
        float(params.get("nms") or 0.3), int(params.get("top_k") or 50))
    samples, t_model, i = [], 0.0, 0
    for frame in _decode(spec["src"], ch["t0"], ch["t1"] - ch["t0"], w, h,
                         fps, n):
        t = time.perf_counter()
        _ok, faces = det.detect(frame)
        t_model += time.perf_counter() - t
        rows = []
        for f in faces if faces is not None else ():
            x, y, bw, bh = (float(v) for v in f[:4])
            box = [min(1.0, max(0.0, x / w)), min(1.0, max(0.0, y / h)),
                   min(1.0, max(0.0, (x + bw) / w)),
                   min(1.0, max(0.0, (y + bh) / h))]
            lms = []
            for k in range(5):
                lms += [float(f[4 + 2 * k]) / w, float(f[5 + 2 * k]) / h]
            rows.append([round(v, 4) for v in box] + [round(float(f[14]), 3)]
                        + [round(v, 4) for v in lms])
        samples.append({"t": round(ch["t0"] + i / fps, 3), "faces": rows})
        i += 1
    if not samples:
        raise DecodeError("no frames decoded in the chunk window")
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.faces.json")
    with open(out, "w") as f:
        json.dump({"version": 1, "fps": fps, "width": w, "height": h,
                   "row": ["x0", "y0", "x1", "y1", "score", "le_x", "le_y",
                           "re_x", "re_y", "n_x", "n_y", "ml_x", "ml_y",
                           "mr_x", "mr_y"],
                   "samples": samples}, f, separators=(",", ":"))
    hit = sum(1 for s in samples if s["faces"])
    return out, {"fps": fps, "frames": len(samples), "width": w, "height": h,
                 "ms_per_frame": round(1000.0 * t_model / max(1, i), 3),
                 "qa": {"flicker": None, "edge_chatter": None,
                        "coverage": round(hit / float(len(samples)), 4)}}


_CHILD = {"matte.person": _child_matte, "faces.yunet": _child_faces}


def _child_main(spec_path):
    with open(spec_path) as f:
        spec = json.load(f)
    fn = _CHILD[spec["kind"]]
    with open(spec["results"], "a") as res:
        for ch in spec["chunks"]:
            row = {"key": ch["key"], "out_key": ch["out_key"],
                   "t0": ch["t0"], "t1": ch["t1"],
                   "warmup": ch.get("warmup", 0.0)}
            try:
                path, meta = fn(spec, ch)
                row.update(ok=True, path=path, meta=meta)
            except DecodeError as e:
                row.update(ok=False, reason="decode", why=str(e)[-300:])
            except Exception as e:  # noqa: BLE001 — one chunk, not the call
                row.update(ok=False, reason="error",
                           why=f"{type(e).__name__}: {str(e)[-280:]}")
            res.write(json.dumps(row) + "\n")
            res.flush()
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        sys.exit(_child_main(sys.argv[2]))
    print("usage: analysis_jobs.py --child <spec.json>", file=sys.stderr)
    sys.exit(2)
