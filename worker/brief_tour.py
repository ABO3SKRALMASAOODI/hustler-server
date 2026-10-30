"""A website brief's own words as the tour's guide (QB, 30 Oct 2026).

The automatic tour (webrecord.do_tour) opens the site's section links the
request names, then the usual sections (pricing, features...). That is right
for a tile or a bare URL. A real BRIEF is longer and names things in its own
words, in its own language:

  * "Explore Início, Diagnóstico e Aprender. Em seguida, apresente as seis
    ferramentas ... gráficos ... planificação" (phmath, 40 s): the tour spent
    its time on the first two pages and never reached Ferramentas, so the
    judge's hard fault was "the six tools never appear";
  * "student guides, events, support resources" (osai.ie, 60 s): none of the
    site's navigation reads as one of those words exactly, so the tour never
    left the home page and scrolled it for 44 s (judge: "passive scroll,
    5 s holds");
  * "designer brands section ... collections ... a product page"
    (klosmo, 20 s): the brands page ate the whole run.

This module is the pure part: which of a page's links or cards a brief names,
in the brief's order, by word STEM (so "guides" finds "Halal Guide" and
"ferramentas" finds "Ferramentas" and "Acessar ferramenta"), and how a
section's time is shared. Only a tour step with ``brief: true`` uses it
(first_cut.url_chain sets it for a request that is_brief); tiles and bare
URLs keep the tour exactly as it was.
"""
import re
import unicodedata

STEM_LEN = 5

# Words that name the REQUEST, not a page of the site (a brief asks for a
# "premium cinematic promotional video" of "the actual website"). Stems of
# English, Portuguese, Spanish and French briefs.
GENERIC = frozenset((
    "creat", "premi", "video", "videos", "websi", "sites", "promo", "cinem",
    "navig", "homep", "secti", "pages", "pagin", "show", "mostr", "avail",
    "elega", "profe", "smoot", "suave", "trans", "actua", "autom", "beaut",
    "scrol", "anima", "curso", "cursor", "movem", "zooms", "look", "like",
    "techn", "tecno", "launc", "adver", "float", "brows", "windo", "motio",
    "graph", "narra", "voice", "backg", "music", "musica", "subtl", "sound",
    "effec", "leave", "enoug", "scene", "expor", "prefe", "separ", "versi",
    "keep", "desig", "image", "refin", "edito", "pacin", "socia", "media",
    "luxur", "minim", "overl", "final", "ending", "close", "start", "begin",
    "alsoo", "using", "usar", "utili", "usand", "para", "como", "with",
    "that", "this", "from", "into", "your", "their", "other", "ofert",
    "aproxi", "segun", "secon", "seria", "vertic", "horiz", "forma",
    "formato", "instr", "texto", "textos", "curto", "curtos", "preser",
    "ident", "visua", "reais", "gravaç", "grava", "funci", "seis", "five",
    "three", "cinco", "tres", "four", "quatr", "ready", "great", "every",
    "real", "main", "brand", "colors", "color", "cores", "verde", "green",
    "dark", "gold", "burgu", "white", "black", "blue", "azul",
))

_WORD = re.compile(r"[^\W\d_]{4,}")

# Q7: a few plural nouns name a PAGE even though their stem is generic: "open
# the designer brands section" (klosmo) is the site's brands page, while "the
# OSAI brand" names nobody's page. Only these exact words escape GENERIC.
CONTENT_WORDS = frozenset(("brands", "marcas", "marques", "designers"))


def _skip(word, st):
    return word in GENERIC or (st in GENERIC and word not in CONTENT_WORDS)


def fold(text):
    """Lower case without accents: 'Diagnóstico' -> 'diagnostico'."""
    nk = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in nk if not unicodedata.combining(c)).lower()


def stem(word):
    return fold(word)[:STEM_LEN]


def brief_stems(text):
    """{stem: first position in the folded text} of the brief's content
    words (4+ letters), minus the words that name the request itself."""
    low = fold(re.sub(r"https?://\S+", " ", str(text or "")))
    out = {}
    for m in _WORD.finditer(low):
        st = m.group(0)[:STEM_LEN]
        if _skip(m.group(0), st):
            continue
        out.setdefault(st, m.start())
    return out


