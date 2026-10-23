"""The ending gate: no held picture right before the end card.

Owner rule (22 Oct 2026, after seeing a stall in every short): the payoff
holds 0.6-1.2 s on LIVE footage (a reaction, a nod, a breath), or the short
ends on the last word. Never a freeze-frame, a slowed or time-effect tail, or
a still image as the last thing before the end card.

The STALL BEFORE END CARD check existed (worker/render_qc.boundary_stall,
motion_critic.lens_ending, motion_qa), yet on 23 Oct export_final shipped
JRE #2054 Part 2 finals p07/p16/p17/p36/p44 (projects 3824, 3833, 3834,
3853, 3861) that hold 0.6-0.9 s frozen frames before the card. Why: every
one ended on an add_freeze_frame payoff hold (an image insert with
``hold``), export_final only read the version's stored verification record,
and those records were written (17 Oct, and 22 Oct before 18:56 UTC) by a
picture QC that did not measure the join yet. The finals' own picture QC
then named the stall ("the held frame 'ins1' (31.71-33.01s) freezes the
picture before the card") after the file existed.

So export_final reads the EDL itself (``edl_holds``: a fact of the edit, no
justification clears it) and the newest render's measured stall rows
(``rendered_holds``: a justification on the version's verification record
may clear only a held run the QC blames on the footage, i.e. a false
positive on genuinely moving source).

Pure: worker/timeline.py is passed in (routes.video loads it as
``wtimeline``), so this needs no worker import and no database.
"""

TAIL_S = 1.5          # the programme seconds before the card that are read
LIVE_MIN_S = 0.6      # live picture that must follow a hold before the card
SLOW_BELOW = 0.99     # a speed factor under this is a slowed tail
# add_time_effect kinds that repeat or slow frames (echo and reverse move).
HOLDING_TIME_KINDS = frozenset({"posterize", "stutter", "remap",
                                "interpolate", "ramp"})
# render_qc._stall_cause codes for a HELD run. 'hold', 'still' and 'slow'
# are the edit's own; 'footage' is the source not moving (or a false
# positive on a quiet shot); 'join' is the renderer's and is reported, not
# the editor's to repair.
EDIT_CAUSES = frozenset({"hold", "still", "slow"})
JUSTIFIABLE_CAUSES = frozenset({"footage"})
STALL_TEXT = "STALL BEFORE END CARD"


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def edl_holds(edl, timeline):
    """[{"what", "id", "t0", "t1", "fix"}] — what the EDL itself holds or
    slows where the programme hands over to the end card: anything that
    reaches past (programme end - LIVE_MIN_S), i.e. leaves less than
    LIVE_MIN_S of live picture before the card. [] when the tail is live."""
    edl = edl if isinstance(edl, dict) else {}
    keep = [list(k) for k in (edl.get("keep") or [])
            if isinstance(k, (list, tuple)) and len(k) >= 2]
    inserts = [i for i in (edl.get("inserts") or []) if isinstance(i, dict)]
    speed = [s for s in (edl.get("speed") or []) if isinstance(s, dict)]
    if not keep:
        # A canvas programme (images and clips, no main video) has no live
        # footage to end on; its last picture is the user's to choose.
        return []
    try:
        tl = timeline.Timeline(keep, inserts, speed)
        windows = timeline.insert_windows(inserts, tl)
    except Exception:  # noqa: BLE001 — an unreadable EDL is the render's to refuse
        return []
    end = float(tl.out_duration)
    if end <= 0:
        return []
    live_from = end - LIVE_MIN_S
    out = []

    def add(what, iid, a, b, fix):
        out.append({"what": what, "id": iid, "t0": round(a, 2),
                    "t1": round(min(b, end), 2), "fix": fix})

    # Freeze-frames, payoff holds, stills, title cards, colour screens:
    # every image insert (they are pictures that do not move).
    for it in inserts:
        if it.get("id") not in windows:
            continue
        a, b = windows[it["id"]]
        if b <= live_from + 1e-3 or a >= end:
            continue
        hold = isinstance(it.get("hold"), dict)
        key = str(it.get("asset_key") or "")
        if it.get("kind") == "image" or hold:
            if hold or "/freeze-" in key:
                what = "the held frame (add_freeze_frame)"
            else:
                what = "the still image insert"
            add(what, it.get("id"), a, b,
                f"remove_insert(id='{it.get('id')}')")
    # A full-frame still overlay (add_freeze_frame audio_mode='continue', a
    # still B-roll cutaway): the programme runs on under a frozen picture.
    for ov in edl.get("overlays") or []:
        if not isinstance(ov, dict) or ov.get("kind") != "image":
            continue
        key = str(ov.get("asset_key") or "")
        if ov.get("fit") != "cover" and "/freeze-" not in key:
            continue
        if ov.get("screen"):
            continue                    # pinned into the moving footage
        a = _f(ov.get("start"))
        b = a + _f(ov.get("duration_s"))
        if b <= live_from + 1e-3 or a >= end:
            continue
        what = ("the frozen-frame overlay (add_freeze_frame)"
                if "/freeze-" in key else "the full-frame still overlay")
        add(what, ov.get("id"), a, b, f"remove_overlay(id='{ov.get('id')}')")
    # A slowed tail: set_speed / set_speed_ramp spans under 1x.
    for i, pcs in enumerate(tl.pieces):
        t = float(tl.offsets[i])
        for ps, pe, f in pcs:
            n = (pe - ps) / f
            if f < SLOW_BELOW and t + n > live_from + 1e-3 and t < end:
                span = next((s for s in speed
                             if _f(s.get("start")) <= ps + 1e-3
                             and _f(s.get("end")) >= pe - 1e-3), {})
                sid = span.get("id")
                add(f"the {f:g}x slowed tail (set_speed)", sid, t, t + n,
                    f"remove_speed(id='{sid}')" if sid else
                    "remove the slow speed span over the ending")
            t += n
    # A time effect that repeats or slows frames over the ending.
    for fx in edl.get("fx") or []:
        if not isinstance(fx, dict) or fx.get("type") != "time":
            continue
        if str(fx.get("kind") or "") not in HOLDING_TIME_KINDS:
            continue
        a, b = _f(fx.get("start")), _f(fx.get("end"))
        if b <= live_from + 1e-3 or a >= end:
            continue
        add(f"the {fx.get('kind')} time effect (add_time_effect)",
            fx.get("id"), a, b, f"remove_depth_time(ids=['{fx.get('id')}'])")
    return out


