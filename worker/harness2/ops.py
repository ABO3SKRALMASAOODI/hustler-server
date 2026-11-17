"""The op compiler: any list of ops applied to one staged EDL.

Each op is one object named by its key (``{"keep": [...]}``, ``{"card":
{...}}``, ``{"upsert": "texts", "id": ..., "value": ...}``, ``{"tool": name,
"args": {...}}``). Ops that change the keep run first, so program-time ops
read the new clock. Every op runs through today's engines against the
staged copy (harness2.stage): the keep tail with word, quiet-point and shot
placement, the verbs' own internals, edit_batch for layer ops. An op that
cannot land is set aside under not_applied in its engine's own words, with
what works instead, and the later ops still run.
"""
import copy
import json

import agent_tools
import audit
import edit_batch
from agent_tools import edl_signature
from schemas import program_duration
from timeline import Timeline

from harness2 import fit_length as fit_mod
from harness2.stage import first_line, result_refused

KEEP_OPS = ("keep", "cut", "restore", "speed", "fit_length")
LAYER_ACTIONS = ("set", "upsert", "remove", "reorder")
BATCH_CHUNK = 64
BATCH_BYTES = 200_000

# Lines today's keep tail appends that address the model, not the edit.
_ADVICE_PREFIXES = ("WARNING (large drop)", "NEXT:", "LOOP ADVISORY")


class Engines:
    """Hooks that do work outside the EDL (downloads, child projects, the
    Director). Tests replace them; the defaults call today's code."""

    @staticmethod
    def activity_track(ctx):
        return agent_tools._activity_track(ctx)

    @staticmethod
    def place(stage, item_id, at, dur, mode, source_start):
        import mcp_search
        return mcp_search.place(stage, item_id, at, dur, mode=mode,
                                source_start_s=source_start)

    @staticmethod
    def place_scenes(stage, scenes, narration):
        import mcp_search
        return mcp_search.place_scenes(stage, scenes,
                                       narration_asset_key=narration)

    @staticmethod
    def make_shorts(ctx, clips, style_note=None):
        return agent_tools.make_shorts(ctx, clips=clips,
                                       style_note=style_note)


ENGINES = Engines()


def op_name(op):
    if not isinstance(op, dict):
        return None
    for key in LAYER_ACTIONS:
        if key in op and isinstance(op.get(key), str):
            return "layer"
    if "tool" in op:
        return "tool"
    for key in op:
        if key in HANDLERS:
            return key
    return None


def order(ops):
    """[(original index, op)] with keep-changing ops first, order kept."""
    indexed = list(enumerate(ops))
    first = [(i, op) for i, op in indexed if op_name(op) in KEEP_OPS]
    rest = [(i, op) for i, op in indexed if op_name(op) not in KEEP_OPS]
    return first + rest


# --------------------------------------------------------------------------
# helpers

def _spans(value):
    """[[a, b], ...] from a list of pairs or {start, end} objects."""
    rows = value.get("spans") if isinstance(value, dict) else value
    out, bad = [], 0
    for row in rows if isinstance(rows, (list, tuple)) else []:
        try:
            if isinstance(row, dict):
                a, b = float(row.get("start")), float(row.get("end"))
            else:
                a, b = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError, KeyError):
            bad += 1
            continue
        out.append([a, b])
    return out, bad


def _clamp_spans(spans, duration, env):
    """Clamped to the source, inverted pairs turned round, empties dropped,
    overlaps merged. Each repair is said once."""
    fixed, flipped, clamped, empty = [], 0, 0, 0
    for a, b in spans:
        if b < a:
            a, b = b, a
            flipped += 1
        na, nb = max(0.0, a), min(float(duration), b) if duration else b
        if (na, nb) != (a, b):
            clamped += 1
        if nb - na < 0.05:
            empty += 1
            continue
        fixed.append([round(na, 3), round(nb, 3)])
    fixed.sort()
    merged = []
    for a, b in fixed:
        if merged and a <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if flipped:
        env.repair(f"{flipped} span(s) given end before start: turned round")
    if clamped:
        env.repair(f"{clamped} span end(s) held to the source "
                   f"(0-{float(duration):g} s)")
    if empty:
        env.repair(f"{empty} span(s) under 0.05 s dropped")
    if len(merged) < len(fixed):
        env.repair(f"{len(fixed) - len(merged)} overlapping or touching "
                   "span(s) merged; to show footage twice, place it again")
    return merged


