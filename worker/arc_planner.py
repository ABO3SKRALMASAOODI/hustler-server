"""Density arcs (Wave 2, track 2E).

A sequence can build by changing what the frame carries: words on black ->
words over picture -> picture only (refs 11 and 14 run it inside a few
seconds). ``density`` maps program slots to those three states from the
story's own energy — the section energies of a user track, or the speech's
phrase stress — so the arc follows the material instead of fixed thirds;
``roles`` hands montage.plan its per-slot roles.
"""

STATES = ("type_black", "type_over", "broll")


def _energy_at(sections, t):
    for s in sections or ():
        if float(s["t0"]) - 1e-6 <= t < float(s["t1"]):
            return float(s.get("energy", 0.5))
    return None


def density(slots, sections=None):
    """Per slot one of STATES: the lowest-energy third of the slots carry
    words on black, the middle words over picture, the highest picture
    only; without energies the thirds follow program order (the classic
    build). Monotonic: once the arc reaches picture it never steps back."""
    n = len(slots)
    if not n:
        return []
    en = [_energy_at(sections, s["start"]) for s in slots]
    if any(e is None for e in en) or len(set(en)) < 2:
        a, b = n // 3, (2 * n) // 3
        return [STATES[0] if k < a else STATES[1] if k < b else STATES[2]
                for k in range(n)]
    order = sorted(range(n), key=lambda k: (en[k], k))
    rank = {k: r for r, k in enumerate(order)}
    out = []
    level = 0
    for k in range(n):
        want = 0 if rank[k] < n / 3 else 1 if rank[k] < 2 * n / 3 else 2
        level = max(level, want)
        out.append(STATES[level])
    return out
