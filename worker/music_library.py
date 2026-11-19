"""Valmera's built-in CC0 music library: licence-verified beds, no upload needed.

History. The 24-track pack shipped in git until 2026-08-08, when it was
deleted and its audio copied to object storage under ``legacy-music/``
(every production EDL was rewritten to those plain keys, so old projects
keep rendering). From then on an autonomous edit could only score a video
with music the user uploaded or linked, and the measured result was that
Valmera reels shipped silent: the audit's biggest single gap against the
owner's references was music (about 7.8 points of 10).

What this module restores is the CATALOGUE, not the files. ``music/
manifest.json`` lists every track with its licence record and the exact
storage object holding its audio; ``music/LICENSES.md`` and
``music/SOURCE_NOTES.md`` are the paper trail (24/24 verified CC0 1.0 on
each track's own source page, twice). The mp3s stay out of git.

Security shape (keep it this way):
- A track is chosen by an EXACT slug from the manifest — a whitelist
  lookup, never a prefix or substring match. The renderer downloads any key
  it is handed with no project scoping, so a loose resolver here would be a
  read primitive over the whole bucket.
- The copy source is the manifest's literal ``storage_key`` (validated to be
  ``legacy-music/<file>.mp3`` at load), never a string the caller built.
- Placement goes through ``add_music`` on a PROJECT asset: the object is
  copied once into ``music/<project_id>/library-<slug>.mp3`` and registered
  with its licence metadata, so every downstream guard (``_resolve_music``,
  the asset inventory, the music verifier) sees an ordinary project file.

Retired tracks stay in the manifest (their licence record survives) but are
never listed and never placeable.
"""

import difflib
import hashlib
import json
import os
import re
import threading

import db as dbx
import storage

MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
MANIFEST_PATH = os.path.join(MUSIC_DIR, "manifest.json")
SOURCE_PREFIX = "legacy-music/"

# Plain-language moods (the words users actually say). Editorial labels —
# the source does not state them (see LICENSES.md).
MOODS = ("upbeat", "chill", "cinematic", "corporate",
         "dramatic", "hiphop", "ambient", "inspiring")
MOOD_NOTES = {
    "upbeat": "bright, driving pop energy",
    "chill": "lofi, relaxed, warm",
    "cinematic": "orchestral-leaning, story underscore",
    "corporate": "light, positive, explainer bed",
    "dramatic": "tense, weighty, building",
    "hiphop": "dark boom-bap beats",
    "ambient": "airy pads, minimal rhythm",
    "inspiring": "gentle, hopeful melodies",
}
LICENSE_NOTE = ("CC0 1.0 public-domain dedication — free for commercial use, "
                "no attribution or other obligations")
# Bed level under a voice. The renderer's smooth duck dips the bed further
# while words play, so this is the level BETWEEN phrases.
DEFAULT_BED_DB = -18.0

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.mp3$")
_LOCK = threading.Lock()
_CACHE = None


def _valid_row(row):
    if not isinstance(row, dict):
        return False
    slug, fn = row.get("slug"), row.get("file")
    if not (isinstance(slug, str) and _SLUG_RE.match(slug)):
        return False
    if not (isinstance(fn, str) and _FILE_RE.match(fn)):
        return False
    if row.get("storage_key") != SOURCE_PREFIX + fn:
        return False
    if row.get("mood") not in MOODS:
        return False
    if str(row.get("license") or "").upper() != "CC0":
        return False
    return True


