"""Where each part of the edit came from: the upload, Valmera's automatic first cut, an earlier
request, or this turn (EDIT-CORE lane B, 16 Nov 2026).

The golden replays of 16 Nov found replies that were not true, and the cause was what the editing
model was shown, not the model:

- p5744: the first cut's own two sound effects were described to the customer as "in the
  original", because the timeline listed the effects with no word on who placed them;
- p5717: "I have cut the silences" on a turn that wrote nothing, because the 92.9 s -> 6.3 s cut
  the automatic first cut made looked like the editor's own work, and options were offered at
  source times the film no longer plays;
- p5744: the first cut said the upload "plays over its song", but no steady beat was measured in it.

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
    steady beat was measured, False when there is no sound track, None otherwise (no steady beat
    is not a measure of no music)."""
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
        if conf >= MUSIC_BEAT_CONF:
            music = True
            parts.append(f"a steady beat measured (confidence {conf:.2f}): music is likely in the upload")
        else:
            # A low beat confidence says only that no steady beat was found: a soft bed, ambient
            # or piano music and gameplay music read below it too (424 of 474 prod uploads in
            # 30 days), so it never says the upload has no music.
            parts.append(f"no steady beat measured (beat confidence {conf:.2f})")
    return "; ".join(parts), music


def upload_has_music(index):
    """True when a steady beat was measured in the upload's own sound."""
    return upload_sound(index)[1] is True


UPLOAD = "the upload as it came in"
_UNLOADED = "earlier history (versions not loaded here)"


def maker_of(version, first_cut_versions=(), turn_versions=(), job_of=None, created_by=None,
             upload_through=1):
    """Plain words for who made one EDL version. Version 1, and any version up to
    ``upload_through`` (the automatic first cut's before version) that no job and not the customer
    wrote, is the upload as it came in: the indexer stores v1 as 'agent' (indexer.insert_edl(default_edl,
    'agent')), so created_by never decides that."""
    version = int(version)
    if version in set(turn_versions or ()):
        return "this turn"
    if version in set(first_cut_versions or ()):
        return "the automatic first cut"
    job = (job_of or {}).get(version)
    if version == 1 or (not job and version <= int(upload_through or 1) and created_by != "user"):
        return UPLOAD
    if created_by == "user" and not job:
        return "the customer's own timeline edit"
    return "an earlier request" + (f" (job {job})" if job else "")


def _known_maker(version, first_cut_versions, turn_versions, job_of, upload_through):
    """The maker of a version that is not loaded, from the receipts alone; None when nothing says."""
    if version in turn_versions or version in first_cut_versions or version in job_of:
        return maker_of(version, first_cut_versions, turn_versions, job_of, None, upload_through)
    if version == 1 or version <= int(upload_through or 1):
        return UPLOAD
    return None


def _span_label(makers, own):
    """One label for a loaded version together with the unloaded versions just before it."""
    labels = []
    for lab in list(makers) + [own]:
        lab = _UNLOADED if lab is None else lab
        if lab != UPLOAD and (not labels or labels[-1] != lab):
            labels.append(lab)
    if len(labels) <= 1:
        return labels[0] if labels else (own or _UNLOADED)
    requests = [lab for lab in labels if lab.startswith("an earlier request")]
    if len(requests) > 2:
        jobs = [lab.split("(job ", 1)[1].rstrip(")") for lab in requests if "(job " in lab]
        shown = ", ".join(jobs[:6]) + (", ..." if len(jobs) > 6 else "")
        merged = f"{len(requests)} earlier requests" + (f" (jobs {shown})" if jobs else "")
        out = []
        for lab in labels:
            lab = merged if lab in requests else lab
            if not out or out[-1] != lab:
                out.append(lab)
        labels = out
    return ", then ".join(labels)


def _groups(rows, label, start_of=None):
    """Consecutive versions with the same maker: [(label, first_row, last_row, first_version)]."""
    out = []
    for row in rows:
        lab = label(row)
        if out and out[-1][0] == lab:
            out[-1][2] = row
        else:
            first = (start_of or {}).get(int(row["version"]), int(row["version"]))
            out.append([lab, row, row, first])
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


