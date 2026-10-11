"""Refbench case specs (INTERFACES §8.1): load and validate.

One YAML file per case at ``cases/<track>/<id>.yaml``. Validation is pure
Python over the parsed dict (PyYAML only reads the file), so the schema is
checked in CI before any media exists. ``validate(case) -> [errors]``.

Extensions used by refbench beyond the pinned keys (all optional, flagged
for the round lead in the 0D report):
  - ``render.use``: score an existing render instead of rendering (the
    Wave 0 baseline board scores the v5 showcase files);
  - ``edl.base``: ``vfl_default`` (the window as one keep, framed to
    ``aspect``) or ``baseline:<name>`` (a stored EDL under
    ``<fixtures>/baselines/``, with ``edl.window_ref``, ``edl.index`` and
    ``edl.name`` for the baseline board);
  - ``edl.window``: which ``footage`` entry the engine renders (default 0);
  - ``edl.keep`` (SOURCE seconds inside the window) and ``edl.focus``
    ([x, y] crop focus) for ``vfl_default``;
  - metric keys ``item`` (the EDL item a device metric measures),
    ``target`` ({word}, {cut} or {t}) and ``event`` (``ink_onset`` |
    ``flash_peak``) for ``land_err_f``, ``role`` for ``legibility``,
    ``ghosted`` for ``glyph_visible``;
  - reference key ``board_t``: the reference second the 2-up still shows.
"""

import os
import re

from . import CASES_DIR

ID_RE = re.compile(r"^(AT-[0-4][A-F]-\d+[a-z]?|AT-[0-4][A-F]-perf|"
                   r"W[0-4]-S\d+|BASE-\d+)$")
TRACK_RE = re.compile(r"^[0-4][A-F]$|^W[0-4]$")
ASPECTS = ("9:16", "16:9", "4:5", "1:1", "4:3", "source")
MODES = ("engine", "agent")
ALLOW = ("music", "sfx", "zoom", "flash_optin")
MUSIC_FIXTURES = ("HM1", "HM2", "HM3")
LENSES = ("motion", "craft", "owner_rules")
VARIANTS = ("preview", "final")
OPS = ("<=", ">=", "==", "<", ">")
REQUIRED = ("id", "track", "title", "reference", "footage", "aspect", "mode",
            "render", "allow", "metrics", "beyond", "board", "judges")


def load(path):
    import yaml
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a case is a mapping")
    data.setdefault("_path", path)
    return data


