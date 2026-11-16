"""find(): media from outside the project, many queries per call
(HARNESS_V2.md 2.6).

Per query: ids, licence, duration, a description that is never empty, and a
board image with the ids printed on the tiles that goes to the caller's own
eyes. Descriptions come from the library's own text, then the vision chain
(mcp_search.describe_missing), then "undescribed: see the board". Every hit
is remembered with its query group, so ``place`` takes the exact item seen
and a failed download takes the next result of the same search. Music comes
only from the reference-grade set (or the library tracks the signal gate
passed), so the owner's music policy holds without a refusal.
"""
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import agent_tools
import llm

from harness2 import envelope

KINDS = ("stock_video", "stock_photo", "web_image", "footage", "song",
         "music", "sfx")
MAX_QUERIES = 40
UNDESCRIBED = "undescribed: see the board"
LIB_PREFIX = "lib:"
_LOCK = threading.Lock()


def carry(fn):
    """fn wrapped to run in a pool thread with this thread's model recorder
    and turn plan (both thread-local), so metering and routing hold."""
    recorder = llm.get_recorder()
    plan = llm.turn_plan()
    lane = llm.turn_lane()
    cache_key = getattr(llm._turn, "cache_key", None)

    def run(*a, **k):
        llm.set_recorder(recorder)
        llm.set_turn_plan(plan, None, lane=lane)
        llm._turn.cache_key = cache_key
        try:
            return fn(*a, **k)
        finally:
            llm.set_recorder(None)
            llm.clear_turn_plan()
    return run


def _queries(query):
    rows = query if isinstance(query, (list, tuple)) else [query]
    out = []
    for q in rows:
        q = str(q or "").strip()
        if q and q not in out:
            out.append(q[:200])
    return out[:MAX_QUERIES]


def _board(ctx, hits, label):
    """One labelled board of thumbnails (ids on the tiles), or None."""
    import net_fetch
    import sheets
    rows = [h for h in hits if h.get("_thumb")]

    def fetch(h):
        local = os.path.join(ctx.workdir, f"find_{uuid.uuid4().hex[:8]}.jpg")
        try:
            net_fetch.download(h["_thumb"], local, max_bytes=3 * 1024 * 1024,
                               timeout_s=15)
            return h, local
        except Exception:  # noqa: BLE001
            return h, None
    frames = []
    with ThreadPoolExecutor(max_workers=min(8, len(rows) or 1)) as pool:
        for h, local in pool.map(fetch, rows):
            if local:
                h["_thumbnail_delivered"] = True
                frames.append((str(h.get("id")), local))
    if not frames:
        return None
    path = os.path.join(ctx.workdir, f"board_{uuid.uuid4().hex[:8]}.jpg")
    try:
        sheets.build_timestamp_sheet(frames, path)
    except Exception:  # noqa: BLE001
        return None
    return (f"board: {label}", path)


def _describe(ctx, hits):
    """Never empty: library text, the vision chain, or UNDESCRIBED."""
    import mcp_search
    try:
        hits, _n = mcp_search.describe_missing(ctx, hits)
    except Exception as exc:  # noqa: BLE001 - the chain's last rung
        print(f"[harness2] describe_missing failed: {exc}", flush=True)
    for h in hits:
        if not str(h.get("description") or "").strip():
            title = str(h.get("title") or "").strip()
            h["description"] = title or UNDESCRIBED
            h["unverified"] = True
    return hits


def _line(h):
    bits = [str(h.get("id"))]
    dur = h.get("duration_s") or h.get("duration")
    if dur:
        try:
            bits.append(f"{float(dur):.1f}s")
        except (TypeError, ValueError):
            pass
    lic = h.get("license") or h.get("licence")
    if lic:
        bits.append(str(lic)[:40])
    size = (f"{h.get('width')}x{h.get('height')}"
            if h.get("width") and h.get("height") else "")
    if size:
        bits.append(size)
    desc = str(h.get("description") or UNDESCRIBED)[:220]
    return " | ".join(bits) + f" | {desc}"


def _stock(ctx, q, kind, orientation, count):
    import stock
    if orientation is None:
        orientation = agent_tools._project_frame(ctx)[0]
    hits = stock.search(q, kind=kind, orientation=orientation, count=count)
    return hits


def _remember(ctx, hits):
    ids = [h["id"] for h in hits if h.get("id")]
    for h in hits:
        h["_v2_group"] = ids
        ctx.stock_results[h["id"]] = h
    agent_tools._remember_search_hits(ctx, "stock", hits, limit=512)


def _search_one(ctx, kind, q, count, orientation, max_s):
    """(lines, hits, error) for one query."""
    if kind in ("stock_video", "stock_photo"):
        import stock
        hits = _stock(ctx, q, stock.KIND_VIDEO if kind == "stock_video"
                      else stock.KIND_PHOTO, orientation, count)
        if max_s:
            hits = [h for h in hits if not h.get("duration_s")
                    or float(h["duration_s"]) <= float(max_s)] or hits
        hits = _describe(ctx, hits)
        return hits
    raise ValueError(kind)


def _text_kind(ctx, kind, q, count):
    """Kinds whose engines answer in text (footage, songs, web images,
    sound effects): their own result lines."""
    if kind == "footage":
        return str(agent_tools.find_footage(ctx, q))
    if kind == "song":
        return str(agent_tools.find_song(ctx, q))
    if kind == "web_image":
        return str(agent_tools.research_broll(
            ctx, [{"id": "m1", "query": q, "purpose": q}]))
    if kind == "sfx":
        return str(agent_tools.execute(ctx, "list_sound_library",
                                       {"role": q}))
    return ""