def entries():
    """Every manifest row (retired included), validated, in manifest order."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            try:
                with open(MANIFEST_PATH) as f:
                    rows = json.load(f)
            except (OSError, ValueError):
                rows = []
            seen, out = set(), []
            for row in rows if isinstance(rows, list) else []:
                if _valid_row(row) and row["slug"] not in seen:
                    seen.add(row["slug"])
                    out.append(dict(row))
            _CACHE = out
        return [dict(r) for r in _CACHE]


def catalog():
    """What may be OFFERED and placed: every non-retired track."""
    return [r for r in entries() if not r.get("retired")]


def available():
    return bool(catalog())


def _norm_slug(value):
    s = str(value or "").strip().lower()
    if s.startswith("library:"):        # stale pre-2026-08 references
        s = s[len("library:"):]
    return s


def find(slug):
    """(track, error) by EXACT slug. Never a prefix/substring match."""
    s = _norm_slug(slug)
    rows = {r["slug"]: r for r in entries()}
    row = rows.get(s)
    if row and row.get("retired"):
        return None, (f"REJECTED: '{row['title']}' was retired from the "
                      "library and cannot be placed. Pick another track from "
                      "list_music_library.")
    if row:
        return row, None
    offer = [r["slug"] for r in catalog()]
    near = difflib.get_close_matches(s, offer, n=3, cutoff=0.5)
    hint = (" Closest slugs: " + ", ".join(near) + "." if near else "")
    return None, (f"REJECTED: '{slug}' is not a library slug.{hint} Call "
                  "list_music_library for the exact slugs — the slug must "
                  "match exactly.")


def browse(mood=None):
    return [r for r in catalog() if mood is None or r["mood"] == mood]


def describe(t):
    bits = [f'"{t["title"]}" by {t.get("author") or "unknown"}', t["mood"]]
    if t.get("duration_s"):
        bits.append(f"{float(t['duration_s']):.0f}s")
    return ", ".join(bits)


def body_start(track):
    """Seconds into the track where its main body starts after a quiet intro
    (manifest ``body_s``; 0.0 when it has none). Twelve tracks open with
    7.5-25.6 s sitting 5-19 LU under their own body — most or all of a
    short. Measured on a 14 s editorial render: the bed started at 0 sat
    21.8 dB under the voice while words played, 16.4 dB started here."""
    try:
        b = float(track.get("body_s") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    try:
        d = float(track.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        d = 0.0
    if b <= 0.0 or (d and b >= d - 10.0):
        return 0.0
    return round(b, 2)


def project_key(project_id, track):
    return f"music/{int(project_id)}/library-{track['slug']}.mp3"


def used_by_user(ctx):
    """Library slugs this user already scored OTHER projects with (fail-open).

    Round 52 lesson, kept: a real customer wrote "do not use the exact
    background music as previous projects". Marked and avoided by auto
    selection, never hidden."""
    try:
        user_id = (getattr(ctx, "job", None) or {}).get("user_id")
        if not user_id:
            return set()
        rows = ctx.db.run(dbx.library_music_used_by_user, user_id,
                          ctx.project_id)
        return {str(r) for r in (rows or []) if r}
    except Exception:
        return set()


def pick(moods, seed, avoid=()):
    """Deterministic track for a mood preference list.

    The first mood with a track the user has not used elsewhere wins; within
    it the seed (the project) rotates the choice, so sibling shorts of one
    podcast do not all carry the same bed. When everything has been used,
    fall back to the first mood's full list."""
    moods = [m for m in (moods or ()) if m in MOODS] or list(MOODS)
    h = int(hashlib.sha256(str(seed).encode()).hexdigest()[:12], 16)
    avoid = set(avoid or ())
    for mood in moods:
        fresh = [t for t in browse(mood) if t["slug"] not in avoid]
        if fresh:
            return fresh[h % len(fresh)]
    for mood in moods:
        pool = browse(mood)
        if pool:
            return pool[h % len(pool)]
    return None


def _missing_object(exc):
    code = ""
    try:
        code = str(exc.response.get("Error", {}).get("Code") or "")
    except Exception:
        pass
    text = str(exc)
    return code in ("404", "NoSuchKey", "NotFound") or "Not Found" in text \
        or "NoSuchKey" in text


COPY_ATTEMPTS = 3
COPY_BACKOFF_S = (0.5, 2.0)


def _copy_sources(ctx, track):
    """Where a track's bytes can be copied from, best first: the library
    object, then byte-identical copies earlier placements made in other
    projects (music/<pid>/library-<slug>.mp3)."""
    out = [track["storage_key"]]
    try:
        rows = ctx.db.run(dbx.library_music_copies, track["slug"]) or []
    except Exception:  # noqa: BLE001 - the library object is still tried
        rows = []
    for k in rows:
        if k and k not in out and str(k).endswith(
                f"/library-{track['slug']}.mp3"):
            out.append(k)
    return out


