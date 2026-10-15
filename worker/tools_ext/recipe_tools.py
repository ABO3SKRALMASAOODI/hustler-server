"""Recipes: whole creative moves, solved from the cut (Wave 1 integration).

Department ``compositing``. ``suggest_recipes`` (read-only) ranks the moves
this cut earns — story fit x freshness against the other shorts of the same
batch — each with the word it lands on, why, a reference exemplar and the
exact apply_edit_batch operations; ``apply_recipe`` writes one through the
apply_edit_batch path (tools_ext._write.commit). worker/recipes.py holds the
recipes and their taste.

Owner rules hold by construction: silent, zoom-free, flash-safe unless the
user opted in to flashing, B&W only as a device (mono_policy).
"""

DEPARTMENT = "compositing"
VERBS = ("suggest_recipes", "apply_recipe")
WRITE_TOOLS = {"apply_recipe"}


def _plan(ctx, edl):
    import device_planner
    return device_planner.moments(edl, getattr(ctx, "index", None) or {})


def _matte_ok(ctx, edl):
    """True (measured), None (measuring / unknown), False (no person)."""
    try:
        import tools_ext.verify_tools as vt
        st = vt.matte_state(ctx, edl) or {}
    except Exception:  # noqa: BLE001
        return None
    if st.get("person") is False:
        return False
    return True if st.get("status") in ("ready", "partial") else None


def _accent(edl):
    try:
        import subject_fx
        return subject_fx.accent_hex(edl)
    except Exception:  # noqa: BLE001
        return None


