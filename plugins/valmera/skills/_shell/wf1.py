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

TASTE = ("Restraint is the default. A device must be earned by an event: a sound belongs to something landing on "
         "screen; on one take, 1-2 sounds and at most one camera move per 30 s, apart from a tenth at a speech cut. "
         "No zoom per cut, no reflexive whoosh, no sound puns. Every graphic adds what captions cannot (a number, a "
         "contrast, a name, evidence). Nothing unclear: type sits off faces and inside the frame. Never worse than the "
         "plain clip. No music: Valmera never picks music. The end card and watermark are added after you; leave the "
         "last frames on live footage.")


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
    allowed = ctx.styles_allowed()
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


def script(ctx, ideas_out):
    allowed = {s["id"]: s for s in ctx.styles_allowed()}
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
    content = (f"# The moment\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{style_menu([style])}\n"
               f"Concepts in its spine are from Valmera's ALIEN list: {', '.join(style['spine'])}. Use what the footage can carry; "
               "a device the engine cannot draw well is left out rather than faked.\n\n"
               f"# Taste rules\n{TASTE}\n\n# Who is in it\n{identity_text(ctx.source)[:1500]}\n\n"
               "# Words near the moment: seconds word\n" + " ".join(f"{t:.2f} {w}" for t, w in near) + "\n\n"
               "Return JSON only: {\"in\": seconds (a little before the first word), \"out\": seconds (0.3-0.8 s after the last word), "
               "\"title\": the film's title, \"hook_line\": exact words heard first, \"close_line\": exact last words, "
               "\"speakers\": number of people heard, \"direction\": 120-220 words to the Director: the story in one line, the hook "
               "and how the first second looks, the style's look applied to THIS footage, the close, names to identify with a "
               "lower third and when, and what stays plain. Anchor every moment on the quoted words, never on seconds: the Director sees the clip from 0, not the episode's clock. Write it as an editor's brief, not a list of effects.}")
    text, use = llm.ask(system, content, effort="medium", max_tokens=8000)
    s = llm.json_block(text)
    t0, t1 = float(s["in"]), float(s["out"])
    if not (5.0 <= t1 - t0 <= 90.0):
        raise RuntimeError(f"script window {t0}-{t1} is not a short")
    return dict(title=s.get("title") or idea.get("title"), style=style, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line"), close_line=s.get("close_line"), speakers=int(s.get("speakers") or 1),
                direction=f"Style: {style['name']}. " + s["direction"].strip(), idea=idea,
                spend=[dict(stage="script", what="clip and direction", **use)])