def _tail_lines(result):
    lines = [ln.strip() for ln in str(result or "").splitlines()[1:]
             if ln.strip()]
    return [ln for ln in lines if not ln.startswith(_ADVICE_PREFIXES)]


def _write_keep(stage, keep, desc, env, exact=False):
    """The keep tail (agent_tools._write_keep) on the staged EDL."""
    if not keep:
        return "nothing would survive"
    result = agent_tools._write_keep(
        stage, keep, desc, snap_to_words=False if exact else True,
        place_all=True)
    text = str(result or "")
    if text.startswith("NO CHANGE"):
        return None
    if result_refused(text) or not text.startswith("EDL v"):
        return first_line(text)
    for line in _tail_lines(text):
        if line.upper().startswith("WARNING"):
            env.finding(line)
        else:
            env.repair(line)
    return None


def _clock(edl):
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _src_to_out(edl, t):
    try:
        return round(float(_clock(edl).src_to_out(float(t))), 3)
    except Exception:  # noqa: BLE001
        return None


def _run_verb(stage, name, args, env, index):
    """One registered verb on the staged EDL. True when it landed."""
    before = edl_signature(stage.edl)
    try:
        result = agent_tools.execute(stage, name, dict(args or {}))
    except (agent_tools.AskUser, agent_tools.FeatureUpgrade):
        raise
    except Exception as exc:  # noqa: BLE001 - an engine error is one op
        env.skip(index, f"{name} raised {type(exc).__name__}: "
                        f"{str(exc)[:240]}",
                 "the other ops landed; this op can be sent again")
        env.engine_errors.append({"op": index, "tool": name,
                                  "error": f"{type(exc).__name__}: {exc}"})
        return False
    changed = edl_signature(stage.edl) != before
    text = str(result or "")
    if changed:
        for line in _tail_lines(text):
            if line.upper().startswith("WARNING"):
                env.finding(f"{name}: {line}")
        return True
    # A verb that set nothing aside in its own words ("NOT APPLIED: ...")
    # did not land: saying "already so" sent p5873's model hunting for a
    # transition that was never placed.
    if result_refused(text) or text.lstrip().upper().startswith(
            "NOT APPLIED"):
        env.skip(index, f"{name}: {first_line(text)}",
                 _instead_for(name))
        env.refusals.append({"op": index, "tool": name,
                             "text": text[:2000]})
        return False
    env.already.append(f"op {index} ({name}): already so")
    return True


def _instead_for(name):
    return {
        "add_title_card": "a text op over a solid card: {'card': {...}} "
                          "at a different time, or {'text': {...}}",
        "insert_media": "{'place': {..., 'mode': 'overlay'}} over the cut",
        "add_overlay": "{'place': {..., 'mode': 'insert'}} at a cut",
        "add_music": "find(kind='music') and place its id with {'music'}",
        "add_sfx": "find(kind='sfx') and place its id with {'sfx'}",
    }.get(name, "the same change as layer ops (timeline(schema='ops'))")


# --------------------------------------------------------------------------
# keep-changing ops

def op_keep(stage, value, env, index):
    spans, bad = _spans(value)
    if bad:
        env.repair(f"{bad} keep row(s) were not [start, end] pairs: left out")
    exact = isinstance(value, dict) and value.get("exact") is True
    spans = _clamp_spans(spans, stage.duration, env)
    if not spans:
        env.skip(index, "no span of the keep list lies inside the source",
                 f"spans between 0 and {float(stage.duration):g} s")
        return False
    why = _write_keep(stage, spans,
                      f"keep set to {len(spans)} piece(s)", env, exact)
    if why:
        env.skip(index, why, "a keep list with at least one span")
        return False
    return True


def op_cut(stage, value, env, index):
    spans, _bad = _spans(value)
    spans = _clamp_spans(spans, stage.duration, env)
    current = stage.edl.get("keep") or []
    new = [list(x) for x in audit.subtract_spans(current, spans)]
    if not new:
        env.skip(index, "cutting that would leave nothing in the film",
                 "cut a smaller range, or keep the part to show")
        return False
    if new == [list(x) for x in current]:
        env.already.append(f"op {index} (cut): those seconds were already "
                           "out of the cut")
        return True
    exact = isinstance(value, dict) and value.get("exact") is True
    why = _write_keep(stage, new, f"cut {len(spans)} range(s)", env, exact)
    if why:
        env.skip(index, why)
        return False
    return True


