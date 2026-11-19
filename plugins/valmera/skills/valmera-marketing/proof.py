"""Workflow 2, proof reels: a marketing film for editors, narrated and cut at the references' pace, around what Valmera
really did with one clip and one typed sentence (the one-command path; market.py starts it).

ideas   one model call reads what editors answer to (the 02-editing-tutorials references' own captions and counts), the
        clips the owner already works from (podcast sources with their transcripts) and the owner's few words, if any,
        and returns ranked ideas: the editor's pain or technique, the true claim, the clip and the seconds, two sentences
        to type into Valmera and what each must visibly do, the style
script  records the real output first: the clip is uploaded to a local stack of the product (the Gauntlet's runner, its
        own database, storage and worker; nothing of production is written, no customer is involved), the sentence is
        typed, the export is downloaded (studio.py). A vision model then checks the export against the sentence, element by
        element; a sentence whose result is not visible is not shown: the second sentence is recorded, and if that fails
        too the run stops. Then the narration is written around the recording, every claim audited against its receipts
        by a second call, and the typed sentence is spoken word for word
supply  the narrator (Phonon Easton, the stories read), real footage per line (worker/footage), free photographs of what it
        names, and the recording cut in with its own sound between two narrator lines: the clip as uploaded (the before)
        and the export (the after); a frame of the export can open the film
score   the story reel builder (valmera-stories/reel.py) at a pace set from this film's own narration against the marketing
        references' medians; the recording is shown as delivered (no section finish, flash or caption pool over it); the
        film's editing device (devices.py: the timeline pull-back, the wipe and its count-up grid, or the triptych) is laid
        on its own beats; then
        every picture is checked against the exact words it sits under (gpt-6-sol, low) and a picture that does not fit
        is dropped and the plan built again
render  Valmera's engine, then the shell's brand finish and the music it chooses (wf3.render, wf3.check)

Model calls run on OpenAI; nothing is spent on Anthropic. Nothing is published.
"""
import base64
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
import importlib.util
_inventory_spec = importlib.util.spec_from_file_location("_wf2_visual_inventory", os.path.join(HERE, "visual_inventory.py"))
VI = importlib.util.module_from_spec(_inventory_spec)
_inventory_spec.loader.exec_module(VI)
_devices_spec = importlib.util.spec_from_file_location("_wf2_devices", os.path.join(HERE, "devices.py"))
D = importlib.util.module_from_spec(_devices_spec)
_devices_spec.loader.exec_module(D)
SKILLS = os.path.dirname(HERE)
STORIES = os.path.join(SKILLS, "valmera-stories")
# this folder's own film.py (the three built styles) must not shadow the stories' film.py that reel.py and wf3.py import
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
for p in (os.path.join(SKILLS, "_shell"), STORIES):
    if p not in sys.path:
        sys.path.insert(0, p)
# a `film` already imported from another folder (this one's) steps aside while the stories' modules bind theirs
_held = {k: sys.modules.pop(k) for k in ("film",) if k in sys.modules
         and os.path.dirname(os.path.abspath(getattr(sys.modules[k], "__file__", "") or "")) != STORIES}
import chain  # noqa: E402
import delivered as DL  # noqa: E402
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import reel as R  # noqa: E402
import wf3 as W3  # noqa: E402
import writer  # noqa: E402
sys.modules.update(_held)

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
G = f"{VV}/week-1106/gauntlet"
HEAVY = f"{VV}/week-1106/heavy.sh"
PY = f"{VV}/venv-worker/bin/python"
SRC_ROOT = os.environ.get("WF2_SOURCES", f"{V}/.tmp/valmera-podcast-shorts")
REFS = f"{V}/reference-library/2026-11-05/02-editing-tutorials-and-app-marketing"
ATLAS = f"{VV}/week-1106/atlas/numbers.json"
FOOTAGE_MORE = 2.6        # clips asked a line: the marketing references cut every 0.58 s, and a wrong picture leaves the film
CHECK_MODEL = "gpt-6-sol"           # the picture checks: every picture against its words, the export against the sentence
FORM = dict(words=(55, 85), segments=(7, 12), seconds=(24, 38))
CAP_PX = 72               # the narration's captions: 56 px read small on a phone beside the references' (owner, 16 Nov: "very not clear")
# the recording's stack: this week's main on a synthetic Pro account. Anthropic spend is $0 for this lane, so the Studio
# runs on Pro's own OpenAI lane (gpt-6.1-sol, llm.py) with the Claude switch for new subscribers off (studio_for_all_plans),
# as production runs it for every subscriber the switch does not cover; OPENAI_EDITS lets that lane edit on a stack.
PLAN = "pro_2026"
STACK = {"be": "main", "fe": "main", "plan_models": {}, "planner_model": "off", "env": {"OPENAI_EDITS": "1"},
         "kv": {"studio_for_all_plans": {"on": False}}, "openai_key": True, "trial_per_account_usd": 3.0, "spend_cap_usd": 3,
         "transcriber": "deepgram", "stock": True, "allow_free_stock": True,
         "slots": {"agent": 2, "index": 1, "media": 2}}
STACK_V = 4           # a recording made on other stack settings is never reused (4, 17 Nov: the phone view, the Studio photographed)
VIEW = "phone"        # the Studio as a phone shows it: the film shows the product's own screen at phone size (studio.py ui/)


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    with open(p + ".tmp", "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "reels.json")))["styles"]}


def registry_row(card):
    return dict(id=card["id"], name=card.get("name", card["id"]), spine=card.get("spine") or [], pace=card.get("pace") or [10, 18],
                engine="proof-reel")


def ledger_rows(ctx):
    return (jload(os.path.join(ctx.state_root, "ledger.json"), {}) or {}).get(ctx.workflow, [])


def able_cards(ctx):
    """The styles outside the last three delivered (each style rests three films)."""
    last = [r.get("style") for r in ledger_rows(ctx)[-3:]]
    c = [s for s in cards().values() if s.get("buildable") and s["id"] in R.STYLES]
    if ctx.args.get("style"):
        c = [s for s in c if s["id"] == ctx.args["style"]]
    return [s for s in c if s["id"] not in last] or c


def model_json(text):
    return writer.json_block(text)


def ask(system, content, *, what, effort="medium", model=None, max_tokens=16000):
    text, use = writer.ask_openai(system, content, effort=effort, max_tokens=max_tokens, what=what, model=model)
    return model_json(text), dict(stage=what, **use)


# ----------------------------------------------------------------- what the film can stand on

def json3_words(path):
    """[(t, word)] from a YouTube json3 caption file, in seconds."""
    out = []
    for e in (jload(path, {}) or {}).get("events") or []:
        t0 = float(e.get("tStartMs") or 0) / 1000
        for s in e.get("segs") or []:
            w = (s.get("utf8") or "").strip()
            if w and not (w.startswith("[") and w.endswith("]")):
                out.append((round(t0 + float(s.get("tOffsetMs") or 0) / 1000, 2), w))
    return out


def boundaries(words, gap_s=0.7):
    """Indexes k where a sentence ends after words[k]: its full stop, or (captions without punctuation) a pause."""
    return {k for k, (t, w) in enumerate(words) if w[-1:] in ".?!" or (k + 1 < len(words) and words[k + 1][0] - t >= gap_s + 0.35)}


def sources():
    """The clips the owner already works from: podcast sources downloaded by the podcast workflow, with captions."""
    out = []
    for d in sorted(os.listdir(SRC_ROOT)) if os.path.isdir(SRC_ROOT) else []:
        s = os.path.join(SRC_ROOT, d, "source")
        vid = next((os.path.join(s, n) for n in ("podcast.mp4", "source.mp4") if os.path.exists(os.path.join(s, n))
                    and os.path.getsize(os.path.join(s, n)) > 50e6), None)
        cap = os.path.join(s, "captions.en-orig.json3")
        if vid and os.path.exists(cap):
            yt = next((x for x in d.split("-") if len(x) == 11), d.split("-")[0])
            out.append(dict(id=yt, file=vid, captions=cap))
    return out


def transcript_windows(src, used=(), win_s=24.0, max_words=7000):
    """The source's words in windows of about win_s seconds, each with its start: what the ideas model reads."""
    ws = json3_words(src["captions"])
    ends = boundaries(ws)
    rows, cur, t0 = [], [], None
    for k, (t, w) in enumerate(ws):
        if t0 is None:
            t0 = t
        cur.append(w)
        if (t - t0 >= win_s and k in ends) or t - t0 >= 2 * win_s:
            rows.append((t0, t, " ".join(cur)))
            cur, t0 = [], None
    if cur:
        rows.append((t0, ws[-1][0], " ".join(cur)))
    rows = [r for r in rows if not any(u and u[0] - 5 <= r[0] <= u[1] + 5 for u in used)]
    total = sum(len(r[2].split()) for r in rows)
    step = max(1, int(round(total / max_words)))
    return [dict(t0=round(a, 1), t1=round(b, 1), text=x) for a, b, x in rows[::step]]


def references():
    """The marketing references' own captions, lengths and how people answered; their pace in numbers (atlas)."""
    out = []
    for folder in sorted(os.listdir(REFS)) if os.path.isdir(REFS) else []:
        d = os.path.join(REFS, folder)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".info.json"):
                j = jload(os.path.join(d, f), {}) or {}
                out.append(dict(id=f.split("__")[0], kind=folder, seconds=round(float(j.get("duration") or 0), 1), likes=j.get("like_count"),
                                comments=j.get("comment_count"), caption=(j.get("description") or j.get("title") or "")[:300]))
    out.sort(key=lambda x: -(x.get("comments") or 0))
    return out


def ref_pace():
    """The marketing references' medians (atlas numbers, folder 02): seconds, median shot, cuts a 10 s, spoken words a second."""
    rows = [x for x in jload(ATLAS, []) or [] if str(x.get("wf", "")).startswith("02")]

    def med(k, d):
        v = [float(x[k]) for x in rows if isinstance(x.get(k), (int, float)) and x[k] > 0]
        return round(statistics.median(v), 3) if v else d
    return dict(n=len(rows), seconds=med("dur", 29.7), shot_med_s=med("shot_med", 0.58), cuts10=med("cuts10", 12.0), wps=med("wps", 3.44),
                sfx10=med("sfx10", 13.6))


def recorded_pool(srcs):
    """Explicit reuse of synthetic Studio receipts, never a customer project."""
    root = os.environ.get("WF2_REUSE_RECORDINGS")
    if not root:
        return {}
    by_file = {s["file"]: s for s in srcs}
    out = {}
    for sid in sorted(os.listdir(root)):
        path = os.path.join(root, sid, "record.json")
        rec = jload(path, {}) or {}
        source = rec.get("source") or {}
        asks = rec.get("asks") or []
        exports = rec.get("exports") or []
        # A one-sentence film needs a one-sentence before-first-result recording.
        if (source.get("file") not in by_file or rec.get("state") != "done" or rec.get("failures")
                or len(asks) != 1 or asks[0].get("phase") != "before_first_result"
                or not exports or exports[-1].get("h", 0) <= exports[-1].get("w", 0)):
            continue
        if not all(os.path.isfile(x.get("file", "")) for x in [rec.get("upload") or {}, *exports]):
            continue
        out[sid] = dict(record=path, source=by_file[source["file"]]["id"], file=source["file"],
                        t0=source["t0"], t1=source["t1"], typed=asks[0]["text"],
                        recorded_at=rec.get("recorded_at"), be_sha=rec.get("be_sha"))
    if not out:
        raise RuntimeError("no eligible synthetic Studio recordings in WF2_REUSE_RECORDINGS")
    return out


def reuse_record(path, destination):
    """Verify immutable receipts before copying them into this run's cleanup boundary."""
    rec = json.load(open(path))
    root = os.path.realpath(os.path.dirname(path))
    for media in [rec["upload"], *rec["exports"]]:
        source = os.path.realpath(media["file"])
        if os.path.commonpath([root, source]) != root:
            raise RuntimeError("Studio recording media escaped its recording folder")
        digest = hashlib.sha256()
        with open(source, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if not media.get("sha256") or digest.hexdigest() != media["sha256"]:
            raise RuntimeError("Studio recording receipt hash mismatch: " + os.path.basename(source))
    shutil.copytree(root, destination)

    def remap(value):
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, str) and value.startswith(root + os.sep):
            return destination + value[len(root):]
        return value

    rec = remap(rec)
    rec["reused_from"] = dict(record=path, recorded_at=rec.get("recorded_at"), be_sha=rec.get("be_sha"))
    jsave(os.path.join(destination, "record.json"), rec)
    return rec


# ----------------------------------------------------------------- ideas

FORMAT_MENU = ("showcase (no narrator: Valmera's own export is the film, a striking edit that makes a viewer ask how it was "
               "made; the raw clip and the export side by side at the same instant open it, then the real Valmera Studio on a "
               "phone with the sentence being typed, the export growing out of it and playing as delivered on its own sound, "
               "then the valmera.io website: choose it when the export itself is striking, with pictures or titles landing on "
               "the speaker's words) or tutorial (narrated: the editor's "
               "problem, the typed sentence and how it made the edit, then the call to action)")
DEVICE_MENU = ("timeline (on the line that says the typed sentence, the frame pulls back onto the project's own Valmera "
               "timeline drawn from the recording's edit list, the sentence is typed as it is said, and on send the cut "
               "blocks leave, the pictures and captions land, the receipt prints and the frame pushes into the export: "
               "best when the edit cuts, adds pictures or adds captions), wipe (on that line the raw clip plays whole "
               "while the sentence appears, then a leaning wipe line crosses the frame and the export is behind it, "
               "with a count-up grid of the films the product made from this clip afterwards: best when the change "
               "reads in one frame, such as a reframe, a look or captions)")


def format_of(script, knobs=None):
    """showcase or tutorial: the direction's, else the idea's, else the tutorial (the narrated path)."""
    f = (knobs or {}).get("format") or (script.get("idea_full") or {}).get("format")
    return f if f in D.FORMATS else "tutorial"


def device_of(script, knobs=None):
    """The device the film lays on its typed line (and the hook, for the triptych): the direction's, else the idea's,
    else the timeline (the owner's favourite of the nine, 13 Nov)."""
    d = (knobs or {}).get("device") or (script.get("idea_full") or {}).get("device")
    return d if d in D.DEVICES else "timeline"



