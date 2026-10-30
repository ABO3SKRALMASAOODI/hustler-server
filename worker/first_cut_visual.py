"""First cut, visual variety (Q1, 30 Oct 2026).

The C2 First cut held one composition for 8-11 s on talking heads (judge
pacing 4-5.5: "11 s of an identical composition"), kept a screen recording
whole and tiny (vfr: "unreadable screen text", a hard fault), fitted a
landscape explainer animation small between blurred bands (u4899: "full-bleed,
follow the pipe"), and kept any slow piece over a beat whole and uncaptioned.
The owner's references (.valmera/week-1029/REFERENCE_PLAYBOOK.md) change the
picture every 2-3 s. This module is what the First cut adds, all with the
editor's own shipped tools:

  talking heads   CUT-INS: a closer camera on the same take at phrase turns
                  (hard cuts, never animated zooms), alternating wide and
                  close so no framing holds past ~4 s; CUTAWAYS: B-roll on
                  the concrete things the speaker names (the scout's picks,
                  source_broll: licensed, judged, colour-matched, photos
                  moving); the C2 slams and cards stay, clear of both.
  screen          a 2-3x camera that travels (add_zoom_path, eased) to the
                  region each sentence is about: the trial model points at
                  it on the frame (computer-use grounding), or the content
                  column's top after each page change without the lane.
  explainers      a landscape animation with narration and no face is
                  cropped to 9:16 and the crop follows the action (colour,
                  detail and motion), with follow.plan's restraint, instead
                  of a strip between blurred bands.
  finished piece  "kept whole" only when the voice is SUNG (held words, low
                  confidence, a repeated chorus): a slow narrated clip over
                  music is a speech source and is cut and captioned.

WHO GETS IT (owner rule D1, strategy-1025/DECISIONS.md): trial accounts and
NEW subscribers only. A payer whose subscription began before the D1 cutoff
(claude_plans.grandfathered) gets the First cut exactly as before this
module (``engine_on``); tests/test_first_cut_visual.py proves it.
"""
import base64
import json
import re
import subprocess

# --------------------------------------------------------------------------
#  Who gets it
# --------------------------------------------------------------------------


def engine_on(conn, user_id, subscribed):
    """True for an unsubscribed (trial) account and a NEW subscriber
    (claude_plans.applies: subscribed after the D1 cutoff, or an internal
    test account); False for every grandfathered subscriber and on any
    doubt about a payer."""
    if not subscribed:
        return True
    if conn is None or user_id is None:
        return False
    import claude_plans
    return bool(claude_plans.safe(conn, claude_plans.applies, user_id, True,
                                  default=False))


# --------------------------------------------------------------------------
#  Sung or spoken (the finished-piece guard)
# --------------------------------------------------------------------------

SUNG_WORD_S = 0.45      # median word length: sung >= 0.45 (u5225 1.05 s);
                        # speech 0.20-0.34 (phone, talk, vlog, vfr, u4899)
SUNG_SOFT_WORD_S = 0.36
SUNG_CONFIDENCE = 0.90  # sung words transcribe unsure (u5225 0.88; speech 0.96+)
CHORUS_SHARE = 0.30     # repeated 4-word runs: a chorus, not a narration


def _tok(word):
    return re.sub(r"[^\w']", "", str(word or "").lower())


