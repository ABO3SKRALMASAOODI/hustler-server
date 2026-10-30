"""Cut the boring parts of a LONG talk (QB, 30 Oct 2026; trial lane only).

"Cut the boring parts" on a 10-minute 16:9 talk was answered with the First
cut's one 36 s vertical moment: the judge's hard fault, twice, "not what the
user asked: a 10-minute clean-up turned into a 36 s vertical highlight that
drops 94% of the source and changes the shape". QI's first fix (the whole take
through the short-form chain) measured worse: an 8.5-minute vertical program
that ended mid-sentence, with the interviewer shown small in blurred bands.

This is the long-form recipe the owner's references do not cover but a user
who types that sentence means:

  * keep the talk's own shape (a 16:9 talk stays 16:9: no vertical crop, so
    no speaker in bands), in its own order;
  * one scout call reads the whole transcript and names the stretches worth
    keeping, about half of it (the claims, stories, examples and turns of the
    argument; never greetings, housekeeping, repeated points, tangents);
  * the program opens on a complete claim and ends on a sentence that lands;
  * the usual craft on top: pauses and fillers cut, loudness, the opening
    line as a title, captions, a closer camera now and then (sparse: a long
    program is not a short).

Without the lane (or when the scout answers badly) the whole take is kept
trimmed to its first and last whole sentences, never a moment of it.
"""
import json
import re
import time

import config
import llm

MIN_SOURCE_S = 150.0        # a shorter source is the short-form chain's
KEEP_MIN, KEEP_MAX = 0.30, 0.80     # share of the talk a scout pick may keep
MIN_RUN_S = 8.0             # a kept stretch shorter than this is dropped
MERGE_GAP_S = 1.2           # stretches this close are one
CUT_IN_TARGET_S = 7.0       # a long program changes camera about this often
CUT_IN_MAX_S = 10.0

SYSTEM = (
    "You tighten a long talk by cutting its boring parts. Keep what a viewer "
    "would choose to watch: the claims, stories, examples, answers and turns "
    "of the argument. Cut greetings and housekeeping, repeated points, "
    "tangents, setup that goes nowhere, chit-chat, false starts and the "
    "speaker thinking aloud. Answer with the stretches to KEEP, in order: "
    "each a run of whole sentences (copy the ids exactly as written) at "
    "least 10 seconds long; together about {share} of the talk, never all "
    "of it. The first kept sentence is a complete claim or question a new "
    "viewer can follow (never one starting with \"and\", \"so\" or a "
    "filler). The LAST kept sentence lands the point (a conclusion or "
    "payoff, never a list item, a setup or a fragment). The hook is the "
    "opening title, 2 to 5 words faithful to what is said, with exactly "
    "ONE word wrapped in *stars*. Answer with JSON only: {{\"keep\": "
    "[[\"<first id>\", \"<last id>\"], ...], \"hook\": \"<2-5 words>\"}}.")


CUT_IN_LEVELS = (0.26, 0.34)    # 1.3-1.35x: a medium shot, never a crop-in


