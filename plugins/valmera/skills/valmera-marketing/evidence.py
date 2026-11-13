"""What the marketing workflow knows before it has an idea.

Read from disk, none of it invented:

  rows()         what the product really did, as rows a film may be about. A row is one or more studio recordings
                 (studio.py): a clip uploaded to the product, a sentence typed, the export that came back, and the
                 project's EDL at every version. A claim in a film is one of these rows or it is not made.
  references()   what editors respond to: the captions, lengths and reactions of the owner's reference films in
                 "editing tutorials and app marketing".
  capabilities() what the product does with one sentence, each with the place its receipt comes from.
"""
import json
import os

V = "/Users/masaoodi/Documents/Valmera"
REFS = f"{V}/reference-library/2026-11-05/02-editing-tutorials-and-app-marketing"


def _read(path, n=700):
    try:
        return open(path, encoding="utf-8", errors="replace").read()[:n]
    except OSError:
        return ""


def clip_of(r):
    """Where a recording's clip came from, in the source's own words when its identity file is beside it."""
    src = (r.get("source") or {})
    ident = _read(os.path.join(os.path.dirname(src.get("file") or ""), "identity.md"))
    return dict(source=ident or os.path.basename(src.get("file") or ""), from_s=src.get("t0"), to_s=src.get("t1"), length_s=r["upload"]["dur"],
                shape=r["facts"]["upload_ratio"])


def rows():
    """Every finished recording as a row, and every upload that three or more sentences were typed on as one more."""
    import film as F
    import studio as ST
    import supply as S
    recs = []
    for x in ST.ledger():
        try:
            recs.append(S.studio_record(x["sid"]))
        except (SystemExit, OSError, ValueError, KeyError):
            continue
    out, by_clip = [], {}
    for r in recs:
        tall = r["export"]["h"] > r["export"]["w"]
        base = dict(records=[r["sid"]], clip=clip_of(r), sentence=r["sentence"], product_replied=(r.get("reply") or "")[:500], result=r["facts"])
        if r["before"]:
            ea, eb = json.load(open(r["before"]["edl"])), json.load(open(r["edl"]))
            tla, da = F.program(ea)
            tlb, db = F.program(eb)
            removed = sum(e - s for s, e, ia, ib in F.edl_pieces(tla.segs, tlb.segs) if ia and not ib)
            had = {o["id"]: o for o in F.pictures_of(ea)}
            now = F.pictures_of(eb)
            change = dict(before_version=r["before"]["edl_version"], after_version=r["export"]["edl_version"], before_length=F.clock(da), after_length=F.clock(db),
                          seconds_removed=round(removed, 1), pictures_added=sum(1 for o in now if o["id"] not in had),
                          pictures_moved_with_the_cut=sum(1 for o in now if o["id"] in had and abs(o["start"] - had[o["id"]]["start"]) > 0.05))
            out.append(dict(row_id=f"edit:{r['sid']}", kind="one project, before and after a typed sentence", fits=["edits-itself"], change=change, **base))
        elif tall and r.get("phase") == "before_first_result":
            out.append(dict(row_id=f"sentence:{r['sid']}", kind="one clip, one typed sentence, one finished vertical film", fits=["reading-wipe"], **base))
        if tall and not r["before"]:
            by_clip.setdefault(r["upload"]["sha256"], []).append(r)
    for sha, group in by_clip.items():
        if len({r["sentence"] for r in group}) >= 3:
            out.append(dict(row_id=f"clip:{sha[:8]}", kind="one clip, three or more typed sentences, a different film from each", fits=["one-clip-three-films"],
                            records=[r["sid"] for r in group], clip=clip_of(group[0]),
                            sentences=[dict(record=r["sid"], sentence=r["sentence"], product_replied=(r.get("reply") or "")[:300], result=r["facts"]) for r in group]))
    return out


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
        dict(id="sentence-to-film", what="A clip is uploaded to the Valmera Studio and one sentence is typed. The product reframes it, trims it, captions it, "
             "and lays pictures on the words when asked, and the result is exported as an MP4.",
             receipt="a studio recording: the journey (what was typed, what was replied), the export file and its sha256"),
        dict(id="same-clip-other-sentence", what="Another sentence on the same clip gives another film.",
             receipt="recordings that share one upload (same sha256) and differ in the sentence"),
        dict(id="edit-is-an-edl", what="Every edit is a new numbered version of the project's EDL: which seconds are kept, which pictures stand where.",
             receipt="the EDL versions kept with a recording, and the worker's own timeline arithmetic"),
    ]
