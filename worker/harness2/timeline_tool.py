"""timeline(): the edit, measured (HARNESS_V2.md 2.2).

Version, length and kept share, what plays where on the output clock
(paged), every layer's items, the measured whole-film facts, the last render
of this version, the version history, and with ``schema='ops'`` every op's
shape so the long tail is learned on demand.
"""
import json

import db as dbx
import read_tools
import timeline as timeline_mod
from score import asks

from harness2 import envelope
from harness2 import measure as measure_mod

PAGE_LINES = 120


def _row(ctx, version):
    if version is None:
        return ctx.latest_edl()
    try:
        return ctx.db.run(dbx.get_edl_version, ctx.project_id, int(version))
    except Exception:  # noqa: BLE001
        return None


def plays(edl, name_of=None, rng=None, limit=PAGE_LINES):
    """One line per piece on the output clock; (lines, total pieces)."""
    try:
        blocks = timeline_mod.program_blocks(edl)
    except Exception:  # noqa: BLE001
        return [], 0
    lo, hi = None, None
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        try:
            lo, hi = float(rng[0]), float(rng[1])
        except (TypeError, ValueError):
            lo = hi = None
    lines = []
    for b in blocks:
        if lo is not None and (b["out_end"] <= lo or b["out_start"] >= hi):
            continue
        head = (f"{measure_mod.clock_text(b['out_start'])}-"
                f"{measure_mod.clock_text(b['out_end'])}")
        if b["kind"] == "footage":
            lines.append(f"{head}  source {b['src_start']:.2f}-"
                         f"{b['src_end']:.2f}")
        else:
            name = None
            if name_of:
                try:
                    name = name_of(b.get("asset_key"))
                except Exception:  # noqa: BLE001
                    name = None
            label = name or str(b.get("asset_key") or "?").split("/")[-1]
            lines.append(f"{head}  {b.get('media') or 'clip'} '{label}' "
                         f"[{b.get('id')}]"
                         + (f" from {b['clip_start_s']:g}s"
                            if b.get("clip_start_s") else ""))
    total = len(lines)
    if total > limit:
        lines = lines[:limit] + [f"... {total - limit} more pieces: pass "
                                 "range=[from_s, to_s] for the rest"]
    return lines, len(blocks)


def _last_render(ctx, version):
    """The newest preview render of exactly this version, as measures."""
    shown = getattr(ctx, "last_preview", None) or {}
    if str(shown.get("edl_version")) == str(version):
        return shown
    try:
        row = ctx.db.run(read_tools.find_render_asset, ctx.project_id,
                         "preview", int(version))
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return None
    meta = row.get("meta") or {}
    return {"edl_version": version, "duration_s": row.get("duration_s"),
            "audio_qc": meta.get("audio_qc"), "storage_key":
            row.get("storage_key"), "screening_pages":
            meta.get("screening_pages")}


def _versions(ctx, limit=20):
    try:
        rows = ctx.db.run(dbx.edl_history, ctx.project_id, limit) or []
    except Exception:  # noqa: BLE001
        rows = []
    lines = []
    for r in rows:
        note = ""
        manifest = (getattr(ctx, "change_manifests", None) or {}).get(
            int(r["version"]))
        if isinstance(manifest, dict):
            note = str(manifest.get("description") or "")[:120]
        when = r.get("created_at")
        when = when.isoformat(timespec="minutes") if hasattr(
            when, "isoformat") else (when or "")
        lines.append(f"v{r['version']}  {r.get('created_by') or '?'}  "
                     f"{when}  {note}".rstrip())
    return lines


def timeline(ctx, version=None, range=None, versions=False, schema=None):
    if schema:
        from harness2 import schema_ops
        return envelope.read("timeline", schema_ops.describe(schema))
    row = _row(ctx, version)
    if not row:
        head = ctx.latest_edl()
        return envelope.read(
            "timeline", f"there is no version {version}; versions run "
            f"1-{head['version']}", coverage="")
    edl = row["json"] or {}
    source_s = float(getattr(ctx, "duration", 0.0) or 0.0) or None
    preview = _last_render(ctx, row["version"])
    measured = measure_mod.edl_measure(edl, source_s, preview)
    name_of = None
    try:
        name_of = read_tools.program_name_of(ctx)
    except Exception:  # noqa: BLE001
        name_of = None
    lines, pieces = plays(edl, name_of, range)
    body = [f"version v{row['version']} (written by "
            f"{row.get('created_by') or 'agent'})",
            f"film {measure_mod.clock_text(measured['program_s'])}"
            + (f", source {measure_mod.clock_text(source_s)}, kept "
               f"{100 * (measured['kept_share'] or 0):.1f}%"
               if source_s else "")
            + f", {pieces} piece(s)"]
    if lines:
        body.append("what plays where (output clock):")
        body.extend("  " + ln for ln in lines)
    try:
        digest = asks.digest(edl)
        digest_text = digest[0] if isinstance(digest, tuple) else digest
    except Exception:  # noqa: BLE001
        digest_text = ""
    if digest_text:
        body.append("layers:")
        body.append(str(digest_text)[:6000])
    try:
        whole = asks.measured(edl, source_s=source_s, preview=preview,
                              index=getattr(ctx, "index", None))
        body.append("whole film: " + json.dumps(
            {k: whole.get(k) for k in (
                "share_of_film_moving", "share_of_film_under_pictures",
                "pictures_over_the_film_count", "transitions", "grade",
                "sound", "upload_kept_in_order")},
            default=str)[:1500])
    except Exception:  # noqa: BLE001
        pass
    if version is None or int(row["version"]) == int(ctx.latest_edl()["version"]):
        # Who made each part of the film (the upload, the automatic first cut, earlier requests,
        # this turn), measured from the saved versions: the same reading v1's state block gives.
        try:
            import edit_provenance
            lineage_rows, jobs = ctx.db.run(dbx.edl_lineage, ctx.project_id)
            origin = edit_provenance.from_lineage(
                lineage_rows, jobs, index=getattr(ctx, "index", None) or {},
                source_s=source_s,
                turn_versions=list(getattr(ctx, "versions_written", None) or ()))
        except Exception:  # noqa: BLE001
            origin = ""
        if origin:
            body.append(origin)
    if versions:
        body.append("versions (newest first):")
        body.extend("  " + ln for ln in _versions(ctx))
    coverage = (f"output {measure_mod.clock_text(range[0])}-"
                f"{measure_mod.clock_text(range[1])}" if isinstance(
                    range, (list, tuple)) and len(range) == 2
                else "the whole film")
    return envelope.read("timeline", "\n".join(body), measured=measured,
                         coverage=coverage, data={"version": row["version"]})
