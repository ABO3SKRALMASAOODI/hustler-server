"""Pure Director measurements, overlapped with ASR in the existing index.

The cache contains the identical perception and colour samples, not a
creative decision. Source bytes, screen clearance, spatial evidence and
measurement code all participate in its identity. No separate cache lives
on disk: scratch belongs to the index/film job and is removed with it.
"""
import base64
import copy
import functools
import hashlib
import io
import json
import os
import zlib


@functools.lru_cache(maxsize=1)
def _code_key():
    import cv2
    import subject
    from . import grade, perceive, picture_cuts, source_audio
    digest = hashlib.sha256(cv2.__version__.encode())
    for module in (grade, perceive, picture_cuts, source_audio, subject):
        with open(module.__file__, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()


def _identity(index, src):
    # Only these inputs are read by picture_cuts/refine and perceive/run.
    data = {k: index.get(k) for k in
            ("video", "shots", "spatial", "source_screen")}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode())
    digest.update(_code_key().encode())
    with open(src, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(index, src, work):
    from . import perceive, picture_cuts
    import source_screen
    bounded = source_screen.first_film_index(index)
    os.makedirs(work, exist_ok=True)
    refined = picture_cuts.refine(bounded, src)
    per = perceive.run(refined, src, work)
    with open(os.path.join(work, "colour.npy"), "rb") as f:
        colour = f.read()
    # The normal twelve tiny frames fit comfortably; never bloat an index
    # for an unusual aspect ratio. A miss uses the ordinary Director path.
    if len(colour) > 2 * 1024 * 1024:
        return None
    return dict(version=1, identity=_identity(bounded, src),
                shots=refined["shots"],
                cut_measure=refined.get("director_cut_measure"),
                perception=per,
                colour=base64.b64encode(zlib.compress(colour)).decode())


def restore(index, src, work):
    """Return (refined index, perception), or None for any stale/failed cache."""
    cache = index.get("director_preparation") or {}
    if not index.get("_first_film_source_guard") or cache.get("version") != 1:
        return None
    try:
        if cache.get("identity") != _identity(index, src):
            return None
        import numpy as np
        colour = zlib.decompress(base64.b64decode(cache["colour"]))
        arr = np.load(io.BytesIO(colour), allow_pickle=False)
        if arr.dtype != np.uint8 or arr.ndim != 4 or arr.shape[-1] != 3:
            return None
        local = copy.deepcopy(index)
        local["shots"] = copy.deepcopy(cache["shots"])
        local["director_cut_measure"] = copy.deepcopy(cache["cut_measure"])
        per = copy.deepcopy(cache["perception"])
        os.makedirs(work, exist_ok=True)
        np.save(os.path.join(work, "colour.npy"), arr, allow_pickle=False)
        with open(os.path.join(work, "perception.json"), "w") as f:
            json.dump(per, f, ensure_ascii=False)
        return local, per
    except (OSError, ValueError, KeyError, TypeError, zlib.error):
        return None
