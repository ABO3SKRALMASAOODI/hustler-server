"""The First cut's speed, without touching what it builds (Q8, 30 Oct 2026).

Measured on the box harness: a talking-head First cut took about 163 s
uncontended, 84 s of it the scout, the B-roll search and the B-roll check,
the rest the 720p approval render, and the user watched "Editing..." with no
word of progress the whole time.

Three things here, all for engine accounts only (trial accounts and new
subscribers: the First cut itself is theirs alone, and every hook below is
reached only from a First cut context that set them up):

  * ``BrollPrefetch``: the scout names its B-roll moments with their stock
    queries before the edit is built. The library searches and the downloads
    of each moment's short list start right then, in parallel threads, while
    the chain cuts pauses, crops bands and sets the hook title. When
    ``source_broll`` places the cutaways it takes the prefetched searches and
    files instead of fetching moment after moment. What it chooses is the
    same: the same searches (same query, kind, orientation and count), the
    same pre-rank, the same short list rule; a candidate the prefetch did not
    fetch is fetched as before, and a frame that turned out different from the
    predicted one makes the prefetch stand aside entirely.
  * ``cached_search``: a library search answers from a short-lived cache
    (30 minutes, complete answers only), so a second search for the same
    shot (the request turn re-sourcing, the same upload twice) is free.
  * ``progress``: the First cut says what it is doing as it does it, as
    activity rows the Studio shows live (``first_cut_step``).
"""
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

SEARCH_TTL_S = 1800.0
PARTIAL_TTL_S = 300.0      # an answer a slow library missed is kept shorter
SEARCH_MAX = 512
SEARCH_WORKERS = 8         # network-bound: searches, then the short lists
FETCH_WORKERS = 6          # the downloads, never queued behind a search
WAIT_S = 90.0              # the longest placement waits on one prefetch item
# A library that has not answered within LANE_DEADLINE_S is not waited for:
# its siblings' answers are used, and it is skipped for SLOW_LANE_SKIP_S.
# Measured 30 Oct: Openverse timed out at its 20 s read timeout on every
# search while Wikimedia and NASA answered in 0.4 s, so every First cut's
# B-roll waited 20 s per wave of searches for nothing.
LANE_DEADLINE_S = 6.0
SLOW_LANE_SKIP_S = 300.0

_SEARCH = {}
_SEARCH_LOCK = threading.Lock()
_SLOW = {}                 # provider -> skip it until (monotonic)
_LANE_POOL = ThreadPoolExecutor(max_workers=24, thread_name_prefix="fc-lane")


def _search_key(query, kind, orientation, count, providers, archival):
    return (" ".join(str(query or "").lower().split()), str(kind),
            str(orientation), int(count),
            tuple(sorted(providers)) if providers else None, bool(archival))


