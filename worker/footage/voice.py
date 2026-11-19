"""Real voices cut in (lane VO2, 14 Nov 2026): the narrator stops and the person the film is about speaks.

Why. All four critic reels cut in a real voice (judge/metromedia/TEARDOWN.md, W2): Musk's own interview for 25 s of
one film, a podcast host's line in a quote card, broadcast sound over the title goal. Palmer Luckey's opens on his own
voice for 11.7 s. Our narrated films were the narrator only.

How, inside one footage run (run.find, after the sources are open and the person's face is known):
  pick      a model reads the transcripts of the sources that show a named person (interviews, speeches, news) with
            the film's lines and chooses up to MOST sentences the person says in their own voice that belong right
            after a line; it copies the words from the transcript                                       (gpt-6-sol)
  hear      the stretch around the choice is heard again on the Mac's listener with word times (faster-whisper),
            and the chosen words are found in it by string alignment: the cut starts and ends on word edges
  face      frames inside the stretch must show the person (SFace against the photographs the picture check
            passed): a host, a reporter or an interviewer speaking is never cut in as the person
  cut       the picture from the source's best stream (cut.grab, reframed by cut.finish) and the sound from the same
            seconds of the scanning copy, 48 kHz mono, so lips and voice stay together
The record: {after, person, said, words [{w, start, end}] relative to the clip, seconds, file (picture), wav (sound),
source {url, title, channel}, time_range}. narrate.py's `splice` puts the sound into the narration after its line,
and the film pins the picture over those seconds.

Rights: the footage switch (switch.py) governs voices as it governs pictures.
"""
import difflib
import json
import os
import subprocess
import time

from . import ask, cut, fetch, identity

MOST = 2                  # cut-ins a film may carry (the critic reels: one to three)
MIN_S, MAX_S = 1.8, 9.5   # a cut-in's length
FACE_FRAMES = 3           # frames checked inside the stretch; two must show the person
PAD_IN, PAD_OUT = 0.06, 0.14

# repair=True (a workflow opts in; the product's defaults above are unchanged). Every step that refused a chosen voice
# repairs it instead (stories round 1, 19 Nov: Jobs's own Macworld 1997 words were refused twice, once for 11.6 s
# against a 9.5 s ceiling the references break, once because 1984 and 2010 photographs did not match 1997 stage frames):
#   length  a choice longer than the band is trimmed on word edges to its best span (sentence edges first, best_span);
#           the ceiling is the references' (Luckey 11.7 s), up to Musk's 25 s when the film's line is the person's own
#           sentence; a choice shorter than the band runs on to its sentence's end
#   face    the person's face is the source's own: frames of this source the look passed for the person are anchors
#           beside the photographs, each anchor compared on its own (several anchors, never one averaged face); a
#           stretch where the person is not matched but plausible (a wide stage, the screen, the crowd, a face near the
#           threshold) is looked at again on more frames, and when still unseen the voice is kept and plays over the
#           source's own frames of the person
#   route   a pick the provider does not answer (a daily cap, a 5xx) is asked again on the next route (ROUTES)
REPAIR_MAX_S = 12.0       # the references' cut-ins: Palmer Luckey's opening 11.7 s
OWN_MAX_S = 25.0          # a line that is the person's own sentence: Musk's own interview ran 25 s
OWN_SHARE = 0.6           # the line's words found in the person's words: the line is the person's own sentence
PLAUSIBLE = 0.20          # SFace cosine under MATCH but above this: maybe the person (era, light, resolution)
MORE_FRAMES = 6           # frames looked at again inside a stretch where the person was not yet seen
ROUTES = [m.strip() for m in os.environ.get("FOOTAGE_VOICE_ROUTES", "claude-sonnet-5-5").split(",") if m.strip()]
SPAN_DEFAULT = "about 2 to 9 seconds spoken"
SPAN_REPAIR = (f"about 2 to {REPAIR_MAX_S:.0f} seconds spoken (up to {OWN_MAX_S:.0f} when the film's line is the person's own "
               f"sentence, which the person then says in their own voice)")

