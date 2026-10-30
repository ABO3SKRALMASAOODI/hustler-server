"""Pure read tools: ONE implementation for the executors and the backend.

THE PROBLEM. Every MCP read (get_words, get_transcript, get_edl, ...) was a
queued job run by a Cloudflare container: get_words took 4-8 s at the median
for about two seconds of executor time, almost all of it Postgres round trips
from a container in another region, and an editor makes thousands of reads a
run. Reading a transcript slice needs no ffmpeg, model or browser.

THE RULE. These functions are the editor tools themselves, moved here
verbatim from agent_tools (which imports them back under the same names), so
the executor's ToolContext and the backend's read context run the same code.
They read only the project's index rows, its EDL and asset rows in Postgres.
This module imports nothing heavy at load: the backend loads it standalone
(backend/video_services/mcp_fast_reads.py) and must stay light, so worker
modules are resolved lazily through ``_mod`` and SQL lives here as plain
functions the worker's db module re-exports.

``run_read`` is agent_tools._execute's success path for one of these tools,
and ``mcp_read_result`` / ``activity_row`` are the parts of mcp_exec and
agent_loop._activity a read produces; mcp_exec and agent_loop use them too.
The backend answers from them only when the read succeeds; anything else
(unknown arguments, an error, a missing input) returns None and the call takes
the queued path, which then owns the exact error text.
"""

import difflib
import importlib
import inspect
import json
import os
import re
import sys

# ── Output budgets (config.py imports these; one definition) ───────────────
TOOL_OUTPUT_CHAR_BUDGET = 12000   # ~3000 tokens
# Transcript tools get a far larger budget: silently dropping the tail of a
# long video's transcript is how far-apart repetitions go unseen.
TRANSCRIPT_CHAR_BUDGET = 48000    # ~12000 tokens
# Visual timeline sampling step of the indexer (seconds), printed by
# get_shots. Read from the same environment variable config always read.
VISUAL_SAMPLE_S = float(os.getenv("VISUAL_SAMPLE_S", "4.0"))

# Only these inspected reads may be repeated without first reconciling state
# (tool_retry re-exports this set).
SAFE_READS = frozenset({
    'get_edl', 'get_video_info', 'get_transcript', 'get_words',
    'get_kept_transcript', 'get_shots', 'search_transcript', 'list_assets',
    'wait_for_job',
})

# The reads the backend may answer itself. Each reads Postgres rows only.
FAST_READ_TOOLS = frozenset({
    "get_words", "get_transcript", "get_kept_transcript",
    "search_transcript", "get_shots", "get_edl", "list_motion_templates",
    "list_assets",
})
# Index keys each fast read touches (the backend loads only these; reading
# any other key falls back to the queued path rather than guessing).
FAST_READ_INDEX_KEYS = {
    "get_words": ("video", "words", "speakers"),
    "get_transcript": ("video", "sentences", "speakers"),
    "get_kept_transcript": ("video", "words", "perception"),
    "search_transcript": ("video", "sentences"),
    "get_shots": ("video", "shots", "moments"),
    "get_edl": ("video",),
    "list_motion_templates": ("video",),
    "list_assets": ("video",),
}

# MCP plans whose calls are metered contracts (mcp_exec, job_completion).
METERED_PLANS = frozenset({"mcp_connect", "advanced", "plus_2026",
                           "pro_2026", "max_2026"})


def _mod(name):
    """A worker module by name, also when this file runs in the backend.

    The backend loads worker modules standalone under ``worker_<name>``
    (routes/video.py, video_services/mcp_fast_reads.py); in a worker process
    they are ordinary top-level modules.
    """
    loaded = sys.modules.get("worker_" + name)
    if loaded is not None:
        return loaded
    return importlib.import_module(name)


# ── SQL (worker/db.py re-exports these) ────────────────────────────────────

def latest_asset(conn, project_id, kind):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, kind))
        return cur.fetchone()


def asset_by_key(conn, project_id, storage_key):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND storage_key = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, storage_key))
        return cur.fetchone()


def listener_policy(meta):
    """Whether a render is deterministic-only MCP evidence, judged by the FACT.

    The separate listening model only ever hears the excerpts the renderer
    records in ``listen_keys``/``listen_clips`` (agent_tools.
    _review_render_audio: no excerpts, nothing heard). So:
      'deterministic_only'     stamped audio_model_review=false, no excerpts;
      'no_listening_excerpts'  stamped true or unstamped (the Studio default)
                               with both excerpt lists recorded and EMPTY;
      None                     excerpts exist, or the lists were never
                               recorded (nothing proves no listener heard it).
    The stamp alone used to decide (ticket #45, 30 Oct 2026): every MCP
    changed-section render was stamped true by default and every Studio
    preview was invisible to MCP. backend/routes/mcp._listener_policy is the
    same rule on the download_url side; keep the two identical."""
    meta = meta or {}
    keys, clips = meta.get("listen_keys"), meta.get("listen_clips")
    if meta.get("audio_model_review") is False:
        return None if (keys or clips) else "deterministic_only"
    if isinstance(keys, list) and isinstance(clips, list) \
            and not keys and not clips:
        return "no_listening_excerpts"
    return None


