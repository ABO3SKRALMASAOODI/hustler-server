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

20 Nov: an unanswered call is an outage, never a verdict. On 19 Nov the local proxy refused every audit call for about 90
seconds (a daily call cap); each shot without an answer became fit=False "not judged", the gates dropped all 87 planned
shots and 76 supply-approved pictures left the film for designed cards. Now a batch with no answer is asked again after
a pause (ask_batches: RETRY_WAITS, the rest of a pass waiting with it so an outage costs one wait), a shot the audit
still could not judge keeps the supply's own verdict for its take and line (judged=False, never a failure), and a supply
take no model could judge stays usable on the line it was found for (unjudged, ranked after every judged take).
"""
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time

import writer

MODEL = "gpt-6.1-sol"
BATCH = 8
RETRY_WAITS = (30.0, 120.0)      # seconds before each further pass over the batches no answer came back for
PHOTO_PX, FRAME_PX = 1024, 720
BOOLS = ("fits_line", "exact_thing", "time_ok", "version_ok", "contradicts", "live_action", "speaking_on_camera")

SYSTEM = """Read the exact words under this picture in their narrative context. For each requested line
ID, describe in shows what a viewer can see that depicts those specific words. Grade 5 for the exact action
or evidence, 4 for a clear depiction, 1 for an unrelated image. Company affiliation alone does not depict
an action. People must be shown in the role asserted by the phrase. A performer in a commercial portrays
a character; the commercial does not identify that performer as an employee, executive or founder.
For a name, a portrait identifies the person at any age unless the words specify a time of life.
For a change in state, evaluate only the state spoken on these words. A metaphor depicts the physical
sense the brief specifies. Use the source and the settled identity as evidence. Answer JSON
{verdicts:[{c,line,shows,why,grade,exact_thing,time_ok,version_ok,contradicts,fits_line,
story_time,picture_time,meaning_box,passages:[{word_i,box}],live_action,speaking_on_camera}]}. fits_line means the viewer can see the
relationship. Give a normalized meaning_box enclosing the evidence for these words. For a cited
document mark only the actual visible passage(s), with the supplied spoken word_i and source box. The first three checks are booleans about the subject, period and version."""

IDENTITY_SYSTEM = """Identify each numbered source take using its frames and provenance. Identify people
and their role only with evidence. Distinguish performers, subjects of interviews and people doing the
work in question. Return JSON {takes:[{t,work,version,year,vintage,depicts,people:[{name,role}],
meaning_box:[left,top,right,bottom],overlays:[{kind,box}],live_action,speaking_on_camera,shows}]}.
Every box is normalized to the full source image. The meaning box encloses the visible evidence.
Overlays are burned-in timecodes, subtitles, watermarks and channel bugs, located separately from the
subject. vintage is archival, modern or unknown. Record the depicted year when supported by evidence."""

READ_SYSTEM = """You check a finished vertical documentary film, frame by frame. For each numbered frame you get what the picture under it is (its identity, settled from its source). The film treats its pictures (black and white, a film gate, scanlines, grain, a tilted print over a blurred copy of itself, a crop or a slow zoom): that is intended. Say only whether the subject of the picture is still readable in this frame: could a viewer tell what it shows?

