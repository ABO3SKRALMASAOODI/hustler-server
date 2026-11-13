"""The Director call. One streamed call writes a whole film, one repair round reads the meter.

No regex or word list reads the customer's text here: probes read the plan, and a model read the
asks once, before the film existed. A measurement is a function of the artefact. The repair runs
only when the clock leaves room, and it is kept only if it measures no worse.
"""
import json
import math
import time

from . import brief, call
from . import compiler as score

MODEL = "claude-opus-5-5"
MAX_TOKENS = 20000
REPAIR_TOKENS = 9000       # thinking counts against max_tokens: a repair at 1,500 spent it all thinking and wrote nothing
USD_OUT = 20.00 / 1e6
REPAIR_NEEDS_S = 50.0      # the slice's repairs: 27-51 s
WRITE_S = 30.0             # first line to last beat in the slice: 12-40 s, most under 30

SINGLE = ("treat", "film", "dna")
ORDER = {"treat": 0, "film": 1, "dna": 2, "fix": 3, "drop": 3, "bind": 4, "pics": 5, "tool": 6, "def": 6, "b": 7, "item": 8}


# ----------------------------------------------------------------- lines


def clean(o):
    return {k: v for k, v in o.items() if not k.startswith("_")}


def dump(lines):
    return "\n".join(json.dumps(clean(o), ensure_ascii=False, separators=(",", ":")) for o in lines) + "\n"


def line_key(o):
    k = o["k"]
    if k in SINGLE:
        return (k,)
    if k == "bind":
        return (k, str(o.get("ask")))
    if k in ("fix", "drop"):
        return (k, str(o.get("w")))
    if k in ("b", "pics", "item"):
        return (k, str(o.get("id")))
    return (k, json.dumps(clean(o), sort_keys=True, ensure_ascii=False))


def read_control(text):
    """A repair answer's lines. `del` and `ok` steer the merge and never reach the compiler."""
    good, bad = [], []
    dec = json.JSONDecoder()
    keep = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("```"):
            continue
        try:
            o, _ = dec.raw_decode(s)
        except ValueError:
            keep.append(raw)
            continue
        if isinstance(o, dict) and o.get("k") in ("del", "ok"):
            good.append(o)
        else:
            keep.append(raw)
    lines, rejected = score.parse_score("\n".join(keep))
    bad.extend(rejected)
    return good, lines, bad


def first_word(o):
    try:
        return score.parse_range(o.get("w"))[0] if o.get("w") is not None else None
    except (ValueError, TypeError):
        return None


def merge(base, patch_text):
    """Lines with the same id replace in place. A new beat goes where `after` says, else last among beats."""
    ctrl, lines, bad = read_control(patch_text)
    out = [clean(o) for o in base]
    for c in ctrl:
        if c["k"] == "del":
            out = [o for o in out if str(o.get("id")) != str(c.get("id")) or o["k"] in SINGLE]
    for o in (clean(x) for x in lines):
        at = next((i for i, x in enumerate(out) if line_key(x) == line_key(o)), None)
        after = o.pop("after", None)
        if at is not None:
            out[at] = o
            continue
        if o["k"] == "b":
            spot = None
            if after is not None:
                spot = next((i + 1 for i, x in enumerate(out) if x["k"] == "b" and str(x.get("id")) == str(after)), None)
            if spot is None:
                first = first_word(o)      # a beat of words that fills a hole goes where those words fall, or the voice plays out of order
                if first is not None:
                    spot = next((i for i, x in enumerate(out) if x["k"] == "b" and first_word(x) is not None and first_word(x) > first), None)
            if spot is None:
                spot = max((i + 1 for i, x in enumerate(out) if x["k"] == "b"), default=len(out))
            out.insert(spot, o)
            continue
        spot = max((i + 1 for i, x in enumerate(out) if ORDER.get(x["k"], 9) <= ORDER.get(o["k"], 9)), default=0)
        out.insert(spot, o)
    return out, bad


# ----------------------------------------------------------------- facts the meter does not hold


def spoken_coverage(l1, words):
    """Share of the spoken words kept in the film with type on screen as they are said."""
    D, fps = l1["dur_f"], l1["fps"]
    cap, any_type = bytearray(D), bytearray(D)
    for i in l1["items"]:
        if i["k"] != "word":
            continue
        for f in range(max(0, i["f"][0]), min(i["f"][1], D)):
            any_type[f] = 1
            if i.get("role") == "cap":
                cap[f] = 1
    kept = shown = shown_cap = 0
    missed = []
    for n, w in enumerate(words):
        mid = (w["t0"] + w["t1"]) / 2
        frames = [int(s["f"][0] + (mid - s["src"][0]) * fps) for s in l1["spine"] if s["src"][0] <= mid < s["src"][1]]
        frames = [f for f in frames if 0 <= f < D]
        if not frames:
            continue
        kept += 1
        shown += any(any_type[f] for f in frames)
        shown_cap += any(cap[f] for f in frames)
        if not any(any_type[f] for f in frames):
            missed.append(n)
    return dict(kept=kept, shown=round(shown / max(1, kept), 3), caption=round(shown_cap / max(1, kept), 3), missed=missed)


