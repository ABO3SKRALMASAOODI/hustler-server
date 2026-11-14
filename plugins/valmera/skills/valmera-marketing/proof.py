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
        references' medians; the recording is shown as delivered (no section finish, flash or caption pool over it); then
        every picture is checked against the exact words it sits under (gpt-6-sol, low) and a picture that does not fit
        is dropped and the plan built again
render  Valmera's engine, then the shell's brand finish and the music it chooses (wf3.render, wf3.check)

Model calls run on OpenAI; nothing is spent on Anthropic. Nothing is published.
"""
import base64
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(HERE)
STORIES = os.path.join(SKILLS, "valmera-stories")
# this folder's own film.py (the three built styles) must not shadow the stories' film.py that reel.py and wf3.py import
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
for p in (os.path.join(SKILLS, "_shell"), STORIES):
    if p not in sys.path:
        sys.path.insert(0, p)
import chain  # noqa: E402
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import reel as R  # noqa: E402
import wf3 as W3  # noqa: E402
import writer  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
G = f"{VV}/week-1106/gauntlet"
HEAVY = f"{VV}/week-1106/heavy.sh"
PY = f"{VV}/venv-worker/bin/python"
SRC_ROOT = os.environ.get("WF2_SOURCES", f"{V}/.tmp/valmera-podcast-shorts")
REFS = f"{V}/reference-library/2026-11-05/02-editing-tutorials-and-app-marketing"
ATLAS = f"{VV}/week-1106/atlas/numbers.json"
CHECK_MODEL = "gpt-6-sol"           # the picture checks: every picture against its words, the export against the sentence
FORM = dict(words=(70, 130), segments=(7, 14), seconds=(30, 55))
# the recording's stack: this week's main, edits on OpenAI (Anthropic spend $0), a paid plan so no wall stands in the way
STACK = {"be": "main", "fe": "main", "model": "gpt-5.5", "plan_models": {}, "planner_model": "off", "env": {"OPENAI_EDITS": "1"},
         "openai_key": True, "trial_per_account_usd": 3.0, "spend_cap_usd": 3, "transcriber": "deepgram", "stock": True,
         "slots": {"agent": 2, "index": 1, "media": 2}}


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


# ----------------------------------------------------------------- ideas

def ideas(ctx):
    able = able_cards(ctx)
    srcs = sources()
    if not srcs:
        raise RuntimeError(f"no source clip with captions under {SRC_ROOT}")
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
              "colour treatment, a title) and say exactly what must be visible for the film to be honest.")
    content = (f"# What editors answered to (the reference films, most comments first)\n{json.dumps(references()[:25], ensure_ascii=False)}\n\n"
               f"# The references' pace (medians)\n{json.dumps(pace)}\n\n"
               f"# Styles this film can take\n" + "\n".join(f"- {s['id']}: {s['look']} Hook: {s['hook']}" for s in able) + "\n\n"
               f"# Films already made (never the same pain with the same clip)\n{json.dumps([r.get('idea') for r in rows[-12:]], ensure_ascii=False)}\n\n"
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
               "greeting, \"style\": one style id, \"why\": one line}.")
    got, use = ask(system, content, what="find ideas", effort="medium", max_tokens=12000)
    ids = {s["id"] for s in able}
    by = {s["id"]: s for s in srcs}
    out = []
    for i in (got.get("ideas") if isinstance(got, dict) else got) or []:
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
    sid = f"m{ctx.run_id[-6:]}-{k}"
    rec_p = os.path.join(studio_root, "records", sid, "record.json")
    if os.path.exists(rec_p):
        return json.load(open(rec_p))
    words = json3_words(next(s["captions"] for s in sources() if s["file"] == idea["file"]))
    t0, t1 = snap_window([w for w in words if idea["t0"] - 20 <= w[0] <= idea["t1"] + 20], float(idea["t0"]), float(idea["t1"]))
    spec = dict(settings=STACK, sessions=[dict(sid=sid, source=idea["file"], t0=t0, t1=t1, plan="plus_2026",
                                                asks=[dict(text=ask_text, phase="before_first_result")], note=idea.get("title"))])
    sp = os.path.join(ctx.run_dir, f"studio_spec_{k}.json")
    jsave(sp, spec)
    run = f"wf2-{ctx.run_id}-{k}"
    env = dict(os.environ, VALMERA_STUDIO=studio_root)
    env.pop("ANTHROPIC_API_KEY", None)
    ctx.log(f"recording {sid}: {idea['source']} {t0}-{t1} s, typed {ask_text!r} (stack {run})")
    t = time.time()
    r = subprocess.run(["zsh", HEAVY, f"wf2-studio-{ctx.run_id}", PY, os.path.join(HERE, "studio.py"), "record", "--spec", sp, "--run", run,
                        "--parallel", "1"], cwd=HERE, env=env, capture_output=True, text=True, timeout=5400)
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


def export_check(ctx, rec, ask_text, must_see):
    """The export against the sentence, element by element, on its own frames (one a second) and the upload's."""
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1] if rec.get("exports") else None)
    if not ex:
        return dict(ok=False, why="no export")
    up = rec["upload"]
    if ex["w"] >= ex["h"]:
        return dict(ok=False, why=f"the export is {ex['w']}x{ex['h']}, not vertical", export=ex)
    n = min(int(ex["dur"]), 40)
    et = [round(0.5 + i * (ex["dur"] - 1) / max(n - 1, 1), 2) for i in range(n)]
    ut = [round(0.5 + i * (up["dur"] - 1) / 9, 2) for i in range(10)]
    content = [dict(type="text", text=f"The sentence typed into the product: {ask_text!r}\nWhat must be visible: {json.dumps(must_see)}\n"
                                      f"First, the clip as uploaded ({up['w']}x{up['h']}, {up['dur']} s), ten frames:")]
    for t, b in zip(ut, frames(up["file"], ut)):
        if b:
            content += [dict(type="text", text=f"upload at {t} s"), img(b)]
    content.append(dict(type="text", text=f"Then the export the product returned ({ex['w']}x{ex['h']}, {ex['dur']} s), a frame a second:"))
    for t, b in zip(et, frames(ex["file"], et)):
        if b:
            content += [dict(type="text", text=f"export at {t} s"), img(b)]
    system = ("You check a product's real output before a marketing film shows it. Be strict and literal: a thing asked for counts as "
              "delivered only if it is plainly visible in the export's frames. You also choose the seconds the film will show.")
    content.append(dict(type="text", text=(
        "Return JSON only: {\"elements\": [{\"asked\": a part of the sentence, \"visible\": true|false, \"seconds\": [export seconds where "
        "it shows], \"how\": what is seen}], \"all_visible\": true|false, \"after\": [start, end] export seconds, 5 to 9 s, where the "
        "asked change reads best (a cut, a picture, captions, a title landing), \"tease_s\": the export second of its most striking "
        "frame, \"before\": [start, end] upload seconds, 2.5 to 4 s, where the raw clip looks most like raw footage, \"looks\": one "
        "plain line on how the export looks to an editor, \"defects\": [anything an editor would call broken: a cut face, wrong "
        "framing, unreadable or misspelled captions, a black frame]}")))
    got, use = ask(system, content, what="export check", effort="low", model=CHECK_MODEL, max_tokens=6000)
    got = got if isinstance(got, dict) else {}
    vis = [e for e in got.get("elements") or [] if isinstance(e, dict)]
    ok = bool(vis) and all(e.get("visible") for e in vis) and bool(got.get("all_visible")) and not got.get("defects")
    return dict(ok=ok, why=None if ok else ("not visible: " + "; ".join(str(e.get("asked")) for e in vis if not e.get("visible"))
                                             + ("; defects: " + "; ".join(map(str, got.get("defects") or [])) if got.get("defects") else "")),
                export=ex, upload=up, **got, spend=[use])


