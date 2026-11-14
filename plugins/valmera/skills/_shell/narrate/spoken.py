"""The listener's text in its spoken form, written by a model, for the VOICE gate's word error.

Whisper writes what it hears in written form ("1973", "$5,000", "the 80s", "15th"). The script the narrator read is
already in spoken form (the workflow's writer model wrote it: "nineteen seventy-three"). Turning the written form back
into words by rule (inflect and patterns) guessed one reading for every number in a transcript and measured 2.6-4.8%
word error on narrations that were spoken right (lane O, 13 Nov: "two thousand" heard as "twenty oh", "eighties" as
"eighty s", "dollar" against "$"). A model reads the transcript and writes it as it is said; where a written number
allows the script's own reading, it uses that reading, and it never changes a word the listener wrote in letters.

That last rule is enforced here, not trusted: the reply is aligned with the transcript token by token, and the model's
words are taken only where the transcript wrote digits, symbols or an abbreviation; every word the listener wrote in
letters is kept exactly as heard, so the model can never correct, drop or add one (merge()).

The key is this process's OPENAI_API_KEY (never printed). Model: NARRATE_SPOKEN_MODEL, default gpt-6.1-sol.
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openai_cache
import string
import time
import unicodedata
import urllib.request

MODEL = os.environ.get("NARRATE_SPOKEN_MODEL", "gpt-6.1-sol")
PRICE = {"gpt-6.1-sol": (2.0e-6, 10.0e-6), "gpt-6-luna": (0.10e-6, 0.50e-6), "gpt-5.5": (5.0e-6, 30.0e-6)}

SYSTEM = ("You write a speech transcript in its spoken form, for a word-error measurement. The transcript is what a speech "
          "recogniser heard a narrator say, in written form. Rewrite only the tokens that carry digits, currency or other "
          "symbols, ordinals, decades and abbreviations, as the words a narrator says for them (1973 -> nineteen seventy-three, "
          "$5,000 -> five thousand dollars, the 80s -> the eighties, 15th -> fifteenth, Dr. -> doctor, % -> percent). A written "
          "number, an initialism or a symbol can often be read more than one way (2005: two thousand five or twenty oh five; "
          "N.B.C.: NBC or N B C; $5,000 fuel bill: five thousand dollar fuel bill or five thousand dollars fuel bill); when "
          "the narrator's script says the same thing at that place in a form that is a valid reading of the token, copy the "
          "script's form exactly, its words, spelling and spacing. Never use the script to "
          "change anything else: every word the transcript wrote in letters stays exactly as written, misheard or not, in "
          "the same order, and nothing is added or left out.")


def _letters(tok):
    t = tok.strip(string.punctuation + "“”‘’—–…").lower().replace("’", "'")
    core = t.replace("'", "").replace("-", "")
    if core.isalpha() and not (tok.endswith(".") and len(core) <= 3 and tok[:1].isupper()):
        return t
    return None


def _toks(text):
    return [_letters(t) for t in str(text).split()]


def _norm(tok):
    return tok.strip(string.punctuation + "“”‘’—–…").lower().replace("’", "'")


def merge(heard, spoken):
    """The listener's tokens, with the model's words taken only where the listener wrote digits, symbols or an
    abbreviation. Aligned as token sequences (difflib); a span the model changed that holds a word the listener wrote
    in letters keeps the listener's own tokens, so the model can never correct, drop or add a heard word.
    Returns (text, report)."""
    import difflib
    h = str(heard).split()
    o = str(spoken).split()
    hn = [_letters(t) or ("#" + _norm(t)) for t in h]
    on = [_norm(t) for t in o]
    out, took, kept, rejected = [], 0, 0, 0
    ops = difflib.SequenceMatcher(a=hn, b=on, autojunk=False).get_opcodes()
    # A currency sign is said after the amount's scale word: "$30 million" is "thirty million dollars". The aligner
    # shows that as the number replaced, the scale word equal, and the sign's word inserted after it; the three are
    # one written token's reading, judged as one span by the rule below (14 Nov: every take of the FedEx line
    # "nearly thirty million dollars" counted a dropped word).
    k, merged = 0, []
    while k < len(ops):
        op, i1, i2, j1, j2 = ops[k]
        if (op != "equal" and any(unicodedata.category(c) == "Sc" for t in h[i1:i2] for c in t) and k + 2 < len(ops)
                and ops[k + 1][0] == "equal" and ops[k + 1][2] - ops[k + 1][1] <= 2 and ops[k + 2][0] == "insert"):
            merged.append(("replace", i1, ops[k + 2][2], j1, ops[k + 2][4]))
            k += 3
            continue
        merged.append(ops[k])
        k += 1
    for op, i1, i2, j1, j2 in merged:
        if op == "equal":
            out += h[i1:i2]
            continue
        span = hn[i1:i2]
        free = [t for t in span if t.startswith("#")]
        letters = [t for t in span if not t.startswith("#")]
        model = on[j1:j2]
        if free and not letters:
            out += o[j1:j2]
            took += len(free)
            continue
        if free:
            it = iter(model)
            if all(any(m == w for m in it) for w in letters) and len(model) - len(letters) <= 8 * len(free):
                out += o[j1:j2]                      # the listener's letter words all stand, in order, among the model's
                took += len(free)
                continue
            kept += len(free)
        if op != "insert":
            out += h[i1:i2]                          # the listener's tokens as written
        rejected += 1 if (op != "equal" and (letters or not free)) else 0
    return " ".join(out), dict(tokens=len(h), spoken_by_model=took, left_written=kept, model_changes_rejected=rejected)


def _call(system, user, model, max_tokens=24000, timeout=600):
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": [{"type": "input_text", "text": user}]}],
            "max_output_tokens": max_tokens, "reasoning": {"effort": "low"}, "store": False}
    body = openai_cache.responses(body, "narrate:spoken")
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result = json.load(r)
            openai_cache.record(model, "narrate:spoken", result.get("usage"),
                                request_id=result.get("id"), status=result.get("status"), force=True)
            return result
        except Exception:  # noqa: BLE001 - a dropped call is asked again, then reported
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def spoken_form(heard, script, names=(), model=None):
    """{"text": the transcript in spoken form, "ok": guard passed, "guard": counts, "usd", "model"} or {"ok": False,
    "why": ...} when no key or no answer. Long transcripts go in pieces of about 600 words."""
    model = model or MODEL
    if not os.environ.get("OPENAI_API_KEY"):
        return dict(ok=False, why="no OPENAI_API_KEY in this process")
    words = str(heard).split()
    pieces = [" ".join(words[i:i + 600]) for i in range(0, len(words), 600)] or [""]
    out, usd, t0 = [], 0.0, time.time()
    for k, piece in enumerate(pieces):
        if not any(_letters(t) is None for t in piece.split()):
            out.append(piece)                       # nothing written in digits or symbols: the piece is already spoken
            continue
        user = (f"Names in the story, as spelled: {json.dumps(list(names)[:80], ensure_ascii=False)}\n\n"
                f"The narrator's script (spoken form):\n{script}\n\n"
                f"Transcript piece {k + 1} of {len(pieces)} (written form):\n{piece}\n\n"
                "Answer JSON only: {\"spoken\": the transcript piece in spoken form}")
        try:
            r = _call(SYSTEM, user, model)
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, why=f"model call failed: {str(e)[:160]}")
        text = "".join(c.get("text", "") for it in r.get("output") or [] if it.get("type") == "message"
                       for c in it.get("content") or [] if c.get("type") == "output_text")
        u = r.get("usage") or {}
        pin, pout = PRICE.get(model, PRICE["gpt-5.5"])
        usd += int(u.get("input_tokens") or 0) * pin + int(u.get("output_tokens") or 0) * pout
        try:
            s = text[text.index("{"):text.rindex("}") + 1]
            out.append(str(json.loads(s)["spoken"]))
        except (ValueError, KeyError):
            return dict(ok=False, why="the model's answer had no spoken text", usd=round(usd, 4))
    text, g = merge(heard, " ".join(out))
    return dict(ok=True, text=text, guard=g, model=model, usd=round(usd, 4), seconds=round(time.time() - t0, 1),
                why=None if not g["left_written"] else f"{g['left_written']} written tokens the model could not place stay written")
