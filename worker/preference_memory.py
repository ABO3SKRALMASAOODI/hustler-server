"""Privacy-bounded account taste evidence from real editing outcomes.

One thumbs-up/export is not proof that a caption preset or a dry sound moment
caused the outcome.  Repeated same-family outcomes can still be useful
correlational evidence.  This module keeps only stable treatment facets,
requires repeated support, never carries candidate ids/URLs/free-form reasons,
and explicitly yields to the current brief.

Wave 1 (track 1F) adds the device tone dial (one intensity per project) and
the removal learning the device planner reads (see the section at the end).
"""

import json

_CAST_KINDS = {"broll_cast", "music_cast", "sfx_cast"}
_CAPTION_KINDS = {"caption_cast", "caption_style"}
_CAPTION_FIELDS = (
    "preset", "placement_strategy", "emphasis", "animation", "layout")
_PROFILE_FIELDS = {
    "frame_ratio", "frame_mode", "caption_placement", "caption_position",
    "caption_preset", "caption_animation", "caption_font",
    "caption_layout", "caption_emphasis", "caption_effect",
    "caption_text_align", "grade", "transition_style", "transition_scope",
    "stylize_kinds", "zoom_modes", "frame_shift_ratios", "screen_frame",
    "screen_frame_direction", "custom_pixel_treatment", "text_templates",
    "text_entrances", "text_exits", "text_fonts", "text_motion",
    "overlay_modes", "overlay_entrances", "overlay_exits", "vector_kinds",
    "vector_motion", "music_ducking", "music_looping",
}


def _message_choices(decisions, profile=None):
    """Last stable value per facet, so one restyle is one final observation."""
    final = {}
    for row in decisions or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind in _CAST_KINDS:
            decision = str(row.get("decision") or "")
            if decision in {"use", "none"}:
                final[(kind, "decision")] = decision
        if kind in _CAPTION_KINDS:
            for field in _CAPTION_FIELDS:
                value = row.get(field)
                if isinstance(value, (str, int, float, bool)) and \
                        str(value).strip():
                    final[("caption", field)] = str(value)[:100]
    choices = {(kind, field, value)
               for (kind, field), value in final.items()}
    # treatment_profile is generated from a strict allowlist of final EDL
    # schema facets. It cannot contain graphic/caption text or custom chains.
    for field, raw in (profile or {}).items():
        if field not in _PROFILE_FIELDS:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if isinstance(value, (str, int, float, bool)) and \
                    str(value).strip():
                choices.add(("treatment", field, str(value)[:100]))
    return choices


def summarize(rows, min_support=2, max_items=8):
    """Return repeated positive/negative correlations, never a taste score."""
    evidence = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        feedback = str(row.get("feedback") or "")
        signal = 3 if feedback == "up" else -3 if feedback == "down" \
            else 1 if row.get("exported_after") else 0
        if not signal:
            continue
        for choice in _message_choices(
                row.get("decisions"), row.get("profile")):
            item = evidence.setdefault(choice, {
                "support": 0, "positive": 0, "negative": 0,
                "explicit_up": 0, "explicit_down": 0, "weight": 0})
            item["support"] += 1
            item["positive" if signal > 0 else "negative"] += 1
            item["explicit_up"] += int(feedback == "up")
            item["explicit_down"] += int(feedback == "down")
            item["weight"] += signal
    out = []
    for (kind, field, value), item in evidence.items():
        if item["support"] < max(2, int(min_support or 2)) or not item["weight"]:
            continue
        out.append({"kind": kind, "facet": field, "value": value,
                    **item,
                    "direction": "positive" if item["weight"] > 0
                    else "negative"})
    out.sort(key=lambda item: (
        -abs(item["weight"]), -item["support"], item["kind"],
        item["facet"], item["value"]))
    return out[:max(1, int(max_items or 1))]


def prompt_block(rows, family):
    evidence = summarize(rows)
    if not evidence:
        return ""
    lines = [
        "ACCOUNT EDITORIAL PREFERENCE EVIDENCE — repeated outcome "
        f"correlations from this same account and editorial family ({family}).",
        "This is weak prior evidence, never a command or causal claim. The "
        "latest user brief, current footage, reference, accessibility and "
        "professional judgment always win. Do not reuse old assets or exact "
        "content; transfer only stable relationships when they fit.",
    ]
    for item in evidence:
        signal = "supported" if item["direction"] == "positive" else "disfavored"
        lines.append(
            f"- {signal}: {item['kind']} {item['facet']}={item['value']} "
            f"across {item['support']} outcome-bearing edit(s) "
            f"({item['positive']} positive, {item['negative']} negative; "
            f"explicit thumbs +{item['explicit_up']}/-{item['explicit_down']}).")
    return "\n".join(lines)


