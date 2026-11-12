"""Workflow 1 on the shell: podcast shorts. The workflow finds its own ideas in the source.

ideas:  the whole conversation's transcript (the source's own word-timed captions when they sit
        beside it, else a local whisper hearing) goes to one model call, which returns ranked
        moments, each with the styles it can carry. The source is logged: path, size, origin URL.
script: the best-ranked moment that fits a style not used in the last three gets one more call
        on its word-timed lines: exact in and out at word edges, the hook, the close, and the
        direction the Director reads. Model output is parsed as JSON; no customer text is read.
"""
import hashlib
import json
import os
import subprocess

import chain
import llm

TASTE = ("A device exists only where an event earns it, and less, placed right, beats more. But a plain take with "
         "captions is not a film: the first second is designed, the things he names are shown, and one idea nobody "
         "else has runs through the whole piece. A sound belongs to something landing on screen (a new picture, a "
         "display line): about 1.5 per 10 s when the film has new pictures, none without a partner, quiet under the "
         "voice. One framing per speaker; no zoom per cut, no reflexive whoosh, no sound puns. Every graphic adds what "
         "captions cannot (a number, a contrast, a name, evidence). Placement is law: all type and cards inside x "
         "0.05-0.95 and y 0.10-0.80, clear of the top-right mark, never on a face, never light type on a light shirt; "
         "one caption seat for the film, between y 0.58 and 0.68. A name is a small chip inside y 0.70-0.78, once per "
         "person. No music: Valmera never picks music. The end card and watermark are added after you; leave the last "
         "frames on live footage.")

# How each style is made. `pass` is the alien pass the render stage runs after the Director's pictures and before
# the type (valmera-podcast-shorts/alien); `made` is told to the script stage so the Director leaves room for it.
RECIPES = {
    "lit-by-voice": dict(passes=["voice_light"], house=" No grade, flash, vignette or camera move on the take: the engine's light is the look.", made=(
        "After the Director, the engine relights the finished picture: the room falls to near black and the only light "
        "is a warm lamp at the speaker's mouth whose brightness is the loudness of his voice, frame by frame. In a pause "
        "the frame is dark. Pictures and cards glow with the same light, so the whole film breathes with him. Type is "
        "drawn after the light and stays at full brightness. Direct for darkness: (1) open on a silent beat of 18-24 "
        "frames, black, carrying the hook as one small display line of at most six of his own words, with a swell of his "
        "own voice rising into the first word; (2) one framing per speaker, no camera move, no flash, no grade, no "
        "vignette, because the light is the grade; (3) captions small (about 0.026 of the height), white, one seat, the "
        "stressed word in serif italic; (4) three to five pictures of the things he names, each landing on its word and "
        "held 1.2-2.5 s, at least two of them full-frame cuts: say exactly what each one shows; (5) no library sound "
        "except one quiet partner per picture landing, and an echo of his last words after the close, which the light "
        "will follow down into the dark; (6) the close is the payoff line, then about one second in which the echo dies.")),
    "receipts": dict(passes=["footnotes"], house=(" Type nothing in y 0.70-0.80 after the first three seconds and add no superscripts or numbered "
                                                  "labels: the engine alone writes the footnotes, from sources."), look=dict(grade=dict(lift=-0.012, gamma=0.96, gain=1.0, sat=0.82, contrast=1.10, temp=-0.012, tint=0.0),
                                                     grain=0.07, vignette=0.35), made=(
        "After the Director, the engine annotates the speech like a court record: each claim that a fetched source "
        "confirms gets a small superscript number on its word and a two-row footnote with its source, typed at the foot "
        "of the safe area (y 0.70-0.80). Only the engine writes footnotes, because only it has the sources. So from "
        "you: no superscripts, no numbered labels, no captions restating a claim, nothing typed in y 0.70-0.80 after the "
        "first three seconds. Direct for evidence: (1) the first second is the boldest claim as one display line of his "
        "own words, in the upper part of the frame, off his head; (2) four to six pictures that are evidence for what he "
        "names (the company, the place, the document, the thing), each landing on its word and held 1.2-2.5 s: at least "
        "two of them full-frame cuts, the others cards beside his head; (3) captions in one seat between y 0.58 and 0.66; (4) a quiet tick or click "
        "partners each picture landing; (5) one framing per speaker; (6) the name chip, if any, inside the first three "
        "seconds; (7) the close holds the payoff line plain.")),
    "second-camera": dict(passes=["second_camera"], house=(" One crop of the take for the whole film: never alternate crops, framings or looks of "
                                                           "the take. The engine alone makes the second angle and its cuts; you cut only to pictures."), look=dict(grade=dict(lift=-0.008, gamma=0.98, gain=1.03, sat=1.06, contrast=1.08, temp=0.008, tint=0.0),
                                                              grain=0.04, vignette=0.22), made=(
        "After the Director, the engine cuts to a second camera that was never there: the film opens on the take seen "
        "from a few degrees off axis and a little closer, rebuilt from the depth of the picture so the room slides "
        "behind him as it would for a real second camera; it cuts to the true frame on a stressed word, visits the "
        "second angle once more, and ends on the true frame. Direct for a two-camera show: (1) one framing of the take "
        "from you, held, no zoom, no punch-in, no camera move: the engine makes the other angle and its cuts; (2) the "
        "first second carries the hook as one display line over the second angle; (3) three to five pictures of the "
        "things he names, each a full-frame cut landing on its word and held 1.2-2.5 s, no inset cards; (4) captions "
        "in one seat; (5) a quiet partner sound on each picture landing and none on a camera cut; (6) the close stays "
        "on the take, plain.")),
}


