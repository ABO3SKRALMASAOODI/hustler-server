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


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _span_text(spans):
    spans = sorted(s for s in spans if s)
    if not spans:
        return ""
    return f"{spans[0][0]:.1f}-{spans[-1][1]:.1f} s"


def _device_span(item):
    """(start, end) of an fx item on the film clock: its window, or a
    junction's moment ("at", its duration either side of the cut)."""
    a, b = _f(item.get("start")), _f(item.get("end"))
    if a is not None and b is not None:
        return (a, b)
    at = _f(item.get("at"))
    if at is None:
        return None
    half = (_f(item.get("duration_s")) or 0.0) / 2.0
    return (max(0.0, at - half), at + half)


def _device_where(items):
    """Where a kind of device plays: junctions by their cuts (p5873 v2c read
    "junction x9 (0.0-0.0 s)" and told the person its dips may not have
    landed), windows by their span."""
    if all(_f(x.get("at")) is not None and _f(x.get("start")) is None
           for x in items):
        ats = sorted(_f(x.get("at")) for x in items)
        shown = ", ".join(f"{t:.1f}" for t in ats[:12])
        return "at the cuts " + shown + (" ..." if len(ats) > 12 else "") + " s"
    return _span_text([s for s in (_device_span(x) for x in items) if s])


