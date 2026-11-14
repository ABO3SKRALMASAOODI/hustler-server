"""Write for the ear: a model rewrites a checked script so a narrator can say it (14 Nov, lane VO2).

    script2, report = for_ear(script, form="story" | "long-form")

The writer's script is true and checked; it is often written for the eye: long sentences, the key word buried in the
middle, figures stacked in one breath. The references' narrators (judge teardown section 12, the 8 metromedia reels)
speak in sentences of 9 to 14 words, open one sentence in three on a hinge (But, So, Then, And, Because), turn every
three or four sentences, drop 1-4 word verdicts ("Rejected.", "Then, 2008 hit."), land a name alone in a short
sentence, and end each sentence low and falling (voice_ref.json: 1.2-2.9 semitones under the phrase).

One model call rewrites every segment's `say` and its delivery (`emphasis`, `pause_after`, `melody`, `pace`,
`direction`) to that shape; a second call, blind to the first one's reasons, compares old and new segment by segment
and names any segment whose facts, figures, names or claims changed. A flagged segment keeps its original words.
Then measured checks, on strings only: the spoken form has no digits, every name and ledger word the original
segment said is still said, the stressed words are words of the line, and the film's length stays within 10%.
Nothing here interprets the words by rule; the model rewrites, the model audits, strings are compared.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import writer  # noqa: E402

KEEP_LEN = 0.10           # the film's spoken words may move this much (its pictures are planned on the old length)
FIELDS = ("say", "emphasis", "pause_after", "melody", "pace", "direction")

SHAPE = (
    "How the best narrators of short documentary films speak (measured on the films that are the bar):\n"
    "- Sentences of 9 to 14 words; one idea a sentence; a long sentence is split where a speaker would breathe.\n"
    "- About one sentence in three opens on a hinge: But, So, Then, And, Because. A real turn every three or four "
    "sentences, said as a turn (\"But the bank said no.\").\n"
    "- Short verdicts of one to four words where the story lands (\"Rejected.\", \"Then, 2008 hit.\", \"That was it.\").\n"
    "- A person's name lands alone in a short sentence the first time it matters (\"His name was Fred Smith.\").\n"
    "- The new information comes late in its sentence, where English puts the main stress, so the voice can climb to "
    "it and fall; never bury it mid-sentence behind a list. The stressed word is the word that carries the news (often "
    "the figure: TWENTY-SEVEN thousand dollars, not dollars), never a unit, an article or a pronoun.\n"
    "- Numbers a listener can hold: one figure a sentence, said in full words the way a narrator says them "
    "(\"twenty-seven thousand dollars\", \"nineteen seventy-three\"), never two figures racing in one breath.\n"
    "- Every sentence ends low and settled except a real question; questions are rare and earn their place.\n")

QUESTION = {
    "story": "Exactly one segment in the setup asks the question the film will answer (the viewer's own question, said by "
             "the narrator: \"So how do you save a company with five thousand dollars?\"); never a question to the viewer "
             "about themselves, never 'did you know'.",
    "long-form": "Each chapter's first segment asks that chapter's question out loud, in the narrator's words (the chapter "
                 "card prints it; the voice says it), and the chapter then answers it.",
}

SYSTEM = ("You are the voice editor of a documentary studio. A checked script reaches you; you rewrite it for the ear so a "
          "narrator can perform it, without changing a single fact. You keep every fact, figure, date, name and claim "
          "exactly; you add no fact, drop no fact, round nothing, and never dramatise beyond the original. You may reorder "
          "words inside a segment, split or join its sentences, add hinge words (But, So, Then, And) and short verdicts made "
          "only of what the segment already says.")


def _segments_view(script):
    out = []
    for i, s in enumerate(script.get("segments") or []):
        v = {"i": i, "say": s.get("say", "")}
        for k in ("beat", "chapter", "pace", "melody", "emphasis", "pause_after", "direction"):
            if s.get(k) not in (None, "", []):
                v[k] = s[k]
        if s.get("ticks"):
            v["must_keep_words"] = [t.get("word") for t in s["ticks"] if t.get("word")]
        out.append(v)
    return out


def rewrite(script, form="story", effort="medium"):
    """(the model's segments, usage)"""
    chapters = script.get("chapters") or []
    content = (SHAPE + "\n" + QUESTION.get(form, QUESTION["story"]) + "\n\n"
               + (f"# Chapters\n{json.dumps(chapters, ensure_ascii=False)}\n\n" if chapters else "")
               + f"# Names the narrator says (keep each one, spelled exactly)\n{json.dumps(script.get('names') or [], ensure_ascii=False)}\n\n"
               + f"# The script, segment by segment\n{json.dumps(_segments_view(script), ensure_ascii=False, indent=0)}\n\n"
               "Never say the same sentence twice in the film: when a segment already asks the question, it stays there and no "
               "other segment repeats it.\n"
               "Rewrite every segment, in the same order, one for one (the pictures are planned per segment). Keep each "
               "segment's length within about ten percent of its original words. Keep `must_keep_words` exactly as written "
               "somewhere in that segment's say. The spoken form stays: every number, year, unit and currency in words the way "
               "it is said, no digits, no symbols, no abbreviations; punctuation is commas, full stops and question marks only (no "
               "dashes, colons, semicolons or brackets: a narrator breathes at a comma or a full stop).\n"
               "For each segment also direct the performance: `emphasis` one or two words of the new say that carry it (the "
               "last stressed word of a sentence first), `pause_after` seconds of silence after it (0.15 to 0.9; longer after a "
               "verdict, a turn or a chapter's end, short inside a run), `melody` falling|rising|level|wide, `pace` "
               "slow|steady|brisk|fast, `direction` a few concrete words for the actor (energy, pace, where to lean).\n\n"
               "Return JSON only: {\"segments\": [{\"i\": the index, \"say\", \"emphasis\", \"pause_after\", \"melody\", "
               "\"pace\", \"direction\"}]}")
    text, use = writer.ask(SYSTEM, content, effort=effort, max_tokens=32000, what="ear")
    return writer.json_block(text).get("segments") or [], use


AUDIT = ("You audit a rewrite of a documentary script for facts only. For each pair of segments (the original, the rewrite) "
         "decide whether the rewrite changed what is asserted: a fact, figure, date, name, quantity, order of events, cause or "
         "claim added, dropped or altered, or a certainty made stronger or weaker. Style, word order, split sentences, hinge "
         "words and short verdicts that restate the original are not changes.")


def audit(pairs, effort="low"):
    """([indices whose facts changed], {i: why}, usage)"""
    content = (f"# Pairs\n{json.dumps(pairs, ensure_ascii=False, indent=0)}\n\n"
               "Return JSON only: {\"changed\": [{\"i\": index, \"why\": a few words}]} (an empty list when no segment changed).")
    text, use = writer.ask(AUDIT, content, effort=effort, max_tokens=12000, what="ear-audit")
    ch = writer.json_block(text).get("changed") or []
    why = {int(c["i"]): str(c.get("why") or "") for c in ch if isinstance(c, dict) and str(c.get("i", "")).lstrip("-").isdigit()}
    return sorted(why), why, use


def _words(s):
    return [w.strip(".,;:!?\"“”'’()—–-").lower() for w in str(s).split()]


def _has(say, phrase):
    """the phrase's words appear in order, together, in say (strings only)"""
    a, b = _words(say), [w for w in _words(phrase) if w]
    return bool(b) and any(a[k:k + len(b)] == b for k in range(len(a) - len(b) + 1))


def measured_problems(old, new, names):
    """why the new segment cannot stand, by string comparison (empty when it can)"""
    p = []
    say = str(new.get("say") or "")
    if not say.strip():
        p.append("empty")
    if any(c.isdigit() for c in say):
        p.append("digits in the spoken form")
    if any(c in say for c in "—–;:()[]"):
        p.append("punctuation a narrator cannot breathe on")
    for n in names:
        if _has(old.get("say", ""), n) and not _has(say, n):
            p.append(f"lost the name {n}")
    for t in old.get("ticks") or []:
        if t.get("word") and not _has(say, t["word"]):
            p.append(f"lost the ledger word {t['word']}")
    em = new.get("emphasis") or []
    if not isinstance(em, list) or not em or not all(_has(say, e) for e in em):
        p.append("stressed words not in the line")
    return p


def _sentences(say):
    out, cur = [], []
    for w in _words(say.replace(".", " . ").replace("?", " ? ").replace("!", " ! ")):
        if w == "":                       # a lone full stop, question or exclamation mark ends the sentence
            if cur:
                out.append(" ".join(cur))
                cur = []
            continue
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return [x for x in out if len(x.split()) >= 3]


def repeated(out_segs, old_segs, kept):
    """{i: sentence} for a rewritten segment that adds a sentence another segment of the film already says"""
    bad = {}
    for i in kept:
        mine = set(_sentences(str(out_segs[i].get("say") or ""))) - set(_sentences(str(old_segs[i].get("say") or "")))
        for j, o in enumerate(out_segs):
            if j != i and i not in bad:
                hit = mine & set(_sentences(str(o.get("say") or "")))
                if hit:
                    bad[i] = sorted(hit)[0]
    return bad


def for_ear(script, form="story", effort="medium", log=print):
    """(script for the ear, report). The original segment stands wherever the rewrite fails a check."""
    segs = script.get("segments") or []
    names = list(script.get("names") or [])
    try:
        got, use1 = rewrite(script, form, effort)
    except Exception as e:  # noqa: BLE001
        log(f"ear: rewrite failed, the script stands: {e!r}"[:300])
        return script, dict(ok=False, error=repr(e)[:300], spend=[])
    by_i = {int(g["i"]): g for g in got if isinstance(g, dict) and str(g.get("i", "")).isdigit()}
    pairs = [{"i": i, "original": s.get("say", ""), "rewrite": (by_i.get(i) or {}).get("say", "")} for i, s in enumerate(segs)]
    try:
        changed, why, use2 = audit(pairs)
    except Exception as e:  # noqa: BLE001
        log(f"ear: audit failed, the script stands: {e!r}"[:300])
        return script, dict(ok=False, error="audit " + repr(e)[:300], spend=[use1])
    out = json.loads(json.dumps(script))
    kept, reverted = [], {}
    for i, s in enumerate(segs):
        g = by_i.get(i)
        if g is None:
            reverted[i] = "missing in the rewrite"
            continue
        if i in changed:
            reverted[i] = "facts changed: " + why.get(i, "")
            continue
        g = dict(g)
        try:
            g["pause_after"] = min(max(float(g.get("pause_after", s.get("pause_after") or 0.3)), 0.12), 1.2)
        except (TypeError, ValueError):
            g["pause_after"] = s.get("pause_after") or 0.3
        p = measured_problems(s, g, names)
        if p:
            reverted[i] = "; ".join(p)
            continue
        for k in FIELDS:
            if g.get(k) not in (None, "", []):
                out["segments"][i][k] = g[k]
        out["segments"][i]["say_written"] = s.get("say", "")
        kept.append(i)
    for i, sent in repeated(out["segments"], segs, kept).items():
        out["segments"][i] = json.loads(json.dumps(segs[i]))
        kept.remove(i)
        reverted[i] = f"says again what another segment says: {sent}"
    n0 = sum(len(str(s.get("say", "")).split()) for s in segs)
    n1 = sum(len(str(s.get("say", "")).split()) for s in out["segments"])
    if n0 and abs(n1 - n0) / n0 > KEEP_LEN:
        log(f"ear: {n1} words against {n0}: outside {KEEP_LEN:.0%}, the script stands")
        return script, dict(ok=False, error=f"length {n1} against {n0}", spend=[use1, use2])
    if out["segments"]:
        if "hook_line" in out:
            out["hook_line"] = out["segments"][0]["say"]
        if "close_line" in out:
            out["close_line"] = out["segments"][-1]["say"]
    rep = dict(ok=True, kept=len(kept), reverted=reverted, words=[n0, n1], segments=len(segs), spend=[use1, use2])
    log(f"ear: {len(kept)}/{len(segs)} segments rewritten for the ear, {len(reverted)} kept as written; {n0} -> {n1} words")
    return out, rep


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="rewrite a writer script (JSON with segments) for the ear")
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--form", default="story", choices=["story", "long-form"])
    ap.add_argument("--first", type=int, default=0, help="only the first N segments (a test)")
    a = ap.parse_args()
    sc = json.load(open(a.script))
    sc = sc.get("script", sc)
    sc = sc.get("script", sc) if isinstance(sc.get("script"), dict) else sc
    if a.first:
        sc = dict(sc, segments=sc["segments"][:a.first])
    new, rep = for_ear(sc, a.form)
    json.dump(dict(script=new, report=rep), open(a.out, "w"), ensure_ascii=False, indent=1)
