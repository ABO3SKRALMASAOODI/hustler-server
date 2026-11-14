"""Does this picture carry this line? A vision model's verdict per (picture, line), read by code only through its booleans
and its grade.

The owner's rater found 12.9 s of a story film (19%) under words they contradict: another film's characters, the 1993 test
reel under lines about the finished 1995 film, a portrait from a later year under the payoff (lanes/RATINGS/
2026-11-14-WF3-codex.json). The footage and picture engines check a frame against its SUBJECT; a line needs more: the
exact thing, at the story time the line means, in the version it means, and nothing that contradicts the words.

    verdicts(cands, lines)    every candidate picture (a photograph at 1024 px with its Commons title, description and
                              page; a clip as three frames of its window at 720 px with its source title, channel and
                              time range) judged against the lines whose brief names its subject (see.thing equals its
                              subject: field equality, not a word rule) and the line it was found for
    usable(v)                 exact_thing, time_ok and version_ok true, contradicts false, grade 3 or more
    audit(shots, ...)         the same question for a planned or rendered shot: three of its own frames and the exact
                              words under it

One model (gpt-6.1-sol, effort medium, OpenAI only: writer.ask would turn to Anthropic when its key is present), about
eight candidates a call; verdicts are cached in supply/fit.json by (path, line, hash of the line's words).
"""
import base64
import hashlib
import json
import os
import subprocess
import tempfile

import writer

MODEL = "gpt-6.1-sol"
BATCH = 8
PHOTO_PX, FRAME_PX = 1024, 720
BOOLS = ("fits_line", "exact_thing", "time_ok", "version_ok", "contradicts", "live_action", "speaking_on_camera")
GRADE_MIN = 3

SYSTEM = """You are the picture checker of a narrated documentary film cut like the best founder-story reels. Each picture is shown while one line of narration is spoken. A viewer who sees a picture that does not match the words stops trusting the film: another film's characters, a toy, sculpture, ride figure or museum display standing in for the thing itself, a test reel under words about the finished film (or the finished film under words about the test reel), a later building under words about the early days, a portrait from decades later under a line about the founding, a different person.

You get the film's lines, each with its words and the editor's brief ("see": the thing, the story time and version it must be in, what a picture must show, and the scene or event the line describes). Then candidates: each one a photograph or a clip (three frames: start, middle, end), with its source's own title, description, date and channel, and the lines to judge it against.

The brief says what is best; a picture need not be the best to pass. The bar is a careful viewer: would they accept this picture under these words, or would it mislead them? The work itself (the film, the product, the person) illustrates any line about making it, unless the line sets two versions or two times against each other and the picture is the other one. Ignore the film's own treatment of a frame: black and white, grain, a film gate, scanlines, captions or type laid over it, a soft or blurred frame, an end card; judge only what the picture shows.

For each candidate and EACH line listed with it, answer:
- "fits_line": true when a careful viewer would accept this picture under this line.
- "exact_thing": the picture shows the very thing the line is about (that person, that film, that product, that place, or the work itself), not a lookalike, a sequel or another film, a namesake, a toy, sculpture, ride or museum version, a fan remake.
- "time_ok": false only when the picture plainly shows another time than the line means (a later building, a later film's characters, an older or younger person decades away from the moment the line tells); true when a viewer cannot tell.
- "version_ok": false only when the picture plainly is the other version the line contrasts with or rules out (the test reel under words about the finished film, the finished film under words about the test reel, a later model, the later campus); true when nothing sets it apart.
- "contradicts": true when what the picture shows says the opposite of the words (the deliberate version under words about the accident, a crowd under "nobody came", the wrong year printed in the frame).
- "grade": 1-5, how well it carries the line (5: the exact moment the words describe; 3: the right thing, plainly; 1: wrong).
- "live_action": the frames are camera footage of real people or places (not animation, drawings, renders or a still graphic).
- "speaking_on_camera": a person's face is on screen and they are visibly speaking.
- "shows": what the picture plainly shows, at most twelve words.
- "why": at most twenty words.

Judge from what is visible and from the source's own text (a title, a date, a caption names what it is).

Answer with one JSON object only: {"verdicts": [{"c": candidate number, "line": line number, "fits_line": bool, "exact_thing": bool, "time_ok": bool, "version_ok": bool, "contradicts": bool, "grade": int, "live_action": bool, "speaking_on_camera": bool, "shows": "...", "why": "..."}]}"""


