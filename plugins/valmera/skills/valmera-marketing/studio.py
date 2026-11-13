#!/usr/bin/env python3
"""The studio recorder: what the product really did with one clip and one sentence, kept with receipts.

A marketing film may show an edit only if the product made it. This drives the product itself, the
way a person does, and keeps what came back:

    python studio.py record --spec spec.json [--run studio-1113a] [--parallel 2]
    python studio.py collect --run studio-1113a          # again, after a stop
    python studio.py ledger                               # every recording, as facts

spec.json:
    {"settings": {"be": "main", "fe": "main", "spend_cap_usd": 3.0},
     "sessions": [{"sid": "why-not-me-punchy", "source": "<file the owner already works from>",
                   "t0": 7916.7, "t1": 7944.2, "plan": "plus_2026",
                   "asks": [{"text": "Make it punchy.", "phase": "before_first_result"},
                            {"text": "Open on his last line.", "phase": "after_result", "export_before": true}]}]}

What happens, per session, on a local stack of the product at that commit (the Gauntlet's runner:
its own database, storage and worker; nothing of production is written, no customer is involved):
a new synthetic account signs in, uploads the clip through the file picker, types each sentence
into the composer, waits for each result, presses Export, and downloads the MP4. `export_before`
presses Export before the sentence too, so a film can show the same project before and after it.

Kept under .tmp/valmera-studio/records/<sid>/: `upload.mp4` (the clip as uploaded), `exports/` (each
MP4 as the browser downloaded it), `edl/v<N>.json` (every version of the project's edit), `journey.json`
(every step, reply and timing), and `record.json`: the facts a film may show, each with its file and
sha256. Nothing here publishes, and nothing reads what a customer wrote.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

V = "/Users/masaoodi/Documents/Valmera"
G = f"{V}/.valmera/week-1106/gauntlet"
ROOT = os.environ.get("VALMERA_STUDIO") or f"{V}/.tmp/valmera-studio"
CORPUS, RECORDS = os.path.join(ROOT, "corpus"), os.path.join(ROOT, "records")
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")
PY = f"{V}/.valmera/venv-worker/bin/python"
HEAVY = f"{V}/.valmera/week-1106/heavy.sh"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    n, den = (s.get("avg_frame_rate") or "30/1").split("/")
    return dict(w=int(s["width"]), h=int(s["height"]), fps=round(float(n) / float(den or 1), 3), dur=round(float(d["format"]["duration"]), 3))


# ----------------------------------------------------------------- the session a person would have

def write_session(row):
    """One recording as the runner reads it: a clip and the sentences, in order."""
    sid = row["sid"]
    d = os.path.join(CORPUS, sid)
    media = os.path.join(d, "media", "1")
    os.makedirs(media, exist_ok=True)
    name = row.get("filename") or f"{sid}.mp4"
    clip = os.path.join(media, name)
    if not os.path.exists(clip):
        # the clip as an editor would have it: the source's own seconds, re-wrapped as H.264 so a browser can pick it
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{row['t0']:.3f}", "-i", row["source"], "-t", f"{row['t1'] - row['t0']:.3f}",
                        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", clip + ".part.mp4"], check=True)
        os.replace(clip + ".part.mp4", clip)
    p = probe(clip)
    steps = [dict(type="upload", asset_ids=[1], first=True)]
    for a in row["asks"]:
        steps.append(dict(type="prompt", text=a["text"], typed_phase=a.get("phase", "after_result"), export_before=bool(a.get("export_before"))))
    plan = row.get("plan", "plus_2026")
    doc = dict(sid=sid, project_id=0, kind="edit", mode="edit", surface="studio",
               account={"class": row.get("class", "plus"), "plan": plan, "device_type": "desktop", "device_browser": "chrome"},
               uploads=[dict(asset_id=1, project_id=0, kind="original", filename=name, bytes=os.path.getsize(clip), duration_s=p["dur"],
                             width=p["w"], height=p["h"], fps=p["fps"])],
               steps=steps, studio=dict(source=row["source"], t0=row["t0"], t1=row["t1"], note=row.get("note")))
    with open(os.path.join(d, "session.json"), "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return clip


# ----------------------------------------------------------------- one browser, one session

def drive(run, sid, browser="chrome"):
    """The Gauntlet's driver with two things a recording needs: an export before a sentence, and every EDL version kept."""
    os.environ["GAUNTLET_CORPUS"] = CORPUS
    sys.path.insert(0, os.path.join(G, "runner"))
    import drive as D

    class Recording(D.Replay):
        def do_prompt(self, i, step, pending):
            if step.get("export_before") and self.pid and self.log.doc.get("renders"):
                self.keep_export(f"before_ask_{i}")
            return super().do_prompt(i, step, pending)

        def keep_export(self, tag):
            v = self.edl()
            self.download()
            src = os.path.join(self.out, "delivered.mp4")
            if os.path.exists(src):
                os.replace(src, os.path.join(self.out, f"export_{tag}.mp4"))
                self.log.doc.setdefault("exports", []).append(dict(tag=tag, file=f"export_{tag}.mp4", edl_version=v and v["version"], at_s=self.log.t()))
            self.log.doc.pop("delivered", None)
            self.log.save()

        def download(self):
            v = self.edl()
            super().download()
            if (self.log.doc.get("delivered") or {}).get("file") == "delivered.mp4":
                self.log.doc["delivered"]["edl_version"] = v and v["version"]

        def run(self):
            try:
                super().run()
            finally:
                try:
                    out = os.path.join(self.out, "edl")
                    os.makedirs(out, exist_ok=True)
                    for r in self.stack.q("SELECT version, json FROM edls WHERE project_id=%s ORDER BY version", (self.pid,)):
                        with open(os.path.join(out, f"v{int(r['version']):03d}.json"), "w") as f:
                            json.dump(r["json"], f, ensure_ascii=False)
                    jobs = self.stack.q("""SELECT id, type, state, created_at, updated_at FROM video_jobs
                                           WHERE project_id=%s ORDER BY id""", (self.pid,))
                    with open(os.path.join(self.out, "jobs.json"), "w") as f:
                        json.dump([dict(r) for r in jobs], f, default=str, indent=1)
                except Exception as e:  # noqa: BLE001   a record without its EDLs says so; the session's result stands
                    with open(os.path.join(self.out, "edl_error.txt"), "w") as f:
                        f.write(str(e)[:400])

    a = argparse.Namespace(run=run, sid=sid, browser=browser, view="desktop", max_prompts=None, no_record=False, stop_after=None)
    Recording(a).run()