def _copy_track(ctx, track, key):
    """Copy the track's audio into the project (EDIT-CORE A4, 16 Nov 2026).
    Every track the editor can see is placeable: a transient storage error
    is retried, and a library object missing from this deployment's store
    is taken from a copy another project already holds. Returns None, or the
    error when no source answers after the retries."""
    import time
    last = None
    sources = None
    for attempt in range(COPY_ATTEMPTS):
        if attempt:
            time.sleep(COPY_BACKOFF_S[min(attempt - 1, len(COPY_BACKOFF_S) - 1)])
        try:
            storage.copy_object(track["storage_key"], key)
            return None
        except Exception as e:  # noqa: BLE001
            last = e
            if not _missing_object(e):
                continue        # transient: the same object again
        sources = sources if sources is not None else _copy_sources(ctx, track)
        for src in sources[1:]:
            try:
                storage.copy_object(src, key)
                print(f"[music_library] {track['slug']}: placed from {src} "
                      "(the library object is missing here)", flush=True)
                return None
            except Exception as e:  # noqa: BLE001
                last = e
        if _missing_object(last):
            break
    if _missing_object(last):
        return (f"UNAVAILABLE: the audio for library track '{track['slug']}' "
                "is missing from storage on this deployment, so nothing was "
                "placed. Place another track from list_music_library, or the "
                "user's own music. Do NOT claim music was added.")
    return (f"Could not prepare library track '{track['slug']}' after "
            f"{COPY_ATTEMPTS} attempts ({str(last)[:160]}). Nothing was "
            "placed; do NOT claim music was added.")


def ensure_project_asset(ctx, track):
    """(storage_key, error): the track as a project music asset, copied once."""
    key = project_key(ctx.project_id, track)
    try:
        existing = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
    except Exception:
        existing = None
    if existing and existing.get("kind") == "music":
        return key, None
    try:
        present = storage.exists(key)
    except Exception:
        present = False
    if not present:
        err = _copy_track(ctx, track, key)
        if err:
            return None, err
    try:
        ctx.db.run(
            dbx.insert_asset, ctx.project_id, "music", key,
            bytes_=track.get("bytes"), duration_s=track.get("duration_s"),
            meta={"filename": f"{track['title']} — {track.get('author')}.mp3",
                  "source": "valmera-music-library",
                  "caption": "Valmera CC0 music library",
                  "library_slug": track["slug"],
                  "library_object": track["storage_key"],
                  "title": track["title"], "author": track.get("author"),
                  "mood": track["mood"], "license": "CC0",
                  "license_note": LICENSE_NOTE,
                  "source_url": track.get("source_url"),
                  # sha256 of the file as it last shipped in git; the storage
                  # copy was made from it (see music/LICENSES.md).
                  "source_sha256": track.get("sha256")})
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not register library track '{track['slug']}' "
                      f"in this project ({str(e)[:160]}). Nothing was "
                      "placed; do NOT claim music was added.")
    return key, None


def _at():
    import agent_tools
    return agent_tools


# ── the reference-grade tracks (19 Nov 2026) ──────────────────────────────
# Valmera's licensed library (music_choose: music_library/manifest.json) now
# carries the reference-grade set (Mixkit and Pixabay tracks that passed the
# full signal gate against the @metromedia.house beds). They are offered here
# beside the CC0 pack, placed by "lib:<cid>", so an editor (and an MCP app)
# asking for a mood gets music of reference grade. The CC0 moods map onto the
# set's catalogue moods (a fixed mapping of catalogue labels).
REF_PREFIX = "lib:"
REF_MOODS = {
    "upbeat": ("upbeat / pop", "confident / hype"),
    "chill": ("chill / lofi",),
    "cinematic": ("tension / cinematic", "emotional / reflective"),
    "corporate": ("upbeat / pop",),
    "dramatic": ("tension / cinematic", "dark / mysterious"),
    "hiphop": ("confident / hype", "dark / mysterious"),
    "ambient": ("emotional / reflective", "chill / lofi"),
    "inspiring": ("emotional / reflective", "upbeat / pop"),
}


def reference_tracks(mood=None):
    """The reference-grade tracks, optionally for one CC0 mood. [] when the
    library has none (never raises)."""
    try:
        import music_choose
        rows = [t for t in music_choose.reference_pool()
                if t.get("reference_set")]
    except Exception:  # noqa: BLE001
        return []
    if mood:
        want = REF_MOODS.get(mood, ())
        rows = [t for t in rows if t.get("mood") in want]
    return rows


def reference_find(slug):
    s = _norm_slug(slug)
    cid = s[len(REF_PREFIX):] if s.startswith(REF_PREFIX) else s
    return next((t for t in reference_tracks() if t["cid"] == cid), None)


