"""Meaning -> verb (Wave 2, track 2F): at most one or two verbs a sentence.

Each spoken sentence is read for what it MEANS on screen (2A's roles: the
hero words; the lexical and rhetoric tags the device planner reads) and
mapped to the one verb that shows it best, a second only when the sentence
carries two separate ideas. Restraint is the point: a sentence that is a
bridge gets nothing.

  number      -> add_motion_graphic counter (the figure as said)
  contrast    -> add_typeset whisper_shout (the turn in two weights)
  list        -> add_motion_graphic list_build (the accumulating list)
  name/place  -> add_typeset (an identification lockup) or find_shots
  payoff      -> add_typeset on the payoff word, on the drop when there is one
  hook        -> add_typeset hook / pick_still freeze poster
  callback    -> add_typeset callback_of (the second telling rhymes)
  action      -> find_shots (b-roll of the thing done) / plan_to_music
"""

import re

MAX_PER_SENTENCE = 2
_NUM = re.compile(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|"
                  r"hundred|thousand|million|billion|percent)\b", re.I)
_CONTRAST = re.compile(r"\b(but|instead|not|never|rather|yet|however|"
                       r"versus|vs)\b", re.I)
_ACTION = re.compile(r"\b(build|built|launch\w*|fly|flying|run|running|"
                     r"make|making|drive|driving|walk\w*|ship\w*)\b", re.I)


def sentences(words):
    """[(i0, i1)] word-index spans, split on sentence punctuation or a
    pause of 0.7 s."""
    out, start = [], 0
    for i, w in enumerate(words):
        txt = str(w.get("w") or "")
        gap = (float(words[i + 1]["t0"]) - float(w.get("t1", w["t0"]))
               if i + 1 < len(words) else 0.0)
        if txt.endswith((".", "?", "!")) or gap >= 0.7 or i == len(words) - 1:
            out.append((start, i))
            start = i + 1
    return [s for s in out if s[1] >= s[0]]


def read(words, i0, i1, tags=None, position="mid"):
    """The meanings of one sentence (most specific first)."""
    text = " ".join(str(w.get("w") or "") for w in words[i0:i1 + 1])
    tags = tags or set()
    out = []
    if position in ("hook", "payoff"):
        out.append(position)
    if "callback" in tags:
        out.append("callback")
    if _NUM.search(text) or "number" in tags:
        out.append("number")
    if _CONTRAST.search(text) or "contrast" in tags:
        out.append("contrast")
    if "list" in tags or text.count(",") >= 2:
        out.append("list")
    if _ACTION.search(text):
        out.append("action")
    return out


VERB_FOR = {
    "hook": ("add_typeset", {"position": "hook"}),
    "payoff": ("add_typeset", {"position": "payoff"}),
    "callback": ("add_typeset", {"callback": True}),
    "number": ("add_motion_graphic", {"template": "counter"}),
    "contrast": ("add_typeset", {"voice": "whisper_shout"}),
    "list": ("add_motion_graphic", {"template": "list_build"}),
    "action": ("find_shots", {}),
}


def plan(words, tags_by_sentence=None, n_hook=1):
    """[{i0, i1, t0, t1, text, meanings, verbs}] with at most
    MAX_PER_SENTENCE verbs a sentence and none on a bridge."""
    spans = sentences(words)
    out = []
    for k, (i0, i1) in enumerate(spans):
        pos = "hook" if k < n_hook else "payoff" if k == len(spans) - 1 \
            else "mid"
        ms = read(words, i0, i1, (tags_by_sentence or {}).get(k), pos)
        verbs = []
        seen = set()
        for m in ms:
            v = VERB_FOR.get(m)
            if v and v[0] not in seen:
                verbs.append({"verb": v[0], "args": dict(v[1]),
                              "meaning": m})
                seen.add(v[0])
            if len(verbs) >= MAX_PER_SENTENCE:
                break
        out.append({"i0": i0, "i1": i1,
                    "t0": float(words[i0]["t0"]),
                    "t1": float(words[i1].get("t1", words[i1]["t0"])),
                    "text": " ".join(str(w.get("w") or "")
                                     for w in words[i0:i1 + 1]),
                    "meanings": ms, "verbs": verbs})
    return out