def find_render_asset(conn, project_id, variant, edl_version):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'render'
                         AND meta->>'variant' = %s
                         AND (meta->>'edl_version')::int = %s
                       ORDER BY id DESC LIMIT 1""",
                    (project_id, variant, int(edl_version)))
        return cur.fetchone()


def assets_by_kinds(conn, project_id, kinds, limit=200):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = ANY(%s)
                       ORDER BY id DESC LIMIT %s""",
                    (project_id, list(kinds), limit))
        return cur.fetchall()


def get_index_by_sha(conn, sha256):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM indexes WHERE video_sha256 = %s", (sha256,))
        return cur.fetchone()


def latest_edl(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 1""", (project_id,))
        return cur.fetchone()


def add_message(conn, session_id, role, content, meta=None):
    from psycopg2.extras import Json
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO chat_messages (session_id, role, content, meta)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (session_id, role, content,
                     Json(meta) if meta is not None else None))
        return cur.fetchone()["id"]


def user_billing(conn, user_id):
    """(is_subscribed, plan, trialing) — everything the turn needs about who is
    paying, in one query.

    The plan is here because routing stopped being a boolean in round 49: the
    Frontier tier ('ai_max') runs its agent AND its vision on the frontier
    provider, so "which model answers" now depends on WHICH plan, not merely on
    whether there is one.

    `trialing` is here because "you're out of credits" has a third truth. A
    trialling account is is_subscribed, but its pool is 10% of the plan and the
    rest is released by converting — so telling that user their credits
    "refresh on your plan's cycle" points them at waiting when the thing that
    helps is one click away.

    Degrades rather than raises: a missing trial_status column (they are added
    by hand in the Render shell) yields trialing=False and the old behaviour.
    """
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT is_subscribed, plan, trial_status "
                        "FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        except Exception:
            conn.rollback()     # a missing column poisons the transaction
            cur.execute("SELECT is_subscribed, plan FROM users WHERE id = %s",
                        (user_id,))
            row = cur.fetchone()
    if not row:
        return False, "free", False
    return (bool(row["is_subscribed"]),
            (row.get("plan") or "free"),
            bool(row["is_subscribed"]) and row.get("trial_status") == "trialing")


# ── Release compatibility (skew_guard re-exports these) ────────────────────

def edl_problem(data, strip_unknown_fx=False):
    """Why this executor's schema cannot read ``data``, or None.

    ``strip_unknown_fx`` applies the render path's own Wave 0 degrade rule
    (schemas.strip_unknown_fx: a lane older than the writer drops an fx
    device it does not register and reports it, rather than failing). It is
    used for previews only; a final waits for the release that draws every
    device, and an editor never edits a document it cannot fully read.
    """
    if not isinstance(data, dict):
        return None
    schemas = _mod("schemas")
    if strip_unknown_fx and hasattr(schemas, "strip_unknown_fx"):
        try:
            data = schemas.strip_unknown_fx(data)[0]
        except Exception:
            pass
    try:
        model = schemas.EDL.model_validate(data)
    except Exception as exc:
        first = " ".join(str(exc).split())[:160]
        return f"its EDL uses fields this release does not know: {first}"
    unknown = []
    _unknown_keys(data, model.model_dump(), model.model_dump(by_alias=True),
                  "", unknown)
    if unknown:
        shown = ", ".join(sorted(set(unknown))[:4])
        return f"its EDL uses fields this release does not know: {shown}"
    return None


def _unknown_keys(raw, kept, kept_alias, path, out):
    """Append every key of ``raw`` this schema would silently drop.

    ``kept``/``kept_alias`` are the validated model dumped by field name and
    by alias. A raw dict facing no dumped dict is a free-form value (a
    dict-typed field) and is not inspected further.
    """
    if isinstance(raw, dict):
        if not isinstance(kept, dict) and not isinstance(kept_alias, dict):
            return
        a = kept if isinstance(kept, dict) else {}
        b = kept_alias if isinstance(kept_alias, dict) else {}
        for key, value in raw.items():
            if key not in a and key not in b:
                out.append(f"{path}.{key}" if path else str(key))
            else:
                _unknown_keys(value, a.get(key), b.get(key),
                              f"{path}.{key}" if path else str(key), out)
    elif isinstance(raw, list):
        a = kept if isinstance(kept, list) else []
        b = kept_alias if isinstance(kept_alias, list) else []
        for i, value in enumerate(raw):
            _unknown_keys(value, a[i] if i < len(a) else None,
                          b[i] if i < len(b) else None, f"{path}[]", out)


# ── The tools (moved verbatim from agent_tools) ────────────────────────────

def clamp_seconds(duration, t):
    """ToolContext.clamp: a source time bounded to the main video."""
    try:
        t = float(t)
    except (TypeError, ValueError):
        raise ValueError(f"'{t}' is not a number of seconds")
    # With no main video there is no source clock to clamp against; the
    # placement tools bound program positions against program_duration
    # themselves, so keep a generous upper here rather than collapsing
    # every time to 0.
    upper = duration if duration > 0 else 1e7
    return round(min(max(t, 0.0), upper), 2)



def _cap(text, budget=None):
    budget = budget or TOOL_OUTPUT_CHAR_BUDGET
    if len(text) <= budget:
        return text
    return text[:budget] + "\n...[truncated — narrow your range and call again]"


def _evidence_cache(ctx):
    cache = getattr(ctx, "_read_evidence_seen", None)
    if cache is None:
        cache = set()
        setattr(ctx, "_read_evidence_seen", cache)
    return cache


def _metric(ctx, key, amount=1):
    metrics = getattr(ctx, "editing_metrics", None)
    if metrics is None:
        metrics = {}
        setattr(ctx, "editing_metrics", metrics)
    metrics[key] = metrics.get(key, 0) + amount


def _fmt_t(t):
    return f"{t:.2f}"


# ------------------------------------------------------------------ #
#  READ tools                                                          #
# ------------------------------------------------------------------ #

def program_name_of(ctx):
    """asset_key -> display filename for the scene map (timeline.
    describe_program), cached on the ctx so a turn does at most one DB
    lookup per distinct key. Returns None for unknown keys — the caller
    falls back to the key's basename."""
    cache = getattr(ctx, "_asset_names", None)
    if cache is None:
        cache = ctx._asset_names = {}

    def name_of(key):
        if key not in cache:
            try:
                a = ctx.db.run(asset_by_key, ctx.project_id, key)
                cache[key] = (a.get("meta") or {}).get("filename") \
                    if a else None
            except Exception:
                cache[key] = None
        return cache[key]
    return name_of


def _program_map(ctx, edl_json):
    """The viewer-ordered scene listing for this EDL, or ''."""
    try:
        return _mod("timeline").describe_program(edl_json, program_name_of(ctx))
    except Exception:
        return ""


def get_transcript(ctx, start=0, end=None, asset_key=None):
    if asset_key:
        # Round 84: every uploaded clip/music file is indexed too — read ITS
        # transcript by the asset's sha. Times are CLIP seconds.
        asset = ctx.db.run(
            lambda conn: asset_by_key(conn, ctx.project_id, asset_key))
        if not asset:
            return f"REJECTED: no asset with storage_key {asset_key!r}."
        row = asset.get("sha256") and \
            ctx.db.run(get_index_by_sha, asset["sha256"])
        idx = (row or {}).get("json") or {}
        sents = idx.get("sentences") or []
        if not sents:
            return ("That upload has no transcript (no speech, no audio "
                    "track, or its analysis has not finished yet).")
        multi = (idx.get("speakers") or 0) > 1
        out = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}]"
               + (f" S{s['speaker']}:" if multi
                  and s.get("speaker") is not None else "")
               + f" {s['text']}" for s in sents]
        return _cap(f"Transcript of {asset_key} (CLIP seconds):\n"
                    + "\n".join(out),
                    budget=TRANSCRIPT_CHAR_BUDGET)
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    if end <= start:
        return "REJECTED: end must be greater than start."
    rows = [s for s in ctx.index.get("sentences", [])
            if s["t1"] > start and s["t0"] < end]
    if not rows:
        return (f"No transcribed speech between {start}s and {end}s."
                if ctx.index.get("sentences") else
                "This video has no transcript (no speech or no audio track).")
    # Only label speakers when there is more than one — "S0:" on every line of
    # a solo talking head is noise the model pays for on every read.
    multi = (ctx.index.get("speakers") or 0) > 1
    out = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}]"
           + (f" S{s['speaker']}:" if multi and s.get("speaker") is not None
              else "")
           + f" {s['text']}"
           for s in rows]
    # Transcripts get a much larger budget than other tools: silently losing
    # the tail of a long video is exactly how far-apart repetitions go unseen.
    return (_cap("\n".join(out), budget=TRANSCRIPT_CHAR_BUDGET)
            + "\n(for word-exact timing, call get_words(start, end))")