def op_restore(stage, value, env, index):
    spans, _bad = _spans(value)
    spans = _clamp_spans(spans, stage.duration, env)
    current = [list(x) for x in stage.edl.get("keep") or []]
    if not spans:
        env.skip(index, "no range to restore inside the source")
        return False
    new = agent_tools._merge_touching(current + spans)
    exact = isinstance(value, dict) and value.get("exact") is True
    # A fit_length later in the same edit pays for these elsewhere.
    protect = list(getattr(stage, "_v2_protect", None) or []) + spans
    object.__setattr__(stage, "_v2_protect", protect)
    why = _write_keep(stage, new, f"restored {len(spans)} range(s)", env,
                      exact)
    if why:
        env.skip(index, why)
        return False
    return True


def film_to_source_spans(edl, a, b):
    """Source spans that play over film seconds [a, b] of ``edl`` (a span
    crossing a cut or an insert splits; existing speed is honoured)."""
    tl = _clock(edl)
    out = []
    for (s, e), pcs, off, length in zip(tl.segs, tl.pieces, tl.offsets,
                                        tl.seg_out_len):
        lo, hi = max(a, off), min(b, off + length)
        if hi - lo <= 1e-3:
            continue

        def at(t, pcs=pcs, off=off, e=e):
            rem = t - off
            for ps, pe, f in pcs:
                plen = (pe - ps) / f
                if rem <= plen + 1e-9:
                    return ps + max(0.0, rem) * f
                rem -= plen
            return e
        src_a, src_b = round(at(lo), 3), round(at(hi), 3)
        if src_b - src_a > 1e-3:
            out.append((src_a, src_b))
    return out


def op_speed(stage, value, env, index):
    """Speed rows are FILM seconds, like everything placed on the film
    (p5744: "slow motion after 6 s" is 6 s of the film the customer
    watches); ``"at_source": true`` on a row takes source seconds."""
    rows = value if isinstance(value, list) else [value]
    ok = False
    for row in rows:
        if not isinstance(row, dict):
            env.skip(index, "a speed row is {start, end, factor}")
            continue
        try:
            a, b = float(row.get("start")), float(row.get("end"))
        except (TypeError, ValueError):
            env.skip(index, "a speed row needs numeric start and end",
                     '{"start": 6, "end": 7.3, "factor": 0.6} (film s)')
            continue
        a, b = min(a, b), max(a, b)
        if row.get("at_source"):
            spans = [(a, b)]
        else:
            program = float(program_duration(stage.edl) or 0.0)
            if program and b > program:
                env.repair(f"speed end {b:g} s is past the film's end "
                           f"({program:.2f} s): held to the end")
                b = program
            spans = film_to_source_spans(stage.edl, a, b)
            if not spans:
                env.skip(index, f"film {a:g}-{b:g} s holds no footage of "
                                "the upload (an insert or past the end)",
                         f"film seconds between 0 and {program:.2f}")
                continue
        for src_a, src_b in spans:
            args = {"start": src_a, "end": src_b,
                    "factor": row.get("factor")}
            ok = _run_verb(stage, "set_speed", args, env, index) or ok
    return ok


def op_fit_length(stage, value, env, index):
    value = value if isinstance(value, dict) else {"max_s": value}
    track, why = ENGINES.activity_track(stage)
    if not track:
        env.skip(index, f"no activity measure for this video: {why}",
                 "keep/cut ranges chosen from look(show='chapters')")
        return False
    import activity
    score, fps, _used = activity.score(track)
    edl = stage.edl
    keep = edl.get("keep") or []
    program = float(program_duration(edl) or 0.0)
    other = program - sum(e - s for s, e in keep)
    words = (stage.index or {}).get("words") or []
    sentences = (stage.index or {}).get("sentences")
    lo, hi = value.get("min_s"), value.get("max_s")
    protect = list(getattr(stage, "_v2_protect", None) or [])
    new_keep, lines, measures = fit_mod.fit(
        keep, list(map(float, score)), fps, words, stage.duration,
        max_s=hi, min_s=lo, other_s=other, sentences=sentences,
        protect=protect)
    for line in lines:
        env.repair(line)
    if new_keep == [list(map(float, k)) for k in keep]:
        return True
    why = _write_keep(stage, new_keep, "fit_length", env)
    if why:
        env.skip(index, why)
        return False
    # The keep tail moves edges out of words; one more pass settles it.
    after = float(program_duration(stage.edl) or 0.0)
    if hi is not None and after > float(hi) + 0.05:
        keep2, lines2, _m = fit_mod.fit(
            stage.edl.get("keep") or [], list(map(float, score)), fps, words,
            stage.duration, max_s=hi, min_s=lo,
            other_s=after - sum(e - s for s, e in stage.edl.get("keep")),
            sentences=sentences, protect=protect)
        env.repair("second pass: " + "; ".join(lines2))
        _write_keep(stage, keep2, "fit_length (settle)", env)
    env.measures.update({"fit_length": dict(
        measures, after_s=round(float(program_duration(stage.edl) or 0), 1))})
    return True