def justified_stall(record):
    """True when the version's verification record carries a justification
    for a STALL BEFORE END CARD finding."""
    record = record if isinstance(record, dict) else {}
    ids = {str(j.get("finding_id")) for j in record.get("justifications") or []
           if isinstance(j, dict) and j.get("finding_id")}
    if not ids:
        return False
    for f in record.get("findings") or []:
        if isinstance(f, dict) and str(f.get("finding_id")) in ids and \
                STALL_TEXT in str(f.get("message") or ""):
            return True
    return False


def rendered_holds(picture_qc, record=None):
    """[{"what", "t0", "t1", "cause", "words"}] — the held runs a render's own
    picture QC measured before the card (render_qc.boundary_stall rows,
    stored as picture_qc['stall']) that block an export: the edit's causes
    always, a 'footage' run unless the verification record justifies the
    stall finding (a false positive on genuinely moving source). The
    renderer's join is never the editor's to fix and is not returned."""
    rows = (picture_qc or {}).get("stall") if isinstance(picture_qc, dict) \
        else None
    out = []
    justified = justified_stall(record)
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        kind, a, b, code, words = row[:5]
        if kind != "held":
            continue
        if code in EDIT_CAUSES or (code in JUSTIFIABLE_CAUSES
                                   and not justified):
            out.append({"what": "a held picture", "t0": round(_f(a), 2),
                        "t1": round(_f(b), 2), "cause": code,
                        "words": str(words or "")})
    return out


def refusal(project_id, version, holds, measured, last_keep=None):
    """The export_final refusal: what holds, and the exact fix."""
    lines = []
    for h in holds:
        fix = h["fix"]
        if fix.startswith("remove_") and "(" in fix:
            fix = fix.replace("(", f"(project_id={project_id}, ", 1)
        lines.append(f"  - {h['what']} '{h['id']}' ({h['t0']:.2f}-"
                     f"{h['t1']:.2f}s) is the last picture before the end "
                     f"card: {fix}")
    for m in measured:
        lines.append(f"  - the render measured {m['t1'] - m['t0']:.2f}s of "
                     f"a held picture ({m['t0']:.2f}-{m['t1']:.2f}s, cause "
                     f"'{m['cause']}'): {m['words']}")
    keep = ""
    if last_keep:
        keep = (f" The last keep segment is source {last_keep[0]:g}-"
                f"{last_keep[1]:g}s: find the last word's end with "
                "get_kept_transcript, then keep_segments with that segment "
                f"ending {LIVE_MIN_S:g}-1.2 s after it (a reaction, a nod, "
                "a breath), or ending on the last word.")
    clear = ("A justification cannot clear a hold the EDL makes."
             if holds else
             "If the footage there genuinely moves (a false positive on a "
             "quiet shot), justify_verification_findings with the frame "
             "evidence clears this measured run, never an EDL hold.")
    return (f"PROJECT {project_id}: EDL v{version} ends on a held picture "
            "before the end card, so no export was started (owner rule: the "
            f"payoff holds {LIVE_MIN_S:g}-1.2 s on LIVE footage, or the short "
            "ends on the last word; never a freeze-frame, a slowed or "
            "time-effect tail, or a still before the card):\n"
            + "\n".join(lines)
            + "\nFix: remove the hold, then extend the keep onto live footage "
            f"{LIVE_MIN_S:g}-1.2 s after the last word, or end on the last "
            f"word.{keep} Render a preview of the new version and export "
            "that version. " + clear)