def _norm_token(w):
    return re.sub(r"[^a-z0-9']+", "", (w or "").lower())


def find_repeated_phrases(out_words, shingle=4):
    """Repeated N-word phrases in the kept program text, as
    [(phrase, [program_times])]. Consecutive repeated shingles merge into
    longer phrases so 'we just built the ultimate ai pipeline' reports once,
    not as four overlapping 4-gram hits."""
    toks = [( _norm_token(w["w"]), w["t0"]) for w in out_words]
    toks = [(t, at) for t, at in toks if t]
    if len(toks) < shingle * 2:
        return []
    counts = {}
    for i in range(len(toks) - shingle + 1):
        key = " ".join(t for t, _ in toks[i:i + shingle])
        counts.setdefault(key, []).append(i)
    rep_idx = sorted({i for idxs in counts.values() if len(idxs) > 1
                      for i in idxs})
    if not rep_idx:
        return []
    runs, s, p = [], rep_idx[0], rep_idx[0]
    for i in rep_idx[1:]:
        if i == p + 1:
            p = i
        else:
            runs.append((s, p))
            s = p = i
    runs.append((s, p))
    phrases = {}
    for a, b in runs:
        text = " ".join(t for t, _ in toks[a:b + shingle])
        phrases.setdefault(text, []).append(round(toks[a][1], 1))
    return [(t, times) for t, times in phrases.items() if len(times) > 1]


