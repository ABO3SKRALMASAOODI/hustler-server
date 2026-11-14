"""The delivered ledger: one file for all four workflows, so the exact same thing is never delivered twice.

The owner, 14 Nov 2026: "the same exact thing in the same exact way must not be replayed". A new version of an idea or
another angle, structure or story is welcome; the same podcast, or the same idea told the same way, is not. The same
narration or beats with only a new visual look is the same idea told the same way (a reskin, not a new way).

    /Users/masaoodi/Documents/Valmera/.valmera/workflow/delivered.jsonl     (VALMERA_DELIVERED_LEDGER overrides it)

One JSON object per line, appended under a file lock (rewritten only by rewrite(), under the same lock):
    {wf, delivered_at, folder, source_ids, idea: {title, subject, angle, style, beats, sources}, seeded, run_id?}

Only a delivery into one of the owner's delivery folders (Podcast Shorts, Story Films, Marketing Films, Long Films; the
same folder however its path is spelled) is recorded. A run that writes anywhere else is a test and is never recorded.

Two kinds of check, and only two:
  identity   a source id (yt:<YouTube id>, sha256:<hex of the source file>, url:<canonical URL>) compared exactly.
             That is identity, not meaning: the same bytes or the same video are the same source.
  meaning    whether a new idea is the same idea told the same way is a model's judgement (OpenAI gpt-6-sol, low
             effort, titles, angles and beats only). No word list and no pattern ever decides it.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import tempfile
import unicodedata
import urllib.parse

DEFAULT = "/Users/masaoodi/Documents/Valmera/.valmera/workflow/delivered.jsonl"
JUDGE_MODEL = "gpt-6-sol"
IDEA_WORKFLOWS = ("wf2", "wf3", "wf4")          # workflows that find their own ideas; wf1 is guarded by its source
RULE = ("No exact repeats: the shared delivered ledger (.valmera/workflow/delivered.jsonl) refuses a source already delivered "
        "and a model refuses an idea told the same way as a delivered one (the same narration or beats with only a new look "
        "is a repeat); a new version, angle, structure or story is welcome.")


V = "/Users/masaoodi/Documents/Valmera"
# the owner's delivery folders: a test run never writes inside one (VALMERA_DELIVERY_ROOTS adds more, os.pathsep apart)
DELIVERY_ROOTS = (f"{V}/Podcast Shorts", f"{V}/Story Films", f"{V}/Marketing Films", f"{V}/Long Films")


FIRMLINK = "/System/Volumes/Data"    # the Mac's data volume: /System/Volumes/Data/Users/... is /Users/... (a firmlink)
_FIRMLINK_FOLDED = unicodedata.normalize("NFD", FIRMLINK).casefold()


def delivery_roots():
    extra = [x for x in os.environ.get("VALMERA_DELIVERY_ROOTS", "").split(os.pathsep) if x]
    return [os.path.realpath(os.path.abspath(r)) for r in list(DELIVERY_ROOTS) + extra]


def _fold(p):
    """A path as the Mac's file system compares names: APFS is case-insensitive and normalisation-insensitive, and the
    data volume's firmlink (/System/Volumes/Data/Users/...) names the same folder as /Users/..."""
    f = unicodedata.normalize("NFD", p).casefold()
    while f.startswith("//"):
        f = f[1:]
    if f == _FIRMLINK_FOLDED:
        return "/"
    if f.startswith(_FIRMLINK_FOLDED + "/"):
        f = f[len(_FIRMLINK_FOLDED):]
    return f


def _ancestors(p):
    """The path itself and every folder above it, nearest first."""
    out = [p]
    while True:
        up = os.path.dirname(out[-1])
        if up == out[-1]:
            return out
        out.append(up)


def inside(p, roots):
    """True when the folder is one of `roots` or inside one, however either path is spelled. Every root, whether it
    exists yet or not, is compared with the path case-folded, normalised and with the data volume's firmlink prefix
    taken off (both sides); every existing folder on the path is also compared with each existing root by identity
    (os.path.samefile: the same folder under a symlink or a hard link)."""
    if not p:
        return False
    rp = os.path.realpath(os.path.abspath(p))
    roots = [os.path.realpath(os.path.abspath(r)) for r in roots]
    folded = _fold(rp)
    for r in roots:
        fr = _fold(r).rstrip("/")
        if folded == (fr or "/") or folded.startswith(fr + "/"):
            return True
    live = [r for r in roots if os.path.isdir(r)]
    for a in _ancestors(rp):
        if not os.path.exists(a):
            continue
        for r in live:
            try:
                if os.path.samefile(a, r):
                    return True
            except OSError:
                continue
    return False


