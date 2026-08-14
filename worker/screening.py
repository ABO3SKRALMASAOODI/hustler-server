"""Plan compact, high-information evidence for reviewing a finished edit.

A uniform 3x3 contact sheet is useful for catastrophic render failures, but it
is a poor proxy for editorial judgment: a two-second B-roll choice, title,
camera move, or transition can fall entirely between its nine samples.  This
module chooses frames from the *authored program* as well as from the clock.
It is pure timeline math so the executor can build the sheets without another
model call, and the exact evidence contract can be regression-tested.

The frame ceiling is an operational perception budget, not a creative rule.
Callers configure it and the planner spends it on whole-program coverage plus
the edit's actual decisions instead of silently judging only the opening.
"""

from timeline import Timeline, program_blocks


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _spread(items, count):
    """Return ``count`` items spread across an already ordered sequence."""
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indexes = sorted({round(i * (len(items) - 1) / (count - 1))
                      for i in range(count)})
    return [items[i] for i in indexes]


def _inside(start, end, bias=0.5):
    """A visible instant inside a window, away from ambiguous boundaries."""
    start, end = float(start), float(end)
    if end <= start:
        return start
    pad = min(0.12, max(0.02, (end - start) / 5.0))
    return start + pad + (end - start - 2 * pad) * bias


def _event_frames(edl, duration):
    """Candidate frames created by deliberate editorial decisions."""
    out = []

    def add(t, reason):
        t = _number(t)
        if t is None or t < 0 or t > duration:
            return
        out.append({"time_s": min(max(t, 0.0), max(0.0, duration - 0.01)),
                    "reason": reason})

    # Viewer-order blocks expose inserted scenes and every cut/junction. A
    # frame on each side of a junction can reveal a transition, discontinuity,
    # or accidental one-frame flash that a midpoint never can.
    try:
        blocks = program_blocks(edl)
    except Exception:
        blocks = []
    for block in blocks:
        start, end = block["out_start"], block["out_end"]
        if block.get("kind") == "insert":
            add(_inside(start, end), f"insert scene {block.get('n')}")
            if end - start >= 1.2:
                add(_inside(start, end, 0.08),
                    f"insert scene {block.get('n')} entrance")
                add(_inside(start, end, 0.92),
                    f"insert scene {block.get('n')} exit")
    for left, right in zip(blocks, blocks[1:]):
        at = _number(left.get("out_end"))
        if at is None:
            continue
        add(max(0.0, at - 0.10),
            f"cut {left.get('n')}→{right.get('n')} before")
        add(min(duration, at + 0.10),
            f"cut {left.get('n')}→{right.get('n')} after")

    for i, overlay in enumerate(edl.get("overlays") or [], 1):
        start = _number(overlay.get("start"), 0.0)
        length = _number(overlay.get("duration_s"), 0.0)
        if length and length > 0:
            kind = "B-roll" if overlay.get("fit") == "cover" else "overlay"
            add(_inside(start, start + length), f"{kind} {i} body")
            add(_inside(start, start + length, 0.04), f"{kind} {i} entrance")
            if length >= 1.2:
                add(_inside(start, start + length, 0.96), f"{kind} {i} exit")

    for i, text in enumerate(edl.get("texts") or [], 1):
        start, end = _number(text.get("start")), _number(text.get("end"))
        if start is not None and end is not None and end > start:
            template = text.get("template") or "text"
            add(_inside(start, end), f"{template} text {i}")
            if text.get("motion") or \
                    (text.get("entrance") or "none") != "none":
                add(_inside(start, end, 0.05), f"text {i} entrance")

    for i, vector in enumerate(edl.get("vectors") or [], 1):
        start, end = _number(vector.get("start")), _number(vector.get("end"))
        if start is not None and end is not None and end > start:
            kind = vector.get("kind") or "vector"
            add(_inside(start, end), f"{kind} graphic {i}")
            if vector.get("motion"):
                add(_inside(start, end, 0.05),
                    f"{kind} graphic {i} entrance")

    effects = edl.get("effects") or {}
    for i, zoom in enumerate(effects.get("zooms") or [], 1):
        start, end = _number(zoom.get("start")), _number(zoom.get("end"))
        if start is None or end is None or end <= start:
            continue
        mode = zoom.get("mode") or "punch"
        add(_inside(start, end), f"zoom {i} {mode}")
        if mode in {"ease", "push_in", "pull_out", "follow", "path"}:
            add(_inside(start, end, 0.08), f"zoom {i} beginning")
            add(_inside(start, end, 0.92), f"zoom {i} ending")

    for i, shift in enumerate(effects.get("frame_shifts") or [], 1):
        at = _number(shift.get("at"))
        length = _number(shift.get("duration_s"), 0.8)
        if at is not None:
            add(at + max(0.02, length / 2.0), f"frame shift {i}")

    for collection, label in ((effects.get("stylize") or [], "stylize"),
                              (effects.get("custom") or [], "custom effect")):
        for i, effect in enumerate(collection, 1):
            start, end = (_number(effect.get("start")),
                          _number(effect.get("end")))
            if start is not None and end is not None and end > start:
                name = effect.get("kind") or effect.get("label") or label
                add(_inside(start, end), f"{label} {i} {name}")

    # Per-shot reframing uses source time. Map each authored aim back onto the
    # finished program so the critic sees wide/crop decisions, not just a
    # global average frame.
    frame = edl.get("frame") or {}
    track = frame.get("focus_track") or []
    if track:
        try:
            tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                          edl.get("speed") or [])
            for i, span in enumerate(track, 1):
                middle = (_number(span.get("t0"), 0.0) +
                          _number(span.get("t1"), 0.0)) / 2.0
                mapped = tl.src_to_out(middle)
                if mapped is not None:
                    add(mapped, f"shot-specific framing {i}")
        except Exception:
            pass

    return out