# --------------------------------------------------------------------------
# film ops through the verbs

def op_card(stage, value, env, index):
    v = dict(value or {})
    at = v.get("at")
    if v.get("at_source") is not None:
        at = _src_to_out(stage.edl, v["at_source"])
        if at is None:
            env.skip(index, f"source {v['at_source']} s is not in the cut",
                     "restore that range first, or give 'at' on the film")
            return False
        env.repair(f"card at source {float(v['at_source']):g} s sits at "
                   f"{at:g} s on the film")
    args = {"text": v.get("text") or "", "at_output_s": float(at or 0.0),
            "duration_s": float(v.get("dur") or v.get("duration_s") or 2.2)}
    for src, dst in (("bg", "bg_color"), ("bg_color", "bg_color"),
                     ("color", "color"), ("template", "template"),
                     ("font", "font"), ("subtitle", "subtitle"),
                     ("accent_color", "accent_color")):
        if v.get(src) is not None:
            args[dst] = v[src]
    return _run_verb(stage, "add_title_card", args, env, index)


def _verb_op(name):
    def handler(stage, value, env, index):
        rows = value if isinstance(value, list) else [value]
        ok = False
        for row in rows:
            ok = _run_verb(stage, name, row if isinstance(row, dict) else {},
                           env, index) or ok
        return ok
    return handler


def op_audio(stage, value, env, index):
    v = dict(value or {})
    if isinstance(v.get("separate"), dict):
        return _run_verb(stage, "separate_music", v["separate"], env, index)
    rng = v.get("range") or [v.get("start"), v.get("end")]
    try:
        args = {"start": float(rng[0]), "end": float(rng[1]),
                "gain_db": float(v.get("gain_db"))}
    except (TypeError, ValueError, IndexError):
        env.skip(index, "audio takes {range: [start, end], gain_db} or "
                        "{separate: {voice_gain_db, music_gain_db}}")
        return False
    return _run_verb(stage, "set_volume", args, env, index)


def op_frame(stage, value, env, index):
    v = dict(value or {})
    ratio = v.get("ratio") or "9:16"
    if v.get("mode") in ("crop", "pad", "pad_blur") and not v.get("follow"):
        return _run_verb(stage, "set_frame", {"ratio": ratio,
                                              "mode": v["mode"]}, env, index)
    return _run_verb(stage, "auto_reframe",
                     {"ratio": ratio, "mode": v.get("mode") or "auto",
                      "follow": v.get("follow", True)}, env, index)


def op_grade(stage, value, env, index):
    v = value if isinstance(value, dict) else {"preset": value}
    if v.get("look"):
        return _run_verb(stage, "apply_look", {"name": v["look"]}, env, index)
    return _run_verb(stage, "set_color_grade",
                     {"preset": v.get("preset") or "none"}, env, index)


def op_transition(stage, value, env, index):
    v = value if isinstance(value, dict) else {"style": value}
    if v.get("at") is not None:
        return _run_verb(stage, "set_junction", v, env, index)
    return _run_verb(stage, "set_transitions", v, env, index)


def op_music(stage, value, env, index):
    v = dict(value or {})
    key = v.get("key") or v.get("storage_key") or v.get("asset_key")
    rid = v.get("id")
    if rid and not key:
        key, note = _music_key(stage, rid)
        if not key:
            env.skip(index, note, "find(kind='music') and use an id it "
                                  "returned")
            return False
        if note:
            env.repair(note)
    args = {"storage_key": key}
    for name in ("start", "end", "gain_db", "duck", "offset_s", "fade_in_s",
                 "fade_out_s", "loop", "purpose"):
        if v.get(name) is not None:
            args[name] = v[name]
    return _run_verb(stage, "add_music", args, env, index)