def ideas(ctx):
    able = able_cards(ctx)
    srcs = sources()
    if not srcs:
        raise RuntimeError(f"no source clip with captions under {SRC_ROOT}")
    recordings = recorded_pool(srcs)
    rows = ledger_rows(ctx)
    used = {}
    for r in rows:
        if r.get("source") and r.get("window"):
            used.setdefault(r["source"], []).append(r["window"])
    menu_src = [dict(id=s["id"], windows=transcript_windows(s, used.get(s["file"], []))) for s in srcs]
    pace = ref_pace()
    line = ctx.args.get("line")
    system = ("You are the ideas stage of Valmera's marketing workflow. Valmera (valmera.io) is an AI video editor: an editor uploads a "
              "clip, types one sentence in the Studio, and gets an edited vertical film back. The films you plan sell Valmera to video "
              "editors and creators the way the best editing accounts do: a pain every editor knows or a technique they want, shown "
              "with a real before and after. The after is never mocked up: the clip you pick is uploaded to the real product and the "
              "sentence you write is typed into it, so write sentences the product can visibly deliver on a talking clip (a vertical "
              "reframe, a tighter cut that opens on the strongest line, captions in a stated style, pictures over what is said, a "
              "colour treatment, a title) and say exactly what must be visible for the film to be honest. Prefer a change an editor sees "
              "in one frame (pictures over what is said, captions in a stated style, a look) over one that rests on the exact syllable a "
              "cut starts on, and ask for the vertical frame to be filled (cropped to the speaker), never the wide picture boxed in it.")
    content = (f"# What editors answered to (the reference films, most comments first)\n{json.dumps(references()[:25], ensure_ascii=False)}\n\n"
               f"# The references' pace (medians)\n{json.dumps(pace)}\n\n"
               f"# Styles this film can take\n" + "\n".join(f"- {s['id']}: {s['look']} Hook: {s['hook']}" for s in able) + "\n\n"
               f"# Films this workflow made lately (their titles, clips and windows)\n{json.dumps([dict(idea=r.get('idea'), source=os.path.basename(r.get('source') or '') or None, window=r.get('window')) for r in rows[-12:]], ensure_ascii=False)}\n"
               "Never the exact same film twice (the owner's rule): never the same idea told the same way. Another version of an "
               "idea, or the same idea told in a different way (another angle, structure, clip or story), is welcome; the same "
               "narration or beats with only a new look is a repeat.\n\n"
               + DL.prompt_block(ctx.workflow)
               + (f"# What the owner asked for\n{line}\nEvery idea must serve this.\n\n" if line else
                  "# No subject given\nChoose the pain or technique yourself: the one these clips can prove best today.\n\n")
               + f"# The clips the owner works from (transcripts in windows with their seconds)\n{json.dumps(menu_src, ensure_ascii=False)}\n\n"
               "Return JSON only: {\"ideas\": [three items, best first]}. Each: {\"title\": a plain title, \"kind\": before-after|technique, "
               "\"pain\": the editor's pain in their own words, \"technique\": what a great editor does about it, \"claim\": what Valmera "
               "does about it, one sentence a viewer can check in the film, \"source\": a clip id, \"t0\": seconds, \"t1\": seconds (25 to 45 s "
               "of one speaker making one complete point, starting and ending on a sentence; the before must look like raw footage an "
               "editor would get), \"asks\": [two different sentences to type, each under 80 characters, plain words as an editor types "
               "them, each asking for a vertical short], \"must_see\": [for each ask, the list of things that must be visible in the "
               "export for the film to show it honestly], \"hook\": the first spoken line, under 12 words, a pain or a stake, never a "
               "greeting, \"style\": one style id, \"format\": one of " + FORMAT_MENU + ", \"device\": for a tutorial, the editing device that shows "
               "Valmera's real edit happening, one of " + DEVICE_MENU + ", \"why\": one line}. Whatever the format, "
               "the film tells ONE story a stranger follows from its first two seconds: one continuous thought of the "
               "speaker (the before and the after are the same moment of the clip), every picture inside the export set "
               "up by the words he says just before it, and Valmera named on screen early.")
    if recordings:
        content += ("\n# Existing REAL synthetic Studio recordings\n" + json.dumps(recordings, ensure_ascii=False)
                    + "\nFor each idea select recording_id from this menu. Build a NEW editing-pain argument around "
                    "that actual result, not a story about the speaker. Use its typed sentence verbatim as the first "
                    "ask and its source window unchanged. Its export will still undergo the full visual check. "
                    "Choose different recordings across the ranked ideas. Do not claim it was recorded today.")
    got, use = ask(system, content, what="find ideas", effort="medium", max_tokens=12000)
    ids = {s["id"] for s in able}
    by = {s["id"]: s for s in srcs}
    out = []
    for i in (got.get("ideas") if isinstance(got, dict) else got) or []:
        if recordings:
            prior = recordings.get(i.get("recording_id"))
            if not prior:
                continue
            i.update(source=prior["source"], t0=prior["t0"], t1=prior["t1"],
                     asks=[prior["typed"]], recording=prior["record"])
        if i.get("source") in by and len(i.get("asks") or []) >= 1:
            i["style"] = i.get("style") if i.get("style") in ids else able[0]["id"]
            i["file"] = by[i["source"]]["file"]
            out.append(i)
    if not out:
        raise RuntimeError(f"the ideas call returned nothing usable: {json.dumps(got)[:400]}")
    return dict(source=dict(kind="the owner's podcast sources and the marketing references", sources=[s["file"] for s in srcs]),
                ideas=out, chosen=out[0].get("title"), spend=[use])


# ----------------------------------------------------------------- the recording

def snap_window(words, t0, t1):
    """t0 on the first word at or after a sentence start near t0; t1 on the end of the sentence nearest t1."""
    if not words:
        return t0, t1
    bd = boundaries(words)
    starts = [words[0][0]] + [words[k + 1][0] for k in sorted(bd) if k + 1 < len(words)]
    a = min(starts, key=lambda t: abs(t - t0))
    ends = [(words[k + 1][0] if k + 1 < len(words) else t + 0.6) for k, (t, w) in enumerate(words) if k in bd and t > a + 12]
    b = min(ends, key=lambda t: abs(t - t1)) if ends else t1
    return round(max(a - 0.25, 0.0), 2), round(b + 0.15, 2)


def record(ctx, idea, k, ask_text):
    """One session on a local stack of the product: upload the clip, type the sentence before the first result, export.
    The record (upload, export, EDLs, journey, sha256s) is kept in this run's folder and deleted with it."""
    studio_root = os.path.join(ctx.tmp, "studio")
    sid = f"m{ctx.run_id[-6:]}-{k}-s{STACK_V}"
    rec_p = os.path.join(studio_root, "records", sid, "record.json")
    if os.path.exists(rec_p):
        return json.load(open(rec_p))
    if idea.get("recording"):
        original = json.load(open(idea["recording"]))
        if original["asks"][0]["text"] != ask_text:
            raise RuntimeError("the reused recording's instruction changed")
        destination = os.path.join(studio_root, "records", original["sid"])
        if os.path.exists(os.path.join(destination, "record.json")):
            return json.load(open(os.path.join(destination, "record.json")))
        ctx.log(f"reusing verified synthetic recording {original['sid']} from {original.get('recorded_at')}")
        return reuse_record(idea["recording"], destination)
    words = json3_words(next(s["captions"] for s in sources() if s["file"] == idea["file"]))
    t0, t1 = snap_window([w for w in words if idea["t0"] - 20 <= w[0] <= idea["t1"] + 20], float(idea["t0"]), float(idea["t1"]))
    spec = dict(settings=STACK, sessions=[dict(sid=sid, source=idea["file"], t0=t0, t1=t1, plan=PLAN, view=VIEW,
                                                asks=[dict(text=ask_text, phase="before_first_result")], note=idea.get("title"))])
    sp = os.path.join(ctx.run_dir, f"studio_spec_{k}.json")
    jsave(sp, spec)
    run = f"wf2-{ctx.run_id}-{k}"
    env = dict(os.environ, VALMERA_STUDIO=studio_root)
    env.pop("ANTHROPIC_API_KEY", None)
    ctx.log(f"recording {sid}: {idea['source']} {t0}-{t1} s, typed {ask_text!r} (stack {run})")
    t = time.time()
    command = ["zsh", HEAVY, f"wf-studio-{ctx.run_id}", PY, os.path.join(HERE, "studio.py"), "record", "--spec", sp, "--run", run,
               "--parallel", "1"]
    if os.environ.get("GAUNTLET_LOCK_HELD") != "1":
        command = ["zsh", f"{VV}/ops-loop/tools/gauntlet_lock.sh", *command]
    r = subprocess.run(command,
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=5400)
    ctx.log_raw((r.stdout or "")[-3000:] + (r.stderr or "")[-1500:])
    rd = os.path.join(G, "runs", run)
    try:          # the stack is down after the recording; its database and its folder (the clip, the browser's files) go too
        subprocess.run([PY, os.path.join(G, "runner", "stack.py"), "down", run, "--drop-db"], cwd=os.path.join(G, "runner"),
                       capture_output=True, text=True, timeout=300)
    except subprocess.SubprocessError:
        pass
    shutil.rmtree(rd, ignore_errors=True)
    shutil.rmtree(os.path.join(studio_root, "corpus", sid), ignore_errors=True)
    if not os.path.exists(rec_p):
        raise RuntimeError(f"the recording {sid} kept nothing (exit {r.returncode}): {(r.stdout or '')[-300:]} {(r.stderr or '')[-300:]}")
    rec = json.load(open(rec_p))
    rec["recorded_in_s"] = round(time.time() - t)
    jsave(rec_p, rec)
    return rec


def frames(path, times, w=288):
    """Base64 JPEG frames of a video at the given seconds."""
    out = []
    for t in times:
        r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2",
                            "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60)
        out.append(base64.b64encode(r.stdout).decode() if r.returncode == 0 and r.stdout else None)
    return out


def img(b64):
    return dict(type="image", source=dict(type="base64", media_type="image/jpeg", data=b64))


CHECK_V = 5           # the export check reads the spoken words too (EDL keep ranges on the source's captions); the end card is the brand's


def spoken(rec, ex):
    """What the upload and the export say: the source's caption words on the upload's seconds, and on the seconds the
    export's EDL keeps, in the export's order (each kept range with its export start)."""
    src = next((s for s in sources() if s["file"] == rec["source"]["file"]), None)
    if not src:
        return None, None
    t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
    ws = [(t - t0, w) for t, w in json3_words(src["captions"]) if t0 <= t <= t1]
    up = " ".join(w for _, w in ws)
    edl = None
    d = os.path.join(os.path.dirname(os.path.dirname(ex["file"])), "edl")
    if ex.get("edl_version") is not None and os.path.exists(os.path.join(d, f"v{int(ex['edl_version']):03d}.json")):
        edl = jload(os.path.join(d, f"v{int(ex['edl_version']):03d}.json"), {})
    elif os.path.isdir(d) and os.listdir(d):
        edl = jload(os.path.join(d, sorted(os.listdir(d))[-1]), {})
    rows, at = [], 0.0
    for k in (edl or {}).get("keep") or []:
        try:
            a, b = float(k[0]), float(k[1])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append(f"export {at:.1f}-{at + b - a:.1f} s (upload {a:.1f}-{b:.1f} s): " + " ".join(w for t, w in ws if a <= t < b))
        at += b - a
    return up, rows


def export_check(ctx, rec, ask_text, must_see):
    """The export against the sentence, element by element, on its own frames (one a second) and the upload's."""
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1] if rec.get("exports") else None)
    if not ex:
        return dict(ok=False, v=CHECK_V, why="no export")
    if rec.get("failures") or rec.get("state") != "done":
        return dict(ok=False, v=CHECK_V, why="the session failed: " + "; ".join(str(f.get("kind")) for f in rec.get("failures") or []), export=ex)
    up = rec["upload"]
    if ex["w"] >= ex["h"]:
        return dict(ok=False, v=CHECK_V, why=f"the export is {ex['w']}x{ex['h']}, not vertical", export=ex)
    n = min(int(ex["dur"]), 40)
    et = [round(0.5 + i * (ex["dur"] - 1) / max(n - 1, 1), 2) for i in range(n)]
    ut = [round(0.5 + i * (up["dur"] - 1) / 9, 2) for i in range(10)]
    content = [dict(type="text", text=f"The sentence typed into the product: {ask_text!r}\nThe elements are the parts of this sentence, "
                                      "and only those: what the film's planner hoped to see as well (a note, never a reason to fail): "
                                      f"{json.dumps(must_see)}\n"
                                      f"First, the clip as uploaded ({up['w']}x{up['h']}, {up['dur']} s), ten frames:")]
    for t, b in zip(ut, frames(up["file"], ut)):
        if b:
            content += [dict(type="text", text=f"upload at {t} s"), img(b)]
    up_words, ex_words = spoken(rec, ex)
    if up_words:
        content.append(dict(type="text", text=f"What the upload says (its captions): {up_words}"))
    if ex_words:
        content.append(dict(type="text", text="What the export says, kept range by kept range (its EDL on the same captions; the export's "
                                              "speed changes, if any, are not counted):\n" + "\n".join(ex_words)))
    content.append(dict(type="text", text=f"Then the export the product returned ({ex['w']}x{ex['h']}, {ex['dur']} s), a frame a second:"))
    for t, b in zip(et, frames(ex["file"], et)):
        if b:
            content += [dict(type="text", text=f"export at {t} s"), img(b)]
    system = ("You check a product's real output before a marketing film shows it. Be strict and literal: a thing asked for counts as "
              "delivered only if it is plainly visible in the export's frames, or, for what is said and where the cut starts or ends, "
              "plainly in the export's spoken words as given. The spoken words come from automatic captions whose times are "
              "about a third of a second off, so a word at the very edge of a kept range may be listed or missing: judge a cut point by "
              "the sentence, not by one edge word. Every Valmera export ends on Valmera's own end card and carries its small watermark: "
              "that is the brand, never a defect, and never part of the after you choose. You also choose the seconds the film will show.")
    content.append(dict(type="text", text=(
        "Return JSON only: {\"elements\": [{\"asked\": a part of the typed sentence, \"visible\": true|false, \"seconds\": [export seconds where "
        "it shows], \"how\": what is seen}], \"all_visible\": true|false, \"after\": [start, end] export seconds, 2 to 4 s, before the end card, where the "
        "asked change reads best (a cut, a picture, captions, a title landing), \"tease_s\": the export second of its most striking "
        "frame, \"before\": [start, end] upload seconds, 1.5 to 3 s, where the raw clip looks most like raw footage, \"looks\": one "
        "plain line on how the export looks to an editor, \"defects\": [anything an editor would call broken: a cut face, wrong "
        "framing, unreadable or misspelled captions, a black frame; never the end card, never a planner's note]}")))
    got, use = ask(system, content, what="export check", effort="low", model=CHECK_MODEL, max_tokens=6000)
    got = got if isinstance(got, dict) else {}
    vis = [e for e in got.get("elements") or [] if isinstance(e, dict)]
    ok = bool(vis) and all(e.get("visible") for e in vis) and bool(got.get("all_visible")) and not got.get("defects")
    return dict(ok=ok, v=CHECK_V, why=None if ok else ("not visible: " + "; ".join(str(e.get("asked")) for e in vis if not e.get("visible"))
                                             + ("; defects: " + "; ".join(map(str, got.get("defects") or [])) if got.get("defects") else "")),
                export=ex, upload=up, **got, spend=[use])


# ----------------------------------------------------------------- the script

def receipts(rec, chk, ask_text):
    ex = chk["export"]
    up_words, ex_words = spoken(rec, ex)
    return dict(typed=ask_text, upload_says=up_words, export_says=ex_words, phase="before the first result", first_result_s=rec.get("first_result_s"), session_s=rec.get("seconds"),
                upload=dict(seconds=rec["upload"]["dur"], w=rec["upload"]["w"], h=rec["upload"]["h"], sha256=rec["upload"]["sha256"]),
                export=dict(seconds=ex["dur"], w=ex["w"], h=ex["h"], sha256=ex["sha256"], edl_version=ex.get("edl_version")),
                visible=[dict(asked=e.get("asked"), how=e.get("how"), seconds=e.get("seconds")) for e in chk.get("elements") or []],
                looks=chk.get("looks"), product=dict(name="Valmera", url="valmera.io", be_sha=rec.get("be_sha"), fe_sha=rec.get("fe_sha"),
                                                     edit_model=rec.get("models"), recorded_at=rec.get("recorded_at"),
                                                     stack="local Gauntlet stack, synthetic account", account=rec.get("account")),
                source=rec.get("source"), cost_usd=rec.get("cost_usd"), reused_from=rec.get("reused_from"))


def norm_words(s):
    return [x for x in (F.norm_word(w) for w in str(s).split()) if x]


def script_problems(sc, ask_text, rc):
    segs = sc.get("segments") or []
    p = []
    lo, hi = FORM["segments"]
    if not lo <= len(segs) <= hi:
        p.append(f"{len(segs)} segments; {lo}-{hi} wanted")
    n = sum(len(str(s.get("say", "")).split()) for s in segs)
    if not FORM["words"][0] <= n <= FORM["words"][1]:
        p.append(f"{n} spoken words; {FORM['words'][0]}-{FORM['words'][1]} wanted")
    pr = sc.get("proof") or {}
    b, a = pr.get("before_after"), pr.get("after_after")
    if not (isinstance(b, int) and isinstance(a, int) and 0 <= b < a <= len(segs) - 2):
        p.append("proof.before_after and proof.after_after must be segment indexes with before < after < the last segment")
    if any(ch.isdigit() for s in segs for ch in str(s.get("say", ""))):
        p.append("digits in say: write numbers as words")
    want = norm_words(ask_text)
    if not any(" ".join(want) in " ".join(norm_words(s.get("say", ""))) for s in segs):
        p.append(f"no segment says the typed sentence word for word: {ask_text!r}")
    have = json.dumps(rc)
    for s in segs:
        if not 1 <= len(str(s.get("fallback_text") or "").split()) <= 5:
            p.append("every segment needs viewer-facing fallback_text of one to five words")
        for x in s.get("show") or []:
            for d in [w for w in str(x).replace(",", "").split() if any(c.isdigit() for c in w)]:
                if "".join(c for c in d if c.isdigit() or c == ".").strip(".") not in have:
                    p.append(f"the number {d!r} on screen is not in the receipts")
    if not (segs and (segs[-1].get("show") or [])):
        p.append("the last segment is the call to action and shows it (e.g. VALMERA.IO)")
    return p


def audit(sc, rc):
    """A second call: every claim the narration makes about Valmera or this recording, against the receipts."""
    system = ("You audit a marketing script for truth before it is narrated. You get the script and the receipts of the one real "
              "recording it shows. List every claim about Valmera, the edit or the recording (speed, what it did, what was typed, what "
              "is visible or said in the before and the after) and whether the receipts support it: what the upload and the export say "
              "is in upload_says and export_says, what the export shows is in visible and looks. General statements about editing, and "
              "plain descriptions of the before and the after that these receipts bear out, are supported. Return JSON only: {\"claims\": [{\"text\": the words, \"supported\": true|false, \"why\": a few words}]}")
    copy = [dict(say=s.get("say"), show=s.get("show") or [], fallback_text=s.get("fallback_text")) for s in sc.get("segments") or []]
    got, use = ask(system, f"# Receipts\n{json.dumps(rc, ensure_ascii=False)}\n\n# Viewer-facing script\n{json.dumps(copy, ensure_ascii=False)}",
                   what="claims audit", effort="low", model=CHECK_MODEL, max_tokens=4000)
    bad = [c for c in (got.get("claims") if isinstance(got, dict) else []) or [] if isinstance(c, dict) and not c.get("supported")]
    return bad, use


