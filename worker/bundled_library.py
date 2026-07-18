"""Shared machinery for media BUNDLED IN THE WORKER IMAGE (music, sfx).

Extracted when the sfx pack landed. The alternative was a second copy of the
resolution logic, and the resolution logic is the security boundary: a fix
applied to one copy and not the other is exactly how a whitelist quietly
becomes a prefix match.

Two design decisions carried over from the music library:

1. Assets are BUNDLED (worker/music/, worker/sfx/), not stored in R2. The
   Dockerfile's `COPY . .` ships them with the code, so a deployed worker can
   never be in the state "code knows about a track that isn't there". It also
   costs zero download per render.

2. An asset is referenced as `<scheme>:<slug>` — deliberately NOT a path-shaped
   storage_key. An EDL must not claim a file lives in object storage when it
   does not, and the scheme prefix makes every call site branch honestly
   instead of guessing from the shape of a string.

SECURITY — read before loosening anything here. renderer._fetch() downloads
whatever key it is handed, with no project scoping. The ONLY thing stopping a
music/sfx item from naming another customer's video is that the add_* tools
refuse keys that aren't project-owned assets. So resolution is a WHITELIST
lookup against the catalog (resolve() returns None for anything not in it),
never a prefix match like key.startswith(scheme). A prefix test would turn
this module into a read primitive over the whole bucket.

Every bundled asset is CC0 / public domain: no attribution obligation, safe to
redistribute inside a customer's exported video. Each pack directory carries a
LICENSES.md with the per-asset paper trail. Anything whose licence could not be
verified was not shipped.
"""

import json
import os


class Library:
    """One bundled pack: a directory, a manifest, and a reference scheme."""

    def __init__(self, scheme, dirname, group_field, groups):
        self.scheme = scheme + ":"
        self.dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                dirname)
        self.group_field = group_field       # "mood" for music, "category" for sfx
        self.groups = tuple(groups)
        self.catalog = self._load()

    def _load(self):
        """Load the shipped manifest. A missing/broken manifest leaves the
        catalog empty, which makes the library tools say honestly that nothing
        is available — never a crash, and never a phantom entry the renderer
        would then fail to find."""
        try:
            with open(os.path.join(self.dir, "manifest.json")) as f:
                raw = json.load(f)
        except Exception:
            return []
        out = []
        for t in raw if isinstance(raw, list) else []:
            if not isinstance(t, dict):
                continue
            slug, fname = t.get("slug"), t.get("file")
            if not slug or not fname:
                continue
            # Only advertise an entry whose audio is actually present in the
            # image. Advertising one that isn't would let the agent add a sound
            # the renderer then cannot open — a failure the user sees as a
            # broken export, long after the agent said it worked.
            if not os.path.exists(os.path.join(self.dir, fname)):
                continue
            out.append(t)
        return out

    def ref(self, slug):
        return f"{self.scheme}{slug}"

    def is_ref(self, key):
        return isinstance(key, str) and key.startswith(self.scheme)

    def resolve(self, key):
        """Catalog entry for a `<scheme>:<slug>` reference, or None.

        WHITELIST lookup — the slug must be in the catalog. Returning an entry
        for an arbitrary string would let a crafted reference reach the
        filesystem."""
        if not self.is_ref(key):
            return None
        slug = key[len(self.scheme):]
        for t in self.catalog:
            if t.get("slug") == slug:
                return t
        return None

    def local_path(self, key):
        """Absolute path to a bundled asset, or None if it isn't one.

        Built from the catalog entry's filename, never from the caller's
        string, so `<scheme>:../../etc/passwd` resolves to None at the lookup
        above rather than escaping the pack directory here."""
        t = self.resolve(key)
        return os.path.join(self.dir, t["file"]) if t else None

    def duration_of(self, key):
        t = self.resolve(key)
        return t.get("duration_s") if t else None

    def browse(self, group=None):
        """Catalog entries, optionally filtered to one mood/category."""
        if not group:
            return list(self.catalog)
        g = str(group).strip().lower()
        return [t for t in self.catalog if t.get(self.group_field) == g]
