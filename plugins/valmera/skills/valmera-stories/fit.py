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
    usable(v)                 fits_line, exact_thing, time_ok and version_ok true, contradicts false (the grade ranks)
    audit(shots, ...)         the same question for a planned or rendered shot: three of its own frames and the exact
                              words under it

One model (gpt-6.1-sol, effort medium, OpenAI only: writer.ask would turn to Anthropic when its key is present), about
eight candidates a call; verdicts are cached in supply/fit.json by (path, take, line, hash of the line's words).

Round 2 (14 Nov): the audit's false rejects (28-31 s of df043a outside the rater's spans) came from the model naming the
same footage differently on each line. Each TAKE (one span between the cuts measured inside a clip; a photograph is one
take) is now identified ONCE (identify: work, version, year, what it depicts, from three frames at high detail and the
source's own title, channel, date and description; cached in supply/identity.json), and that frozen identity is given
as text beside the take's frames to every per-line verdict, so the model only decides whether this identity and these
frames carry this line's brief. The same footage gets the same identity on every line by construction.

Round 4 (15 Nov): the remaining false rejects (about 4.9 s of df043a, measured against the rater's whole verdict: its
four capped defects, the animatronics its dimension notes name and its uncapped note) came from two rules. time_ok was
judged against the line's era even when the line's own words name the earlier state ("Woody was rewritten from tyrant to
leader"), while version_ok was limited to the words: two booleans for one fact, by two standards, flipping run to run.
And grade 3 or more vetoed verdicts the model itself called fitting. Now the model writes the story_time the narration
means at this shot (the words under it, else the line's own words, else its era) and the picture_time, and time_ok and
version_ok are one answer; stand-ins are named by kind; the brief is the wish list, not the test; the gate reads only
the booleans and the grade ranks. On the same frames all four rater spans stay caught (14.77 of 14.8 s), the
animatronics and other films' characters stay rejected, and the reel under the "rewritten" lines passes.
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

SYSTEM = """You are the picture checker of a narrated documentary film cut like the best founder-story reels. Each picture is shown while a few words of narration are spoken. A viewer who sees a picture that does not match the words stops trusting the film. Zero wrong pictures is the bar; a picture need not be the best one to pass.

You get the film's lines: each with its words, its story time ("speaks from"), sometimes the words spoken exactly under this shot, and the editor's brief ("see": the ideal thing, time, version and moment). The brief is the editor's wish list for choosing the BEST picture; it is not the test. Then candidates: a photograph or three frames of one take, with the source's own text and, when given, the take's identity (what it is, which version, which year), settled once from its frames and source for every line it may carry. Take that identity as given.

Ignore the film's own treatment of a frame (black and white, grain, a film gate, scanlines, captions or type laid over it, a soft frame, an end card, a transition frame at the very end of a take); judge what the picture shows.

For each candidate and EACH line listed with it, decide in this order:

1. "exact_thing": the picture is the very thing the narration is about at this point (a thing named in the words under the shot or in the line): that person; that work in any of its own versions (its test reel, storyboards, finished film, trailer, poster, making-of footage of it); that product; that company (its people, its logo, its offices, its own products); that place. False for a stand-in: a toy, sculpture, maquette, statue, ride or park figure, costume character, museum replica, another film's or product's characters, a lookalike, a namesake, a fan remake, a different person.

2. "story_time": the time and version the narration means AT THIS SHOT, found in this order and no further:
   (a) a time or version the words spoken under this shot name ("the finished film", "in 1993", "an early version", "today");
   (b) else a time or version the line's own words name or point back to (a line that says a thing was changed, rewritten, replaced or "from X to Y" names BOTH the before and the after: a picture of either carries it);
   (c) else the line's story time ("speaks from").
   Write it in at most eight words, then "picture_time": the picture's own time and version from its identity and frames, at most eight words.

3. "time_ok": the picture's time and version match story_time, or a viewer could not tell them apart. The finished work itself carries a line about its own making (being pitched, made, stopped, saved, rewritten, resumed) or about its people, unless the words under the shot or the line name another version against it (rule a or b). False when they plainly differ: a later building under the early days, a portrait decades away from the moment, a later film's characters, the test reel under the finished film or the finished film under the test reel. "version_ok": the same answer as time_ok.

4. "contradicts": what the picture shows says the opposite of the words under it (a deliberate act under "an accident", a crowd under "nobody came", the wrong year printed in the frame, a villain under "leader" when the line does not name the villain).

5. "fits_line": a careful viewer would accept this picture under these words: true only when exact_thing and time_ok are true and contradicts is false, and the picture shows the thing plainly enough to be recognised. A plain, recognisable picture of the right thing at the right time passes even when it does not show the brief's ideal moment.

6. "grade": 1-5, how well it carries the words, for ranking only (5: the exact moment the words describe; 3: the right thing plainly; 1: wrong).

Also "live_action" (camera footage of real people or places, not animation, drawings, renders or a still graphic), "speaking_on_camera" (a face on screen visibly speaking), "shows" (what the picture plainly shows, at most twelve words), "why" (at most twenty words).

Answer with one JSON object only: {"verdicts": [{"c": candidate number, "line": line number, "exact_thing": bool, "story_time": "...", "picture_time": "...", "time_ok": bool, "version_ok": bool, "contradicts": bool, "fits_line": bool, "grade": int, "live_action": bool, "speaking_on_camera": bool, "shows": "...", "why": "..."}]}"""


IDENTITY_SYSTEM = """You are the archivist of a narrated documentary film. You get takes: each one a photograph, or three frames from inside one continuous take of a video clip, with its source's own title, channel, upload date, description and time range. For each take, say once and for all what it is, so that every editor who later places it under a line of narration reads the same identity.

For each take answer:
- "work": the film, product, event, broadcast, place or person this take is of, as precisely as the frames and the source allow (e.g. "Toy Story (1995 feature film)", "Toy Story 1993 test reel", "Toy Story Midway Mania ride at Disney's Hollywood Studios", "John Lasseter interview").
- "version": which version or state of it (the finished release, a test reel, concept drawings, a later sequel, a theme-park figure, a toy, a museum display, a fan remake, a news report about it), or null.
- "year": the year the frames were made or show, when the source or the frames say it, else null.
- "depicts": one sentence, what the frames plainly show (who or what, doing what).
- "live_action": the frames are camera footage of real people or places (not animation, drawings, renders or a still graphic).
- "speaking_on_camera": a person's face is on screen and they are visibly speaking.
- "shows": at most twelve words.

Ignore treatment (black and white, grain, captions, a blur): name what the picture is. Say only what the frames and the source's own text support; when the version or year cannot be told, say null rather than guess.

Answer with one JSON object only: {"takes": [{"t": take number, "work": "...", "version": "..." or null, "year": int or null, "depicts": "...", "live_action": bool, "speaking_on_camera": bool, "shows": "..."}]}"""

READ_SYSTEM = """You check a finished vertical documentary film, frame by frame. For each numbered frame you get what the picture under it is (its identity, settled from its source). The film treats its pictures (black and white, a film gate, scanlines, grain, a tilted print over a blurred copy of itself, a crop or a slow zoom): that is intended. Say only whether the subject of the picture is still readable in this frame: could a viewer tell what it shows?

Answer with one JSON object only: {"frames": [{"n": frame number, "readable": bool, "why": "at most twelve words"}]}"""

ID_FIELDS = ("work", "version", "year", "depicts", "live_action", "speaking_on_camera", "shows")


def usable(v):
    """The gate, read from the verdict's booleans only: fits_line and exact_thing, time_ok and version_ok true,
    contradicts false. The grade ranks fitting pictures (reel.py, MOMENT_GRADE); it never vetoes a fitting one."""
    if not isinstance(v, dict):
        return False
    return (v.get("fits_line") is True and v.get("exact_thing") is True and v.get("time_ok") is True and v.get("version_ok") is True
            and v.get("contradicts") is False)


def say_hash(say):
    return hashlib.sha1(" ".join(str(say or "").split()).encode()).hexdigest()[:10]


def key(path, line, say, take=None):
    t = "" if take is None else "|" + take_tag(take)
    return f"{path}{t}|{int(line)}|{say_hash(say)}"


def take_tag(take):
    a, b = take
    return f"{float(a):.3f}-{float(b):.3f}"


def take_key(path, take):
    """The identity cache's key of one take: the path and the take's bounds (a photograph is the take (0, 0))."""
    return hashlib.sha1(f"{path}|{take_tag(take)}".encode()).hexdigest()[:16]


def takes_of(c):
    """A candidate's takes: for a clip, the stretches between the cuts measured inside it (c["cuts"], seconds; the same
    spans as reel.Takes.of, so an identity and a verdict are keyed on the very take the cut plays); a photograph is one
    take (0, 0)."""
    if c.get("kind") == "photo":
        return [(0.0, 0.0)]
    dur = float(c.get("seconds") or 0)
    cuts = sorted(t for t in (c.get("cuts") or []) if 0.15 < float(t) < dur - 0.15)
    edges = [0.0] + [float(t) for t in cuts] + [dur]
    out = [(edges[j], edges[j + 1]) for j in range(len(edges) - 1) if edges[j + 1] - edges[j] >= 0.12]
    return out or [(0.0, dur)]


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
    if os.path.exists(out):
        os.remove(out)
    for back in ((0.0, 0.15, 0.4) if t is not None else (0.0,)):
        cmd = [_ffmpeg(), "-v", "error", "-y"]
        if t is not None:
            cmd += ["-ss", f"{max(float(t) - back, 0.0):.3f}"]
        cmd += ["-i", path, "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "4", out]
        r = subprocess.run(cmd, timeout=60, capture_output=True)
        if r.returncode == 0 and os.path.exists(out):          # a seek past the last frame writes nothing: a little earlier
            return out
    raise RuntimeError(f"no frame of {os.path.basename(str(path))} at {t}")


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


def identity_text(idn):
    if not isinstance(idn, dict):
        return ""
    return json.dumps({k: idn.get(k) for k in ID_FIELDS if idn.get(k) is not None}, ensure_ascii=False)[:600]


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
    for k in ("story_time", "picture_time"):          # the model's own reading of the two times: why time_ok is what it is
        if v.get(k):
            out[k] = str(v[k])[:80]
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
        content = [{"type": "text", "text": "# Lines\n" + "\n".join(_line_text(lines[ln]) for ln in need if ln in lines) + "\n\n# Candidates",
                    "cache_control": {"type": "ephemeral"}}]
        ok_part = []
        for c_no, k in enumerate(part):
            it = items[k]
            try:
                fr = it.get("frames") or frames_of(it, work)
            except Exception as e:  # noqa: BLE001 - a picture that cannot be read is judged by no one: it is not usable
                log(f"fit: no frames of {os.path.basename(str(it.get('path')))}: {str(e)[:100]}")
                continue
            ok_part.append((c_no, k))
            idn = f"; identity (settled once for every line): {identity_text(it['identity'])}" if it.get("identity") else ""
            content.append({"type": "text", "text": f"\nCandidate {c_no}: a {'photograph' if it.get('kind') == 'photo' else 'clip, three frames'}; "
                                                    f"source: {_meta_text(it)}{idn}; judge it against lines {sorted(it['lines'])}"})
            content += [_img(p, detail) for p in fr]
        if not ok_part:
            continue
        try:
            text, use = ask(SYSTEM, content)
            usd += float(use.get("usd") or 0)
            got = writer.json_block(text)
        except Exception as e:  # noqa: BLE001 - an unanswered batch leaves its candidates unjudged: not usable
            __import__("openai_budget").propagate(e)
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
    """The lines a candidate is judged against: those whose brief names its subject or the named thing it was found as
    (c["thing"]: a clip found for a moment or a voice subject of that thing), and the line it was found for."""
    names = {x for x in (c.get("subject"), c.get("thing")) if x}
    out = {i for i, ln in lines.items() if names and any(x.get("thing") in names for x in ln.get("see") or [])}
    if isinstance(c.get("line"), int) and c["line"] in lines:
        out.add(c["line"])
    return sorted(out)


def _load(path):
    if path and os.path.exists(path):
        try:
            return json.load(open(path))
        except ValueError:
            return {}
    return {}


def _save(path, obj):
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(obj, open(path, "w"), ensure_ascii=False)


def _clean_id(v):
    out = {k: (str(v.get(k))[:200] if v.get(k) is not None else None) for k in ("work", "version", "depicts", "shows")}
    y = v.get("year")
    out["year"] = int(y) if isinstance(y, (int, float)) and not isinstance(y, bool) else None
    for k in ("live_action", "speaking_on_camera"):
        if isinstance(v.get(k), bool):
            out[k] = v[k]
    return out


def identify(cands, cache_path=None, ask=None, work=None, log=print, batch=BATCH, detail="high"):
    """({take_key: identity}, usd, calls): every take of every candidate identified ONCE (IDENTITY_SYSTEM: work, version,
    year, depicts, live_action, speaking_on_camera, shows) from three frames inside the take (a photograph once) at high
    detail and the source's own text; cached by take_key(path, take bounds)."""
    ask = ask or _ask_default
    work = work or tempfile.mkdtemp(prefix="fit-id-")
    cache = _load(cache_path)
    todo, seen = [], set()
    for c in cands:
        for take in c.get("takes") or takes_of(c):
            k = take_key(c["path"], take)
            if k in cache or k in seen:
                continue
            seen.add(k)
            todo.append((k, c, take))
    usd, calls = 0.0, 0
    for b0 in range(0, len(todo), batch):
        part = todo[b0:b0 + batch]
        content = [{"type": "text", "text": "# Takes"}]
        ok_part = {}
        for t_no, (k, c, take) in enumerate(part):
            it = dict(c, src=None if c.get("kind") == "photo" else [take[0], take[1]])
            try:
                fr = frames_of(it, work)
            except Exception as e:  # noqa: BLE001 - a take that cannot be read is identified by no one: it is never usable
                log(f"identify: no frames of {os.path.basename(str(c.get('path')))}: {str(e)[:100]}")
                continue
            ok_part[t_no] = k
            kind = "photograph" if c.get("kind") == "photo" else f"clip, three frames inside one take ({take[0]:.2f}-{take[1]:.2f} s of the clip)"
            content.append({"type": "text", "text": f"\nTake {t_no}: a {kind}; source: {_meta_text(c)}"})
            content += [_img(q, detail) for q in fr]
        if not ok_part:
            continue
        calls += 1
        try:
            text, use = ask(IDENTITY_SYSTEM, content)
            usd += float(use.get("usd") or 0)
            got = writer.json_block(text)
        except Exception as e:  # noqa: BLE001 - an unanswered batch leaves its takes unidentified: judged without one
            log(f"identify: a batch of {len(ok_part)} was not answered: {str(e)[:160]}")
            continue
        for v in got.get("takes") or []:
            if isinstance(v, dict) and isinstance(v.get("t"), int) and v["t"] in ok_part:
                cache[ok_part[v["t"]]] = _clean_id(v)
    _save(cache_path, cache)
    out = {}
    for c in cands:
        for take in c.get("takes") or takes_of(c):
            k = take_key(c["path"], take)
            if k in cache:
                out[k] = cache[k]
    return out, round(usd, 4), calls


def verdicts(cands, sc, cache_path=None, ask=None, work=None, log=print, id_cache=None):
    """({path: {take_i: {line: verdict}}}, usd, calls, identities): each take identified once (identify), then judged per
    (take, line) against the lines it may carry, its frozen identity given beside its frames. Verdicts are cached by
    (path, take bounds, line, hash of the line's words)."""
    lines = lines_of(sc)
    if id_cache is None and cache_path:
        id_cache = os.path.join(os.path.dirname(cache_path), "identity.json")
    ids, usd, calls = identify(cands, cache_path=id_cache, ask=ask, work=work, log=log)
    cache = _load(cache_path)
    table, items = {}, []
    for c in cands:
        want = judged_lines(c, lines)
        for ti, take in enumerate(c.get("takes") or takes_of(c)):
            row = table.setdefault(c["path"], {}).setdefault(ti, {})
            miss = []
            for ln in want:
                k = key(c["path"], ln, lines[ln]["say"], take)
                if k in cache:
                    row[ln] = cache[k]
                else:
                    miss.append(ln)
            if miss:
                items.append(dict(c, lines=miss, take_i=ti, take=list(take), src=None if c.get("kind") == "photo" else [take[0], take[1]],
                                  identity=ids.get(take_key(c["path"], take))))
    got, u2 = judge(items, lines, ask=ask, work=work, log=log) if items else ([], 0.0)
    for k_, ln, v in got:
        it = items[k_]
        idn = it.get("identity") or {}
        for b in ("live_action", "speaking_on_camera"):          # the frozen identity's reading, the same on every line
            if isinstance(idn.get(b), bool):
                v[b] = idn[b]
        table.setdefault(it["path"], {}).setdefault(it["take_i"], {})[ln] = v
        cache[key(it["path"], ln, lines[ln]["say"], it["take"])] = v
    _save(cache_path, cache)
    return table, round(usd + u2, 4), calls + -(-len(items) // BATCH), ids


def _rows(rows):
    """{take_i: {line: verdict}} from a table row, the keys as ints (a JSON round trip makes them strings)."""
    out = {}
    for ti, lv in (rows or {}).items():
        if isinstance(lv, dict) and all(isinstance(v, dict) for v in lv.values()):
            out[int(ti)] = {int(ln): v for ln, v in lv.items()}
    return out


def fits(table):
    """{path: {take_i: [lines it is usable on]}} from a verdict table: what the cutter reads."""
    return {p: {ti: sorted(ln for ln, v in lv.items() if usable(v)) for ti, lv in _rows(rows).items()} for p, rows in (table or {}).items()}


def lines_fit(fit_row):
    """Every line any take of a picture is usable on (a fits() row; an older pack's plain list passes through)."""
    if isinstance(fit_row, dict):
        return sorted({int(ln) for v in fit_row.values() for ln in v})
    return sorted(int(ln) for ln in fit_row or [])


def candidates(pack):
    """Every picture of a pack as a candidate: photographs by name, clips by the line they were found for; each with its
    takes (takes_of: its measured inner cuts)."""
    out = []
    for name, recs in (pack.get("pictures") or {}).items():
        for r in recs:
            out.append(dict(path=r["path"], kind="photo", subject=name, line=None, takes=[(0.0, 0.0)],
                            meta=dict(title=r.get("title"), description=r.get("description"), caption=r.get("shows"), page=r.get("page"),
                                      date=r.get("date"))))
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            s = c.get("source") or {}
            d = dict(path=c["path"], kind="clip", subject=c.get("subject"), line=int(ln["i"]), seconds=c.get("seconds"), cuts=c.get("cuts") or [],
                     **({"thing": c["thing"]} if c.get("thing") else {}),
                     meta=dict(title=s.get("title"), channel=s.get("channel"), upload_date=s.get("upload_date"), page=s.get("url"),
                               description=(s.get("description") or "")[:400] or None, time_range=c.get("time_range"), shows=c.get("shows")))
            d["takes"] = takes_of(d)
            out.append(d)
    return out


def readable(items, ask=None, work=None, log=print, batch=12):
    """{key: {"readable", "why"}} and usd: one frame of the rendered film per shot ({"key", "frame": jpg, "identity"}),
    asked only whether its subject still reads in the film's crop or print (READ_SYSTEM)."""
    ask = ask or _ask_default
    out, usd = {}, 0.0
    for b0 in range(0, len(items), batch):
        part = items[b0:b0 + batch]
        content = [{"type": "text", "text": "# Frames"}]
        for n, it in enumerate(part):
            content.append({"type": "text", "text": f"\nFrame {n}: the picture is {identity_text(it.get('identity')) or json.dumps(it.get('meta') or {})[:300]}"})
            content.append(_img(it["frame"], "low"))
        try:
            text, use = ask(READ_SYSTEM, content)
            usd += float(use.get("usd") or 0)
            got = writer.json_block(text)
        except Exception as e:  # noqa: BLE001 - an unanswered batch is not read: its frames count as unreadable
            log(f"readable: a batch of {len(part)} was not answered: {str(e)[:160]}")
            continue
        for v in got.get("frames") or []:
            if isinstance(v, dict) and isinstance(v.get("n"), int) and 0 <= v["n"] < len(part) and isinstance(v.get("readable"), bool):
                out[part[v["n"]]["key"]] = dict(readable=v["readable"], why=str(v.get("why") or "")[:160])
    return out, round(usd, 4)


# ----------------------------------------------------------------- the audit of a cut

def words_under(clock_words, f0, f1, fps):
    """The words spoken while frames f0..f1 are on screen (clock words in programme seconds: w, t0, t1)."""
    a, b = f0 / fps, f1 / fps
    return " ".join(w["w"] for w in clock_words if w["t1"] > a + 0.03 and w["t0"] < b - 0.03)


def audit(shots, sc, ask=None, work=None, cache_path=None, log=print, detail="high"):
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
        ck = hashlib.sha1(json.dumps([sh["path"], sh.get("src"), sh.get("crop"), sh.get("frames"), sh.get("words"), lines[ln]["say"], sh.get("identity")],
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
                       live_action=v.get("live_action"), speaking_on_camera=v.get("speaking_on_camera"),
                       **{k: v[k] for k in ("exact_thing", "time_ok", "contradicts", "story_time", "picture_time") if k in v})
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