def all_case_paths(root=CASES_DIR):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith((".yaml", ".yml")):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def find(case_id, root=CASES_DIR):
    for p in all_case_paths(root):
        if os.path.splitext(os.path.basename(p))[0] == case_id:
            return p
    return None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(case, *, vfl=None, metrics=None):
    """Return a list of human-readable errors (empty when valid).

    ``vfl``: the parsed vfl.json (footage windows are checked when given);
    ``metrics``: the metric registry (default qa_metrics.METRICS)."""
    errs = []
    if metrics is None:
        import qa_metrics
        metrics = qa_metrics.METRICS
    for k in REQUIRED:
        if k not in case:
            errs.append(f"missing {k}")
    cid = str(case.get("id", ""))
    if not ID_RE.match(cid):
        errs.append(f"id {cid!r} does not match the case id pattern")
    track = str(case.get("track", ""))
    if not TRACK_RE.match(track):
        errs.append(f"track {track!r} is not a track (0A..4F) or wave (W0..W4)")
    elif cid.startswith("AT-") and cid[3:5] != track:
        errs.append(f"id {cid} belongs to track {cid[3:5]}, not {track}")
    path = case.get("_path")
    if path and os.path.splitext(os.path.basename(path))[0] != cid:
        errs.append(f"file name must be {cid}.yaml")
    if not isinstance(case.get("title"), str) or not case.get("title"):
        errs.append("title must be a non-empty string")
    for flag in ("local_only", "interim"):
        if flag in case and not isinstance(case[flag], bool):
            errs.append(f"{flag} must be true/false")
    refs = case.get("reference")
    if not isinstance(refs, list):
        errs.append("reference must be a list")
    else:
        for r in refs:
            if not isinstance(r, dict) or "ref" not in r:
                errs.append(f"reference entry {r!r} needs ref")
                continue
            try:
                n = int(str(r["ref"]))
            except ValueError:
                n = 0
            if not 1 <= n <= 41:
                errs.append(f"reference {r['ref']!r} is not one of 01..41")
            t = r.get("t")
            if t is not None and not (isinstance(t, list) and len(t) == 2
                                      and all(_num(x) for x in t)
                                      and t[0] <= t[1]):
                errs.append(f"reference {r['ref']} t must be [a, b]")
    foot = case.get("footage")
    if not isinstance(foot, list):
        errs.append("footage must be a list")
        foot = []
    for f in foot:
        if not isinstance(f, dict) or "vfl" not in f:
            errs.append(f"footage entry {f!r} needs vfl")
            continue
        if ("window" in f) == ("t" in f):
            errs.append(f"footage {f['vfl']}: give window or t, not both")
        if vfl is not None:
            src = vfl.get(f["vfl"])
            if src is None:
                errs.append(f"footage {f['vfl']} is not in vfl.json")
            elif "window" in f and f["window"] not in src.get("windows", {}):
                errs.append(f"footage {f['vfl']}/{f['window']} is not a "
                            "vfl.json window")
    if foot and case.get("local_only") is False:
        errs.append("a case with VFL footage must be local_only: true")
    music = case.get("music")
    if music is not None:
        if not isinstance(music, dict) or \
                music.get("fixture") not in MUSIC_FIXTURES:
            errs.append("music must be null or {fixture: HM1|HM2|HM3} "
                        "(owner-supplied only)")
        if "music" not in (case.get("allow") or []):
            errs.append("a case with music must list music in allow")
    if case.get("aspect") not in ASPECTS:
        errs.append(f"aspect must be one of {ASPECTS}")
    mode = case.get("mode")
    if mode not in MODES:
        errs.append(f"mode must be one of {MODES}")
    elif mode == "engine":
        edl = case.get("edl")
        if not isinstance(edl, dict) or "base" not in edl:
            errs.append("engine mode needs edl: {base, ops}")
        elif not isinstance(edl.get("ops", []), list):
            errs.append("edl.ops must be a list")
        if case.get("chat"):
            errs.append("engine mode takes no chat")
    elif mode == "agent":
        if not isinstance(case.get("chat"), str) or not case.get("chat"):
            errs.append("agent mode needs chat: the user's sentence")
    render = case.get("render")
    if not isinstance(render, dict) or render.get("variant") not in VARIANTS:
        errs.append(f"render.variant must be one of {VARIANTS}")
    allow = case.get("allow")
    if not isinstance(allow, list) or any(a not in ALLOW for a in allow):
        errs.append(f"allow must be a list drawn from {ALLOW}")
    ms = case.get("metrics")
    if not isinstance(ms, list) or not ms:
        errs.append("metrics must be a non-empty list")
        ms = []
    for m in ms:
        if not isinstance(m, dict) or "name" not in m:
            errs.append(f"metric {m!r} needs a name")
            continue
        if m["name"] not in metrics:
            errs.append(f"metric {m['name']!r} is not in qa_metrics.METRICS")
        if "op" in m or "value" in m:
            if m.get("op") not in OPS or not _num(m.get("value")):
                errs.append(f"metric {m['name']}: op must be one of {OPS} "
                            "with a numeric value")
    beyond = case.get("beyond")
    if not isinstance(beyond, str) or not beyond.strip():
        errs.append("beyond is required and must say what the reference "
                    "did not do")
    board = case.get("board")
    if not isinstance(board, dict):
        errs.append("board must be {stills: [...], clip: [a, b]}")
    else:
        if not isinstance(board.get("stills", []), list):
            errs.append("board.stills must be a list of program seconds")
        clip = board.get("clip")
        if clip is not None and not (isinstance(clip, list) and len(clip) == 2
                                     and clip[0] < clip[1]):
            errs.append("board.clip must be [a, b]")
    judges = case.get("judges")
    if not isinstance(judges, dict) or not set(
            judges.get("lenses", [])) <= set(LENSES) or \
            not judges.get("lenses"):
        errs.append(f"judges.lenses must be drawn from {LENSES}")
    elif judges.get("pass") not in ("2of3_no_major", "all_no_major",
                                     "owner"):
        errs.append("judges.pass must be 2of3_no_major, all_no_major or "
                    "owner")
    return errs