def _music_key(stage, rid):
    """(storage key, note) for a find(kind='music') id, provenance kept."""
    from harness2 import find_tool
    return find_tool.music_asset(stage, rid)


def op_sfx(stage, value, env, index):
    rows = value if isinstance(value, list) else [value]
    ok = False
    for row in rows:
        v = dict(row or {})
        key = v.get("key") or v.get("storage_key")
        if not key and v.get("id"):
            from harness2 import find_tool
            key, note = find_tool.sfx_asset(stage, v["id"])
            if not key:
                env.skip(index, note, "find(kind='sfx') and use an id it "
                                      "returned")
                continue
        args = {"storage_key": key, "at": v.get("at", 0.0)}
        for name in ("gain_db", "purpose", "offset_s", "dur_s"):
            if v.get(name) is not None:
                args[name] = v[name]
        ok = _run_verb(stage, "add_sfx", args, env, index) or ok
    return ok


def op_voiceover(stage, value, env, index):
    v = dict(value or {})
    args = {"asset_key": v.get("key") or v.get("asset_key"),
            "start_output_s": float(v.get("at") or v.get("start") or 0.0)}
    for name in ("gain_db", "duck_others", "source_offset_s", "duration_s"):
        if v.get(name) is not None:
            args[name] = v[name]
    return _run_verb(stage, "add_voiceover", args, env, index)


def op_place(stage, value, env, index):
    rows = value if isinstance(value, list) else [value]
    ok = False
    for row in rows:
        ok = _place_one(stage, dict(row or {}), env, index) or ok
    return ok


def _place_one(stage, v, env, index):
    at = v.get("at")
    if v.get("at_source") is not None:
        at = _src_to_out(stage.edl, v["at_source"])
    mode = v.get("mode") or "overlay"
    if mode not in ("overlay", "insert"):
        env.repair(f"place mode {mode!r} read as overlay")
        mode = "overlay"
    try:
        at = float(at or 0.0)
        dur = max(0.2, float(v.get("dur") or v.get("duration_s") or 3.0))
        offset = max(0.0, float(v.get("source_start") or 0.0))
    except (TypeError, ValueError):
        env.skip(index, "place takes numeric at, dur and source_start")
        return False
    key = v.get("key") or v.get("asset_key")
    link = str(v.get("id") or "").strip()
    if not key and link.lower().startswith(("http://", "https://")):
        # A find(kind='footage') answer is links. p5744 asked for bubbles,
        # got links, and the place op had no way to use one: "no tool".
        # Download it as a project clip, then place it like an upload.
        key, why = _fetch_link(stage, link)
        if not key:
            env.skip(index, f"place {link}: {why}",
                     "another link from the same find() answer")
            return False
        env.repair(f"downloaded {link} as {key}")
    if key:
        name = "add_overlay" if mode == "overlay" else "insert_media"
        args = ({"asset_key": key, "start": at, "duration_s": dur,
                 "source_start_s": offset, "fit": v.get("fit") or "cover"}
                if mode == "overlay" else
                {"asset_key": key, "at_output_s": at, "duration_s": dur,
                 "clip_start_s": offset})
        if mode == "overlay":
            for opt in ("opacity", "scale", "x", "y"):
                if v.get(opt) is not None:
                    args[opt] = v[opt]
        return _run_verb(stage, name, args, env, index)
    from harness2 import find_tool
    tried = []
    for candidate in find_tool.candidates(stage, v.get("id")):
        before = edl_signature(stage.edl)
        try:
            result = ENGINES.place(stage, candidate, at, dur, mode, offset)
        except Exception as exc:  # noqa: BLE001 - the next candidate
            result = f"failed: {type(exc).__name__}: {str(exc)[:160]}"
        if edl_signature(stage.edl) != before:
            if tried:
                env.repair(f"{v.get('id')} could not be placed "
                           f"({tried[0][1]}); the next result of the same "
                           f"search, {candidate}, is there instead")
            return True
        tried.append((candidate, first_line(result, 200)))
        if len(tried) >= 4:
            break
    why = tried[0][1] if tried else "no media with that id"
    env.skip(index, f"place {v.get('id')}: {why}",
             "another id from the same find() board")
    return False


