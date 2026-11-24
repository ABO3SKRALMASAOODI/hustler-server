"""Compose a checked author's definition and a speaker's distinct imagined examples on their own words."""
import hashlib
import json
from pathlib import Path


PROMPT = """Edit a checked print definition followed by the interviewee's thought experiment.
If the estimates instead concern measured results, unrelated events or real demonstrations, return {}.
Use inclusive numbered words (zero based). Return {original: index into originals, first: first word
of the spoken definition, citation: word of the book/author attribution where the source credit belongs,
turn: FIRST word moving beyond the named definition into the speaker's own imagined scale (including its
lead-in, earlier than its first number), last: last word of the continuous thought experiment,
paraphrase: one concise phone line of meaning (about 8–11 words), including domain scope; the author is separately credited, title: exact book title, edition: edition identifier only (publisher and year are already supplied),
locator: compact chapter/paragraph identifier, examples:[{first,last,kind:"figure|alternative|aside|payoff"}],
setup: word index where the strongest scale example states its quantity, accent: later word index where the same example reaches its contrasting unit/outcome,
caption_ends:[inclusive final word of each natural spoken cue throughout the passage]}.
Give the paraphrase and citation separate readable beats before the turn. Keep self-contained spoken cues,
especially qualifiers, quantity with unit, and the closing achievement with its modifier. Distinguish
independent examples. Use figure only for the strongest example; other numerical estimates are alternative. Give each its own first and last word. Only call something a payoff if the speaker
imagines it. Skip example rows for connecting speech. Do not add stock or stage a demonstration."""


def materials(direction):
    facts = direction.get("thought_facts") or [f for p in direction.get("display_facts") or [] for f in p["facts"]]
    prints = [f for f in facts if f.get("excerpt") and f["source"].get("publication_date")]
    estimates = [f for f in facts if (f.get("chain") or {}).get("stance") == "personal_estimate"]
    return prints, estimates


def director_material(editorial, words):
    """Give the normal director the checked originals before it writes the score."""
    import wf1_display
    claims = wf1_display.claim_windows(editorial, words)
    facts = [wf1_display.claim_fact(c, {}, editorial["sources"], words) for c in claims
             if c.get("primary_excerpt") or c.get("stance") == "personal_estimate"]
    prints, estimates = materials(dict(thought_facts=facts))
    return facts if prints and estimates else []


def read(direction, words, work, llm):
    prints, estimates = materials(direction)
    if not prints or not estimates:
        return {}, []
    data = dict(words=[w["w"] for w in words], originals=[
        dict(excerpt=f["excerpt"], publisher=f["source"].get("publisher"),
             date=f["source"]["publication_date"], supports=f["source"].get("supports")) for f in prints],
        estimates=[f["chain"] for f in estimates])
    digest = hashlib.sha256(json.dumps([PROMPT,data], sort_keys=True).encode()).hexdigest()
    path = Path(work, "thought-" + digest[:18] + ".json")
    spend = []
    if isinstance(direction.get("thought_reading"), dict):
        plan = direction["thought_reading"]
    elif path.exists():
        plan = json.loads(path.read_text())
    else:
        import httpx
        import openai_budget
        try:
            text, usage = llm.ask(PROMPT, json.dumps(data), effort="medium", max_tokens=1800)
            spend.append(dict(usage, stage="score", what="checked definition and imagined examples"))
            plan = llm.json_block(text)
        except openai_budget.OverBudget:
            raise
        except (httpx.HTTPError, RuntimeError, OSError, ValueError):
            # A transient reader failure leaves the scored source and speech usable.
            # Only completed readings enter the copy's evidence cache.
            return {}, spend
        from wf1_source_frame import write_cache
        write_cache(path, json.dumps(plan).encode())

    def index(v):
        return type(v) is int and 0 <= v < len(words)
    if not isinstance(plan, dict) or not all(index(plan.get(k)) for k in ("first","citation","turn","last","setup","accent")):
        return {}, spend
    if not (plan["first"] < plan["citation"] < plan["turn"] <= plan["setup"] < plan["accent"] <= plan["last"]
            and type(plan.get("original")) is int and 0 <= plan["original"] < len(prints)
            and all(isinstance(plan.get(k),str) and plan[k].strip() for k in ("paraphrase","title","locator"))):
        return {}, spend
    examples = []
    for row in plan["examples"] if isinstance(plan.get("examples"),list) else ():
        if (isinstance(row,dict) and row.get("kind") in ("figure","alternative","aside","payoff")
            and index(row.get("first")) and index(row.get("last"))
            and (examples[-1]["last"]+1 if examples else plan["turn"]) <= row["first"] <= row["last"] <= plan["last"]):
            examples.append(row)
    owners = {f["chain"]["speaker"] for f in estimates if
              plan["turn"] <= f["chain"].get("first", -1) <= plan["last"]}
    if len(owners) != 1:
        return {}, spend
    fact = prints[plan["original"]]
    return dict(plan, examples=examples, edition=plan["edition"] if isinstance(plan.get("edition"),str) else "", source=fact["source"],
                excerpt=fact["excerpt"], speaker=owners.pop(),
                caption_ends=[i for i in (plan.get("caption_ends") or []) if index(i)]), spend


def local_windows(coverage, plan):
    """Stable indices preserve checks on neighboring external evidence."""
    return [dict(w, on_take=True) if plan["first"] <= w["first"] <= w["last"] <= plan["last"] else w
            for w in coverage["windows"]]
