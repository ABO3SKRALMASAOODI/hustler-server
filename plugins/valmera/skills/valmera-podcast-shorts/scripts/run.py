#!/usr/bin/env python3
"""Lean run state for valmera-podcast-shorts v9.

One run.json per run folder, guarded by a file lock (safe for a coordinator
and several editors writing at once). It records what the coordinator decided
-- shorts, Looks, briefs, candidates, verdicts, exports, exceptions -- so a run
can resume and every selected short is accounted for. It does not poll
Valmera, score taste or certify quality: Valmera's shorts_status is the truth
for jobs and EDL versions, and the review verdict is the coordinator's call.

Flow per short:
  add-short -> assign (brief, editor) -> candidate -> review ship|fix|kill
  -> export ; exception closes a short that cannot ship.
register does add-short + assign --brief for a whole assignments folder in
one call. An editor records its candidate whether or not it holds a slot
(a short still "queued" or back in "fix" is taken over by the candidate).
First-candidate gate: until the run has one review, no more than
max_editors shorts are handed out, so a systemic defect (layout, source,
template, a Valmera tool) is caught on the first candidate, not the ninth.
finalize writes exports/manifest.json, exports/publishing-manifest.json and
exports/PUBLISHING.md with the same file names and keys as v7 runs.

Runs created by the v7 state machine (scripts/run_state.py) are resumed with
that script; this one refuses them.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


VERSION = "valmera-podcast-shorts-v9"
LEGACY_VERSION = "valmera-podcast-shorts-v7"
DEFAULT_EDITORS = 3
HERO_CAP = 12
BRIEF_MAX_WORDS = 250
NOTE_MAX_LINES = 10
# Beat coverage is planned in the brief: the body needs a beat that adds
# information every ~6-8 s of PROGRAM, which is about 10-12 s of SOURCE
# before tightening (programs keep 45-80% of their window). Oct 2026 run:
# 8 of 9 briefs left 13-27 s source gaps and the reviews killed 5 shorts
# for beatless stretches the brief had already planned.
BEAT_GAP_MAX_S = 12.0
LOOKS = (
    "headline-pro", "editorial-serif", "kinetic-poster", "cinematic-doc",
    "mono-noir", "clean-data", "creator-glow",
)
STRUCTURES = (
    "fast-conversation", "headline-conversation", "hook-to-silent-montage",
    "silent-action-to-conversation",
)
# Music is off unless the owner supplies or approves a specific song for the
# run (init --song). Agents never choose music: no CC0 library bed, no stock
# track, no song picked by taste. Runs created with the retired "auto" switch
# resolve to off.
MUSIC = ("off", "on")
# A brief inherits the run switch unless it says "off" (keep this short dry)
# or "on" (use the owner's song here; only valid when the run has one).
MUSIC_BRIEF = ("inherit", "on", "off")
NEEDS_SONG = ("music on needs the owner's supplied or approved song for this run "
              "(run.py init --song '<file, link or Artist - Title>'); never choose one")
CHECKS = ("hook", "payoff", "targets", "attention", "clean")
TERMINAL = ("exported", "killed", "needs_user_review", "failed_technical")
STATUSES = ("queued", "editing", "candidate", "fix", "approved") + TERMINAL
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class StateError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: object) -> None:
    write_atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_dir(value: str) -> Path:
    return Path(value).expanduser().resolve()


def check_version(state: dict) -> None:
    version = state.get("version")
    if version == LEGACY_VERSION:
        raise StateError(
            "this is a v7 run; resume it with scripts/run_state.py and "
            "legacy/README.md, not run.py")
    if version != VERSION:
        raise StateError(f"expected {VERSION}, found {version!r}")


@contextlib.contextmanager
def locked(directory: Path, *, write: bool = True):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            path = directory / "run.json"
            if not path.is_file():
                raise StateError(f"run is not initialized: {directory}")
            state = json.loads(path.read_text(encoding="utf-8"))
            check_version(state)
            yield state
            if write:
                state["updated_at"] = now()
                write_json(path, state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def event(state: dict, kind: str, **fields: object) -> None:
    state["events"].append({"at": now(), "type": kind, **fields})


def short(state: dict, short_id: str) -> dict:
    try:
        return state["shorts"][short_id]
    except KeyError as exc:
        known = ", ".join(state["shorts"]) or "none"
        raise StateError(f"unknown short {short_id!r} (known: {known})") from exc


def require(item: dict, allowed: tuple[str, ...], action: str) -> None:
    if item["status"] not in allowed:
        raise StateError(
            f"cannot {action} {item['short_id']} while it is {item['status']}; "
            f"expected {' or '.join(allowed)}")


def text_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{label} must be a non-empty string")
    return " ".join(value.split())


def number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(float(value)):
        raise StateError(f"{label} must be a number")
    return float(value)


def read_json(path_text: str, label: str) -> object:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise StateError(f"{label} does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"{label} is not valid JSON: {exc}") from exc


def hms(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    minutes, rest = divmod(float(seconds), 60.0)
    hours, minutes = divmod(int(minutes), 60)
    body = f"{minutes}:{rest:04.1f}"
    return f"{hours}:{minutes:02d}:{rest:04.1f}" if hours else body


def effective_music(state: dict, item: dict) -> tuple[str, str]:
    """('on' | 'off', why) for one short. Off unless the run records the
    owner's song: a brief "off" keeps a short dry; a brief "on" or the run
    switch "on" puts that song under it. Nothing else turns music on."""
    brief_music = (item.get("brief") or {}).get("music") or "inherit"
    if brief_music == "off":
        return "off", "brief off"
    if not state.get("song"):
        return "off", "no owner song"
    if brief_music == "on":
        return "on", "brief on (owner song)"
    run_music = state.get("music") or "off"
    if run_music == "on":
        return "on", "run on (owner song)"
    return "off", "run off" if run_music == "off" else f"run {run_music} retired: off"


def music_song(state: dict, item: dict) -> str | None:
    """The owner's song when music is on for this short, else None."""
    return state.get("song") if effective_music(state, item)[0] == "on" else None