def _reference_lines(mood=None):
    rows = reference_tracks(mood)
    if not rows:
        return ""
    groups = {}
    for t in rows:
        groups.setdefault(t.get("mood") or "other", []).append(t)
    lines = [f"\nREFERENCE GRADE ({len(rows)} track(s), measured against the "
             "reference reels' music; place with add_library_music(slug="
             "'lib:<cid>'); Mixkit / Pixabay licences, free for video, no "
             "credit required). Prefer these:"]
    for name in sorted(groups):
        lines.append(f"{name}:")
        for t in groups[name]:
            bits = [f"  {REF_PREFIX}{t['cid']} — \"{t['title']}\" by "
                    f"{t.get('author') or 'unknown'}"]
            if t.get("bpm"):
                bits.append(f"{float(t['bpm']):.0f} bpm")
            if t.get("duration_s"):
                bits.append(f"{float(t['duration_s']):.0f}s")
            lines.append(", ".join(bits))
    return "\n".join(lines)


def _add_reference(ctx, track, start=None, end=None, gain_db=None, duck=None,
                   fade_in_s=None, fade_out_s=None, offset_s=None, loop=None,
                   purpose=None, user_pick=None):
    import music_choose
    import music_gate
    record, _none = music_gate.resolve_pick(ctx, track["cid"],
                                            user_pick=user_pick)
    key, err = music_choose.ensure_project_asset(ctx, {"cid": track["cid"]})
    if err:
        return (f"NOT PLACED: {err}. Another reference-grade track of the "
                "same mood places the same way.")
    music_gate.grant(ctx, key, music_gate.pick_provenance(record))
    purpose_n = " ".join(str(purpose or "").split()) or (
        f"{track.get('mood') or 'reference-grade'} bed from Valmera's "
        f"library (\"{track['title']}\")")
    res = _at().add_music(
        ctx, key, start=start, end=end, gain_db=gain_db, duck=duck,
        offset_s=offset_s, fade_in_s=fade_in_s, fade_out_s=fade_out_s,
        loop=True if loop is None else bool(loop), purpose=purpose_n)
    if str(res).startswith("EDL v"):
        res += (f"\nTrack: \"{track['title']}\" by {track.get('author')} "
                f"({track.get('mood')}) — {track.get('license')}, no credit "
                f"required. Source: {track.get('page_url')}")
        if record.get("via") == "valmera":
            res += "\nChosen by Valmera (the customer can keep or remove it)."
    return res


def list_music_library(ctx, mood=None):
    rows = catalog()
    if not rows and not reference_tracks():
        return ("The built-in music library is not available on this "
                "deployment. Use the user's own music (list_assets "
                "kind='music') or a link they paste (fetch_url).")
    m = (mood or "").strip().lower() or None
    if m and m not in MOODS:
        return (f"REJECTED: unknown mood '{mood}'. Moods: "
                + ", ".join(MOODS) + ".")
    hits = browse(m)
    used = used_by_user(ctx)
    head = (f"{len(hits)} CC0 track(s)" + (f" for mood '{m}'" if m else "")
            + " — licence-verified, free for commercial use, no credit "
              "required. Choose the one that fits this film's kind and pace "
              "and place it with add_library_music(slug=...): Valmera may "
              "choose the music, and the customer decides whether to keep it "
              "(a track they named or picked is theirs). "
              "Under a voice it sits as a ducked bed (about -18 to -22 dB); "
              "with no speech it plays as the lead.\n"
              "These moods are ALL the built-in music (no techno/EDM/rock/"
              "phonk). If the user asked for a genre outside them, place the "
              "closest mood and say so; their own track (upload or link) can "
              "replace it.\n")
    lines = []
    for mood_name in MOODS:
        group = [t for t in hits if t["mood"] == mood_name]
        if not group:
            continue
        lines.append(f"{mood_name} ({MOOD_NOTES[mood_name]}):")
        for t in group:
            tag = "  [already used in this user's other projects]" \
                if t["slug"] in used else ""
            lines.append(f"  {t['slug']} — {describe(t)}{tag}")
    return head + "\n".join(lines) + _reference_lines(m)


