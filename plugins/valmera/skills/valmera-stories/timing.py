"""The hook's lead and the close's length, measured on the narration's own word times and brought to the judge's budget
by the writer (round 1, 19 Nov: the hook opened on a date and reached its collision at 9 s; the close ran 17 s).

The budgets are the judge's, measured on the 28 @metromedia.house reels (Atlas transcripts' word times against the story
turns a model read, .valmera/week-1106/atlas and week-1112/lanes/MUSIC/study/turns, 20 Nov):
  the hook ends 3.65 s into the speech at the median (28 reels)
  the last line runs 2.6 s at the median, 5.0 s at the upper quartile (13 reels that mark one)

Nothing here reads what a line means: the writer marks the word where its hook lands (`lands`, else its emphasis), the
narration's aligner times every word, and the measure is seconds. A beat over its budget goes back to the writer with
the measured seconds, its line and the narrator's measured rate, and the writer rewrites that beat from the same verified
facts; the script is then judged on its quotes again and read again (wf3.timed). Nothing is refused: the best measured
read is kept.
"""
import json
import time

HOOK_LAND_S = 3.7          # the hook's collision lands by this many seconds of speech (the judge's median hook end)
CLOSE_S = 5.0              # the close is one landing line of at most this many seconds (the judge's upper quartile)
TIMING_ROUNDS = 3          # reads measured: the written script's and up to two rewrites
RETRIES = 3                # an unanswered rewrite call is an outage: tried again with backoff
BACKOFF_S = 4.0


def _norm(w):
    return "".join(ch for ch in str(w).lower().replace("’", "'") if ch.isalnum() or ch == "'").strip("'")


def _find(spoken, phrase):
    """The index of the last word of the first place `phrase` is spoken in `spoken` ([normalised words]), else None."""
    want = [x for x in (_norm(t) for t in str(phrase or "").split()) if x]
    if not want:
        return None
    for k in range(len(spoken) - len(want) + 1):
        if spoken[k:k + len(want)] == want:
            return k + len(want) - 1
    return None


def measure(words, sc, hook_s=HOOK_LAND_S, close_s=CLOSE_S):
    """{hook_land_s, hook_word, close_s, wps, over: {"hook"|"close": {s, budget}}} from words.json ({"words": [{w, start,
    end, seg}]}, a path or the dict) and the script it reads. The hook lands at the end of the word the writer marked
    (the segment's `lands`, else its last emphasis word found in the line, else the segment's last word), counted from the
    first spoken word; the close is its segment's first word's start to its last word's end."""
    d = json.load(open(words)) if isinstance(words, str) else words
    ws = [w for w in d.get("words") or [] if float(w.get("end", 0)) >= float(w.get("start", 0))]
    segs = sc.get("segments") or []
    if not ws or not segs:
        return dict(hook_land_s=None, close_s=None, wps=None, over={})
    t_first = float(ws[0]["start"])
    hook = [w for w in ws if int(w.get("seg", 0)) == 0]
    last_i = len(segs) - 1
    close = [w for w in ws if int(w.get("seg", 0)) == last_i]
    spoken = [_norm(w["w"]) for w in hook]
    s0 = segs[0]
    k = None
    for mark in [s0.get("lands")] + list(reversed([e for e in (s0.get("emphasis") or []) if isinstance(e, str)])):
        k = _find(spoken, mark) if mark else None
        if k is not None:
            break
    if k is None and hook:
        k = len(hook) - 1
    land = round(float(hook[k]["end"]) - t_first, 2) if hook else None
    cl = round(float(close[-1]["end"]) - float(close[0]["start"]), 2) if close and last_i > 0 else None
    talk = sum(float(w["end"]) - float(w["start"]) for w in ws)
    span = float(ws[-1]["end"]) - t_first
    wps = round(len(ws) / span, 3) if span > 0 else None
    over = {}
    if land is not None and land > hook_s:
        over["hook"] = dict(s=land, budget=hook_s, word=hook[k]["w"])
    if cl is not None and cl > close_s:
        over["close"] = dict(s=cl, budget=close_s)
    return dict(hook_land_s=land, hook_word=hook[k]["w"] if hook else None, close_s=cl, wps=wps,
                talk_share=round(talk / span, 3) if span > 0 else None, over=over)


def overage(m):
    """Seconds over budget, summed over the measured beats (0 when every beat fits)."""
    return round(sum(max(0.0, float(v["s"]) - float(v["budget"])) for v in (m.get("over") or {}).values()), 2)


REWRITE_SYSTEM = """You are the writer of a narrated vertical story film cut like the best documentary reels (the @metromedia.house founder stories). The script is checked against verified quotes; you rewrite only the beats you are given, from the same verified facts, so they land in the time the best reels give them. Each comes with its line, the seconds the narrator took, its budget in seconds, and about how many words the narrator says in that budget at his measured rate.

The hook (segment 0) opens on the story's strongest image or claim: the collision itself, said with a known name, so a viewer who hears only its first seconds already has it. Never a date, a place, a setting or a greeting before it: a date belongs in the setup, after the hook. Its collision must be spoken within the budget; the line may run on a little after it lands. Mark in "lands" the word or two of your new line where the collision lands.

The close (the last segment) is ONE landing line inside its budget: the one fact that resolves the hook's collision, said in new words. Not every supporting fact, no hedge, no moral, no comment on the story.

Every claim stays one the cited facts' quotes state; cite them in "facts". Someone's own words only when a cited fact holds them word for word. Spoken form: numbers and years in words the way they are said, no digits, no symbols; commas and full stops only. Never repeat what another segment already says. Keep the names exactly as spelled."""


