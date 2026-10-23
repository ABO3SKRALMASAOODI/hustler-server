"""Department ``rhythm`` (Wave 2 track 2C): the clock, measured.

  analyze_music   the structure of the USER's track (tempo, beats, bars,
                  sections, drops, gaps, kicks/snares/hats, 808s, filter
                  openings) as a one-screen map, with where each lands in
                  the program. No user track: refused, the speech prosody
                  grid offered (the agent never chooses music).
  get_syllables   syllable onsets of the words said in a program window
                  ("try / ing.") for syllable-timed type and cuts
  check_speech    deterministic listening over a program window: which
                  words are audible, what sound no word covers (an
                  untranscribed "um", a fragment of a cut word, a breath),
                  clipping and clicks — measured, never guessed
  align_lyrics    the user's lyrics on the singer's timing (DP alignment to
                  the recognised vocal words)

All four are READ verbs (no EDL write). Every lane runs them: numpy +
ffmpeg, no model.
"""

import json
import os

DEPARTMENT = "rhythm"
VERBS = ("analyze_music", "get_syllables", "check_speech", "align_lyrics")
USER_PROVENANCE = ("user_upload", "user_named", "user_picked_library")


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _user_music(edl):
    return [m for m in edl.get("music") or [] if isinstance(m, dict)
            and str(m.get("provenance") or "legacy") in USER_PROVENANCE]


def _no_track():
    return ("REJECTED: there is no track the user supplied in this edit, and "
            "the music is never chosen for them. Ask the user (ask_user) to "
            "upload a track, name a song, or pick one from the library list. "
            "Until then the clock is the SPEECH: suggest_devices and the "
            "punctuation verbs bind to the prosody grid (stresses, phrase "
            "ends, pauses) — say so instead of inventing a beat.")


def _music_asset(ctx, item):
    import agent_tools
    asset, err = agent_tools._resolve_media_asset(
        ctx, item.get("asset_key"), ("music", "audio", "video_clip"))
    if err:
        raise ValueError(err)
    path = agent_tools._asset_local_path(ctx, asset)
    return asset, path


def _fmt_t(t):
    return f"{t:.2f}s"


def structure_map(rmap, item=None, program_s=None, max_lines=14):
    """The one-screen text of a RhythmMap (media clock), with program
    positions when the music item says where the track plays."""
    def prog(t):
        if not item:
            return ""
        try:
            p = float(item.get("start") or 0.0) + t - float(
                item.get("offset_s") or 0.0)
        except (TypeError, ValueError):
            return ""
        if p < 0 or (program_s and p > program_s):
            return " (not in the program)"
        return f" (program {p:.2f}s)"
    from collections import Counter
    on = Counter(o["cls"] for o in rmap.get("onsets") or [])
    lines = [
        f"tempo {rmap.get('bpm') or '—'} BPM, confidence "
        f"{rmap.get('confidence', 0):.2f} (bar phase "
        f"{rmap.get('downbeat_confidence', 0):.2f}); "
        f"{len(rmap.get('beats') or [])} beats, "
        f"{len(rmap.get('downbeats') or [])} bars",
        "onsets: " + ", ".join(f"{k} {v}" for k, v in on.most_common()) +
        f"; 808 notes {len(rmap.get('808') or [])}",
    ]
    secs = rmap.get("sections") or []
    if secs:
        lines.append("sections: " + "; ".join(
            f"{s['label']} {s['t0']:.1f}-{s['t1']:.1f}s" for s in secs[:10]))
    for d in (rmap.get("drops") or [])[:6]:
        lines.append(f"DROP at {_fmt_t(d)}{prog(d)}")
    for g in (rmap.get("gaps") or [])[:6]:
        lines.append(f"gap {g['t0']:.2f}-{g['t1']:.2f}s{prog(g['t0'])}")
    for o in (rmap.get("openings") or [])[:4]:
        lines.append(f"filter opens at {_fmt_t(o)}{prog(o)}")
    if rmap.get("confidence", 0) < 0.35:
        lines.append("LOW CONFIDENCE: use the onsets only; do not cut to a "
                     "beat grid on this track")
    return "\n- ".join([""] + lines[:max_lines]).strip("\n")