DEVICE_BRIEF = {
    "timeline": "On the segment that says the typed sentence the picture is Valmera's own project, drawn from this "
                "recording's edit list: the frame pulls back onto its timeline, the sentence is typed as it is said, and "
                "as it is sent the timeline changes and the frame pushes into the export. Say the sentence plainly, with "
                "at most three words of your own before it; never describe the interface in words.",
    "wipe": "On the segment that says the typed sentence the raw clip plays whole while the sentence appears a word as it "
            "is said; a leaning wipe line then crosses the frame and the export is behind it. Say the sentence plainly, "
            "with at most three words of your own before it. The line after the after may name how many films the "
            "product made from this clip only if the receipts carry that number.",
    "_all": "A stranger must follow this film from its first two seconds. Say in the first or second segment, plainly, "
            "what Valmera is: an AI video editor, you type what you want and it edits your video. The speaker's clip is "
            "one continuous thought: the before and the after are the same moment of the clip, never two topics. When "
            "the export shows a picture, the line before it names what the speaker says there, so the picture is "
            "expected (he says 'that light', Valmera puts the light on screen), never dropped in cold. The last "
            "segment is the call to action: say Valmera and valmera.io, what it does in a few words, and what to do; "
            "never 'free', never a price or a plan. The film prints its own call to action on screen.",
    "triptych": "The film opens on three exports of the same instant of this clip, made from three typed sentences "
                "(the receipts' siblings), side by side: the hook is spoken over them and can ask which one an editor "
                "would post. The segment that says the typed sentence then shows Valmera's own timeline as it is typed.",
}


def write(ctx, idea, card, rc, notes=None, inventory=None):
    pace = ref_pace()
    system = ("You are the writer and voice director of Valmera's marketing films for video editors. A synthetic narrator (Phonon Easton, "
              "a calm documentary voice) reads your lines over real footage of what they name, cut fast; between two of your lines the "
              "film cuts to the real recording with its own sound: first the clip as uploaded (the before), later the export Valmera "
              "returned (the after). Write like the best editing accounts talk: concrete, a little dry, no hype words, no exclamation "
              "marks, nothing a viewer cannot check in the film. Every line earns its second: a hook that names the pain so an "
              "editor nods, lines that each turn the screw or show the move, short spoken sentences with one stressed word, no filler "
              "like 'here is what' or 'let me show you', no throat-clearing before the before or the after; the before and the after "
              "speak for themselves, so the line before each sets it up in a few words and the line after lands what changed. "
              "The story is the editor's problem, never the celebrity or the archive clip's subject. Each line must progress "
              "from a concrete editing pain to the typed instruction to visible proof. Do not narrate the source clip's "
              "celebrity story. Name Valmera in the spoken call to action. Plan genuinely distinct pictures, never slices "
              "of the same talking take masquerading as new shots. Each segment makes one visible claim. "
              "Split comparisons and different named objects across segments: a monitor and a phone, or a lamp "
              "and chairs, need separate lines so the cutter cannot swap their pictures under the wrong words.")
    system += (" The supplied product assets are the upload and the export only. Do not invent a shot of this export "
               "playing on a physical phone, a view of its editing timeline, or a product interface recording. "
               "THE UPLOAD and THE EXPORT play as their actual files. Generic editing footage may illustrate a "
               "general editing problem, but cannot impersonate this recorded session or the Valmera interface. "
               "Open on a striking full-frame picture that actually fits the hook's words, or the actual export when "
               "those words describe its visible result; never an editing timeline. Do not describe a finished export "
               "as if it were the broken before. Under the Valmera call to action, use the authenticated export; "
               "never show another editing application's interface as the product being offered. "
               "Each segment also needs fallback_text: a viewer-facing display phrase of one to five words, "
               "drawn from that line's meaning. Never use production directions or a subject label there. "
               "This is an emergency text accent, not a substitute for acquiring the actual pictures. "
               "The final fallback_text names Valmera as the action to take. Choose a brief section finish for "
               "the editing pain when it helps the story: bw, gate or crt, then colour for the result. "
               "Set finish to null, bw, gate or crt on each segment, with finish_rank one for the most useful device. "
               "The real uploaded clip and export remain untouched by those effects. "
               "Clarity before everything (owner, 16 Nov: the last films were 'very not clear'): a viewer scrolling "
               "on a phone must know within two seconds what the film is about from the picture and the words together, "
               "so the hook names one editing pain in plain words while the picture shows it or its solved result. "
               "The recording is the hero and carries the film: THE UPLOAD on the lines about the raw clip and on the pain "
               "lines, where the raw clip at the moment the words name is the pain itself (the speaker says the "
               "thing and nothing shows it), THE EXPORT on the hook when it shows the result, on every line after the "
               "after and under the call to action. "
               "Every external picture shows literally what its words name, something an editor or creator meets in "
               "their work (a phone playing a short, a creator at a camera, an editing timeline, a podcast set); never a "
               "metaphor or analogy (no cups, road signs, shop shelves, landscapes or coffee standing for an editing idea). "
               "Fewer subjects said clearly beat many subjects said loosely; the cut keeps its pace by cutting inside the "
               "real recording and the fitting footage.")
    content = (f"# The editing device on screen\n{DEVICE_BRIEF[device_of(dict(idea_full=idea), ctx.args)]}\n{DEVICE_BRIEF['_all']}\n\n"
               f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The recording's receipts (the only facts about Valmera you may state)\n"
               f"{json.dumps(rc, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n# The references' pace (medians)\n"
               f"{json.dumps(pace)}\n\n"
               f"# Form\n{FORM['words'][0]}-{FORM['words'][1]} spoken words in {FORM['segments'][0]}-{FORM['segments'][1]} segments; with the "
               f"before (at most 3 s) and the after (at most 4 s) the film runs {FORM['seconds'][0]}-{FORM['seconds'][1]} s. The first segment is "
               "the hook, heard in the first second: the pain or the stake in under twelve words, never a greeting or 'did you know'. The "
               "before comes by the third or fourth segment; one segment, just before the after, says the typed sentence word for word "
               f"(\"{rc['typed']}\") introduced in a few words of your own; after the after, one or two lines name what changed (only what "
               "the receipts show as visible), then the last segment is the call to action inside the film: the viewer tries it at "
               "valmera.io, said plainly, shown as VALMERA.IO.\n\n"
               + (f"# Fix these from the last attempt\n{notes}\n\n" if notes else "")
               + "Return JSON only: {\"title\": a plain title, \"pace\": steady|brisk|fast, \"energy\": low|mid|high, \"hook_line\": the first "
               "segment's words, \"close_line\": the last segment's words, \"names\": [proper nouns said, as spelled], \"segments\": [{\"say\": "
               "the words spoken, numbers written as words, no digits, \"show\": at most two short display strings printed large on the "
               "words that say them (digits allowed only for a number in the receipts) or [], \"beat\": hook|setup|turn|low|payoff|close, "
               "\"pace\": slow|steady|brisk|fast, \"see\": [{\"thing\": exact subject from named or THE UPLOAD or THE EXPORT, "
               "\"as_of\": the version or state the words mean, \"must_show\": what visibly carries these exact words, "
               "\"moment\": a specific visual action or null, \"still_only\": boolean}], \"pictures\": names from `named` this segment talks about, in order (two or three a "
               "segment, so the cut can change picture every half second), \"melody\": falling|rising|level|wide, \"emphasis\": [one or "
               "two words of say], \"pause_after\": 0.18 to 0.3 (0.4 where it turns, at most twice), \"direction\": a few words for the "
               "actor, \"era\": 2026, \"collage\": true on the one or two segments that list several things}], \"named\": [{\"name\": what a "
               "camera filmed and anyone can find on YouTube or Wikimedia Commons (the speaker on stage, an editor at a timeline, a "
               "podcast studio, a phone scrolling shorts, a named app's interface), \"is\": thing|place|company|person|event (a generic scene "
               "such as an editor's timeline is a thing, not a person), \"searches\": two or three short image-library queries "
               "of two to four words each, \"video_searches\": one to three YouTube searches, \"must_show\": one "
               "plain sentence of what is visible in a frame (a face and its setting, a screen, an object), never how the film shows it "
               "and never which words are said}]; prefer subjects a camera filmed often (a named editing app's timeline, a phone "
               "scrolling short videos, a podcast studio, the speaker of the clip); never name the recording itself here: "
               "in `pictures`, the names \"THE UPLOAD\" and \"THE EXPORT\" put silent seconds of the real clip and of the real export "
               "on that segment, "
               "\"proof\": {\"before_after\": index of the segment the before plays after, \"after_after\": index of the segment the after "
               "plays after (the one that says the typed sentence)}}. Mark one \"low\" (the pain at its worst), one \"turn\", and the "
               "\"payoff\" right after the after.")
    if inventory:
        system += (" The inspected visual inventory supersedes hypothetical subject suggestions above. "
                   "Use its exact subject names for every external picture. Use only the subjects that literally "
                   "show what a line says; leave out the rest of the inventory rather than bending a line to fit a "
                   "picture. Do not repeatedly name the same picture for different claims. "
                   "Keep the editor's problem and the authenticated product result as the argument; never write a "
                   "line whose only job is to justify an available image, and do not prolong the film to fit inventory.")
        content = [dict(type="text", text=content)] + VI.writer_content(inventory, still)
    got, use = ask(system, content, what="write", effort="high", max_tokens=16000)
    return (got if isinstance(got, dict) else {}), use


RERECORD = 2          # a recording the product itself failed is made again this many times before its idea gives way


def product_failed(rec):
    """The product produced no export and its own run recorded a failure (a failed job, an error reply): the stack
    failed, not the idea. Machine fields of the record only."""
    return bool(rec) and not rec.get("exports") and bool(rec.get("failures"))


def set_aside(ctx, rec, n):
    """A failed recording's folder is moved aside so the next one is made fresh (record() reuses a kept record.json)."""
    sid = rec.get("sid")
    if not sid:
        return
    src = os.path.join(ctx.tmp, "studio", "records", sid)
    if os.path.isdir(src):
        dst = f"{src}.failed{n}"
        shutil.rmtree(dst, ignore_errors=True)
        os.rename(src, dst)


def script(ctx, ideas_out):
    keep = os.path.join(ctx.run_dir, "script_state.json")
    stt = jload(keep, {}) or {}
    spend = []
    able = {s["id"]: s for s in able_cards(ctx)}
    # the first sentence of each idea, best idea first, three recordings at most a film: an idea whose result the product
    # does not visibly deliver gives way to the next idea, not to a second try of the same
    tries = [(ii, idea, (idea.get("asks") or [""])[0], ((idea.get("must_see") or [[]])[0] or [])) for ii, idea in enumerate(ideas_out["ideas"][:3])]
    chosen = None
    for ii, idea, ask_text, must in tries:
        tag = k = f"i{ii}"
        old = stt.get(tag, {}).get("check") or {}
        if old.get("v") == CHECK_V and old.get("stack_v") == STACK_V and stt[tag].get("ok") is False:
            prior = jload(stt[tag].get("record") or "", {}) or {}
            if product_failed(prior) and int(stt[tag].get("product_failures") or 0) < RERECORD:
                # the product's own job failed (no export, a failure on record): an outage of the local stack, not the
                # idea's verdict. The recording is set aside and made again (20 Nov: every index of the stack failed on
                # a Whisper model missing offline; the run then refused the three ideas for good)
                n = int(stt[tag].get("product_failures") or 0) + 1
                ctx.log(f"recording {tag}: the product failed ({', '.join(map(str, prior.get('failures') or []))}); recording it again ({n})")
                set_aside(ctx, prior, n)
                stt[tag] = dict(product_failures=n)
                jsave(keep, stt)
                old = {}
            else:
                continue                 # checked by this check and not shown: never shown
        rec = record(ctx, idea, k, ask_text)
        chk = old if old.get("v") == CHECK_V and old.get("stack_v") == STACK_V else dict(export_check(ctx, rec, ask_text, must), stack_v=STACK_V)
        spend += chk.pop("spend", []) if "spend" in chk else []
        stt[tag] = dict(ok=chk["ok"], check=chk, record=os.path.join(ctx.tmp, "studio", "records", rec["sid"], "record.json"),
                        product_failures=int(stt.get(tag, {}).get("product_failures") or 0))
        jsave(keep, stt)
        ctx.log(f"recording {tag}: {'shown' if chk['ok'] else 'not shown'}: {chk.get('why') or chk.get('looks')}")
        if chk["ok"]:
            chosen = (idea, ask_text, rec, chk, tag)
            break
    if not chosen:
        raise RuntimeError("no recording delivered what its sentence asked, visibly: " + "; ".join(
            f"{v['check'].get('why')}" for v in stt.values() if isinstance(v, dict) and v.get("check")))
    idea, ask_text, rec, chk, tag = chosen
    card = able.get(idea["style"]) or next(iter(able.values()))
    rc = receipts(rec, chk, ask_text)
    sibs = []
    if device_of(dict(idea_full=idea), ctx.args) == "triptych":
        sibs = siblings(ctx, idea, rec, tag, stt, keep)
        rc["siblings"] = [dict(typed=s["typed"], export=s["export_sha256"]) for s in sibs]
        if len(sibs) < 2:
            ctx.log(f"triptych: {len(sibs)} other export of this upload; the film opens on its own export and keeps the timeline")
    if format_of(dict(idea_full=idea), ctx.args) == "showcase":
        return showcase_script(idea, card, rec, rc, chk, stt, tag, spend)
    inventory = VI.prepare(ctx, idea, rc, ask, PX.pictures, still)
    spend += inventory["spend"]
    ctx.log(f"visual inventory: {sum(map(len, inventory['pictures'].values()))} distinct inspected pictures before writing")
    notes, sc, bad = ctx.args.get("notes"), None, []
    for attempt in range(4):
        sc, use = write(ctx, idea, card, rc, notes, inventory)
        spend.append(use)
        probs = script_problems(sc, ask_text, rc) + VI.script_problems(sc, inventory)
        if not probs:
            bad, u2 = audit(sc, rc)
            spend.append(u2)
            probs = [f"unsupported claim: {c.get('text')!r} ({c.get('why')})" for c in bad]
        jsave(os.path.join(ctx.run_dir, f"script_attempt{attempt}.json"), dict(script=sc, problems=probs))
        if not probs:
            break
        notes = "\n".join(probs)
        ctx.log("script repair: " + notes[:400])
    else:
        raise RuntimeError("the script did not pass its checks: " + notes[:600])
    for s in sc["segments"]:
        s["era"] = 2026
    window = [rec["source"]["t0"], rec["source"]["t1"]]
    return dict(title=sc.get("title") or idea.get("title"), style=registry_row(card), window=window, hook_line=sc.get("hook_line"),
                close_line=sc.get("close_line"), direction=f"{card['name']}: {card['look']}", idea=idea.get("title"), idea_full=idea,
                script=sc, receipts=rc, export_check={k: v for k, v in chk.items() if k not in ("export", "upload")},
                record=stt[tag]["record"], siblings=sibs,
                cta=dict(say=sc["segments"][-1]["say"], show=sc["segments"][-1].get("show"), needs_fulfilment=False),
                words=writer.words_of(sc), spend=spend)


def same_upload(rec, roots):
    """Real exports the product made from this exact upload (same sha256) in other synthetic Studio sessions under
    `roots`: [{sid, record, typed, export, export_sha256, edl}], vertical, finished, never this one."""
    sha, out, seen = rec["upload"]["sha256"], [], {rec.get("sid")}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for sid in sorted(os.listdir(root)):
            path = os.path.join(root, sid, "record.json")
            r = jload(path, {}) or {}
            if r.get("sid") in seen or (r.get("upload") or {}).get("sha256") != sha or r.get("state") != "done" or r.get("failures"):
                continue
            ex = next((e for e in r.get("exports") or [] if e.get("tag") == "final"), None)
            edl = os.path.join(root, sid, "edl", f"v{int((ex or {}).get('edl_version') or 0):03d}.json")
            if not ex or ex.get("h", 0) <= ex.get("w", 0) or not os.path.isfile(ex.get("file", "")) or not os.path.isfile(edl):
                continue
            seen.add(r.get("sid"))
            out.append(dict(sid=r.get("sid"), record=path, typed=" / ".join(a.get("text", "") for a in r.get("asks") or []),
                            export=ex["file"], export_sha256=ex.get("sha256"), edl=edl, dur=ex.get("dur")))
    return out