def rewrite_content(sc, m, notes=None):
    segs = sc.get("segments") or []
    facts = {f.get("id"): f for f in sc.get("facts") or [] if f.get("id")}
    wps = float(m.get("wps") or 3.3)
    rows = []
    for beat, v in (m.get("over") or {}).items():
        i = 0 if beat == "hook" else len(segs) - 1
        s = segs[i]
        rows.append(dict(i=i, beat=beat, line=s.get("say"), seconds=v["s"], budget_s=v["budget"],
                         budget_words=max(int(v["budget"] * wps), 4),
                         **({"lands_at": v.get("word")} if beat == "hook" else {}),
                         its_facts=[dict(id=f, quote=(facts.get(f) or {}).get("quote"), source=(facts.get(f) or {}).get("source"),
                                         when=(facts.get(f) or {}).get("when")) for f in s.get("facts") or []]))
    script = [dict(i=k, beat=s.get("beat"), say=s.get("say")) for k, s in enumerate(segs)]
    bank = [dict(id=f.get("id"), quote=f.get("quote"), source=f.get("source"), speaker=f.get("speaker"), when=f.get("when"))
            for f in sc.get("facts") or [] if f.get("id") and f.get("quote")]
    out = (f"# The beats to rewrite (measured on the narration)\n{json.dumps(rows, ensure_ascii=False, indent=1)}\n\n"
           f"# The whole script\n{json.dumps(script, ensure_ascii=False, indent=0)}\n\n"
           f"# The verified facts this film may state\n{json.dumps(bank, ensure_ascii=False, indent=0)}\n\n"
           f"# Names the narrator says\n{json.dumps(sc.get('names') or [], ensure_ascii=False)}\n\n")
    if notes:
        out += f"# What happened to your last rewrite\n{notes}\n\n"
    out += ("Return JSON only: {\"segments\": [{\"i\": the index, \"say\": the new line, \"facts\": [ids], \"quotes\": [{\"words\", "
            "\"who\", \"fact\"}] or [], \"emphasis\": [one or two words of say], \"lands\": the hook's landing word or words (hook only), "
            "\"pause_after\": seconds, \"direction\": a few words for the actor}]}")
    return out


def ask_rewrite(system, content):
    import writer
    return writer.ask_openai(system, content, effort="high", max_tokens=16000, what="timing rewrite", model=writer.BANK_WRITE_MODEL)


def rewrite(sc, m, ask=None, notes=None, log=print, sleep=time.sleep):
    """(script with the over-budget beats rewritten, [usage]): one writer call, tried again with backoff when it goes
    unanswered (an outage, never a verdict). A beat the answer leaves out, or gives no words for, stays as it was."""
    import writer
    ask = ask or ask_rewrite
    uses, got, err = [], None, None
    for k in range(RETRIES):
        try:
            text, use = ask(REWRITE_SYSTEM, rewrite_content(sc, m, notes))
            uses.append(use)
            got = writer.json_block(text)
            break
        except Exception as e:  # noqa: BLE001 - unanswered or unreadable: try again
            err = repr(e)[:200]
            log(f"timing: rewrite call {k + 1} failed: {err}")
            sleep(BACKOFF_S * (2 ** k))
    out = json.loads(json.dumps(sc))
    if not isinstance(got, dict):
        return out, uses, []
    segs = out.get("segments") or []
    want = {0 if b == "hook" else len(segs) - 1 for b in (m.get("over") or {})}
    ids = {f.get("id") for f in out.get("facts") or [] if f.get("id")}
    changed = []
    for g in got.get("segments") or []:
        if not isinstance(g, dict) or g.get("i") not in want or not str(g.get("say") or "").strip():
            continue
        s = segs[g["i"]]
        s["say_timed_from"] = s.get("say")
        s["say"] = " ".join(str(g["say"]).split())
        facts = [f for f in g.get("facts") or [] if f in ids] if isinstance(g.get("facts"), list) else []
        if facts:
            s["facts"] = facts
        s["quotes"] = [q for q in g.get("quotes") or [] if isinstance(q, dict) and q.get("fact") in ids] if isinstance(g.get("quotes"), list) else []
        em = [e for e in g.get("emphasis") or [] if isinstance(e, str) and _find([_norm(w) for w in s["say"].split()], e) is not None]
        if em:
            s["emphasis"] = em
        if g["i"] == 0 and str(g.get("lands") or "").strip():
            s["lands"] = " ".join(str(g["lands"]).split())
        for key in ("pause_after", "direction"):
            if g.get(key) not in (None, "", []):
                s[key] = g[key]
        changed.append(g["i"])
    if segs:
        out["hook_line"], out["close_line"] = segs[0].get("say"), segs[-1].get("say")
    return out, uses, changed
