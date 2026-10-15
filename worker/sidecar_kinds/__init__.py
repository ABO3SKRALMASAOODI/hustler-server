"""Sidecar kinds by file drop (Wave 3 seam; devices/INTERFACES_W3.md §1).

``sidecars.py`` ships the two Wave 0 kinds in its own table. Every later
kind is a module here (not starting with ``_``) exporting::

    KIND_SPECS = {"<reserved kind>": {
        "model": "<models/<name>.json stem>" | None,
        "model_id": str, "model_version": str,   # the cache key's identity
        "format": "...", "formats": (...), "ext": "...", "content_type": "...",
        "fallback_models": (), "defaults": {...}, "rate": "<param>",
        "ms_per_frame": float, "group_s": float,
        "child": "<module>:<function>",           # child-process runner
        "models": ("<models/*.json stem>", ...),  # pinned files it reads
    }}

``sidecars.py`` merges the specs into its kind table at import (reserved
names only, never replacing one); ``analysis_jobs.py`` asks ``serves(kind)``
for the engine and ``child(kind)`` for the runner. A module that fails to
import registers nothing: its kinds read ``absent`` everywhere, so a lane
without them degrades a device, never a render.

Light at import: the agent, MCP and Shorts lanes import sidecars.py, so a
kind module imports nothing heavier than the standard library at module
level (models and OpenCV load inside the child).
"""

import hashlib
import importlib
import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.dirname(_DIR)
_MODULES = None
_SHA_CACHE = {}


def _modules():
    global _MODULES
    if _MODULES is None:
        mods = []
        for fname in sorted(os.listdir(_DIR)):
            if fname.endswith(".py") and not fname.startswith("_"):
                try:
                    mods.append(importlib.import_module(
                        f"{__name__}.{fname[:-3]}"))
                except Exception as e:  # noqa: BLE001 — that module only
                    print(f"[sidecar_kinds] {fname} not loaded: "
                          f"{type(e).__name__}: {str(e)[:160]}", flush=True)
        _MODULES = mods
    return _MODULES


def kind_specs():
    """{kind: spec} over every module here."""
    out = {}
    for mod in _modules():
        for kind, sp in (getattr(mod, "KIND_SPECS", None) or {}).items():
            if kind not in out:
                out[kind] = dict(sp)
    return out


def serves(kind):
    """The module that produces ``kind``, or None."""
    for mod in _modules():
        if kind in (getattr(mod, "KIND_SPECS", None) or {}):
            return mod
    return None


def child(kind):
    """The child-process runner ``fn(spec, chunk) -> (path, meta)``."""
    mod = serves(kind)
    if mod is None:
        raise KeyError(f"no sidecar_kinds module produces {kind}")
    target = mod.KIND_SPECS[kind]["child"]
    mod_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(mod_name), attr)


# ── pinned model files (shared by every kind module) ────────────────────

def models_dir():
    """Where the baked model files live: ``VALMERA_MODELS_DIR`` (dev,
    tests, refbench) or the image's ``worker/models`` (models/bake.py
    --dest /app/models)."""
    return (os.environ.get("VALMERA_MODELS_DIR", "").strip()
            or os.path.join(_WORKER, "models"))


def model_record(name):
    """models/<name>.json (the pinned record)."""
    with open(os.path.join(_WORKER, "models", f"{name}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _file_sha(path):
    st = os.stat(path)
    tag = (path, st.st_size, int(st.st_mtime))
    hit = _SHA_CACHE.get(tag)
    if hit is None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        hit = _SHA_CACHE[tag] = h.hexdigest()
    return hit


def model_file(name):
    """(path | None, why): the pinned file of models/<name>.json, present on
    this lane and byte-identical to its pin."""
    try:
        rec = model_record(name)
    except (OSError, ValueError) as e:
        return None, f"model record {name} unreadable ({e})"
    path = os.path.join(models_dir(), rec["file"])
    if not os.path.exists(path):
        return None, f"{rec['file']} is not on this lane"
    try:
        if os.path.getsize(path) != int(rec["bytes"]) \
                or _file_sha(path) != rec["sha256"]:
            return None, f"{rec['file']} does not match its models.lock pin"
    except OSError as e:
        return None, f"{rec['file']} unreadable: {e}"
    return path, None


def identity(models, code_version):
    """(model_id, model_version) for a kind measured with ``models`` (the
    models/*.json stems it reads) by code at ``code_version``: the pins'
    sha256 prefixes and the code version, so a new pin or a new algorithm
    never reads an old chunk as its own (the version handshake). A record
    that cannot be read contributes its name only."""
    ids, vers = [], []
    for name in models:
        try:
            rec = model_record(name)
            ids.append(str(rec.get("model_id") or name))
            vers.append(str(rec.get("sha256") or "")[:12])
        except (OSError, ValueError):
            ids.append(name)
            vers.append("unpinned")
    return ("+".join(ids) or "opencv", "+".join(vers + [str(code_version)]))


def engine_for(kind, faultinject, spec):
    """The engine dict analysis_jobs hands its child: every pinned file the
    kind reads, verified, or None (model_missing) when one is absent."""
    if faultinject.fault(f"model_missing:{kind}"):
        return None
    paths = {}
    for name in spec.get("models") or ():
        path, _why = model_file(name)
        if not path:
            return None
        paths[name] = path
    try:
        import config
        threads = int(getattr(config, "SIDECAR_ORT_THREADS", 0) or 0)
    except Exception:  # noqa: BLE001
        threads = 0
    return {"name": kind, "model_id": spec["model_id"],
            "model_version": spec["model_version"],
            "path": next(iter(paths.values()), None), "paths": paths,
            "threads": threads, "fallback": False}