def classify_repeated_phrases(out_words, shingle=4):
    """Separate edit duplication from words the speaker actually repeated.

    Timeline.kept_words carries the original source clock. If the same phrase
    appears twice in program time with the same source start, an EDL segment
    was duplicated. Distinct source starts mean the speaker said it twice;
    that may be rhetorical, a stutter, or two takes and is not proof of an
    editing bug.
    """
    rows = [( _norm_token(w.get("w")), w) for w in out_words]
    rows = [(token, word) for token, word in rows if token]
    results = []
    for phrase, _times in find_repeated_phrases(out_words, shingle=shingle):
        needle = phrase.split()
        occ = []
        for i in range(len(rows) - len(needle) + 1):
            if [token for token, _word in rows[i:i + len(needle)]] != needle:
                continue
            word = rows[i][1]
            occ.append({"program_s": round(float(word.get("t0", 0.0)), 3),
                        "source_s": (round(float(word["src_t0"]), 3)
                                     if word.get("src_t0") is not None
                                     else None)})
        source_times = [x["source_s"] for x in occ if x["source_s"] is not None]
        duplicated_source = any(
            abs(a - b) <= 0.08
            for i, a in enumerate(source_times)
            for b in source_times[i + 1:])
        kind = ("edit_duplicate" if duplicated_source else
                "spoken_repetition" if len(source_times) >= 2 else
                "ambiguous")
        results.append({"phrase": phrase, "kind": kind,
                        "occurrences": occ})
    return results


def get_kept_transcript(ctx):
    """The transcript of what the CURRENT edit actually keeps — program
    time — with repeated-phrase detection. THE tool for verifying that a
    repetition/tightening pass really removed the repeats."""
    latest = ctx.latest_edl()
    edl = latest["json"]
    tl = _mod("timeline").Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    # Every word the viewer HEARS, including one whose midpoint a pause cut
    # removed while its sound survived (the captions show the same words).
    Voice = _mod("timeline").Voice
    out_words = tl.kept_words(ctx.index.get("words", []), rescue=True,
                              voice=Voice.from_index(ctx.index))
    if not out_words:
        return ("The current edit keeps no transcribed speech."
                if ctx.index.get("words") else
                "This video has no transcript (no speech or no audio track).")
    lines, group = [], []

    def flush():
        if not group:
            return
        src0 = tl.out_to_src(group[0]["t0"])
        src1 = tl.out_to_src(group[-1]["t1"])
        src = (f" | src {_fmt_t(src0)}-{_fmt_t(src1)}"
               if src0 is not None and src1 is not None else "")
        lines.append(f"[{_fmt_t(group[0]['t0'])}-{_fmt_t(group[-1]['t1'])}"
                     f"{src}] " + " ".join(w["w"] for w in group))
        group.clear()

    for w in out_words:
        if group and (w["t0"] - group[-1]["t1"] > 0.9 or len(group) >= 14):
            flush()
        group.append(w)
    flush()
    header = (f"Program transcript of EDL v{latest['version']} "
              f"({tl.out_duration:.1f}s output — program time, with the "
              "matching source spans):")
    reps = classify_repeated_phrases(out_words)
    if reps:
        duplicate = [r for r in reps if r["kind"] == "edit_duplicate"]
        spoken = [r for r in reps if r["kind"] == "spoken_repetition"]
        parts = []
        if duplicate:
            parts.append("\nEDIT DUPLICATIONS (same source phrase used more "
                         "than once—repair the EDL):\n" + "\n".join(
                f"  '{r['phrase']}' at " + ", ".join(
                    f"{o['program_s']}s" for o in r["occurrences"])
                for r in duplicate[:6]))
        if spoken:
            parts.append("\nSPOKEN REPETITIONS (distinct source moments—not "
                         "proof of an edit bug; preserve rhetorical repeats "
                         "unless the brief asks to tighten them):\n" + "\n".join(
                f"  '{r['phrase']}' at " + ", ".join(
                    f"{o['program_s']}s" for o in r["occurrences"])
                for r in spoken[:6]))
        note = ("\nPOSSIBLE REPETITIONS (classified by source provenance):"
                + "".join(parts))
    else:
        note = "\nNo repeated phrases detected in the output."
    return _cap(header + "\n" + "\n".join(lines) + note,
                budget=TRANSCRIPT_CHAR_BUDGET)


# Adaptive response bound (was a 60s hard range reject): a wide range over
# sparse speech is perfectly answerable, so the WORD count is what's capped —
# the tail says exactly how much was withheld and how to get it.
GET_WORDS_MAX_WORDS = 400


