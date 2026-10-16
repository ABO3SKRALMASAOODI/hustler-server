"""Atomic, media-free edits shared by the Studio API and the agent.

All authored times in a batch describe the resulting timeline. This explicit
contract prevents an implicit remap from moving captions/music twice.
"""
import copy
import json
import os
import sys

try:
    from schemas import (FX_AUTHORABLE_TYPES, FX_TYPES, CaptionStyle,
                         program_duration, validate_edl)
except ImportError:
    from worker_schemas import (FX_AUTHORABLE_TYPES, FX_TYPES, CaptionStyle,
                                program_duration, validate_edl)

# "fx" (Wave 0 seam, worker/devices/INTERFACES.md §4): registered effect
# items by id. Accepted here always; advertised in apply_edit_batch's layer
# enum only once an AUTHORABLE type is registered (schemas.
# FX_AUTHORABLE_TYPES), because until then validation rejects any item.
LAYERS = frozenset(("keep", "speed", "inserts", "frame", "captions",
    "caption_mutes", "texts", "vectors", "music", "sfx", "voiceover", "volume", "master",
    "effects", "overlays", "canvas", "motion", "fx"))
ITEM_LAYERS = frozenset(("inserts", "texts", "vectors", "music", "sfx", "voiceover", "overlays",
    "motion", "fx"))


# Device cards (worker/devices/cards/<id>.json), read as plain JSON so this
# module stays pure: the backend loads it by path for MCP and Studio batches.
_CARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "devices", "cards")
_CARD_CACHE = {}


def _device_cards():
    """{card id: card} from the compiled JSON cards; {} when unreadable."""
    try:
        names = sorted(n for n in os.listdir(_CARDS_DIR)
                       if n.endswith(".json") and not n.startswith((".", "_")))
        stamp = tuple((n, os.path.getmtime(os.path.join(_CARDS_DIR, n)))
                      for n in names)
    except OSError:
        return {}
    hit = _CARD_CACHE.get(stamp)
    if hit is None:
        hit = {}
        for n in names:
            try:
                with open(os.path.join(_CARDS_DIR, n), encoding="utf-8") as f:
                    card = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(card, dict) and card.get("id") == n[:-5]:
                hit[card["id"]] = card
        _CARD_CACHE.clear()
        _CARD_CACHE[stamp] = hit
    return hit


def _flag_on(name):
    """A dark card's flag, read the way devices.flag_on reads it: the worker
    config attribute when the worker's config is loaded, else the
    environment variable of the same name."""
    config = sys.modules.get("config")     # never imported from here
    if config is not None and hasattr(config, name):
        return bool(getattr(config, name))
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def check_fx_devices(before, after):
    """Wave 1 write gate (worker/devices/INTERFACES_W1.md §5): every fx item a
    batch adds or changes that names a ``device`` must name an existing card
    whose ``fx_type`` is the item's type, and a ``dark`` card only while its
    flag is on. Items the batch leaves as they were are not re-judged (a card
    that goes dark later never blocks an unrelated edit). Department verbs
    write through apply_batch, so a verb and a stale client's batch pass the
    very same gate. Raises ValueError."""
    old = {i.get("id"): i for i in (before or {}).get("fx") or []
           if isinstance(i, dict)}
    cards = None
    for item in (after or {}).get("fx") or []:
        if not isinstance(item, dict) or not item.get("device"):
            continue
        if old.get(item.get("id")) == item:
            continue
        cards = _device_cards() if cards is None else cards
        card = cards.get(item["device"])
        if card is None:
            raise ValueError(f"fx '{item.get('id')}': device "
                             f"{item['device']!r} is not a known device card.")
        if card.get("fx_type") and card["fx_type"] != item.get("type"):
            raise ValueError(f"fx '{item.get('id')}': device {card['id']} "
                             f"writes fx type {card['fx_type']}, not "
                             f"{item.get('type')}.")
        if card.get("status") == "dark" and not (
                card.get("flag") and _flag_on(card["flag"])):
            raise ValueError(f"fx '{item.get('id')}': device {card['id']} is "
                             "not available yet (dark).")