# ----------------------------------------------------------------- the script

def receipts(rec, chk, ask_text):
    ex = chk["export"]
    return dict(typed=ask_text, phase="before the first result", first_result_s=rec.get("first_result_s"), session_s=rec.get("seconds"),
                upload=dict(seconds=rec["upload"]["dur"], w=rec["upload"]["w"], h=rec["upload"]["h"], sha256=rec["upload"]["sha256"]),
                export=dict(seconds=ex["dur"], w=ex["w"], h=ex["h"], sha256=ex["sha256"], edl_version=ex.get("edl_version")),
                visible=[dict(asked=e.get("asked"), how=e.get("how"), seconds=e.get("seconds")) for e in chk.get("elements") or []],
                looks=chk.get("looks"), product=dict(be_sha=rec.get("be_sha"), fe_sha=rec.get("fe_sha"), edit_model=STACK["model"],
                                                     stack="local Gauntlet stack, synthetic account, plan plus_2026"),
                source=rec.get("source"), cost_usd=rec.get("cost_usd"))


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
              "is visible) and whether the receipts support it. General statements about editing that make no claim about Valmera "
              "need no receipt. Return JSON only: {\"claims\": [{\"text\": the words, \"supported\": true|false, \"why\": a few words}]}")
    got, use = ask(system, f"# Receipts\n{json.dumps(rc, ensure_ascii=False)}\n\n# Script\n{json.dumps(sc.get('segments'), ensure_ascii=False)}",
                   what="claims audit", effort="low", model=CHECK_MODEL, max_tokens=4000)
    bad = [c for c in (got.get("claims") if isinstance(got, dict) else []) or [] if isinstance(c, dict) and not c.get("supported")]
    return bad, use