def describe(rows, index=None, source_s=None, first_cut_versions=(), turn_versions=(), job_of=None,
             upload_through=1):
    """WHERE THE EDIT CAME FROM: one line for the upload, one per maker in version order with what
    it changed (measured from the EDL diff), and who placed each item the film holds now. ``rows``:
    [{"version", "created_by", "json"}] oldest first; they need not be contiguous. A loaded version
    after versions that are not loaded speaks for that whole span, named from the receipts (or as
    earlier history when no receipt covers it). "" when there is nothing to say."""
    try:
        rows = [r for r in rows or [] if isinstance(r, dict) and isinstance(r.get("json"), dict)]
        rows.sort(key=lambda r: int(r["version"]))
        if not rows:
            return ""
        first_cut_versions = set(first_cut_versions or ())
        turn_versions = set(turn_versions or ())
        job_of = dict(job_of or {})
        effective, start_of = {}, {}
        previous_v = 0
        for row in rows:
            v = int(row["version"])
            own = maker_of(v, first_cut_versions, turn_versions, job_of, row.get("created_by"),
                           upload_through)
            missing = range(previous_v + 1, v)
            makers = [_known_maker(m, first_cut_versions, turn_versions, job_of, upload_through)
                      for m in missing]
            if makers and any(m != own for m in makers):
                # an unattributed version after unloaded ones is part of that earlier history
                known = _known_maker(v, first_cut_versions, turn_versions, job_of, upload_through)
                effective[v] = UPLOAD if own == UPLOAD else _span_label(
                    makers, own if known else None)
                start_of[v] = previous_v + 1
            else:
                effective[v] = own
                if makers:
                    start_of[v] = previous_v + 1
            previous_v = v

        def label(row):
            return effective[int(row["version"])]

        lines = ["WHERE THE EDIT CAME FROM (measured from the saved versions; describe the film to "
                 "the customer this way: never call Valmera's additions part of their upload, and "
                 "never call work an earlier step made your work this turn):"]
        sound, _music = upload_sound(index)
        src = float(source_s or ((index or {}).get("video") or {}).get("duration") or 0)
        lines.append("- The upload: " + (f"{src:.2f} s; " if src else "") + "its own sound: " + sound + ".")
        previous = None
        for lab, first, last, v0 in _groups(rows, label, start_of):
            v1 = int(last["version"])
            span = f"v{v0}" if v0 == v1 else f"v{v0}-v{v1}"
            if previous is None and lab == UPLOAD:
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


def _sound_name(item):
    for key in ("label", "name", "role"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:40]
    key = str(item.get("storage_key") or item.get("asset_key") or "")
    base = key.split("/")[-1].split(":")[-1].rsplit(".", 1)[0]
    return base.replace("_", " ").replace("-", " ").strip()[:40] or "sound"


def sounds_added(before_edl, after_edl):
    """The sound effects a version holds that ``before_edl`` did not, measured from the two EDLs,
    in plain words for a reply: "2 sound effects added: a riser at 0.67 s and an impact at
    1.98 s". "" when none were added."""
    try:
        before = _items(before_edl if isinstance(before_edl, dict) else {}).get("sfx") or {}
        after = _items(after_edl if isinstance(after_edl, dict) else {}).get("sfx") or {}
        new = [item for ident, item in after.items() if ident not in before]
        if not new:
            return ""

        def at(item):
            try:
                return float(item.get("at"))
            except (TypeError, ValueError):
                return 0.0
        new.sort(key=at)
        named = []
        for item in new[:8]:
            name = _sound_name(item)
            art = "an" if name[:1].lower() in "aeiou" else "a"
            named.append(f"{art} {name}{_where('sfx', item)}")
        if len(new) > 8:
            named.append(f"{len(new) - 8} more")
        listed = (", ".join(named[:-1]) + " and " + named[-1]) if len(named) > 1 else named[0]
        return f"{len(new)} sound effect{'s' if len(new) != 1 else ''} added: {listed}"
    except Exception:  # noqa: BLE001 - information only
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
    first_cut, job_of, befores = set(), {}, []
    for job in jobs or []:
        try:
            lo = int(job.get("before_version") or 0)
            hi = int(job.get("after_version") or 0)
        except (TypeError, ValueError):
            continue
        # The automatic first cut runs on the indexed upload: the versions up to its before
        # version are the upload as it came in. A later request's before version says nothing
        # about what came before it (receipts may start after unreceipted history).
        if hi > lo >= 1 and job.get("first_cut"):
            befores.append(lo)
        for v in range(lo + 1, hi + 1):
            job_of[v] = job.get("id")
            if job.get("first_cut"):
                first_cut.add(v)
    return describe(rows, index=index, source_s=source_s, first_cut_versions=first_cut,
                    turn_versions=turn_versions, job_of=job_of,
                    upload_through=min(befores) if befores else 1)
