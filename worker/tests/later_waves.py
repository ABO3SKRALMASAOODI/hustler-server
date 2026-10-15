"""Later-wave growth the Wave 0/1 seam tests set aside.

INTERFACES_W4.md §8.2: each wave after Wave 1 declares what it adds to the
pinned registries in its own file, ``tests/fixtures/waves/w<N>.json``
(``fx_tags``, ``verbs``, ``skills``, ``departments``, ``card_prefixes``,
``sidecar_kinds``, ``templates``, ``models``, ``slots``, and
``core_growth_bytes``: catalog growth beyond names joining enums). The
engines release integration declared Waves 2 and 3 the same way
(``w2.json``, ``w3.json``). The seam tests that pin "nothing outside
the Wave 1 table" subtract exactly that union, so parallel waves each drop
their own file and never edit the same pin.
"""

import glob
import json
import os

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                    "waves")
KEYS = ("fx_tags", "verbs", "skills", "departments", "card_prefixes",
        "sidecar_kinds", "templates", "models", "slots")


def growth():
    """{key: set} — the union of every later wave's declared growth."""
    out = {k: set() for k in KEYS}
    for path in sorted(glob.glob(os.path.join(_DIR, "w*.json"))):
        with open(path, encoding="utf-8") as f:
            pin = json.load(f)
        for k in KEYS:
            out[k] |= set(pin.get(k) or ())
    return out


def get(key):
    return growth()[key]


def core_growth_bytes():
    """The core-catalog bytes later waves pin beyond names joining enums
    (Wave 2: the B&W look line and its department/skill share)."""
    total = 0
    for path in sorted(glob.glob(os.path.join(_DIR, "w*.json"))):
        with open(path, encoding="utf-8") as f:
            total += int(json.load(f).get("core_growth_bytes") or 0)
    return total
