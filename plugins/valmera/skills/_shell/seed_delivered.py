#!/usr/bin/env python3
"""Seed the delivered ledger (delivered.py) once from every past delivery that can be read. Read only on the folders.

    $PY seed_delivered.py            seeds when the ledger holds no seeded row yet; prints the counts and the folders
                                     whose source could not be determined
    $PY seed_delivered.py --dry      prints what it would seed
    $PY seed_delivered.py --enrich [--dry]
                                     gives every film row that holds only a title and style (its folder moved) the idea,
                                     angle and beats of a copy of the same film: a film.json of the same run, or of a
                                     remake of it (script.remake_of), found under ENRICH_ROOTS; rewritten under the lock

Sources, by identity only (a labelled field, never a reading of meaning):
  podcast folders   manifest.json `source` / `source_url`, publishing-manifest.json `source_url`, shorts.json
                    `source.link`, else the first link after a "Source:" label in a README or CREDITS
  film folders      film.json (the shell's record): the idea, its title and style
  shell ledgers     the rows of .valmera/workflow/{,stories/,marketing/,longform/}ledger.json for wf2, wf3 and wf4
                    (a film whose folder has moved keeps its idea title)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delivered as DL  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera"
PODCAST_ROOTS = [f"{V}/Podcast Shorts"]
OLDER = [f"{V}/Elon Musk - 18 Finished Shorts", f"{V}/Elon Musk - Bold Headlines - Revised Shorts",
         f"{V}/Elon Musk Podcast Shorts — 18 Final Videos", f"{V}/Jensen Huang - Dwarkesh Patel - Final Shorts",
         f"{V}/Showcase Shorts 2026-10-10", f"{V}/Preserved Shorts - Storage Cleanup 2026-10-19/Alex Karp Shorts"]
EXPORTS = f"{V}/exports"
FILM_ROOTS = {"wf3": [f"{V}/Story Films"], "wf2": [f"{V}/Marketing Films"], "wf4": [f"{V}/Long Films"]}
SHELL_LEDGERS = [f"{V}/.valmera/workflow/ledger.json", f"{V}/.valmera/workflow/stories/ledger.json",
                 f"{V}/.valmera/workflow/marketing/ledger.json", f"{V}/.valmera/workflow/longform/ledger.json"]


def jload(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def ids_in_line(line):
    out = []
    for tok in line.replace("<", " ").replace(">", " ").replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ").split():
        tok = tok.strip("'\"{},.;`")
        if tok.startswith("http"):
            sid = DL.source_id_of_link(tok)
            if sid:
                out.append(sid)
    return out


def podcast_source(folder):
    """(source ids, how, title, beats) of a podcast delivery folder, from its own labelled records."""
    ids, how, beats, title = [], None, [], None
    for name in ("manifest.json", "publishing-manifest.json", "shorts.json"):
        m = jload(os.path.join(folder, name))
        if not isinstance(m, dict):
            continue
        src = m.get("source")
        cands = [m.get("source_url")] + ([src] if isinstance(src, str) else [])
        if isinstance(src, dict):
            cands += [src.get("link"), src.get("url"), src.get("webpage_url")]
            title = title or src.get("title")
            if src.get("sha256"):
                ids.append("sha256:" + str(src["sha256"]).split(":")[-1])
        for c in cands:
            sid = DL.source_id_of_link(c) if isinstance(c, str) else None
            if sid:
                ids.append(sid)
                how = how or f"{name} source"
        items = m.get("items") or m.get("shorts") or []
        if not beats and isinstance(items, list):
            beats = [str(i.get("title")) for i in items if isinstance(i, dict) and i.get("title")]
    if not ids:
        for name in ("README.md", "READ-ME.md", "README.txt", "READ-ME.txt", "CREDITS.md", "START HERE.md"):
            p = os.path.join(folder, name)
            if not os.path.isfile(p):
                continue
            for line in open(p, errors="replace"):
                s = line.strip().lstrip("#*-> ").strip()
                at = s.find("Source:")                   # the labelled field, wherever it sits on its line
                if at >= 0 or s.lower().startswith("[full interview]"):
                    got = ids_in_line(s[max(at, 0):])
                    if got:
                        ids.append(got[0])
                        how = f"{name} source line"
                        break
            if ids:
                break
    return sorted(set(ids)), how, title, beats


def podcast_rows(folders, origin):
    rows, unknown = [], []
    for f in folders:
        if not os.path.isdir(f):
            continue
        ids, how, title, beats = podcast_source(f)
        if not ids:
            unknown.append(f)
            continue
        name = os.path.basename(f)
        rows.append(DL.make_row("wf1", f, ids, dict(title=title or name, subject=name.split(" - ")[0], style="podcast shorts",
                                                    beats=beats[:40], sources=[]), seeded=True,
                                seed=dict(origin=origin, how=how), delivered_at=mtime(f)))
    return rows, unknown


def mtime(p):
    import datetime as dt
    return dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def film_rows():
    rows = []
    for wf, roots in FILM_ROOTS.items():
        for root in roots:
            for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                d = os.path.join(root, n)
                if os.path.isfile(os.path.join(d, "film.json")):
                    r = DL.row_from_film(d, wf, seeded=True)
                    r["seed"] = dict(origin="delivery folder", how="film.json")
                    rows.append(r)
    return rows


def shell_ledger_rows(have_run_ids):
    rows = []
    for p in SHELL_LEDGERS:
        d = jload(p) or {}
        for wf, items in d.items():
            if wf not in DL.IDEA_WORKFLOWS:
                continue
            for it in items:
                rid = it.get("run_id")
                if not it.get("idea") or (rid and rid in have_run_ids):
                    continue
                have_run_ids.add(rid)
                folder = it.get("delivered")
                if folder and os.path.isfile(os.path.join(folder, "film.json")):
                    r = DL.row_from_film(folder, wf, seeded=True)
                else:
                    r = DL.make_row(wf, folder, [], dict(title=it["idea"], style=it.get("style")), seeded=True,
                                    run_id=rid, delivered_at=it.get("at"))
                r["seed"] = dict(origin=p, how="film.json" if os.path.isfile(os.path.join(folder or "", "film.json")) else "ledger row (folder moved)")
                rows.append(r)
    return rows


ENRICH_ROOTS = [f"{V}/.valmera/week-1112/codex-tests", f"{V}/.valmera/week-1112/lanes", f"{V}/.valmera/workflow",
                f"{V}/.valmera/workflow/stories/outbox", f"{V}/.valmera/workflow/marketing/outbox",
                f"{V}/.valmera/workflow/longform/outbox"] + [r for rs in FILM_ROOTS.values() for r in rs]
SKIP_DIRS = {"music_cache", "node_modules", ".git", "supply", "footage", "frames", "cache", "__pycache__"}


def thin(r):
    """A film row that knows only its title and style: no angle and no beats."""
    i = r.get("idea") or {}
    return r.get("wf") in DL.IDEA_WORKFLOWS and not i.get("angle") and not i.get("beats")


def film_copies(roots, max_depth=6):
    """{run_id: film.json path} of every film.json under the roots, by its own run_id and by the run it re-makes."""
    own, remakes = {}, {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        base = root.rstrip(os.sep).count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base >= max_depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            if "film.json" not in files:
                continue
            p = os.path.join(d, "film.json")
            rec = jload(p)
            if not isinstance(rec, dict):
                continue
            if rec.get("run_id"):
                own.setdefault(rec["run_id"], p)
            rm = (rec.get("script") or {}).get("remake_of")
            if rm:
                remakes.setdefault(rm, p)
    return own, remakes


def enrich_row(r, copy):
    """The row with the idea of its copy (title kept when the copy has none); identity fields stay as they were."""
    got = DL.row_from_film(os.path.dirname(copy), r.get("wf"), folder=r.get("folder"))
    idea = dict(r.get("idea") or {})
    for k, v in (got.get("idea") or {}).items():
        if v and not idea.get(k):
            idea[k] = v
    out = dict(r, idea=idea)
    out["seed"] = dict(r.get("seed") or {}, enriched_from=copy, enriched_at=DL.now())
    return out


def enrich(dry=False, roots=None, p=None):
    """Enrich the thin film rows in place (under the ledger's lock); returns (enriched, still thin)."""
    own, remakes = film_copies(roots or ENRICH_ROOTS)
    done, left = [], []

    def fn(rows):
        out = []
        for r in rows:
            if thin(r) and r.get("run_id"):
                copy = own.get(r["run_id"]) or remakes.get(r["run_id"])
                if copy:
                    r = enrich_row(r, copy)
                    done.append(dict(run_id=r["run_id"], title=r["idea"].get("title"), from_=copy))
                else:
                    left.append(dict(run_id=r.get("run_id"), title=(r.get("idea") or {}).get("title")))
            elif thin(r):
                left.append(dict(run_id=None, title=(r.get("idea") or {}).get("title")))
            out.append(r)
        return out
    if dry:
        fn(DL.rows(None, p))
    else:
        DL.rewrite(fn, p)
    return done, left


def main():
    dry = "--dry" in sys.argv
    if "--enrich" in sys.argv:
        done, left = enrich(dry)
        print(json.dumps(dict(path=DL.path(), dry=dry, enriched=done, still_title_only=left), ensure_ascii=False, indent=1))
        return 0
    if any(r.get("seeded") for r in DL.rows()) and not dry:
        print(json.dumps(dict(skipped="the ledger is already seeded", path=DL.path())))
        return 0
    pod = sorted(os.path.join(r, n) for r in PODCAST_ROOTS for n in os.listdir(r) if os.path.isdir(os.path.join(r, n)))
    older = OLDER + sorted(os.path.join(EXPORTS, n) for n in os.listdir(EXPORTS) if os.path.isdir(os.path.join(EXPORTS, n)))
    p_rows, p_unknown = podcast_rows(pod, "Podcast Shorts")
    o_rows, o_unknown = podcast_rows(older, "older shorts folder")
    f_rows = film_rows()
    s_rows = shell_ledger_rows({r.get("run_id") for r in f_rows if r.get("run_id")})
    rows = p_rows + o_rows + f_rows + s_rows
    if not dry:
        for r in rows:
            DL.append(r, refuse=False)          # past deliveries are recorded as they were, repeats included
    by_src = {}
    for r in p_rows + o_rows:
        for s in r["source_ids"]:
            if s.startswith("yt:"):
                by_src.setdefault(s, []).append(os.path.basename(r["folder"]))
    rep = dict(path=DL.path(), dry=dry, seeded=len(rows), podcast_folders=len(p_rows), older_folders=len(o_rows),
               film_folders=len(f_rows), shell_ledger_rows=len(s_rows),
               by_workflow={wf: sum(1 for r in rows if r["wf"] == wf) for wf in ("wf1", "wf2", "wf3", "wf4")},
               distinct_podcast_sources=len(by_src), delivered_more_than_once={k: v for k, v in by_src.items() if len(v) > 1},
               source_unknown=p_unknown + o_unknown)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
