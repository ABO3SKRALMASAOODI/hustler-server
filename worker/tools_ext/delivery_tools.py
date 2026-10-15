"""Department ``delivery``: track 4E's verbs (INTERFACES_W4.md §4).

  deliver_aspects      READ / WRITE: the edit re-solved for other aspects
                       (16:9, 4:3, 1:1, 4:5, 9:16): UI scenes re-laid out,
                       measurements dropped so the render measures again,
                       overlays re-fitted with their own proportions. With
                       ``apply`` it rewrites the edit to that aspect (one
                       master per export).
  retypeset_language   WRITE: the same edit in another language from the
                       USER's translation (per phrase), subtitle file or
                       text mapping — never machine-translated; every
                       window, cut and device keeps its time.

Wave 4's pack verb (packs E, F, G, I, J) was merged into the one pack verb,
``apply_format_pack`` (department ``direction``, every genre A-J, one pack
schema in ``formats``) by the engines release.
"""

import json

DEPARTMENT = "delivery"
VERBS = ("deliver_aspects", "retypeset_language")


def _rejected(msg):
    return f"REJECTED: {msg}"


def _edl(ctx):
    row = ctx.latest_edl()
    return row["json"], row["version"]


def _commit_full(ctx, new_edl, summary):
    """Write a re-solved EDL through the one validation path: every layer
    that changed becomes an apply_edit_batch ``set`` op (the same gates a
    stale client's batch meets)."""
    import edit_batch
    from tools_ext import _write
    old, _v = _edl(ctx)
    ops = []
    for layer in sorted(edit_batch.LAYERS):
        if json.dumps(old.get(layer), sort_keys=True, default=str) != \
                json.dumps(new_edl.get(layer), sort_keys=True, default=str):
            ops.append({"action": "set", "layer": layer,
                        "value": new_edl.get(layer)})
    if not ops:
        return "NO CHANGE: the edit already is this"
    return _write.commit(ctx, ops, summary)


# Graphics that re-solve themselves at render (typeset lockups: layoutdoc.
# refresh_faces; UI scenes: aspects._resolve_ui) or ride an anchor are left
# to their own solvers.
_SELF_SOLVED = ("typeset", "ui_scene")


def keep_out_master(ctx, edl):
    """The write path's face and safe-area keep-out (motion_tools) for every
    movable graphic of a re-solved master, at ITS canvas: a frame change
    drops each graphic's measurements, and nothing at render moves a
    graphic off a face (AT-4E-1: the hook title sat on the speaker's face
    in every master). Mutates ``edl``; returns [(id, note)] of the moved or
    flagged graphics. A graphic that cannot be measured stays as placed."""
    import motion_tools
    out = []
    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("template") in _SELF_SOLVED \
                or m.get("anchor") or m.get("allow_face_overlap") \
                or m.get("layer") == "behind_subject":
            continue
        try:
            note, _where = motion_tools._keep_out_estimated(ctx, edl, m)
        except Exception as e:  # noqa: BLE001 — a measurement never blocks
            print(f"[delivery] keep-out skipped for {m.get('id')}: "
                  f"{str(e)[:160]}", flush=True)
            continue
        if note and note.strip():
            out.append((m.get("id"), note.strip().splitlines()[0]))
    return out


def _keep_out_lines(moved):
    return [f"  keep-out {mid}: {note[:220]}" for mid, note in moved]


