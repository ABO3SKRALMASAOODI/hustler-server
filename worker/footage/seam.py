"""The one seam. Everything outside worker/footage reaches the engine through these three calls:

  for_lines(...)    the workflow shell's supply stage (plugins/valmera/skills/_shell): a script's lines in,
                    footage per line out (footage.json beside the clips).
  bin_offers(...)   worker/score/supply.run: footage candidates in the Bin's candidate shape for the wishes
                    whose words name a real subject. Customers only through the switch (OFF), and only
                    when the Director's supply budget leaves the engine the minutes it needs.
  acquire(...)      stock.download's route for a footage candidate: the clip this worker cut, or the same
                    section cut again from its source on whichever worker acquires it.
"""
import os
import shutil
import threading
import time

from . import switch

PROVIDER = "footage"
LICENCE = "Footage, not licensed: the owner's decision (app_kv footage)"
MIN_BUDGET_S = 600.0          # the engine took 225-660 s a story on the Mac (13 Nov): it has no deadline of its own yet,
                              # so a customer's supply (36 s today) always skips it and says so
# A first film's footage (score.film, round six of lane K): the film is already the customer's when this arrives, and
# it is waited for only so long. Measured on the box, 14 Nov, for one film's four subjects with the engine's own
# settings: 404 s and $0.61, of which hearing 4,300 s of sources was $0.31 and most of the minutes, and one clip a
# wish. A wish wants a picture of its subject, not a line that was said: nothing is heard, the first five minutes of
# a source are read, two sources a subject and eight in all are opened, and each wish is given two clips to cut on.
FIRST_FILM = {"max_sources": 8, "hear_s": 0.0, "read_s": 300.0, "per": 2}
LINE_S = 3.0                  # two shots a wish at the engine's 1.5 s pace
_CFG = {"cfg": None, "at": 0.0, "busy": False}
_LOCK = threading.Lock()
CFG_TTL_S = 300.0


def for_lines(lines, named, about, out_dir, W=1080, H=1920, scope="workflow", log=print, anchors=None, avoid=None):
    """The shell's call. None when the scope is switched off; else run.find's record. anchors: {person:
    [photographs already checked as that person]} to hold that person's clips to one face. avoid: clips an
    earlier run already cut for this film ([{"url", "time_range"}]): those seconds are not cut again."""
    if not switch.allowed(scope):
        return None
    from . import run
    return run.find(lines, named, about, out_dir, W, H, log=log, anchors=anchors, avoid=avoid)


def _read_cfg():
    try:
        import db
        conn = db.connect()
        try:
            cfg = switch.parse(db.kv_get(conn, switch.KEY))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 no database: off
        cfg = dict(switch.DEFAULTS)
    with _LOCK:
        _CFG.update(cfg=cfg, at=time.monotonic(), busy=False)


def customer_cfg():
    """The switch row, read off the caller's path: a stale or missing value starts one background read
    and answers with what it has (off before the first read). A customer's supply never waits on it."""
    with _LOCK:
        stale = _CFG["cfg"] is None or time.monotonic() - _CFG["at"] > CFG_TTL_S
        start = stale and not _CFG["busy"]
        if start:
            _CFG["busy"] = True
        cfg = _CFG["cfg"] or dict(switch.DEFAULTS)
    if start:
        threading.Thread(target=_read_cfg, name="footage-switch", daemon=True).start()
    return cfg


def bin_offers(work, wishes, subjects, deadline, report=None, about="", user_id=None, W=1080, H=1920):
    """Footage candidates for score/supply.run's named route: [] while the switch is off for this account
    (no call, no cost) or when the budget is shorter than the engine needs (said in the report). Never
    raises: a customer's supply is never lost to this engine."""
    try:
        if not switch.allowed("customer", user_id, customer_cfg()):
            return []
        return _offers(work, wishes, subjects, deadline, report, about, W, H)
    except Exception as e:  # noqa: BLE001 the Bin stands without footage, and says why
        if report is not None:
            report.append(dict(footage_error=str(e)[:160]))
        return []


def _offers(work, wishes, subjects, deadline, report, about, W, H):
    left = deadline - time.monotonic()
    if left < MIN_BUDGET_S:
        if report is not None:
            report.append(dict(footage=f"skipped: the supply budget left {left:.0f} s, the engine needs {MIN_BUDGET_S:.0f} s"))
        return []
    lines, named = [], []
    for i, w in enumerate(wishes):
        sub = subjects[i][0] if i < len(subjects) and subjects[i] else None
        if not sub or w.get("mine"):
            continue
        lines.append(dict(i=i, say=str(w.get("want") or w.get("say") or sub["name"]), pictures=[sub["name"]], dur=LINE_S))
        if sub["name"] not in [n["name"] for n in named]:
            named.append({"name": sub["name"], "is": sub.get("is"), "must_show": sub.get("must_show")})
    if not lines:
        return []
    from . import run
    res = run.find(lines, named, about, os.path.join(work, "footage"), W, H, log=lambda m: print("[footage] " + m, flush=True), **FIRST_FILM)
    from PIL import Image
    out = []
    for ln in res["lines"]:
        for c in ln["clips"]:
            path = os.path.join(work, "footage", c["file"])
            thumb = path[:-4] + ".jpg"
            try:
                from . import shots
                # each clip's frame in a folder of its own: shots.frames names a frame by its time and keeps one that is
                # there, so in one folder every clip stood for by the first clip's frame (14 Nov: ten offers, one picture)
                got = shots.frames(path, [min(0.5, c["seconds"] / 2)], path[:-4] + "_frame")
                img = Image.open(next(iter(got.values()))).convert("RGB")
            except Exception:  # noqa: BLE001 a clip with no frame is not offered
                continue
            out.append(dict(id=f"footage:{os.path.basename(path)}", provider=PROVIDER, kind="video", _wish=ln["i"], named=c["subject"],
                            shows=c["shows"], local_file=path, width=W, height=H, duration_s=c["seconds"], license=LICENCE,
                            credit=c["source"]["channel"], page_url=c["source"]["url"], title=c["source"]["title"], _thumb=None, _img=img,
                            footage=dict(url=c["source"]["url"], time_range=c["time_range"], x=c.get("x"), eq=c.get("eq"), trim=c.get("trim"))))
    if report is not None:
        report.append(dict(footage=res["counts"], footage_usd=res["spend"]["usd"]))
    return out


def acquire(item, out_path, want_w, want_h):
    """stock.download for provider "footage". Returns the item, annotated like a stock download."""
    local = item.get("local_file")
    if local and os.path.exists(local):
        shutil.copyfile(local, out_path)
    else:
        from . import cut, fetch
        f = item.get("footage") or {}
        t0, t1 = f["time_range"]
        try:
            st = fetch.streams(f["url"])
        except Exception:  # noqa: BLE001 yt-dlp's section download is the next rung
            st = None
        raw = out_path + ".raw.mp4"
        try:
            cut.grab(st, f["url"], t0, t1, raw)
            cut.finish(raw, out_path, want_w, want_h, f.get("x") or 0.5, f.get("eq"), trim=f.get("trim") or (0.0, 0.0))
        finally:
            if os.path.exists(raw):
                os.remove(raw)
    item["source_url"] = (item.get("footage") or {}).get("url")
    item["picked_width"], item["picked_height"] = want_w, want_h
    return item
