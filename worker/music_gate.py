"""Music provenance: who chose each track in the edit, stamped as information.

The owner's rule (13 Nov 2026) replaces "agents never choose music": "Valmera
may choose music itself, guided by the references (the kind of music creators
use for each kind of video); optimise for the highest quality; the customer
decides whether to keep it." Nothing here refuses a track for who chose it
(owner, 16 Nov 2026: no refusals on the editing path). Every NEW music item
carries its provenance (``MusicItem.provenance``, INTERFACES.md §4.3), read
from the PROJECT ASSET, never from what a write claims:

  * an uploaded file is ``user_upload``; a link the user named (find_song,
    fetch_url) is ``user_named``;
  * a library track the user picked (from an offer, or by naming it) is
    ``user_picked_library``;
  * any track Valmera chose itself (a library track nobody picked, music_choose's
    pick, web-found music, the sound of a clip Valmera found) is
    ``valmera_chosen``: the customer sees it and can remove it.

An item written before the field existed reads as ``legacy`` and still
renders. A track already in the edit (or anywhere in its history) keeps its
provenance. The only refusal left is a technical one: a key that is not audio
of this project cannot be placed. An MCP edit batch is written by the backend
(routes/video.apply_edit_batch_core), which loads this file by path and runs
the same pure core (``decide``, ``gate_items``), so nothing here imports
worker-only modules at load.

Owner rule 2 (sound effects come only from the approved library while web
sounds are off) is a repair, never a refusal: a sound search_sfx once fetched
is placed as the closest recording of the approved library and the result
says so (``repair_web_sounds``).
"""

import json
import re
import secrets
import time

# valmera_chosen (owner, 13 Nov 2026): "Valmera may pick the track itself ... the customer decides whether to
# keep it." Only music_choose places it: a track from the licensed library (worker/music_library/) that the
# chooser copied into the project and registered with chosen_by "valmera". The customer can remove it.
PROVENANCES = ("user_upload", "user_named", "user_picked_library", "valmera_chosen")
LEGACY = "legacy"
OFFER_TTL_S = 7 * 24 * 3600
_OFFER_KEY = "music_pick_offer:{pid}"
_PICKS_KEY = "music_picks:{pid}"
_LIB_KEY_RE = re.compile(r"^music/\d+/library-([a-z0-9][a-z0-9-]{0,80})\.mp3$")
_LEGACY_KEY_RE = re.compile(r"^legacy-music/([a-z0-9][a-z0-9-]{0,80})\.mp3$")
_TOKEN_RE = re.compile(r"\bpk_[0-9a-f]{12}\b")

NOT_MUSIC = "REJECTED: '{key}' is not an audio asset in this project."


class MusicGateError(ValueError):
    """A new music item without a provenance stamp."""


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
            "<their own words>') so the track is stamped as their pick.]")


def latest_offer(ctx):
    offer = getattr(_root(ctx), "_music_offer", None)
    if not isinstance(offer, dict):
        offer = _kv_get(ctx, _OFFER_KEY.format(pid=_pid(ctx)))
    if not isinstance(offer, dict) or not offer.get("slugs"):
        return None
    if time.time() - float(offer.get("at") or 0) > OFFER_TTL_S:
        return None
    return offer


def _read_pick(ctx, text, offered=(), offer_text=""):
    """A model's reading of the user's reply: {'slug', ...} only when it
    picks one track (music_pick.read), else None. Never raises."""
    try:
        import music_pick
        verdict = music_pick.read(ctx, text, music_pick.candidates(offered),
                                  offer_text or "")
    except Exception as exc:  # noqa: BLE001 - no reading is no pick
        print(f"[music_gate] pick reading unavailable: {str(exc)[:160]}",
              flush=True)
        return None
    if verdict and verdict.get("kind") == "picked" and verdict.get("slug"):
        return verdict
    return None


