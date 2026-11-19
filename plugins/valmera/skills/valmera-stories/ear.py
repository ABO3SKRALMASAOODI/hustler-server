"""Write for the ear: a model rewrites a checked script so a narrator can say it (14 Nov, lane VO2).

    script2, report = for_ear(script, form="story" | "long-form")

The writer's script is true and checked; it is often written for the eye: long sentences, the key word buried in the
middle, figures stacked in one breath. The references' narrators (judge teardown section 12, the metromedia reels) carry
a thought on with commas and hinges until it lands (11-13 words a line, about one comma a sentence), open one sentence
in three on a hinge, turn every three or four sentences and land the news late in the sentence.

17 Nov (stories lane): the earlier shape here, "sentences of 9 to 14 words, one idea a sentence, verdicts, the name
alone in a short sentence, every sentence ends low", read on Phonon Easton as a full stop every few seconds: the three
Easton story films of 14 Nov measured 7.3-7.9 words a line against 11.1-12.6 for the metromedia narrators, 69-73% of
line ends falling more than 2 st and 0-12% rising (references 53-55% and 20-29%), cadence sameness 2.6-2.9 st against
3.4-4.7. Phonon takes no direction: it hears only the words and the punctuation, so the sentence shape is the melody.
The short-film shape is now the long-form lane's ear shape at a reel's length (SHAPE).

One model call rewrites every segment's `say` and its delivery (`emphasis`, `pause_after`, `melody`, `pace`,
`direction`) to that shape; a second call, blind to the first one's reasons, compares old and new segment by segment
and names any segment whose facts, figures, names or claims changed. A flagged segment keeps its original words.
Then measured checks, on strings only: the spoken form has no digits, every name and ledger word the original
segment said is still said, the stressed words are words of the line, and the film's length stays within 10%.
Nothing here interprets the words by rule; the model rewrites, the model audits, strings are compared.

15 Nov (the independent check of the fact gate): the rewrite gave lines claims no quote held ("Pixar had never made a
movie this long"). Each segment now carries the verified quotes it stands on, the rewrite is told to add no fact beyond
them and the auditor checks against them; both calls are pinned to OpenAI (EAR_MODEL). The ear is never the last word:
writer.final_support judges every line on its quotes after it (wf3.final_gate).

15 Nov, later (the spine check): the core segments (hook, turn, payoff, close) also carry `its_point`, the claim the
story's verified spine gives that beat (writer.spine_points), and the rewrite must say it at least as sharply.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import writer  # noqa: E402

KEEP_LEN = 0.10           # the setup's segments keep about this much of their length (the hook and the close may land shorter)
EAR_MODEL = "gpt-6.1-sol"   # pinned to OpenAI: writer.ask would turn to Anthropic when its key is present (Anthropic spend: $0)
FIELDS = ("say", "emphasis", "pause_after", "melody", "pace", "direction")

SHAPE = (
    "How the best narrators of short documentary films speak (measured on the films that are the bar; the narrator's "
    "engine hears only the words and the punctuation, so the sentence shape IS the melody):\n"
    "- A thought runs on with a comma and 'and', 'but', 'so', 'until', 'which', 'when' while it is still moving; a full "
    "stop comes only where it lands. About one full stop every twelve words, most sentences with a comma: a comma keeps "
    "the voice up and carries the listener into the next half, a full stop brings it down.\n"
    "- Lengths mix: a run of twelve to twenty words that builds, then a short line of three to six words that lands it. "
    "Never three short sentences in a row. A fragment without its verb (\"Sony engineer Ken Kutaragi.\") is never a "
    "sentence of its own: the name rides at the end of the clause that needs it (\"and the engineer who would not let "
    "it go was Ken Kutaragi.\").\n"
    "- About one sentence in three opens on a hinge: But, So, Then, And, Because. A real turn every three or four "
    "sentences, said as a turn (\"But the bank said no.\"); a short verdict of two to five words only where the story "
    "truly lands, at most one in four sentences.\n"
    "- The new information comes late in its sentence, where English puts the main stress, so the voice climbs to it; "
    "never bury it mid-sentence behind a list. The stressed word is the word that carries the news (often the figure: "
    "TWENTY-SEVEN thousand dollars, not dollars), never a unit, an article or a pronoun.\n"
    "- Numbers a listener can hold: one figure a sentence, said in full words the way a narrator says them "
    "(\"twenty-seven thousand dollars\", \"nineteen seventy-three\"), never two figures racing in one breath.\n"
    "- Each fact is said plainly once: no sentence corrects a mistake the listener was not making ('not X, but Y'). "
    "Questions are rare and earn their place.\n")

# the reference narrators' joins (VO2 check, 14 Nov: 0.25 s [0.18-0.26], about four real pauses a minute)
PAUSES = {
    "story": "0.18 to 0.26 inside the story; 0.35 to 0.5 only where the story turns, at most three in the film",
    "long-form": "0.18 to 0.26 inside a run; 0.4 only at a consequential turn, no routine long chapter pauses",
}
HARD = ("- Hard words: a long or rare compound a speech engine runs together is written split, the way it is said "
        "(\"retro rockets\"); a word with two readings gets the spelling of the one meant; names stay exactly as spelled.\n")
SHAPE += HARD

# Long form (16 Nov): the old short-film shape (9-14 word sentences, one idea each, verdicts, every sentence ending low)
# read on Easton as a full stop every few seconds: 78% of line ends fell more than 2 semitones and 4% rose, against 53-55%
# and 20-29% for the metromedia narrators, whose sentences run 12-16 words with about one comma each. Phonon hears only the
# words and punctuation, so the sentence shape is the melody.
LONG_SHAPE = (
    "How the best documentary narrators speak (measured on the films that are the bar):\n"
    "- A thought runs on with commas and 'and', 'but', 'so', 'until', 'which' while it is still moving; a full stop comes "
    "only where it lands. About one full stop every fourteen words, most sentences with a comma.\n"
    "- Lengths mix widely: a run of eighteen to twenty-five words that builds, then a short line of three to six words "
    "that lands it. Never three short sentences in a row.\n"
    "- The new information comes late in its sentence, where the voice climbs to it; the stressed word carries the news.\n"
    "- Numbers a listener can hold: one figure a sentence, said in full words.\n"
    "- Each fact is said plainly once: no sentence corrects a mistake the listener was not making ('not X, but Y'), "
    "none reminds the listener of what they just heard.\n") + HARD
SHAPES = {"long-form": LONG_SHAPE}

# the voice a later rewrite keeps (writer.final_support restates an unsupported line in the ear's style)
KEEP_STYLE = ("The line is spoken by a narrator over pictures and was already rewritten for the ear: keep that voice. A "
              "thought runs on with commas and 'and', 'but', 'so' until it lands, a full stop only where it lands (about one "
              "every twelve words, most sentences with a comma); lengths mixed, never three short sentences in a row, never a "
              "verbless fragment as a sentence; hinge words (But, So, Then) where the story turns; the news late in its "
              "sentence; numbers and years in words; commas and full stops only (no dashes, colons, semicolons or "
              "brackets); about the same length as now.")

QUESTION = {
    "story": "Exactly one segment in the setup asks the question the film will answer (the viewer's own question, said by "
             "the narrator: \"So how do you save a company with five thousand dollars?\"); never a question to the viewer "
             "about themselves, never 'did you know'.",
    "long-form": "Open chapters with concrete situations and changing stakes. Ask a question aloud only when it earns its place; "
                 "never impose one on every chapter. Preserve varied sentence lengths and the slower breathing of a long documentary.",
}

SYSTEM = ("You are the voice editor of a documentary studio. A checked script reaches you; you rewrite it for the ear so a "
          "narrator can perform it, without changing a single fact. You keep every fact, figure, date, name and claim "
          "exactly; you add no fact, drop no fact, round nothing, and never dramatise beyond the original. You may reorder "
          "words inside a segment, split or join its sentences, add hinge words (But, So, Then, And) and short verdicts made "
          "only of what the segment already says. Each segment comes with `its_quotes`, the verified source sentences it "
          "stands on: say nothing that the segment and those quotes do not already state. No new superlative, record, first, "
          "never, only or comparison; no new count, length, setting, scene, time, title, role, motive or feeling; no new "
          "person or company as the one who acted (keep who did what exactly as the segment names them); a restated verb "
          "keeps its exact strength (\"made changes\" never becomes \"rewrote\"). A segment with `its_point` carries the "
          "story's spine (the hook, the turn, the payoff, the close): its rewrite says that point at least as sharply, both "
          "halves of a collision, the price and who paid it, the irony, never a flat statement of record or one half.")


def _quotes_of(script):
    return {f.get("id"): f.get("quote") for f in script.get("facts") or [] if f.get("id") and f.get("quote")}


def _segments_view(script):
    out = []
    qb = _quotes_of(script)
    points = writer.spine_points(script)
    for i, s in enumerate(script.get("segments") or []):
        v = {"i": i, "say": s.get("say", ""), "its_quotes": [qb[f] for f in s.get("facts") or [] if f in qb]}
        if i in points:
            v["its_point"] = [p["claim"] for p in points[i]]
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
    content = (SHAPES.get(form, SHAPE) + "\n" + QUESTION.get(form, QUESTION["story"]) + "\n\n"
               + (f"# Chapters\n{json.dumps(chapters, ensure_ascii=False)}\n\n" if chapters else "")
               + f"# Names the narrator says (keep each one, spelled exactly)\n{json.dumps(script.get('names') or [], ensure_ascii=False)}\n\n"
               + f"# The script, segment by segment\n{json.dumps(_segments_view(script), ensure_ascii=False, indent=0)}\n\n"
               "Never say the same sentence twice in the film: when a segment already asks the question, it stays there and no "
               "other segment repeats it.\n"
               "Rewrite every segment, in the same order, one for one (the pictures are planned per segment). Keep each "
               "middle segment's length within about ten percent of its original words. The hook (the first segment) and "
               "the close (the last) may come out shorter: the hook opens on its collision, the story's strongest image or "
               "claim with its known name, never on a date or a setting, and the close is one landing line on the fact that "
               "resolves the hook (the best reels' last line runs two to five seconds). Keep `must_keep_words` exactly as written "
               "somewhere in that segment's say. The spoken form stays: every number, year, unit and currency in words the way "
               "it is said, no digits, no symbols, no abbreviations; punctuation is commas, full stops and question marks only (no "
               "dashes, colons, semicolons or brackets: a narrator breathes at a comma or a full stop).\n"
               "For each segment also direct the performance: `emphasis` one or two words of the new say that carry it (the "
               "last stressed word of a sentence first), `pause_after` seconds of silence after it ("
               + PAUSES.get(form, PAUSES["story"]) + "), `melody` falling|rising|level|wide, `pace` "
               "slow|steady|brisk|fast, `direction` a few concrete words for the actor (energy, pace, where to lean).\n\n"
               "Return JSON only: {\"segments\": [{\"i\": the index, \"say\", \"emphasis\", \"pause_after\", \"melody\", "
               "\"pace\", \"direction\"}]}")
    text, use = writer.ask_openai(SYSTEM, content, effort=effort, max_tokens=32000, what="ear", model=EAR_MODEL)
    return writer.json_block(text).get("segments") or [], use


AUDIT = ("You audit a rewrite of a documentary script for facts only. For each pair of segments (the original, the rewrite) "
         "decide whether the rewrite changed what is asserted: a fact, figure, date, name, quantity, order of events, cause or "
         "claim added, dropped or altered, or a certainty made stronger or weaker. Each pair comes with `its_quotes`, the "
         "verified sentences the original stands on: a detail that neither the original nor its quotes state is an added "
         "claim (a superlative or a 'never' or 'first', a setting such as 'in that early reel', a role such as 'his own "
         "director', a stronger verb such as 'rewrote' for 'made changes', a different actor). Style, word order, split "
         "sentences, hinge words and short verdicts that restate the original are not changes.")


def audit(pairs, effort="low"):
    """([indices whose facts changed], {i: why}, usage)"""
    content = (f"# Pairs\n{json.dumps(pairs, ensure_ascii=False, indent=0)}\n\n"
               "Return JSON only: {\"changed\": [{\"i\": index, \"why\": a few words}]} (an empty list when no segment changed).")
    text, use = writer.ask_openai(AUDIT, content, effort=effort, max_tokens=12000, what="ear-audit", model=EAR_MODEL)
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
    for q in old.get("quotes") or []:
        if isinstance(q, dict) and q.get("words") and not writer.has_words(say, q["words"]):
            p.append(f"lost the quoted words {q['words']}")       # someone's own words stay exactly as the checked quote has them
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
    qb = _quotes_of(script)
    pairs = [{"i": i, "original": s.get("say", ""), "rewrite": (by_i.get(i) or {}).get("say", ""),
              "its_quotes": [qb[f] for f in s.get("facts") or [] if f in qb]} for i, s in enumerate(segs)]
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
    # the length is recorded, never a reason to throw the rewrite out: the narration is read, timed and its pictures
    # planned after the script is final (wf3.timed, supply), so a close cut to one line stands
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
