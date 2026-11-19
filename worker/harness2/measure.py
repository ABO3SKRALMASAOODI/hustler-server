"""Measured facts about an edit, from the EDL itself (never from words)."""
from schemas import output_duration, program_duration
from timeline import Timeline


def clock_text(seconds):
    s = max(0.0, float(seconds or 0.0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{sec:04.1f}"
    return f"{int(m)}:{sec:04.1f}"


def _union(spans):
    total, end = 0.0, None
    for a, b in sorted(spans):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def edl_measure(edl, source_s=None, preview=None):
    edl = edl if isinstance(edl, dict) else {}
    try:
        program = float(program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        program = 0.0
    keep = [k for k in edl.get("keep") or [] if k and len(k) >= 2]
    kept = float(output_duration(keep) or 0.0) if keep else 0.0
    pics = []
    for o in edl.get("overlays") or []:
        try:
            a = float(o.get("start"))
            pics.append((a, a + float(o.get("duration_s") or 0.0)))
        except (TypeError, ValueError, AttributeError):
            continue
    for i in edl.get("inserts") or []:
        try:
            a = float(i.get("at_output_s"))
            pics.append((a, a + float(i.get("duration_s") or 0.0)))
        except (TypeError, ValueError, AttributeError):
            continue
    music = []
    for m in edl.get("music") or []:
        try:
            a = float(m.get("start") or 0.0)
            b = float(m.get("end") if m.get("end") is not None else program)
            music.append((a, min(b, program)))
        except (TypeError, ValueError, AttributeError):
            continue
    caps = edl.get("captions")
    out = {"program_s": round(program, 2), "pieces": len(keep),
           "kept_s": round(kept, 2),
           "kept_share": (round(kept / float(source_s), 3)
                          if source_s else None),
           "pictures_cover": (round(min(1.0, _union(pics) / program), 3)
                              if program and pics else 0.0),
           "music_cover": (round(min(1.0, _union(music) / program), 3)
                           if program and music else 0.0),
           "captions": bool(caps),
           "texts": len(edl.get("texts") or []),
           "sfx": len(edl.get("sfx") or []),
           "frame": (edl.get("frame") or {}).get("ratio")
           if isinstance(edl.get("frame"), dict) else None}
    speed = speed_measure(edl)
    if speed:
        out["speed"] = speed
    if preview:
        qc = preview.get("audio_qc") if isinstance(
            preview.get("audio_qc"), dict) else {}
        out["last_render"] = {
            "version": preview.get("edl_version"),
            "duration_s": preview.get("duration_s"),
            "lufs": qc.get("integrated_lufs"),
            "true_peak": qc.get("true_peak_dbtp") or qc.get("true_peak")}
    return out


def speed_measure(edl):
    """Where each speed span plays on the film: [{id, factor, film_s,
    source_s}], measured from the EDL's own clock."""
    spans = [sp for sp in (edl or {}).get("speed") or []
             if isinstance(sp, dict)]
    if not spans:
        return []
    try:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [], spans)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for sp in spans:
        try:
            s, e = float(sp["start"]), float(sp["end"])
        except (KeyError, TypeError, ValueError):
            continue
        film = tl.span_to_out(s, e)
        out.append({"id": sp.get("id"), "factor": sp.get("factor"),
                    "film_s": ([round(film[0][0], 2), round(film[-1][1], 2)]
                               if film else None),
                    "source_s": [round(s, 2), round(e, 2)]})
    return out


def _interval_sub(a, b):
    out = []
    for s, e in a:
        pieces = [[s, e]]
        for bs, be in b:
            nxt = []
            for ps, pe in pieces:
                if be <= ps or bs >= pe:
                    nxt.append([ps, pe])
                    continue
                if bs > ps:
                    nxt.append([ps, bs])
                if be < pe:
                    nxt.append([be, pe])
            pieces = nxt
        out.extend(p for p in pieces if p[1] - p[0] > 1e-3)
    return out


_LAYERS = ("inserts", "overlays", "texts", "music", "sfx", "voiceover",
           "vectors", "motion", "fx", "speed", "volume")


def change_line(prev, new, prev_version, new_version):
    """'v1 -> v2: removed 89 stretches (21:56); film 59:01.7 (was 81:00)'."""
    prev = prev or {}
    new = new or {}
    parts = []
    pk = [list(map(float, k)) for k in prev.get("keep") or []]
    nk = [list(map(float, k)) for k in new.get("keep") or []]
    if pk != nk:
        gone = _interval_sub(pk, nk)
        back = _interval_sub(nk, pk)
        if gone:
            parts.append(f"removed {len(gone)} stretch(es) "
                         f"({clock_text(sum(e - s for s, e in gone))})")
        if back:
            parts.append(f"restored {len(back)} stretch(es) "
                         f"({clock_text(sum(e - s for s, e in back))})")
    for layer in _LAYERS:
        a = {str(i.get("id")): i for i in prev.get(layer) or []
             if isinstance(i, dict)}
        b = {str(i.get("id")): i for i in new.get(layer) or []
             if isinstance(i, dict)}
        added = len(set(b) - set(a))
        removed = len(set(a) - set(b))
        changed = sum(1 for k in set(a) & set(b) if a[k] != b[k])
        bits = []
        if added:
            bits.append(f"+{added}")
        if removed:
            bits.append(f"-{removed}")
        if changed:
            bits.append(f"~{changed}")
        if bits:
            parts.append(f"{layer} {' '.join(bits)}")
    for key in ("captions", "frame", "effects", "master", "stem_mix",
                "noise_clean"):
        if prev.get(key) != new.get(key):
            parts.append(f"{key} changed")
    try:
        before = float(program_duration(prev) or 0.0)
        after = float(program_duration(new) or 0.0)
    except Exception:  # noqa: BLE001
        before = after = 0.0
    film = f"film {clock_text(after)}"
    if abs(after - before) > 0.05:
        film += f" (was {clock_text(before)})"
    return (f"v{prev_version} -> v{new_version}: "
            + ("; ".join(parts) if parts else "settings changed")
            + f"; {film}")


def program_clock(edl):
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])