def find(ctx, kind="stock_video", query="", count=6, orientation=None,
         max_s=None):
    kind = str(kind or "stock_video").strip()
    if kind not in KINDS:
        kind = "stock_video"
    queries = _queries(query)
    if kind == "music":
        return _music(ctx, queries, count)
    if not queries:
        return envelope.failed("find", "find needs query (a phrase or a "
                               "list of phrases)", "query=['busy street', "
                               "'sunset beach']")
    try:
        count = max(1, min(12, int(count or 6)))
    except (TypeError, ValueError):
        count = 6
    env = envelope.Envelope(tool="find")
    body = []
    if kind in ("stock_video", "stock_photo"):
        def one(q):
            try:
                return q, _search_one(ctx, kind, q, count, orientation,
                                      max_s), None
            except Exception as exc:  # noqa: BLE001 - one query, not all
                return q, [], f"{type(exc).__name__}: {str(exc)[:160]}"
        with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
            results = list(pool.map(carry(one), queries))
        total = 0
        for q, hits, err in results:
            if err:
                body.append(f"[{q}] search failed ({err}); the other "
                            "queries stand")
                continue
            if not hits:
                body.append(f"[{q}] nothing in the libraries for these words")
                continue
            _remember(ctx, hits)
            board = _board(ctx, hits, q)
            if board:
                env.image(*board)
            agent_tools._remember_delivered_thumbs(ctx, hits)
            total += len(hits)
            body.append(f"[{q}] {len(hits)} result(s):")
            body.extend("  " + _line(h) for h in hits)
        env.measured = {"queries": len(queries), "results": total}
        env.body = "\n".join(body)
        env.data = {"kind": kind}
        return env
    for q in queries:
        try:
            text = _text_kind(ctx, kind, q, count)
        except Exception as exc:  # noqa: BLE001
            text = f"search failed ({type(exc).__name__}: {str(exc)[:160]})"
        body.append(f"[{q}]\n{_facts(text)}")
    for label, path in list(getattr(ctx, "pending_images", None) or []):
        env.image(label, path)
    ctx.pending_images = []
    env.body = "\n".join(body)
    return env


def _facts(text):
    keep = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith(("Do NOT", "Do not", "NEXT:", "Tell the user")):
            continue
        keep.append(line)
    return "\n".join(keep)[:4000]


# --------------------------------------------------------------------------
# music: the reference-grade set only

def _music_pool():
    import music_choose
    pool = music_choose.set_tracks()
    if pool:
        return pool, "the reference music set"
    return music_choose.choosable(strict=True), "library tracks the gate passed"


def _music(ctx, queries, count):
    import music_choose
    pool, basis = _music_pool()
    env = envelope.Envelope(tool="find")
    if not pool:
        env.failed = True
        env.skip(0, "no music track is available on this deployment",
                 "the user's own track: upload it and place it by key")
        return env
    try:
        n = max(3, min(24, int(count or 12)))
    except (TypeError, ValueError):
        n = 12
    program = 0.0
    try:
        from schemas import program_duration
        program = float(program_duration(ctx.latest_edl()["json"]) or 0.0)
    except Exception:  # noqa: BLE001
        pass
    film = {"kind": "longform" if program > 120 else "story",
            "duration_s": program}
    try:
        options, _prof = music_choose.shortlist(film, n=n, pool=pool)
        tracks = [o["track"] for o in options]
    except Exception:  # noqa: BLE001 - the pool itself, unranked
        tracks = pool[:n]
    lines = [f"{len(tracks)} track(s) from {basis}, ranked by fit to this "
             f"film's length and pace" + (f" (asked: {', '.join(queries)})"
                                          if queries else "") + ":"]
    for t in tracks:
        bits = [f"{LIB_PREFIX}{t['cid']}"]
        for key in ("title", "style", "mood", "bpm", "duration_s",
                    "license"):
            if t.get(key):
                bits.append(f"{key}={t[key]}")
        lines.append("  " + " | ".join(str(b) for b in bits)[:300])
    env.body = "\n".join(lines)
    env.measured = {"tracks": len(tracks)}
    return env


def music_asset(stage, rid):
    """(storage key, note) for a find(kind='music') id; provenance granted
    so the write is stamped as Valmera's chosen track."""
    import music_choose
    import music_gate
    cid = str(rid or "").strip()
    if cid.startswith(LIB_PREFIX):
        cid = cid[len(LIB_PREFIX):]
    pool, _basis = _music_pool()
    if cid not in {t["cid"] for t in pool}:
        return None, (f"{rid} is not in the music find() offers "
                      "(the reference-grade set)")
    key, err = music_choose.ensure_project_asset(stage, {"cid": cid})
    if err:
        return None, err
    music_gate.grant(stage, key, music_choose.PROVENANCE)
    return key, ""


def sfx_asset(stage, rid):
    rid = str(rid or "").strip()
    if rid.startswith("sound:"):
        return rid, ""
    return None, f"{rid} is not a sound from find(kind='sfx')"


def candidates(stage, item_id):
    """The id, then the other results of the same search, in order."""
    rid = str(item_id or "").strip()
    if not rid:
        return []
    out = [rid]
    hit = (getattr(stage, "stock_results", None) or {}).get(rid)
    if not hit:
        try:
            hit, _err = agent_tools._recover_search_hit(stage, "stock", rid,
                                                        resolver=None)
        except Exception:  # noqa: BLE001
            hit = None
    for other in (hit or {}).get("_v2_group") or []:
        if other not in out:
            out.append(other)
    return out