# ----------------------------------------------------------------- a run: the stack, the sessions, what is kept

def record(a):
    spec = json.load(open(a.spec))
    run = a.run or "studio-" + time.strftime("%m%d%H%M")
    os.makedirs(CORPUS, exist_ok=True)
    os.chmod(ROOT, 0o700)
    for row in spec["sessions"]:
        write_session(row)
    os.environ["GAUNTLET_CORPUS"] = CORPUS
    sys.path.insert(0, os.path.join(G, "runner"))
    import gauntlet as GT
    import stack as S
    settings = S.merged(json.loads(json.dumps(S.DEFAULTS)), spec.get("settings") or {})
    sids = [r["sid"] for r in spec["sessions"] if not GT.finished(run, r["sid"])]
    print(f"{len(sids)} sessions: {' '.join(sids)}", flush=True)
    if sids:
        stack_path = os.path.join(S.run_dir(run), "stack.json")
        if not (os.path.exists(stack_path) and S.load_stack(run).get("state") == "up"):
            room = GT.production_room()
            if room:
                settings["replay_room_usd"] = room
            S.up(run, settings)
        st = S.load_stack(run)
        cap = float(settings.get("spend_cap_usd") or 1e9)
        logs = os.path.join(st["dir"], "logs")
        queue, live = list(sids), {}
        while queue or live:
            while queue and len(live) < a.parallel:
                sid = queue.pop(0)
                fh = open(os.path.join(logs, f"drive_{sid}.log"), "ab")
                live[sid] = subprocess.Popen([PY, os.path.abspath(__file__), "drive", "--run", run, "--sid", sid], stdout=fh,
                                             stderr=subprocess.STDOUT, start_new_session=True)
                print(f"started {sid} pid {live[sid].pid}", flush=True)
            time.sleep(5)
            for sid, p in list(live.items()):
                if p.poll() is not None:
                    print(f"finished {sid} rc={p.returncode} spend ${S.spend(st)}", flush=True)
                    del live[sid]
            if (S.spend(st) or 0) >= cap * 1.25 and "worker" in st["pids"]:
                print(S.stop("worker", st["pids"]["worker"]), flush=True)
                break
        with open(os.path.join(st["dir"], "run.json"), "w") as fh:
            json.dump(dict(sessions=sids, spend_usd=S.spend(st), finished_at=time.time()), fh, indent=1)
        try:
            GT.report_spend(st)
        except Exception as exc:  # noqa: BLE001
            print(f"spend report failed: {str(exc)[:200]}", flush=True)
        if not a.keep_up:
            S.down(run, False)
    for row in spec["sessions"]:
        try:
            print(json.dumps(collect_one(run, row["sid"]))[:600], flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{row['sid']}: not kept: {e}", flush=True)


def edl_facts(j):
    """An EDL as counts a film may show. Lengths of lists and sums of ranges: nothing is read for meaning."""
    keep = j.get("keep") or []
    out = dict(segments=len(keep))
    try:
        out["kept_s"] = round(sum(float(x[1]) - float(x[0]) for x in keep), 2)
    except (TypeError, IndexError, ValueError):
        pass
    cap = j.get("captions")
    if isinstance(cap, dict):
        out["captions"] = True
        for k in ("words", "lines", "items"):
            if isinstance(cap.get(k), list):
                out["caption_" + k] = len(cap[k])
    for k in ("motion", "texts", "effects", "inserts", "sfx", "transitions", "broll", "overlays", "speed_ramps", "zooms", "cards"):
        v = j.get(k)
        if isinstance(v, (list, dict)) and v:
            out[k] = len(v)
    return out


def collect_one(run, sid):
    sys.path.insert(0, os.path.join(G, "runner"))
    import stack as S
    sdir = os.path.join(S.run_dir(run), "sessions", sid)
    j = json.load(open(os.path.join(sdir, "journey.json")))
    sess = json.load(open(os.path.join(CORPUS, sid, "session.json")))
    out = os.path.join(RECORDS, sid)
    if os.path.exists(os.path.join(out, "record.json")):
        return dict(sid=sid, kept=out, note="already kept")
    tmp = out + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "exports"))
    os.makedirs(os.path.join(tmp, "edl"))
    clip_dir = os.path.join(CORPUS, sid, "media", "1")
    clip = os.path.join(clip_dir, os.listdir(clip_dir)[0])
    shutil.copy2(clip, os.path.join(tmp, "upload.mp4"))
    shutil.copy2(os.path.join(sdir, "journey.json"), os.path.join(tmp, "journey.json"))
    if os.path.exists(os.path.join(sdir, "jobs.json")):
        shutil.copy2(os.path.join(sdir, "jobs.json"), os.path.join(tmp, "jobs.json"))
    edls = {}
    for n in sorted(os.listdir(os.path.join(sdir, "edl"))) if os.path.isdir(os.path.join(sdir, "edl")) else []:
        shutil.copy2(os.path.join(sdir, "edl", n), os.path.join(tmp, "edl", n))
        edls[int(n[1:4])] = edl_facts(json.load(open(os.path.join(sdir, "edl", n))))
    exports = []
    for e in j.get("exports") or []:
        exports.append(dict(e, src=os.path.join(sdir, e["file"])))
    d = j.get("delivered") or {}
    if d.get("file") == "delivered.mp4":
        exports.append(dict(tag="final", file="delivered.mp4", src=os.path.join(sdir, "delivered.mp4"), edl_version=d.get("edl_version"), what=d.get("what")))
    kept = []
    for e in exports:
        if not os.path.exists(e["src"]):
            continue
        dst = os.path.join(tmp, "exports", f"{e['tag']}.mp4")
        shutil.copy2(e["src"], dst)
        kept.append(dict(tag=e["tag"], file=os.path.join(out, "exports", f"{e['tag']}.mp4"), edl_version=e.get("edl_version"), sha256=sha256(dst), **probe(dst)))
    previews = []
    for r in j.get("renders") or []:
        if r.get("file") and os.path.exists(os.path.join(sdir, r["file"])):
            dst = os.path.join(tmp, "exports", "preview_" + os.path.basename(r["file"]))
            shutil.copy2(os.path.join(sdir, r["file"]), dst)
            previews.append(dict(step=r.get("step"), edl_version=r.get("edl_version"), file=os.path.join(out, "exports", os.path.basename(dst)),
                                 quality=r.get("quality"), sha256=sha256(dst), **probe(dst)))
    asks = []
    for s in j.get("steps") or []:
        if s.get("prompt"):
            asks.append(dict(text=s["prompt"], phase=s.get("typed_phase"), seconds=s.get("seconds"), result=s.get("result"), reply=s.get("reply"),
                             edl_before=s.get("edl_before"), edl_after=s.get("edl_after"), changed=s.get("edit_changed")))
    rec = dict(sid=sid, run=run, be_sha=j.get("be_sha"), fe_sha=j.get("fe_sha"), account=dict(plan=j.get("plan"), cls=j.get("account_class")),
               models=j.get("settings"), recorded_at=j.get("started_at"), state=j.get("state"), failures=j.get("failures"),
               source=dict(file=sess["studio"]["source"], t0=sess["studio"]["t0"], t1=sess["studio"]["t1"]),
               upload=dict(file=os.path.join(out, "upload.mp4"), sha256=sha256(os.path.join(tmp, "upload.mp4")), **probe(os.path.join(tmp, "upload.mp4"))),
               first_result_s=j.get("first_result_s"), seconds=j.get("seconds"), cost_usd=j.get("cost_usd"), asks=asks, exports=kept, previews=previews,
               edl_versions={str(k): v for k, v in sorted(edls.items())}, journey=os.path.join(out, "journey.json"),
               journey_sha256=sha256(os.path.join(tmp, "journey.json")))
    with open(os.path.join(tmp, "record.json"), "w") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False, default=str)
    os.makedirs(RECORDS, exist_ok=True)
    os.replace(tmp, out)
    return dict(sid=sid, kept=out, exports=[(k["tag"], k["w"], k["h"], k["dur"]) for k in kept], asks=len(asks), state=rec["state"],
                failures=[f.get("kind") for f in rec["failures"] or []], cost_usd=rec["cost_usd"])


