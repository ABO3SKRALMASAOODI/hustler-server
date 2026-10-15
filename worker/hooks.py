"""Re-solve hooks: devices store intent, so a song swap or a keep change
re-lands them (ROADMAP A4; INTERFACES.md §6.4; track 0C).

Later tracks register a re-solver here without editing this file:

    import hooks
    hooks.register("music_swap", fn)   # fn(edl, old_music_id, new_music_id)
    hooks.register("keep_change", fn)  # fn(old_edl, new_edl, old_tl, new_tl)

Each re-solver returns ``(edl, notes)``. Hooks run in registration order,
each on the previous one's output; a hook that raises is skipped with a
note naming it (a re-solver can never fail the write it decorates). The
built-in re-solvers below move only what can be moved honestly: fx items
bound to the swapped track follow its id; music_bus items whose track was
removed, junctions whose cut is gone and items anchored to the letters of a
graphic that is gone are dropped with a note (built in, so the backend's
Studio keep path runs them too).
"""

import copy

EVENTS = ("music_swap", "keep_change")
_REGISTRY = {name: [] for name in EVENTS}


def register(event, fn):
    if event not in EVENTS:
        raise ValueError(f"unknown hook event {event!r}; events: {EVENTS}")
    if not callable(fn):
        raise TypeError("a hook is callable")
    if fn not in _REGISTRY[event]:
        _REGISTRY[event].append(fn)
    return fn


def unregister(event, fn):
    try:
        _REGISTRY[event].remove(fn)
    except (KeyError, ValueError):
        pass


def registered(event):
    return tuple(_REGISTRY.get(event, ()))


def _run(event, edl, args):
    notes = []
    for fn in list(_REGISTRY[event]):
        try:
            out = fn(edl, *args)
            new_edl, more = out if isinstance(out, tuple) else (out, [])
            if isinstance(new_edl, dict):
                edl = new_edl
            notes += [str(n) for n in (more or ())]
        except Exception as e:  # noqa: BLE001 — never fail the write
            notes.append(f"re-solver {getattr(fn, '__name__', fn)} skipped "
                         f"({type(e).__name__})")
    return edl, notes


def _rebind_music_items(edl, old_music_id, new_music_id):
    """Built-in: fx items bound to a swapped track's rhythm or bus follow
    the new track id (their times re-solve once a rhythm track registers)."""
    if not old_music_id or old_music_id == new_music_id:
        return edl, []
    moved = 0
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        if it.get("music_id") == old_music_id:
            it["music_id"] = new_music_id
            moved += 1
        bind = it.get("bind")
        if isinstance(bind, dict) and bind.get("rhythm") == old_music_id:
            bind["rhythm"] = new_music_id
            moved += 1
    return edl, ([f"{moved} device binding(s) follow the new track"]
                 if moved else [])


def _drop_orphan_bus_items(old_edl, new_edl, old_tl=None, new_tl=None):
    """Built-in: a music_bus item whose music item is gone cannot play."""
    ids = {m.get("id") for m in new_edl.get("music") or []
           if isinstance(m, dict)}
    items = new_edl.get("fx") or []
    keep = [it for it in items if not (isinstance(it, dict)
                                       and it.get("stage") == "music_bus"
                                       and it.get("music_id") not in ids)]
    if len(keep) == len(items):
        return new_edl, []
    new_edl["fx"] = keep
    return new_edl, [f"{len(items) - len(keep)} music-bus device(s) removed "
                     "with their track"]


def _timeline_module():
    """worker/timeline.py as this process loaded it: ``timeline`` on the
    worker, ``worker_timeline`` in the backend (loaded by file path)."""
    import sys
    for name in ("timeline", "worker_timeline"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "program_blocks"):
            return mod
    try:
        import timeline
        return timeline
    except Exception:  # noqa: BLE001
        return None


def program_cuts(edl):
    """Program seconds of every cut in ``edl`` (a block boundary that is not
    a split of one continuous take)."""
    tlm = _timeline_module()
    if tlm is None:
        return None
    blocks = tlm.program_blocks(edl)
    out = []
    for a, b in zip(blocks, blocks[1:]):
        if a.get("kind") == b.get("kind") == "footage" and \
                abs(float(b["src_start"]) - float(a["src_end"])) < 1e-3:
            continue                         # a split, not a cut
        out.append(float(a["out_end"]))
    return out


