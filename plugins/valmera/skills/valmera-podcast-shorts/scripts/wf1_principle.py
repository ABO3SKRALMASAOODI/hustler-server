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
