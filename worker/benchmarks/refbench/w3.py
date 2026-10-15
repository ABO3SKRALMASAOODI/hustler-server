"""Wave 3 refbench helpers: sidecars measured locally on VFL fixtures.

A fixture is its own source: its sha256 (vfl.json) is the source identity,
its absolute path the storage key a LocalStore stages directly, and the
sidecar clock is seconds from the file's start (what ``ffmpeg -ss`` on the
file reads; ``clock_offset`` maps the file's PTS clock onto it). Sidecars
land in ``VALMERA_SIDECAR_DIR`` (a LocalStore) and run in a child process
here, exactly as the batch lane runs them.

    python -m benchmarks.refbench w3 measure V12 gestures landmarks.hand
"""

import json
import os
import subprocess
import sys
import time

from . import vfl as vflmod


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate:format=duration,start_time",
         "-of", "json", path], capture_output=True, text=True, check=True)
    d = json.loads(out.stdout)
    st = d["streams"][0]
    num, den = (int(x) for x in st["avg_frame_rate"].split("/"))
    return {"width": int(st["width"]), "height": int(st["height"]),
            "fps": num / float(den or 1),
            "duration": float(d["format"]["duration"]),
            "start": float(d["format"].get("start_time") or 0.0)}


def source_ref(win, shots=()):
    """A launchable SourceRef for a VFL window's fixture file."""
    import sidecars
    info = probe(win["path"])
    return sidecars.source_from(win["sha256"], win["path"], info, None,
                                shots)


def index_for(win, shots=()):
    """An index naming the fixture as its source (video.sha256), so a render
    reads the fixture's sidecars (matte_track.read_source /
    renderer._fx_source)."""
    from . import engine
    idx = engine.index_for(win)
    idx["video"] = dict(idx["video"], sha256=win["sha256"])
    if shots:
        idx["shots"] = [{"start": float(s)} for s in shots]
    return idx


def measure(kind, win, ranges, params=None, timeout=1800, shots=()):
    """Run ``kind`` over ``ranges`` (SOURCE = file seconds) locally and wait;
    returns (handle, status, seconds)."""
    import sidecars
    if not os.environ.get("VALMERA_SIDECAR_DIR"):
        raise RuntimeError("set VALMERA_SIDECAR_DIR (a local sidecar store)")
    src = source_ref(win, shots)
    t = time.time()
    h = sidecars.ensure(kind, src, ranges, priority="interactive",
                        params=params)
    sidecars._launcher().join(timeout)
    return h, sidecars.status(h), round(time.time() - t, 1)


def fetch_json(kind, win, ranges, params=None, shots=()):
    """[(chunk, data)] of a JSON kind's ready chunks over ``ranges``."""
    import sidecars
    src = source_ref(win, shots)
    out = []
    for c, m in sidecars.ready_chunks(kind, src.sha256, src.shots,
                                      src.duration_s, ranges, params):
        out.append((c, sidecars.read_json_chunk(kind, src.sha256, m)))
    return out


def main(argv):
    if len(argv) >= 4 and argv[0] == "measure":
        win = vflmod.window(argv[1], argv[2])
        kind = argv[3]
        params = json.loads(argv[4]) if len(argv) > 4 else None
        info = probe(win["path"])
        h, st, secs = measure(kind, win, [(0.0, info["duration"])], params)
        print(json.dumps({"kind": kind, "status": st, "seconds": secs,
                          "chunks": len(h.chunks)}))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
