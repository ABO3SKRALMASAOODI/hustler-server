"""Where each part of the edit came from: the upload, Valmera's automatic first cut, an earlier
request, or this turn (EDIT-CORE lane B, 16 Nov 2026).

The golden replays of 16 Nov found replies that were not true, and the cause was what the editing
model was shown, not the model:

- p5744: the first cut's own two sound effects were described to the customer as "in the
  original", because the timeline listed the effects with no word on who placed them;
- p5717: "I have cut the silences" on a turn that wrote nothing, because the 92.9 s -> 6.3 s cut
  the automatic first cut made looked like the editor's own work, and options were offered at
  source times the film no longer plays;
- p5744: the first cut said the upload "plays over its song", but no music was measured in it.

Everything here is measured from the stored EDL versions and the index: an EDL diff per maker
(reply_grounding.edl_changes, which reads no words) and the upload's own sound. Pure functions
(dicts and strings in, strings out), so the v1 state block, harness v2's timeline() and the MCP
project state share one reading. Never raises.
"""
import json

import reply_grounding

# A steady beat measured at this confidence or above (perception.analyze_audio's tempo
# autocorrelation): the 16 Nov corpus measures a music video at 0.65 and an 11 s walk with
# footsteps and room tone at 0.37.
MUSIC_BEAT_CONF = 0.5


def _kept(keep):
    total = 0.0
    for span in keep or []:
        try:
            total += max(0.0, float(span[1]) - float(span[0]))
        except (TypeError, ValueError, IndexError):
            continue
    return total


def upload_sound(index):
    """The upload's own sound, measured: (line, music_measured). music_measured is True when a
    steady beat was measured, False when the beat measure says no music, None when unmeasured."""
    index = index or {}
    video = index.get("video") or {}
    words = [w for w in index.get("words") or [] if str(w.get("w") or "").strip()]
    perception = index.get("perception") or {}
    parts = []
    if video and not video.get("has_audio", True):
        return "the upload has no sound track", False
    parts.append(f"{len(words)} spoken word{'s' if len(words) != 1 else ''} transcribed")
    music = None
    try:
        conf = float(perception.get("bpm_conf"))
    except (TypeError, ValueError):
        conf = None
    if conf is not None:
        music = conf >= MUSIC_BEAT_CONF
        if music:
            parts.append(f"a steady beat measured (confidence {conf:.2f}): music is likely in the upload")
        else:
            parts.append(f"no music measured in the upload (beat confidence {conf:.2f}; a song "
                         f"measures {MUSIC_BEAT_CONF:.1f} or more)")
    return "; ".join(parts), music


def upload_has_music(index):
    """True when a steady beat was measured in the upload's own sound."""
    return upload_sound(index)[1] is True


def maker_of(version, first_cut_versions=(), turn_versions=(), job_of=None, created_by=None):
    """Plain words for who made one EDL version."""
    if version in set(turn_versions or ()):
        return "this turn"
    if version in set(first_cut_versions or ()):
        return "the automatic first cut"
    if created_by == "user" and int(version) == 1:
        return "the upload as it came in"
    if created_by == "user":
        return "the customer's own timeline edit"
    job = (job_of or {}).get(version)
    return "an earlier request" + (f" (job {job})" if job else "")


def _groups(rows, label):
    """Consecutive versions with the same maker: [(label, first_row, last_row)]."""
    out = []
    for row in rows:
        lab = label(row)
        if out and out[-1][0] == lab:
            out[-1][2] = row
        else:
            out.append([lab, row, row])
    return out


def _items(edl):
    """{layer: {id: item}} for the layers whose items carry ids."""
    out = {}
    for layer in ("sfx", "music", "inserts", "overlays", "texts", "vectors", "voiceover", "speed", "fx"):
        items = (edl or {}).get(layer) or []
        if isinstance(items, list):
            out[layer] = {str(it.get("id") or json.dumps(it, sort_keys=True, default=str)[:80]): it
                          for it in items if isinstance(it, dict)}
    zooms = ((edl or {}).get("effects") or {}).get("zooms") or []
    out["zooms"] = {str(z.get("id")): z for z in zooms if isinstance(z, dict)}
    return out


_NAMES = {"sfx": "sound effect", "music": "music track", "inserts": "insert", "overlays": "overlay",
          "texts": "title", "vectors": "graphic", "voiceover": "voiceover", "speed": "speed change",
          "fx": "effect", "zooms": "zoom"}


def _where(layer, item):
    for key in ("at", "start", "start_output_s", "at_output_s"):
        if item.get(key) is not None:
            try:
                return f" at {float(item[key]):.2f} s"
            except (TypeError, ValueError):
                pass
    return ""


