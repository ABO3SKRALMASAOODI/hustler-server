"""Re-solve hooks: devices store intent, so a song swap or a keep change
re-lands them (ROADMAP A4; INTERFACES.md §6.4; track 0C).

Later tracks register a re-solver here without editing this file:

    import hooks
    hooks.register("music_swap", fn)   # fn(edl, old_music_id, new_music_id)
    hooks.register("keep_change", fn)  # fn(old_edl, new_edl, old_tl, new_tl)

Each re-solver returns ``(edl, notes)``. Hooks run in registration order,
each on the previous one's output; a hook that raises is skipped with a
note naming it (a re-solver can never fail the write it decorates). The
built-in re-solvers below move only what Wave 0 can move honestly: fx items
bound to the swapped track follow its id, and music_bus items whose track
was removed are dropped with a note.
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
