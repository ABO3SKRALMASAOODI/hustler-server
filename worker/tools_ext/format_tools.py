"""Department ``delivery``: track 4E's verbs (INTERFACES_W4.md §4).

  apply_format         WRITE: a genre's format pack (E UI explainer, F
                       visual essay, G sizzle, I documentary, J edit about
                       the edit): sets the pack's aspect and returns its
                       craft brief (structure, device palette and budgets,
                       type voice, never-rules); the ledger then counts
                       against the pack's budgets. A pack never picks music,
                       a zoom or a sound.
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
"""

import json

DEPARTMENT = "delivery"
VERBS = ("apply_format", "deliver_aspects", "retypeset_language")


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


def apply_format(ctx, format, aspect=True):
    import format_packs
    try:
        p = format_packs.load(format)
    except ValueError as e:
        return _rejected(str(e))
    edl, _v = _edl(ctx)
    out = format_packs.brief(p["code"])
    if aspect and p.get("aspect"):
        cur = (edl.get("frame") or {}).get("ratio")
        if cur != p["aspect"]:
            import aspects
            new, notes = aspects.solve(edl, p["aspect"],
                                       getattr(ctx, "index", None))
            res = _commit_full(ctx, new, f"apply_format {p['code']}: "
                               f"{p['aspect']}")
            if str(res).startswith("REJECTED"):
                return res
            out = res + "\n" + out + "\n  " + "; ".join(notes)
    try:
        import taste
        led = taste.ledger(ctx.latest_edl()["json"], p.get("intensity")
                           or "designed", fmt=p["code"])
        out += f"\nLEDGER ({p['code']}): {led.line()}"
    except Exception:  # noqa: BLE001
        pass
    return out


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
        solved[r] = new
        lines.append(f"  {r}: " + "; ".join(notes))
    if apply:
        new = solved.get(apply) or aspects.solve(edl, apply, idx)[0]
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
    "apply_format": (
        apply_format,
        "WRITE: a format pack — E UI/product explainer, F music-driven "
        "visual essay, G event sizzle, I documentary case study, J edit "
        "about the edit: sets its aspect and returns its craft brief "
        "(structure, device palette, budgets, type voice, never-rules) "
        "for you to follow with taste; the device ledger then counts "
        "against its budgets. Never picks music, a zoom or a sound.",
        {"format": {"type": "string", "enum": ["E", "F", "G", "I", "J"]},
         "aspect": {"type": "boolean"}}),
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
REQUIRED_ARGS = {"apply_format": ["format"],
                 "retypeset_language": ["lang"]}
WRITE_TOOLS = {"apply_format", "deliver_aspects", "retypeset_language"}
COMPACT = {
    "apply_format": "WRITE: format pack E|F|G|I|J: aspect + craft brief "
                    "(structure, palette, budgets, never-rules).",
    "deliver_aspects": "READ/WRITE: re-solve for 16:9/4:3/1:1/4:5/9:16; "
                       "apply=<ratio> rewrites to that master.",
    "retypeset_language": "WRITE: another language from the user's "
                          "translation/SRT/mapping; timing kept.",
}