def _label_of(layer, item):
    for key in ("label", "name", "title", "text", "role", "kind"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return f" '{value.strip()[:40]}'"
    key = str(item.get("storage_key") or item.get("asset_key") or "")
    if key:
        return f" '{key.split('/')[-1].split(':')[-1][:40]}'"
    return ""


def item_origins(rows, label):
    """{(layer, id): label} for every item the newest version holds: the maker of the first version
    (in the run that led to the newest) that holds the same item."""
    born = {}
    seen = set()
    for row in rows:
        here = _items(row.get("json"))
        for layer, items in here.items():
            for ident in items:
                key = (layer, ident)
                if key not in seen:
                    born[key] = label(row)
        seen = {(layer, ident) for layer, items in here.items() for ident in items}
        born = {k: v for k, v in born.items() if k in seen}
    return born


def describe(rows, index=None, source_s=None, first_cut_versions=(), turn_versions=(), job_of=None):
    """WHERE THE EDIT CAME FROM: one line for the upload, one per maker in version order with what
    it changed (measured from the EDL diff), and who placed each item the film holds now. ``rows``:
    [{"version", "created_by", "json"}] oldest first. "" when there is nothing to say."""
    try:
        rows = [r for r in rows or [] if isinstance(r, dict) and isinstance(r.get("json"), dict)]
        rows.sort(key=lambda r: int(r["version"]))
        if not rows:
            return ""

        def label(row):
            return maker_of(int(row["version"]), first_cut_versions, turn_versions, job_of,
                            row.get("created_by"))

        lines = ["WHERE THE EDIT CAME FROM (measured from the saved versions; describe the film to "
                 "the customer this way: never call Valmera's additions part of their upload, and "
                 "never call work an earlier step made your work this turn):"]
        sound, _music = upload_sound(index)
        src = float(source_s or ((index or {}).get("video") or {}).get("duration") or 0)
        lines.append("- The upload: " + (f"{src:.2f} s; " if src else "") + "its own sound: " + sound + ".")
        previous = None
        for lab, first, last in _groups(rows, label):
            v0, v1 = int(first["version"]), int(last["version"])
            span = f"v{v0}" if v0 == v1 else f"v{v0}-v{v1}"
            if previous is None and lab == "the upload as it came in":
                previous = last["json"]
                continue
            changes = reply_grounding.change_list_text(
                reply_grounding.edl_changes(previous or {"keep": [[0.0, src]]} if src else previous or {},
                                            last["json"]))
            who = lab + (" (Valmera, before the customer asked anything)"
                         if lab == "the automatic first cut" else "")
            lines.append(f"- {span}, {who}: {changes}; the film then kept "
                         f"{_kept(last['json'].get('keep')):.2f} s of the upload.")
            previous = last["json"]
        origins = item_origins(rows, label)
        now = _items(rows[-1]["json"])
        placed = {}
        for layer, items in now.items():
            for ident, item in items.items():
                who = origins.get((layer, ident))
                if who:
                    placed.setdefault(who, []).append(
                        f"{_NAMES.get(layer, layer)} {ident}{_label_of(layer, item)}{_where(layer, item)}")
        for who, items in placed.items():
            lines.append(f"- In the film now, placed by {who}: " + "; ".join(items[:16])
                         + (f"; and {len(items) - 16} more" if len(items) > 16 else "") + ".")
        lines.append("- Everything else in the film's sound is the upload's own.")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 - provenance is information, never a failure
        print(f"[provenance] skipped: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return ""


def turn_change(start_edl, now_edl, start_version=None, now_version=None):
    """This turn's change, measured from the EDL diff, as one line for the editor's reply."""
    try:
        if not isinstance(now_edl, dict):
            return ""
        start_edl = start_edl if isinstance(start_edl, dict) else {}
        changes = reply_grounding.edl_changes(start_edl, now_edl)
        a, b = _kept(start_edl.get("keep")), _kept(now_edl.get("keep"))
        head = "THIS TURN, MEASURED" + (f" (v{start_version} -> v{now_version})"
                                        if start_version is not None and now_version is not None else "")
        if not changes:
            return (f"{head}: nothing in the film has changed this turn; it plays {b:.2f} s as it did "
                    "when the turn started. Whatever the film already shows was made before this turn.")
        return (f"{head}: {reply_grounding.change_list_text(changes)}; the film plays {b:.2f} s "
                f"(it played {a:.2f} s when the turn started). Your reply describes this change and "
                "nothing an earlier step made.")
    except Exception:  # noqa: BLE001
        return ""


def from_lineage(rows, jobs, index=None, source_s=None, turn_versions=()):
    """describe() from db.edl_lineage's rows and receipts: each receipt's versions (before, after]
    belong to its job; a job with payload.first_cut is the automatic first cut."""
    first_cut, job_of = set(), {}
    for job in jobs or []:
        try:
            lo = int(job.get("before_version") or 0)
            hi = int(job.get("after_version") or 0)
        except (TypeError, ValueError):
            continue
        for v in range(lo + 1, hi + 1):
            job_of[v] = job.get("id")
            if job.get("first_cut"):
                first_cut.add(v)
    return describe(rows, index=index, source_s=source_s, first_cut_versions=first_cut,
                    turn_versions=turn_versions, job_of=job_of)