def _drop_orphan_junctions(old_edl, new_edl, old_tl=None, new_tl=None):
    """Built-in (Wave 1 track 1D, made built-in at integration so the
    backend's Studio keep path runs it too): a junction whose cut a keep
    change removed (two spans merged, an insert deleted) decorates nothing,
    so it is dropped with a note rather than left for the render to report
    ``omit`` (and a final to ask to acknowledge). Accents and strobes are
    content-anchored and move with their footage."""
    items = (new_edl or {}).get("fx") or []
    juncs = [i for i in items if isinstance(i, dict)
             and i.get("type") == "junction"]
    if not juncs:
        return new_edl, []
    try:
        cuts = program_cuts(new_edl)
    except Exception:  # noqa: BLE001
        cuts = None
    if cuts is None:
        return new_edl, []
    gone = [j["id"] for j in juncs
            if not any(abs(float(j.get("at") or -9) - c) <= 0.06
                       for c in cuts)]
    if not gone:
        return new_edl, []
    new_edl["fx"] = [i for i in items if not (isinstance(i, dict)
                                              and i.get("id") in gone)]
    return new_edl, [f"junction device(s) {', '.join(gone)} removed: their "
                     "cut is no longer in the edit"]


def _glyph_refs(item):
    """Motion item ids an fx item is anchored to (a glyph region, a glyph
    origin)."""
    out = []
    for key in ("region", "origin"):
        v = item.get(key)
        if isinstance(v, dict) and (v.get("shape") == "glyph"
                                    or v.get("kind") == "glyph") \
                and v.get("ref") is not None:
            out.append(str(v.get("ref")))
    return out


def _drop_orphan_glyph_items(old_edl, new_edl, old_tl=None, new_tl=None):
    """Built-in (Wave 1 integration; found by the 1C review): a keep change
    that drops a motion graphic left an fx item anchored to its letters (a
    glyph region, a letter-window origin) naming an item that is gone, and
    validation then refused the WHOLE cut. Such items go with their
    graphic, with a note."""
    items = (new_edl or {}).get("fx") or []
    if not items:
        return new_edl, []
    ids = {str(m.get("id")) for m in (new_edl or {}).get("motion") or []
           if isinstance(m, dict)}
    gone = [i.get("id") for i in items if isinstance(i, dict)
            and any(r not in ids for r in _glyph_refs(i))]
    if not gone:
        return new_edl, []
    new_edl["fx"] = [i for i in items if not (isinstance(i, dict)
                                              and i.get("id") in gone)]
    return new_edl, [f"device(s) {', '.join(str(g) for g in gone)} removed "
                     "with the graphic whose letters they used"]


def on_music_swap(edl, old_music_id, new_music_id):
    """(edl, notes) after the user's track changed under ``old_music_id``
    (same id, new song) or moved to ``new_music_id``. Never mutates the
    caller's dict."""
    edl = copy.deepcopy(edl or {})
    edl, notes = _rebind_music_items(edl, old_music_id, new_music_id)
    edl, more = _run("music_swap", edl, (old_music_id, new_music_id))
    return edl, notes + more


def on_keep_change(old_edl, new_edl, old_tl=None, new_tl=None):
    """(edl, notes) after timeline.remap_program_items moved program items
    for a keep change: device re-solvers run on the remapped EDL."""
    edl = copy.deepcopy(new_edl or {})
    edl, notes = _drop_orphan_bus_items(old_edl or {}, edl, old_tl, new_tl)
    for builtin in (_drop_orphan_glyph_items, _drop_orphan_junctions):
        try:
            edl, more = builtin(old_edl or {}, edl, old_tl, new_tl)
            notes += list(more or [])
        except Exception as e:  # noqa: BLE001 — never fail the write
            notes.append(f"re-solver {builtin.__name__} skipped "
                         f"({type(e).__name__})")
    out_notes = []
    for fn in list(_REGISTRY["keep_change"]):
        try:
            res = fn(old_edl, edl, old_tl, new_tl)
            new, more = res if isinstance(res, tuple) else (res, [])
            if isinstance(new, dict):
                edl = new
            out_notes += [str(n) for n in (more or ())]
        except Exception as e:  # noqa: BLE001
            out_notes.append(f"re-solver {getattr(fn, '__name__', fn)} "
                             f"skipped ({type(e).__name__})")
    return edl, notes + out_notes