def bind_turn_choice(ctx, text, previous_reply=None, previous_reply_id=None):
    """Resolve the incoming answer against the offer the customer actually saw.

    Freeze it before the editor can open another offer. Durable pick provenance
    then survives both context compaction and continuation into another job.
    Ambiguous answers and MCP calls retain the existing explicit-pick flow.
    """
    if is_mcp(ctx):
        return None
    root = _root(ctx)
    bound = getattr(root, '_music_turn_choice', None)
    if bound:
        return bound
    offer = latest_offer(ctx)
    # A bare number answers the latest visible offer, not any week-old offer
    # left in storage. "Make 2 clips" is a new instruction, not a music pick.
    # The preceding visible message is authoritative. A later retry can have
    # overwritten the mutable offer cache, or that cache may have expired.
    # Neither changes what the customer's original answer selected.
    slugs = offered_slugs(previous_reply) if previous_reply else ()
    selection_words = (set(_STRONG_ORDINALS) | set(_NUMBER_WORDS)
                       | set(_POSITION_CUES) | {'the', 'last', 'please', 'use',
                                               'pick', 'choose', 'go', 'with', 'one',
                                               'add', 'play', 'music'})
    answer = _norm(text).split()
    positional = bool(answer) and all(w in selection_words or w.isdigit()
                                     or w.startswith('#') for w in answer)
    if not positional:
        slugs = ()
    slug = identify(text, slugs)
    if slug and not positional:
        remainder = str(text)
        for start, end, _slug in reversed(_mentions(text)):
            remainder = remainder[:start] + ' ' + remainder[end:]
        if any(w not in selection_words for w in _norm(remainder).split()):
            slug = None  # naming a track while rejecting/discussing it: read it
    via = 'chat'
    if not slug:
        # Ticket #1088: "Calm current" answered an offer of "Calm Currents"
        # and no exact rule saw it. A model reads the reply against the
        # offer the customer saw; delegation and moods are never a pick.
        offered = offered_slugs(previous_reply) if previous_reply else []
        verdict = _read_pick(ctx, text, offered, previous_reply)
        if not verdict:
            return None
        slug, via = verdict['slug'], 'chat_read'
        # a track named outright from the library is their pick, not an answer to that offer
        slugs = offered if slug in offered else ()
    record = {'slug': slug, 'at': time.time(), 'via': via,
              'offer': ((offer or {}).get('token')
                        if list(slugs) == (offer or {}).get('slugs') else None),
              'offer_message_id': previous_reply_id,
              'named': bool(via == 'chat_read' and not slugs),
              'evidence': str(text)[:200]}
    _remember_pick(ctx, record)
    root._music_turn_choice = record
    return record


def choice_note(ctx, text, previous_reply=None, previous_reply_id=None):
    try:
        choice = bind_turn_choice(ctx, text, previous_reply, previous_reply_id)
    except Exception:  # unavailable catalog/storage is not a fabricated choice
        return ''
    if not choice:
        return ''
    where = (" by its name in the music library" if choice.get('named')
             else " from the previously displayed offer")
    return ("The customer's current answer selects library track "
            + choice['slug'] + where + ". Load the "
            "audio tools, place that exact track with add_library_music, and "
            "verify it. Do not offer a replacement list or ask them to pick again.")


