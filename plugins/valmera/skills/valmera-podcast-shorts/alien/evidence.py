"""Footnoted speech (ALIEN C6): the claims a source confirms, and where each one is said.

    find(ctx, script, llm) -> {"footnotes": [...], "spend": [...]}      writes supply/alien/footnotes.json

A model reads the clip's words and names the checkable claims and the encyclopedia pages that would
settle them. The pages are fetched. A second call writes a footnote only for a claim a page states,
and must copy the sentence fragment that states it. That fragment is then looked for in the page's own
text: a comparison of two strings. A footnote whose fragment is not there is dropped. Never invented:
no source, no footnote.
"""
import json
import os
import urllib.parse
import urllib.request

API = "https://en.wikipedia.org/w/api.php"
AGENT = "workflow-footnotes/1.0 (video editing tool)"
MAX_PAGES, MAX_NOTES, PAGE_CHARS = 6, 4, 9000


def page_text(title):
    """(resolved title, plain text) of an encyclopedia page, or (None, '')."""
    q = urllib.parse.urlencode(dict(action="query", prop="extracts", explaintext=1, redirects=1, titles=title, format="json", formatversion=2))
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 a page that cannot be read confirms nothing
        return None, ""
    pages = (d.get("query") or {}).get("pages") or []
    if not pages or pages[0].get("missing"):
        return None, ""
    return pages[0].get("title"), pages[0].get("extract") or ""


def _squash(s):
    return " ".join(str(s).split())


def _numbers(s):
    """The runs of digits in a string, as written."""
    out, cur = [], ""
    for ch in str(s):
        if ch.isdigit():
            cur += ch
        elif cur:
            out.append(cur)
            cur = ""
    return out + ([cur] if cur else [])


def find(ctx, script, llm):
    out_path = os.path.join(ctx.supply, "alien", "footnotes.json")
    if os.path.exists(out_path):
        return json.load(open(out_path))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    idx = json.load(open(os.path.join(ctx.supply, "index.json")))
    words = idx["words"]
    lines = " ".join(f"{i}:{w['w']}" for i, w in enumerate(words))
    spend = []
    sys1 = ("You prepare footnotes for a short film cut from an interview. You read what is said and name the claims an "
            "encyclopedia could settle: a price, a date, a count, a named deal, a named event. An opinion or a private "
            "anecdote is not a claim.")
    c1 = (f"# The film\n{script.get('title')}\n\n# Words, as index:word\n{lines}\n\n"
          "Return JSON only: {\"claims\": [up to 5 items {\"w\": index of the last word of the claim as spoken, \"claim\": the claim "
          "in one plain sentence, \"titles\": [one or two English Wikipedia page titles that would state it]}]}. Fewer is fine; none is fine.")
    text, use = llm.ask(sys1, c1, effort="low", max_tokens=3000)
    spend.append(dict(stage="score", what="claims to check", **use))
    got = llm.json_block(text)
    claims = [c for c in (got.get("claims") if isinstance(got, dict) else got) or [] if isinstance(c, dict) and str(c.get("w", "")).lstrip("-").isdigit()]
    pages = {}
    for c in claims:
        for t in (c.get("titles") or [])[:2]:
            if len(pages) < MAX_PAGES and t not in pages:
                title, body = page_text(t)
                if title and body:
                    pages[t] = dict(title=title, text=body)
    notes, rejected = [], []
    if claims and pages:
        sys2 = ("You write footnotes for a film, like a court record. A footnote exists only when the page text you are given "
                "states the fact. You copy the fragment that states it exactly, character for character. If no page states it, "
                "you leave the claim without a footnote. You never use what you know from elsewhere.")
        c2 = ("# Claims\n" + json.dumps(claims, ensure_ascii=False) + "\n\n# Pages\n"
              + "\n\n".join(f"## {p['title']}\n{p['text'][:PAGE_CHARS]}" for p in pages.values())
              + "\n\nReturn JSON only: {\"footnotes\": [{\"w\": the claim's w, \"note\": the fact in at most 70 characters, with its number "
                "or year, in plain words, no quotation marks, \"quote\": a fragment of 6 to 30 words copied exactly from one page that "
                "states it, \"title\": that page's title}]}. Only claims a page states.")
        by_title = {p["title"]: p["text"] for p in pages.values()}
        for attempt in range(2):                           # a note that fails the comparison is asked for once more, with the reason
            ask = c2
            if rejected:
                ask += ("\n\n# Rejected by the check, write these again so they pass\n" + json.dumps(rejected, ensure_ascii=False)
                        + "\nA year or a figure may stand in a note only when the fragment you copy contains it. "
                          "Return every footnote again, the ones that passed included.")
            elif attempt:
                ask += ("\n\nYou returned none. Read the pages once more: a page that states what the company is, when it began, "
                        "or how large a market share is settles the matching claim. Still none is a valid answer.")
            text, use = llm.ask(sys2, ask, effort="medium", max_tokens=6000)
            spend.append(dict(stage="score", what="footnotes from sources", **use))
            got = llm.json_block(text)
            notes, rejected = [], []
            for n in (got.get("footnotes") if isinstance(got, dict) else got) or []:
                body = by_title.get(n.get("title"))
                q = _squash(n.get("quote") or "")
                if not body or len(q) < 20 or q not in _squash(body):          # the fragment must be in the page: two strings compared
                    rejected.append(dict(note=n.get("note"), why="its fragment is not in the page, character for character"))
                    continue
                if any(num not in _numbers(q) for num in _numbers(n.get("note") or "")):   # and the note's numbers in the fragment
                    rejected.append(dict(note=n.get("note"), why="a number in the note is not in its fragment"))
                    continue
                w = int(n["w"])
                if not (0 <= w < len(words)) or not n.get("note"):
                    continue
                notes.append(dict(w=w, t_s=round(float(words[w]["t1"]), 3), note=_squash(n["note"])[:90], quote=q,
                                  source=f"Wikipedia, {n['title']}", url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(n["title"].replace(" ", "_"))))
            if notes and not rejected:
                break
    notes.sort(key=lambda n: n["t_s"])
    kept = []
    for n in notes:                                                        # one footnote at a time, room to read each
        if not kept or n["t_s"] - kept[-1]["t_s"] >= 3.0:
            kept.append(n)
    kept = kept[:MAX_NOTES]
    for i, n in enumerate(kept):
        n["n"] = i + 1
    res = dict(footnotes=kept, claims=len(claims), pages=sorted(p["title"] for p in pages.values()), dropped=len(notes) - len(kept), rejected=rejected, spend=spend)
    json.dump(res, open(out_path + ".part", "w"), ensure_ascii=False, indent=1)
    os.replace(out_path + ".part", out_path)
    return res
