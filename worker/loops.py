"""Loops and bookends (Wave 2, track 2E).

A short that ends where it began loops on a feed: the last frame before the
end card rhymes with the first. ``plan`` looks for the shot in the library
(the main video or the project's clips) whose picture best matches the
opening frame, away from the opening itself, and returns an insert that
closes the program on it — placed BEFORE the end card (the end card and the
watermark are appended by the renderer after every creative stage, so the
rhyme can never touch them).
"""

import numpy as np

MIN_RHYME = 0.85
REPRISE_S = (0.6, 1.4)


def _cos(a, b):
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    if a.shape != b.shape or not a.size:
        return -1.0
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def rhyme(first_desc, entries, exclude=None, min_sim=MIN_RHYME):
    """[(similarity, entry)] best first: shots that rhyme with the opening
    (classic descriptor cosine; ``exclude`` = (key, t0, t1) of the opening
    shot's own window)."""
    out = []
    for e in entries:
        if exclude and e.get("key") == exclude[0] and \
                e.get("t0", 0) < exclude[2] and e.get("t1", 0) > exclude[1]:
            continue
        s = _cos(first_desc.get("vec"), (e.get("desc") or {}).get("vec"))
        if s >= min_sim:
            out.append((round(s, 4), e))
    return sorted(out, key=lambda x: -x[0])


def reprise_insert(entry, program_end, keep_end_boundary, insert_id="loop",
                   dur=0.9):
    """The insert that closes the program on ``entry`` (a clip window): at
    the program's last keep boundary, ``dur`` seconds from the shot's
    middle."""
    a, b = float(entry.get("t0", 0)), float(entry.get("t1", 0))
    d = max(REPRISE_S[0], min(REPRISE_S[1], dur, max(0.2, b - a - 0.1)))
    ss = max(a, min(b - d, float(entry.get("mid", a)) - d / 2))
    return {"id": insert_id, "asset_key": entry["key"], "kind": "video",
            "at_output_s": round(float(keep_end_boundary), 2),
            "duration_s": round(d, 2), "source_start_s": round(ss, 2)}


def bookend_score(first_rgb, last_rgb):
    """The classic cosine between two rendered frames (the AT's measure)."""
    import library
    return _cos(library.describe(first_rgb)["vec"],
                library.describe(last_rgb)["vec"])
