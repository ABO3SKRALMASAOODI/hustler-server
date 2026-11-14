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

from . import ask, cut, fetch, identity

MOST = 2                  # cut-ins a film may carry (the critic reels: one to three)
MIN_S, MAX_S = 1.8, 9.5   # a cut-in's length
FACE_FRAMES = 3           # frames checked inside the stretch; two must show the person
PAD_IN, PAD_OUT = 0.06, 0.14

PICK_SYSTEM = """You choose where a short documentary's narrator stops and the real person speaks in their own voice, the way the best story channels cut in a founder's own interview. You get the film's numbered lines, the people the film is about, and timed transcripts of real videos of them (pieces of about 12 seconds, [start-end] text), each with its title and channel.

Choose up to {most} moments where one of these people says, in their own voice, one or two sentences that belong right after one of the film's lines: the line's point said by the person who lived it, or the film's turn in their words. Rules:
- the speaker must be the person, not an interviewer, a host, a reporter or a narrator: judge by the title and channel and by what is said (first person, the person's own story);
- the words stand alone: whole sentences, about 2 to 9 seconds spoken, no interviewer's question inside;
- they never contradict a line of the film and never just repeat a line;
- "said" is copied exactly, word for word, from one transcript piece (or two neighbouring pieces of the same video).
Answer with one JSON object only: {{"voices":[{{"src": video number, "piece_t0": start seconds of the piece where the words begin, "after": line number, "person": the person's name, "said": the exact words}}]}}, best first, or {{"voices":[]}} when no video has such a moment."""


def _tok(w):
    return "".join(c for c in str(w).lower() if c.isalnum() or c == "'")


def pick(lines, persons, sources, most=MOST, budget_s=120.0):
    """([{src, piece_t0, after, person, said}], usd). sources: [{title, channel, transcript}] (index = src)."""
    blocks = []
    for k, s in enumerate(sources):
        tr = [f"[{p['t0']:.0f}-{p['t1']:.0f}] {p['text']}" for p in s.get("transcript") or [] if p.get("text")]
        if tr:
            blocks.append(f"## video {k}: {s.get('title')!r} ({s.get('channel') or 'unknown channel'})\n" + "\n".join(tr))
    if not blocks:
        return [], 0.0
    content = (f"# The film's lines\n" + "\n".join(f"{ln['i']}. {ln.get('say') or ''}" for ln in lines)
               + f"\n\n# The people\n{json.dumps(persons, ensure_ascii=False)}\n\n# Transcripts\n" + "\n\n".join(blocks))
    text, usd = ask.ask(PICK_SYSTEM.format(most=most), content, "footage_voice_pick", budget_s=budget_s, max_tokens=4000)
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


def find_voices(lines, subjects, sources, anchors, out_dir, W, H, stream_of=None, log=print, most=MOST):
    """The film's real-voice cut-ins: (records, usd). sources: the run's open sources (proxy, transcript, subjects);
    subjects: the run's planned subjects (kind "person" ones are heard); anchors: {person: [checked photographs]}."""
    people = [s for s in subjects if s.get("kind") == "person"]
    if not people:
        return [], 0.0
    idx = {s["name"]: si for si, s in enumerate(subjects)}
    mine = [s for s in sources if s.get("transcript") and s.get("proxy") and any(idx.get(p["name"]) in s.get("subjects", []) for p in people)]
    if not mine:
        return [], 0.0
    work = os.path.join(out_dir, "voices_work")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "voices"), exist_ok=True)
    picks, usd = pick(lines, [p["name"] for p in people], [dict(title=s["title"], channel=s.get("channel"), transcript=s["transcript"]) for s in mine], most)
    log(f"voices: {len(picks)} moments chosen from {len(mine)} sources")
    out, used_lines = [], set()
    try:
        for g in picks:
            if len(out) >= most or g["after"] in used_lines:
                continue
            s = mine[g["src"]]
            try:
                words = heard_words(s["proxy"], max(g["piece_t0"] - 3.0, 0.0), g["piece_t0"] + 28.0, work)
            except Exception as e:  # noqa: BLE001
                log(f"voices: hearing {s['key']} at {g['piece_t0']:.0f}s failed: {str(e)[:120]}")
                continue
            al = align(words, g["said"])
            if not al or al[2] < 0.8:
                log(f"voices: {s['key']} at {g['piece_t0']:.0f}s: the chosen words are not heard there ({al and round(al[2], 2)})")
                continue
            i, j, share = al
            t0 = words[i]["start"] - PAD_IN
            t1 = words[j - 1]["end"] + PAD_OUT
            if i > 0:
                t0 = max(t0, (words[i - 1]["end"] + words[i]["start"]) / 2)
            if j < len(words):
                t1 = min(t1, (words[j - 1]["end"] + words[j]["start"]) / 2)
            if not MIN_S <= t1 - t0 <= MAX_S:
                log(f"voices: {s['key']} {t1 - t0:.1f}s is outside {MIN_S}-{MAX_S}s")
                continue
            seen = face_shows(s["proxy"], t0, t1, (anchors or {}).get(g["person"]) or (), work)
            if seen is None or seen < 2:
                log(f"voices: {s['key']} {t0:.1f}-{t1:.1f}s: the person is on {seen} of {FACE_FRAMES} frames; not cut in")
                continue
            base = f"voice_{len(out)}_{s['key']}_{int(t0 * 100)}"
            wav = os.path.join(out_dir, "voices", base + ".wav")
            audio(s["proxy"], t0, t1, wav, sr=48000)
            pic = os.path.join(out_dir, "voices", base + ".mp4")
            try:
                raw = os.path.join(work, base + "_raw.mp4")
                cut.grab((stream_of or {}).get(s["key"]), s["url"], t0, t1, raw)
                cut.finish(raw, pic, W, H, 0.5)
            except Exception as e:  # noqa: BLE001 the sound alone still carries the person; the film shows its own picture
                log(f"voices: picture for {base} failed: {str(e)[:120]}")
                pic = None
            out.append(dict(after=g["after"], person=g["person"], said=" ".join(w["w"] for w in words[i:j]), chosen=g["said"],
                            share=round(share, 3), seconds=round(t1 - t0, 2), face_frames=seen,
                            words=[dict(w=w["w"], start=round(w["start"] - t0, 3), end=round(w["end"] - t0, 3)) for w in words[i:j]],
                            wav=os.path.relpath(wav, out_dir), file=os.path.relpath(pic, out_dir) if pic else None,
                            source=dict(url=s["url"], title=s["title"], channel=s.get("channel"), where=s.get("where")),
                            time_range=[round(t0, 3), round(t1, 3)]))
            used_lines.add(g["after"])
            log(f"voices: {g['person']} after line {g['after']}: {t1 - t0:.1f}s from {s['title'][:50]!r}")
    finally:
        subprocess.run(["rm", "-rf", work])
    return out, usd
