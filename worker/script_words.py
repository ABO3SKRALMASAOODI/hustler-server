"""Align timed ASR pieces to ICU dictionary words for unspaced scripts."""
import bisect
import ctypes as C
import ctypes.util
import functools
import itertools
import os

LANGUAGES = {"th", "ja", "zh"}


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
    plain = "".join(row["w"] for row in rows)
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
    ends = list(itertools.accumulate(len(row["w"]) for row in rows))
    out, pos = [], 0
    for unit in _units(text, lang):
        if unit.isspace():
            continue
        end = pos + len(unit)
        i, j = bisect.bisect_right(ends, pos), bisect.bisect_left(ends, end)
        if j >= len(rows) or plain[pos:end] != unit:
            return rows
        def at(n, offset):
            row = rows[n]
            start = ends[n] - len(row["w"])
            return round(row["t0"] + (row["t1"] - row["t0"]) * (offset-start)/len(row["w"]), 3)
        row = dict(rows[i], w=unit, t0=at(i, pos), t1=at(j, end))
        if "src_t0" in row:
            row["src_t0"] = rows[i]["src_t0"] + row["t0"] - rows[i]["t0"]
            row["src_t1"] = rows[j]["src_t1"] + row["t1"] - rows[j]["t1"]
        out.append(row)
        pos = end
    return out if pos == len(plain) else rows


def timed(rows, language, transcript=None):
    lang = (language or "").lower().split("-")[0]
    if lang not in LANGUAGES or not rows:
        return rows
    runs, start = [], 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i].get("brk") or rows[i].get("speaker") != rows[i-1].get("speaker") or rows[i]["t0"] - rows[i-1]["t1"] > .45:
            part = rows[start:i]
            # Use the recognizer's original spaces wherever it supplied them.
            excerpt = None
            if transcript:
                import re
                pattern = r"\s*".join(re.escape(row["w"]) for row in part)
                found = re.search(pattern, transcript)
                if found:
                    excerpt = found.group()
                    transcript = transcript[found.end():]
            runs.extend(_aligned(part, lang, excerpt))
            start = i
    return runs


def first_cut_index(index):
    """Old uploads retain their saved source index. Make a coherent planning
    view with sentence word IDs on the repaired clock."""
    if not index or index.get("language") not in LANGUAGES or index.get("script_word_boundaries"):
        return index
    words = timed(index.get("words") or [], index["language"])
    if words == index.get("words"):
        return index
    from schemas import Word
    from transcribe import group_sentences
    sentences = group_sentences([Word(**row) for row in words])
    return dict(index, words=words, sentences=[s.model_dump() for s in sentences], script_word_boundaries=True)