def analyze_music(ctx, music_id=None):
    """READ: the structure of the user's track."""
    edl = _latest(ctx)
    items = _user_music(edl)
    if music_id:
        items = [m for m in items if m.get("id") == music_id]
    if not items:
        return _no_track()
    item = items[0]
    try:
        asset, path = _music_asset(ctx, item)
    except Exception as e:  # noqa: BLE001
        return f"Music analysis could not read the track ({str(e)[:160]})."
    import rhythm
    try:
        rmap = rhythm.map_for(path, asset.get("sha256"),
                              asset.get("duration_s"),
                              ref={"storage_key": asset.get("storage_key"),
                                   "sha256": asset.get("sha256"),
                                   "music_id": item.get("id")})
    except Exception as e:  # noqa: BLE001
        return f"Music analysis failed ({str(e)[:160]})."
    ctx._w2_rhythm = getattr(ctx, "_w2_rhythm", {})
    ctx._w2_rhythm[item.get("id")] = rmap
    prog = float(_tl(edl).out_duration or 0.0)
    return ("Structure of the user's track '" + str(item.get("id")) +
            "' (track seconds; program positions in brackets):\n" +
            structure_map(rmap, item, prog) +
            "\nCut on the kick one frame early (plan_to_music does it); land "
            "the payoff on a DROP; a gap before it is the held breath.")


def _source_audio(ctx, a, b, sr=16000):
    """Mono samples of the main video's source sound over [a, b]."""
    import cut_audio
    src = None
    hook = getattr(ctx, "cut_sound_source", None)
    if callable(hook):
        try:
            src = hook()
        except Exception:  # noqa: BLE001
            src = None
    if not src:
        try:
            src = ctx.proxy_path()
        except Exception:  # noqa: BLE001
            src = None
    if not src:
        return None
    got = cut_audio.fetch(src, [(max(0.0, a), b)], timeout=30.0)
    if not got:
        return None
    (w0, _w1), y = next(iter(got.items()))
    return w0, y


def _program_words(ctx, edl, s, e):
    import device_planner
    pw = device_planner.program_words(edl, getattr(ctx, "index", None) or {},
                                      _tl(edl))
    return [w for w in pw if s - 1e-3 <= w["t0"] < e]


def get_syllables(ctx, start, end):
    """READ: syllable onsets (program seconds) of the words said in
    [start, end]."""
    edl = _latest(ctx)
    try:
        s, e = float(start), float(end)
    except (TypeError, ValueError):
        return "REJECTED: start and end are program seconds."
    if e - s > 30.0:
        return "REJECTED: ask for a window of at most 30 s."
    ws = _program_words(ctx, edl, s, e)
    if not ws:
        return f"Nothing is said between {s:g}s and {e:g}s."
    a = min(w["s0"] for w in ws) - 0.2
    b = max(w["s1"] for w in ws) + 0.2
    got = _source_audio(ctx, a, b)
    import syllables
    rows = []
    for w in ws:
        if got is not None:
            w0, y = got
            syl = syllables.word_syllables(y, 16000, w["s0"] - w0,
                                           w["s1"] - w0, w["w"])
            shift = w["t0"] - (w["s0"] - w0)
        else:
            syl = syllables._even(syllables.split_word(w["w"]), w["s0"],
                                  w["s1"])
            shift = w["t0"] - w["s0"]
        rows.append(f"{w['w']}: " + " / ".join(
            f"{x['text']}@{x['t0'] + shift:.2f}" for x in syl))
    head = ("Syllables (program seconds; split by hyphenation, timed on the "
            "sound" + ("" if got is not None else
                       " — NO sound readable here: spaced evenly") + "; BETA: "
            "about two in three onsets land within 2 frames, so proof the "
            "landing on a preview before building on it):\n")
    return head + "\n".join(rows[:60])