def in_delivery_root(p):
    """True when the folder is one of the owner's delivery folders or inside one, however its path is spelled (inside())."""
    return inside(p, delivery_roots())


# ----------------------------------------------------------------- the drivers (story.py, market.py)

DRIVER_ENV = "VALMERA_DELIVERY_DRIVER"


def driver_token(wf):
    """The token a driver (story.py, market.py) hands the shell it starts: its workflow and its own process id. Only the
    shell started by that driver, with it, can leave the delivered ledger to the driver."""
    return f"{wf}:{os.getpid()}"


def driver_proven(wf, env=None):
    """Whether this process was started by the driver of `wf` with its token (the driver is this process's parent)."""
    tok = (env if env is not None else os.environ).get(DRIVER_ENV) or ""
    return bool(wf) and tok == f"{wf}:{os.getppid()}"


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)


def driver_cli(argv, prog, default_out):
    """A driver's command line: [--out <folder> | --test-out <folder>] [words ...], either form of each flag
    (--out x, --out=x), flags anywhere among the words. Any other argument that starts with "-" is refused, never read as
    the topic (a mistyped flag must not turn a test into a delivery); words after "--" are all topic.
    Returns dict(out_root, test, topic); raises SystemExit with one JSON line when the line is wrong."""
    ap = _Parser(prog=prog, allow_abbrev=False, add_help=False)
    ap.add_argument("--out")
    ap.add_argument("--test-out")
    ap.add_argument("words", nargs="*")
    try:
        a = ap.parse_intermixed_args(list(argv))
    except _ArgError as e:
        raise SystemExit(json.dumps(dict(failed=f"{prog}: {e}; the only flags are --out <folder> and --test-out <folder> "
                                                "(put topic words that start with a dash after --)")))
    if a.out is not None and a.test_out is not None:
        raise SystemExit(json.dumps(dict(failed="--test-out cannot be combined with --out: a test run never delivers")))
    for k in ("out", "test_out"):
        if getattr(a, k) is not None and not getattr(a, k).strip():
            raise SystemExit(json.dumps(dict(failed=f"--{k.replace('_', '-')} needs a folder")))
    test = os.path.abspath(a.test_out) if a.test_out is not None else None
    if test and in_delivery_root(test):
        raise SystemExit(json.dumps(dict(failed=f"--test-out {test} is inside a delivery folder; a test run never writes there")))
    out_root = test or (os.path.abspath(a.out) if a.out is not None else default_out)
    return dict(out_root=out_root, test=test, topic=" ".join(a.words).strip() or None)


class AlreadyDelivered(RuntimeError):
    """The exact source (or, for an idea, the same idea told the same way) was delivered before."""

    def __init__(self, msg, row=None, source_id=None):
        super().__init__(msg)
        self.row = row or {}
        self.source_id = source_id


def path():
    p = os.environ.get("VALMERA_DELIVERED_LEDGER")
    if not p and os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("a test must set VALMERA_DELIVERED_LEDGER (conftest.py does): tests never touch the real ledger")
    return p or DEFAULT


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- identity (exact)