Answer with one JSON object only: {"frames": [{"n": frame number, "readable": bool, "why": "at most twelve words"}]}"""

ID_FIELDS = ("work", "version", "year", "vintage", "people", "depicts", "meaning_box", "overlays", "live_action", "speaking_on_camera", "shows")


def usable(v):
    """The gate, read from the verdict's booleans only: fits_line and exact_thing, time_ok and version_ok true,
    contradicts false. The grade ranks fitting pictures (reel.py, MOMENT_GRADE); it never vetoes a fitting one."""
    if not isinstance(v, dict):
        return False
    return (v.get("fits_line") is True and v.get("exact_thing") is True and v.get("time_ok") is True and v.get("version_ok") is True
            and v.get("contradicts") is False)


def carries(v):
    """What the cutter may place: a usable verdict, or a take no model could judge kept on the line it was found for
    (verdicts: {"unjudged": True}); an unanswered call is an outage, not a rejection."""
    return usable(v) or (isinstance(v, dict) and v.get("unjudged") is True)


def ask_batches(batches, ask, log=print, what="fit", waits=None, sleep=None):
    """({batch index: parsed JSON answer}, usd, [batch indices never answered]). batches: [(system, content)]. Each batch
    is asked once; a batch with no answer (a provider error, a cap, a timeout, an unreadable answer) is asked again after
    a pause, in up to len(waits) further passes. When one batch goes unanswered the rest of that pass waits with it for
    the next pass (an outage costs one wait, not one per batch), and the failed batch is asked last in the next pass, so
    one unreadable answer never holds the others back."""
    waits = RETRY_WAITS if waits is None else tuple(waits)
    sleep = sleep or time.sleep
    got, usd = {}, 0.0
    pending = list(range(len(batches)))
    for rnd in range(len(waits) + 1):
        if not pending:
            break
        if rnd:
            log(f"{what}: {len(pending)} batches unanswered; asked again in {waits[rnd - 1]:.0f} s (pass {rnd + 1})")
            sleep(waits[rnd - 1])
        left = []
        for j, bi in enumerate(pending):
            system, content = batches[bi]
            try:
                text, use = ask(system, content)
                usd += float((use or {}).get("usd") or 0)
                parsed = writer.json_block(text)
                if not isinstance(parsed, dict):
                    raise ValueError("the answer is not one JSON object")
                got[bi] = parsed
            except Exception as e:  # noqa: BLE001 - an outage or an unreadable answer: this batch and the rest wait a pass
                __import__("openai_budget").propagate(e)
                log(f"{what}: a batch was not answered (pass {rnd + 1}): {str(e)[:160]}")
                left = pending[j + 1:] + [bi]
                break
        pending = left
    if pending:
        log(f"{what}: {len(pending)} batches stayed unanswered after {len(waits) + 1} passes; their items stay unjudged, never rejected")
    return got, round(usd, 4), pending


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
    see = ln.get("see") or []
    out = f"Line {ln['i']} ({ln.get('beat') or 'line'}, speaks from {ln.get('era') or '?'}): \"{ln.get('say')}\"\n   brief: {json.dumps(see, ensure_ascii=False)}"
    if ln.get("exhibit") and any(b.get("document_fact")==ln["exhibit"].get("fact") for b in see):
        out += "\n   Document passages: " + json.dumps(ln["exhibit"].get("passages") or [])
    if ln.get("words_under"):
        out += f"\n   words spoken exactly under this shot: \"{ln['words_under']}\""
    return out


def identity_text(idn):
    if not isinstance(idn, dict):
        return ""
    return json.dumps({k: idn.get(k) for k in ID_FIELDS if idn.get(k) is not None}, ensure_ascii=False)[:1600]


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
    if valid_box(v.get("meaning_box")):
        out["meaning_box"] = v["meaning_box"]
    out["passages"] = [p for p in v.get("passages") or [] if isinstance(p,dict) and
                       type(p.get("word_i")) is int and valid_box(p.get("box"))]
    for k in ("story_time", "picture_time"):          # the model's own reading of the two times: why time_ok is what it is
        if v.get(k):
            out[k] = str(v[k])[:80]
    return out


def judge(items, lines, ask=None, work=None, log=print, batch=BATCH, detail="high", waits=None, sleep=None, unanswered=None):
    """[(item index, line, verdict)] and usd. items: [{"path", "kind", "meta", "lines": [line numbers], "frames"?, "src"?,
    "crop"?, "seconds"?}]; lines: {line number: {"i", "say", "see", "era", "beat", "words_under"?}}. A batch with no answer
    is asked again (ask_batches); what stays unanswered has no verdict here, and its item indices are added to the list
    `unanswered` when one is given (the callers keep those unjudged; a picture with no frames is not among them)."""
    ask = ask or _ask_default
    work = work or tempfile.mkdtemp(prefix="fit-")
    out = []
    todo = [k for k, it in enumerate(items) if it.get("lines")]
    batches, parts = [], []
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
        batches.append((SYSTEM, content))
        parts.append(ok_part)
    answers, usd, left = ask_batches(batches, ask, log=log, what="fit", waits=waits, sleep=sleep)
    if unanswered is not None:
        unanswered.extend(k for bi in left for _, k in parts[bi])
    for bi, got in sorted(answers.items()):
        by_no = dict(parts[bi])
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
    return {k: dict(i=k, say=s.get("say"), see=s.get("see") or [], era=s.get("era"), beat=s.get("beat"), exhibit=s.get("exhibit")) for k, s in enumerate(sc["segments"])}


def judged_lines(c, lines):
    """The lines a candidate is judged against: those whose brief names its subject or the named thing it was found as
    (c["thing"]: a clip found for a moment or a voice subject of that thing), and the line it was found for."""
    names = {x for x in (c.get("subject"), c.get("thing")) if x}
    out = {i for i, ln in lines.items() if names and any((x.get("thing") in names or x.get("entity") in names) for x in ln.get("see") or [])}
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
        with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(path), delete=False) as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(f.name, path)


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) for v in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def overlay_crop(boxes, subject):
    """Largest axis-aligned part of the source preserving the subject and clearing every mark."""
    if not boxes or not valid_box(subject):
        return None
    rects = [[0, 0, 1, 1]]
    for box in boxes:
        left, top, right, bottom = box
        available = []
        for x, y, r, b in rects:
            if right <= x or bottom <= y or r <= left or b <= top:
                available.append([x, y, r, b])
                continue
            for crop in ([x,y,left,b], [right,y,r,b], [x,y,r,top], [x,bottom,r,b]):
                if crop[0] <= subject[0] and crop[1] <= subject[1] and crop[2] >= subject[2] and crop[3] >= subject[3]:
                    available.append(crop)
        rects = available
    return max(rects, key=lambda r: (r[2]-r[0])*(r[3]-r[1]), default=None)


def overlay_signal(frames, box):
    """Measure stationary glyph strokes and changing strokes in the same source rectangle."""
    import cv2
    import numpy as np
    edges = []
    for path in frames:
        frame = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if frame is None:
            continue
        h, w = frame.shape
        l,t,r,b = [round(n*size) for n,size in zip(box, (w,h,w,h))]
        if r-l > 2 and b-t > 2:
            edges.append(cv2.Canny(frame[t:b,l:r], 60, 150) > 0)
    if len(edges) < 2:
        return {}
    frequency = np.mean(edges, axis=0)
    ink = max(1, int((frequency > 0).sum()))
    return dict(fixed_strokes=round(float((frequency == 1).sum()/ink),3),
                changing_strokes=round(float(((frequency > 0) & (frequency < 1)).sum()/ink),3))


def _clean_id(v):
    out = {k: (str(v.get(k))[:200] if v.get(k) is not None else None) for k in ("work", "version", "depicts", "shows")}
    out.update(vintage=v.get("vintage"), people=[p for p in v.get("people") or [] if isinstance(p, dict)],
               meaning_box=v.get("meaning_box") if valid_box(v.get("meaning_box")) else None,
               overlays=[o for o in v.get("overlays") or [] if isinstance(o, dict) and valid_box(o.get("box"))])
    y = v.get("year")
    out["year"] = int(y) if isinstance(y, (int, float)) and not isinstance(y, bool) else None
    for k in ("live_action", "speaking_on_camera"):
        if isinstance(v.get(k), bool):
            out[k] = v[k]
    return out


def identify(cands, cache_path=None, ask=None, work=None, log=print, batch=BATCH, detail="high", waits=None, sleep=None):
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
            if (k in cache and "overlays" in cache[k]) or k in seen:
                continue
            seen.add(k)
            todo.append((k, c, take))
    batches, parts = [], []
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
            ok_part[t_no] = (k, fr)
            kind = "photograph" if c.get("kind") == "photo" else f"clip, three frames inside one take ({take[0]:.2f}-{take[1]:.2f} s of the clip)"
            content.append({"type": "text", "text": f"\nTake {t_no}: a {kind}; source: {_meta_text(c)}"})
            content += [_img(q, detail) for q in fr]
        if not ok_part:
            continue
        batches.append((IDENTITY_SYSTEM, content))
        parts.append(ok_part)
    calls = len(batches)
    # an unanswered batch is asked again (ask_batches); what stays unanswered is judged without an identity
    answers, usd, _ = ask_batches(batches, ask, log=log, what="identify", waits=waits, sleep=sleep)
    for bi, got in sorted(answers.items()):
        ok_part = parts[bi]
        for v in got.get("takes") or []:
            if isinstance(v, dict) and isinstance(v.get("t"), int) and v["t"] in ok_part:
                key_id, frames = ok_part[v["t"]]
                identity = _clean_id(v)
                for mark in identity["overlays"]:
                    mark["signal"] = overlay_signal(frames, mark["box"])
                identity["clean_crop"] = overlay_crop([m["box"] for m in identity["overlays"]], identity["meaning_box"])
                cache[key_id] = identity
    _save(cache_path, cache)
    out = {}
    for c in cands:
        for take in c.get("takes") or takes_of(c):
            k = take_key(c["path"], take)
            if k in cache:
                out[k] = cache[k]
    return out, round(usd, 4), calls


def verdicts(cands, sc, cache_path=None, ask=None, work=None, log=print, id_cache=None, waits=None, sleep=None):
    """({path: {take_i: {line: verdict}}}, usd, calls, identities): each take identified once (identify), then judged per
    (take, line) against the lines it may carry, its frozen identity given beside its frames. Verdicts are cached by
    (path, take bounds, line, hash of the line's words). A clip's take no model answered for, after the retries, stays on
    the line it was found for as {"unjudged": True} (carries; never cached, so the next call asks again)."""
    parents = lines_of(sc)
    lines, slots = {}, {}
    for i, parent in parents.items():
        words = str(parent["say"] or "").split()
        briefs = sorted(parent["see"] or [{}], key=lambda x: x.get("word_i", 0))
        by_word = {x.get("word_i", 0): x for x in briefs}
        edges = sorted(by_word)
        for j, wi in enumerate(edges):
            stop = edges[j+1] if j+1 < len(edges) else len(words)
            number = len(lines)
            slots[number] = i, wi
            lines[number] = dict(parent, i=number, see=[by_word[wi]], words_under=" ".join(words[wi:stop]))
    def phrase_key(path, ln, take):
        return key(path, ln, json.dumps(lines[ln], sort_keys=True) + "phrase-v1", take)
    if id_cache is None and cache_path:
        id_cache = os.path.join(os.path.dirname(cache_path), "identity.json")
    ids, usd, calls = identify(cands, cache_path=id_cache, ask=ask, work=work, log=log, waits=waits, sleep=sleep)
    cache = _load(cache_path)
    table, items = {}, []
    for c in cands:
        parent_ids = judged_lines(c, parents)
        want = [ln for ln,(i,wi) in slots.items() if i in parent_ids]
        for ti, take in enumerate(c.get("takes") or takes_of(c)):
            row = table.setdefault(c["path"], {}).setdefault(ti, {})
            miss = []
            for ln in want:
                k = phrase_key(c["path"], ln, take)
                if k in cache:
                    row[ln] = cache[k]
                else:
                    miss.append(ln)
            if miss:
                items.append(dict(c, lines=miss, take_i=ti, take=list(take), src=None if c.get("kind") == "photo" else [take[0], take[1]],
                                  identity=ids.get(take_key(c["path"], take))))
    left = []
    got, u2 = judge(items, lines, ask=ask, work=work, log=log, waits=waits, sleep=sleep, unanswered=left) if items else ([], 0.0)
    for k_, ln, v in got:
        it = items[k_]
        idn = it.get("identity") or {}
        for b in ("live_action", "speaking_on_camera"):          # the frozen identity's reading, the same on every line
            if isinstance(idn.get(b), bool):
                v[b] = idn[b]
        table.setdefault(it["path"], {}).setdefault(it["take_i"], {})[ln] = v
        cache[phrase_key(it["path"], ln, it["take"])] = v
    kept = 0
    for k_ in sorted(set(left)):
        # an outage is not a verdict: the footage engine found this clip for its line (its frames checked against the
        # subject there), so with no answer it stays usable on that line, ranked after every judged take (grade 0)
        it = items[k_]
        for ln in it["lines"]:
            if it.get("kind") == "clip" and slots[ln][0] == it.get("line"):
                table[it["path"]][it["take_i"]][ln] = dict(unjudged=True, grade=0)
                kept += 1
    if kept:
        log(f"fit: {kept} takes no model answered for stay on the lines they were found for, unjudged")
    _save(cache_path, cache)
    for path, takes in table.items():
        for ti, verdicts_by_slot in takes.items():
            by_line = {}
            for number, v in verdicts_by_slot.items():
                line, wi = slots[number]
                by_line.setdefault(line, {})[wi] = v
            takes[ti] = {line: dict(max(vs.values(), key=lambda v: (carries(v), v.get("grade", 0))), phrases=vs)
                         for line, vs in by_line.items()}
    return table, round(usd + u2, 4), calls + -(-len(items) // BATCH), ids


def _rows(rows):
    """{take_i: {line: verdict}} from a table row, the keys as ints (a JSON round trip makes them strings)."""
    out = {}
    for ti, lv in (rows or {}).items():
        if isinstance(lv, dict) and all(isinstance(v, dict) for v in lv.values()):
            out[int(ti)] = {int(ln): v for ln, v in lv.items()}
    return out


def fits(table):
    """{path: {take_i: [lines it is usable on]}} from a verdict table: what the cutter reads (carries: a usable verdict,
    or a take no model could judge, on the line it was found for)."""
    return {p: {ti: sorted(ln for ln, v in lv.items() if carries(v)) for ti, lv in _rows(rows).items()} for p, rows in (table or {}).items()}


def supply_says(fit_table, path, line, take_i=None):
    """The supply's own verdict for a picture on a line (a fits() table: {path: {take_i: [lines]}}, or an older plain list
    of lines): True or False, None when the table has no row for the picture."""
    if not fit_table or path not in fit_table:
        return None
    row = fit_table.get(path)
    if not isinstance(row, dict):
        return int(line) in {int(x) for x in row or []}
    rows = {int(t): {int(x) for x in v} for t, v in row.items()}
    if take_i is not None and int(take_i) in rows:
        return int(line) in rows[int(take_i)]
    return any(int(line) in v for v in rows.values())


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
                                      date=r.get("image_date") or r.get("date"))))
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


def readable(items, ask=None, work=None, log=print, batch=12, waits=None, sleep=None, cache_path=None):
    """{key: {"readable", "why"}} and usd: one frame of the rendered film per shot ({"key", "frame": jpg, "identity"}),
    asked only whether its subject still reads in the film's crop or print (READ_SYSTEM). An unanswered batch is asked
    again (ask_batches); a frame still unread has no entry (readable unknown, never a failure)."""
    cache = _load(cache_path)
    known, pending, fingerprints = {}, [], {}
    for item in items:
        if cache_path:
            with open(item["frame"], "rb") as f:
                pixels = hashlib.sha256(f.read()).hexdigest()
            ck = hashlib.sha256(json.dumps([MODEL, READ_SYSTEM, pixels, item.get("identity"), item.get("meta")],
                                          sort_keys=True).encode()).hexdigest()
            fingerprints[item["key"]] = ck
            if ck in cache:
                known[item["key"]] = cache[ck]
                continue
        pending.append(item)
    items = pending
    ask = ask or _ask_default
    out, batches, parts = {}, [], []
    for b0 in range(0, len(items), batch):
        part = items[b0:b0 + batch]
        content = [{"type": "text", "text": "# Frames"}]
        for n, it in enumerate(part):
            content.append({"type": "text", "text": f"\nFrame {n}: the picture is {identity_text(it.get('identity')) or json.dumps(it.get('meta') or {})[:300]}"})
            content.append(_img(it["frame"], "low"))
        batches.append((READ_SYSTEM, content))
        parts.append(part)
    answers, usd, _ = ask_batches(batches, ask, log=log, what="readable", waits=waits, sleep=sleep)
    for bi, got in sorted(answers.items()):
        part = parts[bi]
        for v in got.get("frames") or []:
            if isinstance(v, dict) and isinstance(v.get("n"), int) and 0 <= v["n"] < len(part) and isinstance(v.get("readable"), bool):
                out[part[v["n"]]["key"]] = dict(readable=v["readable"], why=str(v.get("why") or "")[:160])
    if cache_path and out:
        cache.update({fingerprints[k]: v for k, v in out.items()})
        _save(cache_path, cache)
    return {**known, **out}, round(usd, 4)


# ----------------------------------------------------------------- the audit of a cut

def words_under(clock_words, f0, f1, fps):
    """The words spoken while frames f0..f1 are on screen (clock words in programme seconds: w, t0, t1)."""
    a, b = f0 / fps, f1 / fps
    return " ".join(w["w"] for w in clock_words if w["t1"] > a + 0.03 and w["t0"] < b - 0.03)


def audit(shots, sc, ask=None, work=None, cache_path=None, log=print, detail="high", supply=None, waits=None, sleep=None):
    """[{"key", "fit", "judged", "why", "shows", "grade"}] and usd: each shot ({"key", "path", "kind", "line", "words",
    "take_i"?, "src"?, "crop"?, "frames"?, "meta"}) judged against its own segment's brief and the exact words under it;
    fit is usable(verdict). A shot no model answered for, after the retries (judge, ask_batches), is judged=False and
    keeps the supply's own verdict for its take and line (supply: the pack's fits() table; a picture with no row there
    stays as placed): an outage is never a rejection, and the callers read failures only from judged rows."""
    lines = lines_of(sc)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
        except ValueError:
            cache = {}
    res, items, ln_tab, usd = {}, [], {}, 0.0
    for index, sh in enumerate(shots):
        ln = int(sh["line"])
        try:
            stat = os.stat(sh["path"])
            source = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            source = None
        ck = hashlib.sha1(json.dumps([sh["path"], source, detail, sh.get("src"), sh.get("crop"), sh.get("frames"), sh.get("words"), lines[ln], sh.get("identity"), sh.get("meta"), MODEL, SYSTEM],
                                     default=str).encode()).hexdigest()[:16]
        if ck in cache:
            res[index] = cache[ck]
            continue
        # each shot is its own "line" so the model reads the exact words under it
        vid = 10000 + len(items)
        ln_tab[vid] = dict(lines[ln], i=vid, words_under=sh.get("words") or "")
        items.append(dict(sh, lines=[vid], _ck=ck, index=index))
    if items:
        got, usd = judge(items, ln_tab, ask=ask, work=work, log=log, detail=detail, waits=waits, sleep=sleep)
        for k, _, v in got:
            row = dict(fit=usable(v), why=v.get("why"), shows=v.get("shows"), grade=v.get("grade"),
                       live_action=v.get("live_action"), speaking_on_camera=v.get("speaking_on_camera"),
                       **{k: v[k] for k in ("exact_thing", "time_ok", "contradicts", "story_time", "picture_time") if k in v})
            res[items[k]["index"]] = row
            cache[items[k]["_ck"]] = row
    _save(cache_path, cache)
    out, unjudged = [], 0
    for index, sh in enumerate(shots):
        r = res.get(index)
        if r is None:
            says = supply_says(supply, sh["path"], sh["line"], sh.get("take_i"))
            r = dict(fit=says is not False, supply_fit=says, unjudged=True,
                     why="not judged: no model answered after the retries; the supply's verdict for this take and line stands")
            unjudged += 1
        out.append(dict(key=sh["key"], line=sh["line"], f=sh.get("f"), src=sh.get("src"), take_i=sh.get("take_i"), path=sh["path"], judged=not r.get("unjudged"), **r))
    if unjudged:
        log(f"audit: {unjudged} of {len(shots)} shots unanswered after the retries; kept on the supply's verdicts, never dropped")
    return out, round(usd, 4)


def judged_bad(res):
    """The audit rows a model judged and found unfit: the only failures a caller may drop or count (an unjudged row is an
    outage, not a verdict)."""
    return [r for r in res if r.get("judged", True) and not r.get("unjudged") and not r["fit"]]
