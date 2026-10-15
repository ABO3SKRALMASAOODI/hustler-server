"""The invention-first story planner (Wave 2, track 2F).

``suggest`` proposes at most MAX candidates for THIS story: each binds a
device to a story beat (hook, build, turn, payoff, callback, loop), says
why, says what is NEW about it for this story (``invention``), names a
reference exemplar, its cost, and carries the exact apply_edit_batch
operation(s) — or, when solving needs the footage (a montage, a loop
rhyme), the verb call that solves it.

Range over house style (owner, 15 Oct): candidates are seeded by the story
itself (its words), so two different shorts never receive the same plan;
what this project already used is down-ranked (creative.variety_ledger);
type voices rotate inside the pack's set; B&W only ever appears as a
device (a stretch that opens to colour on the payoff, one colour kept)
unless the user asked for black and white.

Wave 1 punctuation devices come from device_planner.suggest (the same
cards, budgets, flash safety and restraint); Wave 2 adds type lockups,
callbacks, colour turns, montage, the drop on the payoff and loop rhymes.
"""

import hashlib

MAX = 8
INTENSITIES = ("calm", "designed", "charged")
_N_TYPE = {"calm": 1, "designed": 2, "charged": 3}


def _seed(words, seed=None):
    if seed is not None:
        return int(seed)
    txt = " ".join(str(w.get("w") or "") for w in words[:40])
    return int(hashlib.sha256(txt.encode()).hexdigest()[:8], 16)


def _jit(seed, key):
    h = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return (h[0] / 255.0) * 0.3


def _phrase_at(words, t0, t1, max_words=6):
    """The words said in [t0, t1) as a lockup would print them: detached
    signs joined ("26 %" -> "26%"), the function words at either end
    trimmed ("and scored 26%" -> "scored 26%")."""
    from typeset import roles
    ws = [w for w in words if t0 - 0.05 <= float(w["t0"]) < t1]
    text = roles.clean_text(" ".join(str(w["w"]) for w in ws))
    toks = [{"w": t} for t in text.split()]
    try:
        from tools_ext import montage_tools
        core = montage_tools.slot_text(toks, max_words=max_words)
    except Exception:  # noqa: BLE001
        core = ""
    return (core or " ".join(text.split()[:max_words])).strip(" ,.;:")


def _span_of(words, text, t0, t1):
    """(first word onset, last word end) of ``text`` among the words said
    in [t0 - 0.5, t1 + 0.5], or None."""
    from typeset import roles
    want = [roles.norm(t) for t in str(text or "").split()]
    ws = [w for w in words if t0 - 0.5 <= float(w["t0"]) <= t1 + 0.5]
    got = [w for w in ws if roles.norm(roles.clean_text(w["w"])) in want or
           any(roles.norm(w["w"]) and want_t.startswith(roles.norm(w["w"]))
               for want_t in want)]
    if not got:
        return None
    return float(got[0]["t0"]), float(got[-1].get("t1", got[-1]["t0"]))


# a device the sibling shorts of the batch already used ranks this much
# lower per use (W1 recipes' freshness, on Wave 2's proposals)
BATCH_DECAY = 0.22


def _typeset_op(mid, text, s, e, voice, depth="front"):
    return {"action": "upsert", "layer": "motion", "id": mid,
            "value": {"id": mid, "template": "typeset",
                      "start": round(s, 2), "end": round(e, 2),
                      "params": {"text": text, "voice": voice,
                                 "layout": "auto", "depth": depth,
                                 "verb": "auto"},
                      "layer": "above_captions",
                      "purpose": "typeset lockup (story planner)"}}


def _pick_voice(text, position, ledger, seed, pack, taken, batch=()):
    import creative
    from typeset import voices as V
    allowed = set(pack["voices"]) if pack else set(V.VOICES)
    avoid = tuple(v for v in V.VOICES if v not in allowed or v in taken)
    if len(avoid) >= len(V.VOICES):
        avoid = tuple(taken)
    return creative.pick_voice(text, position=position, ledger=ledger,
                               seed=seed, avoid=avoid, batch=batch)