def siblings(ctx, idea, rec, tag, stt, keep):
    """Two more real exports of the same upload for the triptych: the run's own Studio sessions of the idea's other
    sentences (recorded on the same seconds), or, when the run reuses recordings, the reused folder's other films of
    the same upload. Each is a finished vertical export; the vision check is the film's own recording's."""
    roots = [os.path.join(ctx.tmp, "studio", "records"), os.environ.get("WF2_REUSE_RECORDINGS")]
    have = same_upload(rec, roots)
    if len(have) < 2 and not idea.get("recording"):
        for j, ask_text in enumerate((idea.get("asks") or [])[1:4]):
            if len(have) >= 2:
                break
            try:
                record(ctx, idea, f"{tag}t{j}", ask_text)
            except RuntimeError as e:          # a session that kept nothing: the next sentence, then the film without it
                ctx.log(f"triptych sentence {j + 1}: {str(e)[:200]}")
            have = same_upload(rec, roots)
    stt.setdefault(tag, {})["siblings"] = [h["record"] for h in have[:2]]
    jsave(keep, stt)
    return have[:2]


# The music step's brief for a showcase (chain.music_version hands it to the chooser, a model when one is available).
# The references under a speaker and a product screen (marketing-rebuild/SPEC.md section 4): a steady beat low under the
# voice and forward where nobody speaks; the 16 Nov showcase got an epic orchestral cue under a podcast ("Turkish series").
SHOWCASE_DIRECTION = ("showcase: a podcast speaker's own words carry the film (Valmera's edit of his clip): the raw clip and the edit side "
                      "by side, then the Valmera Studio on a phone being typed into, the edit full frame on his voice, then the website. "
                      "The music sits like the references' under a speaker and a product screen: a steady modern beat (hip-hop or "
                      "electronic) low under his voice, filling the screen's stretch and the website; never an epic, orchestral or drama cue.")


def showcase_script(idea, card, rec, rc, chk, stt, tag, spend):
    """A showcase has no narrator: its words are the typed sentence (on the Studio's own screen) and the website."""
    sc = dict(title=idea.get("title"), format="showcase", segments=[], named=[], hook_line=None, close_line="valmera.io",
              proof=dict(before_after=None, after_after=None))
    return dict(title=idea.get("title"), style=registry_row(card), window=[rec["source"]["t0"], rec["source"]["t1"]], hook_line=None,
                close_line="valmera.io", direction=SHOWCASE_DIRECTION, idea=idea.get("title"), idea_full=idea, script=sc, receipts=rc,
                export_check={k: v for k, v in chk.items() if k not in ("export", "upload")}, record=stt[tag]["record"], siblings=[],
                cta=dict(say=None, show="valmera.io", needs_fulfilment=False), words=0, spend=spend)


SHOWCASE_S = (12.0, 20.0)      # the export's own stretch in a showcase: one complete thought, ended where he pauses (17 Nov: the
                               # film no longer spends 2.4 s on a raw intro, so the edit gets the seconds of a whole thought)


def pauses(path, lo, hi, min_s=0.15, below_db=28.0):
    """The speaker's pauses in a file's sound between lo and hi seconds, measured: [(start, end)] where the 20 ms
    energy stays below_db under the stretch's loud level (its 95th percentile) for min_s or longer."""
    import numpy as np
    r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{max(hi - lo, 0.1):.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "f32le", "-"], capture_output=True, timeout=120)
    x = np.frombuffer(r.stdout, np.float32)
    n = 320
    if len(x) < 10 * n:
        return []
    e = 10 * np.log10(np.maximum((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1), 1e-12))
    quiet = e < np.percentile(e, 95) - below_db
    out, k = [], 0
    while k < len(quiet):
        if quiet[k]:
            a = k
            while k < len(quiet) and quiet[k]:
                k += 1
            if (k - a) * 0.02 >= min_s:
                out.append((round(lo + a * 0.02, 3), round(lo + k * 0.02, 3)))
        k += 1
    return out


def showcase_window(ex, body, pics=(), lo=SHOWCASE_S[0], hi=SHOWCASE_S[1], reach=4.0):
    """[start, end] export seconds: from the export's first frame (the product's own opening, growing out of the Studio)
    to the end of a spoken thought between lo and hi seconds: the longest pause the speaker
    makes there, measured on the export's own sound (the captions' clock is not the voice's). `pics`: the export's
    pictures and titles, (start, seconds) from its edit list; the film never ends inside one (17 Nov: a chair cut 0.4 s
    in), so a pause inside a picture is passed over and the search reaches up to `reach` s past hi for one after it."""
    hi = min(hi, body - 0.2)
    inside = lambda t: any(a - 0.2 <= t <= a + d + 0.1 for a, d in pics)  # noqa: E731
    ps = [p for p in pauses(ex["file"], lo, min(hi + reach, body - 0.2) + 0.5) if not inside(p[0])]
    ps = [p for p in ps if lo <= p[0] <= hi] or [p for p in ps if hi < p[0] <= min(hi + reach, body - 0.2)][:1]
    if ps:
        a, b = max(ps, key=lambda p: (p[1] - p[0], p[0]))
        end = a + min(0.25, (b - a) / 2)
    else:
        end = quiet_snap(ex["file"], hi, span=0.25)
    return [0.0, round(end, 3)]


def showcase_hook(ex, body, pics, span=None):
    """[start, end] export seconds of the split hook: the export's most visible change, its longest picture or title
    (read from its edit list, `pics` (start, seconds)), entered a breath before it lands, SPLIT_S long; without one, the
    stretch of the same length at two fifths of the body. Snapped to the quietest point near each end (between words)."""
    span = float(span or D.SPLIT_S)
    fit = [(a, d) for a, d in pics if a + min(d, span) <= body - 0.2]
    if fit:
        a, d = max(fit, key=lambda p: (p[1], p[0]))
        t0 = quiet_snap(ex["file"], max(a - 0.3, 0.0), span=0.25)
    else:
        t0 = quiet_snap(ex["file"], max(0.4 * body - span / 2, 0.0), span=0.3)
    t0 = min(max(t0, 0.0), max(body - span - 0.1, 0.0))
    return [round(t0, 3), round(t0 + span, 3)]


def ui_shots(rec):
    """The recording's photographs of the Studio (phone view): the typing, a photograph per word with the empty
    composer first, the sent screen, and the site's front page. None when the recording has none (made before 17 Nov,
    or in the desktop view)."""
    u = rec.get("ui") or {}
    d = u.get("dir") or os.path.join(os.path.dirname(rec["upload"]["file"]), "ui")
    files = sorted(u.get("files") or (os.listdir(d) if os.path.isdir(d) else []))
    typing = [f for f in files if f.startswith("type_00_") and f[8:10].isdigit()]
    sent = next((f for f in files if f == "type_00_sent.png"), None)
    if not typing or not sent:
        return None
    from PIL import Image
    with Image.open(os.path.join(d, typing[0])) as im:
        w, h = im.size
    out = dict(dir=d, w=w, h=h, typing=typing, sent=sent, view=u.get("view"), player=player_rect(os.path.join(d, typing[0]), rec["upload"]))
    board = next((f for f in ("board_01.png", "board_00.png") if f in files), None)
    if board:
        with Image.open(os.path.join(d, board)) as im:
            out["board"] = dict(path=os.path.join(d, board), w=im.size[0], h=im.size[1])
    if "site.png" in files:
        with Image.open(os.path.join(d, "site.png")) as im:
            out["site"] = dict(path=os.path.join(d, "site.png"), w=im.size[0], h=im.size[1])
    return out


def player_rect(png, upload):
    """[top, bottom] of the Studio player's picture on a photograph of the screen, as fractions of its height: the
    first flat band (the controls' ground) below the top quarter is the picture's bottom, and the upload's own shape
    over the screen's width gives its top. None when no flat band is found."""
    import numpy as np
    from PIL import Image
    try:
        with Image.open(png) as im:
            g = np.asarray(im.convert("L"), np.float32)
        h, w = g.shape
        std = g[:, int(0.13 * w):int(0.87 * w)].std(1)
        flat = [r for r in range(int(0.22 * h), int(0.5 * h)) if std[r] < 1.0]
        if not flat:
            return None
        bottom = flat[0]
        top = bottom - w * float(upload["h"]) / float(upload["w"])
        return [round(max(top, 0) / h, 4), round(bottom / h, 4)] if top > 0 else None
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return None


def later_label(rec):
    """The recording's own time from the upload to its first result, as the push's label ("3 MIN"), or None."""
    s = rec.get("first_result_s")
    try:
        s = float(s)
    except (TypeError, ValueError):
        return None
    return f"{max(int(round(s / 60.0)), 1)} MIN" if s >= 45 else f"{int(round(s))} SEC"


