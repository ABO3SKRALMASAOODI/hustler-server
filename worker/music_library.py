"""The built-in CC0 music library.

Before this existed the agent could only use music the USER had uploaded, so
"add some music" answered with "please attach a file" — the single most common
thing people asked for and the one thing we could not do.

Two design decisions worth keeping:

1. Tracks are BUNDLED IN THE WORKER IMAGE (worker/music/), not stored in R2.
   The Dockerfile's `COPY . .` ships them with the code, so a deployed worker
   can never be in the state "code knows about a track that isn't there". It
   also costs zero download per render. The image already carries a ~1.5GB
   whisper model; the audio is noise next to it.

2. A library track is referenced as `library:<slug>` — deliberately NOT a
   path-shaped storage_key. An EDL must not claim a file lives in object
   storage when it does not, and the scheme prefix makes every call site
   branch honestly instead of guessing from the shape of a string.

SECURITY — read before loosening anything here. renderer._fetch() downloads
whatever key it is handed, with no project scoping. The ONLY thing stopping a
music item from naming another customer's video is that add_music refuses keys
that aren't project-owned assets. So library resolution is a WHITELIST lookup
against CATALOG (resolve() returns None for anything not in it), never a
prefix match like key.startswith("library"). A prefix test would turn this
module into a read primitive over the whole bucket.

Every track is CC0 / public domain: no attribution obligation, safe to
redistribute inside a customer's exported video. LICENSES.md in worker/music/
carries the per-track paper trail (source URL, author, license as stated).
Anything whose license could not be verified was not shipped.
"""

import os

# Reference scheme. `library:upbeat-sunrise-drive` -> worker/music/<file>.
SCHEME = "library:"

MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")

# The moods the agent picks from. Kept short and plain-language on purpose:
# these are the words users actually say ("something chill", "make it epic"),
# not genre taxonomy.
MOODS = ("upbeat", "chill", "cinematic", "corporate",
         "dramatic", "hiphop", "ambient", "inspiring")

# Populated from worker/music/manifest.json at import. Each entry:
#   slug, title, mood, duration_s, file, license, source_url, author,
#   bpm (None unless the source stated it — never estimated).
CATALOG = []


def _load():
    """Load the shipped manifest. A missing/broken manifest leaves CATALOG
    empty, which makes the library tools say honestly that no tracks are
    available — never a crash, and never a phantom track the render would
    then fail to find."""
    import json
    path = os.path.join(MUSIC_DIR, "manifest.json")
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return []
    out = []
    for t in raw if isinstance(raw, list) else []:
        slug, fname = t.get("slug"), t.get("file")
        if not slug or not fname:
            continue
        # Only advertise a track whose audio is actually present in the image.
        # Advertising one that isn't would let the agent add music that the
        # renderer then cannot open — a failure the user would see as a
        # broken export, long after the agent said it worked.
        if not os.path.exists(os.path.join(MUSIC_DIR, fname)):
            continue
        out.append(t)
    return out


CATALOG = _load()


def ref(slug):
    """The EDL reference for a slug."""
    return f"{SCHEME}{slug}"


def is_library_ref(key):
    return isinstance(key, str) and key.startswith(SCHEME)


def resolve(key):
    """Catalog entry for a `library:<slug>` reference, or None.

    WHITELIST lookup — the slug must be in CATALOG. Returning an entry for an
    arbitrary string would let a crafted reference reach the filesystem."""
    if not is_library_ref(key):
        return None
    slug = key[len(SCHEME):]
    for t in CATALOG:
        if t["slug"] == slug:
            return t
    return None


def local_path(key):
    """Absolute path to a library track's audio, or None if it isn't one.

    Built from the CATALOG entry's filename, never from the caller's string,
    so `library:../../etc/passwd` resolves to None at the lookup above rather
    than escaping MUSIC_DIR here."""
    t = resolve(key)
    if not t:
        return None
    return os.path.join(MUSIC_DIR, t["file"])


def duration_of(key):
    t = resolve(key)
    return t.get("duration_s") if t else None


def browse(mood=None):
    """Catalog entries, optionally filtered to one mood."""
    if not mood:
        return list(CATALOG)
    m = str(mood).strip().lower()
    return [t for t in CATALOG if t.get("mood") == m]


def describe(t):
    """One line per track, as the agent sees it."""
    # No slug here — callers print the library:<slug> reference already, and
    # repeating it just makes the agent's catalogue harder to read.
    bits = [f'"{t["title"]}"', t.get("mood", "?")]
    if t.get("duration_s"):
        bits.append(f"{t['duration_s']:.0f}s")
    if t.get("bpm"):
        bits.append(f"{t['bpm']} BPM")
    return ", ".join(bits)