def _graphic_motion_groups(edl, duration, states=7):
    """Ordered frame sequences for explicit text/vector choreography.

    A midpoint proves that words exist, not that their PATH is composed. For
    each explicitly keyframed text we expose opening, authored knots,
    intermediate travel and the final state as one ordered group. `plan`
    spends a bounded share of its existing frame budget on a few groups spread
    across the program, so this adds evidence rather than a new render/model
    pass. Named presets keep their historical lightweight sampling.
    """
    groups = []
    items = [("text", i, item)
             for i, item in enumerate(edl.get("texts") or [], 1)]
    items += [(str(item.get("kind") or "vector"), i, item)
              for i, item in enumerate(edl.get("vectors") or [], 1)]
    for label, i, item in items:
        motion = item.get("motion")
        start, end = _number(item.get("start")), _number(item.get("end"))
        if not isinstance(motion, dict) or start is None or end is None \
                or end <= start:
            continue
        start, end = max(0.0, start), min(float(duration), end)
        span = end - start
        if span <= 0.04:
            continue
        edge = min(0.04, span * 0.08)
        local = {edge, span * 0.25, span * 0.5, span * 0.75, span - edge}
        for value in motion.values():
            if not isinstance(value, list):
                continue
            for keyframe in value:
                try:
                    t = float(keyframe.get("t") if isinstance(keyframe, dict)
                              else keyframe.t)
                except (TypeError, ValueError, AttributeError):
                    continue
                # The exact first/last frame can be intentionally transparent
                # or already outside the Dialogue window. Nudge only those two
                # into the visible event while keeping interior knots exact.
                t = edge if t <= 0 else span - edge if t >= span else t
                if 0 <= t <= span:
                    local.add(t)
        picked = _spread(sorted(local), max(3, int(states or 3)))
        group = []
        for state_n, t in enumerate(picked, 1):
            group.append({
                "time_s": round(min(max(start + t, 0.0),
                                    max(0.0, duration - 0.01)), 3),
                "reason": (f"{label} motion {i} state "
                           f"{state_n}/{len(picked)} "
                           f"(+{t:.2f}s local)"),
            })
        if group:
            groups.append(group)
    return groups


# Private compatibility alias: callers/tests written when only text exposed
# general motion still exercise the now-shared planner.
_text_motion_groups = _graphic_motion_groups


def _dedupe(frames, within_s=0.08):
    """Merge near-identical seeks and retain all reasons for the critic."""
    clean = []
    for frame in sorted(frames, key=lambda row: row["time_s"]):
        if clean and abs(frame["time_s"] - clean[-1]["time_s"]) <= within_s:
            existing = clean[-1]["reason"].split(" + ")
            if frame["reason"] not in existing:
                clean[-1]["reason"] += " + " + frame["reason"]
            continue
        clean.append({"time_s": round(frame["time_s"], 3),
                      "reason": frame["reason"]})
    return clean