PICK_SYSTEM = """You choose where a short documentary's narrator stops and the real person speaks in their own voice, the way the best story channels cut in a founder's own interview. You get the film's numbered lines, the people the film is about, and timed transcripts of real videos of them (pieces of about 12 seconds, [start-end] text), each with its title and channel.

Choose up to {most} moments where one of these people says, in their own voice, one or two sentences that belong right after one of the film's lines: the line's point said by the person who lived it, or the film's turn in their words. Rules:
- the speaker must be the person, not an interviewer, a host, a reporter or a narrator: judge by the title and channel and by what is said (first person, the person's own story);
- the words stand alone: whole sentences, {span}, no interviewer's question inside;
- they never contradict a line of the film and never just repeat a line;
- "said" is copied exactly, word for word, from one transcript piece (or two neighbouring pieces of the same video).
Answer with one JSON object only: {{"voices":[{{"src": video number, "piece_t0": start seconds of the piece where the words begin, "after": line number, "person": the person's name, "said": the exact words}}]}}, best first, or {{"voices":[]}} when no video has such a moment."""


def _tok(w):
    return "".join(c for c in str(w).lower() if c.isalnum() or c == "'")


def _ask_routes(system, content, budget_s, routes):
    """ask.ask on the engine's own model, then on each next route when the provider does not answer (an outage, never a
    verdict: a daily call cap does not lift on a retry of the same route). (text, usd); raises the last failure."""
    last = None
    for k, model in enumerate([None] + [r for r in routes if r]):
        try:
            return ask.ask(system, content, "footage_voice_pick", budget_s=budget_s, max_tokens=4000, model=model)
        except Exception as e:  # noqa: BLE001 the next route
            last = e
            time.sleep(min(2.0 * (k + 1), 6.0))
    raise last


def pick(lines, persons, sources, most=MOST, budget_s=120.0, repair=False):
    """([{src, piece_t0, after, person, said}], usd). sources: [{title, channel, transcript}] (index = src).
    repair: the references' span in the prompt, and an unanswered call asked again on the next route (ROUTES)."""
    blocks = []
    for k, s in enumerate(sources):
        tr = [f"[{p['t0']:.0f}-{p['t1']:.0f}] {p['text']}" for p in s.get("transcript") or [] if p.get("text")]
        if tr:
            blocks.append(f"## video {k}: {s.get('title')!r} ({s.get('channel') or 'unknown channel'})\n" + "\n".join(tr))
    if not blocks:
        return [], 0.0
    content = (f"# The film's lines\n" + "\n".join(f"{ln['i']}. {ln.get('say') or ''}" for ln in lines)
               + f"\n\n# The people\n{json.dumps(persons, ensure_ascii=False)}\n\n# Transcripts\n" + "\n\n".join(blocks))
    system = PICK_SYSTEM.format(most=most, span=SPAN_REPAIR if repair else SPAN_DEFAULT)
    if repair:
        text, usd = _ask_routes(system, content, budget_s, ROUTES)
    else:
        text, usd = ask.ask(system, content, "footage_voice_pick", budget_s=budget_s, max_tokens=4000)
    got = (ask.json_obj(text) or {}).get("voices") or []
    out = []
    for g in got:
        try:
            out.append(dict(src=int(g["src"]), piece_t0=float(g["piece_t0"]), after=int(g["after"]), person=str(g["person"]),
                            said=str(g["said"]).strip()))
        except (KeyError, TypeError, ValueError):
            continue
    return [g for g in out if 0 <= g["src"] < len(sources) and g["said"]][: most * 2], usd


_WHISPER = {}


def listener():
    """faster-whisper with word times (large-v3-turbo on the CPU; FOOTAGE_VOICE_ASR names another)."""
    if "m" not in _WHISPER:
        from faster_whisper import WhisperModel
        _WHISPER["m"] = WhisperModel(os.environ.get("FOOTAGE_VOICE_ASR") or "large-v3-turbo", device="cpu", compute_type="int8")
    return _WHISPER["m"]


def audio(src_path, t0, t1, out_wav, sr=16000):
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(t0, 0):.3f}", "-i", src_path,
                        "-t", f"{t1 - max(t0, 0):.3f}", "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", "-y", out_wav],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError("audio cut failed: " + (r.stderr or "")[-200:])
    return out_wav


