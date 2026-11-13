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
         "captions is not a film: frame 0 is a designed picture, the people, places, companies and documents he names "
         "are shown as themselves (a real archive photograph, never a lookalike from a stock library), a number or a "
         "quote that carries the story becomes a designed chart or document, and one idea nobody else has runs through "
         "the whole piece. A sound belongs to something landing on screen. One framing per speaker; no zoom per cut, no "
         "reflexive whoosh, no sound puns. Placement is law: all type and cards inside x 0.05-0.95 and y 0.10-0.80, "
         "clear of the top-right mark, never on a face; one caption seat for the film, between y 0.58 and 0.68. A name "
         "is a small chip inside y 0.70-0.78, once per person. No music: Valmera never picks music. The end card and "
         "watermark are added after you; leave the last frames on live footage.")

# What the engine adds after the Director on every podcast short (alien/earned.py). The script stage plans it; the
# Director is told to leave room for it. Nothing here is decided by hand per film.
ENGINE = ("After you, the engine adds everything that is not the take and its captions: the hook picture at frame 0 (the "
          "story's real subject, full frame, with the hook line set large), the story's pictures on their words (archive "
          "photographs of the people, places, companies and documents he names, a designed chart or document where a "
          "number or a quote carries the story), one closer framing of his own footage on the payoff, the sounds on "
          "those landings, the motion of all of it, and the grade. So from you: the take in one framing for the whole "
          "film, captions in one seat, the stressed word of a line in the second voice, and the name chip once. No found "
          "pictures, no Bin items, no cards, no plates, no display lines, no sounds, no flashes, no camera moves, no "
          "grade. Start the captions on his first word: the hook picture carries them.")