def stem_counts(text):
    """{stem: how many times the brief uses a word of that stem}: a thing the
    brief names twice ("gráficos ... ferramenta de gráficos") is what it is
    about; a domain word used once ("ferramentas matemáticas" on a maths
    site) names nothing."""
    low = fold(re.sub(r"https?://\S+", " ", str(text or "")))
    out = {}
    for m in _WORD.finditer(low):
        st = m.group(0)[:STEM_LEN]
        if _skip(m.group(0), st):
            continue
        out[st] = out.get(st, 0) + 1
    return out


def label_score(label, text, skip=frozenset()):
    """How strongly the brief names this label: the sum of the brief's uses
    of every stem of the label's words (a label with two of the brief's
    words, or one the brief repeats, scores 2+)."""
    counts = stem_counts(text)
    return sum(counts.get(m.group(0)[:STEM_LEN], 0)
               for m in _WORD.finditer(fold(label))
               if m.group(0)[:STEM_LEN] not in skip)


def label_pos(label, text, skip=frozenset()):
    """Where in the brief this link or card label is named: the position of
    the whole label as a phrase when the brief quotes it, else of the first of
    its words whose stem the brief uses; None when the brief does not name
    it. ``skip``: stems that do not count (the section's own name, when
    looking for the cards inside it)."""
    low = fold(re.sub(r"https?://\S+", " ", str(text or "")))
    lab = " ".join(fold(label).split())
    if not lab or not low:
        return None
    own = [m.group(0) for m in _WORD.finditer(lab)]
    if 3 <= len(lab) <= 28 and any(
            w[:STEM_LEN] not in GENERIC and w not in GENERIC for w in own):
        m = re.search(r"(?<!\w)" + re.escape(lab) + r"(?!\w)", low)
        if m:
            return m.start()
    stems = brief_stems(text)
    best = None
    for m in _WORD.finditer(lab):
        st = m.group(0)[:STEM_LEN]
        if st in stems and st not in skip:
            pos = stems[st]
            best = pos if best is None else min(best, pos)
    return best


def label_stems(label):
    """The stems of a label's own words (what 'skip' holds)."""
    return frozenset(m.group(0)[:STEM_LEN] for m in _WORD.finditer(fold(label)))


def nav_fallback(links, want, avoid=(), cta=()):
    """The first ``want`` of a site's navigation links, in page order, for a
    brief that names no page we can match: short readable labels only (no
    language switch, no sign-in or call to action, no duplicates by
    address). ``links``: [{t, href}]."""
    out, seen = [], set()
    for link in links or []:
        t = str(link.get("t") or "").strip()
        lt = t.lower()
        key = str(link.get("href") or "")
        if len(t) < 4 or len(t) > 28 or key in seen:
            continue
        if any(a in lt for a in avoid) or any(lt.startswith(c) for c in cta):
            continue
        seen.add(key)
        out.append(link)
        if len(out) >= want:
            break
    return out


def section_slot(left_s, remaining):
    """Seconds one more section may take: what is left for the sections,
    shared by the sections still to open."""
    return max(0.0, float(left_s)) / max(1, int(remaining))


def stops_for(slot_s, open_s=3.0, per_stop_s=2.6, most=3, least=1):
    """How many stops down a page a section's time buys, once it has paid for
    opening and reading its heading (``least=0``: a page whose cards open
    next may buy none)."""
    return max(int(least), min(int(most),
                               int((float(slot_s) - open_s) / per_stop_s)))


_SHOP = ("collection", "shop", "product", "arrival", "catalog", "store",
         "coleccion", "colecao", "produto", "loja", "boutique")


def leads_to_products(label):
    """A section label that leads to a grid of products (a collection, the
    shop, new arrivals), the pages a 'product page' ask is reached from."""
    low = fold(label)
    return any(w in low for w in _SHOP)


# ---- Q7 (30 Oct 2026): a count of items, and the honest equivalents --------
#
# "Apresente as seis ferramentas matemáticas, utilizando gravações reais das
# funcionalidades" (phmath): the judges, every run, "show all six tools, each
# with a real interaction". A brief that counts the things on one page ("the
# six tools", "all the tools", "each tool") gets that many of the page's own
# cards, each one USED (an input typed, the button pressed, the result on
# screen), never just the list.

