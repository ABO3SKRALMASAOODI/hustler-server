"""Owner rule 1 in code: agents never choose music (track 0C).

Music reaches an edit only when the user supplies it (an upload, or the
sound of a clip they sent), names it (a song they asked for, fetched through
find_song / fetch_url) or picks it (a library track they chose). Every NEW
music item carries that provenance (``MusicItem.provenance``,
INTERFACES.md §4.3); an item written before the field existed reads as
``legacy`` and still renders — no migration rewrites a stored EDL, because
that would change its signature and bust its cached renders.

Where the gate holds:
  * ``check_write`` runs inside every agent EDL write (ToolContext.write_edl),
    so add_music, add_library_music, apply_look(music=), swap_music and
    apply_edit_batch writes to the music layer all pass through it. A track
    already in the edit (or anywhere in its history) keeps its provenance:
    gain, fit, mute and split edits never need a new pick. An MCP edit batch
    is written by the backend (routes/video.apply_edit_batch_core), which
    loads this file by path and runs the same pure core (``decide``,
    ``gate_items``) — so nothing here imports worker-only modules at load.
  * ``provenance_for`` derives provenance from the PROJECT ASSET, never from
    what a write claims: an uploaded file is ``user_upload``; a link fetched
    with fetch_url is ``user_named``; a library copy is
    ``user_picked_library`` only with a pick (below); a track an agent found
    by searching the web, or a sound effect, is refused as music.
  * A library pick: ``ask_user`` opens an OFFER when its question lists
    library tracks; the user's reply picks one (by slug, title or position
    in the offer). In the in-app agent the reply is the turn's own user
    message; over MCP the caller passes ``user_pick`` — the pick token
    ask_user returned plus the user's own words. A user who names a library
    track outright ("use Warm Tape") has picked it too. "Some background
    music", a mood or "you choose" never picks: the agent offers the list
    and the user chooses.

Offers and picks live in app_kv (no migration); every read and write is
best effort and never raises into a tool.
"""

import json
import re
import secrets
import time

PROVENANCES = ("user_upload", "user_named", "user_picked_library")
LEGACY = "legacy"
OFFER_TTL_S = 7 * 24 * 3600
_OFFER_KEY = "music_pick_offer:{pid}"
_PICKS_KEY = "music_picks:{pid}"
_LIB_KEY_RE = re.compile(r"^music/\d+/library-([a-z0-9][a-z0-9-]{0,80})\.mp3$")
_LEGACY_KEY_RE = re.compile(r"^legacy-music/([a-z0-9][a-z0-9-]{0,80})\.mp3$")
_TOKEN_RE = re.compile(r"\bpk_[0-9a-f]{12}\b")

REFUSE_PICK = (
    "REJECTED: agents never choose music (owner rule). Nothing was placed. "
    "Offer library tracks to the user with ask_user (2-4 exact titles from "
    "list_music_library, in the moods that fit) and place the one they pick; "
    "or use their own upload or a song they name. 'Some background music', a "
    "mood or 'you choose' is not a pick.")


WEB_SEARCH_REFUSAL = (
    "REJECTED: that track was found by an agent web search, and agents never "
    "choose music. Use the user's own upload, a song they named (find_song, "
    "fetch_url) or a library track they pick.")
SFX_REFUSAL = ("REJECTED: that file is a sound effect, not music — place it "
               "with add_sfx.")
AGENT_CLIP_REFUSAL = (
    "REJECTED: that sound was taken from a clip an agent found or generated "
    "(stock, AI video), and agents never choose music. Use the user's own "
    "upload or clip, a song they named (find_song, fetch_url) or a library "
    "track they pick.")


class MusicGateError(ValueError):
    """A new music item without an allowed provenance."""


# ── helpers ─────────────────────────────────────────────────────────────

def _dbx():
    """The worker's db module, imported on use: the backend loads this file
    by path for its MCP batch gate (pure functions only) and has no
    worker ``db`` to import."""
    import db as dbx
    return dbx


def _root(ctx):
    """The real ToolContext under staging views (recipes, sequences)."""
    seen = 0
    while seen < 6:
        base = getattr(ctx, "__dict__", {}).get("_base")
        if base is None:
            return ctx
        ctx, seen = base, seen + 1
    return ctx


