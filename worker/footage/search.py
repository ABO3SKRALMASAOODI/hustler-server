"""Where real footage lives: YouTube (interviews, launches, documentaries, news channels) through
yt-dlp's own search, and archive.org's moving images (newsreels, public-domain and government film)."""
import json
import os
import subprocess

from . import fetch

IA_SEARCH = "https://archive.org/advancedsearch.php"
MAX_SOURCE_S = 3600          # a source longer than an hour is a stream or a compilation, rarely the moment


def youtube(q, n=8, timeout_s=40):
    """[{url, id, title, channel, duration, views, where}] for one search, newest yt-dlp flat listing."""
    cmd = fetch.ytdlp_cmd() + ["--flat-playlist", "-J", f"ytsearch{int(n)}:{q}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=fetch.env())
        d = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, ValueError):
        return []
    out = []
    for e in d.get("entries") or []:
        dur = e.get("duration")
        if not e.get("id") or (dur and dur > MAX_SOURCE_S) or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if "/shorts/" in str(e.get("url") or ""):
            dur = dur or 60
        out.append(dict(url=f"https://www.youtube.com/watch?v={e['id']}", id=e["id"], title=str(e.get("title") or "")[:160],
                        channel=e.get("channel") or e.get("uploader"), duration=dur, views=e.get("view_count"), where="youtube", q=q))
    return out


def archive(q, n=6, timeout_s=20):
    """archive.org moving images for one search: [{url, id, title, channel, duration, where}]."""
    import net_fetch
    params = {"q": f"({q}) AND mediatype:(movies)", "fl[]": ["identifier", "title", "creator", "runtime", "downloads"],
              "rows": str(n), "output": "json", "sort[]": "downloads desc"}
    try:
        docs = net_fetch.get_json(IA_SEARCH, params=params, timeout_s=timeout_s).get("response", {}).get("docs") or []
    except Exception:  # noqa: BLE001 the library is down: YouTube still answers
        return []
    out = []
    for dd in docs:
        ident = dd.get("identifier")
        if not ident:
            continue
        out.append(dict(url=f"https://archive.org/details/{ident}", id=f"ia:{ident}", title=str(dd.get("title") or ident)[:160],
                        channel=(dd.get("creator") if isinstance(dd.get("creator"), str) else "archive.org") or "archive.org",
                        duration=_runtime(dd.get("runtime")), views=dd.get("downloads"), where="archive", q=q))
    return out


def _runtime(v):
    """archive.org writes runtime as seconds, "mm:ss" or "hh:mm:ss"; None when it is anything else."""
    try:
        parts = [float(p) for p in str(v).split(":")]
    except (TypeError, ValueError):
        return None
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s or None


def run(searches, workers=6):
    """All searches at once: {search index: [hits]}."""
    import concurrent.futures as cf
    def one(s):
        return archive(s["q"]) if s.get("where") == "archive" else youtube(s["q"], n=s.get("n", 8))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(enumerate(pool.map(one, searches)))


if __name__ == "__main__":
    import sys
    print(json.dumps(youtube(sys.argv[1], 5) if len(sys.argv) < 3 else archive(sys.argv[1]), indent=1)[:3000])
    os._exit(0)