def cut_times(path, lo=0.0, hi=None, jump=18.0, fps=8):
    """The picture's own cuts in a file (seconds): frames whose mean luma change from the frame before (64x36, 0-255)
    is `jump` or more, sampled at fps."""
    import numpy as np
    cmd = [PX.FF, "-v", "error", "-ss", f"{max(lo, 0.0):.3f}", "-i", path] + (["-t", f"{hi - lo:.3f}"] if hi else []) + \
        ["-vf", f"fps={fps},scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=300).stdout, np.uint8)
    if len(x) < 2 * 64 * 36:
        return []
    x = x[: len(x) // (64 * 36) * 64 * 36].reshape(-1, 36, 64).astype(np.float32)
    d = np.abs(np.diff(x, axis=0)).mean(axis=(1, 2))
    return [round(max(lo, 0.0) + (k + 1) / fps, 3) for k in np.nonzero(d >= jump)[0]]


HERO_S = (8.0, 11.0)           # the hero short's own stretch in the scale film: a short that plays whole in it, else the busiest
                               # stretch of the most-cut one (17 Nov: the board's ten reels had almost no cuts; the whole one won)


def scale_plan(rec):
    """The scale film's facts from a recording whose Shorts board was carried to films (studio.py finish_shorts): the
    vertical films with their bodies (end card off), the hero (the film whose picture changes most often: the most
    visible editing, measured) and its window (the busiest HERO_S stretch, entered and ended between words). None when
    the board gave fewer than four films."""
    films = []
    for f in ((rec.get("shorts") or {}).get("films") or []):
        if f.get("file") and os.path.exists(f["file"]) and f.get("h", 0) > f.get("w", 1):
            body = max(float(f["dur"]) - endcard_s(), 3.0)
            cuts = cut_times(f["file"], 0.0, body)
            films.append(dict(f, body=round(body, 3), cuts=cuts, rate=len(cuts) / body))
    if len(films) < 4:
        return None
    films.sort(key=lambda f: -f["rate"])
    lo, hi = HERO_S
    whole = [f for f in films if lo <= f["body"] <= hi + 1.0]
    if whole:                     # a film that plays complete in the hero's seconds: its title, its whole thought, its end
        hero = max(whole, key=lambda f: (f["rate"], f["body"]))
        t0, t1 = 0.0, hero["body"]
        films.remove(hero)
        films.insert(0, hero)
        return dict(films=films, hero=hero, window=[0.0, round(t1, 3)])
    hero = films[0]
    return dict(films=films, hero=hero, window=[round(t0, 3), round(t1, 3)])


def scale_supply(ctx, script, sup_dir, pk, rec, plan):
    """The scale film's pack: its grid's films, the hero and the voice track cut to devices.showcase_marks (the grid and
    the Studio carry their own sounds; the hero's own sound from where it grows out of the board)."""
    ui = ui_shots(rec)
    typed = script["receipts"]["typed"]
    win = [float(x) for x in (W3.direction_of(ctx).get("export_window") or plan["window"])]
    mk = D.showcase_marks([0.0, D.SCALE_HOOK_S], win, len(typed.split()), lead_f=F.LEAD_F, board_f=D.BOARD_F if ui and ui.get("board") else 0)
    parts = []
    # the grid is heard as its outlined film (the hero, playing from its start in the grid): one voice under ten films
    for name, src, a, n_f in (("grid", plan["hero"]["file"], win[0] + mk["lead_f"] / F.FPS, mk["studio"][0] - mk["lead_f"]),
                              ("studio", None, 0.0, mk["export"][0] - mk["studio"][0]),
                              ("export", plan["hero"]["file"], win[0], mk["export_voice"][1] - mk["export_voice"][0]),
                              ("site", None, 0.0, mk["dur_f"] - mk["export_voice"][1])):
        p_ = os.path.join(sup_dir, f"voice_{name}.wav")
        n = n_f / F.FPS
        if src:
            subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{n:.3f}", "-vn", "-ac", "1", "-ar", "48000", "-af",
                            f"afade=t=in:d=0.03,afade=t=out:st={max(n - 0.06, 0):.3f}:d=0.06,apad=whole_dur={n:.3f}", p_], check=True, timeout=120)
        else:
            subprocess.run([PX.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{n:.3f}", p_], check=True, timeout=60)
        parts.append(p_)
    voice = os.path.join(sup_dir, "voice.wav")
    lst = os.path.join(sup_dir, "voice.txt")
    open(lst, "w").write("".join(f"file '{q}'\n" for q in parts))
    subprocess.run([PX.FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-ar", "48000", voice], check=True, timeout=120)
    words = os.path.join(sup_dir, "words.json")
    jsave(words, dict(voice="the recording", duration=round((mk["dur_f"] - mk["lead_f"]) / F.FPS, 3), words=[]))
    tiles = [dict(path=f["file"], w=int(f["w"]), h=int(f["h"]), fps=float(f.get("fps") or 30.0), dur=float(f["dur"]),
                  t0=(win[0] if f is plan["hero"] else 0.3), project_id=f.get("project_id"), title=f.get("title"))
             for f in plan["films"]]
    mins = int(round(float(rec["upload"]["dur"]) / 60.0))
    pack = dict(sid=ctx.sid, words=words, narration=voice, ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[], footage=[], voices=[],
                work=os.path.join(sup_dir, "finish"), fit=[],
                # the label over the push is the recording's own time from Send to the finished films (the board's turn and the editors)
                product=dict(paths=[plan["hero"]["file"]], window=win, marks=mk, ui=ui, variant="scale", tiles=tiles,
                             later=later_label(dict(first_result_s=float((rec.get("asks") or [{}])[0].get("seconds") or 0)
                                                    + float((rec.get("shorts") or {}).get("seconds") or 0))),
                             hero=tiles[0], sub=f"from a {mins}-min podcast", upload_at=0.0, cta_s=D.SITE_S, cta_said=None))
    rec_ = dict(narration_s=None, voice=None, gate=None, pictures=0, subjects=0, missing=[], spend=[], product=pack["product"], footage=None,
                format="showcase", variant="scale", shorts=len(tiles), hero=dict(project_id=plan["hero"].get("project_id"),
                                                                                  cuts_per_10s=round(plan["hero"]["rate"] * 10, 1)))
    jsave(pk, dict(pack=pack, record=rec_))
    return rec_


def showcase_supply(ctx, script, sup_dir, pk):
    """The showcase's pack (devices.showcase): the export's seconds for the split hook and for its own stretch, and the
    film's voice track cut to the showcase's frames: the export's own sound under the split, the Studio's stretch
    without a voice (its keys and its Send are the device's sounds, the film's bed carries it), the export's own sound
    from where it grows out of the Studio, then the site. No narrator, no footage search."""
    rec = json.load(open(script["record"]))
    plan = scale_plan(rec)
    if plan:                      # a Shorts board carried to films: the scale film
        return scale_supply(ctx, script, sup_dir, pk, rec, plan)
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    prod = os.path.join(sup_dir, "footage", "product")
    os.makedirs(prod, exist_ok=True)
    body = ex["dur"] - endcard_s()
    cut(ex["file"], 0.0, body, os.path.join(prod, "export_body.mp4"))
    pics = [(float(o["start"]), float(o["duration_s"])) for o in D.MF.pictures_of(edl) + D.MF.titles_of(edl)]
    d = W3.direction_of(ctx) if hasattr(ctx, "run_dir") else {}
    win = [float(x) for x in d.get("export_window") or showcase_window(ex, body, pics)]
    hook = [float(x) for x in d.get("hook_window") or showcase_hook(ex, body, pics)]
    ui = ui_shots(rec)
    typed = script["receipts"]["typed"]
    mk = D.showcase_marks(hook, win, len(typed.split()), lead_f=F.LEAD_F)
    voice = os.path.join(sup_dir, "voice.wav")
    lead = F.LEAD_F / F.FPS
    parts = []

    def piece(name, src, a, n_f):
        p = os.path.join(sup_dir, f"voice_{name}.wav")
        n = n_f / F.FPS
        if src:
            subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", src, "-t", f"{n:.3f}", "-vn", "-ac", "1", "-ar", "48000",
                            "-af", f"afade=t=in:d=0.03,afade=t=out:st={max(n - 0.06, 0):.3f}:d=0.06,apad=whole_dur={n:.3f}", p],
                           check=True, timeout=120)
        else:
            subprocess.run([PX.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{n:.3f}", p], check=True, timeout=60)
        parts.append(p)

    # the voice starts on frame LEAD_F: the split's picture at frame f shows hook[0] + f / FPS, so its sound starts there too
    piece("split", ex["file"], hook[0] + lead, mk["split"][1] - mk["lead_f"])
    piece("studio", None, 0.0, mk["export"][0] - mk["split"][1])
    piece("export", ex["file"], win[0], mk["export_voice"][1] - mk["export_voice"][0])     # under the website's arrival too
    piece("site", None, 0.0, mk["dur_f"] - mk["export_voice"][1])
    lst = os.path.join(sup_dir, "voice.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    subprocess.run([PX.FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ac", "1", "-ar", "48000", voice], check=True, timeout=120)
    words = os.path.join(sup_dir, "words.json")
    jsave(words, dict(voice="the recording", duration=round((mk["dur_f"] - mk["lead_f"]) / F.FPS, 3), words=[]))
    pack = dict(sid=ctx.sid, words=words, narration=voice, ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[], footage=[],
                voices=[], work=os.path.join(sup_dir, "finish"), fit=[],
                product=dict(paths=[os.path.join(prod, "export_body.mp4")], window=win, hook=hook, marks=mk, ui=ui, later=later_label(rec),
                             upload_at=round(D.src_at(D.program(edl)[0], hook[0]), 3), cta_s=D.SITE_S, cta_said=None))
    rec_ = dict(narration_s=None, voice=None, gate=None, pictures=0, subjects=0, missing=[], spend=[], product=pack["product"], footage=None,
                format="showcase", studio_ui=bool(ui), site=bool(ui and ui.get("site")))
    jsave(pk, dict(pack=pack, record=rec_))
    return rec_




# ----------------------------------------------------------------- supply

def cut(src, t0, t1, out, audio=True):
    cmd = [PX.FF, "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}", "-map", "0:v:0"]
    cmd += (["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"] if audio else ["-an"])
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p", "-r", "30", out]
    subprocess.run(cmd, check=True, timeout=300)
    if audio:
        subprocess.run([PX.FF, "-v", "error", "-y", "-i", out, "-vn", "-ac", "1", "-ar", "48000", out[:-4] + ".wav"], check=True, timeout=120)


def quiet_snap(path, t, span=0.45):
    """The quietest 40 ms of the clip's sound within span of t (a cut between words, not through one)."""
    import numpy as np
    r = subprocess.run([PX.FF, "-v", "error", "-ss", f"{max(t - span, 0):.3f}", "-i", path, "-t", f"{2 * span:.3f}", "-ac", "1", "-ar", "8000",
                        "-f", "s16le", "-"], capture_output=True, timeout=60)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32)
    n = 320
    if len(x) < 4 * n:
        return t
    e = np.sqrt((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1))
    return round(max(t - span, 0) + (int(np.argmin(e)) + 0.5) * n / 8000, 3)


def endcard_s():
    """Seconds at the end of every export that are Valmera's end card (worker/brand/endcard.mp4), with a settle margin:
    the film shows the export's own content, never its end card (the shell adds the film's own)."""
    try:
        p = os.path.join(F.scene_worker() if hasattr(F, "scene_worker") else "", "brand", "endcard.mp4")
        r = subprocess.run([PX.FF.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) + 0.6
    except (OSError, ValueError, subprocess.SubprocessError):
        return 5.6


def product_crop(record_path, rec, t):
    """The export's crop of the upload at upload second t, [x0, y0, x1, y1] in upload fractions, read from the final
    export's own edit list (frame mode crop: the ratio's largest window, centred on the focus, the focus track
    overriding it in its seconds). None when the product did not crop (a fit, a canvas) or the list is missing."""
    ex = next((e for e in rec.get("exports") or [] if e.get("tag") == "final"), (rec.get("exports") or [{}])[-1])
    try:
        edl = json.load(open(os.path.join(os.path.dirname(record_path), "edl", f"v{int(ex['edl_version']):03d}.json")))
        fr = edl.get("frame") or {}
        if fr.get("mode") != "crop":
            return None
        a_w, a_h = [float(x) for x in str(fr.get("ratio") or "9:16").split(":")]
        pw, ph = float(rec["upload"]["w"]), float(rec["upload"]["h"])
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None
    aspect, pa = a_w / a_h, pw / ph
    cw, ch = (aspect / pa, 1.0) if pa > aspect else (1.0, pa / aspect)
    fx = float(fr.get("focus_x") if fr.get("focus_x") is not None else 0.5)
    fy = float(fr.get("focus_y") if fr.get("focus_y") is not None else 0.5)
    for k in fr.get("focus_track") or []:
        if float(k.get("t0", 0)) <= t < float(k.get("t1", 0)) and (k.get("mode") or "crop") == "crop":
            fx = float(k.get("x", fx))
            fy = float(k.get("y", fy))
    x0 = min(max(fx - cw / 2, 0.0), 1.0 - cw)
    y0 = min(max(fy - ch / 2, 0.0), 1.0 - ch)
    return [round(x0, 4), round(y0, 4), round(x0 + cw, 4), round(y0 + ch, 4)]


def product_clips(script, out):
    """The before and the after as clips with their own sound, and a frame of the after for the hook."""
    rec = json.load(open(script["record"]))
    chk = script["export_check"]
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    up = rec["upload"]
    os.makedirs(out, exist_ok=True)
    body = ex["dur"] - endcard_s()                       # the export without its end card
    a, b = [float(x) for x in (chk.get("after") or [0, min(4.0, body)])]
    a = min(max(a, 0.0), max(body - 2.0, 0.0))
    b = min(max(b, a + 2.0), a + 4.0, body)
    a = quiet_snap(ex["file"], a)
    b = min(quiet_snap(ex["file"], b), a + 4.0, body)
    ba, bb = [float(x) for x in (chk.get("before") or [1.0, 4.0])]
    try:          # the before is the same moment as the after (owner, 17 Nov: one continuous thought, never a topic jump)
        edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
        tl = D.program(edl)[0]
        ba = D.src_at(tl, a)
        bb = min(ba + min(b - a, 3.0), up["dur"] - 0.1)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ba, bb = quiet_snap(up["file"], max(ba, 0.0)), quiet_snap(up["file"], min(max(bb, ba + 2.0), up["dur"] - 0.1))
    bb = min(bb, ba + 3.0)
    cut(up["file"], ba, bb, os.path.join(out, "before.mp4"))
    cut(ex["file"], a, b, os.path.join(out, "after.mp4"))
    ts = float(chk.get("tease_s") or (a + b) / 2)
    ts = min(max(ts - 0.6, 0.0), body - 1.4)
    cut(ex["file"], ts, ts + 1.3, os.path.join(out, "tease.mp4"), audio=False)
    # the whole export, silent: a line the cut has no picture for plays the real result, never a type page
    cut(ex["file"], 0.0, body, os.path.join(out, "export_body.mp4"), audio=False)
    return dict(before=dict(src=up["file"], t=[ba, bb], sha256=up["sha256"], crop=product_crop(script["record"], rec, (ba + bb) / 2)),
                after=dict(src=ex["file"], t=[a, b], sha256=ex["sha256"]), tease=dict(src=ex["file"], t=[ts, ts + 1.3]),
                body=dict(src=ex["file"], t=[0.0, round(body, 3)]))


def export_landings(script):
    """What the final export's own edit lands over its speaker, on the export's seconds: every picture (overlays and
    b-roll) and every designed title of its EDL, [{t, kind: picture|title}]. [] when the edit list cannot be read."""
    try:
        rec = json.load(open(script["record"]))
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return []
    rows = [dict(t=round(float(o["start"]), 3), kind="picture") for o in D.MF.pictures_of(edl)]
    rows += [dict(t=round(float(o["start"]), 3), kind="title") for o in D.MF.titles_of(edl)]
    return sorted(rows, key=lambda r: (r["t"], r["kind"]))


def landings_in(lands, t0, t1):
    """The export's landings inside one cut of it [t0, t1) on that cut's own seconds (the sound design's events)."""
    return [dict(r, t=round(float(r["t"]) - float(t0), 3)) for r in lands or [] if float(t0) <= float(r["t"]) < float(t1)]


def splice(ctx, narr_dir, foot_dir, voices):
    """The recording cut into the narration between two lines (_shell/narrate/realvoice.py), at the narrator's level."""
    vp = os.path.join(ctx.run_dir, "splice_voices.json")
    jsave(vp, voices)
    code = ("import json,sys; sys.path.insert(0, sys.argv[4]); import realvoice; "
            "print(json.dumps(realvoice.splice(sys.argv[1], json.load(open(sys.argv[3])), sys.argv[2])))")
    nd = os.path.dirname(PX.NARRATE)
    r = subprocess.run([PX.NARRATE_PY, "-c", code, narr_dir, foot_dir, vp, nd], cwd=nd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"splice failed: {r.stderr[-600:]}")
    placed = json.loads(r.stdout.strip().splitlines()[-1])
    jsave(os.path.join(narr_dir, "footage_voices.json"), placed)
    lv = subprocess.run([PX.NARRATE_PY, os.path.join(STORIES, "voice.py"), "level-voices", narr_dir], capture_output=True, text=True, timeout=600)
    ctx.log(f"cut-ins levelled: {((lv.stdout or lv.stderr).strip().splitlines() or [''])[-1][:200]}")
    return placed


def photo_briefs(ctx, named):
    """Image libraries need subject queries, not the documentary video's search sentence."""
    path = os.path.join(ctx.supply, "photo_plan.json")
    cached = jload(path, {}) or {}
    if cached.get("subjects") == named:
        return cached["briefs"], []
    result, use = ask(
        "Translate these exact visual subjects into image-library search queries for Wikimedia Commons and Openverse. "
        "Use two or three concrete queries per subject, two to four words each. Preserve the actual object or action; "
        "never replace the requested thing with a generic mood or change its identity. Do not use video-platform terms "
        "such as tutorial, stock footage or 4K. Do not claim these searches guarantee a match: the existing must_show "
        "and per-line frame checks remain binding. Return JSON {subjects:[{name:exact original name, searches:[...]}]}.",
        json.dumps(named, ensure_ascii=False), what="photo search plan", effort="low", max_tokens=3000)
    by_name = {r.get("name"): r.get("searches") for r in result.get("subjects", []) if isinstance(r, dict)}
    briefs = []
    for subject in named:
        queries = by_name.get(subject["name"])
        if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
            raise RuntimeError(f"photo search plan omitted {subject['name']}")
        briefs.append(dict(subject, searches=queries[:3]))
    jsave(path, dict(subjects=named, briefs=briefs))
    return briefs, [use]


def recording_source(script, role):
    """Carry the authenticated recording's identity into the ordinary visual matcher."""
    receipts = script["receipts"]
    title = "THE UPLOAD — original clip uploaded to Valmera" if role == "upload" else "THE EXPORT — actual Valmera export"
    return dict(url="valmera studio recording", title=title,
                description=f"Recorded synthetic Studio session. {role} SHA256: {receipts[role]['sha256']}. "
                            f"Exact submitted request: {receipts['typed']}")


def owed_windows(lines, candidates, out, used, segments):
    """A line that names THE UPLOAD or THE EXPORT shows it. The frame choice above may leave such a line with none
    (it took zero to three a line); the line then gets the unused frames of its named source that lie farthest from
    those already shown, about one a second of the line, so the proof on screen is the recording, not a type page."""
    added = []
    for ln in lines:
        have = sum(1 for o in out + added if o["line"] == ln["i"])
        want = max(1, min(3, int(round(float(segments[ln["i"]].get("duration_s") or 1.0)))))
        for name in ln["sources"]:
            while have < want:
                free = [c for c in candidates if c["source"] == name and c["id"] not in used]
                if not free:
                    break
                shown = [o["center"] for o in out + added if o["source"] == name]
                c = max(free, key=lambda c: (min((abs(c["center"] - t) for t in shown), default=1e9), -c["center"]))
                added.append(dict(c, line=ln["i"], why="the line names this recording; frame owed to it"))
                used.add(c["id"])
                have += 1
    return added


def product_windows(ctx, script, sources):
    """Choose source moments from frames for the words, not an arithmetic walk through the export."""
    plan_path = os.path.join(ctx.supply, "product_windows.json")
    old = jload(plan_path)
    if old:
        return old["windows"], old.get("spend", [])
    lines = [dict(i=i, say=seg["say"], see=seg.get("see"), sources=[n for n in seg.get("pictures", []) if n in sources])
             for i, seg in enumerate(script["script"]["segments"]) if any(n in sources for n in seg.get("pictures", []))]
    if not lines:
        return [], []
    content = [dict(type="text", text=json.dumps(dict(lines=lines, receipts=script["receipts"]), ensure_ascii=False))]
    candidates = []
    for name, (path, duration, busy) in sources.items():
        times = [0.6 + i for i in range(max(0, int(duration - 1.1) + 1))]
        for t in times:
            if any(t + .5 > a and t - .5 < b for a, b in busy):
                continue
            frame = still(path, t, w=540)
            if not frame:
                continue
            candidate = dict(id=len(candidates), source=name, center=t, start=round(t-.5, 3), end=round(t+.5, 3))
            candidates.append(candidate)
            content.append(dict(type="text", text=f"Candidate {candidate['id']}: {name} at {t:.2f}s"))
            content.append(img(frame))
    content.append(dict(type="text", text='Return JSON {"windows":[{"line": index, "candidate": id, "why": visible match}]}.'))
    result, use = ask(
        "Select real upload/export pictures for each marketing narration line from the labelled frames. "
        "Use zero to three candidates per line, only when the exact stated operation or result is visible. "
        "Show lamps for lamp claims and captions for caption claims; a speaker alone is not proof of B-roll. "
        "These files are authenticated Studio recordings, but their provenance does not make every moment fit. "
        "Never assign one candidate or one visual shot to different words. Adjacent frames of the same "
        "lamp, chair or speaker shot are one picture, not separate choices. Preserve distinct moments and do not invent an interface. "
        "The mandatory before/after real-voice passages are already reserved; candidates exclude them.",
        content, what="product source windows", model=CHECK_MODEL, effort="low", max_tokens=3000)
    out, used = [], set()
    by_line = {ln["i"]: ln for ln in lines}
    for row in result.get("windows", []):
        i, k = row.get("line"), row.get("candidate")
        if i not in by_line or not isinstance(k, int) or not 0 <= k < len(candidates) or k in used:
            continue
        c = candidates[k]
        if c["source"] not in by_line[i]["sources"]:
            continue
        out.append(dict(c, line=i, why=row.get("why")))
        used.add(k)
    out += owed_windows(lines, candidates, out, used, script["script"]["segments"])
    jsave(plan_path, dict(windows=out, spend=[use]))
    return out, [use]


def supply(ctx, script):
    sc = script["script"]
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
    if sc.get("format") == "showcase":
        return showcase_supply(ctx, script, sup_dir, pk)
    spend = []
    env = dict(os.environ, TMPDIR=ctx.tmp)
    narr_dir = os.path.join(sup_dir, "narration")
    nar = PX.narrate(writer.narration_script(sc, "03"), narr_dir, band="03", label=f"wf2-narrate-{ctx.run_id}", env=env,
                     reader=os.path.join(STORIES, "voice.py"))
    ctx.log(f"narration {nar['duration']} s, gate {nar['gate']}")
    pack = dict(sid=ctx.sid, words=nar["words"], narration=nar["wav"], ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[],
                work=os.path.join(sup_dir, "finish"))
    used = {n for s in sc["segments"] for n in s.get("pictures") or []}
    named = [n for n in sc.get("named") or [] if n.get("name") in used]
    if named:                    # free photographs are the fallback; the film is cut from footage, so too few is no stop
        inventory = jload(os.path.join(sup_dir, "visual_inventory", "inventory.json"))
        if inventory:
            got = {n: rows for n, rows in inventory["pictures"].items() if n in used}
            reports, usd = inventory["reports"], 0.0
        else:  # a resumable script written before inventory-first acquisition
            photo_named, photo_spend = photo_briefs(ctx, named)
            spend += photo_spend
            got, reports, usd = PX.pictures(photo_named, os.path.join(sup_dir, "pics"), per=4)
        jsave(os.path.join(sup_dir, "photo_reports.json"), reports)
        ctx.log(f"photographs: {sum(len(v) for v in got.values())} across {len(named)} subjects")
        spend.append(dict(stage="supply", provider="openai", model="picture check", what="photographs", usd=usd))
        for recs in got.values():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
        pack["pictures"] = {k: v for k, v in got.items() if v}
        pack["credits"] = [dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
                           for v in got.values() for r in v]
        pack["missing"] = [n["name"] for n in named if not got.get(n["name"])]
    words_all = json.load(open(nar["words"]))["words"]
    durs = chain.seg_durs(words_all, len(sc["segments"]))
    durs = [round(float(x) * FOOTAGE_MORE, 2) for x in durs] if durs else durs
    anchors = {k: [r["path"] for r in v if r.get("path")] for k, v in (pack.get("pictures") or {}).items()}
    own = ("THE UPLOAD", "THE EXPORT")
    fscript = json.loads(json.dumps(script))         # the footage engine searches the web; the recording's own names are ours
    for seg in fscript["script"]["segments"]:
        seg["pictures"] = [n for n in seg.get("pictures") or [] if n not in own]
        seg["see"] = [v for v in seg.get("see") or [] if v.get("thing") not in own]
    search_lines = [i for i, seg in enumerate(fscript["script"]["segments"]) if seg.get("pictures")]
    # Illustrative footage is silent. The two real-voice passages come only
    # from our verified Studio recording, so source transcripts are unused.
    reel_kw = dict(preserve_source=True, shot_s=0.55, line_max=12, per=4, hear_s=0.0)
    foot = chain.footage(ctx, fscript, durs, anchors=anchors, lines=search_lines, **W3.footage_kw(reel_kw)) if search_lines else None
    if foot and foot["counts"]["lines_with_footage"] < len(search_lines) / 2:
        # too little passed: once more with each subject as a plain thing to see (the writer's must_show can be too narrow)
        ctx.log(f"footage: {foot['counts']['lines_with_footage']} of {len(search_lines)} external-footage lines; searching again with plain subjects")
        keep_dir = os.path.join(sup_dir, "footage_first")
        shutil.rmtree(keep_dir, ignore_errors=True)
        os.rename(os.path.join(sup_dir, "footage"), keep_dir)
        for n in fscript["script"].get("named") or []:
            n["must_show"] = f"{n['name']}, plainly visible."
            n["is"] = "person" if n.get("is") == "person" and len(str(n["name"]).split()) <= 3 else "thing"
        foot2 = chain.footage(ctx, fscript, durs, anchors=anchors, lines=search_lines, **W3.footage_kw(reel_kw))
        if foot2 and foot2["counts"]["clips"] > foot["counts"]["clips"]:
            foot = foot2
            shutil.rmtree(keep_dir, ignore_errors=True)
        else:
            shutil.rmtree(os.path.join(sup_dir, "footage"), ignore_errors=True)
            os.rename(keep_dir, os.path.join(sup_dir, "footage"))
    fdir = os.path.join(sup_dir, "footage")
    os.makedirs(fdir, exist_ok=True)
    pack["footage"] = [dict(i=ln["i"], clips=[W3.clip_facts(dict(c, path=os.path.join(fdir, c["file"]))) for c in ln["clips"]])
                       for ln in (foot or {}).get("lines") or []]
    if foot:
        pack["credits"] += [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)", credit=c["channel"],
                                 page=c["url"], time_range=c["time_range"], line=c["line"]) for c in foot["credits"]]
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage", usd=foot["spend"]["usd"]))
        ctx.log(f"footage: {foot['counts']['clips']} clips on {foot['counts']['lines_with_footage']} of {foot['counts']['lines']} lines")
    # the recording: the before and the after cut in with their own sound; a frame of the after may open the film
    pc = product_clips(script, os.path.join(fdir, "product"))
    pr = sc["proof"]
    voices = [dict(after=int(pr["before_after"]), person="before", wav="product/before.wav", file="product/before.mp4", said=None,
                   source=recording_source(script, "upload"), time_range=pc["before"]["t"]),
              dict(after=int(pr["after_after"]), person="after", wav="product/after.wav", file="product/after.mp4", said=None,
                   source=recording_source(script, "export"), time_range=pc["after"]["t"])]
    placed = splice(ctx, narr_dir, fdir, voices)
    if len(placed) != 2:
        raise RuntimeError(f"the recording was not cut into the narration: {placed}")
    lands = export_landings(script)               # the export's own pictures and titles: sound events where a cut of it shows them
    pack["voices"] = [dict(p, clip=W3.clip_facts(dict(path=p["file"], file=os.path.basename(p["file"]), subject=p["person"],
                                                      seconds=round(p["end"] - p["start"], 3),
                                                      **({"landings": landings_in(lands, *pc["after"]["t"])} if p["person"] == "after" else {}))))
                      for p in placed]
    tease = W3.clip_facts(dict(path=os.path.join(fdir, "product", "tease.mp4"), file="product/tease.mp4", subject="valmera export", seconds=1.3,
                               source=recording_source(script, "export"), time_range=pc["tease"]["t"], landings=landings_in(lands, *pc["tease"]["t"])))
    if pack["footage"]:
        pack["footage"][0]["clips"].insert(0, tease)
    else:
        pack["footage"] = [dict(i=0, clips=[tease])]
    # THE UPLOAD / THE EXPORT in a segment's pictures: silent seconds of the real clip and export, outside the cut-ins
    lines = {int(ln["i"]): ln for ln in pack["footage"]}
    rec_ = json.load(open(script["record"]))
    ex_ = next((e for e in rec_["exports"] if e["tag"] == "final"), rec_["exports"][-1])
    srcs = {"THE UPLOAD": (rec_["upload"]["file"], rec_["upload"]["dur"], [pc["before"]["t"]]),
            "THE EXPORT": (ex_["file"], ex_["dur"] - endcard_s(), [pc["after"]["t"], pc["tease"]["t"]])}
    chosen, uses = product_windows(ctx, script, srcs)
    spend += uses
    crops = {os.path.join(fdir, "product", "before.mp4"): pc["before"].get("crop")}
    for k, window in enumerate(chosen):
        name, i = window["source"], window["line"]
        path, _, _ = srcs[name]
        t, end = window["start"], window["end"]
        fn = f"product/{'upload' if name == 'THE UPLOAD' else 'export'}_{k}.mp4"
        cut(path, t, end, os.path.join(fdir, fn), audio=False)
        if name == "THE UPLOAD":
            crops[os.path.join(fdir, fn)] = product_crop(script["record"], rec_, (t + end) / 2)
        c = W3.clip_facts(dict(path=os.path.join(fdir, fn), file=fn, subject=name, seconds=end-t,
                               source=recording_source(script, "upload" if name == "THE UPLOAD" else "export"),
                               time_range=[t, end], **({"landings": landings_in(lands, t, end)} if name == "THE EXPORT" else {})))
        lines.setdefault(i, dict(i=i, clips=[]))["clips"].append(c)
    pack["footage"] = [lines[k] for k in sorted(lines)]
    talk = {}
    for ln in pack["footage"]:
        for c in ln["clips"]:
            t, score = R.talking(c, PX.FF)
            talk[c["path"]] = dict(talking=t, score=score)
    pack["talking"] = talk
    # Use verified takes, not arbitrary slices reused across narration lines.
    W3.measure_clips(pack)
    usd, calls = W3.judge_supply(ctx, sc, pack)
    spend.append(dict(stage="supply", provider="openai", model=CHECK_MODEL,
                      what="per-line picture identity", usd=usd, calls=calls))
    rows = W3.fit_report(pack, sc, durs)
    for attempt in range(2):
        short = [r["i"] for r in rows if r["short"] and fscript["script"]["segments"][r["i"]].get("pictures")]
        if not short:
            break
        for segment in fscript["script"]["segments"]:
            segment["see"] = [v for v in segment.get("see") or [] if v.get("thing") not in own]
        deficit = sum(r["deficit"] for r in rows)
        added = W3.topup_once(ctx, fscript, pack, durs, short, spend, f"footage-proof-{attempt}",
                              anchors, reel_kw, line_max=max(r["deficit"] for r in rows if r["i"] in short))
        rows = W3.fit_report(pack, sc, durs)
        if not added or sum(r["deficit"] for r in rows) >= deficit:
            break
    pack["fit_report"] = dict(lines=rows, deficit=sum(r["deficit"] for r in rows))
    # The export is an opening candidate, not an unconditional override of
    # the hook's meaning. The verified cutter chooses its fitting picture;
    # the full-frame, real-voice payoff remains mandatory above.
    product_dir = os.path.join(fdir, "product")
    pack["product"] = dict(pc, paths=[os.path.join(product_dir, n) for n in sorted(os.listdir(product_dir)) if n.endswith(".mp4")],
                           crops={k: v for k, v in crops.items() if v})
    body = W3.clip_facts(dict(path=os.path.join(product_dir, "export_body.mp4"), file="product/export_body.mp4", subject="THE EXPORT",
                              seconds=pc["body"]["t"][1], source=recording_source(script, "export"), time_range=pc["body"]["t"],
                              landings=landings_in(export_landings(script), *pc["body"]["t"])))
    pack["proof_fill"] = [dict(body, busy=[pc["after"]["t"]])]          # the after plays whole on its own voice
    wj = json.load(open(nar["words"]))
    rec = dict(narration_s=wj["duration"], voice=nar.get("voice"), gate=nar["gate"], pictures=sum(len(v) for v in pack["pictures"].values()),
               subjects=len(pack["pictures"]), missing=pack.get("missing"), spend=spend, product=pc,
               footage=(dict(clips=foot["counts"]["clips"], lines=foot["counts"]["lines_with_footage"], credits=foot["credits"]) if foot else None))
    jsave(pk, dict(pack=pack, record=rec))
    return rec


# ----------------------------------------------------------------- score

def content_pace(pack, sc):
    """The film's own pace: the references' median shot scaled by how fast this narrator speaks against them."""
    ref = ref_pace()
    ws = [w for w in json.load(open(pack["words"]))["words"] if not w.get("voice")]
    talk = sum(max(float(w["end"]) - float(w["start"]), 0) for w in ws)
    wps = len(ws) / max(talk, 1e-3) if ws else ref["wps"]
    aim = min(max(ref["shot_med_s"] * ref["wps"] / max(wps, 1e-3), 0.45), 0.8)
    return round(aim, 3), dict(ref=ref, narrator_wps=round(wps, 2), aim_s=round(aim, 3))


def as_delivered(l1, product_paths, spans=None):
    """Preserve the upload/export comparison: no new captions, section finish or flash over the actual recording.
    spans: only these frame spans (a repaired window that now plays the recording), else every span the recording plays."""
    keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") in product_paths}
    spans = sorted(tuple(s) for s in spans) if spans is not None else \
        sorted(tuple(it["f"]) for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys and it.get("z", 0) <= 1)
    for it in l1["items"]:
        if it.get("pic") in keys:
            it.pop("look", None)
    out = []
    for it in l1["items"]:
        over = [s for s in spans if it["f"][0] < s[1] and it["f"][1] > s[0]]
        if not over or it.get("pic") in keys or it.get("keep_over_proof"):
            out.append(it)                                 # the product's crop drawn on its own upload stays with it
            continue
        if it["k"] == "word":
            continue                                       # neither caption the raw upload nor double-caption its export
        if it["k"] == "flash" and it.get("mode") != "pool":
            continue                                       # a flash frame inside the recording
        if it["k"] == "flash" or (it["k"] == "card" and it.get("z", 0) >= 1):
            a = it["f"][0]
            for s0, s1 in over:                            # the pool and the finish stop at the recording and start after it
                if s0 - a >= 2:
                    out.append(dict(it, id=f"{it['id']}a{a}", f=[a, s0], **({"op": it["op"][:s0 - a]} if isinstance(it.get("op"), list) else {})))
                a = max(a, s1)
            if it["f"][1] - a >= 2:
                out.append(dict(it, id=f"{it['id']}b{a}", f=[a, it["f"][1]],
                                **({"op": it["op"][a - it["f"][0]:]} if isinstance(it.get("op"), list) else {})))
            continue
        out.append(it)
    l1["items"] = out
    return spans


def still(path, t, w=320):
    if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        r = subprocess.run([PX.FF, "-v", "error", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "5", "-f", "image2pipe",
                            "-vcodec", "mjpeg", "-"], capture_output=True, timeout=60)
        return base64.b64encode(r.stdout).decode() if r.stdout else None
    return frames(path, [t], w)[0]


def words_under(clock, f0, f1):
    ws = [w["w"] for w in clock.words if F.Clock.f(w["t1"]) > f0 and F.Clock.f(w["t0"]) < f1]
    return " ".join(ws)


def picture_key(p, words):
    return json.dumps([6, p["path"], p.get("src"), words], ensure_ascii=False)


def picture_check(ctx, beats, clock, product_paths, cache, recording=None, roles=None):
    """Every played source window checked; absent frames/verdicts never pass. roles: {line: beat} (hook, setup, low,
    turn, payoff, close), so the check reads a pain line's real recording as the pain it is."""
    todo = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, *p["f"])
        key = picture_key(p, ws)
        if key not in cache:
            todo.append((key, p, ws))
    spend = []
    system = ("Check each picture against the EXACT words under it in a marketing film for editors. "
              "Return fit=yes only if those words are carried by what the frames actually show. "
              "A celebrity talking head does not illustrate an editing operation or another named company. "
              "A timeline of another app is not Valmera. The uploaded clip and real Valmera export are "
              "valid evidence when the words describe those exact states or their visible editing. "
              "An intentional metaphor must clearly carry these words; generic thematic filler is loose. "
              "Blank, broken, contradictory or unidentified pictures fail. Judge all pictures including "
              "product clips. Authenticated before/after passages speak in the source clip's own voice; "
              "blank narrator words in those passages are not evidence of a blank or mismatched picture. "
              "Use the recording receipts to identify the state and the adjacent narration only to resolve "
              "pronouns or incomplete phrase boundaries. The shown picture must still carry the exact words. "
              "Each picture names its line's role in the story: hook, setup and low are the editor's pain, turn is the "
              "typed request, payoff and close are the result. On a pain line the real recording that shows the problem "
              "itself (the raw upload, or the export where nothing on screen yet illustrates the words) carries those "
              "words: what is missing from the screen is the pain the line names. "
              "Only yes passes; loose and wrong both need another picture.")
    for k in range(0, len(todo), 8):
        batch = todo[k:k + 8]
        content, readable = [], set()
        for n, (key, p, ws) in enumerate(batch, 1):
            src = p.get("src") or [0.0, 0.0]
            times = [src[0] + (src[-1] - src[0]) * q for q in (0.15, 0.5, 0.85)]
            samples = [still(p["path"], t, w=720) for t in times]
            if not all(samples):
                cache[key] = dict(fits=False, why="source window could not be inspected")
                continue
            readable.add(n)
            a, b = p["f"][0] / F.FPS, p["f"][1] / F.FPS
            segs = {w.get("seg") for w in clock.words if w["t1"] > a and w["t0"] < b}
            context = " ".join(w["w"] for w in clock.words if w.get("seg") in segs)
            role = (roles or {}).get(p.get("line"))
            content.append(dict(type="text", text=f"Picture {n}; words: {ws!r}; source role: {p.get('name')!r}; "
                                + (f"line role: {role}; " if role else "") +
                                f"real product recording: {p['path'] in product_paths}; full line for resolving "
                                f"phrase fragments only: {context!r}"))
            if recording and p["path"] in product_paths:
                content.append(dict(type="text", text="Authenticated recording receipts: " + json.dumps(recording, ensure_ascii=False)))
            content.extend(img(b) for b in samples)
        if not readable:
            continue
        content.append(dict(type="text", text='Return JSON: {"pictures": [{"n": number, "fit": "yes"|"loose"|"wrong", "why": "reason"}]}'))
        got, use = ask(system, content, what="picture check", effort="low", model=CHECK_MODEL, max_tokens=3000)
        spend.append(use)
        for v in (got.get("pictures") if isinstance(got, dict) else []) or []:
            try:
                n = int(v["n"])
                if n not in readable:
                    continue
                key = batch[n - 1][0]
            except (KeyError, ValueError, TypeError):
                continue
            fit = str(v.get("fit") or "").lower()
            cache[key] = dict(fits=fit == "yes", fit=fit, why=v.get("why"))
    bad = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, *p["f"])
        v = cache.get(picture_key(p, ws)) or dict(fits=False, why="missing picture verdict")
        if not v["fits"]:
            bad.append((os.path.basename(p["path"]), ws, v.get("why")))
    return bad, spend