def deliver_aspects(ctx, ratios=None, apply=None):
    import aspects
    edl, _v = _edl(ctx)
    idx = getattr(ctx, "index", None) or {}
    if isinstance(ratios, str):
        ratios = [r.strip() for r in ratios.split(",") if r.strip()]
    ratios = ratios or list(aspects.RATIOS)
    bad = [r for r in ratios if r not in aspects.RATIOS]
    if bad:
        return _rejected(f"ratios are {', '.join(aspects.RATIOS)}")
    if apply is not None and apply not in aspects.RATIOS:
        return _rejected(f"apply is one of {', '.join(aspects.RATIOS)}")
    lines = []
    solved = {}
    for r in ratios:
        try:
            new, notes = aspects.solve(edl, r, idx)
        except ValueError as e:
            return _rejected(str(e))
        moved = keep_out_master(ctx, new)
        solved[r] = new
        lines.append(f"  {r}: " + "; ".join(notes))
        lines += _keep_out_lines(moved)
    if apply:
        new = solved.get(apply)
        if new is None:
            new = aspects.solve(edl, apply, idx)[0]
            keep_out_master(ctx, new)
        res = _commit_full(ctx, new, f"deliver_aspects: {apply} master")
        return res + "\nRE-SOLVED:\n" + "\n".join(lines)
    return ("ASPECTS (nothing written; apply=<ratio> rewrites the edit to "
            "that master, export, then the next):\n" + "\n".join(lines))


def retypeset_language(ctx, lang, translation=None, srt=None, mapping=None,
                       list_phrases=False):
    import retypeset_lang
    edl, _v = _edl(ctx)
    idx = getattr(ctx, "index", None) or {}
    if list_phrases:
        ph = retypeset_lang.phrases(edl, idx)
        return ("PHRASES (write one translated line per phrase, in order):\n"
                + "\n".join(f"  {p['i']}. [{p['t0']:.2f}-{p['t1']:.2f}s] "
                            f"{p['text']}" for p in ph))
    for name in ("translation", "mapping"):
        v = locals()[name]
        if isinstance(v, str) and v.strip().startswith(("[", "{")):
            try:
                v = json.loads(v)
            except ValueError:
                return _rejected(f"{name} is not valid JSON")
            if name == "translation":
                translation = v
            else:
                mapping = v
    try:
        new, notes = retypeset_lang.retypeset(edl, idx, lang,
                                              translation=translation,
                                              srt=srt, mapping=mapping)
    except ValueError as e:
        return _rejected(str(e))
    res = _commit_full(ctx, new, f"retypeset_language {lang}")
    if str(res).startswith("REJECTED"):
        return res
    return res + "\n  " + "\n  ".join(notes) + (
        "\n  every window, cut and device keeps its time; render_preview "
        "and look_at the longest lines (legibility)")


TOOL_SPECS = {
    "deliver_aspects": (
        deliver_aspects,
        "READ/WRITE: re-solve this edit for other aspects (16:9, 4:3, 1:1, "
        "4:5, 9:16) — UI scenes re-laid out, graphics and captions "
        "measured again at render, overlays re-fitted with their own "
        "proportions, the crop aim kept (source space). Without apply it "
        "lists what each master changes; apply=<ratio> rewrites the edit "
        "to that master (export it, then the next).",
        {"ratios": {"type": "array", "items": {"type": "string"}},
         "apply": {"type": "string"}}),
    "retypeset_language": (
        retypeset_language,
        "WRITE: the same edit in another language from what the USER gives "
        "(never machine-translated): translation = one line per spoken "
        "phrase (list_phrases=true lists them), srt = their subtitle file, "
        "mapping = {on-screen text: translation}. Captions keep each "
        "phrase's spoken window; graphics swap their text; no timing "
        "moves.",
        {"lang": {"type": "string"},
         "translation": {"description": "list of lines or {phrase: line}"},
         "srt": {"type": "string"}, "mapping": {"type": "object"},
         "list_phrases": {"type": "boolean"}}),
}
REQUIRED_ARGS = {"retypeset_language": ["lang"]}
WRITE_TOOLS = {"deliver_aspects", "retypeset_language"}
COMPACT = {
    "deliver_aspects": "READ/WRITE: re-solve for 16:9/4:3/1:1/4:5/9:16; "
                       "apply=<ratio> rewrites to that master.",
    "retypeset_language": "WRITE: another language from the user's "
                          "translation/SRT/mapping; timing kept.",
}