def cached_search(query, kind, orientation, count=4, providers=None,
                  archival=False):
    """broll_source.search's (candidates, errors, lanes), answered from the
    cache when the same search ran in the last SEARCH_TTL_S. Only a complete
    answer (no library errored) is kept; callers get their own copies, since
    acquisition fills renditions into a candidate in place."""
    key = _search_key(query, kind, orientation, count, providers, archival)
    now = time.monotonic()
    with _SEARCH_LOCK:
        hit = _SEARCH.get(key)
        if hit and now - hit[0] < hit[4]:
            return copy.deepcopy(hit[1]), list(hit[2]), list(hit[3])
    cands, errors, lanes, partial = deadline_search(
        query, kind, orientation, count=count, providers=providers,
        archival=archival)
    if lanes and (not errors or partial):
        with _SEARCH_LOCK:
            if len(_SEARCH) >= SEARCH_MAX:
                for k in sorted(_SEARCH, key=lambda k: _SEARCH[k][0])[
                        :SEARCH_MAX // 4]:
                    _SEARCH.pop(k, None)
            _SEARCH[key] = (now, copy.deepcopy(cands), list(errors),
                            list(lanes),
                            PARTIAL_TTL_S if errors else SEARCH_TTL_S)
    return cands, errors, lanes


def deadline_search(query, kind, orientation="portrait", count=4,
                    providers=None, archival=False):
    """broll_source.search's (candidates, errors, lanes) plus whether a slow
    library was left out: the same libraries for the same kind, searched in
    parallel, but a library still silent after LANE_DEADLINE_S is not waited
    for (and is skipped for SLOW_LANE_SKIP_S). The candidates keep the
    libraries' order, as broll_source.search returns them."""
    import broll_source as bs
    st = bs.status()
    want = [p for p in (providers or bs.PROVIDERS) if p in bs.LANES]
    if not archival:
        want = [p for p in want if p != "archive"]
    lanes = [p for p in want if st.get(p) == "on"]
    if kind == bs.KIND_PHOTO:
        lanes = [p for p in lanes if p != "archive"]
    else:
        lanes = [p for p in lanes if p != "openverse"]
    now = time.monotonic()
    errors, skipped = [], []
    for p in list(lanes):
        if _SLOW.get(p, 0.0) > now:
            lanes.remove(p)
            skipped.append(p)
            errors.append(f"{p}: slow, skipped")
    if not lanes:
        return [], errors, [], bool(skipped)
    futs = {_LANE_POOL.submit(bs.LANES[p], query, kind, orientation, count): p
            for p in lanes}
    done, pending = wait(futs, timeout=LANE_DEADLINE_S)
    got = {}
    for f in done:
        p = futs[f]
        try:
            got[p] = f.result() or []
        except Exception as e:  # noqa: BLE001 — one library down
            errors.append(f"{p}: {str(e)[:120]}")
    for f in pending:
        p = futs[f]
        _SLOW[p] = time.monotonic() + SLOW_LANE_SKIP_S
        errors.append(f"{p}: no answer in {LANE_DEADLINE_S:g}s, skipped")
        skipped.append(p)
    out = []
    for p in lanes:
        out.extend(got.get(p) or [])
    return out, errors, [p for p in lanes if p in got], bool(skipped)


def _prov(providers):
    """A library set as a key: a sorted tuple, or None for every library."""
    return tuple(sorted(providers)) if providers else None


def clear_search_cache():
    with _SEARCH_LOCK:
        _SEARCH.clear()


def cand_key(cand):
    return (str(cand.get("provider")), str(cand.get("id")))


class BrollPrefetch:
    """Searches and short-list downloads for the scout's B-roll moments,
    running in the background from the moment the scout answers.

    ``frame`` is (orientation, W, H) as source_broll will read it from the
    EDL when it places the cutaways; ``kinds`` the libraries' kinds the
    moments will ask for ("video"/"photo"). ``providers`` maps a kind to the
    libraries its search is limited to (QI2: the First cut's first pass asks
    the curated stock libraries alone for video, Q5), None for every one."""

    def __init__(self, ctx, queries, frame, kinds, providers=None):
        self.ctx = ctx
        self.frame = tuple(frame)
        self.kinds = tuple(kinds)
        self.providers = {k: _prov(v) for k, v in (providers or {}).items()
                          if k in self.kinds and _prov(v)}
        self.queries = []
        for q in queries:
            q = " ".join(str(q or "").split())[:120]
            if q and q not in self.queries:
                self.queries.append(q)
        self._lock = threading.Lock()
        self._search = {}      # (query, kind, providers) -> Future[(cands, errors)]
        self._fetch = {}       # cand_key -> Future[fetched]
        self._pool = ThreadPoolExecutor(max_workers=SEARCH_WORKERS,
                                        thread_name_prefix="fc-broll")
        self._fpool = ThreadPoolExecutor(max_workers=FETCH_WORKERS,
                                         thread_name_prefix="fc-fetch")
        self.started = time.monotonic()
        self.stats = {"searches": 0, "fetches": 0, "search_hits": 0,
                      "fetch_hits": 0, "fetch_misses": 0}

    # -- background work ---------------------------------------------------

    def start(self):
        orientation = self.frame[0]
        for q in self.queries:
            for k in self.kinds:
                prov = self.providers.get(k)
                self._search[(q, k, prov)] = self._pool.submit(
                    self._one_search, q, k, orientation, prov)
        for q in self.queries:
            self._pool.submit(self._shortlist, q)
        return self

    def _one_search(self, q, kind, orientation, providers=None):
        c, e, _lanes = cached_search(q, kind, orientation, count=4,
                                     providers=list(providers)
                                     if providers else None)
        with self._lock:
            self.stats["searches"] += 1
        return c, e

    def _shortlist(self, q):
        """The same short list source_broll would draw for this query on an
        empty board: pre-rank, then at most two from one library."""
        try:
            from tools_ext import broll_tools as bt
            import broll_source
            cands = []
            # the first pass's searches: with curated video (Q5) the short
            # list is drawn from that video alone, as the placement draws it
            first = [("video", self.providers["video"])] \
                if "video" in self.providers else \
                [(k, self.providers.get(k)) for k in self.kinds]
            for k, prov in first:
                c, _e = self._search[(q, k, prov)].result(timeout=WAIT_S)
                cands += c
            _o, W, H = self.frame
            import first_cut_visual as fcv
            ranked = broll_source.prerank(bt._dedupe(cands), [q], W, H,
                                          need_s=fcv.BROLL_MIN_S)
            for cand in bt._interleave(ranked, bt.SHORTLIST):
                self._submit_fetch(cand)
        except Exception as exc:  # noqa: BLE001 — placement fetches itself
            print(f"[first_cut] b-roll prefetch '{q}': "
                  f"{type(exc).__name__}: {str(exc)[:120]}", flush=True)

    def _submit_fetch(self, cand):
        from tools_ext import broll_tools as bt
        key = cand_key(cand)
        with self._lock:
            if key in self._fetch:
                return
            _o, W, H = self.frame
            self._fetch[key] = self._fpool.submit(bt._fetch, self.ctx,
                                                  copy.deepcopy(cand), W, H)
            self.stats["fetches"] += 1

    # -- what source_broll asks --------------------------------------------

    def matches(self, orientation, W, H, providers=None, archival=False):
        """The same frame, not archival, and every library (None) or a
        library set this prefetch searched for one of its kinds."""
        if self.frame != (orientation, W, H) or archival:
            return False
        return providers is None or _prov(providers) in \
            set(self.providers.values())

    def search(self, q, kind, providers=None):
        """(candidates, errors) for a prefetched search, else None."""
        fut = self._search.get((" ".join(str(q or "").split())[:120], kind,
                                _prov(providers)))
        if fut is None:
            return None
        try:
            c, e = fut.result(timeout=WAIT_S)
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            self.stats["search_hits"] += 1
        return copy.deepcopy(c), list(e)

    def fetched(self, cand):
        """The prefetched {"cand", "got"|"error"} for this candidate (with
        the caller's own candidate dict), else None."""
        with self._lock:
            fut = self._fetch.get(cand_key(cand))
        if fut is None:
            with self._lock:
                self.stats["fetch_misses"] += 1
            return None
        try:
            got = fut.result(timeout=WAIT_S)
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            self.stats["fetch_hits"] += 1
        return dict(got, cand=cand)

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._fpool.shutdown(wait=False, cancel_futures=True)


def predicted_frame(ctx, vertical_source):
    """(orientation, W, H) source_broll will read when the First cut places
    its cutaways: a vertical source keeps the EDL's frame (the band crop is a
    zoom, not a frame); a landscape talking head is reframed to 9:16."""
    from tools_ext import broll_tools as bt
    if vertical_source:
        try:
            return bt._frame(ctx.latest_edl()["json"])
        except Exception:  # noqa: BLE001
            return bt._frame({})
    return bt._frame({"frame": {"ratio": "9:16"}})


def start_broll_prefetch(ctx, moments, vertical_source):
    """Start the prefetch for the scout's moments [(t0, t1, word, query)];
    returns the BrollPrefetch (also on ctx.broll_prefetch) or None."""
    queries = [m[3] for m in moments or [] if len(m) >= 4 and m[3]]
    if not queries:
        return None
    try:
        import stock
        video = stock.video_available()
        kinds = ("video", "photo") if video else ("photo",)
        providers = None
        if video:
            # QI2: the placement's first pass asks the curated libraries
            # alone for video (Q5, first_cut_visual.place_broll); the
            # prefetch searches that same set so the placement takes it
            import first_cut_visual as fcv
            keyed = fcv.keyed_video_providers()
            providers = {"video": keyed} if keyed else None
        pf = BrollPrefetch(ctx, queries, predicted_frame(ctx, vertical_source),
                           kinds, providers=providers).start()
    except Exception as exc:  # noqa: BLE001 — placement searches itself
        print(f"[first_cut] b-roll prefetch not started: "
              f"{type(exc).__name__}: {str(exc)[:120]}", flush=True)
        return None
    ctx.broll_prefetch = pf
    print(f"[first_cut] b-roll prefetch: {len(pf.queries)} queries, "
          f"frame {pf.frame}, kinds {pf.kinds}", flush=True)
    return pf


# ── live progress ────────────────────────────────────────────────────────

def progress(ctx, worker_db, session_id, step, label):
    """One activity row the Studio shows while the First cut works (it reads
    meta.kind == "first_cut_step"; an older Studio lists it as an ordinary
    editing step). Never raises."""
    if not session_id:
        return
    seen = getattr(ctx, "_first_cut_steps_posted", None)
    if seen is None:
        seen = set()
        try:
            ctx._first_cut_steps_posted = seen
        except Exception:  # noqa: BLE001
            return
    if step in seen:
        return
    seen.add(step)
    try:
        import db as dbx
        worker_db.run(dbx.add_message, session_id, "activity", label,
                      {"kind": "first_cut_step", "step": step,
                       "job_id": (getattr(ctx, "job", None) or {}).get("id")})
    except Exception as exc:  # noqa: BLE001 — progress never sinks the cut
        print(f"[first_cut] progress row not posted: {exc}", flush=True)