def face_centre(index):
    """(cx, cy) a closer camera is pinned at on a LANDSCAPE frame: the
    face's own centre, where the picture keeps it (the short-form aim pins
    the chin, for a centred 9:16 crop, and pushed this talk's head against
    the top edge: the judge's picture check, three times); None without
    faces."""
    xs, ys = [], []
    for smp in ((index or {}).get("spatial") or {}).get("samples") or []:
        faces = [f for f in smp.get("faces") or [] if len(f) >= 4]
        if faces:
            f = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            xs.append((f[0] + f[2]) / 2.0)
            ys.append((f[1] + f[3]) / 2.0)
    if not xs:
        return None
    xs.sort()
    ys.sort()
    cx, cy = xs[len(xs) // 2], ys[len(ys) // 2]
    return round(min(max(cx, 0.25), 0.75), 3), round(min(max(cy, 0.3), 0.6), 3)


def eligible(ctx):
    """A long source with speech (the only sources this recipe is for)."""
    import first_cut
    return (float(ctx.duration or 0) >= MIN_SOURCE_S
            and bool(first_cut._speech(ctx.index)))


def share_for(duration_s):
    """How much of the talk to keep: more of a short one."""
    return 0.6 if duration_s < 300 else 0.5 if duration_s < 900 else 0.4


def rows_of(ctx):
    limit = min(float(ctx.duration or 0), config.FIRST_CUT_MAX_SOURCE_S)
    return [r for r in (ctx.index or {}).get("sentences") or []
            if r.get("t0") is not None and r.get("t1") is not None
            and float(r["t0"]) < limit]


def parse_runs(text, rows):
    """[(first_row_index, last_row_index)] from the scout's JSON answer, in
    transcript order, ids as the model may write them ("s12", 12, "12");
    ([], hook) when nothing usable."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        pick = json.loads(match.group(0)) if match else {}
    except ValueError:
        return [], None
    index = {}
    for k, r in enumerate(rows):
        index[str(r["id"])] = k
        digits = re.sub(r"\D", "", str(r["id"]))
        if digits:
            index.setdefault(digits, k)
    runs = []
    for pair in pick.get("keep") or []:
        try:
            a, b = index[str(pair[0])], index[str(pair[1])]
        except (KeyError, IndexError, TypeError):
            continue
        if b < a:
            a, b = b, a
        runs.append((a, b))
    runs.sort()
    merged = []
    for a, b in runs:
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged, pick.get("hook")


def _ends_thought(row):
    text = str(row.get("text") or "").strip()
    return text[-1:] in ".!?" and len(text.split()) >= 4


_FILLER_OPEN = re.compile(
    r"^\W*(?:you know|i mean|well|so|and|but|yeah|yes|okay|ok|like|right|um|uh"
    r"|anyway|now)\b[\s,.]*", re.I)
END_GUARD_S = 1.2           # a sentence this close to the file's end was cut


def _opens_clean(rows, a, b):
    """The first row of the run that does not open on filler ("you know,
    ..."): a leading discourse marker is cut with its row while the run keeps
    at least MIN_RUN_S."""
    while a < b and _FILLER_OPEN.match(str(rows[a].get("text") or "")) \
            and len(str(rows[a].get("text") or "").split()) <= 14 \
            and float(rows[b]["t1"]) - float(rows[a + 1]["t0"]) >= MIN_RUN_S:
        a += 1
    return a


def shape_runs(rows, runs, duration_s):
    """Runs as [[t0, t1]] source seconds: each opens where the speaker's
    sentence begins, the last ends on a sentence that ends; a stretch that is
    too short goes; stretches a breath apart join."""
    import first_cut
    segs = []
    for a, b in runs:
        first = first_cut.true_sentence_start(rows, rows[a])
        a = _opens_clean(rows, rows.index(first), b)
        segs.append([float(rows[a]["t0"]), float(rows[b]["t1"]), b])
    segs = [s for s in segs if s[1] - s[0] >= MIN_RUN_S]
    if not segs:
        return []
    # the last stretch ends on a sentence that lands: run on to the end of
    # the thought, or walk back to the last one that ends
    t0, _t1, b = segs[-1]

    def lands(m):
        # a sentence that ends AND is not the source's own cut-off (a file
        # that stops mid-sentence still gets a full stop from the
        # transcript: "...and for society to, to benefit." at 10:00)
        return _ends_thought(rows[m]) and has_breath(rows, m) and \
            float(rows[m]["t1"]) < float(duration_s) - END_GUARD_S
    k = b
    while k + 1 < len(rows) and not lands(k) \
            and float(rows[k + 1]["t1"]) - float(rows[b]["t1"]) <= 20.0:
        k += 1
    if not lands(k):
        back = [m for m in range(b, -1, -1)
                if lands(m) and float(rows[m]["t1"]) - t0 >= 6.0]
        k = back[0] if back else b
    segs[-1] = [t0, float(rows[k]["t1"]), k]
    out = []
    for s in segs:
        if out and s[0] - out[-1][1] <= MERGE_GAP_S:
            out[-1][1] = max(out[-1][1], s[1])
        else:
            out.append([s[0], s[1]])
    return [[round(a, 2), round(b, 2)] for a, b in out]


END_SYSTEM = (
    "You pick the last line of a cut talk. Below are candidate last "
    "sentences, each with the sentence before it. Pick the id of the one that "
    "makes the strongest ending: a complete thought that lands a conclusion, "
    "a result or the lesson. Never a joke or an example that was just "
    "opened, a list item, a setup, a question, or a fragment. Answer with "
    "JSON only: {\"end_id\": \"<id>\"}.")


BREATH_S = 0.3              # the speaker pauses this long after a closing line


def has_breath(rows, m, min_gap=BREATH_S):
    """Whether the speaker pauses after row ``m`` (the last row of the file
    always does). A cut on a sentence that runs straight into the next one
    leaves the mouth open on the last frame and no live tail: the judge's
    "ends mid-thought, tail silence 0.0 s" on a talk whose last line WAS a
    full sentence (QB, 30 Oct)."""
    if m + 1 >= len(rows):
        return True
    return float(rows[m + 1]["t0"]) - float(rows[m]["t1"]) >= min_gap


def end_candidates(rows, k, floor_t, most=4):
    """Row indexes (oldest first) that could close the program: the chosen
    last row and up to ``most``-1 earlier rows that end a thought AND are
    followed by a breath, none before ``floor_t`` (source seconds)."""
    out = [k]
    m = k - 1
    while m >= 0 and len(out) < most and float(rows[m]["t0"]) >= floor_t:
        if _ends_thought(rows[m]) and has_breath(rows, m):
            out.append(m)
        m -= 1
    return sorted(out)


def pick_ending(ctx, rows, segs, spend):
    """Ask the trial model which of the last few sentence ends closes the
    program best (QB, 30 Oct: the scout's own last line was "do the first
    thing, do the second thing, question mark, question mark..." in 3 of 4
    judged talk10 runs: a joke opened and never landed). The last stretch
    then ends there. ``segs`` unchanged without the lane or a usable
    answer."""
    import claude_lane
    model = getattr(ctx, "first_cut_model", None)
    if not model or not claude_lane.available() or not segs:
        return segs
    last = segs[-1]
    ks = [m for m, r in enumerate(rows)
          if abs(float(r["t1"]) - last[1]) < 0.05]
    if not ks:
        return segs
    cands = end_candidates(rows, ks[0], last[0] + 6.0)
    if len(cands) < 2:
        return segs
    lines = []
    for m in cands:
        before = str(rows[m - 1]["text"]) if m > 0 else ""
        lines.append(f"[{rows[m]['id']}] (before it: \"{before[:160]}\") "
                     f"{rows[m]['text']}")
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": END_SYSTEM},
                    {"role": "user", "content": "\n".join(lines)}],
            max_tokens=600, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("longform_end", {"model": model, "candidates": len(cands)},
                   {"content": text}, resp.usage)
        print(f"[longform] ending pick: {text.strip()[:120]}", flush=True)
        match = re.search(r"\{.*\}", text, re.S)
        want = str((json.loads(match.group(0)) if match else {}).get("end_id"))
    except Exception as exc:  # noqa: BLE001 - the scout's own end stands
        print(f"[longform] ending pick failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return segs
    for m in cands:
        ids = {str(rows[m]["id"]), re.sub(r"\D", "", str(rows[m]["id"]))}
        if want in ids and has_breath(rows, m):
            out = [list(x) for x in segs]
            out[-1][1] = round(float(rows[m]["t1"]), 2)
            return out
    return segs


def kept_share(segs, duration_s):
    return sum(b - a for a, b in segs) / max(1.0, float(duration_s))


def whole_take(rows, duration_s):
    """The fallback: the first to the last whole sentence of the talk."""
    import first_cut
    if not rows:
        return []
    first, last = first_cut._trim_pick(rows, duration_s)
    last = first_cut._complete_end(rows, first, last)
    return [[round(float(first["t0"]), 2), round(float(last["t1"]), 2)]]


def scout(ctx, rows, spend):
    """([[t0, t1]] source seconds, hook, how): one call over the whole
    transcript; the whole take trimmed without the lane or on a bad answer."""
    import claude_lane
    duration = float(ctx.duration or 0)
    model = getattr(ctx, "first_cut_model", None)
    if model and claude_lane.available() and rows:
        lines, budget = [], 120000
        for r in rows:
            line = f"[{r['id']} {float(r['t0']):.1f}-{float(r['t1']):.1f}] {r['text']}"
            budget -= len(line)
            if budget < 0:
                break
            lines.append(line)
        try:
            share = share_for(duration)
            messages = [
                {"role": "system", "content": SYSTEM.format(
                    share=f"{int(share * 100)}%")},
                {"role": "user", "content":
                 "Transcript (sentence id, source seconds):\n" + "\n".join(lines)}]
            resp = claude_lane.create(model, messages, max_tokens=4000,
                                      effort="low")
            text = resp.choices[0].message.content or ""
            spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
            llm.record("longform_scout", {"model": model, "sentences": len(lines)},
                       {"content": text}, resp.usage)
            print(f"[longform] scout pick: {text.strip()[:300]}", flush=True)
            runs, hook = parse_runs(text, rows)
            segs = shape_runs(rows, runs, duration)
            segs = pick_ending(ctx, rows, segs, spend)
            share_kept = kept_share(segs, duration)
            if segs and KEEP_MIN <= share_kept <= KEEP_MAX:
                return segs, hook, "scout"
            print(f"[longform] scout pick unusable (kept {share_kept:.0%}); "
                  "keeping the whole take", flush=True)
        except Exception as exc:  # noqa: BLE001 - the whole take is the fallback
            print(f"[longform] scout failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", flush=True)
    return whole_take(rows, duration), None, "whole"


def tighten(ctx, worker_db, steps):
    """The long-form chain on the whole source. Returns a plain-words
    summary of what landed, or None when nothing could be built (the turn
    then runs as before)."""
    import first_cut
    import first_cut_visual as fcv
    started = time.monotonic()
    rows = rows_of(ctx)
    if not rows:
        return None
    trial = getattr(ctx, "claude_trial", None)
    if trial is not None and not getattr(ctx, "first_cut_model", None):
        ctx.first_cut_model = trial.model
    spend = {"usd": 0.0}
    segs, hook, how = scout(ctx, rows, spend)
    if not segs:
        return None
    # the cut is the deliverable: this turn's renders are the 720p approval
    # render the user watches (agent_tools.render_preview)
    ctx.approval_previews = True
    if not steps.run("reset_edit", {}):
        return None
    if not steps.run("keep_segments", {"segments": segs, "snap_to_words": True}):
        return None
    args = {"min_silence_s": first_cut.PAUSE_CUT_S,
            "padding_s": first_cut.PAUSE_PAD_S}
    if first_cut._talk_density(ctx) >= 1.2:
        args["allow_nonquiet"] = True
    steps.run("cut_silences", args)
    steps.run("remove_filler_words", {})
    # a word said twice in a row ("for society to, to benefit"): cut once
    first_cut.drop_stutters(ctx, _Run(steps), started)
    first_cut.shape_ends(ctx, _Run(steps), started)
    steps.run("set_master_loudness", {"enabled": True})
    keep = first_cut._keep(ctx) or []
    # the opening line as the title (the spoken words, never invented)
    hook_end = 0.0
    opening = first_cut.spoken_opening(ctx)
    if opening:
        text, end = opening
        hook_end = round(min(max(end + 0.15, first_cut.HOOK_MIN_S),
                             first_cut.HOOK_MAX_S), 2)
        if steps.run("add_motion_graphic", {
                "template": "hook_title", "start": 0.0, "end": hook_end,
                "params": first_cut.hook_params(text, None, False),
                "mute_captions": True,
                "purpose": "the opening line as a title"}):
            steps.applied.append("the opening line as a title")
    if len(keep) > 1:
        steps.run("conceal_jump_cuts", {})
    faces = first_cut._faces(ctx.index)
    if faces and faces["share"] >= 0.5:
        n = fcv.cut_ins(ctx, steps.rows, started, _runner(steps), [],
                        face_centre(ctx.index), max(hook_end, 1.0),
                        levels=CUT_IN_LEVELS, target=CUT_IN_TARGET_S,
                        hi=CUT_IN_MAX_S)
        if n:
            steps.applied.append("a closer camera now and then")
    style, words_per = fcv.caption_style(
        {"preset": "reels", "motion_look": "editorial",
         "size_scale": first_cut.CAPTION_SCALE}, talking=True, screen=False,
        vertical=False)
    if steps.run("add_captions", {"mode": "from_transcript", "style": style,
                                  "max_words_per_caption": words_per}):
        steps.applied.append("captions")
    prog = sum(b - a for a, b in (first_cut._keep(ctx) or keep))
    total = float(ctx.duration or 0)
    kept = f"{int(round(prog / 60.0))} min" if prog >= 90 else f"{int(round(prog))} s"
    was = f"{int(round(total / 60.0))}-minute"
    steps.applied.insert(0, (
        f"the {was} talk tightened to {kept} in its own 16:9 shape: "
        + ("the boring stretches cut" if how == "scout"
           else "its ends trimmed to whole sentences")
        + ", pauses and fillers cut"))
    steps.facts = {"program": kept, "was": was, "landscape": True}
    return {"how": how, "segments": len(segs), "program_s": round(prog, 1),
            "usd": round(spend["usd"], 6)}


class _Run:
    """first_cut.shape_ends writes through first_cut._run(ctx, steps,
    started); this lets a trial_turn.Steps stand in for the list."""

    def __init__(self, steps):
        self.steps = steps

    def append(self, row):
        self.steps.rows.append(row)


def _runner(steps):
    """A run(ctx, rows, name, args, started) function for first_cut_visual
    that goes through the trial turn's own Steps."""
    def run(_ctx, _rows, name, args, _started):
        return steps.run(name, args)
    return run