# ── Wave 1, track 1A: behind the subject through the PROGRAM matte ──────
# (worker/devices/INTERFACES_W1.md §6.3, the behind_subject rule). A
# behind-subject motion graphic (or a text whose ``behind`` names only this
# key) needs no measured mask clip any more: the renderer carries the
# person matte, measured once in source space, through the picture's own
# chain (worker/matte_track.py). A stale MCP client — which cannot measure
# anything — writes one with a plain upsert; this fills in the SubjectMatte
# that names the program matte (matte_track.PROGRAM_KEY; a test pins the
# two literals equal) and the source span under the item's window. Where no
# matte is measured yet the render draws its face-safe fallback and says so.
PROGRAM_MATTE_KEY = "sidecar:matte.person"
PROGRAM_MATTE_LIVE = True           # flipped with matte_track.LIVE


def program_matte_live():
    if os.getenv("PROGRAM_MATTE_OFF", "0").strip().lower() in (
            "1", "true", "yes", "on"):
        return False
    config = sys.modules.get("config")     # never imported from here
    if config is not None and getattr(config, "PROGRAM_MATTE_OFF", False):
        return False
    return PROGRAM_MATTE_LIVE


def _program_behind(edl, item):
    """The SubjectMatte naming the program matte for ``item``'s window, or
    None (no main footage under it)."""
    try:
        try:
            from timeline import Timeline
        except ImportError:
            from worker_timeline import Timeline
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        a, b = float(item["start"]), float(item["end"])
    except Exception:  # noqa: BLE001 — the item is refused as before
        return None
    spans = []
    for (s, e), pcs, off, L in zip(tl.segs, tl.pieces, tl.offsets,
                                   tl.seg_out_len):
        lo, hi = max(a, off), min(b, off + L)
        if hi - lo <= 1e-4:
            continue
        acc = off
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            x, y = max(lo, acc), min(hi, acc + plen)
            if y - x > 1e-4:
                spans.append((ps + (x - acc) * f, ps + (y - acc) * f))
            acc += plen
    if not spans:
        return None
    lo = round(min(x for x, _y in spans), 2)
    hi = round(max(y for _x, y in spans), 2)
    if hi - lo < 0.2:
        hi = round(lo + 0.2, 2)
    return {"asset_key": PROGRAM_MATTE_KEY, "src_start": lo, "src_end": hi,
            "fp": "program-matte", "method": "program"}


def _behind_through_program_matte(edl, before=None):
    """Fill program-matte SubjectMattes in place (see above): a
    behind_subject motion item without ``behind``, and any text or motion
    item whose ``behind`` names PROGRAM_MATTE_KEY. Raises ValueError where
    the window of an item the batch WROTE holds no main footage; an item
    the batch left untouched keeps its stored mask there (a keep edit or an
    insert elsewhere in the batch is never refused for it — the render
    draws it as a plain title until footage plays under it again)."""
    live = program_matte_live()
    prev = {}
    for layer in ("motion", "texts"):
        for item in (before or {}).get(layer) or []:
            if isinstance(item, dict) and item.get("id") is not None:
                prev[(layer, item["id"])] = item
    for layer in ("motion", "texts"):
        for item in edl.get(layer) or []:
            if not isinstance(item, dict):
                continue
            b = item.get("behind")
            named = isinstance(b, dict) and \
                b.get("asset_key") == PROGRAM_MATTE_KEY
            bare = (layer == "motion"
                    and item.get("layer") == "behind_subject" and not b)
            if not (named or bare):
                continue
            if not live:
                if bare:
                    raise ValueError(
                        "A behind_subject motion graphic needs its subject "
                        "mask; place it with add_motion_graphic("
                        "layer='behind_subject').")
                raise ValueError("The program matte is off on this server: "
                                 "place the item with add_text_behind / "
                                 "add_motion_graphic(layer='behind_subject').")
            filled = _program_behind(edl, item)     # the CURRENT window
            if filled is None and named and \
                    prev.get((layer, item.get("id"))) == item:
                continue                    # untouched: as it was
            if filled is None:
                raise ValueError(
                    f"{layer} '{item.get('id')}' cannot go behind the "
                    "subject: no main footage plays under its window.")
            item["behind"] = dict(b or {}, **filled) if named else filled