def write(ctx, idea, card, rc, notes=None):
    pace = ref_pace()
    system = ("You are the writer and voice director of Valmera's marketing films for video editors. A synthetic narrator (Phonon Easton, "
              "a calm documentary voice) reads your lines over real footage of what they name, cut fast; between two of your lines the "
              "film cuts to the real recording with its own sound: first the clip as uploaded (the before), later the export Valmera "
              "returned (the after). Write like the best editing accounts talk: concrete, a little dry, no hype words, no exclamation "
              "marks, nothing a viewer cannot check in the film.")
    content = (f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The recording's receipts (the only facts about Valmera you may state)\n"
               f"{json.dumps(rc, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n# The references' pace (medians)\n"
               f"{json.dumps(pace)}\n\n"
               f"# Form\n{FORM['words'][0]}-{FORM['words'][1]} spoken words in {FORM['segments'][0]}-{FORM['segments'][1]} segments; with the "
               f"before (about 3 s) and the after (about 7 s) the film runs {FORM['seconds'][0]}-{FORM['seconds'][1]} s. The first segment is "
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
               "\"pace\": slow|steady|brisk|fast, \"pictures\": names from `named` this segment talks about, in order (two or three a "
               "segment, so the cut can change picture every half second), \"melody\": falling|rising|level|wide, \"emphasis\": [one or "
               "two words of say], \"pause_after\": 0.18 to 0.3 (0.4 where it turns, at most twice), \"direction\": a few words for the "
               "actor, \"era\": 2026, \"collage\": true on the one or two segments that list several things}], \"named\": [{\"name\": what a "
               "camera filmed (an editor at a timeline, a podcast studio, a phone feed of shorts, a named app), \"is\": thing|place|"
               "company|person|event, \"searches\": one to three searches, \"must_show\": what a picture must visibly show to count}], "
               "\"proof\": {\"before_after\": index of the segment the before plays after, \"after_after\": index of the segment the after "
               "plays after (the one that says the typed sentence)}}. Mark one \"low\" (the pain at its worst), one \"turn\", and the "
               "\"payoff\" right after the after.")
    got, use = ask(system, content, what="write", effort="medium", max_tokens=16000)
    return (got if isinstance(got, dict) else {}), use


def script(ctx, ideas_out):
    keep = os.path.join(ctx.run_dir, "script_state.json")
    stt = jload(keep, {}) or {}
    spend = []
    able = {s["id"]: s for s in able_cards(ctx)}
    tries = []
    for idea in ideas_out["ideas"][:2]:
        for k, a in enumerate((idea.get("asks") or [])[:2]):
            tries.append((idea, a, (idea.get("must_see") or [[]] * 2)[k] if k < len(idea.get("must_see") or []) else []))
    tries = tries[:2]                    # two recordings at most a film
    chosen = None
    for k, (idea, ask_text, must) in enumerate(tries):
        tag = f"r{k}"
        if stt.get(tag, {}).get("ok") is False:
            continue
        rec = record(ctx, idea, k, ask_text)
        chk = stt.get(tag, {}).get("check") or export_check(ctx, rec, ask_text, must)
        spend += chk.pop("spend", []) if "spend" in chk else []
        stt[tag] = dict(ok=chk["ok"], check=chk, record=os.path.join(ctx.tmp, "studio", "records", rec["sid"], "record.json"))
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
    notes, sc, bad = ctx.args.get("notes"), None, []
    for attempt in range(4):
        sc, use = write(ctx, idea, card, rc, notes)
        spend.append(use)
        probs = script_problems(sc, ask_text, rc)
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
                record=stt[tag]["record"],
                cta=dict(say=sc["segments"][-1]["say"], show=sc["segments"][-1].get("show"), needs_fulfilment=False),
                words=writer.words_of(sc), spend=spend)


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


