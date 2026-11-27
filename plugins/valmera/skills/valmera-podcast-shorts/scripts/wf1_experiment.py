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






def local_windows(coverage, plan):
    """Stable indices preserve checks on neighboring external evidence."""
    return [dict(w, on_take=True) if plan["first"] <= w["first"] <= w["last"] <= plan["last"] else w
            for w in coverage["windows"]]
