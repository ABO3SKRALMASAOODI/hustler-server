"""The callback detector (Wave 2, track 2E).

A speaker who says the same phrase twice ("bigger than the internet" ...
"bigger than the internet") gives the edit a rhyme: the second telling
should LOOK like the first (2A ``typeset.memory``: same geometry, mirrored
or not). ``find(words)`` returns every repeated run of at least two words
with a content word in it, first telling and each later one, on whatever
clock the words carry.
"""

import re

_STRIP = re.compile(r"[^\w']+")
STOP = frozenset("""
a an the and or but so of to in on at for with is are was were be been it
its it's this that these those i you he she we they me him her us them my
your our their do does did not no yes just like very really um uh
""".split())


def norm(w):
    return _STRIP.sub("", str(w or "").lower())


def _function_words():
    try:
        import caption_carry
        return STOP | caption_carry.FUNCTION_WORDS
    except Exception:  # noqa: BLE001
        return STOP


def _carries(gram):
    """A repeat worth a rhyme says something: a content word of three
    letters or more ('we have a', 'it is the' are speech habits, not a
    callback)."""
    fw = _function_words()
    return any(g not in fw and len(g) >= 3 for g in gram)


def find(words, min_len=2, max_len=6, min_gap_s=3.0):
    """[{text, first: (i0, i1), again: [(i0, i1), ...], t_first, t_again}]
    longest runs first; a run inside a longer reported run is dropped."""
    toks = [norm(w.get("w")) for w in words]
    n = len(toks)
    found = {}
    for L in range(max_len, min_len - 1, -1):
        seen = {}
        for i in range(0, n - L + 1):
            gram = tuple(toks[i:i + L])
            if not all(gram) or not _carries(gram):
                continue
            seen.setdefault(gram, []).append(i)
        for gram, starts in seen.items():
            if len(starts) < 2:
                continue
            keep = [starts[0]]
            for s in starts[1:]:
                if float(words[s]["t0"]) - float(words[keep[-1]]["t0"]) >= \
                        min_gap_s and s >= keep[-1] + L:
                    keep.append(s)
            if len(keep) < 2:
                continue
            covered = any(set(range(keep[0], keep[0] + L)) <=
                          set(range(f[0], f[0] + len(g)))
                          for g, f in found.items())
            if covered:
                continue
            found[gram] = keep
    out = []
    for gram, starts in found.items():
        L = len(gram)
        out.append({"text": " ".join(str(words[starts[0] + k]["w"])
                                     for k in range(L)),
                    "first": (starts[0], starts[0] + L - 1),
                    "again": [(s, s + L - 1) for s in starts[1:]],
                    "t_first": round(float(words[starts[0]]["t0"]), 3),
                    "t_again": [round(float(words[s]["t0"]), 3)
                                for s in starts[1:]]})
    out.sort(key=lambda c: (-len(c["text"].split()), c["t_first"]))
    return out


def for_typeset(callbacks, edl):
    """[(callback, first typeset item id)] where the first telling already
    has a typeset lockup: the second one should be set as its callback
    (add_typeset(callback_of=...))."""
    items = [m for m in (edl or {}).get("motion") or []
             if m.get("template") == "typeset"]
    out = []
    for c in callbacks:
        words = set(norm(w) for w in c["text"].split())
        for m in items:
            txt = set(norm(w) for w in str((m.get("params") or {}).get(
                "text", "")).replace("*", " ").split())
            if words and words <= txt:
                out.append((c, m["id"]))
                break
    return out