def heard_words(proxy, t0, t1, work):
    """[{w, start, end}] in the source's own seconds, for [t0, t1] of the scanning copy."""
    wav = audio(proxy, t0, t1, os.path.join(work, f"voice_{int(t0 * 100)}.wav"))
    import wave

    import numpy as np
    with wave.open(wav) as wf:          # samples, not a path: faster-whisper's own decoder breaks on some PyAV builds
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs, _ = listener().transcribe(x, language="en", word_timestamps=True, vad_filter=False, beam_size=5)
    out = []
    for s in segs:
        for w in s.words or []:
            out.append(dict(w=w.word.strip(), start=round(t0 + w.start, 3), end=round(t0 + w.end, 3)))
    return out


def align(words, said):
    """(i, j, share): the run words[i:j] that says `said`, by string alignment of the tokens; share = the part of
    said's tokens found in order inside that run."""
    a = [_tok(w["w"]) for w in words]
    b = [t for t in (_tok(x) for x in said.split()) if t]
    if not a or not b:
        return None
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    blocks = [m for m in sm.get_matching_blocks() if m.size]
    if not blocks:
        return None
    # the densest stretch: drop leading or trailing blocks that sit far from the rest (another sentence with the same words)
    while len(blocks) > 1 and blocks[1].a - (blocks[0].a + blocks[0].size) > 6:
        blocks = blocks[1:]
    while len(blocks) > 1 and blocks[-1].a - (blocks[-2].a + blocks[-2].size) > 6:
        blocks = blocks[:-1]
    i, j = blocks[0].a, blocks[-1].a + blocks[-1].size
    found = sum(m.size for m in blocks)
    # the cut takes the said words' own edges: words of `said` outside the matched run at its ends are allowed back in
    i = max(0, i - blocks[0].b)
    j = min(len(a), j + (len(b) - (blocks[-1].b + blocks[-1].size)))
    return i, j, found / len(b)


_STOP = (".", "!", "?")
_SOFT = (",", ";", ":", "\u2014", "-")


def _ends(w, marks):
    return str(w.get("w") or "").rstrip("\"')\u201d\u2019 ").endswith(marks)


def edges(words, i, j):
    """(t0, t1) of words[i:j] on word edges: the pads, never into the neighbouring words."""
    t0 = words[i]["start"] - PAD_IN
    t1 = words[j - 1]["end"] + PAD_OUT
    if i > 0:
        t0 = max(t0, (words[i - 1]["end"] + words[i]["start"]) / 2)
    if j < len(words):
        t1 = min(t1, (words[j - 1]["end"] + words[j]["start"]) / 2)
    return t0, t1


def best_span(words, i, j, max_s, min_s=MIN_S):
    """(a, b): the best run words[a:b] inside words[i:j] that lasts at most max_s on its word edges: ending a sentence
    first (the heard words' own punctuation), then a clause, then starting a sentence, then the longest; a span of at
    least min_s before any shorter one. The chosen
    choice's own start is preferred among equals, so the cut opens where the person's sentence opens."""
    best, key = None, None
    for a in range(i, j):
        starts = a == i or _ends(words[a - 1], _STOP)
        for b in range(a + 1, j + 1):
            t0, t1 = edges(words, a, b)
            d = t1 - t0
            if d > max_s:
                break
            k = (d >= min_s, _ends(words[b - 1], _STOP), _ends(words[b - 1], _STOP + _SOFT), starts, round(d, 2), a == i)
            if key is None or k > key:
                best, key = (a, b), k
    return best or (i, i + 1)


def run_on(words, i, j, max_s):
    """(i, j2): a choice shorter than the band runs on, word by word, to its sentence's end when that end is heard
    within max_s (the person finishing their own sentence); else as it was."""
    for b in range(j, len(words) + 1):
        t0, t1 = edges(words, i, b)
        if t1 - t0 > max_s:
            break
        if _ends(words[b - 1], _STOP) and t1 - t0 >= MIN_S:
            return i, b
    return i, j


def own_line(line_say, heard):
    """The share of the film's line found, in order, in the person's heard words (align): near 1 when the narrator's
    line is the person's own sentence."""
    al = align([dict(w=w) for w in str(heard or "").split()], str(line_say or ""))
    return al[2] if al else 0.0