def youtube_id(link):
    """The 11-character id of a YouTube link, read from the URL's own structure; None for anything else."""
    try:
        u = urllib.parse.urlparse(str(link or "").strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    cand = None
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        q = urllib.parse.parse_qs(u.query).get("v")
        if q:
            cand = q[0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
                cand = parts[1]
    if cand and len(cand) == 11 and all(c.isalnum() or c in "-_" for c in cand):
        return cand
    return None


def canonical_url(link):
    """One spelling of a URL: lower-case scheme and host, no www., no fragment, no tracking query, no trailing slash.
    A YouTube link becomes its video id."""
    vid = youtube_id(link)
    if vid:
        return f"yt:{vid}"
    try:
        u = urllib.parse.urlparse(str(link or "").strip())
    except ValueError:
        return None
    if not u.scheme or not u.netloc:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("si", "fbclid", "gclid", "feature")]
    p = u.path.rstrip("/") or ""
    return "url:" + urllib.parse.urlunparse(("https", host, p, "", urllib.parse.urlencode(sorted(q)), ""))


def source_id_of_link(link):
    c = canonical_url(link)
    return c if c else None


def sha256_file(p, chunk=8 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return "sha256:" + h.hexdigest()


# ----------------------------------------------------------------- the file

@contextlib.contextmanager
def _locked(p):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".lock", "a") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


def rows(wf=None, p=None):
    """Every delivered row (a half-written line is skipped), oldest first; of one workflow when `wf` is given."""
    p = p or path()
    out = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and (wf is None or r.get("wf") == wf):
                    out.append(r)
    except FileNotFoundError:
        pass
    return out


def find(source_ids, wf=None, p=None, _rows=None):
    """(row, id) of the first delivered row holding one of these exact source ids, else (None, None)."""
    want = {s for s in source_ids or [] if s}
    if not want:
        return None, None
    for r in (_rows if _rows is not None else rows(wf, p)):
        if wf is not None and r.get("wf") != wf:
            continue
        for s in r.get("source_ids") or []:
            if s in want:
                return r, s
    return None, None


def make_row(wf, folder, source_ids=(), idea=None, seeded=False, **extra):
    idea = idea or {}
    row = dict(wf=wf, delivered_at=extra.pop("delivered_at", None) or now(), folder=folder,
               source_ids=sorted({s for s in source_ids or [] if s}),
               idea=dict(title=idea.get("title"), subject=idea.get("subject"), angle=idea.get("angle"), style=idea.get("style"),
                         beats=[str(b)[:200] for b in idea.get("beats") or []][:40], sources=list(idea.get("sources") or [])[:20]),
               seeded=bool(seeded))
    row.update((k, v) for k, v in extra.items() if v is not None)
    return row


def records(folder):
    """Whether a folder written here is a delivery the ledger records: only inside one of the owner's delivery folders.
    Everything else (codex-tests, lanes, a workflow's test root, a scratch folder) is a test and is never recorded."""
    return bool(folder) and in_delivery_root(folder)


class NotJudged(AlreadyDelivered):
    """A remake of a delivered film reached a delivery folder without the repeat judgement."""


def deliver_once(row, commit=None, p=None, refuse=True):
    """The delivery of one folder. Into a delivery folder, under the lock: refuse when one of the row's exact source ids
    is already delivered by its workflow (or when the row is a remake that skipped the repeat judgement), else run
    `commit` (the rename that delivers the folder) and append the row. Two deliveries of one source cannot both pass.
    Anywhere else the run is a test: `commit` runs and nothing is recorded (row["recorded"] is False)."""
    if not records(row.get("folder")):
        if commit:
            commit()
        return dict(row, recorded=False)
    if row.get("remake_of") and not row.get("judged"):
        raise NotJudged(f"{row.get('folder')} re-makes delivered run {row['remake_of']} without the repeat judgement: a remake "
                        "goes to --test-out, or through the ideas stage's judgement like any idea", {}, None)
    return _record(row, commit, p, refuse)


def _record(row, commit=None, p=None, refuse=True):
    p = p or path()
    with _locked(p):
        if refuse:
            hit, sid = find(row.get("source_ids"), row.get("wf"), p)
            if hit:
                raise AlreadyDelivered(f"{sid} was delivered on {hit.get('delivered_at')} to {hit.get('folder')}", hit, sid)
        recorded = bool(row.get("run_id")) and any(r.get("run_id") == row["run_id"] and r.get("wf") == row.get("wf") for r in rows(None, p))
        if commit:
            commit()
        if recorded:                       # this run's delivery is already recorded (a run resumed after the record)
            return row
        line = json.dumps(row, ensure_ascii=False) + "\n"
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    return row


def row_from_film(film_dir, wf=None, folder=None, seeded=False):
    """A delivered row from a shell film folder's film.json (the idea, its title and style; the source's ids)."""
    rec = json.load(open(os.path.join(film_dir, "film.json")))
    sc = rec.get("script") or {}
    base = sc.get("idea_full") or (rec.get("idea") if isinstance(rec.get("idea"), dict) else None) or {}
    idea = idea_of(base)
    if not idea.get("title") and isinstance(rec.get("idea"), str):
        idea["title"] = rec["idea"]
    idea["title"] = idea.get("title") or sc.get("title")
    idea["style"] = (rec.get("style") or {}).get("id") or idea.get("style")
    ids = []
    src = rec.get("source") or {}
    for k in ("url", "link", "origin_url", "webpage_url"):
        v = src.get(k) if isinstance(src, dict) else None
        if isinstance(v, str) and canonical_url(v):
            ids.append(canonical_url(v))
    return make_row(wf or rec.get("workflow"), folder or film_dir, ids, idea, seeded=seeded, run_id=rec.get("run_id"),
                    film_title=sc.get("title"), delivered_at=rec.get("delivered") if seeded else None,
                    remake_of=sc.get("remake_of") or (src.get("remake_of") if isinstance(src, dict) else None),
                    judged=True if isinstance(rec.get("no_repeat"), dict) else None)


def append(row, p=None, refuse=True):
    """Record a row as it is, wherever its folder is (the seed of past deliveries; tests)."""
    return _record(row, None, p, refuse)


def rewrite(fn, p=None, backup=None):
    """Rewrite the ledger under its lock: `fn(rows)` returns the rows to keep (edited or not). A line that does not
    parse is kept as it was. The new file replaces the old in one step; `backup` gets a copy of the old one first."""
    p = p or path()
    with _locked(p):
        raw = open(p).read().splitlines() if os.path.exists(p) else []
        parsed, bad = [], []
        for line in raw:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad.append(line)
                continue
            (parsed if isinstance(r, dict) else bad).append(r if isinstance(r, dict) else line)
        if backup:
            if os.path.exists(backup):
                raise RuntimeError(f"{backup} exists: a backup is never overwritten")
            with open(backup, "w") as f:
                f.write("\n".join(raw) + ("\n" if raw else ""))
        kept = fn([dict(r) for r in parsed])
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", prefix=".delivered.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            for line in bad:
                f.write(line + "\n")
            for r in kept:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    return kept


# ----------------------------------------------------------------- ideas (a model's judgement)

def idea_of(d):
    """The compact form a workflow's idea is compared in: title, subject, angle, style and its beats, one line each.
    Field picking only: whichever of these keys the workflow's idea carries."""
    d = d or {}
    beats = d.get("beats")
    if not beats:
        beats = [d.get(k) for k in ("hook", "story", "turn", "payoff", "pain", "claim", "first_second", "promise") if d.get(k)]
        beats += [c.get("title") for c in d.get("chapters") or [] if isinstance(c, dict) and c.get("title")]
    return dict(title=d.get("title"), subject=d.get("subject") or d.get("pain") or ", ".join(
                    n.get("name") for n in (d.get("named") or [])[:3] if isinstance(n, dict) and n.get("name")) or None,
                angle=d.get("angle") or d.get("why_style") or d.get("claim") or d.get("hook"), style=d.get("style"),
                beats=[str(b)[:200] for b in beats if b][:12], sources=list(d.get("sources") or [])[:8])


TITLE_ONLY = "title and style only: judge on the subject and the story its title names"


def _brief(r):
    i = r.get("idea") or {}
    b = {k: v for k, v in dict(title=i.get("title"), subject=i.get("subject"), angle=i.get("angle"), style=i.get("style"),
                               beats=(i.get("beats") or [])[:8]).items() if v}
    if not b.get("angle") and not b.get("beats"):
        b["detail"] = TITLE_ONLY            # what was told is unknown: the judgement may never read that as "allowed"
    return b


def delivered_ideas(wf, p=None, n=80):
    """The workflow's delivered ideas, oldest first (a stable prefix for the model's cache)."""
    return [_brief(r) for r in rows(wf, p) if (r.get("idea") or {}).get("title")][-n:]


def prompt_block(wf, p=None):
    """The section an ideas prompt carries: what this workflow already delivered, and the rule."""
    got = delivered_ideas(wf, p)
    if not got:
        return ""
    return ("# Already delivered by this workflow (title, angle, beats)\n" + json.dumps(got, ensure_ascii=False) + "\n"
            "Never choose the exact same idea told the same way as any of these. The same story told through the same beats "
            "or the same narration is a repeat even under a new title or a new visual look. A new angle, structure or story "
            "about the same subject, or a new version of it, is not a repeat and is welcome: the same events told in a new "
            "structure (a dated timeline, a list, a countdown, another narrator's frame) with new narration and beats are a "
            "new way.\n\n")


JUDGE_SYSTEM = (
    "You guard a video workflow against delivering the exact same film twice. You are given the ideas this workflow "
    "already delivered and the candidate ideas of a new run. For each candidate, decide one thing: is it the exact same "
    "idea told the same way as one delivered idea?\n"
    "Judge how the story is told, not what happened. The events of a true story belong to every telling of it: the same "
    "subject and the same events are never by themselves a repeat.\n"
    "- A repeat (same: true): the candidate tells a delivered idea the same way: the same structure carrying the same "
    "narration and beats (the same hook, the same beats in the same order, the same turn and payoff), however it is "
    "re-titled or re-worded.\n"
    "- A reskin is a repeat: the same narration or script (the same beats and words) with only a new visual look (another "
    "style, grade, footage, pace, music or captions) is the same idea told the same way; a reskin is not a new way of "
    "telling it.\n"
    "- A new way is NOT a repeat, even on exactly the same events: a different structure (a dated timeline, a list, a "
    "countdown, one moment told in depth, another narrator's or another person's frame), a different angle, or a "
    "different story about the same subject (another moment, another turn, another person's side). Re-ordering, "
    "re-framing or re-structuring the same events into a new form is a new way, never \"only repackaging\" them. A new "
    "structure is a repeat only when its narration and beats are still the delivered ones: the same lines under a new "
    "heading.\n"
    "- Some delivered ideas carry only a title and a style (\"detail\" says so). Judge them on what the title names: a "
    "candidate about the same subject that tells the same story the title names, in the plain way the title names it, is a "
    "repeat. Missing detail never counts in the candidate's favour; it is not a repeat only when its angle, story or "
    "structure is plainly different from what the title names.\n"
    "Return JSON only: {\"verdicts\": [{\"id\": the candidate's id, \"same\": true|false, \"of\": the delivered title it "
    "repeats or null, \"why\": one short sentence}]}.")


def _shell_llm():
    """This folder's llm.py under a name of its own (the worker has an `llm` module too)."""
    import importlib.util
    import sys
    name = "valmera_shell_llm"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return sys.modules[name]


def _json_answer(text):
    """The model's JSON answer: the outermost object in its text (machine syntax only)."""
    s = (text or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j <= i:
        raise ValueError("no JSON in the repeat judgement's answer")
    return json.loads(s[i:j + 1])


def judge(candidates, delivered, ask=None):
    """[{id, same, of, why}] for the candidates, from one small model call. `ask(system, content)` returns
    (text, usage); the default is the OpenAI rung on gpt-6-sol."""
    if not candidates or not delivered:
        return [dict(id=c["id"], same=False, of=None, why="nothing delivered to compare") for c in candidates], None
    if ask is None:
        def ask(system, content):
            return _shell_llm().ask_openai(system, content, model=JUDGE_MODEL, effort="low", max_tokens=4000)
    content = ("# Delivered\n" + json.dumps(delivered, ensure_ascii=False) + "\n\n# Candidates\n"
               + json.dumps(candidates, ensure_ascii=False))
    text, use = ask(JUDGE_SYSTEM, content)
    got = _json_answer(text)
    got = got.get("verdicts") if isinstance(got, dict) else got
    by = {str(v.get("id")): v for v in got or [] if isinstance(v, dict)}
    out = []
    for c in candidates:
        v = by.get(str(c["id"]))
        if v is None:
            raise RuntimeError(f"the repeat judgement gave no verdict for candidate {c['id']}")
        out.append(dict(id=c["id"], same=v.get("same") is True, of=v.get("of"), why=str(v.get("why") or "")[:300]))
    return out, use


def guard_ideas(wf, ideas_out, ask=None, log=print, p=None):
    """The ideas stage's output with every exact repeat of a delivered idea taken out (the next idea then leads).
    Raises AlreadyDelivered when every idea repeats one."""
    ideas = list((ideas_out or {}).get("ideas") or [])
    delivered = delivered_ideas(wf, p)
    if not ideas or not delivered:
        ideas_out["no_repeat"] = dict(checked=0, delivered=len(delivered), repeats=[])
        return ideas_out
    cands = []
    for k, i in enumerate(ideas):
        c = {kk: vv for kk, vv in idea_of(i).items() if vv and kk != "sources"}
        c["id"] = str(i.get("id") or f"c{k + 1}")
        cands.append(c)
    verdicts, use = judge(cands, delivered, ask=ask)
    same = {v["id"] for v in verdicts if v["same"]}
    keep = [i for i, c in zip(ideas, cands) if c["id"] not in same]
    reps = [dict(title=c.get("title"), of=v["of"], why=v["why"]) for c, v in zip(cands, verdicts) if v["same"]]
    for r in reps:
        log(f"no-repeat: '{r['title']}' is the same idea told the same way as delivered '{r['of']}': {r['why']}")
    ideas_out["no_repeat"] = dict(checked=len(cands), delivered=len(delivered), repeats=reps, model=JUDGE_MODEL)
    if use:
        ideas_out.setdefault("spend", []).append(dict(stage="ideas", what="no-repeat judgement", **use))
    if not keep:
        raise AlreadyDelivered("every idea found repeats a delivered one exactly: " + json.dumps(reps, ensure_ascii=False)[:600])
    ideas_out["ideas"] = keep
    return ideas_out