def _fetch_link(stage, link):
    """(storage key, None) for a link downloaded as a project clip, or
    (None, why) in the downloader's own words."""
    fetched = getattr(stage, "urls_fetched", None)
    count = len(fetched) if fetched is not None else 0
    try:
        result = agent_tools.fetch_url(stage, link, as_kind="clip")
    except Exception as exc:  # noqa: BLE001 - the next link
        return None, f"download failed: {type(exc).__name__}: {str(exc)[:160]}"
    fetched = getattr(stage, "urls_fetched", None) or []
    if len(fetched) <= count:
        return None, first_line(result, 240)
    key = fetched[-1].get("storage_key")
    # v2 shows the pixels after the edit; it never holds a placement.
    pending = getattr(stage, "_pending_visual_review_assets", None)
    if isinstance(pending, set):
        pending.discard(str(key))
    return key, None


def op_scenes(stage, value, env, index):
    v = dict(value or {})
    items = v.get("items") or v.get("scenes") or []
    scenes = []
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        scenes.append({"id": row.get("id"), "asset_key": row.get("key")
                       or row.get("asset_key"),
                       "start_s": row.get("from", row.get("start_s")),
                       "end_s": row.get("to", row.get("end_s")),
                       "source_start_s": row.get("source_start", 0),
                       "motion": row.get("motion")})
    before = edl_signature(stage.edl)
    try:
        report = ENGINES.place_scenes(stage, scenes, v.get("narration"))
    except Exception as exc:  # noqa: BLE001
        env.skip(index, f"scenes raised {type(exc).__name__}: "
                        f"{str(exc)[:200]}")
        return False
    lines = [ln.strip() for ln in str(report or "").splitlines()
             if ln.strip() and not ln.strip().startswith("NEXT:")]
    for line in lines:
        env.finding(line)
    if edl_signature(stage.edl) == before:
        env.skip(index, first_line(report) or "no scene placed",
                 "scenes with ids from find() or uploaded asset keys")
        return False
    return True


def op_clips(stage, value, env, index):
    v = dict(value or {})
    items = v.get("items") or []
    clips = []
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        rng = row.get("range") or [row.get("start"), row.get("end")]
        try:
            clips.append({"start": float(rng[0]), "end": float(rng[1]),
                          "title": str(row.get("title") or "")[:120]})
        except (TypeError, ValueError, IndexError):
            env.repair(f"a clip without a numeric range was left out: "
                       f"{json.dumps(row)[:80]}")
    if not clips:
        env.skip(index, "clips takes items=[{range: [start, end], title}]")
        return False
    try:
        result = ENGINES.make_shorts(stage._base, clips,
                                     style_note=v.get("brief"))
    except Exception as exc:  # noqa: BLE001
        env.skip(index, f"clips raised {type(exc).__name__}: "
                        f"{str(exc)[:200]}")
        return False
    if result_refused(result):
        env.skip(index, first_line(result))
        return False
    env.finding(first_line(result, 600))
    job_id = getattr(stage._base, "serial_job_id", None)
    if job_id:
        env.running = {"handle": f"job:{job_id}",
                       "what": f"{len(clips)} clip project(s)"}
    return True


def op_tool(stage, op, env, index):
    name = str(op.get("tool") or "").strip()
    args = op.get("args") or {}
    if name not in agent_tools.TOOLS:
        near = [t for t in agent_tools.TOOLS if name and name in t][:5]
        env.skip(index, f"no verb named {name!r}",
                 ("did you mean " + ", ".join(near)) if near
                 else "timeline(schema='ops') lists the verbs")
        return False
    if not isinstance(args, dict):
        env.skip(index, "tool args must be an object")
        return False
    return _run_verb(stage, name, args, env, index)


def op_layers(stage, ops_with_index, env):
    """Layer ops through edit_batch.apply_batch, in chunks (no 64-op or
    256 KB cap). A chunk that fails is retried op by op, so one bad op
    is set aside and the rest land."""
    chunks, chunk, size = [], [], 0
    for index, op in ops_with_index:
        body = _layer_op(op)
        n = len(json.dumps(body, default=str))
        if chunk and (len(chunk) >= BATCH_CHUNK or size + n > BATCH_BYTES):
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append((index, body))
        size += n
    if chunk:
        chunks.append(chunk)
    keys = _project_keys(stage)
    landed = 0
    for chunk in chunks:
        if _apply_chunk(stage, chunk, keys, env):
            landed += len(chunk)
            continue
        for index, body in chunk:
            landed += 1 if _apply_chunk(stage, [(index, body)], keys, env,
                                        report=True) else 0
    return landed


