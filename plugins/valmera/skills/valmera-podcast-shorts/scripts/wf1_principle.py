"""A published rule and an imagined decision share one word-addressed comparison."""
PROMPT = """When a speaker proposes comparing observable decisions with a published principle,
write principle_example {source: exact external source key, principle: exact short wording from
its supports, decision: a single anonymous, observable example (at most nine words), comparison:
a concrete discrepancy in THAT decision (at most nine words), warning: a specific follow-up
question about the same evidence (at most nine words), setup, observe, compare, release, warn,
last}. These six zero-based word IDs are ordered: introduce the principle on the hypothetical
question, introduce the decision while explaining what to look at, contrast the evidence when
discussing a possible result, return to the face when the example ends, then bring back the
question only where the closing warning or suggested action is actually spoken. Keep time to
read it. The staged observation is an illustration. Never infer a real person's or team's beliefs.
If the analogy opens with a movie and/or book, add film_source (URL of the verified excerpt),
film_label (film title and year), cover_source (publisher page URL) when present. Label this a
film example. Give the borrowed movie one brief shot and the cited cover a whole-artifact view.
Otherwise omit these keys. Omit principle_example when there is no such comparison."""


def bind(raw, sources, words):
    if not isinstance(raw, dict):
        return None
    key = raw.get("source")
    source = sources.get(key) if isinstance(key,str) else None
    if not source or source == sources.get("interview"):
        return None
    quote = raw.get("principle")
    if not isinstance(quote,str) or not quote.strip() or not any(
            quote.strip().casefold() in text.casefold() for text in source.get("supports", []) if isinstance(text,str)):
        return None
    points = [raw.get(k) for k in ("setup","observe","compare","release","warn","last")]
    if not all(type(i) is int and 0 <= i < len(words) for i in points) or not (
            points[0] < points[1] < points[2] < points[3] <= points[4] <= points[5]):
        return None
    if not all(isinstance(raw.get(k),str) and raw[k].strip() for k in ("decision","comparison","warning")):
        return None
    fields = ("decision","comparison","warning","setup","observe","compare","release","warn","last")
    out = {k:raw[k] for k in fields}
    for k in ("film_source","film_label","cover_source"):
        if isinstance(raw.get(k),str) and raw[k].strip():
            out[k] = raw[k].strip()
    return dict(out,source=source,principle=quote.strip())


def outside(page, lo, hi):
    """Keep both sides of a word interval, including the current sequence state."""
    if page["last"] < lo or page["first"] > hi:
        return [page]
    result = []
    for a,b in ((page["first"],lo-1),(hi+1,page["last"])):
        if a > b:
            continue
        part = dict(page,first=a,last=b)
        if page.get("sequence"):
            active = [s for s in page["sequence"] if s["at"] <= a]
            steps = ([dict(active[-1],at=a)] if active else []) + [
                s for s in page["sequence"] if a < s["at"] <= b]
            if not steps:
                continue
            part["sequence"] = steps
        result.append(part)
    return result


def pages(existing, example, words=()):
    illustrations = [p for p in existing if p.get("episode_example")]
    result = []
    for page in existing:
        parts = [page]
        if page.get("sequence") and not page.get("sequence_kind"):
            for owner in illustrations:
                parts = [r for p in parts for r in outside(p,owner["first"],owner["last"])]
        result.extend(parts)
    if not example:
        return result
    e = example
    # The host's bridge belongs to the synchronous conversation. Reintroduce
    # the same decision only on its warning cue.
    result = [r for p in result if not (p.get("context_cue") and (e.get("film_source") or e.get("film_sources"))
              and p["last"] < e["setup"]) for r in outside(p,e["setup"],e["last"])]
    source = e["source"]
    stamp = source.get("publication_date") or ("checked " + source["checked_on"] if source.get("checked_on") else "")
    credit = " · ".join(filter(None,(source.get("publisher"),stamp)))
    note_at = e.get("note_at",e["compare"])
    if words and e.get("note") and note_at < e["compare"]:
        readable = words[e["observe"]]["t0"] + len(e["decision"].split())/3.5
        note_at = next((i for i in range(note_at,e["compare"]) if words[i]["t0"] >= readable),note_at)
    states = [
        (e["setup"],e["observe"]-1,"Published principle","“"+e["principle"]+"”",credit),
        (e["observe"],note_at-1,"Illustrative decision",e["decision"],"Principle · "+e["principle"]),
        (e["compare"],e["release"]-1,"Compare the decision",e["comparison"],"Illustrative example"),
        (e["warn"],e["last"],"Early warning · Example",e["warning"],"")]
    if e.get("note") and e["observe"] < note_at < e["compare"]:
        states.append((note_at,e["compare"]-1,"Illustrative meeting note",e["note"],"Principle · "+e["principle"]))
    for a,b,heading,body,footer in states:
        result.append(dict(first=a,last=b,principle_plate=True,heading=heading,credit=footer,
                           facts=[dict(source=source,value=body)]))
    return sorted(result,key=lambda p:p["first"])