def repeat_offer_refusal(ctx, question):
    choice = getattr(_root(ctx), '_music_turn_choice', None)
    failed = any(r.get('tool') == 'add_library_music' and r.get('kind') == 'failed'
                 and not r.get('resolved')
                 for r in getattr(ctx, 'turn_tool_outcomes', []) or [])
    if failed:
        return None  # an actual placement failure may require another choice
    if choice and offered_slugs(question):
        return ("REJECTED: this user already selected " + choice['slug']
                + ". Place their selection with add_library_music; do not "
                  "replace the list they answered. If that track is unavailable, "
                  "explain the concrete failure before asking for an alternative.")
    return None


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
    """(record, None) for placing library track ``slug``: never an error.

    A track the user picked before in this project stays picked. Otherwise the
    user's own words are read for a pick: the in-app turn's own message, or
    over MCP ``user_pick`` (the token ask_user returned plus the user's
    words). When they picked this track the record is their pick
    (``via`` chat / chat_read / mcp, remembered for the project); when they
    did not, Valmera is choosing it (``via`` "valmera", owner rule of
    13 Nov 2026), and ``customer_named`` says which track they did name, if
    any, so the editor can be told."""
    prior = picked(ctx, slug)
    if prior:
        return prior, None
    offer = latest_offer(ctx)
    mcp = is_mcp(ctx)
    evidence = (user_pick if mcp else getattr(ctx, "user_message", "")) or ""
    slugs = (offer or {}).get("slugs") or ()
    same_turn = (offer and not mcp and offer.get("job_id") is not None and
                 offer.get("job_id") == (getattr(ctx, "job", None)
                                         or {}).get("id"))
    if mcp and offer and offer.get("token") and offer["token"] not in str(evidence):
        # Over MCP a position ("the third one") answers the offer only with
        # the pick token ask_user returned; without it only a named track
        # is the user's pick.
        slugs = ()
    try:
        who = identify(evidence, () if same_turn else slugs)
    except Exception:  # noqa: BLE001 — an unreadable catalog is no pick
        who = None
    via = "mcp" if mcp else "chat"
    if who is None and not mcp and str(evidence).strip():
        # Ticket #1088: the exact rules did not see a pick ("Calm current"
        # for "Calm Currents"); a model reads the user's own message against
        # the offer they answered (or the catalog, before any offer).
        verdict = _read_pick(ctx, evidence,
                             [] if same_turn else list(slugs),
                             "" if same_turn else (offer or {}).get("question"))
        if verdict:
            who, via = verdict["slug"], "chat_read"
    if who == slug:
        record = {"slug": slug, "at": time.time(), "via": via,
                  "offer": (offer or {}).get("token"),
                  "evidence": " ".join(str(evidence).split())[:200]}
        _remember_pick(ctx, record)
        return record, None
    return {"slug": slug, "at": time.time(), "via": "valmera",
            "customer_named": who}, None


def pick_provenance(record):
    """The provenance a resolve_pick record stamps."""
    return ("valmera_chosen" if (record or {}).get("via") == "valmera"
            else "user_picked_library")


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
    path and the backend's MCP batch path (routes/video.py) share it.

    Never refuses for who chose a track: what the user supplied, named or
    picked is stamped so, and anything else is ``valmera_chosen`` (owner,
    13 Nov 2026: Valmera may choose the music; the customer decides whether
    to keep it). provenance None means the track played in an earlier version
    (``seen_before``), so it inherits legacy. ``is_picked(slug)``: the user
    picked that library track before in this project. ``pick(slug)`` reads a
    pick from the user's own words now -> (provenance, None). ``source``: the
    asset row of the video an extracted soundtrack came from. The only error
    is technical: a key that is no audio asset of this project."""
    kind = classify_asset(asset, key)
    if kind == "not_music":
        return None, NOT_MUSIC.format(key=key)
    if kind in ("user_upload", "user_named"):
        if source is not None and agent_acquired(source):
            return (None, None) if seen_before(key) else ("valmera_chosen", None)
        return kind, None
    if kind == "library":
        slug = library_slug(key, (asset or {}).get("meta"))
        if ((asset or {}).get("meta") or {}).get("chosen_by") == "valmera":
            return "valmera_chosen", None
        if slug and is_picked(slug):
            return "user_picked_library", None
        if seen_before(key):
            return None, None
        if slug and pick is not None:
            return pick(slug)
        return "valmera_chosen", None
    if seen_before(key):
        return None, None
    return "valmera_chosen", None


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
        rec, _err = resolve_pick(ctx, slug, user_pick=user_pick)
        return pick_provenance(rec), None

    return decide(key, asset, is_picked=lambda slug: bool(picked(ctx, slug)),
                  seen_before=lambda k: _seen_in_history(ctx, k), pick=_pick,
                  source=source)


def gate_items(prev_edl, new_edl, decide_key):
    """Stamp one EDL write's music against its previous version (pure core
    of ``check_write``; the backend's MCP batch path uses it too). Every music
    item whose track is new to the edit gets the provenance
    ``decide_key(storage_key) -> (provenance, error)`` returns, stamped in
    place on ``new_edl``; a track already there keeps its provenance whatever
    the write claims. Returns an error string only for a key that is no audio
    of this project, else None. A decision that raises stamps the track as
    Valmera's choice: provenance is information, never a reason to fail."""
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
            except Exception:  # noqa: BLE001 — never a traceback, never a refusal
                prov, err = "valmera_chosen", None
            if err:
                return "music: " + err.replace("REJECTED: ", "", 1)
            decided[key] = prov
        m["provenance"] = decided[key]
    return None