def _layer_op(op):
    for action in LAYER_ACTIONS:
        if isinstance(op.get(action), str):
            body = {"action": action, "layer": op[action]}
            if "id" in op:
                body["id"] = op["id"]
            if "value" in op:
                body["value"] = op["value"]
            return body
    return {"action": "noop"}


def _project_keys(stage):
    try:
        return stage.db.run(edit_batch.project_media_keys,
                            stage.project_id) or set()
    except Exception:  # noqa: BLE001
        return set()


def _apply_chunk(stage, chunk, keys, env, report=False):
    notes = []
    try:
        updated = edit_batch.apply_batch(
            copy.deepcopy(stage.edl), [body for _i, body in chunk],
            stage.duration or None, keys, notes=notes)
    except Exception as exc:  # noqa: BLE001 - one op set aside, not all
        if report:
            env.skip(chunk[0][0], f"{chunk[0][1].get('action')} "
                                  f"{chunk[0][1].get('layer')}: "
                                  f"{str(exc)[:300]}",
                     "timeline(schema=<layer>) shows that layer's shape")
        return False
    result = stage.write_edl(updated, f"{len(chunk)} layer op(s)")
    if result_refused(result):
        if report:
            env.skip(chunk[0][0], first_line(result))
        return False
    for line in notes:
        env.repair(line)
    return True


HANDLERS = {
    "keep": op_keep, "cut": op_cut, "restore": op_restore,
    "speed": op_speed, "fit_length": op_fit_length,
    "card": op_card, "text": _verb_op("add_text"),
    "captions": _verb_op("add_captions"), "place": op_place,
    "scenes": op_scenes, "voiceover": op_voiceover, "music": op_music,
    "sfx": op_sfx, "audio": op_audio, "frame": op_frame, "grade": op_grade,
    "transition": op_transition, "zoom": _verb_op("add_zoom"),
    "clips": op_clips,
}

# The engine ops (H28-H30) are named here so an op naming them is read as
# what it is; until they land, the same result is reached with the ops above.
ENGINE_OPS = {
    "direct": "the film is built with ops here: keep + place/scenes + "
              "music + captions + card (the Director's op is being "
              "connected)",
    "finish": "the same departments as ops: captions, music, sfx, grade, "
              "transition, zoom, card (the finish op is being connected)",
}


def apply(stage, ops, env):
    """Run every op on the stage; returns how many landed."""
    landed = 0
    layer_batch = []
    for index, op in order(ops):
        name = op_name(op)
        if name == "layer":
            layer_batch.append((index, op))
            continue
        if layer_batch:
            landed += op_layers(stage, layer_batch, env)
            layer_batch = []
        if name is None:
            key = next(iter(op), None) if isinstance(op, dict) else None
            if key in ENGINE_OPS:
                env.skip(index, f"{key} is not connected in this build",
                         ENGINE_OPS[key])
            else:
                env.skip(index, f"unknown op {json.dumps(op)[:120]}",
                         "timeline(schema='ops') lists every op")
            continue
        before = edl_signature(stage.edl)
        try:
            if name == "tool":
                ok = op_tool(stage, op, env, index)
            else:
                ok = HANDLERS[name](stage, op[name], env, index)
        except (agent_tools.AskUser, agent_tools.FeatureUpgrade):
            raise
        except Exception as exc:  # noqa: BLE001 - an engine error is one op
            ok = False
            env.skip(index, f"{name} raised {type(exc).__name__}: "
                            f"{str(exc)[:240]}",
                     "the other ops landed; this op can be sent again")
            env.engine_errors.append({"op": index, "tool": name,
                                      "error": f"{type(exc).__name__}: "
                                               f"{exc}"})
        for line in stage.take_repairs():
            env.repair(line)
        if ok or edl_signature(stage.edl) != before:
            landed += 1
    if layer_batch:
        landed += op_layers(stage, layer_batch, env)
        for line in stage.take_repairs():
            env.repair(line)
    return landed