def bound_ids(bind, l1):
    """Which `to` entries of a bind line name something the compiled film holds."""
    ids = [str(i["id"]) for i in l1["items"]]
    good, dead = [], []
    for t in bind.get("to") or []:
        t = str(t)
        if t.endswith("*"):
            head = t[:-1]
            hit = (head == "cap:" and any(i["k"] == "word" and i.get("role") == "cap" for i in l1["items"])) or any(x.startswith(head) for x in ids)
        else:
            hit = any(x == t or x.startswith(t + ".") for x in ids)
        (good if hit else dead).append(t)
    return good, dead


def ask_facts(lines, comp, idx, scout):
    """Each Scout ask against the plan. An ask with no bound item is a finding, at once, with no model."""
    l1 = comp.l1
    binds = {str(o.get("ask")): o for o in lines if o["k"] == "bind"}
    cov = spoken_coverage(l1, idx.get("words") or [])
    out, open_asks = [], []
    for a in (scout or {}).get("asks") or []:
        probe, aid, need = a.get("probe"), a.get("id"), a.get("min")
        b = binds.get(str(aid))
        row = f"{aid} {probe}"
        short = False
        if probe == "caption_coverage":
            row += f": {cov['caption']:.2f} of the spoken words have a caption up as they are said, {cov['shown']:.2f} counting display type"
            short = cov["shown"] < (need or 0.9)
            if short:
                row += ", min " + str(need) + ". Bare: " + " ".join(f"w{i}" for i in cov["missed"][:24])
        elif probe == "design_items":
            n = comp.meter["facts"]["design_items10"] * comp.meter["dur_s"] / 10
            row += f": {n:.0f} designed items"
            short = n < (need or 0)
        if b is None:
            row += ". No bind line"
            short = True
        else:
            good, dead = bound_ids(b, l1)
            if dead:
                row += f". Bound to something the film does not hold: {' '.join(dead)}"
                short = True
            elif good:
                row += f". Bound to {' '.join(good[:6])}"
            elif b.get("say"):
                row += f". Said it can't be done: {b['say']}"
            else:
                row += ". Bound to nothing"
                short = True
        out.append(row)
        if short:
            open_asks.append(aid)
    return out, open_asks, cov


def fix_facts(lines, scout, words):
    """The Scout's hearing of misheard words the Score has not decided."""
    covered = set()
    for o in lines:
        if o["k"] == "fix":
            try:
                a, b = score.parse_range(o.get("w"))
            except (ValueError, TypeError):
                continue
            covered |= set(range(a, b + 1))
    return [f"w{f['w']} \"{words[int(f['w'])]['w']}\" heard as \"{f['t']}\"" for f in (scout or {}).get("fixes", [])
            if str(f.get("w", "")).isdigit() and int(f["w"]) < len(words) and int(f["w"]) not in covered
            and str(f["t"]).strip() != words[int(f["w"])]["w"].strip(".,;:!?")]


def band_distance(m):
    """How far the film sits from the card it named, on how often its picture really changes. A crop of the take
    is not a cut: held against cuts/10s, a take with 22 crops read as in band (p5591, distance 0.0, rated 2.5)."""
    b = score.BANDS.get(m.get("band") or "", {}) if isinstance(m.get("band"), str) else {}
    d = 0.0
    if b.get("cuts10") is not None:
        d += abs(math.log((float(m.get("new_pictures10") or 0.0) + 1.0) / (b["cuts10"] + 1.0)))
    if b.get("type10") and m["type_events10"]:
        d += abs(math.log(m["type_events10"] / b["type10"]))
    return round(d, 3)


def quality(comp, open_asks):
    """Q, sorted so a defect is never traded for density (DESIGN 5.4)."""
    m = comp.meter
    return (len(open_asks), len(m["rejects"]) + len(m["findings"]), sum(len(v) for v in m["blanks"].values()), band_distance(m))


def repair_ask(comp, facts, fixes, proof):
    out = ["That is your Score, compiled. The meter, measured from the plan:", "", comp.meter_text.rstrip(), ""]
    if facts:
        out += ["ASKS"] + facts + [""]
    if fixes:
        out += ["HEARING not decided yet, read each and keep it with a fix line if it is what was said: " + "; ".join(fixes), ""]
    out.append('Read it the way the person who uploaded this reads it, then the way a stranger scrolling past does. Would they post this over their upload? '
               'Does the first second carry the hook title, where the Seats section said it can stand? Is every Bin picture cut in once, on its word? '
               'Do the words the voice stresses stand as chunks of their own in the second voice? Is it still the upload with captions (SOURCES, EARNED)? '
               'Which frames look automatic: a stock picture standing still, type on type, a page where the person should be, a device the upload is better without? '
               'The TASTE and LINES rows are device lines the compiler removed: nothing in the voice or on screen earned them, or this footage can\'t carry that many. '
               'Don\'t write those back as they were. A removed line returns only with the `for` it answers, inside the counts on the SUPPLY line; a film that wants more of them needs more pictures from the Bin first. '
               'Rewrite what is weak. Answer with only the lines that change, JSONL, each line complete. '
               'A line with the same id replaces the old one. A new beat takes "after":"b05". {"k":"del","id":"b05"} removes one. '
               'If it stands as it is, answer {"k":"ok"}.')
    return "\n".join(out)


