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
    An ask may carry "shorts": {"plan_s": 1800, "edit_s": 4200, "final_s": 2400}: the Shorts board it makes is carried to
    finished films (Edit on every card, every reel exported and downloaded to shorts/). "view": "phone" on a session
    photographs the Studio as a phone shows it (ui/).

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
        steps.append(dict(type="prompt", text=a["text"], typed_phase=a.get("phase", "after_result"), export_before=bool(a.get("export_before")),
                          shorts=a.get("shorts")))
    plan = row.get("plan", "plus_2026")
    view = "phone" if row.get("view") == "phone" else "desktop"
    doc = dict(sid=sid, project_id=0, kind="edit", mode="edit", surface="studio",
               account={"class": row.get("class", "plus"), "plan": plan, "device_type": view, "device_browser": "chrome"},
               uploads=[dict(asset_id=1, project_id=0, kind="original", filename=name, bytes=os.path.getsize(clip), duration_s=p["dur"],
                             width=p["w"], height=p["h"], fps=p["fps"])],
               steps=steps, studio=dict(source=row["source"], t0=row["t0"], t1=row["t1"], note=row.get("note"), view=view))
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
        def send(self, text):
            """The sentence typed the way a person types it, the real Studio photographed at every word (a film shows the
            product's own screen, never a drawing of it): ui/type_<step>_<k>.png, at the view's device scale."""
            p = self.page
            if not os.path.exists(os.path.join(self.out, "shots", "site.png")):
                self.site_shot()
            ta = p.locator("textarea").first
            ta.click(timeout=15000)
            n = len([x for x in os.listdir(os.path.join(self.out, "shots")) if x.startswith("type_")]) if os.path.isdir(os.path.join(self.out, "shots")) else 0
            tag = f"type_{n // 100:02d}"
            self.shot(f"{tag}_00")
            words = text.split()
            for k, w in enumerate(words):
                ta.press_sequentially(w + (" " if k < len(words) - 1 else ""), delay=18)
                self.shot(f"{tag}_{k + 1:02d}")
            if ta.input_value() != text:          # the composer is the person's: whatever it made of the keys, it sends the sentence
                ta.fill(text)
                self.shot(f"{tag}_{len(words):02d}")
            p.wait_for_timeout(250)
            if self.view == "phone":
                p.get_by_role("button", name="Send").click(timeout=10000)
            else:
                ta.press("Enter")
            p.wait_for_timeout(700)
            self.shot(f"{tag}_sent")

        def site_shot(self):
            """The website's own front page as this view shows it (the stack serves the same frontend as valmera.io), at
            three times the device scale: a film's call to action shows the site itself (owner, 17 Nov: "something that
            shows the website directly")."""
            try:
                ctx = self.page.context.browser.new_context(**dict(D.VIEWS[self.view], device_scale_factor=3))
                pg = ctx.new_page()
                pg.goto(self.stack.st["fe_url"] + "/", wait_until="networkidle", timeout=60000)
                pg.wait_for_timeout(2500)
                pg.screenshot(path=os.path.join(self.out, "shots", "site.png"))
                ctx.close()
            except Exception as e:  # noqa: BLE001   a recording never fails on its site photograph
                self.log.doc["site_shot_error"] = str(e)[:200]

        def watch(self, tag):
            super().watch(tag)
            self.shot(f"result_{tag}")            # the Studio with its result in the player

        def do_prompt(self, i, step, pending):
            if step.get("export_before") and self.pid and self.log.doc.get("renders"):
                self.keep_export(f"before_ask_{i}")
            ok = super().do_prompt(i, step, pending)
            if step.get("shorts") and self.pid:
                try:
                    self.finish_shorts(dict(step["shorts"]) if isinstance(step["shorts"], dict) else {})
                except Exception as e:  # noqa: BLE001   what was kept stays kept; the record says where it stopped
                    self.log.doc.setdefault("shorts", {})["error"] = str(e)[:400]
                    self.log.save()
            return ok

        def api(self, method, path, body=None):
            """The product's own API from the signed-in page, as the Studio's buttons call it (the session's token)."""
            return self.page.evaluate("""async ([m, p, b]) => {
              const r = await fetch('/api-backend' + p, {method: m, headers: {'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + localStorage.getItem('token')}, body: m === 'GET' ? undefined : JSON.stringify(b || {})});
              let j = null; try { j = await r.json(); } catch (e) {}
              return {status: r.status, body: j}; }""", [method, path, body])

        def wait_jobs(self, ids, kind, limit_s):
            """Until every project in ids has a settled job of this type (or the time is up): {project: state}."""
            t = time.time()
            while True:
                rows = self.stack.q("""SELECT DISTINCT ON (project_id) project_id, state FROM video_jobs
                                       WHERE project_id = ANY(%s) AND type = %s ORDER BY project_id, id DESC""", (list(ids), kind))
                st = {r["project_id"]: r["state"] for r in rows}
                if (len(st) == len(ids) and all(v not in ("queued", "running") for v in st.values())) or time.time() - t > limit_s:
                    return st
                self.page.wait_for_timeout(5000)

        def finish_shorts(self, opts):
            """The Studio's Shorts board, carried to finished films the way a person does it: the scout's board lands
            from the one typed sentence, Edit is pressed on every card (the product's own autonomous editor finishes each
            reel), every reel is exported, and every export is downloaded. Photographs of the board are kept (ui/)."""
            t0 = time.time()
            rec = self.log.doc.setdefault("shorts", {})
            st = self.wait_jobs([self.pid], "shorts_plan", float(opts.get("plan_s") or 1800))
            kids = [r["id"] for r in self.stack.q("SELECT id FROM projects WHERE parent_project_id=%s ORDER BY id", (self.pid,))]
            meta = (self.stack.q("SELECT meta FROM projects WHERE id=%s", (self.pid,), one=True) or {}).get("meta") or {}
            clips = {c.get("child_project_id"): c for c in (meta.get("shorts") or {}).get("clips") or []}
            rec.update(plan=st.get(self.pid), plan_s=round(time.time() - t0, 1), children=kids,
                       clips=[{k: v for k, v in (clips.get(k) or {}).items() if k in ("title", "hook", "start", "end", "child_project_id")} for k in kids])
            self.log.save()
            try:
                self.page.reload(wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(4000)
            except Exception:  # noqa: BLE001
                pass
            self.shot("board_00")
            if not kids:
                return
            # Edit on every card. The Studio runs a few editors at once and answers the rest "busy" (429): pressed again as
            # each one finishes, the way a person does (17 Nov: 7 of 10 first presses met busy_capacity).
            rec["edit"], t_e = {}, time.time()
            while time.time() - t_e < float(opts.get("edit_s") or 4200):
                have = {r["project_id"] for r in self.stack.q("""SELECT DISTINCT project_id FROM video_jobs
                                                                 WHERE project_id = ANY(%s) AND type = 'agent_turn'""", (kids,))}
                for k in [k for k in kids if k not in have]:
                    code = self.api("POST", f"/projects/{self.pid}/shorts/{k}/edit", {}).get("status")
                    rec["edit"][str(k)] = code
                    if code == 429:
                        break
                if len(have) == len(kids):
                    break
                self.page.wait_for_timeout(20000)
            self.log.save()
            rec["edited"] = {str(k): v for k, v in self.wait_jobs(kids, "agent_turn", float(opts.get("edit_s") or 4200)).items()}
            rec["edit_s"] = round(time.time() - t0, 1)
            self.log.save()
            try:
                self.page.reload(wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(4000)
            except Exception:  # noqa: BLE001
                pass
            self.shot("board_01")
            versions = {}
            for k in kids:
                v = self.stack.q("SELECT max(version) AS v FROM edls WHERE project_id=%s", (k,), one=True)
                if v and v["v"]:
                    versions[k] = int(v["v"])
                    rec.setdefault("export", {})[str(k)] = self.api("POST", f"/projects/{k}/render/final", {"edl_version": versions[k]}).get("status")
            self.log.save()
            rec["finals"] = {str(k): v for k, v in self.wait_jobs(list(versions), "final", float(opts.get("final_s") or 2400)).items()}
            out = os.path.join(self.out, "shorts")
            os.makedirs(out, exist_ok=True)
            got = []
            for k in versions:
                r = self.stack.q("""SELECT id, storage_key, meta FROM assets WHERE project_id=%s AND kind='render'
                                    ORDER BY (meta->>'variant' = 'final') DESC NULLS LAST, id DESC LIMIT 1""", (k,), one=True)
                if not r:
                    continue
                dest = os.path.join(out, f"short_{k}.mp4")
                try:
                    self.stack.s3.download_file(self.stack.st["bucket"], r["storage_key"], dest)
                    got.append(dict(project_id=k, file=f"shorts/short_{k}.mp4", edl_version=versions[k], variant=(r["meta"] or {}).get("variant")))
                except Exception as e:  # noqa: BLE001
                    got.append(dict(project_id=k, error=str(e)[:200]))
            rec.update(files=got, seconds=round(time.time() - t0, 1))
            self.log.save()

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

    try:
        view = (json.load(open(os.path.join(CORPUS, sid, "session.json"))).get("studio") or {}).get("view") or "desktop"
    except (OSError, ValueError):
        view = "desktop"
    a = argparse.Namespace(run=run, sid=sid, browser=browser, view=view, max_prompts=None, no_record=False, stop_after=None)
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
    ui = []                                  # the Studio's own screen: the typing, word by word, and the result (send, watch)
    shots = os.path.join(sdir, "shots")
    for n in sorted(os.listdir(shots)) if os.path.isdir(shots) else []:
        if n.endswith(".png") and (n.startswith("type_") or n.startswith("result_") or n.startswith("01_uploaded") or n == "site.png"
                                  or n.startswith("board_")):
            os.makedirs(os.path.join(tmp, "ui"), exist_ok=True)
            shutil.copy2(os.path.join(shots, n), os.path.join(tmp, "ui", n))
            ui.append(n)
    vids = os.path.join(sdir, "ui")
    for n in sorted(os.listdir(vids)) if os.path.isdir(vids) else []:
        if n.endswith(".webm"):
            os.makedirs(os.path.join(tmp, "ui"), exist_ok=True)
            shutil.copy2(os.path.join(vids, n), os.path.join(tmp, "ui", "session.webm"))
            ui.append("session.webm")
    shorts = []                              # the Shorts board carried to films (finish_shorts): one export per reel
    sh = j.get("shorts") or {}
    for f in sh.get("files") or []:
        src = os.path.join(sdir, f.get("file") or "")
        if f.get("file") and os.path.exists(src):
            os.makedirs(os.path.join(tmp, "shorts"), exist_ok=True)
            dst = os.path.join(tmp, "shorts", os.path.basename(src))
            shutil.copy2(src, dst)
            clip = next((c for c in sh.get("clips") or [] if c.get("child_project_id") == f["project_id"]), {})
            shorts.append(dict(project_id=f["project_id"], file=os.path.join(out, "shorts", os.path.basename(src)), edl_version=f.get("edl_version"),
                               title=clip.get("title"), source_window=[clip.get("start"), clip.get("end")], sha256=sha256(dst), **probe(dst)))
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
               shorts=dict(plan=sh.get("plan"), children=sh.get("children"), edited=sh.get("edited"), finals=sh.get("finals"),
                           seconds=sh.get("seconds"), error=sh.get("error"), films=shorts) if sh else None,
               ui=dict(view=j.get("view") or (sess.get("studio") or {}).get("view"), dir=os.path.join(out, "ui"), files=ui) if ui else None,
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