def face_shows(proxy, t0, t1, anchors, work):
    """How many of FACE_FRAMES frames inside [t0, t1] show the anchored person (SFace, identity.MATCH)."""
    m = identity.models()
    if not m or not anchors:
        return None
    import cv2
    import numpy as np
    det = cv2.FaceDetectorYN.create(m[0], "", (320, 320), 0.7, 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(m[1], "")

    def unit(v):
        v = np.asarray(v, dtype=np.float32).ravel()
        return v / (np.linalg.norm(v) + 1e-9)
    feats = []
    for a in anchors:
        feats += [unit(f[0]) for f in sorted(identity.faces(a, det, rec), key=lambda f: -f[2])[:1]]
    if not feats:
        return None
    centre = unit(np.mean(feats, axis=0))
    hits = 0
    for k in range(FACE_FRAMES):
        t = t0 + (t1 - t0) * (k + 0.5) / FACE_FRAMES
        p = identity.full_frame(proxy, t, os.path.join(work, f"vface_{int(t * 100)}.jpg"))
        fs = identity.faces(p, det, rec) if p else []
        if any(float(unit(f[0]) @ centre) >= identity.MATCH for f in fs):
            hits += 1
    return hits


def face_check(proxy, t0, t1, anchors, own_frames, work):
    """repair's face step: {verdict, matched, frames, other, faceless, plausible, best}. anchors: photographs of the
    person; own_frames: [frame paths] of this same source the look passed for the person (same era, light and
    resolution as the stretch). Each anchor face is compared on its own, never through one averaged face. FACE_FRAMES
    frames first; a stretch with no match yet is looked at again on MORE_FRAMES more. verdict:
      "seen"   the person matched on two frames, or on one with nothing that contradicts it
      "other"  only when the source's own frames anchor the person: faces on most frames and none of them even
               plausibly the person (an interviewer or a host is on camera, speaking)
      "unseen" otherwise (no face model, a wide stage, the screen, the crowd, a face near the threshold): the voice is
               kept and its picture is the source's own frames of the person"""
    m = identity.models()
    if not m:
        return dict(verdict="unseen", matched=0, frames=0, why="no face model")
    import cv2
    import numpy as np
    det = cv2.FaceDetectorYN.create(m[0], "", (320, 320), 0.7, 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(m[1], "")

    def unit(v):
        v = np.asarray(v, dtype=np.float32).ravel()
        return v / (np.linalg.norm(v) + 1e-9)

    def biggest(path):
        fs = sorted(identity.faces(path, det, rec), key=lambda f: -f[2])[:1] if path else []
        return [unit(f[0]) for f in fs]
    photo = [v for a in anchors or () for v in biggest(a)]
    own = [v for a in own_frames or () for v in biggest(a)]
    if len(own) >= 3:            # the person recurs on their own frames; a face that agrees with no other is someone else
        own = [v for k, v in enumerate(own) if any(float(v @ w) >= identity.MATCH for q, w in enumerate(own) if q != k)
               or any(float(v @ w) >= identity.MATCH for w in photo)] or own
    feats = own + photo
    if not feats:
        return dict(verdict="unseen", matched=0, frames=0, why="no anchor face")
    rows = []

    def look_at(ts):
        for t in ts:
            p = identity.full_frame(proxy, t, os.path.join(work, f"vface_{int(t * 1000)}.jpg"))
            fs = identity.faces(p, det, rec) if p else []
            sims = [max(float(unit(f[0]) @ a) for a in feats) for f in fs]
            rows.append(dict(t=round(t, 3), faces=len(fs), best=round(max(sims), 3) if sims else None))
    look_at([t0 + (t1 - t0) * (k + 0.5) / FACE_FRAMES for k in range(FACE_FRAMES)])
    if sum(1 for r in rows if (r["best"] or 0) >= identity.MATCH) < 2:
        look_at([t0 + (t1 - t0) * (k + 0.25) / MORE_FRAMES for k in range(MORE_FRAMES)])
    matched = sum(1 for r in rows if (r["best"] or 0) >= identity.MATCH)
    faceless = sum(1 for r in rows if not r["faces"])
    plausible = sum(1 for r in rows if r["faces"] and PLAUSIBLE <= (r["best"] or 0) < identity.MATCH)
    other = sum(1 for r in rows if r["faces"] and (r["best"] or 0) < PLAUSIBLE)
    if matched >= 2 or (matched == 1 and other == 0):
        verdict = "seen"
    elif own and matched == 0 and plausible == 0 and other * 3 >= 2 * len(rows):
        verdict = "other"
    else:
        verdict = "unseen"
    return dict(verdict=verdict, matched=matched, frames=len(rows), other=other, faceless=faceless, plausible=plausible,
                best=max((r["best"] or 0) for r in rows) if rows else None)


def _picture_at(s, t0, t1, own_t):
    """The seconds of the source to show under a voice whose speaker is not on camera: the same length of the source's
    own frames of the person, around the frame the look passed nearest the stretch (their own lips are not at those
    seconds, so it plays as the source's picture of the person, like the references' broadcast sound over pictures)."""
    if not own_t:
        return t0, t1
    c = min(own_t, key=lambda t: abs(t - (t0 + t1) / 2))
    d = t1 - t0
    a = max(0.0, c - d / 2)
    dur = float(s.get("duration") or 0)
    if dur and a + d > dur:
        a = max(0.0, dur - d)
    return a, a + d


def find_voices(lines, subjects, sources, anchors, out_dir, W, H, stream_of=None, log=print, most=MOST, tried=None,
                repair=False, own=None):
    """The film's real-voice cut-ins: (records, usd). sources: the run's open sources (proxy, transcript, subjects);
    subjects: the run's planned subjects (kind "person" ones are heard); anchors: {person: [checked photographs]}.
    tried (a list, opt-in): every candidate considered is appended with its outcome ({person, after, source, said,
    reason}; reason None when it was cut in), and a run that had no candidate at all appends why.
    repair (a workflow opts in; see REPAIR_MAX_S): a choice is trimmed or run on to the band instead of refused, the face
    step is face_check, and the pick is asked again on the next route. own: {source key: [(subject name, t)]} the frames
    of each source the look passed, per subject (run.find), the person's own anchors for face_check."""
    tried = tried if tried is not None else []

    def note(g, s, reason):
        tried.append(dict(person=(g or {}).get("person"), after=(g or {}).get("after"), said=str((g or {}).get("said") or "")[:200],
                          source=dict(url=s.get("url"), title=s.get("title")) if s else None, reason=reason))
    people = [s for s in subjects if s.get("kind") == "person"]
    if not people:
        note(None, None, "no person among the planned subjects")
        return [], 0.0
    idx = {s["name"]: si for si, s in enumerate(subjects)}
    mine = [s for s in sources if s.get("transcript") and s.get("proxy") and any(idx.get(p["name"]) in s.get("subjects", []) for p in people)]
    if not mine:
        note(None, None, "no opened source of a person was heard")
        return [], 0.0
    work = os.path.join(out_dir, "voices_work")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "voices"), exist_ok=True)
    picks, usd = pick(lines, [p["name"] for p in people], [dict(title=s["title"], channel=s.get("channel"), transcript=s["transcript"]) for s in mine], most,
                      **({"repair": True} if repair else {}))
    say_of = {int(ln.get("i", k)): ln.get("say") for k, ln in enumerate(lines)}
    log(f"voices: {len(picks)} moments chosen from {len(mine)} sources")
    if not picks:
        note(None, None, f"the model chose no moment in {len(mine)} heard sources")
    out, used_lines = [], set()
    try:
        for g in picks:
            s = mine[g["src"]]
            if len(out) >= most or g["after"] in used_lines:
                note(g, s, "enough voices already, or one already after this line")
                continue
            try:
                words = heard_words(s["proxy"], max(g["piece_t0"] - 3.0, 0.0), g["piece_t0"] + 28.0, work)
            except Exception as e:  # noqa: BLE001
                log(f"voices: hearing {s['key']} at {g['piece_t0']:.0f}s failed: {str(e)[:120]}")
                note(g, s, f"hearing failed: {str(e)[:100]}")
                continue
            al = align(words, g["said"])
            if not al or al[2] < 0.8:
                log(f"voices: {s['key']} at {g['piece_t0']:.0f}s: the chosen words are not heard there ({al and round(al[2], 2)})")
                note(g, s, f"the chosen words are not heard there (share {al and round(al[2], 2)})")
                continue
            i, j, share = al
            t0, t1 = edges(words, i, j)
            fixed, face = None, None
            if repair:
                own_s = own_line(say_of.get(g["after"]), " ".join(w["w"] for w in words[i:j]))
                top = OWN_MAX_S if own_s >= OWN_SHARE else REPAIR_MAX_S
                if t1 - t0 > top:
                    was = t1 - t0
                    i, j = best_span(words, i, j, top)
                    t0, t1 = edges(words, i, j)
                    fixed = f"trimmed on word edges from {was:.1f} s to {t1 - t0:.1f} s"
                elif t1 - t0 < MIN_S:
                    was = t1 - t0
                    i, j = run_on(words, i, j, top)
                    t0, t1 = edges(words, i, j)
                    if t1 - t0 > was:
                        fixed = f"ran on to its sentence's end, {was:.1f} s to {t1 - t0:.1f} s"
                if fixed:
                    log(f"voices: {s['key']} {fixed}")
            elif not MIN_S <= t1 - t0 <= MAX_S:
                log(f"voices: {s['key']} {t1 - t0:.1f}s is outside {MIN_S}-{MAX_S}s")
                note(g, s, f"{t1 - t0:.1f} s is outside {MIN_S}-{MAX_S} s")
                continue
            pic_t = (t0, t1)
            if repair:
                mine_t = sorted(t for nm, t in (own or {}).get(s["key"]) or () if str(g["person"]).casefold() in str(nm).casefold())
                own_jpgs = [identity.full_frame(s["proxy"], t, os.path.join(work, f"vown_{s['key']}_{int(t * 1000)}.jpg")) for t in mine_t[:8]]
                face = face_check(s["proxy"], t0, t1, (anchors or {}).get(g["person"]) or (), [p for p in own_jpgs if p], work)
                seen = face.get("matched")
                if face["verdict"] == "other":
                    log(f"voices: {s['key']} {t0:.1f}-{t1:.1f}s: another person is on camera ({face['other']} of {face['frames']} frames)")
                    note(g, s, f"another person is on camera on {face['other']} of {face['frames']} frames")
                    continue
                if face["verdict"] == "unseen":
                    pic_t = _picture_at(s, t0, t1, mine_t)
                    log(f"voices: {s['key']} {t0:.1f}-{t1:.1f}s: the person is not on camera ({face}); the voice plays over "
                        f"{'their own frames of this source' if mine_t else 'the source at those seconds'}")
            else:
                seen = face_shows(s["proxy"], t0, t1, (anchors or {}).get(g["person"]) or (), work)
                if seen is None or seen < 2:
                    log(f"voices: {s['key']} {t0:.1f}-{t1:.1f}s: the person is on {seen} of {FACE_FRAMES} frames; not cut in")
                    note(g, s, f"the person is on {seen} of {FACE_FRAMES} frames")
                    continue
            base = f"voice_{len(out)}_{s['key']}_{int(t0 * 100)}"
            wav = os.path.join(out_dir, "voices", base + ".wav")
            audio(s["proxy"], t0, t1, wav, sr=48000)
            pic = os.path.join(out_dir, "voices", base + ".mp4")
            try:
                raw = os.path.join(work, base + "_raw.mp4")
                if s.get("whole"):          # a source opened whole (run.find whole_first): cut from that file, never fetched again
                    cut.local(s["whole"], pic_t[0], pic_t[1], raw)
                else:
                    cut.grab((stream_of or {}).get(s["key"]), s["url"], pic_t[0], pic_t[1], raw)
                cut.finish(raw, pic, W, H, 0.5)
            except Exception as e:  # noqa: BLE001 the sound alone still carries the person; the film shows its own picture
                log(f"voices: picture for {base} failed: {str(e)[:120]}")
                pic = None
            out.append(dict(after=g["after"], person=g["person"], said=" ".join(w["w"] for w in words[i:j]), chosen=g["said"],
                            share=round(share, 3), seconds=round(t1 - t0, 2), face_frames=seen,
                            words=[dict(w=w["w"], start=round(w["start"] - t0, 3), end=round(w["end"] - t0, 3)) for w in words[i:j]],
                            wav=os.path.relpath(wav, out_dir), file=os.path.relpath(pic, out_dir) if pic else None,
                            source=dict(url=s["url"], title=s["title"], channel=s.get("channel"), where=s.get("where")),
                            time_range=[round(t0, 3), round(t1, 3)],
                            **({"repaired": fixed} if fixed else {}),
                            **({"face": face} if face else {}),
                            **({"picture_range": [round(pic_t[0], 3), round(pic_t[1], 3)]} if pic_t != (t0, t1) else {})))
            used_lines.add(g["after"])
            note(g, s, None)
            log(f"voices: {g['person']} after line {g['after']}: {t1 - t0:.1f}s from {s['title'][:50]!r}")
    finally:
        subprocess.run(["rm", "-rf", work])
    return out, usd
