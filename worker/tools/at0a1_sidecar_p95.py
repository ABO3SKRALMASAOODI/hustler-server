"""AT-0A-1: person-matte sidecars ready in <= 120 s p95 on 3 shards; a repeat
request is a cache hit in < 1 s.

ROADMAP Wave 0 track 0A. The windows are V01 149.44-187.0 plus a 60 s V02
range (V02 840.47-900.47 here). Two modes:

LOCAL (no production access; runs anywhere the fixtures and models are):
    YUNET_MODEL=... MATTE_RVM_MODEL=... PYTHONPATH=.. \\
      python tools/at0a1_sidecar_p95.py --local --trials 3
  Uses 960x540 proxies of the two windows from the fixture store
  ($VALMERA_FIXTURES_DIR/w0a/proxies), a throwaway local object store, and
  three launcher workers whose children run RVM with --threads each (the 4
  vCPU of a standard-4 shard). Local cores are faster than Cloudflare's, so
  the report also gives the CPU cost per frame for scaling.

POST-DEPLOY (the real acceptance; an operator runs it, it is never run by CI
or a deploy, and it spends batch-lane time):
    CLOUDFLARE_EXECUTOR_ENABLED=1 CLOUDFLARE_EXECUTOR_URL=... \\
    REMOTE_EXECUTOR_SECRET=... S3_*=... PYTHONPATH=.. \\
      python tools/at0a1_sidecar_p95.py --remote --trials 10 \\
        --source sha256=<orig sha>,key=<proxy key>,duration=<s>,fps=<f>,width=960,height=540,shots=<a;b;c>,ranges=149.44:187.0 \\
        --source sha256=...,ranges=840.47:900.47 --cleanup
  Each trial keys its chunks with a fresh ``trial`` param, so every trial is
  a cold measurement without deleting anything a customer could be using;
  --cleanup removes exactly the trial objects afterwards. Chunks go to the
  batch lane's sidecar shards (SIDECAR_SHARDS = 3) through
  remote.run_sidecar_remote, exactly as production prefetch does.

Prints a JSON report: per-trial wall seconds to every chunk ready, p50 /
p95 / max, chunk count and the repeat-request (cache hit) latency.
"""

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOCAL_WINDOWS = (("V01", "ai_bigger", 149.44, 187.0),
                 ("V02", "flying_cars_60s", 840.47, 900.47))


def _fixtures():
    return os.environ.get("VALMERA_FIXTURES_DIR") or os.path.expanduser(
        "~/Documents/Valmera/.valmera/fixtures")


def _pctl(values, q):
    v = sorted(values)
    if not v:
        return None
    k = max(0, min(len(v) - 1, int(round(q * (len(v) - 1)))))
    return v[k]


def local_sources():
    import media
    import scenes
    import sidecars
    out = []
    for vid, name, t0, t1 in LOCAL_WINDOWS:
        path = os.path.join(_fixtures(), "w0a", "proxies", f"{vid}_{name}.mp4")
        if not os.path.exists(path):
            raise SystemExit(f"missing fixture {path}")
        info = media.probe(path)
        shots = scenes.detect_shots(path, float(info["duration"]))
        src = sidecars.source_from(media.sha256_file(path), path, info, 0,
                                   [s.model_dump() if hasattr(s, "model_dump")
                                    else s for s in shots])
        out.append((src, [(0.0, src.duration_s)], f"{vid} {t0}-{t1}"))
    return out


def parse_source(text):
    import sidecars
    kv = dict(part.split("=", 1) for part in text.split(","))
    ranges = [tuple(float(x) for x in r.split(":"))
              for r in kv["ranges"].split(";")]
    shots = [float(s) for s in kv.get("shots", "").split(";") if s]
    src = sidecars.SourceRef(
        sha256=kv["sha256"], storage_key=kv["key"],
        duration_s=float(kv["duration"]), fps=float(kv["fps"]),
        width=int(kv.get("width", 960)), height=int(kv.get("height", 540)),
        project_id=int(kv["project"]) if kv.get("project") else None,
        shots=tuple(shots))
    return src, ranges, kv.get("label", kv["sha256"][:12])


def run_trial(sources, trial, launcher, budget_s):
    import sidecars
    t0 = time.monotonic()
    handles = [sidecars.ensure("matte.person", src, ranges,
                               priority="interactive",
                               params={"trial": trial})
               for src, ranges, _label in sources]
    t_ensure = time.monotonic() - t0
    states = {}
    while time.monotonic() - t0 < budget_s:
        states = {}
        for h in handles:
            states.update(sidecars.chunk_status(h))
        if all(s in ("ready", "failed") for s in states.values()):
            break
        time.sleep(0.5)
        for h in handles:
            sidecars._forget_listing(h.kind, h.source_sha)
    wall = time.monotonic() - t0
    if launcher is not None:
        launcher.join(5)
    return handles, {"wall_s": round(wall, 2), "ensure_s": round(t_ensure, 3),
                     "chunks": len(states),
                     "ready": sum(1 for s in states.values() if s == "ready"),
                     "failed": sum(1 for s in states.values()
                                   if s == "failed")}


def repeat_hit(handles, sources, trial, store, n=10):
    """A cold reader asking again: fresh process caches, same request."""
    import sidecars
    times = []
    for _ in range(n):
        sidecars.use_store(store)                  # drops every cache
        t = time.monotonic()
        again = [sidecars.ensure("matte.person", src, ranges,
                                 params={"trial": trial})
                 for src, ranges, _label in sources]
        ok = all(sidecars.status(h) == "ready" for h in again)
        times.append(time.monotonic() - t)
        if not ok:
            return {"ok": False, "times": times}
    return {"ok": True, "max_s": round(max(times), 3),
            "mean_s": round(statistics.mean(times), 3)}