def _fmt(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def captions_words(path):
    """YouTube json3 captions -> [(t, word)]; times in seconds."""
    d = json.load(open(path))
    out = []
    for e in d.get("events") or []:
        t0 = e.get("tStartMs", 0) / 1000.0
        for s in e.get("segs") or []:
            w = (s.get("utf8") or "").strip()
            if w:
                out.append((t0 + s.get("tOffsetMs", 0) / 1000.0, w))
    out.sort()
    return out


def whisper_words(ctx, src):
    """No captions: hear the whole source locally (heavy), word-timed."""
    p = os.path.join(ctx.tmp, "hearing_full.json")
    if not os.path.exists(p):
        code = ("import json,sys,mlx_whisper;r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo=sys.argv[2],word_timestamps=True,"
                "condition_on_previous_text=False);json.dump([[w['start'],w['word'].strip()] for g in r['segments'] for w in g.get('words') or []],open(sys.argv[3],'w'))")
        chain.sh(ctx, [chain.ATLAS_PY, "-c", code, src, f"{chain.VV}/week-1106/atlas/models/whisper-large-v3-mlx", p],
                 label=f"wf-hearfull-{ctx.run_id}", heavy=True, timeout=14400)
    return [tuple(x) for x in json.load(open(p))]


def lines_of(words, step=12.0):
    out, cur, t_start = [], [], None
    for t, w in words:
        if t_start is None:
            t_start = t
        if t - t_start >= step and cur:
            out.append(f"[{_fmt(t_start)}|{t_start:.0f}] " + " ".join(cur))
            cur, t_start = [], t
        cur.append(w)
    if cur:
        out.append(f"[{_fmt(t_start)}|{t_start:.0f}] " + " ".join(cur))
    return out


def source_log(src):
    d = os.path.dirname(src)
    h = hashlib.sha256()
    with open(src, "rb") as f:
        h.update(f.read(4 << 20))
    rec = dict(path=src, bytes=os.path.getsize(src), sha256_first_4mb=h.hexdigest())
    for name in ("acquisition.json", "plan.json"):
        a = os.path.join(d, name)
        if os.path.exists(a):
            j = json.load(open(a))
            for k in ("source_url", "url", "video_id", "title"):
                if j.get(k):
                    rec[k] = j[k]
    return rec


def identity_text(src):
    for name in ("identity.md", "SOURCE.md"):
        p = os.path.join(os.path.dirname(src), name)
        if os.path.exists(p):
            return open(p).read()[:3000]
    return ""


def style_menu(styles):
    return "\n".join(f"- {s['id']}: {s['name']}. {s.get('look', '')} Needs: {s.get('needs', '')}. Hook: {s.get('hook', '')} "
                     f"Close: {s.get('close', '')} Pace {s['pace'][0]}-{s['pace'][1]} cuts/10 s." for s in styles)


def _styles(ctx):
    """The styles this run may take: the registry minus the last three, and only the one the owner's line names
    (--args '{"style": id}') when it names one that is allowed. The moment is still found by the workflow."""
    allowed = ctx.styles_allowed()
    want = (ctx.args or {}).get("style")
    one = [s for s in allowed if s["id"] == want]
    return one or allowed


def _words_file(ctx):
    return os.path.join(ctx.tmp, "words.json")


def ideas(ctx):
    src = ctx.source
    caps = [os.path.join(os.path.dirname(src), n) for n in sorted(os.listdir(os.path.dirname(src))) if n.endswith(".json3")]
    if caps:
        words, how = captions_words(caps[0]), f"captions {os.path.basename(caps[0])}"
    else:
        words, how = whisper_words(ctx, src), "whisper-large-v3 local"
    json.dump(words, open(_words_file(ctx), "w"))
    allowed = _styles(ctx)
    system = ("You are the ideas stage of Valmera's podcast shorts workflow. You read a whole conversation and find the "
              "moments that become great vertical shorts: a story with a turn, a claim that stops the scroll, a payoff "
              "that lands inside the clip. Each moment must stand alone without the rest of the episode. You find the "
              "ideas yourself; nobody gives you a topic.")
    used = ctx.windows_used()
    content = (f"# Who is in it (the acquisition record)\n{identity_text(src)}\n\n"
               f"# Styles a film can take (one each). These are the ones not used in the last three films:\n{style_menu(allowed)}\n\n"
               + (f"# Already made from this source, do not repeat\n{json.dumps(used)}\n\n" if used else "")
               + "# Transcript, one line per ~12 s: [h:mm:ss|seconds] words\n" + "\n".join(lines_of(words)) + "\n\n"
               "Return JSON only: {\"ideas\": [ ... 8 items ... ]}, best first. Each item: "
               "{\"id\": \"i1\", \"title\": short plain title, \"t0\": seconds, \"t1\": seconds (20-60 s apart, starting where the thought starts), "
               "\"hook\": the first line a viewer hears or reads, from the words, \"payoff\": the line it lands on, "
               "\"why\": why a stranger watches to the end, \"speakers\": how many people speak in it, "
               "\"names\": people named or shown, \"styles\": [{\"id\": style id from the list, \"why\": what in this moment the style needs}] "
               "only styles whose needs this moment truly has, best fit first, \"strength\": 1-10}.")
    text, use = llm.ask(system, content, effort="medium", max_tokens=12000)
    got = llm.json_block(text)
    rows = got.get("ideas") if isinstance(got, dict) else got
    ids = {s["id"] for s in allowed}
    for r in rows:
        r["styles"] = [s for s in r.get("styles") or [] if s.get("id") in ids]
    return dict(source=dict(source_log(src), transcript=how, words=len(words)), ideas=rows, chosen=None,
                spend=[dict(stage="ideas", what="find moments", **use)])


HOUSE = (" House rules, binding: every card and every piece of type sits inside x 0.05-0.95 and y 0.10-0.80 (a card 0.24 "
         "high is centred at y 0.66 or higher), clear of faces and of the mark at the top right. A found picture is used "
         "whole or nearly whole, its subject filling the frame and readable in half a second on a phone: never crop deep "
         "into a wide photo, and never grade a found picture darker. A sound sits at least 9 dB under the voice.")


def _pictures_line(rows):
    rows = [r for r in rows or [] if isinstance(r, dict) and r.get("shows")]
    if not rows:
        return ""
    return " Pictures to find: " + "; ".join(f"on \"{r.get('on', '')}\": {r['shows']}" for r in rows) + "."


def script(ctx, ideas_out):
    allowed = {s["id"]: s for s in _styles(ctx)}
    rows = sorted((r for r in ideas_out["ideas"] if r["styles"]), key=lambda r: -float(r.get("strength") or 0))
    if not rows:
        raise RuntimeError("no idea fits a style outside the last three")
    idea = rows[0]
    style = allowed[idea["styles"][0]["id"]]
    ideas_out["chosen"] = idea
    words = json.load(open(_words_file(ctx)))
    near = [(t, w) for t, w in words if float(idea["t0"]) - 25 <= t <= float(idea["t1"]) + 25]
    system = ("You are the script stage of Valmera's podcast shorts workflow. You turn one chosen moment into the exact "
              "clip and the direction a film Director will follow. You cut on word edges, start on the first word of "
              "the thought (no throat-clearing), and end on the payoff with a breath of tail.")
    recipe = RECIPES.get(style["id"]) or {}
    made = (f"# How this style is made (binding: the engine does this part, you direct the rest around it)\n{recipe['made']}\n\n"
            if recipe else "")
    content = (f"# The moment\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{style_menu([style])}\n"
               f"Concepts in its spine are from Valmera's ALIEN list: {', '.join(style['spine'])}. Use what the footage can carry; "
               "a device the engine cannot draw well is left out rather than faked.\n\n" + made +
               f"# Taste rules\n{TASTE}\n\n# Who is in it\n{identity_text(ctx.source)[:1500]}\n\n"
               "# Words near the moment: seconds word\n" + " ".join(f"{t:.2f} {w}" for t, w in near) + "\n\n"
               "Return JSON only: {\"in\": seconds (a little before the first word), \"out\": seconds (0.3-0.8 s after the last word), "
               "\"title\": the film's title, \"hook_line\": exact words heard first, \"close_line\": exact last words, "
               "\"speakers\": number of people heard, \"pictures\": [3-6 items {\"on\": the quoted words it lands on, \"shows\": "
               "the physical thing a camera can film or an archive holds, specific, never a person's likeness}], "
               "\"direction\": 170-260 words to the Director: the story in one line; the first second, frame by frame (what is on "
               "screen at frame 0 and what lands in the first 30 frames); how the style above looks on THIS footage; each picture "
               "and the words it lands on; the sounds and their partners; the close; names to identify with a chip and when; and "
               "what stays plain. Anchor every moment on the quoted words, never on seconds: the Director sees the clip from 0, not "
               "the episode's clock. Write it as an editor's brief, not a list of effects.}")
    spend, s = [], {}
    for _ in range(2):                                   # an answer cut short or missing a field is asked for once more
        text, use = llm.ask(system, content, effort="medium", max_tokens=14000)
        spend.append(dict(stage="script", what="clip and direction", **use))
        try:
            s = llm.json_block(text)
        except ValueError:
            s = {}
        if isinstance(s, dict) and all(s.get(k) not in (None, "") for k in ("in", "out", "direction")):
            break
    else:
        raise RuntimeError("the script stage gave no clip and direction")
    t0, t1 = float(s["in"]), float(s["out"])
    if not (5.0 <= t1 - t0 <= 90.0):
        raise RuntimeError(f"script window {t0}-{t1} is not a short")
    return dict(title=s.get("title") or idea.get("title"), style=style, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line"), close_line=s.get("close_line"), speakers=int(s.get("speakers") or 1),
                direction=f"Style: {style['name']}. " + s["direction"].strip() + _pictures_line(s.get("pictures")) + HOUSE + (recipe.get("house") or ""),
                pictures=s.get("pictures") or [], passes=recipe.get("passes") or [], look=recipe.get("look"), idea=idea,
                spend=spend)


def _alien():
    import importlib.util
    import sys
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "valmera-podcast-shorts", "alien")
    if d not in sys.path:
        sys.path.insert(0, d)
    import alien_render
    return alien_render


def score(ctx, script):
    """The Director's Score, then, for a style that footnotes its claims, the evidence: only what a source states."""
    r = chain.score(ctx, script)
    if "footnotes" in (script.get("passes") or []):
        _alien()
        import evidence
        ev = evidence.find(ctx, script, llm)
        r["spend"] = (r.get("spend") or []) + (ev.get("spend") or [])
        r["footnotes"] = dict(found=len(ev.get("footnotes") or []), claims=ev.get("claims"), pages=ev.get("pages"))
    return r


def render(ctx, script, sc):
    """The style's alien passes sit between the Director's pictures and the type; a style with none is the shared chain."""
    if not script.get("passes"):
        return chain.render(ctx, script, sc)
    return _alien().render(ctx, script, sc, chain)


def check(ctx, rd, sc):
    """The shared finish and checks, plus what the alien pass measured on its own film: a claim is a number."""
    r = chain.finish_and_check(ctx, rd, sc)
    if rd.get("alien"):
        r["checks"]["alien"] = rd["alien"]
        r["checks"]["ok"] = bool(r["checks"]["ok"] and rd["alien"].get("ok", True))
    return r