def finish_reading(example, credits, words, work, llm):
    """Bind the decision to its checked setup and its final spoken warning once."""
    if not example or (example.get("note") and type(example.get("note_at")) is int):
        return example, []
    import hashlib
    import json
    from pathlib import Path
    import httpx
    import openai_budget
    from wf1_source_frame import write_cache
    prompt = """Finish directing ONE illustration against the checked originals and the numbered speech.
Keep the cited principle and the same anonymous hypothetical decision. Return only JSON:
{decision, note_at, note, compare, comparison, warn, last, warning, film_sources, film_label, cover_source}.
Plain short copy without badges or prefixes. decision names the action; note is one concrete
entry from that illustrative decision record, added when the speaker asks what can be observed.
comparison identifies exactly what evidence is missing, on the first words introducing a possible
finding. Choose word IDs observe < note_at < compare < release. Bring back the concrete question
on the closing phrase that actually says early warning (begin at its spoken lead-in, not at the
earlier mention of drift). last includes the last word of that warning. warning is a short follow-up question (four to six words). In credits distinguish
dramatic film excerpts and a cited cover: film_sources is the list of their actual film URLs ONLY,
film_label is film title and year, cover_source is the credited publisher page, empty if absent.
Labels describe an illustrative record, never an actual case or a conclusion about motives."""
    evidence = [c for c in credits.values() if isinstance(c,dict) and c.get("source")]
    data = dict(example=example,credits=evidence,words=[w["w"] for w in words])
    digest = hashlib.sha256(json.dumps([prompt,data],sort_keys=True).encode()).hexdigest()
    memo = Path(work,"principle-"+digest[:18]+".json")
    spend = []
    if memo.is_file():
        result = json.loads(memo.read_text())
    else:
        try:
            text, use = llm.ask(prompt,json.dumps(data),effort="low",max_tokens=1000)
            spend.append(dict(use,stage="score",what="source setup and illustrative decision"))
            result = llm.json_block(text)
        except openai_budget.OverBudget:
            raise
        except (httpx.HTTPError,RuntimeError,OSError,ValueError):
            return example, spend
        if not isinstance(result,dict):
            return example, spend
        write_cache(memo,json.dumps(result).encode())
    out = dict(example)
    urls = {c["source"].get("url") for c in evidence}
    films = result.get("film_sources")
    if isinstance(films,list):
        out["film_sources"] = [url for url in films if isinstance(url,str) and url in urls]
    if isinstance(result.get("cover_source"),str) and result["cover_source"] in urls:
        out["cover_source"] = result["cover_source"]
    if isinstance(result.get("film_label"),str):
        out["film_label"] = result["film_label"]
    note, compare = result.get("note_at"),result.get("compare")
    if type(note) is int and type(compare) is int and example["observe"] < note < compare < example["release"]:
        if all(isinstance(result.get(k),str) and result[k].strip() for k in ("decision","note","comparison")):
            out.update({k:result[k] for k in ("decision","note_at","note","compare","comparison")})
    warn, last = result.get("warn"),result.get("last")
    if type(warn) is int and type(last) is int and example["release"] <= warn <= last < len(words):
        out.update(warn=warn,last=last)
        if isinstance(result.get("warning"),str) and result["warning"].strip():
            out["warning"] = result["warning"].strip()
    return out, spend
