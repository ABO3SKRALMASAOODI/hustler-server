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
        try:
            storage.copy_object(track["storage_key"], key)
        except Exception as e:  # noqa: BLE001
            if _missing_object(e):
                return None, (
                    f"UNAVAILABLE: the audio for library track "
                    f"'{track['slug']}' is missing from storage on this "
                    "deployment, so nothing was placed. Pick another "
                    "track from list_music_library, or use the user's own "
                    "music. Do NOT claim music was added.")
            return None, (
                f"Could not prepare library track '{track['slug']}' "
                f"({str(e)[:160]}). Nothing was placed — try again once; "
                "do NOT claim music was added.")
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


def list_music_library(ctx, mood=None):
    rows = catalog()
    if not rows:
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
              "required. Place one with add_library_music(slug=...). "
              "Under a voice it sits as a ducked bed (about -18 to -22 dB); "
              "with no speech it plays as the lead.\n"
              "These moods are ALL the built-in music (no techno/EDM/rock/"
              "phonk). If the user asked for a genre outside them, say so, "
              "offer the closest mood, and offer to use their own track "
              "(upload or link).\n")
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
    return head + "\n".join(lines)


def add_library_music(ctx, slug, start=None, end=None, gain_db=None,
                      duck=None, fade_in_s=None, fade_out_s=None,
                      offset_s=None, loop=None, purpose=None):
    # duck=None lets add_music decide from the program: a bed ducks under
    # speech, while music that IS the audio (no speech) is never pumped by
    # the sidechain on every loud original sound.
    track, err = find(slug)
    if err:
        return err
    key, err = ensure_project_asset(ctx, track)
    if err:
        return err
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
    return res


TOOL_SPECS = {
    "list_music_library": (
        list_music_library,
        "READ: the built-in CC0 music library by mood, with exact slugs for "
        "add_library_music. Licence-verified background tracks (upbeat, "
        "chill, cinematic, corporate, dramatic, hiphop, ambient, inspiring), "
        "free for commercial use with no credit required; marks tracks this "
        "user already used in other projects.",
        {"mood": {"type": "string", "enum": list(MOODS),
                  "description": "Optional mood filter."}}),
    "add_library_music": (
        add_library_music,
        "Score the edit with a CC0 library track by exact slug (from "
        "list_music_library). No upload or download needed: the track is "
        "copied into this project once, registered with its licence, and "
        "placed like add_music — OUTPUT seconds, whole program by default, "
        "ducked under speech, looped to fill. Under a voice keep the bed "
        "around -18 to -22 dB (the default -18 dB is used when gain_db is "
        "omitted); with no speech it becomes the lead at -4 dB.",
        {"slug": {"type": "string"},
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
