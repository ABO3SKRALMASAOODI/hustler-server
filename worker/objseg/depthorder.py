"""In front of / behind the person, measured from the masks (track 3B).

An object definition's ``depth: auto`` is decided here from what the
sidecars already hold — no model, no depth network. The object's mask is
its VISIBLE pixels (the segmenter never sees what is hidden), so:

  * an object IN FRONT of the person shows inside the person's outline
    (the silhouette with its holes closed: a mic in front of a chest);
    whether the matte cut a hole for it or swallowed it as "person" (a
    matting model often does with held / attached things), most of its
    visible pixels sit inside the outline -> ``front``;
  * an object BEHIND the person shows only around the outline — at its
    edge, or through a gap (between an arm and the torso) the closed
    silhouette spans, where the matte does not claim it -> ``behind``;
  * no contact at all: the order cannot be seen (None — callers keep the
    authored occluders; an author says ``depth: behind`` for a background
    object the person never touches).

What it changes (world_layer): a graphic hidden behind an object that is
itself BEHIND the person is also hidden behind the person where they
overlap (the type is further back than both).
"""

import numpy as np

MIN_CONTACT = 0.04        # share of the object inside the silhouette
FRONT_CONTACT = 0.35      # this much inside the outline: in front
COVER_FRONT = 0.55        # the matte swallowed it (an attached thing)


def _closed(person, k):
    import cv2
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    filled = cv2.morphologyEx(person, cv2.MORPH_CLOSE, ker)
    # fill holes enclosed by the silhouette
    h, w = filled.shape
    flood = filled.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if flood[seed[1], seed[0]] == 0:
            cv2.floodFill(flood, mask, seed, 255)
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(filled, holes)


def order(obj_mask, person_mask):
    """('front' | 'behind' | None, detail) for one frame's masks (uint8,
    0..255, the same size)."""
    o = np.asarray(obj_mask) > 127
    p = (np.asarray(person_mask) > 127).astype(np.uint8) * 255
    n = int(o.sum())
    if n < 16:
        return None, {"object_px": n}
    h, w = p.shape
    sil = _closed(p, max(5, int(round(min(h, w) * 0.04)) | 1)) > 127
    contact = o & sil
    c = int(contact.sum())
    detail = {"object_px": n, "contact": round(c / float(n), 4)}
    if c < MIN_CONTACT * n:
        return None, detail
    covered = float((contact & (p > 127)).sum()) / c
    detail["covered"] = round(covered, 4)
    if c >= FRONT_CONTACT * n or covered >= COVER_FRONT:
        return "front", detail
    return "behind", detail


def vote(pairs):
    """The order over several frames' (obj_mask, person_mask) pairs: the
    majority of the frames that could see it; None when none could."""
    votes = {"front": 0, "behind": 0}
    details = []
    for om, pm in pairs:
        o, d = order(om, pm)
        details.append(d)
        if o:
            votes[o] += 1
    if not any(votes.values()):
        return None, details
    return max(votes, key=lambda k: (votes[k], k == "front")), details