def sibling_edls(ctx, limit=12):
    """The fx layers of the other shorts of this batch (same parent
    project), newest version each — what freshness is judged against."""
    parent = (getattr(ctx, "project", None) or {}).get("parent_project_id")
    if not parent:
        return []

    def q(conn):
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (e.project_id) e.json->'fx' AS fx
                   FROM edls e JOIN projects p ON p.id = e.project_id
                   WHERE p.parent_project_id = %s AND p.id <> %s
                   ORDER BY e.project_id, e.version DESC LIMIT %s""",
                (parent, ctx.project_id, int(limit)))
            return [{"fx": r.get("fx") or []} for r in cur.fetchall()]
    try:
        return ctx.db.run(q) or []
    except Exception:  # noqa: BLE001 — freshness is advice
        return []


def _used_here(edl):
    import recipes
    return set(recipes.batch_usage([edl]))


def _flash_ok(ctx):
    try:
        import flash_safety
        return bool(flash_safety.opted_in(getattr(ctx, "user_message", "")))
    except Exception:  # noqa: BLE001
        return False


def _ops(items):
    import tools_ext._write as w
    return [w.upsert_fx(it) for it in items]


def suggest_recipes(ctx, start=None, end=None, max_results=None):
    """READ: the creative moves this cut earns, best first."""
    import json
    import recipes
    if not getattr(ctx, "has_main_video", True):
        return ("REJECTED: recipes land on spoken words of a main video; "
                "this canvas program has none.")
    edl = ctx.latest_edl()["json"]
    plan = _plan(ctx, edl)
    batch = recipes.batch_usage(sibling_edls(ctx))
    ids = {str(i.get("id")) for i in edl.get("fx") or []
           if isinstance(i, dict)}
    ranked = recipes.rank(plan, used_here=_used_here(edl), batch=batch,
                          matte_ok=_matte_ok(ctx, edl),
                          flash_ok=_flash_ok(ctx), accent=_accent(edl),
                          ids=ids, edl=edl)
    if start is not None or end is not None:
        lo = float(start or 0.0)
        hi = float(end) if end is not None else plan["program_s"]
        ranked = [r for r in ranked if lo <= r["at"] < hi]
    ranked = ranked[:max(1, min(8, int(max_results or 5)))]
    if not ranked:
        return ("NO RECIPE fits this cut yet: none of its words earns one "
                "(a turn, a payoff, a number, a mechanism, a heat or speed "
                "word). Nothing is wrong with that — restraint is a choice.")
    lines = [f"RECIPES for this cut (story fit x freshness; "
             f"{len(plan['moments'])} moments read). Pick ONE signature "
             "move per short — the one that fits THIS story — and prefer a "
             "fresh one over what the other shorts of the batch used. "
             "apply_recipe(name) writes it; or write the ops yourself in "
             "one apply_edit_batch."]
    for k, r in enumerate(ranked, 1):
        lines.append(
            f"{k}. {r['name']} — {r['title']}. Lands on '{r['word']}' at "
            f"{r['at']:.2f}s: {r['why']}. Fits: {r['story']}. "
            f"Score {r['score']:.2f} (fit {r['fit']:.2f}, "
            + ("fresh in this batch" if r["fresh"] else
               f"used by {r['uses']} other short(s) of this batch")
            + f"). Like {', '.join(r['exemplars'])}."
            + (" " + " ".join(r["notes"]) if r["notes"] else "")
            + ("\n   ops: " + json.dumps(_ops(r["items"]),
                                         separators=(",", ":"))
               if r["items"] else
               f"\n   write it with apply_recipe(name='{r['name']}'"
               f", at={r['at']:g}): it makes the registered stills of that "
               "frame first, then the strobe"))
    lines.append("Each is silent and zoom-free; flashes stay <= 3 a second "
                 "unless the user asked for flashing.")
    return "\n".join(lines)


def apply_recipe(ctx, name, at=None):
    """WRITE: one recipe, solved on this cut (optionally on the moment
    nearest ``at``) and written through apply_edit_batch."""
    import recipes
    import tools_ext._write as w
    if name not in recipes.RECIPES:
        return ("REJECTED: recipe is one of "
                + ", ".join(sorted(recipes.RECIPES)) + ".")
    if not getattr(ctx, "has_main_video", True):
        return "REJECTED: recipes need a main video."
    row = ctx.latest_edl()
    edl = row["json"]
    if name in _used_here(edl):
        return (f"REJECTED: {name} is already in this edit (a recipe is a "
                "signature move: once per short). remove_subject_effect / "
                "remove_finish / remove_punctuation its rc_ items first.")
    r = recipes.RECIPES[name]
    matte = _matte_ok(ctx, edl)
    if r.matte and matte is False:
        return (f"REJECTED: {name} draws on the person cut-out and this cut "
                "shows no person.")
    plan = _plan(ctx, edl)
    if at is not None:
        try:
            t = float(at)
        except (TypeError, ValueError):
            return "REJECTED: at is a program second."
        # restrict the moment model to the neighbourhood of `at`
        near = [m for m in plan["moments"] if abs(m["t"] - t) <= 1.5]
        if not near:
            return f"REJECTED: no spoken moment within 1.5 s of {t:g}s."
        # recipes that land on "the payoff" land on the spoken moment
        # nearest `at` when the planner's payoff is elsewhere (the call
        # names the landing; it was ignored and the move went to the
        # planner's payoff)
        pay = plan.get("payoff")
        if not pay or abs(float(pay["t"]) - t) > 1.5:
            words = [m for m in near if m["kind"] in ("stress", "word")]
            m = min(words or near, key=lambda m: (
                abs(float(m["t"]) - t), -float(m.get("strength") or 0)))
            pay = {"t": m["t"], "word": m.get("word"), "moment": m,
                   "hold_end": round(min(float(plan["program_s"]) - 0.05,
                                         float(m["t"]) + 1.5), 3)}
        plan = dict(plan, moments=near, payoff=pay)
    ids = {str(i.get("id")) for i in edl.get("fx") or []
           if isinstance(i, dict)}
    keys = None
    flash_ok = _flash_ok(ctx)
    if r.assets:
        items, landing, why = recipes.build(
            name, plan, ids=ids, accent=_accent(edl), flash_ok=flash_ok,
            edl=edl, variant_keys=recipes.STILLS_PLACEHOLDER)
        if not items:
            return f"NO CHANGE: {name} does not fit here ({why})."
        keys = _variant_keys(ctx, edl, landing["t"])
    items, landing, why = recipes.build(name, plan, ids=ids,
                                        accent=_accent(edl),
                                        variant_keys=keys,
                                        flash_ok=flash_ok, edl=edl)
    if not items:
        return f"NO CHANGE: {name} does not fit here ({why})."
    ops = _ops(items)
    if r.flash and not flash_ok:
        # automatic proposals stay flash-safe: judged on the whole edit
        try:
            import copy
            import edit_batch
            import flash_safety
            probe = edit_batch.apply_batch(copy.deepcopy(edl), ops,
                                           ctx.duration or None, set())
            fs = flash_safety.assess(probe, getattr(ctx, "index", None))
            if fs.get("over_limit"):
                return (f"REJECTED: {name} would put {fs['peak_per_s']} "
                        "full-frame flashes in one second next to the "
                        "flashes already in this edit (the safe limit is "
                        f"{fs['max_per_s']} unless the user asks for "
                        "flashing). Pick another recipe or thin the "
                        "existing flashes.")
        except Exception:  # noqa: BLE001 — the export still judges it
            pass
    res = w.commit(ctx, ops, f"recipe {name}: {why}")
    measuring = ""
    if res.startswith("EDL v") and r.matte:
        # ask for the person matte over the recipe's windows (never waits)
        try:
            from timeline import Timeline
            import tools_ext.subject_tools as st
            new = ctx.latest_edl()["json"]
            tl = Timeline(new.get("keep") or [], new.get("inserts") or [],
                          new.get("speed") or [])
            measuring = st._ensure_matte(
                ctx, new, tl, [(float(i["start"]), float(i["end"]))
                               for i in items if i.get("start") is not None])
        except Exception:  # noqa: BLE001 — analysis never fails a write
            measuring = ""
    if res.startswith("EDL v"):
        res += (f"\nRECIPE {name}: {why}. Ids: "
                + ", ".join(i["id"] for i in items)
                + ". Look at the frames around it before keeping it.")
        if measuring:
            res += "\n" + measuring
    return res


def _variant_keys(ctx, edl, t):
    """Registered variant stills of the frame at program second ``t`` (the
    lab scan's images), or None (the scan then strobes the frame itself)."""
    try:
        import variants
        from timeline import Timeline
        tl = Timeline(edl.get("keep") or [[0, ctx.duration]],
                      edl.get("inserts") or [], edl.get("speed") or [])
        src = tl.out_to_src(float(t))
        if src is None:
            return None
        return variants.make_variant_set(
            ctx, src, ["photo", "negative", "line", "blueprint", "thermal"])
    except Exception as exc:  # noqa: BLE001
        print(f"[recipe] lab scan stills skipped: {str(exc)[:160]}",
              flush=True)
        return None


TOOL_SPECS = {
    "suggest_recipes": (
        suggest_recipes,
        "READ: the creative MOVES this cut earns — each a whole editor's "
        "move solved on the right word, not a single effect: "
        "mono_until_turn (B&W until the turn, colour lands ON the word), "
        "colour_kept_payoff (grey world, the speaker in colour), "
        "silhouette_hold, negative_hits (1-frame negatives into the "
        "payoff), white_silhouette (the speaker flashes white on the "
        "payoff), lab_scan (registered variant strobe), thermal_flip, "
        "light_figure (the speaker as light on black), echo_trail, "
        "room_dims (the room dims round him on the turn), world_negative "
        "(the world inverts round him on a hard negation), colour_drain "
        "(a build drains to grey, colour snaps back on the payoff). Ranked "
        "by story fit x freshness: a move the other "
        "shorts of the same batch used ranks lower. Each comes with the "
        "word it lands on, why, a reference exemplar and the exact "
        "apply_edit_batch ops. Silent, zoom-free, flash-safe.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "max_results": {"type": "integer"}}),
    "apply_recipe": (
        apply_recipe,
        "Write ONE recipe from suggest_recipes, solved on this cut (or on "
        "the spoken moment nearest at). Once per short; flash-safe unless "
        "the user asked for flashing; B&W only as a device. Returns the "
        "item ids (rc_*), removable with the department removers.",
        {"name": {"type": "string", "enum": [
            "mono_until_turn", "colour_kept_payoff", "silhouette_hold",
            "negative_hits", "white_silhouette", "lab_scan", "thermal_flip",
            "light_figure", "echo_trail", "room_dims", "world_negative",
            "colour_drain"]},
         "at": {"type": "number"}}),
}

REQUIRED_ARGS = {"apply_recipe": ["name"]}
COMPACT = {
    "suggest_recipes": "READ: whole creative moves this cut earns (story "
                       "fit x freshness across the batch), each with its "
                       "word, why, exemplar and apply_edit_batch ops.",
    "apply_recipe": "Write one recipe (once per short), solved on the cut "
                    "or near at.",
}