def plan(edl, duration, max_frames=32, base_frames=12, extra_frames=None):
    """Return timestamped frames covering both program time and edit events.

    At least half of a normal budget remains available for authored events;
    the rest is an evenly spaced safety net. When events outnumber their
    slots, temporal spreading prevents an effect-heavy opening from hiding the
    second half of the video. The final fill prefers still-unseen events.
    """
    duration = max(0.0, _number(duration, 0.0))
    max_frames = max(1, int(max_frames or 1))
    if duration <= 0.02:
        return []
    base_count = min(max_frames, max(3, int(base_frames or 3)))
    uniform = [{"time_s": duration * (i + 0.5) / base_count,
                "reason": "whole-program coverage"}
               for i in range(base_count)]
    anchors = [
        {"time_s": min(0.08, duration / 2.0), "reason": "opening frame"},
        {"time_s": max(0.0, duration - min(0.08, duration / 2.0)),
         "reason": "closing frame"},
    ]
    extras = []
    for row in extra_frames or []:
        if not isinstance(row, dict):
            continue
        at = _number(row.get("time_s"))
        reason = " ".join(str(row.get("reason") or "").split())[:180]
        if at is None or not reason or at < 0 or at > duration:
            continue
        extras.append({"time_s": min(at, max(0.0, duration - .01)),
                       "reason": reason})
    requested = _dedupe(extras)
    events = _dedupe(_event_frames(edl or {}, duration) + extras)
    # Use the merged event row as the priority item so a director beat that
    # lands on an authored title/cutaway retains both explanations on the
    # critic sheet instead of suppressing the lower-priority event label.
    priority = [row for row in events if any(
        abs(row["time_s"] - wanted["time_s"]) <= 0.08
        for wanted in requested)]
    motion_groups = _graphic_motion_groups(edl or {}, duration)
    if motion_groups:
        # Preserve static-plan behavior byte-for-byte when no general motion
        # exists. With motion, reserve at most two thirds of the non-base
        # evidence slots (and at most 16 frames) for ordered state sequences;
        # the remainder still covers inserts/cuts/reframes/effects. This is an
        # operational perception budget, never an authoring limit.
        base = _dedupe(anchors + uniform)
        room = max(0, max_frames - len(base))
        # A caller-supplied frame names a semantic claim that must be judged
        # (for example a calm director beat with no EDL event). Spend the
        # existing perception budget on those first; uniform coverage and the
        # opening/closing anchors remain intact. The sequence-map contract is
        # bounded below this default room, while smaller custom budgets degrade
        # by spreading across the entire treatment instead of keeping only its
        # opening.
        chosen_priority = _spread(priority, room)
        selected = _dedupe(base + chosen_priority)
        room = max(0, max_frames - len(selected))
        motion_budget = min(16, max(0, int(room * 2 / 3)))
        group_count = min(len(motion_groups), motion_budget // 4)
        chosen_groups = _spread(motion_groups, group_count)
        motion_frames = []
        if chosen_groups:
            per_group = max(3, motion_budget // len(chosen_groups))
            for group in chosen_groups:
                motion_frames.extend(_spread(group, per_group))
        selected = _dedupe(selected + motion_frames)
        remaining = max(0, max_frames - len(selected))
        leftovers = [row for row in events if all(
            abs(row["time_s"] - picked["time_s"]) > 0.08
            for picked in selected)]
        chosen_events = _spread(leftovers, remaining)
        selected = _dedupe(selected + chosen_events)
        # Deduping can free a slot. Spend it on the most temporally distinct
        # unseen event, as the legacy branch does below.
        leftovers = [row for row in leftovers if all(
            abs(row["time_s"] - picked["time_s"]) > 0.08
            for picked in selected)]
        while leftovers and len(selected) < max_frames:
            candidate = max(
                leftovers,
                key=lambda row: min(abs(row["time_s"] - p["time_s"])
                                    for p in selected))
            selected.append(candidate)
            selected = _dedupe(selected)
            leftovers.remove(candidate)
        return sorted(selected[:max_frames], key=lambda row: row["time_s"])

    # Preserve the historical planner exactly when no caller supplied direct
    # semantic evidence. With explicit frames, protect the normal coverage
    # floor and then review as many named claims as the configured perception
    # budget permits before filling from lower-priority authored events.
    if priority:
        selected = _dedupe(anchors + uniform)
        room = max(0, max_frames - len(selected))
        selected = _dedupe(selected + _spread(priority, room))
        remaining = max(0, max_frames - len(selected))
        leftovers = [row for row in events if all(
            abs(row["time_s"] - picked["time_s"]) > 0.08
            for picked in selected)]
        selected = _dedupe(selected + _spread(leftovers, remaining))
        return sorted(selected[:max_frames], key=lambda row: row["time_s"])

    event_budget = max(0, max_frames - base_count)
    chosen_events = _spread(events, event_budget)
    selected = _dedupe(anchors + uniform + chosen_events)

    # Deduplication can free slots. Spend those on the most temporally distinct
    # event evidence not already selected, never on duplicate uniform frames.
    leftovers = [row for row in events if all(
        abs(row["time_s"] - picked["time_s"]) > 0.08 for picked in selected)]
    while leftovers and len(selected) < max_frames:
        candidate = max(
            leftovers,
            key=lambda row: min(abs(row["time_s"] - p["time_s"])
                                for p in selected))
        selected.append(candidate)
        selected = _dedupe(selected)
        leftovers.remove(candidate)
    if len(selected) > max_frames:
        selected = _spread(selected, max_frames)
    return sorted(selected, key=lambda row: row["time_s"])


def pages(frames, page_tiles=16):
    """Chunk a plan into image-sheet pages without losing tile identity."""
    page_tiles = max(1, int(page_tiles or 1))
    return [frames[i:i + page_tiles]
            for i in range(0, len(frames), page_tiles)]


def describe_page(page, number=None):
    prefix = f"screening page {number}: " if number is not None else ""
    return prefix + "; ".join(
        f"tile {i + 1}={row['time_s']:.2f}s ({row['reason']})"
        for i, row in enumerate(page))