# How each style is made. `pass` is the alien pass the render stage runs after the Director's pictures and before
# the type (valmera-podcast-shorts/alien); `made` is told to the script stage so the Director leaves room for it.
RECIPES = {
    "room-listens": dict(passes=["room_light"], house=" No grade, flash or vignette on the take: the engine lights the room.",
                         look=dict(grade=dict(lift=-0.006, gamma=0.97, gain=1.05, sat=1.10, contrast=1.10, temp=0.014, tint=-0.004),
                                   grain=0.05, vignette=0.30, halation=0.16, bloom=0.10),
                         accent="#FFC857", sounds=dict(payoff="heartbeat_1"), made=(
        "After the pictures, the engine makes the room listen (ALIEN L1 and L4): he stays as he was shot, and the room "
        "behind him brightens and falls with the level of his own voice, frame by frame; every picture that is about to "
        "cut in throws its own colour on his face and the wall for a few frames before it arrives, the way a screen "
        "lights a room, and the hook line lights his cheek in its colour when the film returns to him. Direct for a room "
        "that answers: one framing, no camera move, no grade and no vignette from you; captions small and white in one "
        "seat; the pictures and the hook are the engine's.")),
    "lit-by-voice": dict(passes=["voice_light"], house=" No grade, flash, vignette or camera move on the take: the engine's light is the look.",
                         accent="#FFD9A0", sounds=dict(payoff="heartbeat_1"), made=(
        "After the pictures, the engine relights the finished film: the room falls to near black and the only light is a "
        "warm lamp at the speaker's mouth whose brightness is the loudness of his voice, frame by frame. In a pause the "
        "frame is dark. Pictures glow with the same light, so the whole film breathes with him. Type is drawn after the "
        "light and stays at full brightness. Direct for darkness: one framing per speaker, no camera move, no flash, no "
        "grade, no vignette, because the light is the grade; captions small (about 0.026 of the height), white, one "
        "seat, the stressed word in serif italic; the close is the payoff line, then about one second in which the light "
        "falls.")),
    "receipts": dict(passes=["footnotes"], house=(" Type nothing in y 0.70-0.80 after the first three seconds and add no superscripts or numbered "
                                                  "labels: the engine alone writes the footnotes, from sources."),
                     look=dict(grade=dict(lift=-0.012, gamma=0.96, gain=1.0, sat=0.80, contrast=1.12, temp=-0.014, tint=0.0),
                               grain=0.08, vignette=0.36, halation=0.0, bloom=0.0),
                     accent="#FF5A36", made=(
        "After the pictures, the engine annotates the speech like a court record: each claim that a fetched source "
        "confirms gets a small superscript number on its word and a two-row footnote with its source, typed at the foot "
        "of the safe area (y 0.70-0.80). Only the engine writes footnotes, because only it has the sources. So from "
        "you: no superscripts, no numbered labels, no captions restating a claim, nothing typed in y 0.70-0.80 after the "
        "first three seconds. Captions in one seat between y 0.58 and 0.66; the name chip, if any, inside the first "
        "three seconds; the close holds the payoff line plain.")),
    "second-camera": dict(passes=["second_camera"], house=(" One crop of the take for the whole film: never alternate crops, framings or looks of "
                                                           "the take. The engine alone makes the second angle and its cuts."),
                          look=dict(grade=dict(lift=-0.008, gamma=0.98, gain=1.03, sat=1.06, contrast=1.08, temp=0.008, tint=0.0),
                                    grain=0.04, vignette=0.22, halation=0.10, bloom=0.06),
                          accent="#7FE3FF", made=(
        "After the pictures, the engine cuts to a second camera that was never there: the take seen from a few degrees "
        "off axis and a little closer, rebuilt from the depth of the picture so the room slides behind him as it would "
        "for a real second camera; it cuts to the true frame on a stressed word, visits the second angle once more, and "
        "ends on the true frame. Direct for a two-camera show: one framing of the take from you, held, no zoom, no "
        "punch-in, no camera move; captions in one seat; the close stays on the take, plain.")),
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


def _again(ctx, allowed):
    """--args '{"again": "<delivered folder>", "style": id}': the moment of a film already delivered from this source,
    made again in a style the ledger allows. The idea is read from that film's record; nothing is asked of a model."""
    folder = (ctx.args or {}).get("again")
    if not folder:
        return None
    rec = json.load(open(os.path.join(folder, "film.json")))
    if os.path.abspath((rec.get("source") or {}).get("path") or "") != os.path.abspath(ctx.source):
        raise RuntimeError("--args again: that film was made from another source")
    was = (rec.get("style") or {}).get("id")
    ids = [s["id"] for s in allowed if s["id"] != was]
    if not ids:
        raise RuntimeError("--args again: no style outside the last three is left for this moment")
    idea = dict(rec["idea"], styles=[dict(id=ids[0], why=f"made again from {os.path.basename(folder.rstrip('/'))}, which was {was}")],
                again=dict(folder=folder, run_id=rec.get("run_id"), was=was))
    w = rec.get("window") or [idea.get("t0"), idea.get("t1")]
    idea["t0"], idea["t1"] = float(w[0]), float(w[1])
    return idea


def ideas(ctx):
    src = ctx.source
    caps = [os.path.join(os.path.dirname(src), n) for n in sorted(os.listdir(os.path.dirname(src))) if n.endswith(".json3")]
    if caps:
        words, how = captions_words(caps[0]), f"captions {os.path.basename(caps[0])}"
    else:
        words, how = whisper_words(ctx, src), "whisper-large-v3 local"
    json.dump(words, open(_words_file(ctx), "w"))
    allowed = _styles(ctx)
    again = _again(ctx, allowed)
    if again:                                            # a delivered moment made again in another style: no call
        return dict(source=dict(source_log(src), transcript=how, words=len(words)), ideas=[again], chosen=None, again=again["again"], spend=[])
    system = ("You are the ideas stage of Valmera's podcast shorts workflow. You read a whole conversation and find the "
              "moments that become great vertical shorts: a story with a turn, a claim that stops the scroll, a payoff "
              "that lands inside the clip. Each moment must stand alone without the rest of the episode. Prefer a moment "
              "that names things a picture can show as themselves: real people, companies, places, products, documents, "
              "numbers. You find the ideas yourself; nobody gives you a topic.")
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


HOUSE = (" House rules, binding: every piece of type sits inside x 0.05-0.95 and y 0.10-0.80, clear of faces and of the "
         "mark at the top right; the name chip is small, inside y 0.70-0.78, once.")


def _pictures_line(rows):
    rows = [r for r in rows or [] if isinstance(r, dict) and (r.get("shows") or r.get("name"))]
    if not rows:
        return ""
    return (" The engine's pictures, for your information only (place none of them): "
            + "; ".join(f"on \"{r.get('on', '')}\": {r.get('name') or r.get('shows')}" for r in rows) + ".")


PICTURES = (
    "\"pictures\": [7-10 items, in the order he says them, each {\"on\": the quoted words it lands on (its most stressed "
    "word first), \"at\": the seconds printed before the first of those words, \"hold\": seconds on screen (1.2-2.6), "
    "\"kind\": \"named\" | \"thing\" | \"design\", and: "
    "for \"named\" (a real person, company, place, building, product, event or document he names or plainly points to; "
    "a real archive photograph will be searched in Wikimedia Commons and checked by eye): \"name\" its usual English "
    "name, \"is\" person|company|place|product|event|document, \"searches\" one to three Commons searches, the most "
    "specific first (the person at the age or in the act the story is about, the company's building or first product, "
    "the document itself) and the bare name last, \"must_show\" one plain sentence on what the picture must visibly "
    "show, \"shows\" what it is, in eight words; "
    "for \"thing\" (an unnamed physical thing he points at): the same fields, \"name\" being what an archive would "
    "title it; "
    "for \"design\" (figures he says carry the story, or he describes a paper nobody can photograph; with no figure "
    "and no paper there is no design): "
    "\"design\": {\"kind\": \"number\" (one figure) | \"versus\" (two named sides with their values) | \"bars\" (two to "
    "four amounts to compare) | \"document\" (the paper he describes: an email, an invoice, a term sheet, drawn as "
    "that paper with the line that matters marked), \"title\": at most four words or \"\", \"parts\": [{\"value\": the "
    "figure as he says it, at most twelve characters, \"label\": at most five words, \"n\": the figure as a plain number}], \"lines\": for a "
    "document, three to five short lines of the paper in his words, \"mark\": the one line to mark}, and \"shows\"}. "
    "Every word and number of a design comes from what he says; never a fact he does not say, and never his sentence "
    "set again as type: the captions already say it. Prefer \"named\": a film about real people and companies shows "
    "them, each as an archive would hold them (the founder at the age of the story, the first product, the building, "
    "the document itself). For a \"thing\", ask for the famous or historical instance an archive keeps, not the "
    "everyday one. Never the speaker as he looks today: he is on screen. At most two designs. Never ask for a generic "
    "stock scene.], "
    "\"hook\": {\"picture\": the index in pictures of the one that opens the film at frame 0 (the picture a stranger "
    "would stop for: the most famous face or object of the story), \"line\": the hook as at most five words, his own "
    "words where they work, \"accent\": the one or two words of that line that carry it}, "
    "\"payoff\": {\"on\": the quoted words of the payoff line, \"at\": the seconds of its first word}, ")


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
              "clip, the pictures that will be shown on his words, and the direction a film Director will follow. You cut "
              "on word edges, start on the first word of the thought (no throat-clearing), and end on the payoff with a "
              "breath of tail.")
    recipe = RECIPES.get(style["id"]) or {}
    made = (f"# How this style is made (binding: the engine does this part, you direct the rest around it)\n{recipe['made']}\n\n"
            if recipe else "")
    content = (f"# The moment\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{style_menu([style])}\n"
               f"Concepts in its spine are from Valmera's ALIEN list: {', '.join(style['spine'])}. Use what the footage can carry; "
               "a device the engine cannot draw well is left out rather than faked.\n\n" + made +
               f"# What the engine adds to every film (binding)\n{ENGINE}\n\n"
               f"# Taste rules\n{TASTE}\n\n# Who is in it\n{identity_text(ctx.source)[:1500]}\n\n"
               "# Words near the moment: seconds word\n" + " ".join(f"{t:.2f} {w}" for t, w in near) + "\n\n"
               "Return JSON only: {\"in\": seconds (a little before the first word), \"out\": seconds (0.3-0.8 s after the last word), "
               "\"title\": the film's title, \"hook_line\": exact words heard first, \"close_line\": exact last words, "
               "\"speakers\": number of people heard, " + PICTURES +
               "\"direction\": 120-200 words to the Director: the story in one line; how the style above looks on THIS footage; "
               "the one framing of the take; the caption seat and which words take the second voice; the name chip and the words "
               "it lands on; the close; and what stays plain. Anchor every moment on the quoted words, never on seconds: the "
               "Director sees the clip from 0, not the episode's clock. Write it as an editor's brief, not a list of effects.}")
    spend, s = [], {}
    for _ in range(2):                                   # an answer cut short or missing a field is asked for once more
        text, use = llm.ask(system, content, effort="medium", max_tokens=16000)
        spend.append(dict(stage="script", what="clip, pictures and direction", **use))
        try:
            s = llm.json_block(text)
        except ValueError:
            s = {}
        if isinstance(s, dict) and all(s.get(k) not in (None, "") for k in ("in", "out", "direction")) and s.get("pictures"):
            break
    else:
        raise RuntimeError("the script stage gave no clip, pictures and direction")
    t0, t1 = float(s["in"]), float(s["out"])
    if not (5.0 <= t1 - t0 <= 90.0):
        raise RuntimeError(f"script window {t0}-{t1} is not a short")
    pictures = [r for r in s.get("pictures") or [] if isinstance(r, dict)]
    return dict(title=s.get("title") or idea.get("title"), style=style, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line"), close_line=s.get("close_line"), speakers=int(s.get("speakers") or 1),
                direction=(f"Style: {style['name']}. " + s["direction"].strip() + " " + ENGINE + _pictures_line(pictures) + HOUSE
                           + (recipe.get("house") or "")),
                pictures=pictures, hook=s.get("hook") if isinstance(s.get("hook"), dict) else None,
                payoff=s.get("payoff") if isinstance(s.get("payoff"), dict) else None,
                passes=recipe.get("passes") or [], look=recipe.get("look"), accent=recipe.get("accent"), sounds=recipe.get("sounds"), idea=idea,
                spend=spend)