# ----------------------------------------------------------------- a window the check judged loose or wrong is repaired

REPAIR_TRIES = 3          # ranked replacements a misfit window tries in turn, each checked on its own frames only
REPAIR_CANDIDATES = 48    # the film's own pictures offered for the misfits in one look


def roles_of(sc):
    return {i: s.get("beat") for i, s in enumerate(sc.get("segments") or [])}


def repair_candidates(pack, sc, lines, cap=REPAIR_CANDIDATES):
    """The pack's own pictures a misfit window may take: the footage and photographs of the misfits' lines first, then
    the real export at a second each (outside the after and the tease it already plays), then the recording's other
    cuts. [{id, path, kind (clip|photo), name, t (window start in the file), seconds, w, h, fps, faces, landings, lines}]."""
    out, seen = [], set()

    def add(c, kind, name, t, line=None):
        path = str(c.get("path"))
        k = (path, round(float(t), 1))
        if k in seen or not os.path.exists(path):
            return
        seen.add(k)
        out.append(dict(path=path, kind=kind, name=name, t=round(float(t), 3), seconds=float(c.get("seconds") or 0.0),
                        w=int(c.get("w") or F.W), h=int(c.get("h") or F.H), fps=float(c.get("fps") or F.FPS), faces=c.get("faces") or [],
                        landings=c.get("landings"), line=line, product=name in ("THE UPLOAD", "THE EXPORT")))
    segs = sc.get("segments") or []
    near, rest = [], []
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            sec = float(c.get("seconds") or 0.0)
            row = (c, "clip", c.get("subject") or c.get("title") or os.path.basename(str(c.get("path"))),
                   max(0.0, sec / 2 - 0.5) if sec > 1.5 else 0.0, int(ln["i"]))
            (near if int(ln["i"]) in lines else rest).append(row)
    for name, recs in (pack.get("pictures") or {}).items():
        on = [i for i, s in enumerate(segs) if name in (s.get("pictures") or [])]
        for r in recs:
            (near if set(on) & set(lines) else rest).append((r, "photo", name, 0.0, on[0] if on else None))
    for row in near:
        add(*row)
    for body in pack.get("proof_fill") or []:
        busy = [tuple(b) for b in body.get("busy") or []] + [tuple((pack.get("product") or {}).get("tease", {}).get("t") or (0, 0))]
        sec = float(body.get("seconds") or 0.0)
        t = 0.3
        while t + 1.0 <= sec:
            if not any(t + 1.0 > a and t < b for a, b in busy):
                add(dict(body), "clip", "THE EXPORT", t)
            t += 1.0
    for row in rest:
        if row[2] in ("THE UPLOAD", "THE EXPORT"):
            add(*row)
    for i, c in enumerate(out):
        c["id"] = i
    return out[:cap]


