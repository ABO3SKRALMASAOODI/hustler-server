"""Pure paged EDL reads shared by executor and authenticated API."""
import json
try:
    from schemas import describe_edl, program_duration
except ImportError:
    from worker_schemas import describe_edl, program_duration

def compact_edl(row, duration, program_map):
    edl = row["json"]
    collection_names = (
        "keep", "inserts", "music", "voiceover", "sfx", "overlays",
        "texts", "vectors", "speed", "volume")
    duplicates = {}
    for coll in ("inserts", "overlays"):
        for item in edl.get(coll) or []:
            key = item.get("asset_key")
            if key:
                duplicates.setdefault(key, []).append(
                    f"{coll}:{item.get('id', '?')}")
    duplicates = {k: uses for k, uses in duplicates.items() if len(uses) > 1}
    caps = edl.get("captions")
    if isinstance(caps, dict):
        cap_summary = {
            "mode": caps.get("mode"),
            "design_version": caps.get("design_version"),
            "style": caps.get("style"),
            "max_words_per_caption": caps.get("max_words_per_caption"),
            "placement_spans": len(caps.get("placement_track") or []),
            "emphasis_words": caps.get("emphasis_words"),
        }
    elif isinstance(caps, list):
        cap_summary = {"mode": "manual", "items": len(caps)}
    else:
        cap_summary = None
    return {
        "version": row["version"],
        "description": describe_edl(edl, duration),
        "program_map": program_map or None,
        "program_duration_s": round(program_duration(edl), 3),
        "frame": edl.get("frame"),
        "master": edl.get("master"),
        "captions": cap_summary,
        "collection_counts": {
            name: len(edl.get(name) or []) for name in collection_names
        },
        "visual_asset_duplicates": duplicates,
        "available_sections": sorted(edl.keys()),
    }

_EDL_SECTION_ALIASES = {
    "segments": ("keep",), "cuts": ("keep",), "text": ("texts",),
    "vectors": ("vectors",), "graphics": ("texts", "vectors"),
    "zoom": ("effects",), "zooms": ("effects",),
    "transitions": ("effects",),
    "color": ("effects",), "grade": ("effects",), "grades": ("effects",),
    "stylize": ("effects",), "effects": ("effects",),
    "fades": ("effects",),
    "look": ("effects", "master"), "frames": ("frame",),
    "audio": ("music", "volume", "voiceover", "sfx", "stem_mix",
              "master"),
    "media": ("inserts", "overlays", "music", "voiceover", "sfx"),
    "timeline": ("keep", "inserts", "overlays", "texts", "vectors",
                 "effects"),
    "erases": ("source_clean", "patches"),
}
_EDL_OVERVIEW_ALIASES = {"program", "program_map", "overview", "summary", "video"}
_EDL_ALL_ALIASES = {"all", "everything", "full"}

def read_edl(row, duration, program_map, sections=None, compact=False, offset=0, limit=100):
    """Current EDL without ever returning amputated/invalid JSON.

    Large timelines default to a compact index. Callers can then request one
    or more top-level sections, with list pagination. This replaces the old
    character slice that often cut the JSON in the middle of the exact
    captions/overlays collection an MCP caller needed to repair.
    """
    edl = row["json"]
    try:
        off = max(0, int(offset or 0))
        lim = min(200, max(1, int(limit or 100)))
    except (TypeError, ValueError):
        return "REJECTED: offset and limit must be integers."
    if sections is not None and not isinstance(sections, (list, tuple, str)):
        return ("REJECTED: sections must be a section name or array of names "
                f"from {sorted(edl.keys())}.")
    requested = (sections.split(",") if isinstance(sections, str)
                 else list(sections or []))
    wanted, resolved = [], {}
    overview = False
    unknown = []
    canonical_lc = {str(key).lower(): key for key in edl}
    for raw in requested:
        label = str(raw).strip()
        low = label.lower()
        if low in canonical_lc:
            names = (canonical_lc[low],)
        elif low in _EDL_SECTION_ALIASES:
            names = tuple(n for n in _EDL_SECTION_ALIASES[low] if n in edl)
            resolved[label] = list(names)
        elif low in _EDL_OVERVIEW_ALIASES:
            overview = True
            resolved[label] = ["compact_overview"]
            continue
        elif low in _EDL_ALL_ALIASES:
            names = tuple(edl.keys())
            resolved[label] = list(names)
        else:
            unknown.append(label)
            continue
        for name in names:
            if name not in wanted:
                wanted.append(name)
    unknown = sorted(set(unknown))
    # A misspelled read is a discovery request, not an unsafe edit. Return
    # the real index and any valid requested sections together, so the agent
    # can act without guessing 20 more feature names in separate calls.
    if unknown:
        overview = True
    if compact:
        return json.dumps(compact_edl(row, duration, program_map), indent=1)
    if wanted or overview:
        selected, pages = {}, {}
        for raw_name in wanted:
            name = str(raw_name)
            value = edl.get(name)
            if isinstance(value, list):
                selected[name] = value[off:off + lim]
                pages[name] = {"offset": off,
                               "returned": len(selected[name]),
                               "total": len(value),
                               "next_offset": (off + len(selected[name])
                                               if off + len(selected[name]) < len(value)
                                               else None)}
            else:
                selected[name] = value
        payload = {"version": row["version"], "sections": selected,
                   "pagination": pages}
        if overview:
            payload["overview"] = compact_edl(row, duration, program_map)
        if resolved:
            payload["aliases_resolved"] = resolved
        if unknown:
            payload["unknown_sections"] = unknown
            payload["notice"] = (
                "These names are not EDL sections. Use overview.available_sections "
                "for exact fields; feature settings live inside effects, texts or "
                "captions. Read the creative plan with get_edit_plan. No state changed.")
        rendered = json.dumps(payload, indent=1)
        if len(rendered) > 21000:
            return json.dumps({
                "version": row["version"],
                "error": ("Requested page is too large for a reliable tool "
                          "response; request fewer sections or a smaller limit."),
                "requested_sections": wanted,
                "suggested_limit": max(1, lim // 2),
            }, indent=1)
        return rendered
    rendered = json.dumps(edl, indent=1)
    if len(rendered) <= 19000:
        header = {"version": row["version"],
                  "description": describe_edl(edl, duration),
                  "edl": edl}
        return json.dumps(header, indent=1)
    compact_payload = compact_edl(row, duration, program_map)
    compact_payload["notice"] = (
        "Full EDL is large, so this is a complete compact index—not truncated "
        "JSON. Call get_edl(sections=['captions']) or another named section; "
        "use offset/limit for long list sections.")
    return json.dumps(compact_payload, indent=1)
