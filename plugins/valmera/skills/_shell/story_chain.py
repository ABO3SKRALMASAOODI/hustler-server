"""Plan the narration on its sources. Role names describe the story; indexes address the cut."""
import json


LANDMARKS = ("hook", "setback", "turn", "payoff", "meaning", "coda")
PITCH = """Plan this film before the writer speaks a line. Start by writing the meaning line:
use the hook's own key word, now changed by the outcome. Then plan backwards from that landing.
Write a short chain explaining who wanted what and why, the grounds of the objection,
what decided it and what followed. Use the evidence in the bank, including causes established
across quotes. Give the arc one payoff figure if the evidence supplies one. Leave other totals
in the bank. Introduce each person by role when first named.

In a story or documentary, open on the collision of the starting and ending states, leaving the how
open, or on a sourced climax in the subject's voice. Establish who matters now, rewind in one line,
establish the want in the first quarter, then attempts that fall short and force the next attempt.
Place the setback between a quarter and halfway, escalate to the ironic return, and resolve it
with the meaning line. If a coda follows, stay with the changed life, choice or listener at the end of the arc. Let its emotional consequence land there. Introduce a historical development only when it leads forward to the payoff. Plant a concrete detail early and pay the same thread off in the last quarter.
Combine the payoff and its meaning where a coda follows. End within one line of the payoff.
Use as many beats as this chain needs. Two lines that could trade places need a stronger link.

In marketing, use six ordered beats, one each named subject, pain, before, typed, after, cta.
Introduce the speaker by name and what they claim. Tie the editor's pain to that particular claim.
The before introduces the speaker's uninterrupted proof in their own voice; the typed line gives
the actual instruction(s); let the after demonstrate the change with its own sound. Speak a
single thematic CTA over the brand end card. Both formats have a narrator. Use the source
sentences and visible export receipts. Select whole thoughts, including the last item the speaker
names. For split, after and stretch choose inclusive first/last numbered EXPORT sentence indexes;
for before choose UPLOAD sentence indexes. Prefer proof visible inside those sentences. The split
is a distinct comparison, the raw before is heard once.

Return JSON with meaning_line first, hook_word, meaning_word (the same word as used in that
line), chain (one string containing a short paragraph), beats (hook,setback,turn,payoff,meaning,coda each a zero-based segment index;
when jobs coincide point both at the same line), and sheet, an ordered list, one entry per line.
Each entry has role (its job in your own words; marketing uses the six specified role names),
action (the actual story beat), link (so|but|because|until|then|instead, empty for the hook),
facts (supporting source ids), plant and pays (lists of matching thread ids), job
(list|proof|emotion|time_jump|reveal|action), display (one printed word or figure chosen for
the stake, contrast or turn, or empty). Marketing also returns spans with split,before,after,stretch
each [first_sentence,last_sentence]. Long-form develops the chain through the supplied chapters,
with one sheet entry per narrated segment and a consequential turn within each chapter."""

WRITE = """Write exactly one segment for each sheet entry in order. Let each sentence depend on the
one before it; a conjunction need not be spoken where the relation is audible. State a referent
before a line points back to it. Write the planned meaning line using the hook word. Return on each
segment a print dictionary mapping every voice spelling in say to its printed form (numbers,
acronyms, web addresses, units and names), or {}. Write the spoken words and their punctuation
for the actor. The reference lengths describe completed films; end where this story lands."""


def plan(kind, idea, evidence, ask=None):
    import writer
    ask = ask or writer.ask_openai
    content = json.dumps(dict(form=kind, idea=idea, evidence=evidence), ensure_ascii=False)
    correction = ""
    for attempt in range(3):
        answer, usage = ask(PITCH, content + correction, effort="high", what="story beat sheet", max_tokens=32000)
        try:
            sheet = writer.json_block(answer)
            if isinstance(sheet.get("chain"), list):
                sheet["chain"] = " ".join(sheet["chain"])
            entries = sheet["sheet"]
            if not isinstance(sheet["chain"], str) or not sheet["chain"].strip() or not entries:
                raise ValueError("chain must be a paragraph and sheet a nonempty list")
            for index in [sheet["beats"][key] for key in LANDMARKS]:
                if type(index) is not int or not 0 <= index < len(entries):
                    raise ValueError("landmark indexes are zero-based sheet positions")
            for entry in entries:
                for key in ("role", "action", "facts", "plant", "pays", "display", "link", "job"):
                    if key not in entry:
                        raise ValueError(f"sheet entry missing {key}")
            if kind == "marketing":
                roles = [entry["role"] for entry in entries]
                if roles != ["subject", "pain", "before", "typed", "after", "cta"]:
                    raise ValueError("use the six named marketing roles in order")
                for key in ("before", "split", "after", "stretch"):
                    first, last = sheet["spans"][key]
                    clock = evidence["sentences"]["upload" if key == "before" else "export"]
                    if not (type(first) is int and type(last) is int and 0 <= first <= last < len(clock)):
                        raise ValueError(f"{key} needs two existing inclusive sentence indexes")
            return sheet, usage
        except (KeyError, TypeError, ValueError) as exc:
            if attempt == 2:
                raise
            correction = f"\nRepair the JSON structure of your beat sheet: {exc}."


def write_segments(sheet, request):
    """A truncated or mis-sized JSON reply is completed by the writer on the original instructions."""
    repair, usage = "", {}
    for attempt in range(3):
        try:
            script, current = request(repair)
            usage = dict(current, usd=float(usage.get("usd", 0)) + float(current.get("usd", 0)))
            return attach(script, sheet), usage
        except (KeyError, TypeError, ValueError) as exc:
            if attempt == 2:
                raise
            repair = (f"Your JSON response could not be assembled: {exc}. Return the complete object with "
                      f"all {len(sheet['sheet'])} segment records, in sheet order.")


def attach(script, sheet):
    """Carry the actual planner's fields through to existing beat and display readers."""
    lines = script["segments"]
    entries = sheet["sheet"]
    if len(lines) != len(entries):
        raise ValueError(f"writer returned {len(lines)} segments for {len(entries)} planned lines")
    beats = {key: sheet["beats"][key] for key in LANDMARKS}
    if any(type(index) is not int or not 0 <= index < len(lines) for index in beats.values()):
        raise ValueError("planner landmark outside its beat sheet")
    legacy = {beats["turn"]: "turn", beats["payoff"]: "payoff", beats["setback"]: "low",
              beats["meaning"]: "close", beats["hook"]: "hook"}
    for i, (segment, entry) in enumerate(zip(lines, entries)):
        for field in ("role", "link", "job", "plant", "pays", "display"):
            segment[field] = entry[field]
        segment.setdefault("print", {})
        chapter_turn = segment.get("chapter") and segment.get("beat") == "turn"
        segment["beat"] = legacy.get(i, "turn" if chapter_turn else "setup")
    if isinstance(sheet["chain"], list):
        sheet = dict(sheet, chain=" ".join(sheet["chain"]))
    script.update(chain=sheet["chain"], beats=beats, beat_sheet=sheet)
    return script