def set_music(item: dict, state: dict) -> None:
    item["music_effective"], item["music_why"] = effective_music(state, item)
    item["music_song"] = music_song(state, item)


def editors_busy(state: dict) -> dict:
    return {item["editor"]: item["short_id"] for item in state["shorts"].values()
            if item["status"] == "editing" and item.get("editor")}


def gate_open(state: dict) -> bool:
    """The first-candidate gate is open once any short has a review (or the
    run was told to skip it with assign --no-gate)."""
    return bool(state.get("gate_skipped")) or any(
        item["reviews"] for item in state["shorts"].values())


def handed_out(state: dict) -> list[str]:
    """Shorts an editor has started (editing or beyond, or with a candidate)."""
    return [item["short_id"] for item in state["shorts"].values()
            if item["status"] != "queued" or item["candidates"]]


def beat_gap(payload: dict, window: list | None) -> tuple[float, list] | None:
    """(largest gap in source seconds, [from, to]) between the window start
    and the brief's timed beats, the payoff's tail excluded; None when the
    brief has no window or no timed beats to measure."""
    window = payload.get("source_window_s") or window
    if not (isinstance(window, (list, tuple)) and len(window) == 2):
        return None
    try:
        start, end = float(window[0]), float(window[1])
    except (TypeError, ValueError):
        return None
    times = []
    for beat in payload.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        try:
            at = float(beat.get("source_s"))
        except (TypeError, ValueError):
            continue
        if start - 1.0 <= at <= end + 1.0:
            times.append(max(start, at))
    if not times:
        return None
    points = [start] + sorted(times)
    gaps = [(b - a, [a, b]) for a, b in zip(points, points[1:])]
    return max(gaps, key=lambda g: g[0])


