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
  set_export_settings  WRITE: the exported file's size or bitrate target
                       (EDL export; renderer.fit_export_size measures the
                       delivered file and re-encodes it until it fits).

Wave 4's pack verb (packs E, F, G, I, J) was merged into the one pack verb,
``apply_format_pack`` (department ``direction``, every genre A-J, one pack
schema in ``formats``) by the engines release.
"""

import json

DEPARTMENT = "delivery"
VERBS = ("deliver_aspects", "retypeset_language", "set_export_settings")


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


def _export_number(value):
    """A size or rate the model wrote: a number, or a string such as '4.5'
    or '4.5 GB' (machine syntax: the leading number)."""
    import schemas as _schemas_mod
    return _schemas_mod.export_number(value)


def export_ceiling_line(edl):
    """The measured ceiling of an EDL's export, in one plain line: the film's
    length with the end card, the video bitrate the export is held under and
    the largest file that makes. "" when the EDL names no export target.
    One source for every surface (v1 set_export_settings, harness v2's
    export op, MCP), so each reply can report the size in numbers."""
    import renderer as _r
    import schemas as _schemas_mod
    target = (edl or {}).get("export") if isinstance(edl, dict) else None
    if not isinstance(target, dict) or not _r.export_target(edl):
        return ""
    outro = _r.outro_seconds(False)
    dur = float(_schemas_mod.program_duration(edl)) + outro
    if dur <= 0:
        return ""
    vk = _r.export_video_kbps(edl, dur)
    ceiling = ((vk + _r.EXPORT_AUDIO_KBPS) * 1000 / 8 * dur
               * (1 + _r.EXPORT_CONTAINER_HEADROOM)
               + _r.EXPORT_CONTAINER_FIXED_BYTES)
    mins = int(dur // 60)
    return (
        f"Measured for this film ({mins} min {dur - mins * 60:.0f} s"
        f"{' with the end card' if outro else ''}): the export's picture is held at or under {vk} "
        f"kb/s with {_r.EXPORT_AUDIO_KBPS} kb/s sound, so the file is at "
        f"most {ceiling / 1e9:.2f} GB"
        + (f" (the target is {float(target['max_size_mb']) / 1000:g} GB)"
           if target.get("max_size_mb") else "") + ".")


def set_export_settings(ctx, max_size_gb=None, max_size_mb=None,
                        video_kbps=None, clear=False):
    """The exported file's size or bitrate target (16 Nov 2026, p5857:
    "make the download less than 4.5GB so it can fit onto a DVD"; the editor
    looked for export_final, set_export_settings and set_bitrate and found
    nothing, so the size could never be met).

    Writes EDL ``export``. The final export encodes the picture under the
    bitrate that fits the film's length (sound and container counted), then
    measures the delivered file and re-encodes it in two passes until it
    fits (renderer.fit_export_size). Previews are unchanged."""
    import read_tools
    import schemas as _schemas_mod
    edl = dict(ctx.latest_edl()["json"])
    if clear:
        if not edl.get("export"):
            return ("NO CHANGE: the export has no size or bitrate target; "
                    "it exports at Valmera's normal quality.")
        from tools_ext import _write
        return _write.commit(
            ctx, [{"action": "set", "layer": "export", "value": None}],
            "export size/bitrate target removed") + (
            "\nThe export is back to Valmera's normal quality settings.")
    gb, mb, kbps = (_export_number(max_size_gb), _export_number(max_size_mb),
                    _export_number(video_kbps))
    notes = []
    if gb and mb:
        notes.append(f"both max_size_gb ({gb:g}) and max_size_mb ({mb:g}) "
                     f"were given: the smaller, {min(gb * 1000, mb):g} MB, "
                     "is the target")
    size_mb = min([v for v in (gb * 1000 if gb else None, mb) if v] or [0])
    target = {}
    if size_mb > 0:
        target["max_size_mb"] = round(max(size_mb,
                                          _schemas_mod.EXPORT_MIN_SIZE_MB), 3)
    if kbps and kbps > 0:
        target["video_kbps"] = int(max(kbps,
                                       _schemas_mod.EXPORT_MIN_VIDEO_KBPS))
    if not target:
        # Nothing to aim at was named: report what the export is today.
        cur = edl.get("export") or {}
        now = (f"max {cur['max_size_mb'] / 1000:g} GB"
               if cur.get("max_size_mb") else "")
        if cur.get("video_kbps"):
            now += (", " if now else "") + f"video {cur['video_kbps']} kb/s"
        return ("NO CHANGE: name max_size_gb (e.g. 4.5 for a DVD), "
                "max_size_mb or video_kbps. The export today: "
                + (now or "Valmera's normal quality, no size target") + ".")
    what = []
    if target.get("max_size_mb"):
        what.append(f"file at most {target['max_size_mb'] / 1000:g} GB")
    if target.get("video_kbps"):
        what.append(f"video at most {target['video_kbps']} kb/s")
    from tools_ext import _write
    res = _write.commit(ctx, [{"action": "set", "layer": "export",
                               "value": target}],
                        "export target: " + ", ".join(what))
    if not (res.startswith("EDL v") or res.startswith("NO CHANGE")):
        return res
    lines = [res]
    lines.extend(f"NOTE: {n}" for n in notes)
    try:
        ceiling_line = export_ceiling_line(dict(ctx.latest_edl()["json"]))
    except Exception:  # noqa: BLE001 - the measured line is optional
        ceiling_line = ""
    if ceiling_line:
        lines.append(ceiling_line)
    try:
        last = ctx.db.run(read_tools.latest_final_asset, ctx.project_id)
    except Exception:  # noqa: BLE001
        last = None
    if last and last.get("bytes"):
        meta = last.get("meta") or {}
        lines.append(f"The last export (version {meta.get('edl_version')}) "
                     f"measured {int(last['bytes']) / 1e9:.2f} GB.")
    lines.append(
        "When the customer exports, the delivered file is measured and, if "
        "it would land over the target, re-encoded in two passes until it "
        "fits; its measured size is stored with the export. Tell the "
        "customer the target and the ceiling above in plain numbers; the "
        "preview is not the export and keeps its own small size.")
    return "\n".join(lines)


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
    "set_export_settings": (
        set_export_settings,
        "WRITE: the EXPORTED FILE's size or bitrate target: 'make the "
        "download under 4.5GB for a DVD', 'a smaller file', 'under 2 GB for "
        "the upload limit', 'set the bitrate'. max_size_gb / max_size_mb is "
        "the whole MP4's ceiling (GB = 10^9 bytes; a single-layer DVD holds "
        "4.7); video_kbps caps the picture's bitrate. The export encodes "
        "under the bitrate that fits the film's length, measures the "
        "delivered file and re-encodes it in two passes until it fits. The "
        "result gives the measured ceiling for this film: report it. "
        "clear=true removes the target. Previews are unchanged.",
        {"max_size_gb": {"type": "number"},
         "max_size_mb": {"type": "number"},
         "video_kbps": {"type": "integer"},
         "clear": {"type": "boolean"}}),
}
REQUIRED_ARGS = {"retypeset_language": ["lang"]}
WRITE_TOOLS = {"deliver_aspects", "retypeset_language", "set_export_settings"}
COMPACT = {
    "deliver_aspects": "READ/WRITE: re-solve for 16:9/4:3/1:1/4:5/9:16; "
                       "apply=<ratio> rewrites to that master.",
    "retypeset_language": "WRITE: another language from the user's "
                          "translation/SRT/mapping; timing kept.",
    "set_export_settings": "WRITE: the exported file's size (max_size_gb, "
                           "e.g. 4.5 for a DVD) or video_kbps; measured and "
                           "fitted on export.",
}