_COUNT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
          "sete": 7, "oito": 8, "nove": 9, "dez": 10,
          "dos": 2, "cuatro": 4, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
          "deux": 2, "trois": 3, "quatre": 4, "sept": 7, "huit": 8,
          "neuf": 9, "dix": 10}
_ALL = frozenset(("all", "every", "each", "todas", "todos", "cada", "tous",
                  "toutes", "chaque"))
_TOOL_WORDS = ("tool", "ferram", "herram", "outil", "calcul", "funcion",
               "featur", "recurs", "app")
ALL_ITEMS = 6
MOST_ITEMS = 8


def count_ask(text, label=""):
    """How many of one page's items a brief asks to see: "as seis
    ferramentas" -> 6, "show all the tools" -> ALL_ITEMS, else 0. The count
    must stand right before the page's own word (by stem: "Ferramentas"),
    or a tool word when the page is a tools page ("Tools", "Calculators"),
    so "a 60-second video" or "two sections" count nothing, and a count of
    tools never lands on another page ("Diagnóstico")."""
    low = fold(re.sub(r"https?://\S+", " ", str(text or "")))
    words = re.findall(r"[^\W_]+", low)
    own = label_stems(label)
    for i, w in enumerate(words):
        if w in _COUNT:
            n = _COUNT[w]
        elif w.isdigit() and 2 <= int(w) <= 12:
            n = int(w)
        elif w in _ALL:
            n = ALL_ITEMS
        else:
            continue
        for nxt in words[i + 1:i + 4]:
            if (len(nxt) >= 4 and nxt[:STEM_LEN] in own) or \
                    (_tool_word(nxt) and any(_tool_word(w) for w in own)):
                return min(int(n), MOST_ITEMS)
    return 0


def _tool_word(word):
    return any(str(word).startswith(t[:STEM_LEN]) for t in _TOOL_WORDS)


_JOURNEY = re.compile(
    r"\bfrom\s+([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)\s+to\s+([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)")
_OPENING = re.compile(r"\b(open(?:ing|s)?|intro(?:duction)?|start(?:s|ing)?|begin(?:s|ning)?|"
                      r"abertura|in[ií]cio|comece|apertura|ouverture)\b", re.I)


def journey(text):
    """("Muscat", "Ireland") when a brief asks to OPEN on a journey between
    two named places ("an elegant opening inspired by an Omani student's
    journey from Muscat to Ireland"); None otherwise. Only the brief's own
    place names, in a sentence about the opening: never invented."""
    for sentence in re.split(r"(?<=[.!?\n])\s+", str(text or "")):
        if not _OPENING.search(sentence):
            continue
        m = _JOURNEY.search(sentence)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None


_FLOAT = re.compile(r"floating (?:browser )?windows?|browser (?:windows?|frames?|mock-?ups?)|"
                    r"(?:device|laptop|desktop|screen) (?:frames?|mock-?ups?)|mock-?ups?|"
                    r"janelas? flutuantes?|ventanas? flotantes?|fen[eê]tres? flottantes?", re.I)


def wants_window(text):
    """The brief asks for the site shown in a floating window or a device
    frame (a 'tech launch ad' look). The capture cannot float itself, but
    the edit can: the whole picture inset on the brief's colours."""
    return bool(_FLOAT.search(str(text or "")))


_MUSIC = re.compile(r"\b(music|soundtrack|sound effects?|sfx|m[uú]sica|musique|efeitos sonoros)\b", re.I)
_LOGO = re.compile(r"\b(logo|logotipo|logotype)\b", re.I)
_BOTH = re.compile(r"(?:separate|second|also|alternate|plus|additional|another)[^.\n]{0,24}"
                   r"\b(9\s*:\s*16|16\s*:\s*9|vertical|horizontal|square|1\s*:\s*1)\b"
                   r"|\b(9\s*:\s*16|16\s*:\s*9)\b[^.\n]{0,20}\bversion", re.I)


def other_asks(text):
    """What a brief asks for that the First cut does not make itself, so the
    reply can say so plainly (never a claim, never a list of limits):
    {"music": bool, "logo": bool, "second_shape": "9:16"|... or None}."""
    t = str(text or "")
    shape = None
    m = _BOTH.search(t)
    if m:
        shape = (m.group(1) or m.group(2) or "").replace(" ", "")
    return {"music": bool(_MUSIC.search(t)), "logo": bool(_LOGO.search(t)),
            "second_shape": shape or None}