# The longest program window check_speech reads in one call (a whole short).
CHECK_SPEECH_MAX_S = 60.0


def _kept_pieces(tl, s, e):
    """[(p0, p1, a, b, f)]: the kept footage under program [s, e] — its
    program span, its source span and rate — one row per kept piece, so a
    window across a cut reads each side's own sound."""
    out = []
    for pcs, off in zip(tl.pieces, tl.offsets):
        acc = float(off)
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            p0, p1 = max(s, acc), min(e, acc + plen)
            if p1 - p0 > 0.02:
                out.append((p0, p1, ps + (p0 - acc) * f,
                            ps + (p1 - acc) * f, f))
            acc += plen
    return out


def _listen_sources(ctx):
    src = None
    hook = getattr(ctx, "cut_sound_source", None)
    if callable(hook):
        try:
            src = hook()
        except Exception:  # noqa: BLE001
            src = None
    if not src:
        try:
            src = ctx.proxy_path()
        except Exception:  # noqa: BLE001
            src = None
    return src


def check_speech(ctx, start=None, end=None):
    """READ: deterministic listening over [start, end] (program s). Each
    kept span under the window is read on its own source sound and reported
    on the PROGRAM clock (Oct 22 Kamath run: a window across a cut read the
    whole source range between its ends, cut-away words and fillers
    included, on a clock that ran off after the first cut). Without a
    window it reads the whole short (its first 60 s): three calls with no
    window were refused 'missing 2 required positional arguments' in the
    JRE #2054 part 2 run."""
    edl = _latest(ctx)
    if start is None and end is None:
        start, end = 0.0, min(CHECK_SPEECH_MAX_S, _tl(edl).out_duration)
    elif start is None:
        start = max(0.0, float(end) - CHECK_SPEECH_MAX_S)
    elif end is None:
        end = min(float(start) + CHECK_SPEECH_MAX_S, _tl(edl).out_duration)
    try:
        s, e = float(start), float(end)
    except (TypeError, ValueError):
        return "REJECTED: start and end are program seconds."
    if e <= s:
        return "REJECTED: end must be after start (program seconds)."
    if e - s > CHECK_SPEECH_MAX_S:
        return (f"REJECTED: ask for a window of at most "
                f"{CHECK_SPEECH_MAX_S:g} s (a whole short fits).")
    tl = _tl(edl)
    pieces = _kept_pieces(tl, s, e)
    if not pieces:
        return ("No main footage plays in that window (a spliced insert or "
                "past the end): there is no speech of the source to check.")
    src = _listen_sources(ctx)
    import cut_audio
    windows = [(max(0.0, a - 0.05), b + 0.05) for _p0, _p1, a, b, _f in pieces]
    got = cut_audio.fetch(src, windows, timeout=30.0) if src else {}
    if not got:
        return ("The source sound cannot be read on this lane; nothing was "
                "guessed. Try again after the preview renders.")
    import audio_checks
    words = [w for w in (getattr(ctx, "index", None) or {}).get("words") or []
             if isinstance(w, dict) and "t0" in w]
    heard, extra, floors, clipped, clicks, unread = [], [], [], 0.0, [], []
    for (p0, p1, a, b, f), win in zip(pieces, windows):
        y = got.get(win)
        if y is None:
            unread.append(f"{p0:.2f}-{p1:.2f}s")
            continue
        rep = audio_checks.listen(y, 16000, words, a, b, clock_offset=win[0])

        def prog(t, _p0=p0, _a=a, _f=f):
            return _p0 + (float(t) - _a) / _f
        floors.append(rep["floor_db"])
        heard += [f"{r['w']}@{max(p0, prog(r['t0'])):.2f}"
                  + ("" if r["audible"] else " (inaudible)")
                  for r in rep["words"]]
        extra += [f"UNTRANSCRIBED sound {max(p0, prog(u['t0'])):.2f}-"
                  f"{min(p1, prog(u['t1'])):.2f}s ({u['db']} dBFS): "
                  f"{u['guess']}" for u in rep["unexplained"]]
        clipped += float(rep["clipped_s"] or 0.0)
        clicks += [prog(c) for c in rep["clicks"]]
    cuts = [round(p1, 2) for _p0, p1, *_r in pieces[:-1]]
    lines = [f"Measured sound over program {s:.2f}-{e:.2f}s (floor "
             f"{min(floors) if floors else '?'} dBFS"
             + (f"; {len(pieces)} kept spans, cut at "
                + ", ".join(f"{c:g}s" for c in cuts[:8]) if cuts else "")
             + "; program seconds):"]
    lines.append("transcript words: " + (", ".join(heard) or "none"))
    lines += extra
    if clipped > 0:
        lines.append(f"CLIPPING: {clipped:.3f}s of full-scale samples")
    if clicks:
        lines.append("CLICKS at " + ", ".join(f"{c:.2f}s" for c in clicks[:8]))
    if unread:
        lines.append("NOT READ (the source sound did not decode): "
                     + ", ".join(unread))
    edge_lines = _cut_edge_lines(ctx, edl, pieces, words, src)
    if len(lines) == 2:
        lines.append("no untranscribed sound, clipping or clicks")
    return "\n".join(lines + edge_lines)