def choose_replacements(misfits, cands, clock, roles, cache):
    """One look at the misfit windows and the film's own pictures: for each window the candidates that show its exact
    words, best first (up to REPAIR_TRIES). {window index: [candidate ids]}. An unanswered look is asked again once."""
    system = ("Some windows of a marketing film for video editors show a picture that does not carry the exact words under it. "
              "For each window, rank up to three of the film's own candidate pictures that show what those words say, best first. "
              "On a line about the editor's pain (role hook, setup or low) the real recording showing the problem itself "
              "(the raw upload, or the export where nothing on screen yet illustrates the words) carries the words. The real "
              "Valmera export carries every line about the result. A candidate that shows something unrelated to the words "
              "is left out; an empty list is right when none carries them.")
    content = [dict(type="text", text="The candidates (the film's own footage, photographs and its real recording):")]
    for c in cands:
        b = still(c["path"], c["t"] + min(0.5, max(c["seconds"] - c["t"], 0.0) / 2), w=360)
        if b:
            content += [dict(type="text", text=f"Candidate {c['id']}: {c['name']}" + (" (the real Valmera recording)" if c["product"] else "")
                             + (f" at {c['t']:.1f} s" if c["kind"] == "clip" else "")), img(b)]
    for n, p in enumerate(misfits):
        ws = words_under(clock, *p["f"])
        v = cache.get(picture_key(p, ws)) or {}
        src = p.get("src") or [0.0, 0.0]
        b = still(p["path"], (src[0] + src[-1]) / 2, w=360)
        content.append(dict(type="text", text=f"Window {n}: words {ws!r}; line role: {roles.get(p.get('line'))}; it shows "
                                              f"{p.get('name')!r}, judged {v.get('fit')}: {v.get('why')}"))
        if b:
            content.append(img(b))
    content.append(dict(type="text", text='Return JSON: {"windows": [{"n": window, "ranked": [candidate ids, best first]}]}'))
    got, spend = {}, []
    for _ in range(2):
        try:
            got, use = ask(system, content, what="picture repair", effort="low", model=CHECK_MODEL, max_tokens=3000)
            spend.append(use)
            break
        except Exception as e:  # noqa: BLE001 - an unanswered look is asked again; then the windows keep their pictures
            spend.append(dict(stage="picture repair", fault=repr(e)[:200]))
    ok_ids = {c["id"] for c in cands}
    out = {}
    for row in (got.get("windows") if isinstance(got, dict) else []) or []:
        try:
            n = int(row["n"])
            ids = [int(x) for x in row.get("ranked") or [] if int(x) in ok_ids]
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= n < len(misfits):
            out[n] = [i for i in ids if cands[i]["path"] != misfits[n]["path"]][:REPAIR_TRIES]
    return out, spend


def _asset_key(l1, c):
    """The L1 asset that plays candidate c: the one already on its file, else a new one."""
    assets = l1.setdefault("assets", {})
    for k, a in assets.items():
        if a.get("path") == c["path"]:
            return k
    k = f"rp{len(assets):02d}"
    if c["kind"] == "photo":
        assets[k] = dict(kind="image", path=c["path"], w=c["w"], h=c["h"])
    else:
        assets[k] = dict(kind="video", path=c["path"], w=c["w"], h=c["h"], fps=c["fps"], dur=c["seconds"])
        if c.get("landings"):
            assets[k]["landings"] = [dict(x) for x in c["landings"]]
    return k


def swap_in(l1, p, items, c):
    """The window's own items (`items`: the originals as built) playing candidate c over the same frames: same frames,
    same layer and finish, the new picture covering the item's box (its framing moving when the original's moved)."""
    key = _asset_key(l1, c)
    f0 = int(p["f"][0])
    (fx, fy), _ = F.focus_of(c)
    out = []
    for o in items:
        it = json.loads(json.dumps(o))
        it["pic"], it["repair"] = key, True
        a, b = int(it["f"][0]), int(it["f"][1])
        if c["kind"] == "clip":
            t0 = c["t"] + (a - f0) / F.FPS
            t1 = c["t"] + (b - f0) / F.FPS
            if c["seconds"] and t1 > c["seconds"]:
                t0, t1 = max(0.0, t0 - (t1 - c["seconds"])), c["seconds"]
            it["src"] = [round(t0, 4), round(t1, 4)]
        else:
            it.pop("src", None)
        if it["k"] == "card":
            w, h = it.get("w"), it.get("h")
            w = w[-1] if isinstance(w, list) else w
            h = h[-1] if isinstance(h, list) else h
            asp = float(w or 1.0) * F.W / max(float(h or 1.0) * F.H, 1e-6)
        else:
            asp = F.W / F.H
        base = [F.r4(v) for v in F.cover_crop(float(c["w"]), float(c["h"]), asp, (fx, fy), (0.5, 0.42))]
        crop = it.get("crop")
        it["crop"] = F.push(base, b - a, 0.05, (fx, fy)) if isinstance(crop, list) and crop and isinstance(crop[0], (list, tuple)) else base
        out.append(it)
    ids = {o["id"] for o in items}
    l1["items"] = [it for it in l1["items"] if it.get("id") not in ids] + out
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))


def repair_misfits(ctx, l1, seen, clock, product_paths, cache, recording, pack, sc):
    """Every window the check judged loose or wrong takes the best of the film's own pictures that shows its words, in
    place over the same frames (never dropped, never a type page): the candidates ranked by one look, each tried in
    turn and checked on its own frames only, the first that fits kept. A window none of them fits keeps the best it
    had. Returns (record, spend)."""
    roles = roles_of(sc)

    def verdict(p):
        return cache.get(picture_key(p, words_under(clock, *p["f"]))) or {}
    misfits = [p for p in seen.get("pictures") or [] if verdict(p).get("fit") in ("wrong", "loose")]
    if not misfits:
        return dict(misfits=0, repaired=[]), []
    cands = repair_candidates(pack, sc, {p.get("line") for p in misfits})
    ranked, spend = choose_replacements(misfits, cands, clock, roles, cache) if cands else ({}, [])
    rank = {"yes": 2, "loose": 1, "wrong": 0}
    state = []
    for p in misfits:
        keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") == p["path"]}
        items = [json.loads(json.dumps(it)) for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys
                 and p["f"][0] <= it["f"][0] < p["f"][1]]
        state.append(dict(orig=dict(p), items=items, best=(rank.get(verdict(p).get("fit"), 0), None), tried=[]))
    for attempt in range(REPAIR_TRIES):
        trial = []
        taken = {st["best"][1]["id"] for st in state if st["best"][1] is not None}     # one candidate fills one window
        for n, p in enumerate(misfits):
            st, ids = state[n], ranked.get(n) or []
            if verdict(p).get("fits") or attempt >= len(ids) or not st["items"] or ids[attempt] in taken:
                continue
            c = cands[ids[attempt]]
            taken.add(c["id"])
            swap_in(l1, p, st["items"], c)            # over the window's original items, never over the last try
            p.update(path=c["path"], name=c["name"], kind="clip" if c["kind"] == "clip" else "photo", tier="repair",
                     src=[c["t"], round(c["t"] + (p["f"][1] - p["f"][0]) / F.FPS, 4)] if c["kind"] == "clip" else None)
            st["tried"].append(c["id"])
            trial.append((n, c))
        if not trial:
            break
        _bad, sp = picture_check(ctx, dict(pictures=[misfits[n] for n, _ in trial]), clock, product_paths, cache, recording, roles=roles)
        spend += sp
        for n, c in trial:
            r = rank.get(verdict(misfits[n]).get("fit"), 0)
            if r > state[n]["best"][0]:
                state[n]["best"] = (r, c)
    repaired, product_spans = [], []
    for n, p in enumerate(misfits):
        st = state[n]
        r, c = st["best"]
        ids = {o["id"] for o in st["items"]}
        if c is None:                      # nothing the film has carried these words better: the window keeps its picture
            if st["tried"]:
                l1["items"] = [it for it in l1["items"] if it.get("id") not in ids] + [json.loads(json.dumps(o)) for o in st["items"]]
                p.clear()
                p.update(st["orig"])
            continue
        if not (p["path"] == c["path"] and (p.get("src") or [None])[0] == (c["t"] if c["kind"] == "clip" else None)):
            swap_in(l1, p, st["items"], c)            # the best try was not the last one
            p.update(path=c["path"], name=c["name"], kind="clip" if c["kind"] == "clip" else "photo", tier="repair",
                     src=[c["t"], round(c["t"] + (p["f"][1] - p["f"][0]) / F.FPS, 4)] if c["kind"] == "clip" else None)
        repaired.append(dict(frames=list(p["f"]), was=os.path.basename(st["orig"]["path"]), now=os.path.basename(c["path"]),
                             src=p.get("src"), fit={2: "yes", 1: "loose", 0: "wrong"}[r], words=words_under(clock, *p["f"])))
        if c["path"] in product_paths or c.get("product"):
            product_spans.append(tuple(p["f"]))
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))
    if product_spans:                      # the real recording now plays there: shown as delivered, as everywhere else
        as_delivered(l1, set(product_paths) | {c["path"] for c in cands if c.get("product")}, spans=product_spans)
    return dict(misfits=len(misfits), candidates=len(cands), repaired=repaired), spend


def instruction_panel(l1, clock, sc, typed):
    """The exact submitted sentence remains readable before its real result."""
    i = int(sc["proof"]["after_after"])
    words = clock.seg_words(i)
    if not words:
        raise RuntimeError("the typed instruction has no spoken timing")
    start, end = F.Clock.f(words[0]["t0"]), F.Clock.f(words[-1]["t1"])
    voices = dict(l1["voices"], receipt=dict(font="InterDisplay-Bold.ttf", px=64, track=0),
                  receipt_label=dict(font="InterDisplay-Bold.ttf", px=56, track=1))
    panel = F.Plan(voices, {})
    lines, line = [], []
    for word in typed.split():
        candidate = " ".join(line + [word])
        if line and panel.R.width("receipt", candidate) > 0.74 * F.W:
            lines.append(" ".join(line))
            line = []
        line.append(word)
    if line:
        lines.append(" ".join(line))
    top = 0.53
    bottom = top + (90 + len(lines) * 78) / F.H
    if bottom > 0.80:
        raise RuntimeError("typed instruction cannot fit readably in the receipt panel")
    panel.block([start, end], [0.08, top, 0.92, bottom], "#121A27", z=20, op=0.96)
    panel.word([start, end], D.TYPED_LABEL, "receipt_label", 0.12, top + 54 / F.H,
               ink="#7FE3FF", z=21)
    for k, line in enumerate(lines):
        panel.word([start, end], line, "receipt", 0.12, top + (126 + k * 78) / F.H, z=21)
    for it in panel.items:
        it["id"] = "receipt." + it["id"]
    l1["voices"] = voices
    # The exact sentence takes the caption seat; never stack both versions.
    l1["items"] = [it for it in l1["items"] if not (
        it.get("k") == "word" and it["f"][0] < end and it["f"][1] > start)]
    l1["items"].extend(panel.items)
    return dict(typed=typed, frames=[start, end], lines=lines, minimum_px=56)


def caption_spans(rec, edl, per=3):
    """The export's caption groups on its own clock: the source's caption words inside each kept range, in the
    export's order, `per` words a group. [(start, end)] programme seconds; [] when the source has no captions."""
    src = next((s for s in sources() if s["file"] == (rec.get("source") or {}).get("file")), None)
    if not src:
        return []
    t0, t1 = float(rec["source"]["t0"]), float(rec["source"]["t1"])
    words = [t - t0 for t, _ in json3_words(src["captions"]) if t0 <= t <= t1]
    out, at = [], 0.0
    for k in edl.get("keep") or []:
        a, b = float(k[0]), float(k[1])
        ts = [at + t - a for t in words if a <= t < b]
        for j in range(0, len(ts), per):
            g = ts[j:j + per]
            out.append((round(g[0], 3), round(min(g[-1] + 0.35, at + b - a), 3)))
        at += b - a
    return out


def face_focus(ff, path, t, work, fallback):
    """The centre of the largest face in a video at second t, in picture fractions (YuNet on one frame); fallback
    when no face is found."""
    try:
        st = D.stills(ff, path, [t], work, h=720)
        fs = PX.faces(st[0]["path"]) if st else []
    except Exception:  # noqa: BLE001 - a frame that cannot be read keeps the product's own crop centre
        fs = []
    if not fs:
        return fallback
    b = max(fs, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))["box"]
    return round((b[0] + b[2]) / 2, 4), round((b[1] + b[3]) / 2, 4)


def exports_of_upload(rec, roots):
    return [dict(sid=s["sid"], export=s["export"], dur=s.get("dur")) for s in same_upload(rec, roots)]