def _alien():
    import importlib.util
    import sys
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "valmera-podcast-shorts", "alien")
    if d not in sys.path:
        sys.path.insert(0, d)
    import alien_render
    return alien_render


def supply(ctx, script):
    """The shared supply, then the story's own pictures: every person, company, place and document the script named
    goes down the named route and is checked by eye (alien/earned.py). Nothing found is nothing shown."""
    r = chain.supply(ctx, script)
    _alien()
    import brand
    import earned
    e = earned.find(ctx, script, llm, brand.worker_dir())
    r["earned"] = dict(planned=e.get("plan"), wanted=e.get("wanted"), found=e.get("found"), said=e.get("said"), credits=e.get("credits"))
    r["spend"] = (r.get("spend") or []) + (e.get("spend") or [])
    return r


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
    if rd.get("plan") and os.path.exists(rd["plan"]):   # the sounds of the plan that was drawn, not of the Director's draft
        m = chain.no_music(rd["plan"])
        r["checks"]["ok"] = bool(r["checks"]["length_ok"] and r["checks"]["mark_ok"] and r["checks"]["endcard_ok"] and r["checks"]["loud_ok"] and m["ok"])
        r["checks"]["music"] = m
    if rd.get("alien"):
        r["checks"]["alien"] = rd["alien"]
        r["checks"]["ok"] = bool(r["checks"]["ok"] and rd["alien"].get("ok", True))
    return r
