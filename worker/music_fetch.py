"""Find a song on the open web and bring it back — search + download over
catalogs whose licence can actually be checked.

WHY THE OBVIOUS VERSION IS WRONG
The natural implementation is "search the Internet Archive filtered to
licenseurl=publicdomain, download the top hit". Measured, that returns — as
the top result for "lofi hip hop beat", tagged publicdomain/mark:

    yt-5s.com-no-copyright-10-minutes-lofi-chill-instrumental-beat-...

a YouTube rip laundered through a yt-to-mp3 site. IA's licence field is
UPLOADER-ASSERTED and unverified, so filtering on it is not a licence check;
it is a check that somebody ticked a box. Worse, it inverts: the laundered
uploads carry tags, while the genuinely-public-domain 78rpm collection
carries NO licenseurl at all (200/200 sampled: none). A licenceurl filter
therefore excludes the clean material and admits the infringing material.

So this module never trusts the tag. Each source is gated on something
external and checkable:

  * archive78 — the Great 78 Project, restricted to `year <= 1925`. Under the
    Music Modernization Act, US sound recordings published before 1926 are in
    the public domain as of 2026 (pre-1923 entered PD in 2022; 1923-1946
    recordings enter 100 years after publication). That is a fact about the
    recording's age, not a claim by its uploader. ~47k recordings.
  * commons — Wikimedia Commons, admitting only files whose extmetadata
    licence parses as CC0 or public domain. Small yield, but the licence is
    curated by a community with a deletion process rather than self-declared
    at upload.

WHAT THIS HONESTLY COVERS
Vintage recordings: jazz, blues, ragtime, dance band, classical, opera,
gospel, tango — as recorded between roughly 1900 and 1925, and sounding like
it. Plus a thin seam of public-domain classical performances from Commons.

It covers NO modern commercial song, and that is structural rather than a
gap to be closed: a song that is CC0 or old enough to be public domain is by
definition not this year's chart single. It is also weak on MOOD words —
these catalogs index titles, performers and genres, not feelings ("jazz"
returns 51,301 hits; "sad piano" returns 1). Callers must be able to say
"I could not find that", and for mood queries an empty result is the normal
case, not the edge case. Falling back to "here is something else instead" is
how a wedding video gets scored with a 1928 foxtrot.

PROVENANCE IS PART OF THE RESULT, NOT A FOOTNOTE
Every candidate carries its source, its licence basis and the page a human
can open to check. The caller states it. What we can honestly say is "this is
a 1921 recording from the Internet Archive's 78rpm collection, public domain
by age" — never "this is cleared for your use".
"""

import re
from concurrent import futures
from urllib.parse import quote

import config
import net_fetch

# Every host this module may talk to. `.archive.org` must be a SUFFIX match:
# a download redirects from the www host to whichever storage node holds the
# item (observed: ia902809.us.archive.org, dn720709.ca.archive.org), and the
# node is not predictable. net_fetch anchors the suffix on a dot, so
# `evil-archive.org` does not match.
ALLOWED_HOSTS = ["archive.org", "upload.wikimedia.org",
                 "commons.wikimedia.org"]

_IA_SEARCH = "https://archive.org/advancedsearch.php"
_IA_META = "https://archive.org/metadata/"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Wikimedia returns 403 to a request without a descriptive User-Agent, on both
# api.php and upload. This is their documented policy, not a quirk.
_UA = "valmera/1.0 (+https://valmera.io; video editor)"

# The newest year we will treat as public-domain-by-age in the US. Bump this
# ONCE PER YEAR: the Music Modernization Act rolls sound recordings into the
# public domain 100 years after publication, so in 2027 this becomes 1926.
# Deliberately a constant with a comment rather than a computed
# `now().year - 100`: a silent, automatic widening of what we call public
# domain is not something that should happen while nobody is looking.
PD_YEAR_MAX = 1925

# Audio containers we can hand to ffmpeg, best first. IA items usually carry
# several encodings of the same recording; the VBR MP3 is the best
# size/quality trade and is present on nearly every 78rpm item.
_AUDIO_EXT = (".mp3", ".ogg", ".oga", ".m4a", ".flac", ".wav")

# Lucene syntax characters. A user asking for a song called "C++" or typing an
# apostrophe must not produce a malformed query (IA answers a syntax error
# with an empty result set, which would read to the agent as "not found" and
# get reported to the user as "that song does not exist").
_LUCENE = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def available():
    return bool(config.MUSIC_FETCH_ENABLED)


def _clean(query):
    q = _LUCENE.sub(" ", str(query or ""))
    return " ".join(q.split())[:120]


