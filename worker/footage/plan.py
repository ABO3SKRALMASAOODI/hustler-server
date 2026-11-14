"""The two reading steps before any byte is fetched: what to search for, and which sources to open.
A model reads the words and decides; nothing here reads them by rule."""
import json

from . import ask

PLAN_SYSTEM = """You are the footage researcher for a short documentary film (a vertical social video, one new shot every 1-3 seconds). The best films in this form are cut from REAL VIDEO of the actual people, places and events: TV interviews with the founder, news reports of the day, launch and event footage, archive newsreels, the company's own films, documentary footage of the real place. Stills and generic stock are the last resort.

You get the film's lines (numbered) and the named things the writer listed. Write the searches that find real footage of each thing.

For each subject (keep the writer's names; add a subject only when a line needs a real thing the list lacks, such as the place the line happens in):
- "name": the subject.
- "kind": person, place, thing, company or event.
- "must_show": one sentence, what a frame must plainly show to count (the person himself on camera; the jet in that livery; the strip at night; the machine and its panels). Name every form the subject can take on camera.
- "searches": 3 to 5 searches, each {"where": "youtube" or "archive", "q": "..."}. YouTube for interviews ("Fred Smith interview"), news ("FedEx 1973 news report"), launches, documentaries and the company's own films; archive.org for newsreels, government and public-domain film before about 1980. Write them the way an archivist would search: the name with the kind of footage. Never a search for a still photo, a meme or a reaction.

For each line: "i" (its number), "show" (what the picture should show while it is said, plainly, the real thing first), and "subjects" (the subject names whose footage can carry it, best first).

When a line's brief (see) names a moment, also search for that moment itself, such as the release film's own scene or the news report of the day, in the version the brief names.

Answer with one JSON object only: {"subjects":[...],"lines":[...]}"""

RANK_SYSTEM = """You choose which videos a footage researcher should open. For each subject you get YouTube and archive.org results (number, title, channel, length in seconds, views).

Keep the sources most likely to hold real, clean video of that very subject: the person interviewed or filmed himself, a news report of the day, event or launch footage, archive newsreel, the company's own film, a documentary with real footage. A long interview or documentary is good: the editor takes 1-3 seconds of it.

Drop: another editor's short or reel (it carries their captions), slideshows of photos with a voice, AI narrations, reaction and commentary videos, top-10 lists, music videos, games, animations about the subject, a namesake that is not the subject, anything over an hour.

Answer with one JSON object only: {"keep":[{"s":subject number,"h":result number,"why":"at most eight words"}]}, at most the requested number of sources per subject, best first, and only sources you would bet on."""


def plan(lines, named, about="", budget_s=90.0):
    """({"subjects": [...], "lines": [...]}, usd)."""
    rows = [f"{ln['i']}. {ln['say']}" + (f"   [writer's pictures: {', '.join(ln.get('pictures') or [])}]" if ln.get("pictures") else "")
            + (f"   [see: {json.dumps([dict(thing=x.get('thing'), as_of=x.get('as_of'), moment=x.get('moment')) for x in ln['see'] if isinstance(x, dict)], ensure_ascii=False)}]"
               if ln.get("see") else "")
            for ln in lines]
    subj = [dict(name=n.get("name"), kind=n.get("is"), must_show=n.get("must_show")) for n in named or []]
    content = (f"Film: {about}\n\nLines:\n" + "\n".join(rows) + "\n\nNamed things:\n" + json.dumps(subj, ensure_ascii=False))
    txt, usd = ask.ask(PLAN_SYSTEM, content, "footage_plan", budget_s=budget_s, max_tokens=6000)
    obj = ask.json_obj(txt)
    subs = []
    for s in obj.get("subjects") or []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        q = [x for x in s.get("searches") or [] if isinstance(x, dict) and x.get("q")]
        given = next((n.get("is") for n in named or [] if n.get("name") == s["name"]), None)
        kind = str(s.get("kind") or given or "thing").lower()
        subs.append(dict(name=str(s["name"]), kind=kind, must_show=str(s.get("must_show") or s["name"]),
                         searches=[dict(where="archive" if x.get("where") == "archive" else "youtube", q=str(x["q"])[:120]) for x in q[:5]]))
    # a caller's own video searches (opt-in "video_searches" on a named row; only the stories skill sends them, e.g. a
    # person's interview of the story's year, the work's trailer): they lead that subject's searches, and a subject the
    # model left out is added with them, so the caller's explicit search is always run
    for n in named or []:
        vq = [str(q)[:120] for q in n.get("video_searches") or [] if isinstance(q, str) and q.strip()]
        if not vq or not n.get("name"):
            continue
        sub = next((x for x in subs if x["name"] == n["name"]), None)
        if sub is None:
            sub = dict(name=str(n["name"]), kind=str(n.get("is") or "thing").lower(), must_show=str(n.get("must_show") or n["name"]), searches=[])
            subs.append(sub)
        have = {x["q"] for x in sub["searches"]}
        sub["searches"] = ([dict(where="youtube", q=q) for q in vq if q not in have] + sub["searches"])[:5]
    names = {s["name"] for s in subs}
    lns = []
    for ln in obj.get("lines") or []:
        if isinstance(ln, dict) and isinstance(ln.get("i"), int):
            lns.append(dict(i=ln["i"], show=str(ln.get("show") or ""), subjects=[x for x in ln.get("subjects") or [] if x in names]))
    return dict(subjects=subs, lines=lns), usd


def rank(subjects, hits, per=3, budget_s=60.0):
    """([(subject index, hit dict, why)], usd). hits: {subject index: [hit]}."""
    blocks = []
    for si, s in enumerate(subjects):
        rows = [f"  {hi}. {h['title']} | {h.get('channel') or '?'} | {int(h['duration']) if h.get('duration') else '?'} s | "
                f"{h.get('views') or '?'} views | {h['where']}" for hi, h in enumerate(hits.get(si) or [])]
        if rows:
            blocks.append(f"Subject {si}: {s['name']} (must show: {s['must_show']})\n" + "\n".join(rows))
    if not blocks:
        return [], 0.0
    txt, usd = ask.ask(RANK_SYSTEM, f"Keep at most {per} sources per subject.\n\n" + "\n\n".join(blocks), "footage_rank", budget_s=budget_s, max_tokens=3000)
    out, seen = [], set()
    for k in ask.json_obj(txt).get("keep") or []:
        try:
            si, hi = int(k.get("s")), int(k.get("h"))
            h = (hits.get(si) or [])[hi]
        except (TypeError, ValueError, IndexError):
            continue
        n = sum(1 for o in out if o[0] == si)
        if n < per and (si, h["id"]) not in seen:
            seen.add((si, h["id"]))
            out.append((si, h, str(k.get("why") or "")[:80]))
    return out, usd