def _cut_edge_lines(ctx, edl, pieces, words, src):
    """The keep's cut edges under the window, judged by the one sound-based
    rule the keep tools, the render's MID-WORD AUDIT and critique_short use
    (audit.edge_verdicts): a cut the transcript puts inside a word is named
    only when the source still sounds there, with the edge to move it to.
    Before, check_speech said nothing about edges while the other three
    gave three different answers (ASR word edges run 0.05-0.25 s off the
    sound; Diamandis 4416-4425, Gates 4364/4383)."""
    import audit
    import cut_audio
    keep = [[float(a), float(b)] for a, b in (edl or {}).get("keep") or []]
    if not keep or not words or not src:
        return []
    duration = getattr(ctx, "duration", None)
    srcs = [round(a, 2) for _p0, _p1, a, _b, _f in pieces] + \
        [round(b, 2) for _p0, _p1, _a, b, _f in pieces]
    hits = [h for h in audit.midword_boundaries(keep, words, duration)
            if round(float(h["boundary"]), 2) in srcs]
    edges = sum(1 for i, side, b in cut_audio.cut_edges(keep, duration)
                if round(b, 2) in srcs)
    if not edges:
        return []
    if not hits:
        return [f"CUT EDGES: {edges} cut edge{'s' if edges != 1 else ''} "
                "here, none inside a transcript word"]
    try:
        level = cut_audio.source_levels(src, [h["boundary"] for h in hits],
                                        duration, timeout=20.0)
    except Exception:  # noqa: BLE001
        level = None
    if level is None:
        return ["CUT EDGES: " + ", ".join(
            f"{h['kind']} {h['boundary']:g} inside '{h['word']}'"
            for h in hits) + " (transcript only: the sound at the cut was "
            "not read)"]
    try:
        fps = float(((getattr(ctx, "index", None) or {}).get("video") or {})
                    .get("fps") or 30.0)
    except (TypeError, ValueError):
        fps = 30.0
    verdicts = audit.edge_verdicts(hits, level, 1.0 / max(fps, 1.0), keep,
                                   words)
    bad = [v for v in verdicts if v["loud"]]
    out = []
    for v in bad:
        out.append(
            f"CUT INSIDE A SOUNDING WORD: {v['kind']} {v['boundary']:g} in "
            f"'{v['word']}' ({v['db']:.0f} dB at the cut)"
            + (f" -> move it to {v['safe']:g} (the sound-safe edge)"
               if v.get("safe") is not None else
               " -> no quiet point within reach: cut at a breath or "
               "sentence end"))
    quiet = len(verdicts) - len(bad)
    out.append(f"CUT EDGES: {edges} here; {quiet} the transcript puts in a "
               "word sit in quiet sound (its word timing is off: clean)"
               if quiet else f"CUT EDGES: {edges} here")
    return out