def check_write(ctx, prev_edl, new_edl):
    """Stamp one agent EDL write's music: provenance on every music item
    whose track is new to the edit (in place on ``new_edl``), the provenance
    of tracks already there kept. Returns an error only for a key that is no
    audio of this project (it cannot play), else None."""
    return gate_items(prev_edl, new_edl,
                      lambda key: provenance_for(ctx, key))


# ── owner rule 2 on the write path (web sounds): a repair ───────────────
# Sound effects come only from the owner-approved library. With the web-SFX
# chain off, a sound an earlier search_sfx fetched into the project is never
# refused: wherever a write (add_sfx, an edit batch) places it, the closest
# recording of the approved library of real recordings plays instead, and
# the result says so. Sounds already in the edit keep playing.

def web_found_sound(asset):
    """A sound search_sfx / add_web_sfx fetched from the web (the fetch
    records it in the asset caption), not one the user uploaded."""
    meta = (asset or {}).get("meta") or {}
    return "search_sfx" in str(meta.get("caption") or "")


def new_sfx_keys(prev_edl, new_edl):
    """Storage keys on sound-effect items of ``new_edl`` that no sound-effect
    item of ``prev_edl`` plays (sorted)."""
    before = {s.get("storage_key") for s in (prev_edl or {}).get("sfx") or []
              if isinstance(s, dict)}
    return sorted({s.get("storage_key") for s in (new_edl or {}).get("sfx")
                   or [] if isinstance(s, dict) and s.get("storage_key")
                   and s.get("storage_key") not in before})


def web_sound_keys(prev_edl, new_edl, asset_of):
    """The keys new to the edit that a web search fetched (pure; the
    caller asks only while web sounds are off). A lookup that raises reads
    as not web-found: the sound plays as written."""
    out = []
    for key in new_sfx_keys(prev_edl, new_edl):
        try:
            asset = asset_of(key)
        except Exception:  # noqa: BLE001
            continue
        if web_found_sound(asset):
            out.append(key)
    return out


def grant_sfx(ctx, storage_key):
    """add_sfx resolved this key in this call: the write repair skips it."""
    root = _root(ctx)
    try:
        grants = getattr(root, "_sfx_grants", None) or set()
        grants.add(str(storage_key))
        root._sfx_grants = grants
    except Exception:  # noqa: BLE001
        pass


def _sfx_cleared(ctx, key):
    """No lookup needed: add_sfx resolved it, or it is an approved library
    recording (never web-found)."""
    if key in (getattr(_root(ctx), "_sfx_grants", None) or ()):
        return True
    try:
        import sound_library
        return sound_library.id_for_key(key) is not None
    except Exception:  # noqa: BLE001
        return False


def _web_sounds_on():
    try:
        import sfx_search
        return bool(sfx_search.available())
    except Exception:  # noqa: BLE001 — the flag unreadable reads as off
        return False


def _signal_nearest(path):
    """The approved recording nearest a sound file on the sound engine's
    signal ruler (score.sound_signal.distance over the library's measured
    shapes), or None when the file cannot be read."""
    from score import sound as SD
    from score import sound_choice as CH
    from score import sound_signal as SS
    import sound_library
    samples = SD.pcm(path, SS.SR, 2)
    if not len(samples):
        return None
    shape = SS.describe(samples.mean(axis=1))
    if not shape:
        return None
    playable = {r["id"] for r in sound_library.playable()}
    rows = [(SS.distance(shape, d), sid) for sid, d in
            CH.describe_library(SD.library(), SD.SOUNDS, SD.pcm).items()
            if sid in playable]
    return min(rows)[1] if rows else None


def library_stand_in(ctx, asset):
    """(sound_id, how) of the approved recording closest to a web-found
    sound: measured on its signal when its file can be read, else the
    library recording its own title describes, else the library's general
    pick for a one-shot. Never raises; (None, "") with no library."""
    try:
        import sound_library
    except Exception:  # noqa: BLE001
        return None, ""
    try:
        import agent_tools
        sid = _signal_nearest(agent_tools._asset_local_path(ctx, asset))
        if sid:
            return sid, "nearest by its measured sound"
    except Exception as exc:  # noqa: BLE001 — the title still describes it
        print(f"[web sound] not measured: {str(exc)[:160]}", flush=True)
    meta = (asset or {}).get("meta") or {}
    title = " ".join(str(meta.get(k) or "") for k in ("title", "filename"))
    try:
        hits = sound_library.search(title, 1)
        if hits:
            return hits[0]["id"], "the library recording its title describes"
        for role in ("whoosh", "hit", "click", "pop"):
            row = sound_library.pick(role, title)
            if row:
                return row["id"], "the library's general one-shot"
    except Exception:  # noqa: BLE001
        pass
    return None, ""