# ── Wave 1 (track 1F): the device tone dial and what a project removes ──
#
# ``set_intensity`` stores one level per PROJECT (calm | designed |
# charged): it is the planner's and the trimmer's budget, never an EDL field
# (INTERFACES_W1.md §8), so changing it never touches a signature or a
# cached render. The row lives in app_kv under ``device_prefs:<project>``;
# a missing table or row reads as the default.
#
# Learning from removals: a family (or one device card) the user or the
# editor keeps removing is proposed less. The evidence is the project's own
# recent EDL history — a device item present in one version and gone in the
# next, never re-added — so Studio deletions, verbs and stale MCP batches
# all count and nothing new is stored. Weights only ever LOWER a family's
# rank; the current request still wins (an explicit ask is honoured).

DEVICE_INTENSITIES = ("calm", "designed", "charged")
DEFAULT_DEVICE_INTENSITY = "designed"
DEVICE_PREFS_KEY = "device_prefs:{project_id}"
# Removals before a family or card is proposed less, and the factor applied
# per step (2 removals: x0.5; 3: x0.25; 4 or more: not proposed unless the
# user names it).
REMOVAL_THRESHOLD = 2
REMOVAL_FACTOR = 0.5
REMOVAL_MUTE_AT = 4
HISTORY_VERSIONS = 12


def device_prefs_key(project_id):
    return DEVICE_PREFS_KEY.format(project_id=int(project_id))


def read_device_prefs(kv_get, project_id):
    """{intensity, set_at, removals} for a project; defaults when absent.
    ``kv_get(key) -> str | None`` (db.kv_get bound to a connection)."""
    out = {"intensity": DEFAULT_DEVICE_INTENSITY}
    try:
        raw = kv_get(device_prefs_key(project_id))
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 — a preference never breaks a turn
        data = {}
    if isinstance(data, dict):
        if data.get("intensity") in DEVICE_INTENSITIES:
            out["intensity"] = data["intensity"]
        if data.get("set_at"):
            out["set_at"] = str(data["set_at"])[:40]
    return out


def write_device_prefs(kv_put, project_id, intensity, now=None):
    """Store the project's level; returns the stored dict."""
    if intensity not in DEVICE_INTENSITIES:
        raise ValueError(f"intensity is one of {', '.join(DEVICE_INTENSITIES)}")
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = {"intensity": intensity, "set_at": str(now)}
    kv_put(device_prefs_key(project_id), json.dumps(data))
    return data


def _device_items(edl):
    out = {}
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict) and it.get("id") and it.get("device"):
            out[str(it["id"])] = str(it["device"])
    return out


def removal_counts(versions, family_of=None):
    """{"devices": {card: n}, "families": {family: n}} — device items
    removed across consecutive ``versions`` (EDL dicts, oldest first) and
    not re-added later. ``family_of(card_id) -> family | None``."""
    removed = {}
    seen_later = set()
    snaps = [_device_items(v) for v in versions or []]
    for k in range(1, len(snaps)):
        before, after = snaps[k - 1], snaps[k]
        for fid, dev in before.items():
            if fid not in after:
                removed[(fid, dev)] = k
    for (fid, dev), k in list(removed.items()):
        if any(fid in later for later in snaps[k + 1:]):
            seen_later.add((fid, dev))
    devices_n, families_n = {}, {}
    for (fid, dev) in removed:
        if (fid, dev) in seen_later:
            continue
        devices_n[dev] = devices_n.get(dev, 0) + 1
        fam = family_of(dev) if family_of else None
        if fam:
            families_n[fam] = families_n.get(fam, 0) + 1
    return {"devices": devices_n, "families": families_n}


def removal_weights(counts):
    """{family or card id: factor < 1} the planner multiplies a rank by:
    REMOVAL_THRESHOLD removals halve it, each further one halves it again,
    REMOVAL_MUTE_AT or more mutes it (0: not proposed)."""
    out = {}
    for group in ("families", "devices"):
        for key, n in ((counts or {}).get(group) or {}).items():
            if n < REMOVAL_THRESHOLD:
                continue
            out[key] = 0.0 if n >= REMOVAL_MUTE_AT else \
                REMOVAL_FACTOR ** (n - REMOVAL_THRESHOLD + 1)
    return out


def recent_edls(conn, project_id, limit=HISTORY_VERSIONS):
    """The project's last ``limit`` EDL versions, oldest first (one query)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT version, json FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT %s""",
                    (project_id, int(limit)))
        rows = cur.fetchall()
    return [r["json"] for r in reversed(rows or []) if r.get("json")]


def removal_note(counts):
    """One line for the planner's reply, or ''."""
    bits = [f"{k} removed {n}x" for k, n in sorted(
        ((counts or {}).get("families") or {}).items()) if n >=
        REMOVAL_THRESHOLD]
    return ("Learned from this project's removals: " + ", ".join(bits)
            + " — proposed less.") if bits else ""