def _pid(ctx):
    try:
        return int(getattr(ctx, "project_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _kv_get(ctx, key):
    try:
        raw = ctx.db.run(_dbx().kv_get, key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _kv_put(ctx, key, value):
    try:
        ctx.db.run(_dbx().kv_put, key, json.dumps(value, sort_keys=True))
        return True
    except Exception:  # noqa: BLE001
        return False


def _library():
    import music_library
    return music_library


def _norm(text):
    return " ".join(re.sub(r"[^a-z0-9#]+", " ",
                           str(text or "").lower()).split())


def is_mcp(ctx):
    """Over MCP the caller is a model relaying its own user: the pick
    arrives as ``user_pick``; the in-app agent reads the user's message."""
    return bool(getattr(ctx, "sight_out", False))


def provenance_of(item):
    """The provenance a stored music item reads as (None -> legacy)."""
    value = (item or {}).get("provenance")
    return value if value in PROVENANCES else LEGACY


# ── asset classification ────────────────────────────────────────────────

def library_slug(storage_key, meta=None):
    meta = meta or {}
    if meta.get("library_slug"):
        return str(meta["library_slug"])
    key = str(storage_key or "")
    m = _LIB_KEY_RE.match(key) or _LEGACY_KEY_RE.match(key)
    return m.group(1) if m else None


def classify_asset(asset, storage_key=None):
    """Who chose this audio file: user_upload | user_named | library |
    web_search | sfx | not_music."""
    key = str(storage_key or (asset or {}).get("storage_key") or "")
    if _LEGACY_KEY_RE.match(key) or _LIB_KEY_RE.match(key):
        return "library"
    if not asset:
        return "not_music"
    meta = asset.get("meta") or {}
    if meta.get("source") == "valmera-music-library" or \
            meta.get("library_slug"):
        return "library"
    caption = str(meta.get("caption") or "")
    if meta.get("source") == "valmera-sound-library" or \
            meta.get("library_sound") or "search_sfx" in caption or \
            key.startswith("sfx/"):
        return "sfx"
    if "search_music" in caption or meta.get("provider_candidate_id"):
        return "web_search"
    if asset.get("kind") not in (None, "music"):
        return "not_music"
    if meta.get("music_provenance") in PROVENANCES:
        return meta["music_provenance"]
    if meta.get("fetched"):
        return "user_named"
    return "user_upload"


def extracted_from(asset):
    """The storage key of the video an extracted soundtrack came from
    (agent_tools._audio_from_clip records it), else None."""
    meta = (asset or {}).get("meta") or {}
    if meta.get("extracted_from_video") and meta.get("from_asset_key"):
        return str(meta["from_asset_key"])
    return None


def agent_acquired(asset):
    """A clip an agent brought in on its own (stock search, AI generation),
    not one the user uploaded or linked."""
    meta = (asset or {}).get("meta") or {}
    caption = str(meta.get("caption") or "")
    return bool(meta.get("stock") or meta.get("research_candidate_id")
                or caption.startswith("AI-generated"))


# ── offers and picks ────────────────────────────────────────────────────

def _mentions(text):
    """[(start, end, slug)] for every library track a text names by exact
    slug or exact title. A title inside a longer named title ("A Small Town
    On Pluto" inside "A Small Town On Pluto (Music Box)") is not a second
    mention."""
    low = str(text or "").lower()
    spans = []
    for row in _library().catalog():
        pats = [r"(?<![a-z0-9-])" + re.escape(row["slug"]) + r"(?![a-z0-9-])"]
        title = str(row.get("title") or "").strip().lower()
        if len(title) >= 3:
            pats.append(r"(?<![a-z0-9])" + re.escape(title)
                        + r"(?![a-z0-9])")
        for pat in pats:
            for m in re.finditer(pat, low):
                spans.append((m.start(), m.end(), row["slug"]))
    keep = [a for a in spans
            if not any(b != a and b[0] <= a[0] and a[1] <= b[1]
                       and (b[1] - b[0]) > (a[1] - a[0]) for b in spans)]
    return sorted(keep)


def offered_slugs(question):
    """Library tracks a question offers, in the order it lists them."""
    return list(dict.fromkeys(slug for _a, _b, slug in _mentions(question)))


def open_offer(ctx, question):
    """Record that ask_user offered library tracks; the offer (and its pick
    token) is what a later pick is checked against. None when the question
    offers no library track."""
    try:
        slugs = offered_slugs(question)
    except Exception:  # noqa: BLE001 — never block a question
        return None
    if not slugs:
        return None
    offer = {"token": "pk_" + secrets.token_hex(6), "slugs": slugs,
             "at": time.time(),
             "job_id": (getattr(ctx, "job", None) or {}).get("id"),
             "question": str(question)[:400]}
    root = _root(ctx)
    try:
        root._music_offer = offer
    except Exception:  # noqa: BLE001
        pass
    _kv_put(ctx, _OFFER_KEY.format(pid=_pid(ctx)), offer)
    return offer


def offer_note(offer):
    """What an MCP caller is told with the question (the in-app user never
    sees it: their reply itself is checked)."""
    return (f" [Valmera pick token {offer['token']}: after the user picks, "
            f"call add_library_music(slug, user_pick='{offer['token']} "
            "<their own words>'). Never choose for them.]")


def latest_offer(ctx):
    offer = getattr(_root(ctx), "_music_offer", None)
    if not isinstance(offer, dict):
        offer = _kv_get(ctx, _OFFER_KEY.format(pid=_pid(ctx)))
    if not isinstance(offer, dict) or not offer.get("slugs"):
        return None
    if time.time() - float(offer.get("at") or 0) > OFFER_TTL_S:
        return None
    return offer


def _picks(ctx):
    rows = _kv_get(ctx, _PICKS_KEY.format(pid=_pid(ctx)))
    return rows if isinstance(rows, dict) else {}


def _remember_pick(ctx, record):
    picks = _picks(ctx)
    picks[record["slug"]] = record
    if len(picks) > 64:
        for slug, _r in sorted(picks.items(),
                               key=lambda kv: kv[1].get("at", 0))[:-64]:
            picks.pop(slug, None)
    _kv_put(ctx, _PICKS_KEY.format(pid=_pid(ctx)), picks)
    root = _root(ctx)
    try:
        cache = getattr(root, "_music_picks", None) or {}
        cache[record["slug"]] = record
        root._music_picks = cache
    except Exception:  # noqa: BLE001
        pass


def picked(ctx, slug):
    """The durable pick record for a library slug in this project, if the
    user picked it before."""
    cache = getattr(_root(ctx), "_music_picks", None) or {}
    return cache.get(slug) or _picks(ctx).get(slug)


_STRONG_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2,
                    "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
                    "fifth": 5, "5th": 5, "sixth": 6, "6th": 6}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6}
_POSITION_CUES = ("option", "number", "track", "song", "#", "no")


def _positions(text, n_offered):
    """1-based positions a reply points at: an ordinal ("the second one",
    "the last"), or a bare number when the reply is that short or cued
    ("2", "#2", "option two")."""
    words = _norm(text).split()
    idx = {_STRONG_ORDINALS[w] for w in words if w in _STRONG_ORDINALS}
    if "last" in words:
        idx.add(n_offered)
    if not idx:
        for i, w in enumerate(words):
            num = _NUMBER_WORDS.get(w)
            m = re.fullmatch(r"#?(\d)", w)
            if m:
                num = int(m.group(1))
            if num is None:
                continue
            cued = i > 0 and words[i - 1] in _POSITION_CUES
            if cued or len(words) <= 4:
                idx.add(num)
    return {n for n in idx if 1 <= n <= n_offered}


def identify(text, offer_slugs=()):
    """The ONE library slug ``text`` identifies, else None: an exact slug or
    title anywhere in the catalog, or a position in the offered list. Two
    different tracks named is no pick."""
    hits = [slug for _a, _b, slug in _mentions(text)]
    offer_slugs = list(offer_slugs or ())
    if offer_slugs:
        hits += [offer_slugs[n - 1]
                 for n in sorted(_positions(text, len(offer_slugs)))]
    hits = list(dict.fromkeys(hits))
    return hits[0] if len(hits) == 1 else None


def resolve_pick(ctx, slug, user_pick=None):
    """(record, error) for placing library track ``slug``.

    A track the user picked before in this project stays picked. Otherwise
    the pick comes from the user: the in-app turn's own message, or over
    MCP ``user_pick`` (the token ask_user returned plus the user's words).
    A position ("the second one") needs the offer ask_user opened; a slug
    or title names the track outright."""
    prior = picked(ctx, slug)
    if prior:
        return prior, None
    offer = latest_offer(ctx)
    mcp = is_mcp(ctx)
    evidence = (user_pick if mcp else getattr(ctx, "user_message", "")) or ""
    if mcp and offer and offer.get("token") and offer["token"] not in str(
            evidence) and identify(evidence, ()) != slug:
        return None, (REFUSE_PICK + " Over MCP pass user_pick: the pick "
                      "token ask_user returned plus the user's own words.")
    slugs = (offer or {}).get("slugs") or ()
    same_turn = (offer and not mcp and offer.get("job_id") is not None and
                 offer.get("job_id") == (getattr(ctx, "job", None)
                                         or {}).get("id"))
    who = identify(evidence, () if same_turn else slugs)
    if who != slug:
        if who:
            return None, (f"REJECTED: the user picked '{who}', not '{slug}'. "
                          "Place the track they chose.")
        return None, REFUSE_PICK
    record = {"slug": slug, "at": time.time(),
              "via": "mcp" if mcp else "chat",
              "offer": (offer or {}).get("token"),
              "evidence": " ".join(str(evidence).split())[:200]}
    _remember_pick(ctx, record)
    return record, None


# ── named songs ─────────────────────────────────────────────────────────

def note_named_song(ctx, query, hits=()):
    """find_song: the user named this song; fetch_url stamps what it then
    downloads as user_named (and which name it answered)."""
    root = _root(ctx)
    try:
        named = getattr(root, "_named_songs", None) or {}
        for h in hits or ():
            url = str((h or {}).get("url") or "")
            if url:
                named[url] = str(query or "")[:200]
        named.setdefault("", str(query or "")[:200])
        root._named_songs = named
    except Exception:  # noqa: BLE001
        pass


def named_song_for(ctx, url):
    named = getattr(_root(ctx), "_named_songs", None) or {}
    return named.get(str(url or "")) or None


# ── the write gate ──────────────────────────────────────────────────────

def grant(ctx, storage_key, provenance):
    """A tool that already verified this key's provenance in this call (a
    library pick) hands it to the write gate."""
    root = _root(ctx)
    try:
        grants = getattr(root, "_music_grants", None) or {}
        grants[str(storage_key)] = provenance
        root._music_grants = grants
    except Exception:  # noqa: BLE001
        pass


def _granted(ctx, key):
    return (getattr(_root(ctx), "_music_grants", None) or {}).get(key)


# An earlier version of this project played this exact track (shared with
# the backend's MCP batch gate).
HISTORY_SQL = ("SELECT 1 FROM edls WHERE project_id = %s AND "
               "json::jsonb @> %s::jsonb LIMIT 1")


def history_args(project_id, key):
    return (project_id, json.dumps({"music": [{"storage_key": key}]}))


def picks_key(project_id):
    """The app_kv row holding a project's library picks."""
    return _PICKS_KEY.format(pid=project_id)


def _seen_in_history(ctx, key):
    """Has this exact track played in an earlier version of this project?
    Then re-adding it (an undo, a rebuild) is not a new choice."""
    pid = _pid(ctx)
    if not pid:
        return False

    def q(conn):
        with conn.cursor() as cur:
            cur.execute(HISTORY_SQL, history_args(pid, key))
            return cur.fetchone() is not None
    try:
        return bool(ctx.db.run(q))
    except Exception:  # noqa: BLE001
        return False


def _existing(edl, key):
    """(present, provenance) of ``key`` in an EDL's music."""
    found, prov = False, None
    for m in (edl or {}).get("music") or []:
        if isinstance(m, dict) and m.get("storage_key") == key:
            found = True
            if m.get("provenance") in PROVENANCES:
                return True, m["provenance"]
    return found, prov


def decide(key, asset, *, is_picked, seen_before, pick=None, source=None):
    """(provenance, error) for a track new to the edit, from its project
    ``asset`` row (never from what a write claims). Pure: the worker's write
    gate and the backend's MCP batch gate (routes/video.py) share it.

    ``is_picked(slug)``: the user picked that library track before in this
    project. ``seen_before(key)``: the track played in an earlier version, so
    re-adding it is not a new choice (provenance stays legacy). ``pick(slug)``
    resolves a pick from the user's own words now; without it an unpicked
    library track is refused. ``source``: the asset row of the video an
    extracted soundtrack came from (``extracted_from``), when known — the
    sound of a clip an agent found is not the user's."""
    kind = classify_asset(asset, key)
    if kind in ("user_upload", "user_named"):
        if source is not None and agent_acquired(source):
            if seen_before(key):
                return None, None
            return None, AGENT_CLIP_REFUSAL
        return kind, None
    if kind == "library":
        slug = library_slug(key, (asset or {}).get("meta"))
        if slug and is_picked(slug):
            return "user_picked_library", None
        if seen_before(key):
            return None, None
        if slug and pick is not None:
            return pick(slug)
        return None, REFUSE_PICK
    if kind == "web_search":
        if seen_before(key):
            return None, None
        return None, WEB_SEARCH_REFUSAL
    if kind == "sfx":
        return None, SFX_REFUSAL
    return None, (f"REJECTED: '{key}' is not a music asset in this project.")


def provenance_for(ctx, storage_key, edl=None, user_pick=None,
                   resolved=False):
    """(provenance, error) for placing ``storage_key`` as music now.
    provenance None means "inherits legacy" (the track was already in this
    edit before provenance existed). ``resolved``: the caller already proved
    the key is a project music asset (add_music's _resolve_music), so an
    unreadable asset row is classified by its key alone."""
    key = str(storage_key or "")
    g = _granted(ctx, key)
    if g in PROVENANCES:
        return g, None
    present, prov = _existing(edl, key)
    if present:
        return prov, None
    asset = None
    try:
        asset = ctx.db.run(_dbx().asset_by_key, ctx.project_id, key)
    except Exception:  # noqa: BLE001
        asset = None
    if asset is None and resolved:
        asset = {"kind": "music", "storage_key": key, "meta": {}}

    source = None
    src_key = extracted_from(asset)
    if src_key:
        try:
            source = ctx.db.run(_dbx().asset_by_key, ctx.project_id, src_key)
        except Exception:  # noqa: BLE001
            source = None

    def _pick(slug):
        _rec, err = resolve_pick(ctx, slug, user_pick=user_pick)
        return (None, err) if err else ("user_picked_library", None)

    return decide(key, asset, is_picked=lambda slug: bool(picked(ctx, slug)),
                  seen_before=lambda k: _seen_in_history(ctx, k), pick=_pick,
                  source=source)


def gate_items(prev_edl, new_edl, decide_key):
    """Gate one EDL write against its previous version (pure core of
    ``check_write``; the backend's MCP batch gate uses it too). Every music
    item whose track is new to the edit gets the provenance
    ``decide_key(storage_key) -> (provenance, error)`` returns, stamped in
    place on ``new_edl``; a track already there keeps its provenance whatever
    the write claims. Returns an error string, or None when the write may
    proceed. A decision that raises refuses that track (fail closed) instead
    of failing the write with a traceback."""
    items = (new_edl or {}).get("music") or []
    if not items:
        return None
    prev_tracks = {}
    for m in (prev_edl or {}).get("music") or []:
        if isinstance(m, dict) and m.get("storage_key"):
            cur = prev_tracks.get(m["storage_key"])
            if cur is None or (cur not in PROVENANCES
                               and m.get("provenance") in PROVENANCES):
                prev_tracks[m["storage_key"]] = m.get("provenance")
    decided = {}
    for m in items:
        if not isinstance(m, dict):
            continue
        key = m.get("storage_key")
        if key in prev_tracks:
            inherited = prev_tracks[key]
            if m.get("provenance") != inherited:
                m["provenance"] = inherited
            continue
        if key not in decided:
            try:
                prov, err = decide_key(key)
            except Exception:  # noqa: BLE001 — never a traceback, never a pass
                prov, err = None, ("REJECTED: who chose this track could not "
                                   "be verified, so nothing was placed; try "
                                   "again.")
            if err:
                return "music gate: " + err.replace("REJECTED: ", "", 1)
            decided[key] = prov
        m["provenance"] = decided[key]
    return None


def check_write(ctx, prev_edl, new_edl):
    """Gate one agent EDL write. Stamps provenance on every music item whose
    track is new to the edit (in place on ``new_edl``) and keeps the
    provenance of tracks already there; returns an error string, or None
    when the write may proceed."""
    return gate_items(prev_edl, new_edl,
                      lambda key: provenance_for(ctx, key))


def validate_new_items(prev_edl, new_edl):
    """Pure check (no project context): every music item whose track is new
    to the edit must carry an allowed provenance. Raises MusicGateError —
    the AT-0C-4 'a MusicItem without provenance fails validation' half that
    needs no database."""
    prev_keys = {m.get("storage_key") for m in (prev_edl or {}).get("music")
                 or [] if isinstance(m, dict)}
    for i, m in enumerate((new_edl or {}).get("music") or []):
        if not isinstance(m, dict) or m.get("storage_key") in prev_keys:
            continue
        if m.get("provenance") not in PROVENANCES:
            raise MusicGateError(
                f"music[{i}] ({m.get('id') or m.get('storage_key')}) has no "
                "provenance: new music must be user_upload, user_named or "
                "user_picked_library — agents never choose music.")