def product_clips(script, out):
    """The before and the after as clips with their own sound, and a frame of the after for the hook."""
    rec = json.load(open(script["record"]))
    chk = script["export_check"]
    ex = next((e for e in rec["exports"] if e["tag"] == "final"), rec["exports"][-1])
    up = rec["upload"]
    os.makedirs(out, exist_ok=True)
    a, b = [float(x) for x in (chk.get("after") or [0, min(7.0, ex["dur"])])]
    a, b = quiet_snap(ex["file"], max(a, 0.0)), quiet_snap(ex["file"], min(max(b, a + 4.0), ex["dur"] - 0.1))
    ba, bb = [float(x) for x in (chk.get("before") or [1.0, 4.0])]
    ba, bb = quiet_snap(up["file"], max(ba, 0.0)), quiet_snap(up["file"], min(max(bb, ba + 2.0), up["dur"] - 0.1))
    cut(up["file"], ba, bb, os.path.join(out, "before.mp4"))
    cut(ex["file"], a, b, os.path.join(out, "after.mp4"))
    ts = float(chk.get("tease_s") or (a + b) / 2)
    ts = min(max(ts - 0.6, 0.0), ex["dur"] - 1.4)
    cut(ex["file"], ts, ts + 1.3, os.path.join(out, "tease.mp4"), audio=False)
    return dict(before=dict(src=up["file"], t=[ba, bb], sha256=up["sha256"]), after=dict(src=ex["file"], t=[a, b], sha256=ex["sha256"]),
                tease=dict(src=ex["file"], t=[ts, ts + 1.3]))


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


def supply(ctx, script):
    sc = script["script"]
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
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
        got, reports, usd = PX.pictures(named, os.path.join(sup_dir, "pics"), per=4)
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
    durs = [round(float(x) * W3.FOOTAGE_MORE, 2) for x in durs] if durs else durs
    anchors = {k: [r["path"] for r in v if r.get("path")] for k, v in (pack.get("pictures") or {}).items()}
    foot = chain.footage(ctx, script, durs, anchors=anchors)
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
                   source=dict(url="valmera studio recording: the clip as uploaded"), time_range=pc["before"]["t"]),
              dict(after=int(pr["after_after"]), person="after", wav="product/after.wav", file="product/after.mp4", said=None,
                   source=dict(url="valmera studio recording: the export"), time_range=pc["after"]["t"])]
    placed = splice(ctx, narr_dir, fdir, voices)
    if len(placed) != 2:
        raise RuntimeError(f"the recording was not cut into the narration: {placed}")
    pack["voices"] = [dict(p, clip=W3.clip_facts(dict(path=p["file"], file=os.path.basename(p["file"]), subject=p["person"],
                                                      seconds=round(p["end"] - p["start"], 3)))) for p in placed]
    tease = W3.clip_facts(dict(path=os.path.join(fdir, "product", "tease.mp4"), file="product/tease.mp4", subject="valmera export", seconds=1.3,
                               source=dict(url="valmera studio recording: the export"), time_range=pc["tease"]["t"]))
    if pack["footage"]:
        pack["footage"][0]["clips"].insert(0, tease)
    else:
        pack["footage"] = [dict(i=0, clips=[tease])]
    talk = {}
    for ln in pack["footage"]:
        for c in ln["clips"]:
            t, score = R.talking(c, PX.FF)
            talk[c["path"]] = dict(talking=t, score=score)
    pack["talking"] = talk
    pack["product"] = dict(pc, paths=[os.path.join(fdir, "product", n) for n in ("before.mp4", "after.mp4", "tease.mp4")])
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


def as_delivered(l1, product_paths):
    """The recording is shown as the product delivered it: no section finish, flash or caption pool over its seconds."""
    keys = {k for k, a in (l1.get("assets") or {}).items() if a.get("path") in product_paths}
    spans = sorted(tuple(it["f"]) for it in l1["items"] if it.get("k") in ("shot", "card") and it.get("pic") in keys and it.get("z", 0) <= 1
                   and not it.get("blur"))
    spans = [s for s in spans if s[1] - s[0] >= 30]                # the cut-ins, not the tease's frame
    for it in l1["items"]:
        if it.get("pic") in keys:
            it.pop("look", None)
    out = []
    for it in l1["items"]:
        over = [s for s in spans if it["f"][0] < s[1] and it["f"][1] > s[0]]
        if not over or it.get("pic") in keys:
            out.append(it)
            continue
        if it["k"] == "flash" and it.get("mode") != "pool":
            continue                                       # a flash frame inside the recording
        if it["k"] == "flash" or (it["k"] == "card" and it.get("z", 0) >= 40):
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
    ws = [w["w"] for w in clock.words if F.Clock.f(w["t1"]) > f0 - 4 and F.Clock.f(w["t0"]) < f1 + 4]
    return " ".join(ws)


