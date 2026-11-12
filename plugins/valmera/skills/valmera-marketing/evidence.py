"""What the marketing workflow knows before it has an idea.

Two kinds of knowledge, both read from disk, none invented:

  ledger()       what Valmera really delivered: every finished podcast-shorts run with its source,
                 the count of exported shorts and where they are. A claim in a film is one of these
                 rows or it is not made.
  references()   what editors respond to: the captions, lengths and reactions of the owner's
                 reference films in "editing tutorials and app marketing".
  capabilities() what the product does in one request, each with the place its receipt comes from.
"""
import json
import os

V = "/Users/masaoodi/Documents/Valmera"
RUNS = f"{V}/.tmp/valmera-podcast-shorts"
REFS = f"{V}/reference-library/2026-11-05/02-editing-tutorials-and-app-marketing"


def _read(path, n=1200):
    try:
        return open(path, encoding="utf-8", errors="replace").read()[:n]
    except OSError:
        return ""


def ledger(root=RUNS):
    """Finished runs whose finals and source are still on disk, largest delivery first."""
    import supply as S
    rows = []
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, n)
        if not os.path.exists(os.path.join(d, "run.json")) or not os.path.isdir(os.path.join(d, "source")):
            continue
        try:
            r = S.run_record(d)
        except (OSError, ValueError, KeyError):
            continue
        if r["stage"] != "complete" or not r["delivered"] or r["delivered"] != r["declared"] or not r["source"]:
            continue
        looks = {}
        for s in r["shorts"]:
            looks[s["look"] or "?"] = looks.get(s["look"] or "?", 0) + 1
        single = sum(1 for s in r["shorts"] if len(s["keeps"]) == 1)
        rows.append(dict(run=d, run_id=r["run_id"], delivered=r["delivered"], source_s=r["source_s"],
                         source_clock=S.clock(r["source_s"]) if r["source_s"] else None, source_url=r["source_url"],
                         identity=_read(os.path.join(d, "source", "identity.md")) or _read(os.path.join(d, "SOURCE.md")),
                         folder=r["delivered_to"], looks=looks, shorts_on_one_take=single, music=r["music"],
                         started=r["started"], finished=r["finished"]))
    rows.sort(key=lambda x: -x["delivered"])
    return rows


def references(root=REFS, n=40):
    """The reference films' own words: what they promise, how they ask, how people answered."""
    out = []
    for folder in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".info.json"):
                continue
            try:
                j = json.load(open(os.path.join(d, f)))
            except ValueError:
                continue
            out.append(dict(id=f.split("__")[0], kind=folder, seconds=j.get("duration"), likes=j.get("like_count"),
                            comments=j.get("comment_count"), caption=(j.get("description") or j.get("title") or "")[:320]))
    out.sort(key=lambda x: -(x.get("comments") or 0))
    return out[:n]


def capabilities():
    """True product behaviour a film may show, and where the proof is read from. Add a row only with its receipt."""
    return [
        dict(id="podcast-to-shorts", what="One request to an AI app with the Valmera plugin turns a whole podcast into finished vertical shorts: "
             "stories chosen, cut on word edges, framed, captioned, titled, designed in several Looks, exported.",
             receipt="a finished run in the ledger: run.json delivered_files and the exported .mp4 files"),
        dict(id="looks", what="The shorts of one run are not one template: each takes one of several designed Looks.",
             receipt="the looks count of a ledger row"),
        dict(id="unaltered-export", what="Every short is an export of a Valmera project with the product's own end card and mark.",
             receipt="the exported file itself, shown unaltered, with its sha256"),
    ]