def get_words(ctx, start=0, end=None):
    """Word-level timestamps straight from the index — the ONLY correct
    source for cut points inside a sentence."""
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    if end <= start:
        return "REJECTED: end must be greater than start."
    words = ctx.index.get("words", [])
    rows = [w for w in words if w["t1"] > start and w["t0"] < end]
    if not rows:
        return (f"No transcribed words between {start}s and {end}s."
                if words else
                "This video has no transcript (no speech or no audio track).")
    shown = rows[:GET_WORDS_MAX_WORDS]
    multi = (ctx.index.get("speakers") or 0) > 1
    out = [f"{_fmt_t(w['t0'])}-{_fmt_t(w['t1'])} {w['w']}"
           + (f" [S{w['speaker']}]" if multi and w.get("speaker") is not None
              else "")
           + (" [filler]" if w.get("filler") else "")
           + (f" [confidence={w['confidence']:.3f}" +
              (", REVIEW]" if w['confidence'] < .8 else "]")
              if w.get("confidence") is not None else "")
           for w in shown]
    tail = ""
    if len(rows) > len(shown):
        # Floor the suggested start (int()): rounding UP could skip words
        # whose t1 falls inside the rounded gap; flooring only re-shows a
        # ≤1s overlap the strict t1>start filter tolerates. The end is
        # printed exactly for the same reason.
        tail = (f"\n...{len(rows) - len(shown)} more words (up to {end}s) "
                f"not shown — narrow the range and call again "
                f"(e.g. get_words({int(shown[-1]['t1'])}, {end:g})).")
    unknown = sum(w.get("confidence") is None for w in shown)
    confidence_note = (f"\nConfidence unavailable for {unknown} displayed token(s)."
                       if unknown else "")
    return _cap("\n".join(out) + confidence_note + tail,
                budget=TRANSCRIPT_CHAR_BUDGET)


def search_transcript(ctx, query):
    q = (query or "").strip().lower()
    if not q:
        return "REJECTED: query is empty."
    sentences = ctx.index.get("sentences", [])
    if not sentences:
        return "This video has no transcript to search."
    exact = [s for s in sentences if q in s["text"].lower()]
    fuzzy = []
    if len(exact) < 5:
        texts = {s["id"]: s["text"].lower() for s in sentences}
        close = difflib.get_close_matches(q, list(texts.values()), n=8,
                                          cutoff=0.5)
        hit_ids = {sid for sid, t in texts.items() if t in close}
        fuzzy = [s for s in sentences
                 if s["id"] in hit_ids and s not in exact]
    lines = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}] {s['text']}"
             for s in exact[:20]]
    lines += [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}] (similar) {s['text']}"
              for s in fuzzy[:8]]
    if not lines:
        return f"No matches for '{query}'. Try a shorter or different phrase."
    return _cap(f"{len(exact)} exact matches:\n" + "\n".join(lines))


# How many lines of visual timeline one get_shots call may return. Past this
# the shortest moments are dropped with a pointer to a narrower range — an
# unbounded timeline on a long video would bury the transcript in the same
# context window.
SHOT_TIMELINE_MAX_LINES = 60


def get_shots(ctx, start=0, end=None):
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    rows = [s for s in ctx.index.get("shots", [])
            if s["end"] > start and s["start"] < end]
    if not rows:
        return f"No shots between {start}s and {end}s."
    lines = []
    for s in rows:
        cap = s.get("caption") or {}
        desc = "; ".join(x for x in (cap.get("action"), cap.get("setting"),
                                     cap.get("people")) if x)
        ost = cap.get("on_screen_text")
        if ost:
            desc += f'; on-screen text: "{ost}"'
        if cap.get("subtitles"):
            desc += "; [burned-in captions visible]"
        lines.append(f"[#{s['id']} {_fmt_t(s['start'])}-{_fmt_t(s['end'])}] "
                     f"{desc or '(no visual caption)'}")
    # Round 69: shots are SCENE structure — on a locked-off talking head there
    # is exactly one of them, and one line describing 19 minutes of footage was
    # the whole of what the editor could see. The sampled timeline below says
    # what is on screen over time, collapsed so that each line is a CHANGE.
    tl = _mod("visual").timeline_lines(ctx.index.get("moments") or [], start, end,
                               max_lines=SHOT_TIMELINE_MAX_LINES)
    if tl:
        step = VISUAL_SAMPLE_S
        lines.append(f"\nWHAT IS ON SCREEN OVER TIME (sampled ~every {step:g}s, "
                     "consecutive identical frames merged — a span means "
                     "nothing changed through it):")
        lines += tl
    elif not any(s.get("caption") for s in rows):
        # v10 indexes carry no captions by design: the PICTURE is the
        # filmstrip in your context and look_at. These timings are the cut
        # geometry only — never pick scenes or zoom targets from them alone.
        lines.append(
            "\nThese are scene-change timings only. WHAT IS ON SCREEN is in "
            "your filmstrip tiles (timestamps under each frame) — read those, "
            "or look_at exact times for a closer view, before choosing "
            "anything visual.")
    return _cap("\n".join(lines))


