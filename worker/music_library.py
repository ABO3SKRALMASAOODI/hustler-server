"""The built-in CC0 music library.

Before this existed the agent could only use music the USER had uploaded, so
"add some music" answered with "please attach a file" — the single most common
thing people asked for and the one thing we could not do.

The resolution machinery (whitelist lookup, manifest loading, the reasons both
are shaped the way they are) now lives in bundled_library.py, shared with the
sfx pack. Read that module's docstring before changing anything here; the
security rationale is there. This module is the music-specific surface: the
mood vocabulary and the one-line description the agent reads.

Every track is CC0 / public domain: no attribution obligation, safe to
redistribute inside a customer's exported video. LICENSES.md in worker/music/
carries the per-track paper trail (source URL, author, license as stated).
Anything whose license could not be verified was not shipped.
"""

import bundled_library

# The moods the agent picks from. Kept short and plain-language on purpose:
# these are the words users actually say ("something chill", "make it epic"),
# not genre taxonomy.
MOODS = ("upbeat", "chill", "cinematic", "corporate",
         "dramatic", "hiphop", "ambient", "inspiring")

_LIB = bundled_library.Library("library", "music", "mood", MOODS)

# Preserved module-level surface — every call site and test refers to these.
SCHEME = _LIB.scheme
MUSIC_DIR = _LIB.dir
# Each entry: slug, title, mood, duration_s, file, license, source_url,
# author, bpm (None unless the source stated it — never estimated).
CATALOG = _LIB.catalog

ref = _LIB.ref
is_library_ref = _LIB.is_ref
resolve = _LIB.resolve
local_path = _LIB.local_path
duration_of = _LIB.duration_of
browse = _LIB.browse


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