def add_library_music(ctx, slug, start=None, end=None, gain_db=None,
                      duck=None, fade_in_s=None, fade_out_s=None,
                      offset_s=None, loop=None, purpose=None, user_pick=None):
    # duck=None lets add_music decide from the program: a bed ducks under
    # speech, while music that IS the audio (no speech) is never pumped by
    # the sidechain on every loud original sound.
    ref = reference_find(slug)
    if ref is not None:
        return _add_reference(ctx, ref, start=start, end=end,
                              gain_db=gain_db, duck=duck,
                              fade_in_s=fade_in_s, fade_out_s=fade_out_s,
                              offset_s=offset_s, loop=loop, purpose=purpose,
                              user_pick=user_pick)
    track, err = find(slug)
    if err:
        return err
    # Who chose it (music_gate.resolve_pick): the user's pick (from an offer,
    # or by naming it) stamps user_picked_library; otherwise Valmera chose it
    # (owner, 13 Nov 2026), stamped valmera_chosen so the customer can see
    # and remove it. Never a refusal.
    import music_gate
    record, _none = music_gate.resolve_pick(ctx, track["slug"],
                                            user_pick=user_pick)
    key, err = ensure_project_asset(ctx, track)
    if err:
        return err
    music_gate.grant(ctx, key, music_gate.pick_provenance(record))
    purpose_n = " ".join(str(purpose or "").split()) or (
        f"{track['mood']} bed from the Valmera CC0 library "
        f"(\"{track['title']}\")")
    body = body_start(track) if offset_s is None else 0.0
    res = _at().add_music(
        ctx, key, start=start, end=end, gain_db=gain_db, duck=duck,
        offset_s=(body or None) if offset_s is None else offset_s,
        fade_in_s=fade_in_s, fade_out_s=fade_out_s,
        loop=True if loop is None else bool(loop), purpose=purpose_n)
    if str(res).startswith("EDL v"):
        if body:
            res += (f"\nStarts {body:g}s into the track, where its body "
                    "begins after a quiet intro (offset_s=0 plays the "
                    "intro).")
        res += (f"\nTrack: \"{track['title']}\" by {track.get('author')} "
                f"({track['mood']}) — {LICENSE_NOTE}. Source: "
                f"{track.get('source_url')}")
        if record.get("via") == "valmera":
            res += ("\nChosen by Valmera (the customer can keep or remove it)"
                    + (f"; the customer named '{record['customer_named']}'"
                       if record.get("customer_named") else "") + ".")
    return res


TOOL_SPECS = {
    "list_music_library": (
        list_music_library,
        "READ: the built-in music library by mood: the CC0 pack and the "
        "reference-grade tracks ('lib:<cid>'), exact slugs for "
        "add_library_music (choose the track that fits the film, or place the "
        "one the user named). Licence-verified background tracks (upbeat, "
        "chill, cinematic, corporate, dramatic, hiphop, ambient, inspiring), "
        "free for commercial use with no credit required; marks tracks this "
        "user already used in other projects.",
        {"mood": {"type": "string", "enum": list(MOODS),
                  "description": "Optional mood filter."}}),
    "add_library_music": (
        add_library_music,
        "Place a library track by exact slug (from list_music_library; "
        "'lib:<cid>' for a reference-grade track): "
        "the one the user named or picked, or the one you choose for the film "
        "(Valmera may choose the music; it is stamped as Valmera's choice and "
        "the customer can remove it). The track is copied into this project once, "
        "registered with its licence, and placed like add_music — OUTPUT "
        "seconds, whole program by default, ducked under speech, looped to "
        "fill. Under a voice keep the bed around -18 to -22 dB (the default "
        "-18 dB is used when gain_db is omitted); with no speech it becomes "
        "the lead at -4 dB.",
        {"slug": {"type": "string"},
         "user_pick": {"type": "string",
                       "description": "MCP: the pick token ask_user returned "
                                      "plus the user's own words."},
         "start": {"type": "number"}, "end": {"type": "number"},
         "gain_db": {"type": "number"},
         "duck": {"type": "boolean",
                  "description": "Omit to duck under speech only; with no "
                                 "speech the track plays undiminished as "
                                 "the lead."},
         "fade_in_s": {"type": "number"}, "fade_out_s": {"type": "number"},
         "offset_s": {"type": "number",
                      "description": "Seconds into the track to start. "
                                     "Omit to start where the track's body "
                                     "begins (skipping a quiet intro); 0 "
                                     "plays it from the very start."},
         "loop": {"type": "boolean"},
         "purpose": {"type": "string"}}),
}