def projection(handles, metas, shards, rate_ms):
    """What the measured work costs on production shards: every chunk's
    model frames (kept + discarded warm-up), grouped into calls exactly as
    the launcher groups them and handed longest first to the first free
    shard. ``projected_s`` is that busiest shard at ``rate_ms`` per frame
    (config.py: RVM measured 128 ms/frame at 960x540 on standard-4); it
    EXCLUDES admission, cold start, staging and model load, so the real p95
    can only be higher."""
    import sidecars
    groups = []
    for h in handles:
        for g in sidecars._group(list(h.chunks),
                                 sidecars._group_span(h.kind, h.chunks)):
            n = 0
            for c in g:
                m = metas.get(c.key) or {}
                fps = float(m.get("fps") or 0)
                n += int(m.get("frames") or 0) + int(round(
                    float(m.get("warmup_s") or 0) * fps))
            groups.append((sum(c.t1 - c.t0 for c in g), n))
    loads = [0] * max(1, shards)
    for _work, n in sorted(groups, key=lambda g: -g[0]):
        loads[loads.index(min(loads))] += n
    total = sum(n for _w, n in groups)
    return {"calls": len(groups), "model_frames": total,
            "busiest_shard_frames": max(loads),
            "even_split_frames": round(total / max(1, shards)),
            "rate_ms": rate_ms,
            "projected_s": round(max(loads) * rate_ms / 1000.0, 1),
            "excludes": "admission, cold start, staging, model load"}


def cleanup(store, handles):
    import sidecars
    keys = []
    for h in handles:
        listing = store.list(sidecars.kind_dir(h.kind, h.source_sha))
        mine = {c.key for c in h.chunks}
        keys += [k for k in listing if k.rsplit("/", 1)[-1][:64] in mine]
    store.delete_many(keys)
    return len(keys)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true")
    mode.add_argument("--remote", action="store_true")
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--shards", type=int, default=3)
    ap.add_argument("--budget", type=float, default=600.0)
    ap.add_argument("--rate-ms", type=float, default=128.0,
                    help="production RVM ms/frame for the projection")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    import config
    import sidecars
    workdir = None
    if args.local:
        os.environ["SIDECAR_ORT_THREADS"] = str(args.threads)
        config.SIDECAR_ORT_THREADS = args.threads
        config.SIDECAR_SHARDS = args.shards
        workdir = tempfile.mkdtemp(prefix="at0a1-")
        store = sidecars.LocalStore(os.path.join(workdir, "store"))
        sources = local_sources()
        import analysis_jobs

        def execute(source, payload):
            return analysis_jobs.run_sidecar_job(
                None, {"id": None, "type": "sidecar", "payload": payload})
    else:
        import remote
        if not remote.sidecar_available():
            raise SystemExit("remote mode needs CLOUDFLARE_EXECUTOR_ENABLED=1,"
                             " CLOUDFLARE_EXECUTOR_URL and 'sidecar' in the "
                             "synchronous types")
        store = sidecars._store()
        if store is None:
            raise SystemExit("remote mode needs the product bucket (S3_*)")
        sources = [parse_source(s) for s in args.source]
        if not sources:
            raise SystemExit("give at least one --source")
        execute = None
    sidecars.use_store(store)
    report = {"mode": "local" if args.local else "remote",
              "shards": args.shards, "threads_per_shard": args.threads
              if args.local else None,
              "sources": [{"label": label, "sha": src.sha256[:12],
                           "ranges": ranges, "shots": list(src.shots)}
                          for src, ranges, label in sources],
              "trials": []}
    all_handles = []
    try:
        for i in range(args.trials):
            launcher = sidecars._Launcher(execute=execute,
                                          workers=args.shards)
            sidecars.use_launcher(launcher)
            trial = f"at0a1-{uuid.uuid4().hex[:8]}"
            handles, row = run_trial(sources, trial, launcher, args.budget)
            all_handles += handles
            row["trial"] = trial
            row["calls"] = launcher.stats["calls"]
            row["retries"] = launcher.stats["retries"]
            if i == 0:
                row["repeat"] = repeat_hit(handles, sources, trial, store)
                ms, cpu, frames, metas = [], [], 0, {}
                for h in handles:
                    for r in sidecars.fetch(h, os.path.join(
                            workdir or tempfile.gettempdir(), "f")):
                        ms.append(r["meta"].get("ms_per_frame") or 0)
                        cpu.append(r["meta"].get("cpu_ms_per_frame") or 0)
                        frames += int(r["meta"].get("frames") or 0)
                        metas[r["key"]] = r["meta"]
                row["frames"] = frames
                row["rvm_ms_per_frame_mean"] = round(statistics.mean(ms), 1) \
                    if ms else None
                row["rvm_cpu_ms_per_frame_mean"] = round(
                    statistics.mean(cpu), 1) if cpu else None
                report["projection"] = projection(handles, metas, args.shards,
                                                  args.rate_ms)
            report["trials"].append(row)
            print(json.dumps(row), flush=True)
        walls = [t["wall_s"] for t in report["trials"]]
        report["p50_s"] = _pctl(walls, 0.5)
        report["p95_s"] = _pctl(walls, 0.95)
        report["max_s"] = max(walls)
        report["repeat_hit_s"] = report["trials"][0]["repeat"].get("max_s")
        report["pass"] = bool(report["p95_s"] <= 120.0 and
                              report["trials"][0]["repeat"].get("ok") and
                              report["repeat_hit_s"] < 1.0 and
                              all(t["failed"] == 0 for t in report["trials"]))
        if args.cleanup:
            report["cleaned_objects"] = cleanup(store, all_handles)
    finally:
        sidecars.use_launcher(None)
        sidecars.use_store(None)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    text = json.dumps(report, indent=1)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