def align_lyrics(ctx, lyrics, music_id=None):
    """READ: the user's lyrics on the singer's timing."""
    edl = _latest(ctx)
    items = _user_music(edl)
    if music_id:
        items = [m for m in items if m.get("id") == music_id]
    if not items:
        return _no_track()
    item = items[0]
    try:
        asset, _path = _music_asset(ctx, item)
    except Exception as e:  # noqa: BLE001
        return f"Could not read the track ({str(e)[:160]})."
    words = ((asset.get("meta") or {}).get("vocal_words") or
             (asset.get("meta") or {}).get("words"))
    if not words:
        return ("The track's vocal has not been transcribed yet: run "
                "separate_music on it (the vocal stem is transcribed on the "
                "batch lane), then align_lyrics again. Nothing was guessed.")
    import lyrics_align
    rows = lyrics_align.align(lyrics, words)
    est = sum(1 for r in rows if r["estimated"])
    out = [f"{r['w']}@{r['t0']:.2f}" + ("~" if r["estimated"] else "")
           for r in rows]
    return (f"Lyrics aligned to the vocal ({len(rows) - est} matched, {est} "
            "interpolated ~; track seconds):\n" + " ".join(out[:200]))


TOOL_SPECS = {
    "analyze_music": (
        analyze_music,
        "READ: the structure of the user's track — tempo, beats, bars, "
        "sections, drops, gaps, kicks/snares/hats, 808s, filter openings — "
        "with program positions. Refuses when the user supplied no track "
        "(never choose music; the speech prosody grid is the clock then).",
        {"music_id": {"type": "string"}}),
    "get_syllables": (
        get_syllables,
        "READ: syllable onsets of the words said in a program window, for "
        "syllable-timed type ('try / ing.') and cuts.",
        {"start": {"type": "number"}, "end": {"type": "number"}}),
    "check_speech": (
        check_speech,
        "READ: deterministic listening over a program window (up to 60 s: "
        "a whole short) — audible transcript words, untranscribed sound (an "
        "'um', a cut word's tail, a breath), clipping, clicks, all on the "
        "program clock; a window across cuts reads each kept span's own "
        "sound, and a cut inside a word that still sounds is named with "
        "the sound-safe edge to move it to (the edge keep_segments, the "
        "render's MID-WORD AUDIT and critique_short name). Measured, never "
        "guessed; use it before and after a cut. Omit start/end to read the "
        "whole short.",
        {"start": {"type": "number"}, "end": {"type": "number"}}),
    "align_lyrics": (
        align_lyrics,
        "READ: the user's lyrics text aligned to the singer's timing on "
        "their track (after separate_music).",
        {"lyrics": {"type": "string"}, "music_id": {"type": "string"}}),
}
REQUIRED_ARGS = {"get_syllables": ["start", "end"],
                 "check_speech": ["start", "end"],
                 "align_lyrics": ["lyrics"]}
WRITE_TOOLS = set()
COMPACT = {
    "analyze_music": "READ: the user's track's tempo/bars/sections/drops/"
                     "gaps/onsets with program positions; refuses without "
                     "a user track.",
    "get_syllables": "READ: syllable onsets of the words in a program "
                     "window.",
    "check_speech": "READ: measured listening over a window: audible words, "
                    "untranscribed sound (um, cut-word tails), clipping, "
                    "clicks.",
    "align_lyrics": "READ: user lyrics aligned to the vocal timing.",
}