def get_edl(ctx, sections=None, compact=False, offset=0, limit=100):
    read_edl = _mod("edl_read").read_edl
    row = ctx.latest_edl()
    key = ("get_edl", row["version"], json.dumps([sections, compact, offset, limit], sort_keys=True))
    evidence = _evidence_cache(ctx)
    if key in evidence:
        _metric(ctx, "evidence_reads_reused")
        return (f"UNCHANGED EDL — v{row['version']} and these exact sections "
                "were already returned earlier in this turn. The earlier "
                "JSON remains authoritative; call again after a write or "
                "request different sections/page.")
    result = read_edl(row, ctx.duration, _program_map(ctx, row["json"]),
                      sections, compact, offset, limit)
    if not result.startswith("REJECTED"):
        evidence.add(key)
    return result




def edl_used_asset_keys(edl):
    """Every storage_key the current edit already places — inserts,
    overlays, music, voiceover and sfx. Unused project files are still
    available to place; this set is how the inventory says which are which."""
    keys = set()
    if not isinstance(edl, dict):
        return keys
    for ins in edl.get("inserts") or []:
        if ins.get("asset_key"):
            keys.add(ins["asset_key"])
    for ov in edl.get("overlays") or []:
        if ov.get("asset_key"):
            keys.add(ov["asset_key"])
    for m in edl.get("music") or []:
        if m.get("storage_key"):
            keys.add(m["storage_key"])
    for v in edl.get("voiceover") or []:
        if v.get("asset_key"):
            keys.add(v["asset_key"])
    for s in edl.get("sfx") or []:
        if s.get("storage_key"):
            keys.add(s["storage_key"])
    return keys


def _tool_identifier(value):
    """Normalize one double-encoded identifier, never arbitrary tool text."""
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        value = value[1:-1].strip()
    return value


def list_assets(ctx, kind=None):
    """Project files the user has uploaded or the system has produced.
    kind 'audio' (the pipeline's extracted copy of the source's own audio,
    used for transcription) is deliberately excluded everywhere — offering
    it as 'music' just doubles the speaker's voice under itself."""
    kinds = {"music": ["music"], "image": ["image_ref"],
             "clip": ["video_clip"], "render": ["render"],
             "all": ["music", "image_ref", "video_clip",
                     "render", "original"]}
    requested = _tool_identifier(kind or "music").lower()
    aliases = {
        "video": "clip", "videos": "clip", "clips": "clip",
        "images": "image", "photo": "image", "photos": "image",
        "audio": "music", "song": "music", "songs": "music",
        "renders": "render", "everything": "all",
    }
    canonical = aliases.get(requested, requested)
    sel = kinds.get(canonical)
    if not sel:
        return ("REJECTED: kind must be one of "
                f"{', '.join(sorted(kinds))}.")
    rows = ctx.db.run(assets_by_kinds, ctx.project_id, sel)
    if not rows:
        if sel == kinds["music"]:
            return ("No audio uploaded to this project. fetch_url ingests a "
                    "music link the user pastes (song URL, YouTube, "
                    "SoundCloud); for a SPECIFIC song they named, load the "
                    "acquisition department and find_song can locate a link. "
                    "For a generic genre/vibe or a trending platform sound, "
                    "ask the user to attach the file (paperclip button in "
                    "chat, mp3/wav/m4a) instead of repeatedly searching, or "
                    "offer library tracks with ask_user and place their "
                    "pick. Sound effects come from the approved library "
                    "(list_sound_library).")
        return f"No {canonical} assets in this project."
    try:
        edl_row = ctx.latest_edl()
        used = edl_used_asset_keys(edl_row["json"])
    except Exception:
        edl_row = {"version": None}
        used = set()
    unused_n = 0
    lines = []
    for a in rows:
        m = a.get("meta") or {}
        dur = f", {a['duration_s']:.1f}s" if a.get("duration_s") else ""
        cap = f" — {m['caption'][:120]}" if m.get("caption") else ""
        role = m.get("role") or ""
        role_bit = (f" ROLE={role} — STYLE REFERENCE, not footage: "
                    "look_at_asset / extract_audio, never insert_media"
                    if role in ("edit_reference", "shorts_reference")
                    else "")
        key = a.get("storage_key") or ""
        if a["kind"] == "render" or role in ("edit_reference",
                                             "shorts_reference"):
            place = ""
        elif key in used:
            place = " — in the current edit"
        else:
            place = (" — AVAILABLE, not in the current edit "
                     "(insert_media / add_overlay / add_music)")
            unused_n += 1
        staged = (m.get("staged") is True or m.get("staged") == "true")
        if staged:
            place = " — STAGING TRAY (user has not pressed Submit)"
        rights_bit = ""
        if a["kind"] == "music":
            evidence_input = dict(m)
            evidence_input.setdefault("downloaded_sha256", a.get("sha256"))
            rights = _mod("music_rights").provenance(evidence_input)
            rights["tool_normalized_license_note"] = (
                m.get("tool_normalized_license_note") or
                m.get("license_note") or _mod("music_rights").license_note(
                    evidence_input))
            rights_bit = (" — MUSIC_PROVENANCE=" + json.dumps(
                rights, sort_keys=True, separators=(",", ":")))
            source_probe = {
                "source_audio_stream_status": (
                    m.get("source_audio_stream_status") or "not_exposed"),
                "source_has_audio_stream": (
                    m.get("source_has_audio_stream")
                    if isinstance(m.get("source_has_audio_stream"), bool)
                    else None),
                "source_audio_stream_codec": (
                    m.get("source_audio_stream_codec") or None),
                "source_audio_stream_channels": (
                    m.get("source_audio_stream_channels")
                    if type(m.get("source_audio_stream_channels")) is int
                    else None),
                "source_audio_stream_probe_tool": (
                    m.get("source_audio_stream_probe_tool") or None),
                "source_audio_stream_probed_at": (
                    m.get("source_audio_stream_probed_at") or None),
            }
            rights_bit += (" — MUSIC_SOURCE_PROBE=" + json.dumps(
                source_probe, sort_keys=True, separators=(",", ":")))
        lines.append(f"[{a['kind']}] storage_key={a['storage_key']} "
                     f"\"{m.get('filename', '?')}\"{dur}{cap}{role_bit}"
                     f"{place}{rights_bit}")
    head = ""
    if unused_n:
        head = (f"{unused_n} file(s) are in the project and NOT on the "
                "timeline — use them; do not tell the user they have no "
                "footage.\n")
    out = head + "\n".join(lines)
    # The commonest way a user delivers a SONG is as the video they found it
    # in — a TikTok/Reel download. Say here that this works, at the moment the
    # agent is looking at the clip, rather than leaving it to guess.
    if any(a["kind"] == "video_clip" for a in rows):
        out += ("\nAny [video_clip] can also be used as SOUND ONLY — pass its "
                "storage_key straight to add_music / add_sfx / add_voiceover "
                "(or call extract_audio first). Its picture stays out of the "
                "edit entirely.")
    result = _cap(out)
    fingerprint = (
        "list_assets", str(kind or "music").strip().lower(),
        edl_row.get("version"),
        tuple((a.get("id"), a.get("kind"), a.get("storage_key"),
               a.get("duration_s"),
               json.dumps(a.get("meta") or {}, sort_keys=True, default=str))
              for a in rows))
    evidence = _evidence_cache(ctx)
    if fingerprint in evidence:
        _metric(ctx, "evidence_reads_reused")
        return ("UNCHANGED INVENTORY — this exact asset list and placement "
                "state was already returned earlier in this turn. Use that "
                "result; call again only after an asset or EDL change.")
    evidence.add(fingerprint)
    return result


