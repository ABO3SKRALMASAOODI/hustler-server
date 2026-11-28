"""Align timed ASR pieces to ICU dictionary words for unspaced scripts."""
import bisect
import ctypes as C
import ctypes.util
import functools
import itertools
import os

FAMILIES = {"th": "Noto Sans Thai", "ja": "Noto Sans JP", "zh": "Noto Sans SC"}
LANGUAGES = set(FAMILIES)


def join_tokens(tokens):
    """Join timed words without inserting a Latin space between Thai/CJK units."""
    import unicodedata
    def unspaced(char):
        name = unicodedata.name(char, "")
        return name.startswith(("THAI ", "CJK ", "HIRAGANA ", "KATAKANA ", "IDEOGRAPHIC "))
    out = ""
    for token in tokens:
        if not token:
            continue
        if out and not (unspaced(out[-1]) and unspaced(token[0])):
            out += " "
        out += token
    return out


def caption_family(language):
    return FAMILIES.get((language or "").lower().split("-")[0])


@functools.lru_cache(maxsize=1)
def _iterator_api():
    # libicu is shipped with the worker's media stack. Darwin exports its C
    # API unsuffixed; Linux exports a suffix matching the library soname.
    name = C.util.find_library("icuuc") or C.util.find_library("icucore")
    lib = C.CDLL(name)
    suffix = os.path.basename(name).split(".")[-1]
    def symbol(name, args, result):
        fn = getattr(lib, name, None) or getattr(lib, name + "_" + suffix)
        fn.argtypes, fn.restype = args, result
        return fn
    return (symbol("ubrk_open", [C.c_int, C.c_char_p, C.c_void_p, C.c_int, C.POINTER(C.c_int)], C.c_void_p),
            symbol("ubrk_next", [C.c_void_p], C.c_int),
            symbol("ubrk_close", [C.c_void_p], None))


def _units(text, lang):
    open_it, advance, close = _iterator_api()
    data = text.encode("utf-16-le")
    status = C.c_int(0)
    it = open_it(1, lang.encode(), data, len(data) // 2, C.byref(status))
    if not it or status.value > 0:
        return [text]
    parts, start = [], 0
    try:
        while (end := advance(it)) != -1:
            parts.append(data[2*start:2*end].decode("utf-16-le"))
            start = end
    finally:
        close(it)
    return parts


def _aligned(rows, lang, text):
    compact = ["".join(row["w"].split()) for row in rows]
    plain = "".join(compact)
    if text is None or "".join(text.split()) != plain:
        text = plain
        # Historical indexes dropped the transcript's whitespace. ICU has
        # dictionaries for Thai/CJK. For adjacent Latin subwords in such an
        # index recover lexical boundaries without changing any character.
        import wordninja
        groups = itertools.groupby(rows, key=lambda r: r["w"].isascii() and r["w"].isalpha())
        runs = []
        for latin, group in groups:
            words = [row["w"] for row in group]
            joined = "".join(words)
            if latin:
                words = wordninja.split(joined) if len(words) > 1 and min(map(len, words)) <= 3 else words
                joined = " ".join(words)
            runs.append(joined)
        text = " ".join(runs)
    ends = list(itertools.accumulate(map(len, compact)))
    out, pos = [], 0
    for unit in _units(text, lang):
        if unit.isspace():
            continue
        end = pos + len(unit)
        i, j = bisect.bisect_right(ends, pos), bisect.bisect_left(ends, end)
        if j >= len(rows) or plain[pos:end] != unit:
            return rows
        def at(n, offset, lo, hi):
            row = rows[n]
            fraction = (offset - (ends[n] - len(compact[n]))) / len(compact[n])
            return round(row[lo] + (row[hi] - row[lo]) * fraction, 3)
        row = dict(rows[i], w=unit, t0=at(i, pos, "t0", "t1"), t1=at(j, end, "t0", "t1"))
        if rows[i].get("brk") and pos > ends[i] - len(compact[i]):
            row.pop("brk", None)
        if all(r.get(k) is not None for r in (rows[i], rows[j]) for k in ("src_t0", "src_t1")):
            row.update(src_t0=at(i, pos, "src_t0", "src_t1"), src_t1=at(j, end, "src_t0", "src_t1"))
        out.append(row)
        pos = end
    return out if pos == len(plain) else rows


def timed(rows, language, transcript=None):
    lang = (language or "").lower().split("-")[0]
    if lang not in LANGUAGES or not rows:
        return rows
    runs, start, search_from = [], 0, 0
    transcript = transcript or ""
    offsets = [n for n, char in enumerate(transcript) if not char.isspace()]
    letters = "".join(transcript[n] for n in offsets)
    for i in range(1, len(rows) + 1):
        if (i == len(rows) or rows[i].get("brk")
                or rows[i].get("speaker") != rows[i - 1].get("speaker")
                or rows[i]["t0"] - rows[i - 1]["t1"] > .45):
            part = rows[start:i]
            excerpt = None
            if letters:
                spelling = "".join("".join(r["w"].split()) for r in part)
                match = letters.find(spelling, search_from)
                if spelling and match >= 0:
                    search_from = match + len(spelling)
                    excerpt = transcript[offsets[match]:offsets[search_from - 1] + 1]
            runs.extend(_aligned(part, lang, excerpt))
            start = i
    return runs


def first_cut_index(index):
    """Old uploads retain their saved source index. Make a coherent planning
    view with sentence word IDs on the repaired clock."""
    if not index or not caption_family(index.get("language")) or index.get("script_word_boundaries"):
        return index
    words = timed(index.get("words") or [], index["language"])
    if words == index.get("words"):
        return index
    from schemas import Word
    from transcribe import group_sentences
    sentences = group_sentences([Word(**row) for row in words])
    return dict(index, words=words, sentences=[s.model_dump() for s in sentences], script_word_boundaries=True)