def repair_web_sounds(ctx, prev_edl, new_edl):
    """Owner rule 2 inside every agent EDL write (ToolContext.write_edl), as
    a repair: each sound-effect item new to the edit whose sound a web search
    fetched plays the closest approved library recording instead (in place
    on ``new_edl``, its hit on the same moment). A stand-in that cannot be
    prepared is tried once more; if it still cannot, that one sound is left
    out of the saved write (the web file never plays) and the note says so,
    while the rest of the write saves. Returns the note, or "". Never
    raises."""
    if _web_sounds_on():
        return ""
    keys = [k for k in new_sfx_keys(prev_edl, new_edl) if not _sfx_cleared(ctx, k)]
    if not keys:
        return ""
    assets = {}

    def asset_of(key):
        assets[key] = ctx.db.run(_dbx().asset_by_key, ctx.project_id, key)
        return assets[key]
    web = None
    for _attempt in range(2):
        try:
            web = web_sound_keys(prev_edl, {"sfx": [{"storage_key": k} for k in keys]},
                                 asset_of)
            break
        except Exception as exc:  # noqa: BLE001 — read once more
            print(f"[web sound] lookup failed: {str(exc)[:160]}", flush=True)
    if web is None:
        # Which of the new sounds came from the web could not be read; a
        # project sound that is no web find (an upload, a library copy) is
        # the usual case, so they stay as written.
        return ""
    lines, dropped = [], []
    for key in web:
        name = str(((assets.get(key) or {}).get("meta") or {}).get("filename") or key)
        sid = lib_key = None
        how, why, row = "", "no approved recording was found for it", {}
        for _attempt in range(2):
            try:
                import motion_tools
                import sound_library
                sid, how = library_stand_in(ctx, assets.get(key))
                if not sid:
                    continue
                lib_key = motion_tools.ensure_library_asset(ctx, sid)
                row = sound_library.get(sid) or {}
                break
            except Exception as exc:  # noqa: BLE001 — tried once more
                lib_key, why = None, str(exc)[:160]
                print(f"[web sound] {key}: stand-in not prepared: {why}", flush=True)
        if not (sid and lib_key):
            items = (new_edl or {}).get("sfx") or []
            new_edl["sfx"] = [it for it in items
                              if not (isinstance(it, dict) and it.get("storage_key") == key)]
            dropped.append(f"'{name[:60]}' was not placed: web sounds are off and the "
                           f"approved recording that plays in its place could not be "
                           f"prepared ({why}); add_sfx again")
            continue
        for item in (new_edl or {}).get("sfx") or []:
            if not isinstance(item, dict) or item.get("storage_key") != key:
                continue
            hit = float(item.get("at") or 0.0)
            item["storage_key"] = lib_key
            for k in ("offset_s", "dur_s"):
                item.pop(k, None)
            pl = sound_library.place(sid, hit)
            item["at"] = pl["at"]
            for k in ("offset_s", "dur_s"):
                if pl[k]:
                    item[k] = pl[k]
            if row.get("gain_db") is not None:
                item["gain_db"] = float(row["gain_db"])
        lines.append(f"'{name[:60]}' -> {sid} ({how})")
    out = []
    if lines:
        out.append("SOUND REPLACED (saved): web sounds are off, so the closest recording "
                   "of the approved library plays instead: " + "; ".join(lines)
                   + ". Tell the user which sound plays; remove_sfx takes it out.")
    if dropped:
        out.append("SOUND NOT PLACED (the rest of the write saved): " + "; ".join(dropped)
                   + ". Do NOT tell the user that sound was added.")
    return " ".join(out)


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
                "provenance stamp: new music is user_upload, user_named, "
                "user_picked_library or valmera_chosen.")
