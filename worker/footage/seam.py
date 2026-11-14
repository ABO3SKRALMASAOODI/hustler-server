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


# What real video exists of (round seven). The pictures route names people, places, brands and events, because a
# photo archive is searched by a proper name; in round six a film about the Greenland shark, one about cupping and
# acupuncture and one about a robotics league named "nothing" and got no footage. Video is found of a kind of thing
# by its own name too: a species, a dish, a sport, a craft, a therapy, a machine, a natural phenomenon.
NAME_SYSTEM = """You help a film editor find real video. You get moments of a film: the words said there and what the editor wants to show, in any language, perhaps misspelled or run together.

For each moment, name the one real-world subject its words name or plainly point to that real video exists of: interviews, launches, races, news, archive, documentary and nature film. A named person, place or building, a brand, company or product by name, a named event, team, vehicle, artwork or organisation. Also a specific kind of thing by its own name: a species or breed (Greenland shark), a named dish (hakarl), a sport, craft, therapy or technique by its name (cupping therapy, acupuncture), a named kind of machine (a Formula One car), a natural phenomenon (the aurora). Fix the spelling to the subject's usual English name. Not an idea, a feeling, a mood or a metaphor; not an unnamed person or the speaker; not the customer's own product, shop or workplace; and not a thing so general that any library clip shows it (an office, a phone, a handshake, a city street, "students", "a customer").

For each subject: "name" (the usual English name), "is" (person, place, brand, event or thing), "searches" (one to three video searches, most specific first, the bare name last), "must_show" (one plain sentence: what a clip must visibly show to count, naming every form the subject can take on camera: a racing driver "himself, or his own car on track"; a species "the animal itself, alive, filling the frame"; a therapy "the treatment being given, close on the body").

Answer with one JSON object and nothing else."""


def name_for_footage(work, wishes, about=""):
    """(subjects per wish, usd), as score.supply.name_wishes, by the wider rule above. A moment that is the customer's
    own (`mine`) is not asked."""
    import broll_source
    from score import supply
    texts, idx = [], []
    for i, w in enumerate(wishes):
        if w.get("mine"):
            continue
        said = supply.clause_of(work, w.get("w") or w.get("at"))
        texts.append((f'words said there: "{said}" | ' if said else "") + f"the editor wants: {w.get('want') or ''}")
        idx.append(i)
    out = [[] for _ in wishes]
    if not texts:
        return out, 0.0
    got, usd = broll_source.name_many(texts, context=about, system=NAME_SYSTEM)
    for j, subs in zip(idx, got):
        out[j] = subs[:1]
    return out, usd


def shelved(wishes, subjects, W=1080, H=1920, per=2):
    """The clips earlier films' engine runs cut of these moments' subjects (footage.cache), as Bin candidates: `per`
    a moment, a clip offered once, each carrying the moment it is for (`_wish`, and `_w`, the moment's own words or
    second, for a caller whose wishes are numbered another way). (offers, names with a shelf)."""
    from . import cache
    by_name, out, used = {}, [], set()
    for i, w in enumerate(wishes):
        sub = subjects[i][0] if i < len(subjects) and subjects[i] else None
        if not sub or w.get("mine"):
            continue
        name = sub["name"]
        if name not in by_name:
            by_name[name] = cache.get(name, W, H)
        n = 0
        for c in by_name[name]:
            if c["id"] in used or n >= per:
                continue
            used.add(c["id"])
            n += 1
            out.append(dict(c, _wish=i, _w=w.get("w") or w.get("at")))
    return out, sorted(k for k, v in by_name.items() if v)


def shelve(offers, W=1080, H=1920):
    """An engine run's offers onto the shelf, a subject at a time. Returns {subject: clips shelved}."""
    from . import cache
    by = {}
    for o in offers or []:
        if o.get("named") and o.get("local_file"):
            by.setdefault(o["named"], []).append(o)
    return {name: cache.put(name, rows, W, H) for name, rows in by.items()}


def bin_offers(work, wishes, subjects, deadline, report=None, about="", user_id=None, W=1080, H=1920, skip=()):
    """Footage candidates for score/supply.run's named route: [] while the switch is off for this account
    (no call, no cost) or when the budget is shorter than the engine needs (said in the report). Never
    raises: a customer's supply is never lost to this engine. skip: subjects that came off the shelf."""
    try:
        if not switch.allowed("customer", user_id, customer_cfg()):
            return []
        return _offers(work, wishes, subjects, deadline, report, about, W, H, skip=skip)
    except Exception as e:  # noqa: BLE001 the Bin stands without footage, and says why
        if report is not None:
            report.append(dict(footage_error=str(e)[:160]))
        return []


def _offers(work, wishes, subjects, deadline, report, about, W, H, skip=()):
    left = deadline - time.monotonic()
    if left < MIN_BUDGET_S:
        if report is not None:
            report.append(dict(footage=f"skipped: the supply budget left {left:.0f} s, the engine needs {MIN_BUDGET_S:.0f} s"))
        return []
    lines, named = [], []
    for i, w in enumerate(wishes):
        sub = subjects[i][0] if i < len(subjects) and subjects[i] else None
        if not sub or w.get("mine") or sub["name"] in skip:
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
    elif item.get("cache_key"):                # a clip an earlier film's engine run cut (footage.cache): the object as it was shelved
        import storage
        storage.download_to(item["cache_key"], out_path)
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