# ----------------------------------------------------------------- proof frames


class Empty(Exception):
    """The answer holds no film."""


def direct(work, idx, scout, deadline, model=MODEL, effort="medium", budget_usd=0.7, spent_usd=0.0):
    """Returns (compiled, score_text, usage). Raises call.Late, call.Refused, Empty."""
    system, content = brief.load(work)
    content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}      # the film part: read back at a twentieth on the repair
    usage = dict(model=model, effort=effort, rounds=[])
    room = int(max(6000, min(MAX_TOKENS, (budget_usd - spent_usd - 0.20) / USD_OUT)))
    r = call.stream(model, system, content, max_tokens=room, effort=effort, purpose="director_direct", deadline=deadline,
                    first_text_by=deadline - WRITE_S)
    usage["rounds"].append(dict(round="direct", usd=round(r.usd, 5), first_text_s=r.first_s, seconds=r.seconds, stop=r.stop, out=r.usage.output_tokens,
                                cache_read=r.usage.cache_read_input_tokens, cache_write=r.usage.cache_creation_input_tokens))
    usage["usd"] = r.usd
    text = r.text
    first_lines, _ = score.parse_score(text)
    first = score.compile_score(text, work_dir=work)      # the text as written: a line that failed must reach the meter
    if not first.meter.get("beats"):
        raise Empty(f"the answer compiled to no beats, stop {r.stop}")
    usage["truncated"] = r.stop == "max_tokens"
    facts, open_asks, _ = ask_facts(first_lines, first, idx, scout)
    fixes = fix_facts(first_lines, scout, idx.get("words") or [])
    q0 = quality(first, open_asks)
    usage["q_first"] = list(q0)
    kept, lines = first, first_lines
    weak = q0[0] or q0[1] or q0[2] or r.stop == "max_tokens"
    if weak and deadline - time.monotonic() >= REPAIR_NEEDS_S and spent_usd + r.usd < budget_usd - 0.12:
        ask = repair_ask(first, facts, fixes, None)
        # A new request, not a continuation: same cached prefix, the Score as plain text. Replaying the first turn
        # would bill its thinking again as input, and thinking blocks can't be stripped from a continuation.
        turn = content + [{"type": "text", "text": "Your Score, as you wrote it:\n" + text}, {"type": "text", "text": ask}]
        try:
            r2 = call.stream(model, system, turn, max_tokens=REPAIR_TOKENS, effort=effort, purpose="director_repair", deadline=deadline)
            usage["rounds"].append(dict(round="repair", usd=round(r2.usd, 5), first_text_s=r2.first_s, seconds=r2.seconds, stop=r2.stop, out=r2.usage.output_tokens,
                                        cache_read=r2.usage.cache_read_input_tokens))
            usage["usd"] += r2.usd
            merged, bad2 = merge(first_lines, r2.text)
            c2 = score.compile_score(dump(merged), work_dir=work)
            f2, o2, _ = ask_facts(merged, c2, idx, scout)
            q1 = quality(c2, o2)
            q1 = (q1[0], q1[1] + len(bad2)) + q1[2:]
            usage["q_repair"] = list(q1)
            if dump(merged) != dump(first_lines) and q1 <= q0:      # Q is sorted, so a defect is never traded for density
                kept, lines, open_asks, facts = c2, merged, o2, f2
                usage["repair_kept"] = True
        except (call.Late, call.Refused) as e:   # the first answer stands
            usage["repair_error"] = str(e)[:120]
    usage["asks"] = facts
    usage["asks_open"] = open_asks
    usage["validate_l1"] = score.validate_l1(kept.l1)[:20]
    usage["taste"] = taste_record(kept)
    return kept, dump(lines), usage


def taste_record(comp):
    """What the governor did to this film, for the job record: the class, what stood, what went and why."""
    t = comp.meter.get("taste") or {}
    if not t:
        return None
    return {"cls": (t.get("supply") or {}).get("cls"), "np10": (t.get("supply") or {}).get("np10"), "allow": t.get("allow"), "kept": t.get("kept"),
            "sounds": t.get("sounds"), "count": t.get("count"), "seats": t.get("seats"), "speaks": t.get("speaks"), "floor": t.get("floor"),
            "why": [f"{r['id']} {r['did']}: {r['why']}"[:140] for r in (t.get("rows") or []) if r["why"] != score.NO_FOR][:24]}