# ── commands ───────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> dict:
    directory = run_dir(args.run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("source", "assignments", "candidates", "exports"):
        (directory / name).mkdir(exist_ok=True)
    path = directory / "run.json"
    song = " ".join(args.song.split()) if args.song and args.song.strip() else None
    music_arg = args.music or ("on" if song else None)
    settings = {
        "music": music_arg, "song": song, "max_editors": args.max_editors,
        "max_jobs": args.max_jobs, "parent_project_id": args.parent_project_id,
        "min_final_s": args.min_final_s, "max_final_s": args.max_final_s,
    }
    meta = {"title": args.source_title, "channel": args.channel,
            "upload_date": args.upload_date, "credit_line": args.credit_line,
            "people_verified": args.people or None,
            "archival_quality_note": args.archival_note}
    for key in ("max_editors", "max_jobs"):
        if settings[key] is not None and settings[key] < 1:
            raise StateError(f"--{key.replace('_', '-')} must be at least 1")
    if path.exists():
        with locked(directory) as state:
            if state["run_id"] != args.run_id:
                raise StateError(
                    f"{path} belongs to run {state['run_id']!r}; use a new folder")
            changed = {k: v for k, v in settings.items() if v is not None}
            if (changed.get("music") or state.get("music")) == "on" and \
                    not (changed.get("song") or state.get("song")):
                raise StateError(NEEDS_SONG)
            state.update(changed)
            for item in state["shorts"].values():
                if item.get("brief"):
                    set_music(item, state)
            state["source_meta"].update({k: v for k, v in meta.items() if v})
            if changed:
                event(state, "settings", **changed)
            return state
    if settings["music"] == "on" and not song:
        raise StateError(NEEDS_SONG)
    stamp = now()
    editors = settings["max_editors"] or DEFAULT_EDITORS
    state = {
        "version": VERSION, "run_id": args.run_id, "source": args.source,
        "source_meta": {k: v for k, v in meta.items() if v},
        "parent_project_id": settings["parent_project_id"],
        "music": settings["music"] or "off", "song": song,
        "max_editors": editors, "max_jobs": settings["max_jobs"] or editors,
        "min_final_s": settings["min_final_s"] or 15.0,
        "max_final_s": settings["max_final_s"] or 45.0,
        "stage": "editing", "created_at": stamp, "updated_at": stamp,
        "shorts": {}, "events": [{"at": stamp, "type": "run_initialized"}],
    }
    write_json(path, state)
    return state


def cmd_add_short(args: argparse.Namespace) -> dict:
    if not ID_RE.fullmatch(args.short_id):
        raise StateError("short id must be 1-40 letters, digits, '_' or '-'")
    if (args.source_start is None) != (args.source_end is None):
        raise StateError("pass both --source-start and --source-end, or neither")
    if args.source_start is not None and args.source_end <= args.source_start:
        raise StateError("--source-end must be after --source-start")
    if args.score is not None and not 0 <= args.score <= 100:
        raise StateError("--score is the make_shorts score, 0-100")
    with locked(run_dir(args.run_dir)) as state:
        for other in state["shorts"].values():
            if other["child_project_id"] == args.project_id and \
                    other["short_id"] != args.short_id:
                raise StateError(
                    f"project {args.project_id} already belongs to {other['short_id']}")
        existing = state["shorts"].get(args.short_id)
        if existing and existing["child_project_id"] != args.project_id:
            raise StateError(f"{args.short_id} already maps to project "
                             f"{existing['child_project_id']}")
        heroes = [i for i in state["shorts"].values()
                  if i["tier"] == "hero" and i["short_id"] != args.short_id]
        if args.tier == "hero" and len(heroes) >= HERO_CAP:
            raise StateError(
                f"hero cap reached ({HERO_CAP}); rank the slate and keep the best, "
                "or add this one with --tier standard")
        item = existing or {
            "short_id": args.short_id, "child_project_id": args.project_id,
            "status": "queued", "editor": None, "look": None, "structure": None,
            "brief": None, "candidates": [], "fix_used": False, "review": None,
            "reviews": [], "export": None, "exception": None,
        }
        item.update({
            "title": text_value(args.title, "--title"), "tier": args.tier,
            "rank": args.rank, "score": args.score, "speaker": args.speaker,
            "source_range_s": ([args.source_start, args.source_end]
                               if args.source_start is not None else
                               (existing or {}).get("source_range_s")),
            "updated_at": now(),
        })
        state["shorts"][args.short_id] = item
        if not existing:
            event(state, "short_added", short_id=args.short_id)
        return item


def load_brief(path_text: str, short_id: str, window: list | None = None) -> dict:
    payload = read_json(path_text, "brief")
    if not isinstance(payload, dict):
        raise StateError("brief must be a JSON object")
    if payload.get("short_id") not in (None, short_id):
        raise StateError(f"brief is for {payload.get('short_id')!r}, not {short_id!r}")
    look = payload.get("look")
    if look not in LOOKS and not (isinstance(look, str) and look.startswith("custom-")
                                  and SLUG_RE.fullmatch(look)):
        raise StateError(f"brief look must be one of {', '.join(LOOKS)} "
                         "(or a custom-<slug> Look described in the brief)")
    if payload.get("structure") not in STRUCTURES:
        raise StateError(f"brief structure must be one of {', '.join(STRUCTURES)}")
    story = payload.get("story")
    if not isinstance(story, dict):
        raise StateError("brief story must be an object with viewer_question, "
                         "hook, turn and payoff")
    clean_story = {key: text_value(story.get(key), f"story.{key}")
                   for key in ("viewer_question", "hook", "turn", "payoff")}
    brief_text = text_value(payload.get("brief"), "brief text")
    words = len(brief_text.split())
    if words > BRIEF_MAX_WORDS:
        raise StateError(f"brief text is {words} words; keep it near 150 "
                         f"(max {BRIEF_MAX_WORDS})")
    music = payload.get("music", "inherit")
    if music not in MUSIC_BRIEF:
        raise StateError(
            f"brief music must be one of {', '.join(MUSIC_BRIEF)} (inherit = "
            "follow the run switch; off keeps this short dry; on needs the "
            "owner's song for the run)")
    beats = payload.get("beats", [])
    if not isinstance(beats, list):
        raise StateError("brief beats must be a list")
    gap = beat_gap(payload, window)
    gap_reason = payload.get("beat_gap_reason")
    if gap and gap[0] > BEAT_GAP_MAX_S and not (
            isinstance(gap_reason, str) and gap_reason.strip()):
        a, b = gap[1]
        raise StateError(
            f"brief beats leave source {a:.1f}-{b:.1f} s ({gap[0]:.1f} s) with no "
            f"beat; plan one at least every {BEAT_GAP_MAX_S:g} s of source (about "
            "6-8 s of program): a thesis line, number, name, list or contrast in "
            "that span, with its cue and source_s. If the span is cut in the edit, "
            "say so in beat_gap_reason")
    resolved = Path(path_text).expanduser().resolve()
    record = {
        "file": str(resolved), "sha256": sha256(resolved), "words": words,
        "story": clean_story, "music": music, "beats": len(beats),
        "max_beat_gap_s": round(gap[0], 1) if gap else None,
    }
    for key in ("headline", "speaker", "structure_reason", "closest_alternative"):
        value = payload.get(key)
        record[key] = " ".join(value.split()) if isinstance(value, str) and \
            value.strip() else None
    return {"look": look, "structure": payload["structure"], "brief": record}


def cmd_assign(args: argparse.Namespace) -> dict:
    with locked(run_dir(args.run_dir)) as state:
        item = short(state, args.short_id)
        require(item, ("queued", "editing", "fix"), "assign")
        if args.brief:
            loaded = load_brief(args.brief, args.short_id, item.get("source_range_s"))
            if item["status"] in ("editing", "fix") and item.get("structure") and \
                    loaded["structure"] != item["structure"] and item["candidates"]:
                raise StateError("structure cannot change after a candidate exists")
            if loaded["brief"]["music"] == "on" and not state.get("song"):
                raise StateError(f"brief {NEEDS_SONG}")
            item.update(loaded)
        if item.get("brief"):
            set_music(item, state)
        if args.editor is not None:
            editor = text_value(args.editor, "--editor")
            if not item.get("brief"):
                raise StateError("record a brief before handing the short to an editor")
            busy = editors_busy(state)
            if busy.get(editor) not in (None, args.short_id):
                raise StateError(f"editor {editor!r} is still on {busy[editor]}")
            others = {e for e, sid in busy.items() if sid != args.short_id}
            if item["status"] != "editing" and len(others) >= state["max_editors"]:
                raise StateError(
                    f"{len(others)} editors are busy (max {state['max_editors']}); "
                    "wait for a candidate or raise --max-editors with init (an "
                    "editor already working records its candidate without a slot)")
            if args.no_gate and not state.get("gate_skipped"):
                state["gate_skipped"] = True
                event(state, "gate_skipped", short_id=args.short_id)
            started = [s for s in handed_out(state) if s != args.short_id]
            if item["status"] == "queued" and not gate_open(state) and \
                    len(started) >= state["max_editors"]:
                raise StateError(
                    "first-candidate gate: review the run's first candidate "
                    f"(run.py review) before handing out short #{len(started) + 1}. "
                    "If its verdict names a shared cause (layout, source, template, "
                    "a Valmera tool), fix the framing recipe and open briefs first. "
                    "--no-gate only for a source whose recipe is already proven")
            item["editor"] = editor
            item["status"] = "editing"
            event(state, "assigned", short_id=args.short_id, editor=editor,
                  look=item["look"], structure=item["structure"])
        elif not args.brief:
            raise StateError("pass --brief, --editor or both")
        item["updated_at"] = now()
        return item


def cmd_register(args: argparse.Namespace) -> dict:
    """add-short + assign --brief for every assignments/<id>.json at once.
    Each file carries short_id and child_project_id (plus rank, score,
    speaker, tier, source_window_s, child_title or headline). All briefs are
    checked before anything is written, so one bad brief writes nothing."""
    folder = Path(args.assignments).expanduser().resolve()
    files = sorted(p for p in folder.glob("*.json") if p.is_file())
    if not files:
        raise StateError(f"no assignment JSON files in {folder}")
    plans = []
    for path in files:
        payload = read_json(str(path), f"assignment {path.name}")
        if not isinstance(payload, dict) or not payload.get("short_id"):
            raise StateError(f"{path.name}: needs short_id")
        if not isinstance(payload.get("child_project_id"), int):
            raise StateError(f"{path.name}: needs an integer child_project_id")
        window = payload.get("source_window_s")
        load_brief(str(path), payload["short_id"], window)  # validate first
        title = payload.get("child_title") or payload.get("headline")
        plans.append((path, payload, title, window))
    rows = []
    for path, payload, title, window in plans:
        start, end = (window if isinstance(window, (list, tuple)) and len(window) == 2
                      else (None, None))
        cmd_add_short(argparse.Namespace(
            run_dir=args.run_dir, short_id=payload["short_id"],
            project_id=payload["child_project_id"], title=title,
            tier=payload.get("tier") or "hero", rank=payload.get("rank"),
            score=payload.get("score"), speaker=payload.get("speaker"),
            source_start=start, source_end=end))
        item = cmd_assign(argparse.Namespace(
            run_dir=args.run_dir, short_id=payload["short_id"], brief=str(path),
            editor=None, no_gate=False))
        rows.append({"short_id": item["short_id"], "look": item["look"],
                     "structure": item["structure"],
                     "music_effective": item["music_effective"],
                     "max_beat_gap_s": item["brief"].get("max_beat_gap_s")})
    return {"registered": len(rows), "shorts": rows}


def cmd_candidate(args: argparse.Namespace) -> dict:
    note = args.note
    if args.note_file:
        note = Path(args.note_file).expanduser().read_text(encoding="utf-8")
    if not note or not note.strip():
        raise StateError("a candidate needs a note (--note or --note-file)")
    lines = [line for line in note.strip().splitlines() if line.strip()]
    if len(lines) > NOTE_MAX_LINES:
        raise StateError(f"candidate note has {len(lines)} lines; "
                         f"keep it to {NOTE_MAX_LINES}")
    if args.edl_version < 1:
        raise StateError("--edl-version must be a positive integer")
    handback = read_json(args.handback, "handback") if args.handback else None
    if handback is not None and not isinstance(handback, dict):
        raise StateError("handback must be a JSON object")
    with locked(run_dir(args.run_dir)) as state:
        item = short(state, args.short_id)
        require(item, ("editing", "queued", "fix"), "record a candidate for")
        if item["status"] == "editing":
            if args.editor and item.get("editor") != args.editor:
                raise StateError(
                    f"{args.short_id} belongs to editor {item.get('editor')!r}")
        else:
            # An editor that worked without a recorded slot (or the fix pass)
            # records its finished candidate directly: no slot wait, no
            # reviewer bookkeeping on its behalf.
            if not item.get("brief"):
                raise StateError("record the brief (run.py assign --brief) before "
                                 "a candidate")
            if item["status"] == "fix" and not item["candidates"]:
                raise StateError(f"{args.short_id} is in fix with no candidate")
            item["editor"] = args.editor or item.get("editor")
            event(state, "assigned", short_id=args.short_id,
                  editor=item["editor"] or "unrecorded", look=item["look"],
                  structure=item["structure"], implicit=True)
        record = {"preview": args.preview, "edl_version": args.edl_version,
                  "note": "\n".join(lines), "renders": args.renders,
                  "editor": item.get("editor"), "recorded_at": now()}
        local = Path(args.preview).expanduser()
        if "://" not in args.preview and local.is_file():
            record["preview"] = str(local.resolve())
            record["sha256"] = sha256(local)
        if handback is not None:
            record["handback"] = handback
        item["candidates"].append(record)
        item.update(status="candidate", editor=None, updated_at=now())
        event(state, "candidate", short_id=args.short_id,
              edl_version=args.edl_version)
        return item


def parse_checks(text: str | None) -> dict:
    checks: dict[str, str] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if not sep or key not in CHECKS or value not in ("yes", "no", "y", "n"):
            raise StateError(
                f"--checks takes {','.join(c + '=yes|no' for c in CHECKS)}")
        checks[key] = "yes" if value in ("yes", "y") else "no"
    return checks


def cmd_review(args: argparse.Namespace) -> dict:
    checks = parse_checks(args.checks)
    note = text_value(args.note, "--note")
    with locked(run_dir(args.run_dir)) as state:
        item = short(state, args.short_id)
        require(item, ("candidate", "approved"), "review")
        if not item["candidates"]:
            raise StateError(f"{args.short_id} has no candidate to review")
        latest = item["candidates"][-1]
        if args.verdict == "ship":
            missing = [c for c in CHECKS if checks.get(c) != "yes"]
            if missing:
                raise StateError(
                    "ship needs a yes for every binary check; not yes: "
                    + ", ".join(missing) + " (use fix or kill instead)")
            item["status"] = "approved"
        elif args.verdict == "fix":
            if item["fix_used"]:
                raise StateError("the one targeted fix is already used; "
                                 "ship, kill or record an exception")
            item["fix_used"] = True
            item["status"] = "fix"
        else:
            item["status"] = "killed"
            item["exception"] = {"reason": note, "next_action": args.next_action
                                 or "none: killed in review", "status": "killed"}
        item["review"] = {"verdict": args.verdict, "note": note, "checks": checks,
                          "edl_version": latest["edl_version"],
                          "recorded_at": now()}
        item["reviews"].append(item["review"])
        item["updated_at"] = now()
        event(state, "review", short_id=args.short_id, verdict=args.verdict,
              edl_version=latest["edl_version"])
        return item


def probe(path: Path) -> dict:
    tool = shutil.which("ffprobe")
    if not tool:
        return {}
    try:
        result = subprocess.run(
            [tool, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True)
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    stream = (data.get("streams") or [{}])[0]
    out = {}
    if (data.get("format") or {}).get("duration"):
        out["duration"] = float(data["format"]["duration"])
    if stream.get("width") and stream.get("height"):
        out["dimensions"] = [int(stream["width"]), int(stream["height"])]
    return out


def cmd_export(args: argparse.Namespace) -> dict:
    media = Path(args.file).expanduser().resolve()
    if not media.is_file():
        raise StateError(f"final file does not exist: {media}")
    with locked(run_dir(args.run_dir)) as state:
        item = short(state, args.short_id)
        require(item, ("approved",), "export")
        approved = item["review"]["edl_version"]
        if args.edl_version != approved:
            raise StateError(f"export EDL v{args.edl_version} is not the shipped "
                             f"candidate v{approved}")
        prefix = f"{item['structure']}__{args.short_id}__"
        if not media.name.startswith(prefix) or media.suffix.lower() != ".mp4":
            raise StateError(f"final file name must start with {prefix} and end "
                             "in .mp4")
        measured = {} if (args.duration is not None and args.width) else probe(media)
        duration = args.duration if args.duration is not None else measured.get("duration")
        if duration is None:
            raise StateError("could not probe the duration; pass --duration")
        duration = number(duration, "duration")
        if not state["min_final_s"] - 0.05 <= duration <= state["max_final_s"] + 0.05:
            raise StateError(
                f"final is {duration:.2f}s; the run allows {state['min_final_s']:g}-"
                f"{state['max_final_s']:g}s including the native ending")
        dims = [args.width, args.height] if args.width and args.height else \
            measured.get("dimensions")
        ending = number(args.native_ending_s, "--native-ending-s")
        item["export"] = {
            "file": str(media), "sha256": sha256(media),
            "bytes": media.stat().st_size, "duration_s": round(duration, 3),
            "editorial_s": round(duration - ending, 3), "native_ending_s": ending,
            "dimensions": dims, "edl_version": args.edl_version,
            "job_id": args.job_id, "verified_full": bool(args.verified_full),
            "finished_at": now(),
        }
        item.update(status="exported", updated_at=now())
        event(state, "exported", short_id=args.short_id,
              edl_version=args.edl_version, sha256=item["export"]["sha256"])
        return item


def cmd_exception(args: argparse.Namespace) -> dict:
    with locked(run_dir(args.run_dir)) as state:
        item = short(state, args.short_id)
        if item["status"] in TERMINAL:
            raise StateError(f"{args.short_id} is already {item['status']}")
        item["exception"] = {"reason": text_value(args.reason, "--reason"),
                             "next_action": text_value(args.next_action,
                                                       "--next-action"),
                             "status": args.status, "previous_status": item["status"],
                             "recorded_at": now()}
        item.update(status=args.status, editor=None, updated_at=now())
        event(state, "exception", short_id=args.short_id, status=args.status)
        return item


def summary(state: dict) -> dict:
    counts: dict[str, int] = {}
    rows = []
    for item in state["shorts"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        rows.append({
            "short_id": item["short_id"], "status": item["status"],
            "tier": item["tier"], "look": item.get("look"),
            "structure": item.get("structure"), "editor": item.get("editor"),
            "candidates": len(item["candidates"]),
            "edl_version": (item["candidates"][-1]["edl_version"]
                            if item["candidates"] else None),
            "fix_used": item["fix_used"], "title": item["title"],
            "music": (effective_music(state, item)[0]
                      if item.get("brief") else None),
        })
    rows.sort(key=lambda r: (r["status"] in TERMINAL, r["short_id"]))
    busy = editors_busy(state)
    ready = [r["short_id"] for r in rows if r["status"] in ("queued", "fix")
             and state["shorts"][r["short_id"]].get("brief")]
    nxt = []
    idle = max(0, state["max_editors"] - len(busy))
    review = [r["short_id"] for r in rows if r["status"] == "candidate"]
    gated = not gate_open(state) and len(handed_out(state)) >= state["max_editors"]
    if review:
        # reviews go before new edits: a verdict can change every open brief
        nxt.append("review candidates first: " + ", ".join(review)
                   + (" (first-candidate gate: no new shorts until one is reviewed)"
                      if gated else ""))
    if gated:
        ready = [r for r in ready if state["shorts"][r]["status"] == "fix"]
    if idle and ready:
        nxt.append(f"hand {min(idle, len(ready))} short(s) to idle editors: "
                   + ", ".join(ready[:idle]))
    export = [r["short_id"] for r in rows if r["status"] == "approved"]
    if export:
        nxt.append("export approved: " + ", ".join(export))
    unbriefed = [r["short_id"] for r in rows if r["status"] == "queued"
                 and not state["shorts"][r["short_id"]].get("brief")]
    if unbriefed:
        nxt.append("write briefs for: " + ", ".join(unbriefed))
    open_items = [r["short_id"] for r in rows if r["status"] not in TERMINAL]
    if rows and not open_items and state["stage"] != "complete":
        nxt.append("every short is accounted for: run finalize")
    return {
        "version": state["version"], "run_id": state["run_id"],
        "stage": state["stage"], "music": state["music"], "song": state.get("song"),
        "selected": len(rows), "counts": counts,
        "editors": {"busy": busy, "max": state["max_editors"], "idle": idle},
        "max_jobs": state["max_jobs"],
        "renders": sum(c.get("renders") or 1 for i in state["shorts"].values()
                       for c in i["candidates"]),
        "open": open_items, "next": nxt, "shorts": rows,
    }


def cmd_status(args: argparse.Namespace) -> dict:
    with locked(run_dir(args.run_dir), write=False) as state:
        return summary(state)


def format_status(value: dict) -> str:
    song = f" - {value['song']}" if value.get("song") else ""
    lines = [f"{value['run_id']} ({value['stage']}, music {value['music']}{song}): "
             f"{value['selected']} shorts, "
             + ", ".join(f"{k} {v}" for k, v in sorted(value["counts"].items())),
             f"editors busy {len(value['editors']['busy'])}/{value['editors']['max']}"
             f"; renders so far {value['renders']}"]
    for row in value["shorts"]:
        lines.append(f"  {row['short_id']:<8} {row['status']:<17} "
                     f"{(row['look'] or '-'):<15} {(row['structure'] or '-'):<30} "
                     f"music {(row['music'] or '-'):<4} {row['editor'] or ''}")
    lines += [f"next: {n}" for n in value["next"]]
    return "\n".join(lines)


# ── finalize ───────────────────────────────────────────────────────────────

def publishing_item(state: dict, item: dict) -> dict:
    exp = item["export"]
    brief = item.get("brief") or {}
    story = brief.get("story") or {}
    handback = (item["candidates"][-1].get("handback") or {}) if item["candidates"] else {}
    review = item.get("review") or {}
    music = handback.get("music") or music_song(state, item) or (
        "none" if effective_music(state, item)[0] == "off" else "not recorded")
    first_full = next((i["short_id"] for i in state["shorts"].values()
                       if (i.get("export") or {}).get("verified_full")), None)
    if exp.get("verified_full"):
        branding = "corner mark + complete native ending verified in this final"
    elif first_full:
        branding = (f"probed (duration, sha256); the run's representative final "
                    f"{first_full} was watched in full for branding")
    else:
        branding = "probed (duration, sha256); not watched in full"
    rng = item.get("source_range_s")
    return {
        "short_id": item["short_id"], "status": "exported", "title": item["title"],
        "style_lane": item["structure"], "look": item["look"],
        "child_project_id": item["child_project_id"],
        "lane_reason": brief.get("structure_reason"),
        "closest_alternative": brief.get("closest_alternative"),
        "speaker": brief.get("speaker") or item.get("speaker"),
        "source_range_s": rng,
        "source_range_hms": [hms(rng[0]), hms(rng[1])] if rng else None,
        "file": Path(exp["file"]).name, "path": exp["file"],
        "sha256": exp["sha256"], "bytes": exp["bytes"],
        "duration_s": exp["duration_s"], "editorial_s": exp["editorial_s"],
        "native_ending_s": exp["native_ending_s"], "dimensions": exp["dimensions"],
        "edl_version": exp["edl_version"], "export_job_id": exp["job_id"],
        "end_card": f"native Valmera {exp['native_ending_s']:g} s ending",
        "headline": handback.get("headline") or brief.get("headline"),
        "story": {"viewer_question": story.get("viewer_question"),
                  "immediate_hook": story.get("hook"),
                  "turn_or_evidence": story.get("turn"),
                  "final_payoff": story.get("payoff")},
        "kept_transcript": handback.get("kept_transcript"),
        "kept_source_ranges_s": handback.get("kept_source_ranges_s"),
        "broll": handback.get("broll") or [],
        "required_credits": handback.get("required_credits") or [],
        "rights_note": handback.get("rights_note"),
        # "verdict" keeps the v7 value "ready" for consumers of older manifests;
        # "review" carries the v9 verdict. v9 has no numeric score.
        "qc": {"score": None, "verdict": "ready", "review": review.get("verdict"),
               "note": review.get("note"), "checks": review.get("checks"),
               "fix_used": item["fix_used"], "known_minor_issues": []},
        "branding_check": branding, "final_evidence": None,
        "previews_not_final": [
            {"path": c["preview"],
             "label": f"preview EDL v{c['edl_version']} - NOT a final"}
            for c in item["candidates"]],
        "music": music, "published": False,
    }


def exception_item(item: dict) -> dict:
    exc = item.get("exception") or {}
    review = item.get("review") or {}
    return {"short_id": item["short_id"], "child_project_id": item["child_project_id"],
            "title": item["title"], "status": item["status"],
            "look": item.get("look"), "style_lane": item.get("structure"),
            "reason": exc.get("reason"), "next_action": exc.get("next_action"),
            "qc": {"verdict": item["status"], "review": review.get("verdict"),
                   "note": review.get("note") or exc.get("reason")}}


def publishing_markdown(manifest: dict) -> str:
    src = manifest["source"]
    title = src.get("title") or src.get("url")
    out = [f"# Publishing manifest: {title}", "",
           f"Run `{manifest['run_id']}`. Source: {src.get('url')}"
           + (f" ({src['channel']}" + (f", {src['upload_date']}" if src.get("upload_date")
                                         else "") + ")" if src.get("channel") else "")
           + ". Nothing was published or scheduled. "
           + ("Every file below is a verified final; previews are not finals."
              if manifest["items"] else
              "No final was exported in this run; every selected short is listed "
              "under Not exported with its reason and next action."), ""]
    for it in manifest["items"]:
        dims = "x".join(map(str, it["dimensions"])) if it["dimensions"] else "?"
        out += [f"## {it['short_id']} - {it['title']}",
                f"- File: `{it['file']}` ({it['duration_s']} s = {it['editorial_s']} s + "
                f"{it['native_ending_s']} s ending; {dims}; SHA-256 `{it['sha256'][:16]}...`)",
                f"- Look: {it['look']}. Structure: {it['style_lane']}."
                + (f" Why: {it['lane_reason']}" if it.get("lane_reason") else "")
                + (f" Closest alternative: {it['closest_alternative']}"
                   if it.get("closest_alternative") else "")]
        if it.get("source_range_hms"):
            out.append(f"- Source: {it['source_range_hms'][0]}-{it['source_range_hms'][1]}"
                       + (f" ({it['speaker']})" if it.get("speaker") else ""))
        out.append(f"- Music: {it['music']}")
        if it["required_credits"]:
            out.append("- Required credits (post description):")
            out += [f"  - {c if isinstance(c, str) else json.dumps(c)}"
                    for c in it["required_credits"]]
        out.append("")
    if manifest["exceptions"]:
        out += ["## Not exported", ""]
        out += [f"- {e['short_id']} - {e['title']}: {e['status']}. {e.get('reason') or ''}"
                f" Next: {e.get('next_action') or '-'}" for e in manifest["exceptions"]]
        out.append("")
    if manifest.get("not_selected"):
        out += ["## Considered but not selected", ""]
        out += [f"- {n}" for n in manifest["not_selected"]]
        out.append("")
    return "\n".join(out)


def cmd_finalize(args: argparse.Namespace) -> dict:
    directory = run_dir(args.run_dir)
    not_selected: list = []
    if args.not_selected:
        payload = read_json(args.not_selected, "not-selected list")
        if isinstance(payload, dict):
            payload = payload.get("not_selected", [])
        if not isinstance(payload, list):
            raise StateError("not-selected must be a JSON list (or {\"not_selected\": [...]})")
        not_selected = payload
    with locked(directory) as state:
        if not state["shorts"]:
            raise StateError("cannot finalize a run with no selected shorts")
        open_items = [f"{i['short_id']} ({i['status']})" for i in state["shorts"].values()
                      if i["status"] not in TERMINAL]
        if open_items:
            raise StateError("every selected short must be exported or have an "
                             "exception first; open: " + ", ".join(open_items))
        ordered = sorted(state["shorts"].values(), key=lambda i: i["short_id"])
        exported = [i for i in ordered if i["status"] == "exported"]
        exceptions = [exception_item(i) for i in ordered if i["status"] != "exported"]
        stamp = now()
        base = {
            "version": "valmera-shorts-export-v1", "run_id": state["run_id"],
            "source": state["source"], "generated_at": stamp,
            "items": [{
                "short_id": i["short_id"], "child_project_id": i["child_project_id"],
                "edl_version": i["export"]["edl_version"], "title": i["title"],
                "style_lane": i["structure"], "look": i["look"],
                "file": i["export"]["file"], "sha256": i["export"]["sha256"],
                "duration_s": i["export"]["duration_s"], "status": "exported",
            } for i in exported],
            "exceptions": exceptions,
        }
        exports = directory / "exports"
        manifest_path = exports / "manifest.json"
        created = dt.datetime.fromisoformat(state["created_at"])
        meta = state.get("source_meta") or {}
        publishing = {
            "version": "valmera-publishing-manifest-v1", "run_id": state["run_id"],
            "generated_at": stamp,
            "source": {"url": state["source"], "title": meta.get("title"),
                       "channel": meta.get("channel"),
                       "upload_date": meta.get("upload_date"),
                       "sha256": meta.get("sha256"),
                       "people_verified": meta.get("people_verified") or [],
                       "credit_line": meta.get("credit_line"),
                       "archival_quality_note": meta.get("archival_quality_note")},
            "run": {"created_at": state["created_at"],
                    "elapsed_h": round((dt.datetime.now(dt.timezone.utc) - created)
                                       .total_seconds() / 3600, 2),
                    "index_attempts": {"parent_project_id": state.get("parent_project_id"),
                                       "reindex_count": args.reindex_count},
                    "stage": "complete", "skill_version": VERSION,
                    "previews_rendered": summary(state)["renders"],
                    "manifest_from_run_state": str(manifest_path)},
            "publishing": "Nothing was published or scheduled. Paste each item's "
                          "required_credits into the post description.",
            "items": [publishing_item(state, i) for i in exported],
            "exceptions": exceptions, "not_selected": not_selected,
        }
        write_json(manifest_path, base)
        write_json(exports / "publishing-manifest.json", publishing)
        write_atomic(exports / "PUBLISHING.md", publishing_markdown(publishing))
        state["stage"] = "complete"
        state["manifest"] = str(manifest_path)
        event(state, "finalized", exported=len(exported), exceptions=len(exceptions))
        return base


# ── CLI ────────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = root.add_subparsers(dest="command", required=True)

    def command(name: str, func, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("--run-dir", required=True, help="the run folder")
        p.set_defaults(func=func)
        return p

    p = command("init", cmd_init, "Create the run (idempotent; re-run with the same "
                "--run-id to change settings).")
    p.add_argument("--run-id", required=True)
    p.add_argument("--source", required=True, help="source URL or path")
    p.add_argument("--source-title")
    p.add_argument("--channel")
    p.add_argument("--upload-date")
    p.add_argument("--credit-line")
    p.add_argument("--people", action="append", help="verified speaker (repeatable)")
    p.add_argument("--archival-note")
    p.add_argument("--parent-project-id", type=int)
    p.add_argument("--music", choices=MUSIC,
                   help="run music switch (default off: no music in any short). on needs "
                   "--song; agents never choose music")
    p.add_argument("--song", help="the owner's supplied or approved song for this run "
                   "(file path, link or 'Artist - Title'); turns music on unless "
                   "--music off is given")
    p.add_argument("--max-editors", type=int, help=f"default {DEFAULT_EDITORS}")
    p.add_argument("--max-jobs", type=int, help="Valmera jobs in flight (default = editors)")
    p.add_argument("--min-final-s", type=float, help="default 15")
    p.add_argument("--max-final-s", type=float, help="default 45 (includes the ending)")

    p = command("add-short", cmd_add_short, "Register one make_shorts child.")
    p.add_argument("--short-id", required=True)
    p.add_argument("--project-id", required=True, type=int)
    p.add_argument("--title", required=True)
    p.add_argument("--tier", choices=("hero", "standard"), default="hero")
    p.add_argument("--rank", type=int, help="1 = best story in the run")
    p.add_argument("--score", type=int,
                   help="the 0-100 make_shorts score (higher is better)")
    p.add_argument("--speaker")
    p.add_argument("--source-start", type=float)
    p.add_argument("--source-end", type=float)

    p = command("register", cmd_register, "add-short + assign --brief for every "
                "assignment JSON in a folder (one call for the whole slate).")
    p.add_argument("--assignments", required=True,
                   help="folder of <short_id>.json briefs, each with child_project_id")

    p = command("assign", cmd_assign, "Record the brief JSON and/or hand the short "
                "to an editor.")
    p.add_argument("--short-id", required=True)
    p.add_argument("--brief", help="assignment JSON (see references/selection.md)")
    p.add_argument("--editor", help="editor name; one short per editor at a time")
    p.add_argument("--no-gate", action="store_true",
                   help="skip the first-candidate gate for this run (only when the "
                   "source's framing recipe is already proven by a shipped short)")

    p = command("candidate", cmd_candidate, "Record an editor's candidate and free "
                "the editor (works from queued or fix too: no slot needed).")
    p.add_argument("--short-id", required=True)
    p.add_argument("--preview", required=True, help="preview path or URL")
    p.add_argument("--edl-version", required=True, type=int)
    p.add_argument("--note", help=f"candidate note, at most {NOTE_MAX_LINES} lines")
    p.add_argument("--note-file")
    p.add_argument("--handback", help="optional JSON: music, broll, required_credits, "
                   "rights_note, headline, kept_transcript, kept_source_ranges_s")
    p.add_argument("--renders", type=int, default=1, help="previews rendered for it")
    p.add_argument("--editor", help="optional ownership check")

    p = command("review", cmd_review, "Record the coordinator verdict on the latest "
                "candidate.")
    p.add_argument("--short-id", required=True)
    p.add_argument("--verdict", required=True, choices=("ship", "fix", "kill"))
    p.add_argument("--note", required=True,
                   help="ship: strongest moment; fix: timestamp + exact change; kill: why")
    p.add_argument("--checks", help="hook=yes,payoff=yes,targets=yes,attention=yes,"
                   "clean=yes (all yes required to ship)")
    p.add_argument("--next-action", help="kill only: what would revive it")

    p = command("export", cmd_export, "Record a downloaded final (sha256 computed).")
    p.add_argument("--short-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--job-id", required=True, type=int)
    p.add_argument("--edl-version", required=True, type=int)
    p.add_argument("--duration", type=float, help="seconds; probed with ffprobe if omitted")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--native-ending-s", type=float, default=5.0)
    p.add_argument("--verified-full", action="store_true",
                   help="this final was watched in full (branding, ending, audio)")

    p = command("exception", cmd_exception, "Close a short that will not ship.")
    p.add_argument("--short-id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--next-action", required=True)
    p.add_argument("--status", choices=("needs_user_review", "failed_technical", "killed"),
                   default="needs_user_review")

    p = command("status", cmd_status, "Show progress and the next actions.")
    p.add_argument("--json", action="store_true")

    p = command("finalize", cmd_finalize, "Write exports/manifest.json, "
                "publishing-manifest.json and PUBLISHING.md.")
    p.add_argument("--not-selected", help="JSON list of 'story (range): reason' strings")
    p.add_argument("--reindex-count", type=int, default=0)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = args.func(args)
    except (StateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.command == "status" and not args.json:
        print(format_status(value))
    else:
        print(json.dumps(value, indent=2 if args.command in ("status", "finalize")
                         else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