def project_media_keys(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT storage_key,duration_s FROM assets WHERE project_id=%s", (project_id,))
        return {row["storage_key"]: row for row in cur.fetchall()}


def media_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("asset_key", "storage_key", "vocals_key", "accomp_key") and item:
                if not isinstance(item, str):
                    raise ValueError("A media reference must be a storage key string.")
                yield item
            else:
                yield from media_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from media_keys(item)


def _motion_refs(edl):
    """The project media named by motion graphics' asset params."""
    refs = set()
    for item in (edl or {}).get("motion") or []:
        if not isinstance(item, dict):
            continue
        try:
            import motion_templates
            refs |= set(motion_templates.asset_params(
                item.get("template") or "", item.get("params") or {}).values())
        except Exception:
            continue
    return refs


def _check_new_motion_params(before, edl):
    """A motion graphic a batch adds or changes passes add_motion_graphic's
    strict parameter check (motion_templates.check_params): validate_edl's
    lenient pass would store phrase_build row sizes 's'/'xl' the template
    cannot read. Items the batch leaves as they were are not re-judged."""
    import motion_templates
    old = {m.get("id"): m for m in (before or {}).get("motion") or []
           if isinstance(m, dict)}
    for item in edl.get("motion") or []:
        if not isinstance(item, dict):
            continue
        prev = old.get(item.get("id"))
        if prev is not None and all(prev.get(k) == item.get(k)
                                    for k in ("template", "params", "html")):
            continue
        params = item.get("params")
        if isinstance(params, dict) and "allow_face_overlap" in params:
            # the write's flag passed inside params, as add/set_motion_graphic
            # take it (motion_tools._hoist_face_flag): honoured, not refused
            try:
                accepts = motion_templates.spec(str(item.get("template") or ""))["params"]
            except (ValueError, KeyError):
                accepts = {}
            if "allow_face_overlap" not in accepts:
                params = dict(params)
                flag = params.pop("allow_face_overlap")
                item["params"] = params
                if item.get("allow_face_overlap") is None:
                    item["allow_face_overlap"] = (
                        flag.strip().lower() in ("1", "true", "yes", "on")
                        if isinstance(flag, str) else bool(flag))
        try:
            motion_templates.check_params(str(item.get("template") or ""),
                                          item.get("params") or {}, html=item.get("html"))
        except ValueError as e:
            raise ValueError(f"motion '{item.get('id')}': {e}")


# An item whose end is this close to the programme's end before the batch
# ran with it to the end.
END_WITH_PROGRAMME_S = 0.06
# Program-time item lists whose end moves with a shortened programme:
# (dotted path, display name). Overlays end at start + duration_s.
_END_LAYERS = (("motion", "motion"), ("texts", "text"), ("vectors", "vector"),
               ("effects.picture_cards", "picture card"),
               ("effects.zooms", "zoom"), ("effects.stylize", "stylize"),
               ("effects.custom", "custom filter"),
               ("effects.regions", "region"), ("fx", "fx"))


def _layer_items(edl, path):
    node = edl
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return [i for i in node or [] if isinstance(i, dict)] \
        if isinstance(node, list) else []


def _programme_end(edl):
    try:
        return float(program_duration(edl))
    except (KeyError, TypeError, ValueError):
        return None


def end_with_programme(before, edl, operations):
    """A batch that SHORTENS the programme (an insert or a hold removed, a
    keep trimmed) ends, at the new end, every item the batch did not author
    that ran with the programme to its old end; the rest that would now end
    past it are refused together, by name.

    The batch contract stands: authored times describe the resulting
    timeline, so nothing the batch names is moved and no start is ever
    remapped. Without this, removing a payoff hold failed one layer at a
    time: the owner's Jensen run, s25 (project 3762 v12/v13): 'motion[0]:
    end 32.35 exceeds the limit 31.35s', the headline re-ended by hand, then
    'picture card: end 32.35 exceeds the limit 31.35s', and the short was
    abandoned. Returns the notes; raises ValueError naming every item that
    still overruns."""
    old_end, new_end = _programme_end(before), _programme_end(edl)
    if old_end is None or new_end is None or new_end >= old_end - 0.005:
        return []
    touched_layers, touched_ids = set(), set()
    for op in operations:
        layer = op.get("layer")
        if op.get("id"):
            touched_ids.add((layer, op.get("id")))
        else:
            touched_layers.add(layer)
    ended, over = [], []
    for path, name in _END_LAYERS + (("overlays", "overlay"),):
        top = path.split(".")[0]
        for item in _layer_items(edl, path):
            try:
                start = float(item.get("start"))
                end = (start + float(item["duration_s"]) if path == "overlays"
                       else float(item.get("end")))
            except (KeyError, TypeError, ValueError):
                continue
            if end <= new_end + 0.01:
                continue
            label = f"{name} '{item.get('id') or '?'}'"
            authored = top in touched_layers or \
                (top, item.get("id")) in touched_ids
            if not authored and abs(end - old_end) <= END_WITH_PROGRAMME_S \
                    and new_end - start >= 0.2:
                if path == "overlays":
                    item["duration_s"] = round(new_end - start, 3)
                else:
                    item["end"] = round(new_end, 3)
                ended.append(label)
            else:
                over.append(f"{label} ends at {end:g}s")
    if over:
        raise ValueError(
            f"This batch makes the programme {new_end:g}s long (it was "
            f"{old_end:g}s), and these items end after it: "
            + "; ".join(over) + ". A batch's times describe the resulting "
            "timeline: end each one at or before the new end in this same "
            "batch (items that ran to the old end and that the batch does "
            "not name are ended with it), or remove an insert with "
            "remove_insert, which re-anchors every item.")
    if not ended:
        return []
    return [f"PROGRAMME END {old_end:g}s -> {new_end:g}s: "
            + ", ".join(ended) + f" ran to the end and now end at {new_end:g}s."]


# Near misses a batch author means unambiguously (the owner's Codex runs,
# 13-16 Oct: each cost a rejected batch with a raw validation dump).
_NO_TRANSITION = ("cut", "none", "hard_cut", "hard cut", "")
_CARD_FIT = {"contain": "pad", "letterbox": "pad", "fit": "pad",
             "cover": "crop", "fill": "crop"}
_CARD_ANIM_MIN_S = 0.12


def _near_misses(edl, layers):
    """Normalize the values a batch plainly meant and return their notes."""
    notes = []
    fx = edl.get("effects") if "effects" in layers else None
    if isinstance(fx, dict):
        tr = fx.get("transition")
        style = tr.get("style") if isinstance(tr, dict) else tr
        if tr is not None and isinstance(style, str) and \
                style.strip().lower() in _NO_TRANSITION:
            fx["transition"] = None
            notes.append(f"effects.transition '{style}' is a plain cut: stored as "
                         "no transition (null)")
        for card in fx.get("picture_cards") or []:
            if not isinstance(card, dict):
                continue
            cid = card.get("id") or "?"
            if isinstance(card.get("follow"), bool):
                if card["follow"]:
                    raise ValueError(
                        f"picture card {cid}: follow is the list of measured face "
                        "paths set_picture_card(source='auto', follow=true) writes, "
                        "not a switch. Leave it out for a still crop, or call "
                        "set_picture_card to measure it.")
                card.pop("follow")
                notes.append(f"picture card {cid}: follow false = no follow (left out)")
            fit = card.get("fit")
            if isinstance(fit, str) and fit.strip().lower() in _CARD_FIT:
                card["fit"] = _CARD_FIT[fit.strip().lower()]
                notes.append(f"picture card {cid}: fit '{fit}' stored as "
                             f"'{card['fit']}' (fit is 'crop' or 'pad')")
            dur = card.get("duration_s")
            if isinstance(dur, (int, float)) and not isinstance(dur, bool) \
                    and 0 <= dur < _CARD_ANIM_MIN_S:
                card["duration_s"] = _CARD_ANIM_MIN_S
                notes.append(f"picture card {cid}: duration_s is its entrance/exit "
                             f"animation (0.12-1.2 s), so {dur:g} is stored as 0.12; "
                             "the card's window is start-end")
    return notes


def _style_field_hint(path, unknown):
    """A caption track given its style fields at the top level: say where
    they go instead of only listing them."""
    if path != "captions":
        return ""
    style = sorted(set(unknown) & set(CaptionStyle.model_fields))
    if not style:
        return ""
    return (f" {', '.join(style)} {'is a style field' if len(style) == 1 else 'are style fields'}: "
            "put them under captions.style, e.g. {\"mode\": \"from_transcript\", "
            "\"style\": {\"font\": ..., \"size\": ...}}.")


def apply_batch(before, operations, duration, allowed_keys, notes=None):
    """``notes``: an optional list the batch appends its receipts to (an
    item ended with a shortened programme)."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        raise ValueError("Supply between 1 and 64 edit operations.")
    if len(json.dumps(operations, allow_nan=False)) > 256_000:
        raise ValueError("This edit batch is too large; split it into smaller batches.")
    edl = copy.deepcopy(before)
    operations = copy.deepcopy(operations)
    # Keys already in the EDL were placed by Valmera's own tools (erase
    # patches, subject mattes, fetched stock, library sounds) and validated
    # when written; many never appear in the project's assets table. Only
    # media a batch INTRODUCES must be attached to the project — re-checking
    # the whole EDL refused every later batch once such a key was present.
    known = set(media_keys(before)) | _motion_refs(before)
    for op in operations:
        if not isinstance(op, dict) or set(op) - {"action", "layer", "value", "id"}:
            raise ValueError("An operation accepts action, layer, value and id only.")
        action, layer = op.get("action"), op.get("layer")
        if layer == "text":
            layer = op["layer"] = "texts"
        # An explicitly named object is an item patch, never a replacement
        # for its entire list. Preserve its siblings and still validate the
        # resulting EDL atomically below.
        if action == "set" and layer in ITEM_LAYERS and op.get("id") \
                and isinstance(op.get("value"), dict):
            action = op["action"] = "upsert"
        if layer == "fx":
            # Engine/test fx types render but are never authored.
            value = op.get("value")
            for item in (value if isinstance(value, list) else [value]):
                kind = item.get("type") if isinstance(item, dict) else None
                if kind in FX_TYPES and kind not in FX_AUTHORABLE_TYPES:
                    raise ValueError(f"fx type {kind} is internal to the "
                                     "engine and cannot be authored.")
        if not isinstance(layer, str) or layer not in LAYERS:
            raise ValueError(f"Unsupported batch layer: {layer}. "
                             "Use get_edl for exact layer shapes; transitions "
                             "are inside effects, or call set_transitions.")
        if action == "set":
            if "value" not in op:
                raise ValueError("A set operation requires value.")
            edl[layer] = copy.deepcopy(op["value"])
            if layer == "keep":
                edl.pop("split_keep_boundaries", None)
        elif action in ("upsert", "remove") and layer in ITEM_LAYERS:
            item_id = op.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("An item operation requires a stable id.")
            items = edl.get(layer) or []
            if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                raise ValueError(f"{layer} must be a list of objects.")
            edl[layer] = items
            at = next((i for i, item in enumerate(items) if item.get("id") == item_id), None)
            if action == "remove":
                if at is None:
                    raise ValueError(f"{layer} item {item_id} does not exist.")
                items.pop(at)
            else:
                value = op.get("value")
                if not isinstance(value, dict) or value.get("id", item_id) != item_id:
                    raise ValueError("An upsert requires an object with the same id.")
                value = {**(items[at] if at is not None else {}), **value, "id": item_id}
                if at is None:
                    items.append(value)
                else:
                    items[at] = value
        elif action == "reorder" and layer == "inserts":
            ids = op.get("value")
            items = edl.get("inserts") or []
            if not isinstance(items, list) or any(not isinstance(i, dict) or not isinstance(i.get("id"), str) for i in items):
                raise ValueError("Inserts must be a list of named objects.")
            if (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                    or len(set(ids)) != len(ids) or set(ids) != {i["id"] for i in items}):
                raise ValueError("Reorder must name every insert id exactly once.")
            if edl.get("keep"):
                raise ValueError("Reorder is for a canvas sequence; set insert positions for mixed footage.")
            lookup = {i["id"]: i for i in items}
            edl["inserts"] = [lookup[i] for i in ids]
            cursor = 0.
            for item in edl["inserts"]:
                item["at_output_s"] = cursor
                cursor += item["duration_s"]
        else:
            raise ValueError(f"Unsupported operation {action} on {layer}.")
    for layer in ITEM_LAYERS:
        items = edl.get(layer) or []
        if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
            raise ValueError(f"{layer} must be a list of objects.")
        ids = [i.get("id") for i in items if i.get("id")]
        if not all(isinstance(i, str) for i in ids):
            raise ValueError(f"{layer} ids must be strings.")
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate ids in {layer}.")
    if program_matte_live():
        # the program matte is not a project file (track 1A)
        known.add(PROGRAM_MATTE_KEY)
    if (set(media_keys(edl)) | _motion_refs(edl)) - known - set(allowed_keys):
        raise ValueError("Use media attached to this project. A referenced asset is not available here.")
    near = _near_misses(edl, {op["layer"] for op in operations})
    moved = end_with_programme(before, edl, operations)
    if notes is not None:
        notes.extend(near + moved)
    _check_new_motion_params(before, edl)
    # A graphic behind the subject needs a subject mask: a measured clip
    # (add_motion_graphic builds one) or, from Wave 1, the program matte.
    _behind_through_program_matte(edl, before)
    normalized = validate_edl(edl, duration).model_dump()
    check_fx_devices(before, normalized)
    if isinstance(allowed_keys, dict):
        for item in normalized.get("inserts") or []:
            asset_duration = (allowed_keys.get(item["asset_key"]) or {}).get("duration_s")
            if item["kind"] == "video" and asset_duration:
                end = (item.get("source_start_s") or 0) + item["duration_s"] * (item.get("rate") or 1)
                if end > float(asset_duration) + .05:
                    raise ValueError(f"Clip {item['id']} extends beyond its source video.")
    # Pydantic's historical EDL schema tolerates extras. Authoring a new
    # batch must not silently discard a misspelled field and claim success.
    def check_fields(raw, clean, path):
        if isinstance(raw, dict) and isinstance(clean, dict):
            unknown = set(raw) - set(clean)
            if unknown:
                raise ValueError(f"Unknown field in {path}: {', '.join(sorted(unknown))}."
                                 + _style_field_hint(path, unknown))
            for key, value in raw.items():
                check_fields(value, clean[key], f"{path}.{key}")
        elif isinstance(raw, list) and isinstance(clean, list):
            # validate_edl re-sorts item layers (motion by start, ...): pair
            # items by id where they have one, never by position — an item
            # added before an existing one is not that one's "unknown field"
            by_id = {c.get("id"): c for c in clean
                     if isinstance(c, dict) and c.get("id")}
            for index, value in enumerate(raw):
                if isinstance(value, dict) and value.get("id") in by_id:
                    checked = by_id[value["id"]]
                elif index < len(clean):
                    checked = clean[index]
                else:
                    continue
                check_fields(value, checked, f"{path}[{index}]")
    for layer in {op["layer"] for op in operations}:
        check_fields(edl[layer], normalized[layer], layer)
    return normalized