def suggest(edl, index, *, intensity="designed", seed=None, asked=(),
            pack=None, user_text="", maps=None, base=None, max_n=MAX,
            batch=None):
    """<= max_n candidates [{device, family, beat, at, op | ops | call,
    reason, invention, exemplar, cost_ms, score}] for this story.
    ``batch``: creative.ledger_of the sibling shorts (voices and devices
    the batch already used rank lower, so ten shorts get ten plans)."""
    import callbacks
    import creative
    import device_planner
    import meaning_map
    if intensity not in INTENSITIES:
        raise ValueError(f"intensity is one of {INTENSITIES}")
    edl = edl or {}
    plan = device_planner.moments(edl, index, maps=maps)
    words = plan["words"]
    prog = float(plan["program_s"])
    sd = _seed(words, seed)
    ledger = creative.variety_ledger(edl)
    used_dev = set(ledger.get("devices") or [])
    batch = batch or {}
    batch_voices = list(batch.get("voices") or [])
    batch_dev = list(batch.get("devices") or [])

    def stale(dev):
        """The batch freshness penalty of a device (0 when unused)."""
        return min(0.66, BATCH_DECAY * batch_dev.count(dev))
    out = []
    taken_voices = set(ledger.get("voices") or [])
    ids = {str(m.get("id")) for m in edl.get("motion") or []}

    def new_id(prefix):
        k = 1
        while f"{prefix}{k}" in ids:
            k += 1
        ids.add(f"{prefix}{k}")
        return f"{prefix}{k}"

    sents = meaning_map.plan(words) if words else []
    # ── hook and payoff lockups (2A) ──
    pay = plan.get("payoff") or {}
    beats = []
    if sents:
        h = sents[0]
        beats.append(("hook", h["t0"], min(h["t1"], h["t0"] + 2.6),
                      device_planner_text(words, h)))
    if pay.get("t") is not None:
        pt = float(pay["t"])
        ptext = _phrase_at(words, pt - 0.6, pt + 1.2)
        span = _span_of(words, ptext, pt - 0.6, pt + 1.2)
        ps = max(0.0, (span[0] if span else pt) - 0.15)
        # held through the payoff hold, never a 6 s poster over the next
        # line: at most 1.2 s after its last word
        pe = min(prog, float(pay.get("hold_end") or pt + 1.6),
                 (span[1] if span else pt + 0.4) + 1.2)
        beats.append(("payoff", ps, max(pe, ps + 0.8), ptext))
    for s in sents[1:-1]:
        if "contrast" in s["meanings"] or "number" in s["meanings"]:
            beats.append(("turn", s["t0"], s["t1"],
                          device_planner_text(words, s)))
    n_type = _N_TYPE[intensity]
    for beat, s, e, text in beats:
        if not text or n_type <= 0:
            continue
        voice, why = _pick_voice(text, beat if beat != "turn" else "turn",
                                 ledger, sd, pack, taken_voices,
                                 batch=batch_voices)
        taken_voices.add(voice)
        depth = "behind" if pack and pack["id"] == "C" and beat == \
            "payoff" else "front"
        mid = new_id("ts")
        new_voice = voice not in (ledger.get("voices") or [])
        out.append({"device": "typeset_behind" if depth == "behind" else
                    "typeset_lockup", "family": "layout", "beat": beat,
                    "at": round(s, 2),
                    "op": _typeset_op(mid, text, s, e, voice, depth),
                    "reason": f"the {beat} line set as type: {why}",
                    "invention": (f"'{text}' in {voice}, "
                                  + ("a voice this batch has not used"
                                     if new_voice and voice not in
                                     batch_voices else
                                     "new to this short" if new_voice else
                                     "returning on a different beat")),
                    "exemplar": "09@1.27" if beat != "payoff" else "13@3.44",
                    "cost_ms": 4500,
                    "score": 1.0 + (0.3 if beat in ("hook", "payoff")
                                    else 0.0) + _jit(sd, beat)})
        n_type -= 1
    # ── callbacks (2E -> 2A memory) ──
    for cb in callbacks.find(words)[:1]:
        first = [m for m in edl.get("motion") or []
                 if m.get("template") == "typeset"]
        hit = callbacks.for_typeset([cb], edl)
        t_again = cb["t_again"][0]
        mid = new_id("ts")
        op = _typeset_op(mid, cb["text"], max(0.0, t_again - 0.1),
                         min(prog, t_again + 2.0), "auto")
        if hit:
            op["value"]["params"]["callback_of"] = hit[0][1]
        out.append({"device": "typeset_lockup", "family": "layout",
                    "beat": "callback", "at": round(t_again, 2), "op": op,
                    "reason": f"'{cb['text']}' is said twice; the second "
                              "telling rhymes with the first",
                    "invention": "a rhyme only this speech has",
                    "exemplar": "13@15.92", "cost_ms": 4500,
                    "score": 1.25 + _jit(sd, "callback")})
        break
    # ── colour turn: B&W as a device, never a look ──
    pack_bw = bool(pack and set((pack.get("colour_world") or {}).get(
        "devices") or ()) & {"bw_section_to_colour"})
    # earned, never a default: the story turns INTO its payoff (a contrast
    # or a reversal in the last sentences), or the pack's colour world is
    # built on it; and it is fresh in the batch (two siblings with a grey
    # build are a house style, not a device)
    turn_in = any("contrast" in (x.get("meanings") or [])
                  for x in sents[-2:]) if sents else False
    if pay.get("t") is not None and (pack_bw or (not pack and turn_in)) \
            and (pack_bw or batch_dev.count("grade_section") < 2):
        pt = float(pay["t"])
        a = max(0.0, pt - min(4.0, pt * 0.4))
        if pt - a >= 1.0 and "grade_section" not in used_dev:
            fid = new_id("gw")
            out.append({"device": "grade_section", "family": "finish",
                        "beat": "payoff", "at": round(a, 2),
                        "op": {"action": "upsert", "layer": "fx", "id": fid,
                               "value": {"id": fid, "type": "grade_window",
                                         "stage": "world", "start": round(a, 2),
                                         "end": round(pt, 2),
                                         "saturation": 0.0, "ramp_out_f": 2,
                                         "device": "grade_section"}},
                        "reason": "the build in black and white, colour "
                                  "returns ON the payoff word",
                        "invention": "B&W as a device that resolves into "
                                     "this story's turn",
                        "exemplar": "18@3.64", "cost_ms": 1200,
                        "score": 0.95 + _jit(sd, "bw")
                        - stale("grade_section")})
    # ── music: the user's track only ──
    users = [m for m in edl.get("music") or []
             if str(m.get("provenance") or "") in ("user_upload",
                                                    "user_named",
                                                    "user_picked_library")]
    if users and pay.get("t") is not None:
        out.append({"device": "arrange_drop_hold", "family": "rhythm",
                    "beat": "payoff", "at": round(float(pay["t"]), 2),
                    "call": {"verb": "shape_music",
                             "args": {"payoff": round(float(pay["t"]), 2),
                                      "hold_s": 0.45,
                                      "music_id": users[0].get("id")}},
                    "reason": "the user's drop lands on the payoff word "
                              "after a held breath",
                    "invention": "the track bent to this story's turn",
                    "exemplar": "02@12.15", "cost_ms": 2500,
                    "score": 1.1 + _jit(sd, "drop")})
        if pack and pack["id"] in ("A", "B", "H"):
            out.append({"device": "montage_cut", "family": "rhythm",
                        "beat": "build", "at": 0.0,
                        "call": {"verb": "plan_to_music",
                                 "args": {"style": "arc" if pack["id"] ==
                                          "A" else "broll",
                                          "seed": sd % 997}},
                        "reason": "b-roll cut on the kick one frame early",
                        "invention": "a montage seeded by this story",
                        "exemplar": "22@0-14", "cost_ms": 9000,
                        "score": 0.9 + _jit(sd, "montage")})
    # ── loop rhyme before the end card ──
    out.append({"device": "recall_loop_rhyme", "family": "transition",
                "beat": "loop", "at": round(prog, 2),
                "call": {"verb": "plan_loop", "args": {"write": True}},
                "reason": "the last frame before the end card rhymes with the "
                          "first, so the feed loops",
                "invention": "a loop built from this footage's own opening",
                "exemplar": "08@13.37", "cost_ms": 3000,
                "score": 0.7 + _jit(sd, "loop")
                - stale("recall_loop_rhyme")})
    # ── Wave 1 punctuation (the same cards, budgets and safety) ──
    if base is None:
        try:
            dur = ((index or {}).get("video") or {}).get("duration")
            base = device_planner.suggest(edl, index, intensity=intensity,
                                          asked=asked, maps=maps,
                                          request_text=user_text,
                                          max_n=4, duration=dur,
                                          validate=dur is not None)
        except Exception:  # noqa: BLE001 — Wave 2 candidates stand alone
            base = []
    pal = (pack or {}).get("palette") or {}
    for c in base or []:
        dev = c.get("device")
        if pack and dev not in pal and c.get("family") not in (
                "punctuation", "transition"):
            continue
        mo = (c.get("detail") or {}).get("moment")
        out.append({"device": dev, "family": c.get("family"),
                    "beat": (mo.get("kind", "moment") if isinstance(mo, dict)
                             else str(mo or "moment")),
                    "at": c.get("at"), "op": c.get("op"),
                    "reason": c.get("reason"),
                    "invention": ("new to this project" if dev not in
                                  used_dev else "used before: a different "
                                  "beat"),
                    "exemplar": c.get("exemplar"),
                    "cost_ms": c.get("est_ms"),
                    "score": 0.8 + _jit(sd, dev) - (0.3 if dev in used_dev
                                                     else 0.0)
                    - stale(dev)})
    # pack budgets and the used-device penalty
    for c in out:
        if pack and c["device"] in pal:
            c["score"] += 0.15
        if c["device"] in used_dev:
            c["score"] -= 0.25
    out.sort(key=lambda c: -c["score"])
    seen, final = {}, []
    for c in out:
        cap = pal.get(c["device"], 3) if pack else 3
        if seen.get(c["device"], 0) >= cap:
            continue
        seen[c["device"]] = seen.get(c["device"], 0) + 1
        final.append(c)
        if len(final) >= max(1, min(MAX, int(max_n))):
            break
    for c in final:
        c["score"] = round(c["score"], 3)
    return final


def device_planner_text(words, sent, max_words=6):
    """The words of a sentence that carry it (function words trimmed)."""
    from tools_ext import montage_tools
    ws = words[sent["i0"]:sent["i1"] + 1]
    return montage_tools.slot_text(ws, max_words=max_words) or \
        " ".join(str(w["w"]) for w in ws[:max_words])