def usable(v):
    """The gate: read from the verdict's booleans and grade only."""
    if not isinstance(v, dict):
        return False
    g = v.get("grade")
    return (v.get("exact_thing") is True and v.get("time_ok") is True and v.get("version_ok") is True and v.get("contradicts") is False
            and isinstance(g, (int, float)) and not isinstance(g, bool) and g >= GRADE_MIN)


def say_hash(say):
    return hashlib.sha1(" ".join(str(say or "").split()).encode()).hexdigest()[:10]


def key(path, line, say):
    return f"{path}|{int(line)}|{say_hash(say)}"


# ----------------------------------------------------------------- frames

def _ffmpeg():
    return os.environ.get("SHELL_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"


def _frame(path, t, out, px, crop=None):
    """One frame of a video (or a still) at t seconds, its long side px, cropped first when `crop` ([x0, y0, x1, y1] in
    fractions) is given."""
    vf = []
    if crop:
        x0, y0, x1, y1 = [float(q) for q in crop]
        vf.append(f"crop=iw*{max(x1 - x0, 0.01):.4f}:ih*{max(y1 - y0, 0.01):.4f}:iw*{x0:.4f}:ih*{y0:.4f}")
    vf.append(f"scale='if(gt(iw,ih),{px},-2)':'if(gt(iw,ih),-2,{px})'")
    cmd = [_ffmpeg(), "-v", "error", "-y"]
    if t is not None:
        cmd += ["-ss", f"{max(float(t), 0.0):.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "4", out]
    subprocess.run(cmd, check=True, timeout=60, capture_output=True)
    return out


def clip_times(seconds, src=None):
    a, b = (float(src[0]), float(src[1])) if src else (0.0, float(seconds or 1.0))
    b = max(b, a + 0.05)
    return [a + 0.04 * (b - a), (a + b) / 2, a + 0.96 * (b - a)]


def frames_of(c, work):
    """The candidate's pictures: a photograph once at PHOTO_PX, a clip three times at FRAME_PX."""
    os.makedirs(work, exist_ok=True)
    h = hashlib.sha1(f"{c['path']}|{c.get('src')}|{c.get('crop')}".encode()).hexdigest()[:12]
    if c.get("kind") == "photo":
        return [_frame(c["path"], None, os.path.join(work, f"{h}_p.jpg"), PHOTO_PX, c.get("crop"))]
    return [_frame(c["path"], t, os.path.join(work, f"{h}_{k}.jpg"), FRAME_PX, c.get("crop"))
            for k, t in enumerate(clip_times(c.get("seconds"), c.get("src")))]


def _img(path, detail="high"):
    return {"type": "image", "detail": detail, "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.standard_b64encode(open(path, "rb").read()).decode()}}


# ----------------------------------------------------------------- the model

def _line_text(ln):
    see = [dict(thing=x.get("thing"), as_of=x.get("as_of"), must_show=x.get("must_show"), moment=x.get("moment")) for x in ln.get("see") or []]
    out = f"Line {ln['i']} ({ln.get('beat') or 'line'}, speaks from {ln.get('era') or '?'}): \"{ln.get('say')}\"\n   brief: {json.dumps(see, ensure_ascii=False)}"
    if ln.get("words_under"):
        out += f"\n   words spoken exactly under this shot: \"{ln['words_under']}\""
    return out


def _meta_text(c):
    m = c.get("meta") or {}
    keep = {k: m.get(k) for k in ("title", "description", "caption", "date", "page", "channel", "upload_date", "time_range", "shows") if m.get(k)}
    return json.dumps(keep, ensure_ascii=False)[:900]


def _ask_default(system, content):
    return writer.ask_openai(system, content, effort="medium", max_tokens=16000, what="picture check", model=MODEL)


def _clean(v):
    out = {k: v.get(k) for k in BOOLS if isinstance(v.get(k), bool)}
    g = v.get("grade")
    if isinstance(g, (int, float)) and not isinstance(g, bool):
        out["grade"] = int(round(g))
    out["shows"] = str(v.get("shows") or "")[:160]
    out["why"] = str(v.get("why") or "")[:240]
    return out


def judge(items, lines, ask=None, work=None, log=print, batch=BATCH, detail="high"):
    """[(item index, line, verdict)] and usd. items: [{"path", "kind", "meta", "lines": [line numbers], "frames"?, "src"?,
    "crop"?, "seconds"?}]; lines: {line number: {"i", "say", "see", "era", "beat", "words_under"?}}."""
    ask = ask or _ask_default
    work = work or tempfile.mkdtemp(prefix="fit-")
    out, usd = [], 0.0
    todo = [k for k, it in enumerate(items) if it.get("lines")]
    for b0 in range(0, len(todo), batch):
        part = todo[b0:b0 + batch]
        need = sorted({ln for k in part for ln in items[k]["lines"]})
        content = [{"type": "text", "text": "# Lines\n" + "\n".join(_line_text(lines[ln]) for ln in need if ln in lines) + "\n\n# Candidates"}]
        ok_part = []
        for c_no, k in enumerate(part):
            it = items[k]
            try:
                fr = it.get("frames") or frames_of(it, work)
            except Exception as e:  # noqa: BLE001 - a picture that cannot be read is judged by no one: it is not usable
                log(f"fit: no frames of {os.path.basename(str(it.get('path')))}: {str(e)[:100]}")
                continue
            ok_part.append((c_no, k))
            content.append({"type": "text", "text": f"\nCandidate {c_no}: a {'photograph' if it.get('kind') == 'photo' else 'clip, three frames'}; "
                                                    f"source: {_meta_text(it)}; judge it against lines {sorted(it['lines'])}"})
            content += [_img(p, detail) for p in fr]
        if not ok_part:
            continue
        try:
            text, use = ask(SYSTEM, content)
            usd += float(use.get("usd") or 0)
            got = writer.json_block(text)
        except Exception as e:  # noqa: BLE001 - an unanswered batch leaves its candidates unjudged: not usable
            log(f"fit: a batch of {len(ok_part)} was not judged: {str(e)[:160]}")
            continue
        by_no = dict(ok_part)
        for v in got.get("verdicts") or []:
            if not isinstance(v, dict) or not isinstance(v.get("c"), int) or v["c"] not in by_no:
                continue
            k = by_no[v["c"]]
            ln = v.get("line")
            if not isinstance(ln, int) or ln not in items[k]["lines"]:
                continue
            out.append((k, ln, _clean(v)))
    return out, round(usd, 4)


# ----------------------------------------------------------------- per-line verdicts for the supply

def lines_of(sc):
    return {k: dict(i=k, say=s.get("say"), see=s.get("see") or [], era=s.get("era"), beat=s.get("beat")) for k, s in enumerate(sc["segments"])}


def judged_lines(c, lines):
    """The lines a candidate is judged against: those whose brief names its subject, and the line it was found for."""
    subj = c.get("subject")
    out = {i for i, ln in lines.items() if subj and any(x.get("thing") == subj for x in ln.get("see") or [])}
    if isinstance(c.get("line"), int) and c["line"] in lines:
        out.add(c["line"])
    return sorted(out)


def verdicts(cands, sc, cache_path=None, ask=None, work=None, log=print):
    """({path: {line: verdict}}, usd, calls): every candidate judged against its lines; cached by (path, line, words)."""
    lines = lines_of(sc)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
        except ValueError:
            cache = {}
    table, items = {}, []
    for c in cands:
        want = judged_lines(c, lines)
        have = {ln: cache[key(c["path"], ln, lines[ln]["say"])] for ln in want if key(c["path"], ln, lines[ln]["say"]) in cache}
        table.setdefault(c["path"], {}).update(have)
        miss = [ln for ln in want if ln not in have]
        if miss:
            items.append(dict(c, lines=miss))
    got, usd = judge(items, lines, ask=ask, work=work, log=log) if items else ([], 0.0)
    for k, ln, v in got:
        table.setdefault(items[k]["path"], {})[ln] = v
        cache[key(items[k]["path"], ln, lines[ln]["say"])] = v
    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        json.dump(cache, open(cache_path, "w"), ensure_ascii=False)
    return table, usd, -(-len(items) // BATCH)


def fits(table):
    """{path: [lines it is usable on]} from a verdict table: what the cutter reads."""
    return {p: sorted(int(ln) for ln, v in (rows or {}).items() if usable(v)) for p, rows in (table or {}).items()}


def candidates(pack):
    """Every picture of a pack as a candidate: photographs by name, clips by the line they were found for."""
    out = []
    for name, recs in (pack.get("pictures") or {}).items():
        for r in recs:
            out.append(dict(path=r["path"], kind="photo", subject=name, line=None,
                            meta=dict(title=r.get("title"), description=r.get("description"), caption=r.get("shows"), page=r.get("page"),
                                      date=r.get("date"))))
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            s = c.get("source") or {}
            out.append(dict(path=c["path"], kind="clip", subject=c.get("subject"), line=int(ln["i"]), seconds=c.get("seconds"),
                            meta=dict(title=s.get("title"), channel=s.get("channel"), upload_date=s.get("upload_date"), page=s.get("url"),
                                      time_range=c.get("time_range"), shows=c.get("shows"))))
    return out


# ----------------------------------------------------------------- the audit of a cut

def words_under(clock_words, f0, f1, fps):
    """The words spoken while frames f0..f1 are on screen (clock words in programme seconds: w, t0, t1)."""
    a, b = f0 / fps, f1 / fps
    return " ".join(w["w"] for w in clock_words if w["t1"] > a + 0.03 and w["t0"] < b - 0.03)


def audit(shots, sc, ask=None, work=None, cache_path=None, log=print, detail="low"):
    """[{"key", "fit", "why", "shows", "grade"}] and usd: each shot ({"key", "path", "kind", "line", "words", "src"?, "crop"?,
    "frames"?, "meta"}) judged against its own segment's brief and the exact words under it. fit is usable(verdict)."""
    lines = lines_of(sc)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
        except ValueError:
            cache = {}
    res, items, ln_tab, usd = {}, [], {}, 0.0
    for sh in shots:
        ln = int(sh["line"])
        ck = hashlib.sha1(json.dumps([sh["path"], sh.get("src"), sh.get("crop"), sh.get("frames"), sh.get("words"), lines[ln]["say"]],
                                     default=str).encode()).hexdigest()[:16]
        if ck in cache:
            res[sh["key"]] = cache[ck]
            continue
        # each shot is its own "line" so the model reads the exact words under it
        vid = 10000 + len(items)
        ln_tab[vid] = dict(lines[ln], i=vid, words_under=sh.get("words") or "")
        items.append(dict(sh, lines=[vid], _ck=ck))
    if items:
        got, usd = judge(items, ln_tab, ask=ask, work=work, log=log, detail=detail)
        for k, _, v in got:
            row = dict(fit=usable(v), why=v.get("why"), shows=v.get("shows"), grade=v.get("grade"),
                       live_action=v.get("live_action"), speaking_on_camera=v.get("speaking_on_camera"))
            res[items[k]["key"]] = row
            cache[items[k]["_ck"]] = row
    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        json.dump(cache, open(cache_path, "w"), ensure_ascii=False)
    out = []
    for sh in shots:
        r = res.get(sh["key"])
        out.append(dict(key=sh["key"], line=sh["line"], f=sh.get("f"), path=sh["path"], judged=r is not None,
                        **(r or dict(fit=False, why="not judged"))))
    return out, round(usd, 4)