def list_motion_templates(ctx, category=None):
    motion_templates = _mod("motion_templates")
    cat = motion_templates.catalog(category or None)
    if not cat:
        if category:
            known = sorted({c for t in motion_templates.catalog()
                            for c in motion_templates.categories(t) if c})
            return (f"No motion templates match category '{category}'. Categories: "
                    f"{', '.join(known)}.")
        return "No motion templates are installed on this deployment."
    lines = []
    for t in cat:
        ps = []
        for k, p in t["params"].items():
            bit = k
            if p.get("required"):
                bit += "*"
            if p["type"] == "enum":
                bit += "=" + "|".join(map(str, p.get("values") or []))
            elif p["type"] == "rows":
                # a row's shape: the editor never has to guess it (Oct 2026
                # run: rejected calls and 's'/'xl' sizes nobody read)
                types = p.get("field_types") or {}
                bit += "=[{" + ",".join(
                    f + (":" + ("|".join(types[f]) if isinstance(types[f], list)
                                else types[f]) if f in types else "")
                    for f in p.get("fields") or []) + "}]"
            elif "default" in p and p["type"] not in ("text", "str"):
                bit += f"={p['default']}"
            ps.append(bit)
        cats = "/".join(c for c in motion_templates.categories(t) if c)
        lines.append(f"- {t['name']} [{cats}, ~{t['duration']}s]: {t['description']} "
                     f"Params: {', '.join(ps)}" + (f". Sound: {', '.join(t['sfx'])}" if t["sfx"] else ""))
    return ("Motion templates (add_motion_graphic(template, start, end, params)). * = required.\n"
            + "\n".join(lines) +
            "\nOr template='html' with your own composition on the MG runtime (read_skill "
            "'motion-design' for the API).")


FAST_READ_FUNCTIONS = {
    "get_words": get_words,
    "get_transcript": get_transcript,
    "get_kept_transcript": get_kept_transcript,
    "search_transcript": search_transcript,
    "get_shots": get_shots,
    "get_edl": get_edl,
    "list_motion_templates": list_motion_templates,
    "list_assets": list_assets,
}


def main_asset_alias(name, args, notes):
    """agent_tools._normalize_tool_call's repair for the main video's key."""
    if name in {"get_transcript", "get_audio_analysis"}:
        key = str(args.get("asset_key") or "").strip().lower()
        if key in {"main", "source", "original", "main_video"}:
            args.pop("asset_key", None)
            notes.append("main asset alias -> omitted")