def ledger(root=None):
    """Every finished recording that still has an export on disk."""
    root = root or RECORDS
    rows = []
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, n, "record.json")
        if not os.path.exists(p):
            continue
        r = json.load(open(p))
        r["exports"] = [e for e in r.get("exports") or [] if os.path.exists(e["file"])]
        if r["exports"] and os.path.exists(r["upload"]["file"]):
            r["folder"] = os.path.join(root, n)
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--spec", required=True)
    r.add_argument("--run")
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--keep-up", action="store_true")
    d = sub.add_parser("drive")
    d.add_argument("--run", required=True)
    d.add_argument("--sid", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--run", required=True)
    c.add_argument("--sid")
    sub.add_parser("ledger")
    a = ap.parse_args()
    if a.cmd == "record":
        record(a)
    elif a.cmd == "drive":
        drive(a.run, a.sid)
    elif a.cmd == "collect":
        sids = [a.sid] if a.sid else sorted(os.listdir(CORPUS))
        for sid in sids:
            try:
                print(json.dumps(collect_one(a.run, sid)))
            except Exception as e:  # noqa: BLE001
                print(f"{sid}: {e}")
    else:
        print(json.dumps([dict(sid=x["sid"], asks=[q["text"] for q in x["asks"]], exports=[(e["tag"], e["dur"]) for e in x["exports"]],
                               first_result_s=x["first_result_s"]) for x in ledger()], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