def voice_profile(index):
    """{"n", "word_s" (median word length), "conf" (median confidence or
    None), "repeat" (share of 4-word runs heard more than once)}."""
    words = [w for w in (index or {}).get("words") or []
             if isinstance(w, dict) and str(w.get("w") or "").strip()]
    durs = []
    for w in words:
        try:
            a = float(w["t0"] if w.get("t0") is not None else w.get("s"))
            b = float(w["t1"] if w.get("t1") is not None else w.get("e"))
        except (TypeError, ValueError, KeyError):
            continue
        if b > a:
            durs.append(b - a)
    durs.sort()
    confs = sorted(float(w["confidence"]) for w in words
                   if isinstance(w.get("confidence"), (int, float)))
    toks = [t for t in (_tok(w.get("w")) for w in words) if t]
    grams = [tuple(toks[i:i + 4]) for i in range(len(toks) - 3)]
    seen = {}
    for g in grams:
        seen[g] = seen.get(g, 0) + 1
    repeat = (sum(1 for g in grams if seen[g] > 1) / len(grams)) if grams else 0.0
    return {"n": len(words),
            "word_s": durs[len(durs) // 2] if durs else 0.0,
            "conf": confs[len(confs) // 2] if confs else None,
            "repeat": round(repeat, 3)}


def sung(index):
    """The voice is singing, not talking: words held long, or medium-long
    and transcribed unsure, or a chorus that repeats."""
    p = voice_profile(index)
    if p["n"] < 8:
        return False
    if p["word_s"] >= SUNG_WORD_S:
        return True
    if p["word_s"] >= SUNG_SOFT_WORD_S and p["conf"] is not None \
            and p["conf"] < SUNG_CONFIDENCE:
        return True
    return p["n"] >= 16 and p["repeat"] >= CHORUS_SHARE


def spoken_slowly(index):
    """Clearly spoken words (short, as speech is) at a slow overall rate:
    a narration with long pauses, possibly over a music bed. Its pauses are
    cut even where the bed keeps them from being quiet."""
    p = voice_profile(index)
    return p["n"] >= 8 and 0.0 < p["word_s"] <= SUNG_SOFT_WORD_S


# --------------------------------------------------------------------------
#  B-roll moments named by the scout
# --------------------------------------------------------------------------

ENDING_RULE = (
    "When the video closes on its own conclusion (the result, the benefit, "
    "a tagline), END there, starting later if you must, so the piece pays "
    "off; the 20-60 seconds count speech after pauses are cut. ")
BROLL_RULE = (
    "BROLL: 5 to 8 moments inside your span (not in its first sentence), "
    "about one for every 5 seconds of it, "
    "where a top editor would cut away to footage of what is said: a "
    "concrete thing, place, person, object or action the viewer could SEE "
    "(\"the sun\", \"a factory\", \"Stanford\", \"a rocket launch\"), never "
    "an abstract idea. Each: the sentence id, \"word\": the 1-2 CONSECUTIVE "
    "words copied EXACTLY as written where the shot starts, and \"query\": "
    "a 2-5 word stock-footage search describing what the shot SHOWS, as "
    "literal as the words (\"the sun\" -> \"the sun in space\"). Spread "
    "them through the WHOLE span, about 3-5 seconds apart, the last ones in "
    "its final third. ")
BROLL_JSON = (", \"broll\": [{\"id\": \"<sentence id>\", \"word\": "
              "\"<1-2 words>\", \"query\": \"<2-5 words>\"}]")


def broll_spans(index, by_id, items):
    """[(src_t0, src_t1, word, query)] for the scout's moments whose word
    is said word for word in its sentence (anything else is dropped)."""
    import first_cut
    out = []
    for item in (items or [])[:10]:
        if not isinstance(item, dict):
            continue
        query = " ".join(str(item.get("query") or "").split())[:80]
        if not query or len(query.split()) > 7:
            continue
        row = by_id.get(str(item.get("id")))
        if row is None:
            row = by_id.get(re.sub(r"\D", "", str(item.get("id"))))
        if row is None:
            continue
        found = first_cut._phrase_words(index, row, item.get("word"))
        if not found or len(found) > 3:
            continue
        word = " ".join(str(w["w"]).strip(" ,.;:!?\"'") for w in found)
        out.append((first_cut._wt(found[0], "t0"),
                    first_cut._wt(found[-1], "t1"), word, query))
    print(f"[first_cut] b-roll picks: {len(items or [])} named, "
          f"{len(out)} said word for word", flush=True)
    return out


BROLL_S = 2.1           # one cutaway (references: 0.5-2.5 s)
BROLL_MIN_S = 1.3
BROLL_GAP_S = 1.2       # the face returns at least this long between them
BROLL_SHARE = 0.45      # at most this share of the program (refs 30-60%)
BROLL_TAIL_S = 1.4      # the ending is on the speaker


def broll_windows(moments, tl, prog, hook_end):
    """[(start, end, word, query)] program windows for the cutaways: after
    the hook, BROLL_GAP_S of face between them, clear of the ending, at most
    BROLL_SHARE of the program."""
    cands = []
    for t0, _t1, word, query in moments or []:
        a = tl.src_to_out(t0)
        if a is not None:
            cands.append((float(a), word, query))
    cands.sort()
    out, used, last = [], 0.0, None
    cands = [c for c in cands if c[0] >= hook_end + 0.15]
    for k, (a, word, query) in enumerate(cands):
        start = round(a, 2)
        if last is not None and start < last + BROLL_GAP_S:
            continue
        end = min(start + BROLL_S, prog - BROLL_TAIL_S)
        # shorten to let the next named thing have its own cutaway
        nxt = [c[0] for c in cands[k + 1:] if c[0] >= start + BROLL_MIN_S + BROLL_GAP_S]
        if nxt and nxt[0] - BROLL_GAP_S < end:
            end = max(start + BROLL_MIN_S, nxt[0] - BROLL_GAP_S)
        end = round(end, 2)
        if end - start < BROLL_MIN_S - 1e-6:
            continue
        if used + (end - start) > BROLL_SHARE * prog:
            break
        out.append((start, end, word, query))
        used += end - start
        last = end
    return out


def place_broll(ctx, steps, started, run, moments, hook_end, spend=None):
    """Cut away to B-roll on the scout's concrete moments (source_broll:
    every live library, licence-checked, judged on the real files,
    colour-matched; photos move). Returns the placed program windows."""
    import schemas
    import stock
    from timeline import Timeline
    try:
        edl = ctx.latest_edl()["json"]
        tl = Timeline(edl.get("keep") or [], [], None)
        prog = float(schemas.program_duration(edl))
    except Exception:
        return []
    print(f"[first_cut] b-roll at program s: "
          f"{[(None if tl.src_to_out(m[0]) is None else round(tl.src_to_out(m[0]), 1), m[2]) for m in moments or []]}",
          flush=True)
    wins = broll_windows(moments, tl, prog, hook_end)
    print(f"[first_cut] b-roll: {len(moments or [])} scout moments, "
          f"{len(wins)} windows", flush=True)
    if not wins:
        return []
    # video where a keyed library is live (production: Pexels), photos
    # (moving) from the keyless libraries otherwise
    kind = "any" if stock.video_available() else "photo"
    specs = [{"id": f"b{n}", "query": q, "word": w, "at": a,
              "duration_s": round(b - a, 2), "kind": kind,
              "purpose": f"show what '{w}' names, as it is said"}
             for n, (a, b, w, q) in enumerate(wins, 1)]
    print(f"[first_cut] b-roll moments: {[(s['at'], s['word'], s['query']) for s in specs]}",
          flush=True)
    import first_cut
    with first_cut._timed(ctx, "broll_source"):
        run(ctx, steps, "source_broll", {"moments": specs, "max": len(specs)},
            started)
    if spend is not None:
        import first_cut
        with first_cut._timed(ctx, "broll_check"):
            return verify_broll(ctx, steps, started, run, spend)
    return covered(ctx, ("broll",))


_BROLL_CHECK = (
    "Each numbered picture is a cutaway an editor placed while the speaker "
    "says the quoted words. KEEP it when a viewer would see at once that it "
    "shows what is being said (the thing named, literally or as a clear "
    "example). DROP it when it is off-topic, a different thing that only "
    "shares a word with the line, mostly text, a screenshot, a QR code, a "
    "diagram nobody can read, noise, or broken. Answer with JSON only: "
    "{\"drop\": [<picture numbers>]}.")


def verify_broll(ctx, steps, started, run, spend):
    """One look by the trial model at every placed cutaway with the words
    said over it; the off-topic ones are removed (judges: "a QR code on
    'AlexNet'", "power lines with a moon on 'roughly'"). Returns the
    cutaway windows that stand. Without the lane every cutaway stands."""
    import claude_lane
    import device_planner
    import llm
    import media_cache
    model = getattr(ctx, "first_cut_model", None)
    try:
        edl = ctx.latest_edl()["json"]
    except Exception:
        return covered(ctx)
    items = [it for it in edl.get("fx") or [] if isinstance(it, dict)
             and it.get("type") == "broll" and it.get("start") is not None]
    if not items or not model or not claude_lane.available():
        return covered(ctx)
    try:
        words = device_planner.program_words(edl, ctx.index or {})
    except Exception:
        words = []
    parts, ids = [], {}
    for n, it in enumerate(items, 1):
        key = ((it.get("source") or {}).get("asset_key"))
        try:
            local = media_cache.lease(key, ctx.workdir, f"broll_check_{n}")
        except Exception:
            local = None
        if not local:
            continue
        seek = [] if it.get("media") == "still" else \
            ["-ss", f"{float((it.get('source') or {}).get('offset_s') or 0) + 0.8:.2f}"]
        try:
            jpg = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", *seek, "-i", local,
                 "-frames:v", "1", "-vf", "scale=384:-2", "-f", "image2",
                 "-c:v", "mjpeg", "-q:v", "6", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            jpg = b""
        if not jpg:
            continue
        a, b = float(it["start"]), float(it["end"])
        said = " ".join(str(w.get("w") or "") for w in words
                        if a - 1.2 <= float(w["t0"]) <= b + 0.3)
        ids[n] = str(it.get("id"))
        parts.append({"type": "text", "text": f"Picture {n}, said over it: "
                                              f"\"{said[:200]}\""})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if not ids:
        return covered(ctx)
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": _BROLL_CHECK},
                    {"role": "user", "content": parts}],
            max_tokens=2000, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("first_cut_broll_check", {"model": model,
                                              "pictures": len(ids)},
                   {"content": text}, resp.usage)
        print(f"[first_cut] b-roll check: {text.strip()[:200]}", flush=True)
        match = re.search(r"\{.*\}", text, re.S)
        drop = (json.loads(match.group(0)) if match else {}).get("drop") or []
    except Exception as exc:  # noqa: BLE001 — the cutaways stand
        print(f"[first_cut] b-roll check failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return covered(ctx)
    gone = []
    for d in drop:
        try:
            if int(d) in ids:
                gone.append(ids[int(d)])
        except (TypeError, ValueError):
            continue
    if gone:
        run(ctx, steps, "remove_broll", {"ids": gone}, started)
    return covered(ctx)


def covered(ctx, types=("broll",)):
    """Program windows where the picture is replaced (cutaways)."""
    try:
        edl = ctx.latest_edl()["json"]
    except Exception:
        return []
    out = []
    for it in edl.get("fx") or []:
        if isinstance(it, dict) and it.get("type") in types \
                and it.get("start") is not None:
            out.append((float(it["start"]), float(it["end"])))
    return sorted(out)


CAPTION_SCALE = 1.6     # judges, C2 and Q1: "captions 20-30% larger"


def caption_style(style, talking, screen, vertical):
    """(style, words per caption) for the new engine: 1-3 word phrases in
    speech order (the editorial look's emphasis word tucked above the line
    read as "delivered / rolled", out of order, to the judge; single words
    flickered), larger. A talking head gets the inline serif look (the
    emphasis word turns gold serif italic in place: the slams' two
    families and accent); a screen or an animation (white snow) gets the
    phrase pill so it reads on any picture, at one steady band."""
    if screen:
        # a screen keeps the C2 look (judges: editorial 6-7 over the docs;
        # the serif line "dissolves into the doc text", 4-4.5)
        return dict(style), 2
    out = dict(style, size_scale=CAPTION_SCALE, motion_look="serif",
               single_line=True)
    if not talking:
        # one steady band (judges: captions hopping top to bottom); the
        # look's own legibility (dark ink, a glyph scrim) on bright plates
        out["anchor_y"] = 0.66 if vertical else 0.86
    return out, 3


TAIL_S = 0.45           # live picture after the last word (refs 0.2-0.7)


def muted_tail(ctx, steps, started, run):
    """A screen or an animation (no mouth on screen) ends on TAIL_S of live
    picture after its last word even when the narrator runs straight on
    (vfr ended 0.0 s after "copy": judges, twice): the program runs on
    TAIL_S, and that tail's sound and captions are muted. None when the cut
    already has a live tail."""
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        keep = [[float(a), float(b)] for a, b in edl.get("keep") or []]
    except Exception:
        return None
    words = [w for w in (ctx.index or {}).get("words") or []
             if isinstance(w, dict) and w.get("w") is not None
             and w.get("t0") is not None and w.get("t1") is not None]
    if not keep or not words:
        return None
    s1, e1 = keep[-1]
    spoken = [w for w in words if s1 <= float(w["t0"]) < e1]
    if not spoken:
        return None
    last_end = float(spoken[-1]["t1"])
    new_e = min(float(ctx.duration or e1), last_end + TAIL_S)
    if e1 - last_end >= 0.3 or new_e - e1 < 0.1:
        return None
    runon = [w for w in words if last_end - 0.01 <= float(w["t0"]) < new_e]
    mute_from = min([last_end + 0.02] + [float(w["t0"]) - 0.01 for w in runon])
    keep[-1][1] = round(new_e, 3)
    res = run(ctx, steps, "keep_segments", {"segments": keep,
                                            "snap_to_words": False}, started)
    if res is None or str(res).lstrip().upper().startswith(
            ("REJECTED", "FAILED", "CORRECTION", "NO CHANGE")):
        return None
    run(ctx, steps, "set_volume", {"start": round(mute_from, 3),
                                   "end": round(new_e + 0.05, 3),
                                   "gain_db": -60.0}, started)
    try:
        prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
        tail_at = prog - (new_e - mute_from)
        # the run-on words are dropped from the captions once they exist
        # (judges: a dangling "And" captioned in the tail)
        # scoped from the tail to well past the end: a later request that
        # restores earlier footage shifts the tail later (trial vfr: the
        # "And" came back), never earlier than the cut it was made on
        ctx.first_cut_tail_words = [
            {"from": str(w["w"]).strip(), "to": "",
             "start": round(max(0.0, tail_at - 0.3), 2),
             "end": round(prog + 600.0, 2)}
            for w in runon if str(w.get("w") or "").strip()]
    except Exception:
        pass
    return res


def drop_tail_words(ctx, steps, started, run):
    """After the captions exist: delete the muted run-on words of the live
    tail from them (set_caption_fixes, scoped to the tail)."""
    fixes = getattr(ctx, "first_cut_tail_words", None) or []
    if fixes:
        run(ctx, steps, "set_caption_fixes", {"replacements": fixes,
                                              "operation": "append"}, started)


BAND_PURPOSE = "crop the source's own static bands"


def refresh_band_crop(ctx, steps, started, run):
    """The band crop is one zoom over the program; a zoom stays anchored to
    the footage it covered, so a cut that later EXTENDS the program (the
    live tail, a request that restores earlier footage) leaves the new
    seconds uncropped and the bars show (judges, trial phone/phonehook:
    "the first frame and the last 0.5 s in a small, banded layout"). After
    the last cut the crop is re-laid over the whole program. Returns True
    when it was re-laid."""
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        prog = float(schemas.program_duration(edl))
    except Exception:
        return False
    zooms = ((edl.get("effects") or {}).get("zooms") or [])
    band = [z for z in zooms if isinstance(z, dict)
            and str(z.get("purpose") or "").startswith(BAND_PURPOSE)]
    if len(band) != 1:
        return False
    z = band[0]
    if float(z.get("start") or 0) <= 0.01 and float(z.get("end") or 0) >= prog - 0.01:
        return False
    if run(ctx, steps, "remove_zoom", {"id": z["id"]}, started) is None:
        return False
    args = {"start": 0.0, "end": round(prog, 2),
            "strength": z.get("strength"), "mode": "punch", "ramp_s": 0.0,
            "purpose": z.get("purpose")}
    for k in ("cx", "cy"):
        if z.get(k) is not None:
            args[k] = z[k]
    run(ctx, steps, "add_zoom", args, started)
    return True


EXPLAINER_MAX_SPEECH_S = 60.0


def conclusion_end(rows, first, last, ends_thought):
    """A narrated explainer (no face) closes on its own conclusion: the
    result, the benefit, the tagline (u4899: "a new service life of at
    least 50 years. Safe, reliable, sustainable."); a scout end before it
    stopped on a parts list (judges: "no payoff"). The last row that ends a
    thought, when the speech from ``first`` to it fits
    EXPLAINER_MAX_SPEECH_S; else ``last``."""
    order = [str(r["id"]) for r in rows]
    try:
        i, j = order.index(str(first["id"])), order.index(str(last["id"]))
    except ValueError:
        return last
    k = len(rows) - 1
    while k > j and not ends_thought(rows[k]):
        k -= 1
    if k <= j:
        return last
    speech = sum(max(0.0, float(r["t1"]) - float(r["t0"])) for r in rows[i:k + 1])
    return rows[k] if speech <= EXPLAINER_MAX_SPEECH_S else last


OPENING_RULE = (
    "Never START on a sentence that opens with a subordinate clause "
    "(\"Since...\", \"Because...\", \"If...\", \"When...\"): start "
    "where the claim stands on its own. ")


# --------------------------------------------------------------------------
#  Cut-ins: a closer camera on the same take at phrase turns
# --------------------------------------------------------------------------

CUT_IN_TARGET_S = 2.4   # a framing lasts about this long (refs: 2-3 s)
CUT_IN_MIN_S = 1.3
CUT_IN_MAX_S = 3.5
CUT_IN_FIRST_S = 1.5    # the first change lands under the hook title
CUT_IN_STRENGTH = (0.38, 0.52)      # close, tighter (no bands): 1.4-1.5x
CUT_IN_BANDED = (0.32, 0.44)        # over a band crop's own zoom: ~1.25-1.3x
CUT_IN_SOFT = (0.16, 0.24)          # an animation's crop (no face to frame)
_TURN = re.compile(r"[,.;:!?]$")


def phrase_points(words, gap=0.12):
    """Program times where a framing may change: a word onset after a
    pause of ``gap`` or more, or after a word that ends a clause."""
    out = []
    for prev, w in zip(words, words[1:]):
        if float(w["t0"]) - float(prev["t1"]) >= gap or \
                _TURN.search(str(prev.get("w") or "").strip()):
            out.append(round(float(w["t0"]) - 0.02, 3))
    return out


def _visible(start, end, busy):
    out, t = [], start
    for a, b in sorted(busy):
        if b <= t or a >= end:
            continue
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if end > t:
        out.append((t, end))
    return [(a, b) for a, b in out if b - a > 0.05]


def plan_cut_ins(points, busy, start, end, target=CUT_IN_TARGET_S,
                 lo=CUT_IN_MIN_S, hi=CUT_IN_MAX_S, preferred=()):
    """[(a, b, close)] framing segments over the face-visible stretches of
    [start, end] (busy windows are cutaways or cards): each change lands on
    a phrase point, segments last lo..hi (target ``target``), and wide and
    close alternate, across cutaways too (the face comes back on the other
    camera). ``preferred`` points (phrase turns) win over a plain word
    onset up to 0.8 s farther from the target."""
    pref = {round(p, 3) for p in preferred}
    out, close = [], True
    for s, e in _visible(start, end, busy):
        if e - s < lo:
            continue
        segs, t = [], s
        while e - t > hi:
            cands = [p for p in points
                     if t + lo <= p <= t + hi and e - p >= lo]
            if not cands:
                break
            p = min(cands, key=lambda p: abs(p - (t + target))
                    - (0.8 if round(p, 3) in pref else 0.0))
            segs.append((t, p))
            t = p
        segs.append((t, e))
        for a, b in segs:
            out.append((round(a, 2), round(b, 2), close))
            close = not close
    return out


def face_aim(index, bands=None, vertical_source=True):
    """(cx, cy) where a cut-in is pinned in the OUTPUT frame: the face's
    centre across and its CHIN down (the median face of the spatial
    samples; a landscape source's 9:16 crop is centred on it, so cx 0.5).
    Pinned at the chin, a closer camera grows the face upward (the crown
    leaves, as in a real close-up) and never down into the chest band where
    the title, the slams and the captions sit. None without faces."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    xs, ys = [], []
    for smp in samples:
        faces = [f for f in smp.get("faces") or [] if len(f) >= 4]
        if faces:
            f = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            xs.append((f[0] + f[2]) / 2.0)
            ys.append(f[3])
    if not xs:
        return None
    xs.sort()
    ys.sort()
    cx, cy = xs[len(xs) // 2], ys[len(ys) // 2]
    if bands:
        cy = (cy - bands[0]) / max(1e-6, bands[1] - bands[0])
    if not vertical_source:
        cx = 0.5
    return round(min(max(cx, 0.2), 0.8), 3), round(min(max(cy, 0.3), 0.72), 3)


def cut_ins(ctx, steps, started, run, busy, aim, hook_end, bands=False,
            levels=None, target=None, hi=None):
    """Write the close framings (add_zoom punch, ramp 0: a hard cut to a
    tighter camera and a hard cut back). Returns how many landed."""
    import device_planner
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        prog = float(schemas.program_duration(edl))
        words = device_planner.program_words(edl, ctx.index or {})
    except Exception:
        return 0
    if not words or prog < 6:
        return 0
    turns = phrase_points(words) + [round(b, 3) for _a, b in busy]
    # whisper's words abut (no gap to read a phrase from): any word onset
    # may take a cut-in, a phrase turn is preferred
    onsets = [round(float(w["t0"]) - 0.02, 3) for w in words]
    points = sorted(set(turns + onsets))
    # the first change lands under the hook title (judges: "a first
    # visual change by 1.5-2 s"), on a word onset near 1.5 s; the opening
    # framing before it is the wide one
    near = [p for p in points if 1.1 <= p <= 2.2]
    first = min(near, key=lambda p: abs(p - CUT_IN_FIRST_S)
                - (0.3 if p in turns else 0.0)) if near else hook_end
    segs = plan_cut_ins(points, busy, first, prog, preferred=turns,
                        target=target or CUT_IN_TARGET_S,
                        hi=hi or CUT_IN_MAX_S)
    levels = levels or (CUT_IN_BANDED if bands else CUT_IN_STRENGTH)
    cx, cy = aim or (0.5, 0.4)
    n = 0
    for a, b, close in segs:
        if not close:
            continue
        strength = levels[n % len(levels)]
        res = run(ctx, steps, "add_zoom", {
            "start": a, "end": b, "strength": strength, "mode": "punch",
            "ramp_s": 0.0, "cx": cx, "cy": cy,
            "purpose": "a closer camera on the same take, cut in on a "
                       "phrase turn (references: the framing changes every "
                       "2-3 s)"}, started)
        if res is not None and not str(res).lstrip().upper().startswith(
                ("REJECTED", "FAILED", "CORRECTION")):
            n += 1
    return n


# --------------------------------------------------------------------------
#  Screen recordings: a 2-3x camera on the region being discussed
# --------------------------------------------------------------------------

SCREEN_ZOOM = 2.2       # the window is 1/2.2 of the frame (2-3x, task)
SCREEN_ZOOM_MIN = 1.8   # the floor: a region, never the page (task: 2-3x)
SCREEN_MOVE_S = 0.7     # an eased travel between regions
SCREEN_HOLD_S = 1.2     # at least this long on a region
SCREEN_SAME = 0.6       # a new region overlapping the held one this much: hold
_SCREEN_SYSTEM = (
    "You direct the camera on a screen-recording tutorial. Each numbered "
    "frame comes with the sentence the narrator says over it. For each "
    "frame give the box (pixel coordinates of THAT image, x0 y0 x1 y1) of "
    "the region the sentence is about: the heading, menu item, code block "
    "or paragraph it names or points at, or where the action is (the "
    "cursor, what changed). The box must be SMALL: the named heading with "
    "the 2-4 lines under it, one code block, one button or menu, at most a "
    "third of the image wide and a quarter of it tall, never a whole column "
    "or page. Answer with JSON only: {\"boxes\": "
    "[{\"n\": <frame number>, \"box\": [x0, y0, x1, y1]}]}.")


def _gray_frames(path, fps=4.0, width=192):
    """(times, frames uint8 (n, h, w)) of the whole file, or None."""
    import numpy as np
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip().split(",")
        h = int(round(width * int(probe[1]) / int(probe[0])))
        h += h % 2
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-an", "-vf",
             f"fps={fps},scale={width}:{h},format=gray", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"], capture_output=True, timeout=180).stdout
    except Exception:
        return None
    n = len(raw) // (width * h)
    if n < 4:
        return None
    frames = np.frombuffer(raw[:n * width * h], np.uint8).reshape(n, h, width)
    return [k / fps for k in range(n)], frames


def page_changes(times, frames, share=0.2):
    """Source times where most of the screen changed at once (a new page, a
    jump to an anchor); a scroll inside a column is not one."""
    import numpy as np
    out = []
    for k in range(1, len(frames)):
        d = np.abs(frames[k].astype(np.int16) - frames[k - 1]) > 24
        if d.mean() >= share:
            ys, xs = np.nonzero(d)
            if xs.size and (xs.max() - xs.min()) >= 0.8 * frames.shape[2]:
                if not out or times[k] - out[-1] > 1.0:
                    out.append(times[k])
    return out


def text_column(index):
    """[x0, x1] of the screen's main text column (the wide text lines), or
    None."""
    boxes = [b for smp in ((index or {}).get("spatial") or {}).get("samples") or []
             for b in (smp.get("text") or []) if len(b) >= 4]
    wide = [b for b in boxes if float(b[2]) - float(b[0]) >= 0.15]
    if len(wide) < 6:
        return None
    x0 = sorted(float(b[0]) for b in wide)[int(0.1 * len(wide))]
    x1 = sorted(float(b[2]) for b in wide)[int(0.9 * len(wide)) - 1]
    return [round(x0, 3), round(x1, 3)] if x1 - x0 > 0.2 else None


def column_top(index, src_t, column):
    """A box at the top of the text column at source second ``src_t``: the
    first text lines there (after a page change the section the narrator
    names is usually at the top)."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    best = min(samples, key=lambda s: abs(float(s.get("t") or 0) - src_t),
               default=None)
    x0, x1 = column or (0.1, 0.9)
    lines = sorted((b for b in (best or {}).get("text") or []
                    if len(b) >= 4 and float(b[0]) >= x0 - 0.03
                    and float(b[2]) <= x1 + 0.05),
                   key=lambda b: float(b[1]))
    y0 = float(lines[0][1]) if lines else 0.1
    return [x0, max(0.0, y0 - 0.03), x0 + 0.42, min(1.0, y0 + 0.24)]


def window_for(box, aspect, zoom=SCREEN_ZOOM):
    """The zoom window ([x0, y0, x1, y1], fractions; the frame's own aspect)
    that frames ``box`` at ``zoom`` (smaller when the box is bigger, down to
    SCREEN_ZOOM_MIN), and its zoom."""
    bx0, by0, bx1, by1 = box
    bw, bh = max(0.02, bx1 - bx0), max(0.02, by1 - by0)
    z = max(SCREEN_ZOOM_MIN, min(zoom, 1.0 / (bh * 1.1)))
    w = h = 1.0 / z
    cx = (bx0 + bx1) / 2.0
    cy = (by0 + by1) / 2.0
    if bw > 0.8 * w:
        # a line wider than the window: text reads from the left, so the
        # window opens on the line's start (the heading, the code's indent)
        cx = bx0 - 0.04 * w + w / 2.0
    x0 = min(max(cx - w / 2.0, 0.0), 1.0 - w)
    y0 = min(max(cy - h / 2.0, 0.0), 1.0 - h)
    return [round(x0, 4), round(y0, 4), round(x0 + w, 4), round(y0 + h, 4)], z


def _overlap(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return (ix * iy) / area if area > 0 else 0.0


def screen_keyframes(targets, prog, hook_end):
    """add_zoom_path keyframes from [(t, window, zoom)] (program seconds):
    wide under the hook, a push onto the first region as it leaves, an eased
    travel to each new region (held SCREEN_HOLD_S at least; a region that
    mostly overlaps the held one is no move), held to the end."""
    targets = sorted(targets)
    if not targets or prog < 4:
        return []
    held = [targets[0]]
    for t, win, z in targets[1:]:
        if t - held[-1][0] < SCREEN_HOLD_S + SCREEN_MOVE_S:
            continue
        if _overlap(win, held[-1][1]) >= SCREEN_SAME:
            continue
        held.append((t, win, z))
    first = held[0]
    push_at = min(1.0, prog - 1.0)
    keys = [{"t": 0.0, "rect": first[1], "strength": 0.0},
            {"t": round(push_at, 2), "rect": first[1], "strength": 0.0},
            {"t": round(min(push_at + 0.9, prog), 2), "rect": first[1],
             "strength": round(first[2] - 1.0, 3)}]
    for (t, win, z), prev in zip(held[1:], held):
        a = max(keys[-1]["t"] + SCREEN_HOLD_S, t - SCREEN_MOVE_S / 2.0)
        b = a + SCREEN_MOVE_S
        if b >= prog - 0.3:
            break
        keys.append({"t": round(a, 2), "rect": prev[1],
                     "strength": round(prev[2] - 1.0, 3)})
        keys.append({"t": round(b, 2), "rect": win,
                     "strength": round(z - 1.0, 3)})
    last = keys[-1]
    if prog - last["t"] > 0.05:
        keys.append(dict(last, t=round(prog, 2)))
    return keys


def _vision_boxes(ctx, frames_at, spend):
    """{n: [x0, y0, x1, y1] fractions} from one trial-model look at the
    numbered frames with their sentences; {} without the lane."""
    import claude_lane
    import llm
    model = getattr(ctx, "first_cut_model", None)
    if not model or not claude_lane.available():
        return {}
    try:
        path = ctx.proxy_path()
    except Exception:
        return {}
    parts, sizes = [], {}
    for n, (src_t, sentence) in enumerate(frames_at, 1):
        try:
            jpg = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{src_t:.2f}",
                 "-i", path, "-frames:v", "1", "-vf", "scale=768:-2", "-f",
                 "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            jpg = b""
        if not jpg:
            continue
        sizes[n] = _jpeg_size(jpg) or (768, 480)
        parts.append({"type": "text", "text":
                      f"Frame {n} ({sizes[n][0]}x{sizes[n][1]} px). The "
                      f"narrator: \"{str(sentence)[:220]}\""})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if len(sizes) < 2:
        return {}
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": _SCREEN_SYSTEM},
                    {"role": "user", "content": parts}],
            max_tokens=3000, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("first_cut_screen_scout", {"model": model,
                                               "frames": len(sizes)},
                   {"content": text}, resp.usage)
        print(f"[first_cut] screen scout: {text.strip()[:300]}", flush=True)
        match = re.search(r"\{.*\}", text, re.S)
        pick = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 — the column is the fallback
        print(f"[first_cut] screen scout failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return {}
    out = {}
    for item in pick.get("boxes") or []:
        try:
            n = int(item.get("n"))
            x0, y0, x1, y1 = [float(v) for v in item.get("box")]
        except (TypeError, ValueError, AttributeError):
            continue
        if n not in sizes:
            continue
        w, h = sizes[n]
        box = [x0 / w, y0 / h, x1 / w, y1 / h]
        if not (0 <= box[0] < box[2] <= 1.02 and 0 <= box[1] < box[3] <= 1.02):
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.6:
            continue        # "the whole page" is no direction
        if box[3] - box[1] > 0.3:
            # a whole column: its top, where a section's heading sits
            box = [box[0], box[1], box[2], box[1] + 0.24]
        out[n] = [min(max(v, 0.0), 1.0) for v in box]
    return out


def _jpeg_size(data):
    """(w, h) of a baseline/progressive JPEG, or None."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        size = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + size
    return None


def screen_follow(ctx, steps, started, run, spend, hook_end):
    """A travelling 2-3x camera on a screen recording kept in its own shape:
    one region per sentence (the trial model points at it; the text
    column's top after each page change without the lane), eased between.
    Returns the add_zoom_path result or None."""
    import schemas
    from timeline import Timeline
    try:
        edl = ctx.latest_edl()["json"]
        keep = edl.get("keep") or []
        tl = Timeline(keep, [], None)
        prog = float(schemas.program_duration(edl))
        path = ctx.proxy_path()
    except Exception:
        return None
    video = (ctx.index or {}).get("video") or {}
    try:
        aspect = float(video["width"]) / float(video["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        aspect = 16 / 9.0
    got = _gray_frames(path)
    changes = page_changes(*got) if got else []
    rows = []
    for s in (ctx.index or {}).get("sentences") or []:
        try:
            a, b = tl.src_to_out(float(s["t0"])), tl.src_to_out(float(s["t1"]) - 0.05)
        except (KeyError, TypeError, ValueError):
            continue
        if a is None or b is None or b - a < 0.6:
            continue
        # the frame after a page change inside the sentence is what it is
        # about; else its middle
        src_a, src_b = float(s["t0"]), float(s["t1"])
        inside = [c for c in changes if src_a - 0.2 <= c <= src_b - 0.3]
        src_t = (inside[-1] + 0.4) if inside else (src_a + src_b) / 2.0
        t_out = a
        if inside:
            at = tl.src_to_out(src_t)
            if at is not None:
                t_out = max(a, min(at, b))
        text = str(s.get("text") or "")
        if b - a >= 4.5 and not inside:
            # a long sentence gets two looks: its first and second half
            mid = (src_a + src_b) / 2.0
            words = text.split()
            rows.append((a, (src_a + mid) / 2.0,
                         " ".join(words[:len(words) // 2])))
            m_out = tl.src_to_out(mid)
            if m_out is not None:
                rows.append((m_out, (mid + src_b) / 2.0,
                             " ".join(words[len(words) // 2:])))
            continue
        rows.append((t_out, src_t, text))
    for c in changes:
        out_c = tl.src_to_out(c + 0.3)
        if out_c is not None and not any(abs(out_c - r[0]) < 1.0 for r in rows):
            rows.append((out_c, c + 0.4, ""))
    rows.sort()
    if not rows:
        return None
    rows = rows[:14]
    boxes = _vision_boxes(ctx, [(r[1], r[2]) for r in rows], spend)
    column = text_column(ctx.index)
    targets = []
    for n, (t, src_t, _text) in enumerate(rows, 1):
        box = boxes.get(n) or column_top(ctx.index, src_t, column)
        win, z = window_for(box, aspect)
        targets.append((round(t, 2), win, z))
    keys = screen_keyframes(targets, prog, hook_end)
    if len(keys) < 3:
        return None
    return run(ctx, steps, "add_zoom_path", {
        "keyframes": keys, "ease": "cubic_in_out",
        "purpose": "the camera travels to the region each sentence is about "
                   "(2-3x, eased) so the screen text reads"}, started)


# --------------------------------------------------------------------------
#  Landscape explainers: a 9:16 crop that follows the action
# --------------------------------------------------------------------------

ACTION_FPS = 4.0


def _rgb_frames(path, a, b, fps=ACTION_FPS, width=160):
    import numpy as np
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip().split(",")
        h = int(round(width * int(probe[1]) / int(probe[0])))
        h += h % 2
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{a:.3f}", "-t",
             f"{max(0.3, b - a):.3f}", "-i", path, "-an", "-vf",
             f"fps={fps},scale={width}:{h}", "-f", "rawvideo", "-pix_fmt",
             "rgb24", "-"], capture_output=True, timeout=120).stdout
    except Exception:
        return None
    n = len(raw) // (width * h * 3)
    if n < 1:
        return None
    return np.frombuffer(raw[:n * width * h * 3], np.uint8).reshape(n, h, width, 3)


def action_boxes(frames, ww=0.316):
    """Per frame [x0, x1] (fractions) of where the action is: the window of
    width ``ww`` holding the most colour saturation, motion and detail
    against the frame's own background (u4899: an orange pipe and a red
    cleaner on white snow), narrowed to its middle 70% so a follow keeps it
    inside with room; None for a flat frame."""
    import numpy as np
    f = frames.astype(np.float32) / 255.0
    mx, mn = f.max(axis=3), f.min(axis=3)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    luma = f.mean(axis=3)
    gx = np.abs(np.diff(luma, axis=2, prepend=luma[:, :, :1]))
    gy = np.abs(np.diff(luma, axis=1, prepend=luma[:, :1, :]))
    grad = gx + gy
    mot = np.zeros_like(luma)
    mot[1:] = np.abs(luma[1:] - luma[:-1])
    width = f.shape[2]
    win = max(2, int(round(ww * width)))
    out = []
    for k in range(len(f)):
        parts = []
        for m, wgt in ((sat[k], 0.55), (mot[k], 0.30), (grad[k], 0.15)):
            m = m - np.median(m)
            m[m < 0] = 0
            top = float(np.percentile(m, 99.5)) or 1.0
            parts.append(wgt * np.clip(m / top, 0, 1))
        prof = (sum(parts) ** 2).sum(axis=0)
        tot = float(prof.sum())
        if tot <= 1e-6:
            out.append(None)
            continue
        run = np.convolve(prof, np.ones(win), mode="valid")
        # a subject narrower than the window fits many windows equally:
        # the middle of those centres it
        near = np.nonzero(run >= 0.98 * float(run.max()))[0]
        best = int(near[len(near) // 2])
        c = (best + win / 2.0) / width
        half = 0.35 * win / width
        out.append([c - half, c + half])
    return out


def action_follow(ctx, steps, started, run):
    """Crop a landscape explainer to 9:16 and make the crop follow the
    action inside each shot (follow.plan's restraint: still while the
    action stays in the window, an eased glide when it leaves). Returns
    True when the crop was written."""
    import follow
    import schemas
    res = run(ctx, steps, "auto_reframe", {"ratio": "9:16", "mode": "crop",
                                           "follow": False}, started)
    if res is None or str(res).lstrip().upper().startswith(
            ("REJECTED", "FAILED", "CORRECTION")):
        return False
    try:
        edl = dict(ctx.latest_edl()["json"])
        path = ctx.proxy_path()
    except Exception:
        return True
    video = (ctx.index or {}).get("video") or {}
    try:
        ww = (9 / 16.0) / (float(video["width"]) / float(video["height"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return True
    if not 0.1 < ww < 0.95:
        return True
    keep = [tuple(map(float, k)) for k in edl.get("keep") or []]
    shots = [(float(s["start"]), float(s["end"]))
             for s in (ctx.index or {}).get("shots") or []
             if s.get("start") is not None and s.get("end") is not None]
    if not shots:
        shots = [(keep[0][0], keep[-1][1])] if keep else []
    spans = []
    for s0, s1 in shots:
        kept = [(max(s0, a), min(s1, b)) for a, b in keep
                if min(s1, b) - max(s0, a) > 0.05]
        if not kept:
            continue
        a, b = kept[0][0], kept[-1][1]
        frames = _rgb_frames(path, a, b)
        if frames is None:
            continue
        boxes = action_boxes(frames, ww)
        samples = []
        for k, bx in enumerate(boxes):
            if bx is None:
                continue
            t = a + k / ACTION_FPS
            # follow.plan keeps a head box (the face box widened by 15%
            # each side): hand it the action box narrowed to match
            cx, half = (bx[0] + bx[1]) / 2.0, (bx[1] - bx[0]) / 2.0 / 1.3
            samples.append((t, [cx - half, 0.45, cx + half, 0.55], 0.0))
        if len(samples) < 2:
            continue
        keys, info = follow.plan(samples, a, b, ww, 1.0, kept=kept,
                                 dead_zone=(0.08, 0.10), headroom=0.0,
                                 lead=0.0)
        if not keys:
            x = (info.get("static") or (None,))[0]
            if x is None:
                xs = sorted(s[1][0] / 2.0 + s[1][2] / 2.0 for s in samples)
                x = xs[len(xs) // 2]
            x = min(max(x, ww / 2.0), 1.0 - ww / 2.0)
            keys = [[round((a + b) / 2.0, 3), round(x, 4), 0.5]]
        spans.append({"t0": round(a, 3), "t1": round(b, 3),
                      "k": [[k[0], k[1], 0.5] for k in keys]})
    if not spans:
        return True
    frame = dict(edl.get("frame") or {})
    frame["follow"] = spans[:schemas.FOLLOW_MAX_SPANS] \
        if hasattr(schemas, "FOLLOW_MAX_SPANS") else spans
    edl["frame"] = frame
    try:
        out = ctx.write_edl(edl, "the 9:16 crop follows the action in each "
                                 "shot")
        steps.append({"tool": "action_follow", "ok": str(out).startswith(
            ("EDL v", "NO CHANGE")), "note": str(out)[:160]})
    except Exception as exc:  # noqa: BLE001 — the static crop stands
        steps.append({"tool": "action_follow", "ok": False,
                      "note": f"{type(exc).__name__}: {str(exc)[:140]}"})
    return True