def on_screen(edl):
    """What the film shows and plays now, on the film clock, one line per
    layer: captions, every text with its window, pictures over the cut,
    drawn elements and graphics, camera moves, speed, the look, music and
    sounds. Measured from the EDL, never from words: the model reads what
    is still there (captions a first cut left, a title held over the
    action) and takes out what the request excludes (p5728: "no subtitles"
    and the captions ran on at 32-37 s; a title covered the fighters for
    29 s). Each line names the op that removes it."""
    edl = edl if isinstance(edl, dict) else {}
    try:
        program = float(program_duration(edl) or 0.0)
        tl = program_clock(edl)
    except Exception:  # noqa: BLE001
        return []
    lines = []
    caps = edl.get("captions")
    if isinstance(caps, dict) and caps.get("mode") == "from_transcript":
        lines.append("captions: from the transcript over every spoken word "
                     "(off: {'captions': {'mode': 'off'}})")
    elif isinstance(caps, list) and caps:
        spans, words = [], []
        for c in caps:
            a, b = _f(c.get("start")), _f(c.get("end"))
            if a is None or b is None:
                continue
            for s0, s1 in tl.span_to_out(a, b) or []:
                spans.append((s0, s1))
            if len(words) < 5 and c.get("text"):
                words.append(str(c["text"])[:20])
        where = _span_text(spans)
        if where:
            lines.append(f"captions: {len(caps)} item(s) on film {where} ("
                         + ", ".join(f"'{w}'" for w in words)
                         + ") (off: {'captions': {'mode': 'off'}})")
    texts = [t for t in edl.get("texts") or [] if isinstance(t, dict)]
    for t in texts[:10]:
        a, b = _f(t.get("start")), _f(t.get("end"))
        if a is None or b is None:
            continue
        held = ""
        if program and b - a >= max(6.0, 0.4 * program):
            held = f", held {b - a:.1f} s of the {program:.1f} s film"
        y = _f(t.get("y"))
        lines.append(f"text {t.get('id')} \"{str(t.get('text') or '')[:28]}\" "
                     f"{t.get('template') or ''} {a:.1f}-{b:.1f} s{held}"
                     + (f", y {y:.2f}" if y is not None else "")
                     + f" (remove: {{'remove': 'texts', 'id': "
                       f"'{t.get('id')}'}})")
    if len(texts) > 10:
        lines.append(f"text: {len(texts) - 10} more (timeline() lists them)")
    ovs = [o for o in edl.get("overlays") or [] if isinstance(o, dict)]
    if ovs:
        own = sum(1 for o in ovs if str(o.get("asset_key") or "").startswith(
            "originals/"))
        parts = []
        for o in ovs[:8]:
            a, d = _f(o.get("start")), _f(o.get("duration_s"))
            if a is None or d is None:
                continue
            src = (f" from upload {float(o['source_start_s']):.1f} s"
                   if str(o.get("asset_key") or "").startswith("originals/")
                   and o.get("source_start_s") is not None else "")
            parts.append(f"{o.get('id')} {a:.1f}-{a + d:.1f} s{src}")
        lines.append(f"pictures over the cut: {len(ovs)}"
                     + (f" ({own} of them the upload's own footage from "
                        "other moments)" if own else "")
                     + ": " + "; ".join(parts)
                     + " (remove one: {'remove': 'overlays', 'id': ...})")
    ins = [i for i in edl.get("inserts") or [] if isinstance(i, dict)]
    if ins:
        lines.append(f"inserts spliced into the cut: {len(ins)} ("
                     + "; ".join(f"{i.get('id')} at {float(i.get('at_output_s') or 0):.1f} s"
                                 for i in ins[:6]) + ")")
    for m in [m for m in edl.get("motion") or [] if isinstance(m, dict)][:8]:
        a, b = _f(m.get("start")), _f(m.get("end"))
        if a is not None and b is not None:
            lines.append(f"graphic {m.get('id')} {m.get('template')} "
                         f"{a:.1f}-{b:.1f} s (remove: {{'remove': 'motion', "
                         f"'id': '{m.get('id')}'}})")
    fx = [f for f in edl.get("fx") or [] if isinstance(f, dict)]
    if fx:
        kinds = {}
        for f in fx:
            kinds.setdefault(str(f.get("type")), []).append(f)
        lines.append("devices: " + "; ".join(
            f"{k} x{len(v)} ({_device_where(v)})" for k, v in kinds.items()))
    eff = edl.get("effects") or {}
    zooms = [z for z in list(eff.get("zooms") or []) + list(edl.get("zooms")
                                                           or [])
             if isinstance(z, dict)]
    if zooms:
        lines.append(f"camera moves: {len(zooms)} ("
                     + "; ".join(f"{z.get('mode')} {float(z.get('start') or 0):.1f}-"
                                 f"{float(z.get('end') or 0):.1f} s"
                                 for z in zooms[:8]) + ")")
    else:
        lines.append("camera moves: none (the frame holds still throughout)")
    sp = speed_measure(edl)
    if sp:
        lines.append("speed: " + "; ".join(
            f"{r['factor']}x on film {r['film_s'][0]:.1f}-{r['film_s'][1]:.1f} s"
            for r in sp if r.get("film_s")))
    look = []
    if eff.get("grade"):
        look.append(f"preset {eff['grade']}")
    gc = {k: v for k, v in (eff.get("grade_custom") or {}).items()
          if v not in (None, 0, 0.0)}
    if gc:
        look.append("custom " + ", ".join(f"{k} {v:+.2f}" if isinstance(v, (int, float))
                                          else f"{k} {v}" for k, v in gc.items()))
    for s in eff.get("stylize") or []:
        if isinstance(s, dict):
            look.append(f"{s.get('kind')} {s.get('intensity')}")
    if look:
        lines.append("look: " + "; ".join(look))
    tr = eff.get("transition") or {}
    if tr.get("style") not in (None, "", "none", "cut"):
        lines.append(f"transitions: {tr.get('style')} {tr.get('duration_s') or ''}")
    music = [m for m in edl.get("music") or [] if isinstance(m, dict)]
    if music:
        lines.append(f"music: {len(music)} item(s): " + "; ".join(
            f"{m.get('id')} {str(m.get('storage_key') or '').rsplit('/', 1)[-1][:40]}"
            f"{' (muted)' if m.get('mute') else ''}" for m in music[:4])
            + (" (two at once play together: a new track replaces the bed "
               "unless asked to add)" if len(music) > 1 else ""))
    sfx = edl.get("sfx") or []
    if sfx:
        lines.append(f"sounds: {len(sfx)}")
    fr = edl.get("frame") or {}
    if isinstance(fr, dict) and fr.get("ratio"):
        lines.append(f"frame: {fr.get('ratio')} {fr.get('mode') or ''}".strip())
    return lines