def picture_check(ctx, beats, clock, product_paths, cache):
    """Every picture against the exact words it sits under (CHECK_MODEL, low). [(basename, words, why)] that do not fit."""
    todo = []
    for p in beats.get("pictures") or []:
        if p["path"] in product_paths and p.get("name") in ("before", "after"):
            continue
        ws = words_under(clock, p["f"][0], p["f"][1])
        key = f"{p['path']}|{ws}"
        if key not in cache:
            t = sum(p["src"]) / 2 if p.get("src") else 0.0
            todo.append((key, p, ws, t))
    spend = []
    system = ("You check a marketing film for video editors before it is published. Each numbered picture is on screen while the "
              "narrator says the words given with it. fits is true when the picture shows what the words say or plainly illustrates "
              "it (an editor at a timeline under words about cutting, a feed of shorts under words about posting). fits is false when it "
              "shows a different specific thing than the words name (another app, person or product), contradicts the words, carries "
              "text or a logo that misleads, is a blank or broken frame, or has nothing to do with the words.")
    for k in range(0, len(todo), 10):
        batch = todo[k:k + 10]
        content = []
        for n, (key, p, ws, t) in enumerate(batch, 1):
            b = still(p["path"], t)
            if b:
                content += [dict(type="text", text=f"Picture {n}, under the words: \"{ws}\""), img(b)]
            else:
                cache[key] = dict(fits=True, why="no frame to read")
        content.append(dict(type="text", text="Return JSON only: {\"pictures\": [{\"n\": number, \"fits\": true|false, \"why\": a few words}]}"))
        got, use = ask(system, content, what="picture check", effort="low", model=CHECK_MODEL, max_tokens=3000)
        spend.append(use)
        for v in (got.get("pictures") if isinstance(got, dict) else []) or []:
            try:
                key = batch[int(v["n"]) - 1][0]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            cache[key] = dict(fits=bool(v.get("fits")), why=v.get("why"))
    bad = []
    for p in beats.get("pictures") or []:
        ws = words_under(clock, p["f"][0], p["f"][1])
        v = cache.get(f"{p['path']}|{ws}")
        if v and not v["fits"] and p["path"] not in product_paths:
            bad.append((os.path.basename(p["path"]), ws, v.get("why")))
    return bad, spend


def score(ctx, script):
    pack = W3.load_pack(ctx)
    d = W3.direction_of(ctx)
    sc = json.loads(json.dumps(script["script"]))
    aim, pace_rec = content_pace(pack, sc)
    knobs = {k: v for k, v in d.items() if k not in ("script", "why", "alien")}
    knobs.setdefault("aim_s", aim)
    knobs.setdefault("bed", chain.wants_music(ctx))
    sys.path[:0] = [p for p in W3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    build = R.builder(script["style"]["id"])
    product = set(pack["product"]["paths"])
    clock = F.Clock(pack["words"], sc)
    cache_p = os.path.join(ctx.run_dir, "picture_check.json")
    cache = jload(cache_p, {}) or {}
    drop, spend, rounds = list(knobs.get("drop") or []), [], []
    for rnd in range(3):
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
        bad, sp = picture_check(ctx, beats, clock, product, cache)
        spend += sp
        jsave(cache_p, cache)
        rounds.append(dict(round=rnd, pictures=len(beats.get("pictures") or []), misfits=[dict(file=b, words=w, why=y) for b, w, y in bad]))
        ctx.log(f"picture check round {rnd}: {len(bad)} of {len(beats.get('pictures') or [])} do not fit their words")
        if not bad or rnd == 2:
            break
        drop += [b for b, _, _ in bad if b not in drop]
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    out = os.path.join(ctx.supply, "l1.json")
    jsave(out, l1)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")
    jsave(l1_audio, F.audio_scene(l1, pack))
    jsave(os.path.join(ctx.supply, "beats.json"), beats)
    turn_s = round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, beats={k: v for k, v in beats.items() if k != "look"}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction=dict(knobs, drop=drop), pace=pace_rec, recording_spans=spans,
                picture_check=dict(model=CHECK_MODEL, effort="low", rounds=rounds, misfits_left=rounds[-1]["misfits"]), spend=spend)


def render(ctx, script, sc):
    return W3.render(ctx, script, sc)


def check(ctx, rd, sc):
    r = W3.check(ctx, rd, sc)
    c = r["checks"]
    c["marketing"] = dict(receipts=json.load(open(os.path.join(ctx.run_dir, "script.json"))).get("receipts"),
                          picture_check=sc.get("picture_check"), pace=sc.get("pace"), recording_spans=sc.get("recording_spans"))
    left = (sc.get("picture_check") or {}).get("misfits_left") or []
    c["marketing"]["pictures_fit"] = not left
    c["ok"] = bool(c["ok"] and not left)
    return r