def run_read(ctx, name, args):
    """(text, structured_outcome) of agent_tools._execute for a pure read.

    Only the success path is reproduced: a call whose arguments the tool
    does not take, or that raises, returns None so the caller uses the queued
    path, whose executor owns every error text. The text is exactly what
    _execute returns for the same context (tests run both side by side).
    """
    if name not in FAST_READ_FUNCTIONS or not isinstance(args, dict):
        return None
    name = str(name or "").strip()
    args = dict(args or {})
    main_asset_alias(name, args, [])
    fn = FAST_READ_FUNCTIONS[name]
    params = inspect.signature(fn).parameters
    if any(key == "ctx" or key not in params for key in args):
        return None
    try:
        out = fn(ctx, **args)
    except Exception:
        return None
    if not isinstance(out, str):
        return None
    tool_outcome = _mod("tool_outcome")
    outcome = tool_outcome.from_legacy(
        out, state_changed=False, idempotent=name in SAFE_READS,
        affected_ranges=[])
    return outcome.render_text(), outcome.to_dict()


# ── What mcp_exec and agent_loop make of a call (shared with them) ─────────

def tool_result_contract(structured, text):
    """mcp_exec's deterministic refusal/failure flag for one tool result."""
    tool_outcome = _mod("tool_outcome")
    if not isinstance(structured, dict) or \
            structured.get("status") not in tool_outcome.STATUSES:
        structured = tool_outcome.from_legacy(text).to_dict()
    return {
        "is_error": structured["status"] != "success",
        "tool_outcome": structured,
    }


def visual_evidence(available_before=0, created_this_call=0,
                    publish_attempts=0, published_this_call=0, remaining=0):
    return {
        "available_before": available_before,
        "created_this_call": created_this_call,
        "publish_attempts": publish_attempts,
        "published_this_call": published_this_call,
        "remaining": remaining,
    }


def preview_summary(preview_asset):
    """The complete preview of an EDL version, as an MCP result names it."""
    preview_meta = preview_asset.get("meta") or {}
    # The fact, not the stamp (listener_policy): a Studio render no listener
    # could hear reports false, and an unstamped legacy render no longer
    # reports false by default.
    policy = listener_policy(preview_meta)
    preview = {
        "render_type": "complete_preview",
        "render_job_id": preview_meta.get("render_job_id"),
        "quality": preview_meta.get("quality", "draft"),
        "sha256": preview_asset.get("sha256") or preview_meta.get("sha256"),
        "edl_version": int(preview_meta.get("edl_version")),
        "duration_s": preview_asset.get("duration_s"),
        "audio_model_review": policy is None,
        "listener_policy": policy,
        "asset_id": preview_asset.get("id"),
    }
    if preview_meta.get("motion_warnings"):
        # Motion items this render could not draw: the edit is
        # intact, the pixels are missing them.
        preview["motion_warnings"] = list(preview_meta["motion_warnings"])
    return preview


def mcp_read_result(text, structured, edl_version, plan, preview_asset):
    """mcp_exec.run_mcp_job's result for a read on a fresh context."""
    out = {"text": text, "edl_version": edl_version, "edl_changed": False}
    if plan in METERED_PLANS:
        out["metered_contract"] = True
    out.update(tool_result_contract(structured, text))
    out["visual_evidence"] = visual_evidence()
    if preview_asset:
        out["preview"] = preview_summary(preview_asset)
    return out


def activity_row(name, args, result, source=None, edl_version=None,
                 change=None):
    """(content, meta) of the studio activity-feed row for one tool call."""
    res_str = (result or "").replace("\n", " ")
    # Long enough that a diff line PLUS its appended WARNING lines survive —
    # truncating warnings out of the activity feed would hide them from the
    # user entirely.
    if len(res_str) > 600:
        res_str = res_str[:600] + "…"
    # Auto-triggered previews read as "auto preview" in the UI; the raw
    # trigger tag stays in meta for the logs.
    if name == "render_preview" and (args or {}).get("auto"):
        label = "auto preview"
    else:
        arg_str = json.dumps(args or {}, ensure_ascii=False)
        if len(arg_str) > 160:
            arg_str = arg_str[:160] + "…"
        label = f"{name}{arg_str if arg_str != '{}' else '()'}"
    meta = {"tool": name, "args": args, "source": source or "agent",
            "tool_outcome": {"status": _mod("tool_outcome").from_legacy(
                result).status}}
    if edl_version is not None:
        # The EDL version current when this call ran — lets the studio roll
        # the activity feed back in step with the version stepper.
        meta["edl_version"] = edl_version
    if change:
        # Structural diff of THIS write (edl_diff.change_ranges): the output
        # ranges the edit touched, so the studio can flash them briefly when
        # the next preview lands. Only ever present on the row of an EDL
        # write; read rows and non-writing calls never carry it.
        meta["change"] = change
    if source:
        # Which driver made this call. The studio renders MCP activity exactly
        # like the agent's — it IS the same tool doing the same thing — but the
        # admin views and the logs must be able to tell an outside model's edit
        # from ours.
        meta["source"] = source
    return f"{label} → {res_str}", meta