def lay_devices(ctx, l1, sc, pack, clock, script, knobs):
    """The film's editing devices on its own beats (devices.py): the typed line as the timeline pull-back or the wipe,
    the triptych on the hook, the count-up grid after the export. A device that cannot be laid leaves the reel as it
    was and the run goes on (the typed line then gets the plain instruction panel); the reason is recorded."""
    out = dict(device=device_of(script, knobs), laid=[], faults=[])
    try:
        rec = json.load(open(script["record"]))
        ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
        edl_b = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
        up = rec["upload"]
        upload = dict(path=up["file"], w=int(up["w"]), h=int(up["h"]), fps=float(up.get("fps") or 30.0), dur=float(up["dur"]))
        edl_a = dict(keep=[[0.0, round(float(up["dur"]), 3)]])        # the upload as it came in: the sentence was typed before any cut
        product_dir = os.path.dirname(pack["product"]["paths"][0])
        body = D.media(pack["ffmpeg"], os.path.join(product_dir, "export_body.mp4"))
        lib = dict(pack.get("sounds") or {})
        lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
        crop = (pack["product"].get("before") or {}).get("crop")
        focus = ((crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2) if crop else (0.5, 0.42)
        after_keys = {k for k, a in (l1.get("assets") or {}).items() if os.path.basename(str(a.get("path"))) == "after.mp4"}
        after_items = sorted((it for it in l1["items"] if it.get("pic") in after_keys and it.get("k") in ("shot", "card")),
                             key=lambda it: it["f"][0])
        if after_items:                     # the reel's own after: its first frame and the export second it shows there
            f_after = int(after_items[0]["f"][0])
            after_end = int(max(it["f"][1] for it in after_items))
            export_at = float(pack["product"]["after"]["t"][0]) + float((after_items[0].get("src") or [0.0])[0])
        else:
            payoff = next(v for v in pack.get("voices") or [] if v.get("person") == "after")
            f_after = F.Clock.f(float(payoff["start"]) + F.LEAD_F / F.FPS)
            after_end = F.Clock.f(float(payoff["end"]) + F.LEAD_F / F.FPS)
            export_at = float(pack["product"]["after"]["t"][0])
        i = int(sc["proof"]["after_after"])
        words = clock.seg_words(i)
        typed = script["receipts"]["typed"]
        wf = D.word_frames(words, typed, F.norm_word)
        f_send = min(max(b for _, b in wf), f_after) if wf else f_after
        f0 = max(F.Clock.f(words[0]["t0"]) - 6, 0) if words else max(f_after - 90, 0)
        work = os.path.join(ctx.supply, "devices")
        # the pull starts on the speaker: the largest face in the upload at the device's first second, measured (YuNet)
        t_first = max(D.src_at(D.program(edl_b)[0], export_at) - (f_after - f0) / F.FPS, 0.0)
        focus = face_focus(pack["ffmpeg"], upload["path"], t_first, work, focus)
        kind = out["device"]
        if kind == "wipe":
            rec_d, f_end = D.wipe(l1, f0=f0, f_send=f_send, f_after=f_after, upload=upload, export=body, edl_b=edl_b, typed=typed,
                                  word_f=wf, export_at=export_at, lib=lib, focus=focus)
        else:
            rec_d, f_end = D.timeline(l1, f0=f0, f_send=f_send, f_after=f_after, upload=upload, export=body, edl_a=edl_a, edl_b=edl_b,
                                      typed=typed, word_f=wf, export_at=export_at, lib=lib, ff=pack["ffmpeg"], work=work, focus=focus,
                                      caption_spans=caption_spans(rec, edl_b), versions=(1, ex.get("edl_version")))
        out["typed_line"] = rec_d["device"]
        out["laid"].append(rec_d)
        ctx.log(f"device {rec_d['device']}: frames {rec_d['frames']}, typed at {rec_d.get('typed_px')} px, {len(rec_d['sounds'])} sounds")
        pw = clock.seg_words(i + 1) if i + 1 < len(sc["segments"]) - 1 else []
        if kind == "wipe" and pw:          # the count-up of the films made from this clip, on the line after the export
            roots = [os.path.dirname(os.path.dirname(script["record"])), os.environ.get("WF2_REUSE_RECORDINGS"),
                     os.path.join(V, ".tmp", "valmera-studio", "records")]
            films = [dict(sid=rec.get("sid"), export=ex["file"], dur=ex["dur"])] + exports_of_upload(rec, roots)
            g0 = max(F.Clock.f(pw[0]["t0"]) - 2, after_end, f_end)
            g1 = min(F.Clock.f(pw[-1]["t1"]) + 4, g0 + 66)
            if len(films) >= 4 and g1 - g0 >= 36:
                tiles = []
                for fm in films[:12]:
                    st = D.stills(pack["ffmpeg"], fm["export"], [max(float(fm.get("dur") or 6.0) - endcard_s(), 1.0) * 0.4], work, h=480)
                    if st:
                        tiles.append(dict(st[0], sid=fm["sid"]))
                g = D.grid(l1, f0=g0, f1=g1, tiles=tiles, lib=lib, upload=upload)
                g["films"] = [dict(sid=fm["sid"], export=os.path.basename(fm["export"])) for fm in films]
                out["laid"].append(g)
        if kind == "triptych" and len(script.get("siblings") or []) >= 2:
            sibs = script["siblings"][:2]
            exs = [dict(media=D.media(pack["ffmpeg"], ex["file"]), edl=edl_b)] + [
                dict(media=D.media(pack["ffmpeg"], s["export"]), edl=json.load(open(s["edl"]))) for s in sibs]
            hw = clock.seg_words(0)
            t1 = max(min((F.Clock.f(hw[-1]["t1"]) + 3) if hw else 75, 90), 45)
            s_at = D.common_second([e["edl"] for e in exs], t1 / F.FPS + 0.2, prefer=D.src_at(D.program(edl_b)[0], export_at), dur=upload["dur"])
            if s_at is not None:
                tr = D.triptych(l1, f0=0, f1=t1, exports=exs, upload=upload, lib=lib, src_s=s_at)
                tr["sentences"] = [typed] + [s["typed"] for s in sibs]
                out["laid"].append(tr)
            else:
                out["faults"].append("triptych: the three exports share no uncut stretch of the upload; the hook stays the reel's")
    except Exception as e:  # noqa: BLE001 - a device never costs the film: the reel stays as built
        out["faults"].append(repr(e)[:400])
        ctx.log(f"device not laid: {repr(e)[:300]}")
    return out


def showcase_score(ctx, script, pack):
    """The showcase's plan: devices.showcase over the recording (no narrator, no picture check: every picture is the
    product's own export, upload, Studio screen or site)."""
    rec = json.load(open(script["record"]))
    up = rec["upload"]
    upload = dict(path=up["file"], w=int(up["w"]), h=int(up["h"]), fps=float(up.get("fps") or 30.0), dur=float(up["dur"]))
    if pack["product"].get("variant") == "scale":
        return scale_score(ctx, script, pack, upload)
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    edl = json.load(open(os.path.join(os.path.dirname(script["record"]), "edl", f"v{int(ex['edl_version']):03d}.json")))
    body = D.media(pack["ffmpeg"], pack["product"]["paths"][0])
    lib = dict(pack.get("sounds") or {})
    lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
    work = os.path.join(ctx.supply, "devices")
    prod = pack["product"]
    focus = face_focus(pack["ffmpeg"], upload["path"], prod["upload_at"], work, (0.5, 0.42))
    ui = prod.get("ui") or ui_shots(rec)
    if not ui:
        raise RuntimeError("the showcase needs the recording's Studio photographs (studio.py, phone view): record it again")
    page = ui.get("site") or dict(path=os.path.join(ui["dir"], ui["typing"][0]), w=ui["w"], h=ui["h"])
    l1, beats = D.showcase(upload=upload, export=body, edl_b=edl, typed=script["receipts"]["typed"], window=prod["window"], hook=prod["hook"],
                           ui=ui, page=page, lib=lib, sid=ctx.sid, focus=focus, lead_f=F.LEAD_F, later=prod.get("later"))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    taste = dict(ok=True, problems=[], min_text_px=min(d["min_text_px"] for d in beats["devices"] if d.get("min_text_px")), sounds=len(l1["sound"]))
    devices = dict(device="studio", format="showcase", laid=beats["devices"], faults=[], typed_line="studio")
    return dict(l1=out, l1_audio=l1_audio, turn_s=round(beats["reveal"][1] / F.FPS, 3), beats={k: v for k, v in beats.items() if k != "look"},
                taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=dict(format="showcase"), pace=beats["pace"],
                recording_spans=[beats["export"]], instruction_panel=dict(typed=script["receipts"]["typed"], device="studio"), devices=devices,
                picture_check=dict(model=None, rounds=[], misfits_left=[], note="showcase: every picture is the recording's own"), spend=[])


def scale_score(ctx, script, pack, upload):
    """The scale film's plan (devices.scale_showcase): every picture is the recording's own (its films, its Studio, its
    site)."""
    prod = pack["product"]
    lib = dict(pack.get("sounds") or {})
    lib.update(PX.sound_facts(("typing_2", "click_1", "tick_1", "whoosh_soft_1")))
    ui = prod["ui"]
    page = ui.get("site") or dict(path=os.path.join(ui["dir"], ui["typing"][0]), w=ui["w"], h=ui["h"])
    tiles = [dict(t, path=t["path"]) for t in prod["tiles"]]
    hero = dict(path=tiles[0]["path"], w=tiles[0]["w"], h=tiles[0]["h"], fps=tiles[0]["fps"], dur=tiles[0]["dur"])
    l1, beats = D.scale_showcase(upload=upload, hero=hero, tiles=tiles, typed=script["receipts"]["typed"], window=prod["window"], ui=ui, page=page,
                                 lib=lib, sid=ctx.sid, lead_f=F.LEAD_F, later=prod.get("later"), sub=prod.get("sub"))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    taste = dict(ok=True, problems=[], min_text_px=min(d["min_text_px"] for d in beats["devices"] if d.get("min_text_px")), sounds=len(l1["sound"]))
    return dict(l1=out, l1_audio=l1_audio, turn_s=round(beats["reveal"][1] / F.FPS, 3), beats={k: v for k, v in beats.items() if k != "look"},
                taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=dict(format="showcase", variant="scale"), pace=beats["pace"],
                recording_spans=[beats["export"]], instruction_panel=dict(typed=script["receipts"]["typed"], device="studio"),
                devices=dict(device="studio", format="showcase", variant="scale", laid=beats["devices"], faults=[], typed_line="studio"),
                picture_check=dict(model=None, rounds=[], misfits_left=[], note="showcase: every picture is the recording's own"), spend=[])


def lay_cta(ctx, l1, sc, pack, clock, script):
    """The call to action on the film's last line, held CTA_S or to the end of the programme: the website itself
    (devices.site, the recording's photograph of valmera.io) when the recording has it, else devices.cta."""
    try:
        words = clock.seg_words(len(sc["segments"]) - 1)
        f0 = max(F.Clock.f(words[0]["t0"]) - 2, 0) if words else max(l1["dur_f"] - int(D.CTA_S * F.FPS), 0)
        body = os.path.join(os.path.dirname(pack["product"]["paths"][0]), "export_body.mp4")
        m = D.media(pack["ffmpeg"], body)
        lib = dict(pack.get("sounds") or {})
        lib.update(PX.sound_facts(("click_1", "whoosh_soft_1", D.CTA_RISER)))
        src0 = max(m["dur"] - (l1["dur_f"] - f0) / F.FPS - 0.2, 0.0)
        ui = ui_shots(json.load(open(script["record"]))) if script.get("record") else None
        if ui and ui.get("site"):
            return D.site(l1, f0=f0, f1=int(l1["dur_f"]), page=ui["site"], export=m, lib=lib, src0=src0)
        return D.cta(l1, f0=f0, f1=int(l1["dur_f"]), export=m, lib=lib, src0=src0)
    except Exception as e:  # noqa: BLE001 - the film keeps its spoken call to action
        ctx.log(f"call to action not laid: {repr(e)[:300]}")
        return dict(device="cta", fault=repr(e)[:300])


def score(ctx, script):
    pack = W3.load_pack(ctx)
    if script["script"].get("format") == "showcase":
        return showcase_score(ctx, script, pack)
    d = W3.direction_of(ctx)
    sc = json.loads(json.dumps(script["script"]))
    aim, pace_rec = content_pace(pack, sc)
    knobs = {k: v for k, v in d.items() if k not in ("script", "why", "alien")}
    knobs.setdefault("aim_s", aim)
    knobs.setdefault("bed", chain.wants_music(ctx))
    knobs.setdefault("require_fit", True)
    knobs["prefer_new_pictures"] = True
    knobs.setdefault("layout", "bleed")
    knobs["full_frame_paths"] = list(pack["product"]["paths"])
    knobs["whole_frame_paths"] = [p for p in pack["product"]["paths"]
                                   if os.path.basename(p) == "before.mp4" or os.path.basename(p).startswith("upload_")]
    knobs["whole_frame_crop"] = dict(pack["product"].get("crops") or {})   # the before pushes into the export's own crop
    knobs["proof_fill"] = list(pack.get("proof_fill") or [])               # a line short of pictures plays the export
    knobs.setdefault("cap_px", CAP_PX)
    after_v = next((v for v in pack.get("voices") or [] if v.get("person") == "after"), None)
    if after_v:   # the payoff is the after: the drop lands on its first picture landing (the export's own), else on its arrival
        knobs["turn_span_s"] = [round(float(after_v["start"]) + F.LEAD_F / F.FPS, 3), round(float(after_v["end"]) + F.LEAD_F / F.FPS, 3)]
    try:          # the call to action holds CTA_S on screen: the programme runs on past the last word for it
        ck = F.Clock(pack["words"], sc)
        last = ck.seg_words(len(sc["segments"]) - 1)
        if last:
            knobs.setdefault("tail_f", max(15, int(D.CTA_S * F.FPS) - (F.Clock.f(ck.dur) - F.Clock.f(last[0]["t0"]))))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    sys.path[:0] = [p for p in W3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    build = R.builder(script["style"]["id"])
    product = set(pack["product"]["paths"])
    clock = F.Clock(pack["words"], sc)
    cache_p = os.path.join(ctx.run_dir, "picture_check.json")
    cache = jload(cache_p, {}) or {}
    drop, spend, rounds = list(knobs.get("drop") or []), [], []
    # One cut. The picture check below reads what plays under which words; a window judged loose or wrong is filled in
    # place with the film's own picture that shows them (repair_misfits). It never drops a picture and rebuilds (16 Nov:
    # four rounds of drops turned 35-47% of two films into flat type pages and still left 25 misfits).
    for rnd in range(1):
        l1, beats = build(sc, pack, dict(knobs, drop=drop))
        taste = F.taste_check(l1, beats)
        if not taste["ok"]:
            for safer in (dict(display=False), dict(display=False, collage=0.0)):
                ctx.log("taste: " + "; ".join(taste["problems"][:4]) + f"; built again with {safer}")
                l1, beats = build(sc, pack, dict(knobs, drop=drop, **safer))
                taste = F.taste_check(l1, beats)
                if taste["ok"]:
                    break
        spans = as_delivered(l1, product)
        devices = lay_devices(ctx, l1, sc, pack, clock, script, knobs)
        devices["cta"] = lay_cta(ctx, l1, sc, pack, clock, script)
        if devices["cta"].get("frames"):
            devices.setdefault("laid", []).append(devices["cta"])
        receipt_panel = (instruction_panel(l1, clock, sc, script["receipts"]["typed"]) if not devices.get("typed_line")
                         else dict(typed=script["receipts"]["typed"], device=devices["typed_line"]))
        spans_dev = [d["frames"] for d in devices.get("laid") or []]
        seen = dict(beats, pictures=[p for p in beats.get("pictures") or []          # a picture a device covers is not seen
                                     if not any(a <= p["f"][0] and p["f"][1] <= z for a, z in spans_dev)])
        bad, sp = picture_check(ctx, seen, clock, product, cache, script.get("receipts"), roles=roles_of(sc))
        spend += sp
        # a window judged loose or wrong is filled in place with the film's own picture that shows its words (round1
        # stock-inventory-wrong-picture-kept: a Marine at a laptop shipped under "my edit is still his face", recorded only)
        repair, sp = repair_misfits(ctx, l1, seen, clock, product, cache, script.get("receipts"), pack, sc)
        spend += sp
        if repair["repaired"]:
            ctx.log(f"picture repair: {len(repair['repaired'])} of {repair['misfits']} loose or wrong windows took another of the film's pictures")
            bad = [(os.path.basename(p["path"]), words_under(clock, *p["f"]), (cache.get(picture_key(p, words_under(clock, *p["f"]))) or
                    dict(why="missing picture verdict")).get("why")) for p in seen.get("pictures") or []
                   if not (cache.get(picture_key(p, words_under(clock, *p["f"]))) or {}).get("fits")]
        jsave(cache_p, cache)
        shown = []
        for picture in seen.get("pictures") or []:
            words = words_under(clock, *picture["f"])
            verdict = cache.get(picture_key(picture, words)) or dict(fits=False, why="missing picture verdict")
            shown.append(dict(file=os.path.basename(picture["path"]), frames=picture["f"], source=picture.get("src"),
                              words=words, **verdict))
        rounds.append(dict(round=rnd, pictures=len(shown), shown=shown, repair=repair,
                           misfits=[dict(file=b, words=w, why=y) for b, w, y in bad]))
        ctx.log(f"picture check: {len(bad)} of {len(seen.get('pictures') or [])} judged loose against their words (recorded)")
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    payoff = next((v for v in pack.get("voices") or [] if v.get("person") == "after"), None)
    turn_s = round(float(payoff["start"]) + F.LEAD_F / F.FPS, 3) if payoff else (round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None)
    if beats.get("turn_by") == "landing" and beats.get("turn_f") is not None:
        turn_s = round(beats["turn_f"] / F.FPS, 3)         # the music's drop and the sound's turn on the same landing
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, beats={k: v for k, v in beats.items() if k != "look"}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction=dict(knobs, drop=drop), pace=pace_rec, recording_spans=spans,
                instruction_panel=receipt_panel, devices=devices,
                picture_check=dict(model=CHECK_MODEL, effort="low", rounds=rounds, misfits_left=rounds[-1]["misfits"]), spend=spend)


def render(ctx, script, sc):
    return W3.render(ctx, script, sc)


def check(ctx, rd, sc):
    if (sc.get("devices") or {}).get("format") == "showcase":
        # The showcase's sound is the speaker's own and the Studio's (owner, 17 Nov: "what does Mark Cuban talking have to do
        # with a music type that's in Turkish series"). The references under a talking edit are speech-led (DUQzZ_kE3O7 music
        # in 9% of the reel, DJrjetsKPHW 33%): no library bed is baked in; the song is the platform's own catalog hit chosen at
        # delivery (platform_music.json, the whole film at a quarter of the voice), as the judge plays under marketing talk.
        ctx.args = dict(ctx.args or {}, music=False)
    r = W3.check(ctx, rd, sc)
    c = r["checks"]
    c["marketing"] = dict(receipts=json.load(open(os.path.join(ctx.run_dir, "script.json"))).get("receipts"),
                          picture_check=sc.get("picture_check"), pace=sc.get("pace"), recording_spans=sc.get("recording_spans"),
                          instruction_panel=sc.get("instruction_panel"), devices=sc.get("devices"))
    left = (sc.get("picture_check") or {}).get("misfits_left") or []
    c["marketing"]["pictures_fit"] = not left
    c["ok"] = bool(c["ok"] and not left)
    return r