def _first(v):
    """IA returns some fields as a string and others as a list of strings,
    for the same field, depending on the item."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _year_of(doc):
    y = _first(doc.get("year"))
    try:
        return int(str(y)[:4])
    except (TypeError, ValueError):
        return None


_IA_GATE = (f"mediatype:(audio) AND collection:(78rpm) "
            f"AND year:[* TO {PD_YEAR_MAX}]")


def _ia_query(q, limit):
    data = net_fetch.get_json(
        _IA_SEARCH, allowed_hosts=ALLOWED_HOSTS,
        timeout_s=config.MUSIC_FETCH_TIMEOUT_S,
        params={"q": q, "rows": limit, "page": 1, "output": "json",
                "fl[]": ["identifier", "title", "creator", "year"]})
    return (data.get("response") or {}).get("docs") or []


def _search_archive78(query, limit):
    """The Great 78 Project, gated on publication year rather than on any
    licence claim. `year:[* TO PD_YEAR_MAX]` also drops the many items with
    NO year at all, which is the conservative direction: unknown age is not
    evidence of public domain.

    TWO PASSES, because the two things users ask for want different queries.
    A bare `(st louis blues)` matches those words across every field and
    ranked "Jazzie-Addie" above the song itself; `title:("st louis blues")`
    returns ten recordings of exactly that tune from 1919-1924. So a named
    song is searched as a TITLE PHRASE first, and the loose query is only a
    fallback for genre browsing ("jazz", "ragtime piano") where there is no
    title to match."""
    clean = _clean(query)
    if not clean:
        return []
    docs = _ia_query(f'title:("{clean}") AND {_IA_GATE}', limit)
    seen = {_first(d.get("identifier")) for d in docs}
    if len(docs) < limit:
        for d in _ia_query(f"({clean}) AND {_IA_GATE}", limit - len(docs)):
            if _first(d.get("identifier")) not in seen:
                docs.append(d)
    out = []
    for d in docs:
        ident = _first(d.get("identifier"))
        if not ident:
            continue
        year = _year_of(d)
        out.append({
            "source": "archive78",
            "id": ident,
            "title": str(_first(d.get("title")) or ident),
            "artist": str(_first(d.get("creator")) or "unknown performer"),
            "year": year,
            "licence": "public domain (US, by age)",
            "licence_basis": (
                f"published {year}; US sound recordings published before "
                f"{PD_YEAR_MAX + 1} are public domain"),
            "page_url": f"https://archive.org/details/{ident}",
        })
    return out


def _ia_download_url(ident):
    """Resolve an IA item to a direct audio URL, or (None, why).

    The filename must be percent-encoded from the EXACT `name` in the
    metadata response — 78rpm filenames contain spaces, apostrophes and
    parentheses, and re-deriving the name from the title 404s."""
    meta = net_fetch.get_json(_IA_META + quote(ident),
                              allowed_hosts=ALLOWED_HOSTS,
                              timeout_s=config.MUSIC_FETCH_TIMEOUT_S)
    files = meta.get("files") or []
    best = None
    for ext in _AUDIO_EXT:
        for f in files:
            name = f.get("name") or ""
            if not name.lower().endswith(ext):
                continue
            # Skip the sample/preview derivatives IA generates.
            if "_sample" in name.lower():
                continue
            best = name
            break
        if best:
            break
    if not best:
        return None, "that item has no downloadable audio file"
    return (f"https://archive.org/download/{quote(ident)}/"
            f"{quote(best)}"), None


# Wikimedia's licence strings, normalised. Only these are admitted: CC-BY and
# CC-BY-SA are commercially usable but carry an attribution obligation this
# product cannot enforce on the customer's behalf once the file is inside
# their video, and NC/ND are outright wrong for someone monetising an export.
_PD_LICENCES = ("cc0", "public domain", "publicdomain", "pd-old", "cc-zero")


def _commons_licence(extmeta):
    for key in ("LicenseShortName", "License", "UsageTerms"):
        v = ((extmeta or {}).get(key) or {}).get("value")
        if not v:
            continue
        s = re.sub("<[^>]+>", "", str(v)).strip().lower()
        for ok in _PD_LICENCES:
            if ok in s:
                return re.sub("<[^>]+>", "", str(v)).strip()
    return None


def _search_commons(query, limit):
    data = net_fetch.get_json(
        _COMMONS_API, allowed_hosts=ALLOWED_HOSTS,
        timeout_s=config.MUSIC_FETCH_TIMEOUT_S, user_agent=_UA,
        params={"action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"filetype:audio {_clean(query)}",
                "gsrnamespace": 6, "gsrlimit": max(limit * 3, 12),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata"})
    out = []
    for p in (((data.get("query") or {}).get("pages")) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        lic = _commons_licence(ii.get("extmetadata"))
        if not lic:
            continue            # CC-BY / NC / ND / unknown -> not admitted
        url = ii.get("url") or ""
        if not url.lower().endswith(_AUDIO_EXT):
            continue
        title = str(p.get("title") or "").replace("File:", "")
        artist = ((ii.get("extmetadata") or {}).get("Artist") or {}).get(
            "value") or "unknown"
        out.append({
            "source": "commons",
            "id": title,
            "title": re.sub(r"\.[a-z0-9]+$", "", title, flags=re.I),
            "artist": re.sub("<[^>]+>", "", str(artist)).strip()[:80],
            "year": None,
            "licence": lic,
            "licence_basis": f"Wikimedia Commons file licensed {lic}",
            "page_url": f"https://commons.wikimedia.org/wiki/{quote(title)}",
            "download_url": url,
        })
        if len(out) >= limit:
            break
    return out


def search(query, limit=None):
    """Candidates across both sources, best-effort. Returns (hits, notes).

    A source that errors contributes a NOTE rather than failing the search:
    one catalog being down should degrade coverage, not turn a working
    request into an error the user sees."""
    limit = limit or config.MUSIC_FETCH_MAX_RESULTS
    sources = (("archive78", _search_archive78), ("commons", _search_commons))
    hits, notes = [], []
    # CONCURRENT, with a deadline. Sequentially, one slow catalog sets the
    # latency of the whole feature: Commons alone measured 46s from Python
    # (a broken-IPv6 connect stall that curl's Happy Eyeballs hid) while IA
    # answered in 1s. Even with that fixed, a third-party catalog having a
    # bad day should cost coverage and a note — never the user's turn.
    with futures.ThreadPoolExecutor(max_workers=len(sources)) as ex:
        pending = {ex.submit(fn, query, limit): name for name, fn in sources}
        try:
            done = futures.as_completed(
                pending, timeout=config.MUSIC_FETCH_TIMEOUT_S)
            for fut in done:
                name = pending[fut]
                try:
                    hits.extend(fut.result())
                except Exception as e:
                    notes.append(f"{name} search failed ({str(e)[:120]})")
        except futures.TimeoutError:
            slow = [n for f, n in pending.items() if not f.done()]
            notes.append(f"{', '.join(slow)} did not respond in time")
    # Stable order regardless of which thread finished first, so the same
    # query returns the same top hit twice running.
    order = {name: i for i, (name, _) in enumerate(sources)}
    hits.sort(key=lambda h: (order.get(h["source"], 9),
                             -(h.get("year") or 0)))
    return hits[: limit * 2], notes


def download(cand, out_path):
    """Fetch one candidate to out_path. Returns (ok, error).

    Never raises: a dead mirror or a withdrawn item must reach the agent as
    a result it can act on, not as a traceback mid-turn."""
    try:
        url = cand.get("download_url")
        if not url:
            url, err = _ia_download_url(cand["id"])
            if err:
                return False, err
        net_fetch.download(
            url, out_path, allowed_hosts=ALLOWED_HOSTS,
            max_bytes=config.MUSIC_FETCH_MAX_BYTES,
            timeout_s=config.MUSIC_FETCH_TIMEOUT_S, user_agent=_UA)
        return True, None
    except net_fetch.FetchError as e:
        return False, str(e)
    except Exception as e:
        return False, f"download failed ({str(e)[:160]})"


def fetch_best(query, out_path, limit=None):
    """Search, then download the first candidate that actually works.

    Returns (candidate, alternatives, notes, error).

    Walking past failures matters because IA items can be catalogued but
    empty: the top hit for "st louis blues" is a real 1925 Ferera & Paaluhi
    entry whose metadata lists ZERO files. Stopping there would report "I
    couldn't find that song" for a query the catalog answers well — the
    second hit downloads a 168s recording of the same tune. A dead item is
    an accident of the catalog, not an answer to the user."""
    hits, notes = search(query, limit=limit)
    if not hits:
        return None, [], notes, None
    tried = []
    for i, cand in enumerate(hits):
        ok, err = download(cand, out_path)
        if ok:
            others = [h for j, h in enumerate(hits) if j != i]
            return cand, others[:4], notes, None
        tried.append(f"{cand['title'][:40]}: {err}")
    return None, [], notes, "; ".join(tried[:3])


def describe(cand):
    """One line for the agent, provenance included — never just a title.

    The licence line is deliberately phrased as a claim WITH ITS BASIS. A
    catalog's licence field is a statement by whoever uploaded the file, and
    the difference between "public domain by age" and "cleared for your use"
    is the whole point of this module."""
    bits = [f'"{cand["title"]}"']
    if cand.get("artist"):
        bits.append(cand["artist"])
    if cand.get("year"):
        bits.append(str(cand["year"]))
    return f"{', '.join(bits)} — {cand['licence']} ({cand['page_url']})"
