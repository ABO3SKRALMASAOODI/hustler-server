"""Footage that was found once is there the next time (lane K round seven, 14 Nov 2026).

The engine takes minutes for a film's subjects (the box, 14 Nov: 420 s for ten clips of four subjects) and a first
film has a deadline of 150 s, so in round six real footage reached one film of twelve, after the film was already
the customer's. The clips it cuts are checked pictures of a named subject in the film's shape: the next film that
names that subject wants the same clips, and so does this film written again.

One shelf in object storage, by subject and frame shape:

    footage-cache/v1/<subject>/<W>x<H>/index.json     {"name", "clips": [{file, thumb, shows, seconds, source, ...}]}
    footage-cache/v1/<subject>/<W>x<H>/<clip>.mp4     the clip as the engine cut it
    footage-cache/v1/<subject>/<W>x<H>/<clip>.jpg     one frame of it, for the Bin's board

get() answers in the Bin's candidate shape (as footage.seam._offers does), with `cache_key` in place of a local
file; footage.seam.acquire copies the object. put() shelves what an engine run cut. Nothing here is on a customer's
path unless the footage switch opened it (the callers check), every clip keeps its source and credit, and neither
call raises: a shelf that cannot be read is an empty shelf. VALMERA_FOOTAGE_CACHE=0 turns it off.
"""
import hashlib
import json
import os
import re
import tempfile
import time

PREFIX = "footage-cache/v1/"
MOST = 6                   # clips kept on a subject's shelf, and offered from it
MAX_AGE_S = 90 * 86400.0   # a shelf older than this is read as empty: sources go, and a subject's pictures date


def on():
    return os.environ.get("VALMERA_FOOTAGE_CACHE", "1") != "0"


def shelf(name, W, H):
    """The storage prefix of one subject in one frame shape."""
    folded = " ".join(str(name or "").casefold().split())
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:48] or "x"
    return f"{PREFIX}{slug}-{hashlib.sha1(folded.encode('utf-8')).hexdigest()[:8]}/{int(W)}x{int(H)}/"


def _read(key):
    import storage
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(key)[1] or ".bin")
    os.close(fd)
    try:
        if not storage.exists(key):
            return None
        storage.download_to(key, path, check_capacity=False)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def get(name, W=1080, H=1920, most=MOST):
    """A subject's shelved clips as Bin candidates, [] when there are none. One small read for the index and one a
    clip for its frame; the clips themselves stay in storage until one is acquired."""
    if not on() or not name:
        return []
    try:
        import io
        from PIL import Image
        from . import seam
        base = shelf(name, W, H)
        raw = _read(base + "index.json")
        doc = json.loads(raw.decode("utf-8")) if raw else None
        if not isinstance(doc, dict) or time.time() - float(doc.get("at") or 0.0) > MAX_AGE_S:
            return []
        out = []
        for c in (doc.get("clips") or [])[:most]:
            img = _read(base + str(c["thumb"]))
            if not img:
                continue
            src = c.get("source") or {}
            out.append(dict(id=f"footage:{c['file']}", provider=seam.PROVIDER, kind="video", named=doc.get("name") or name, shows=c.get("shows"),
                            cache_key=base + str(c["file"]), width=int(W), height=int(H), duration_s=float(c.get("seconds") or 0.0), license=seam.LICENCE,
                            credit=src.get("channel"), page_url=src.get("url"), title=src.get("title"), _thumb=None,
                            _img=Image.open(io.BytesIO(img)).convert("RGB"),
                            footage=dict(url=src.get("url"), time_range=c.get("time_range"), x=c.get("x"), eq=c.get("eq"), trim=c.get("trim"))))
        return out
    except Exception as e:  # noqa: BLE001 - an unreadable shelf is an empty one
        print(f"[footage] cache read for {str(name)[:40]!r}: {type(e).__name__}: {str(e)[:120]}", flush=True)
        return []


def put(name, offers, W=1080, H=1920, most=MOST):
    """Shelve an engine run's clips of one subject (the offers of footage.seam._offers, with their local files and
    frames). The index is written last, so a shelf is never read half full. Returns how many were shelved."""
    if not on() or not name or not offers:
        return 0
    try:
        import storage
        base = shelf(name, W, H)
        rows = []
        with tempfile.TemporaryDirectory() as tmp:
            for o in offers[:most]:
                path = o.get("local_file")
                if not path or not os.path.exists(path) or o.get("_img") is None:
                    continue
                stem = hashlib.sha1((str((o.get("footage") or {}).get("url")) + str((o.get("footage") or {}).get("time_range"))).encode()).hexdigest()[:12]
                thumb = os.path.join(tmp, stem + ".jpg")
                o["_img"].convert("RGB").save(thumb, quality=88)
                storage.upload_file(path, base + stem + ".mp4", "video/mp4")
                storage.upload_file(thumb, base + stem + ".jpg", "image/jpeg")
                f = o.get("footage") or {}
                rows.append(dict(file=stem + ".mp4", thumb=stem + ".jpg", shows=o.get("shows"), seconds=o.get("duration_s"),
                                 source=dict(url=o.get("page_url"), title=o.get("title"), channel=o.get("credit")),
                                 time_range=f.get("time_range"), x=f.get("x"), eq=f.get("eq"), trim=f.get("trim")))
            if not rows:
                return 0
            idx = os.path.join(tmp, "index.json")
            with open(idx, "w", encoding="utf-8") as fh:
                json.dump({"name": name, "at": time.time(), "frame": [int(W), int(H)], "clips": rows}, fh, ensure_ascii=False)
            storage.upload_file(idx, base + "index.json", "application/json")
        return len(rows)
    except Exception as e:  # noqa: BLE001 - the film has its clips; only the shelf is missed
        print(f"[footage] cache write for {str(name)[:40]!r}: {type(e).__name__}: {str(e)[:120]}", flush=True)
        return 0
