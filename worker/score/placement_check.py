"""Bind supplied pictures to the words at their actual first-film placements."""
import json
import os
import shutil
import tempfile

from . import boards, supply, taste


def check(work, comp, text, deadline):
    """Keep approvals attached to the actual compiled pictures, through cuts.

    Removing an earlier use of a video rewinds the compiler's source cursor.
    Recheck only placements whose picture interval or spoken passage changed.
    """
    approved = set()
    rec = {"checked": 0, "usd": 0., "removed": [], "placements": [], "passes": 0, "errors": []}
    # Each changing pass removes at least one occurrence. An unchanged rejected
    # occurrence is a compiler failure, never permission to show it unchecked.
    limit = len(comp.l1.get("items") or []) + 1
    for _ in range(limit):
        comp, text, part = _check_once(work, comp, text, deadline, approved)
        rec["passes"] += 1
        rec["checked"] += part["checked"]
        rec["usd"] += part.get("usd", 0.)
        rec["placements"].extend(part.get("placements") or [])
        rec["errors"].extend(part.get("errors") or [])
        if set(part["removed"]) & set(rec["removed"]):
            raise ValueError("Rejected picture persisted after recompilation")
        rec["removed"].extend(part["removed"])
        if not part["removed"]:
            with open(os.path.join(work, "placement-check.json"), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            return comp, text, rec
    raise ValueError("Picture placement checking did not settle")


def _check_once(work, comp, text, deadline, approved):
    """Check every used Bin picture, including each reused occurrence.

    Supply approved its search moment, not every passage the Director might
    later put it under. Video placements expose the beginning, middle and end
    of the actual source interval. Rejected occurrences become explicit Score
    cuts; the other uses of that asset keep their own evidence.
    """
    from . import compiler
    path = os.path.join(work, "bin.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return comp, text, {"checked": 0, "removed": []}
    source = {f"bin:{r['id']}": r for r in doc.get("items") or []}
    l1, candidates, wishes, owners, signatures = comp.l1, [], [], {}, {}
    try:
        with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
            words = json.load(f).get("words") or []
    except (OSError, ValueError):
        words = []
    tmp = tempfile.mkdtemp(prefix="placement-", dir=work)
    missing = set()
    try:
        for item in l1.get("items") or []:
            row = source.get(item.get("pic"))
            if row is None or item.get("k") not in ("shot", "card"):
                continue
            mid = sum(item["f"]) / (2 * float(l1["fps"]))
            at = taste.source_second(l1, mid)
            wish = {"at": at, "want": "The exact thing in the words at this placement"}
            windows = []
            for span in l1.get("spine") or []:
                a, b = max(item["f"][0], span["f"][0]), min(item["f"][1], span["f"][1])
                if b > a:
                    from scene.spine import source_at
                    windows.append((source_at(span, a, float(l1["fps"])),
                                    source_at(span, b, float(l1["fps"]))))
            wish["placed_words"] = " ".join(str(w.get("w", "")) for w in words
                if any(float(w.get("t0", 0)) < b and float(w.get("t1", w.get("t0", 0))) > a
                       for a, b in windows))
            # Association is explicit evidence from the original wish, never
            # inferred from the file name or from a broad topic match.
            original = next((w for w in doc.get("wishes") or []
                             if w.get("wish") == row.get("wish")), {})
            if not words:
                wish["want"] = original.get("want") or row.get("what") or wish["want"]
            if original.get("how") == "like":
                wish["how"] = "like"
            signature = json.dumps([item, wish], sort_keys=True)
            if signature in approved:
                continue
            signatures[item["id"]] = signature
            asset = l1.get("assets", {}).get(item["pic"], {})
            image_path = os.path.join(work, asset.get("path") or row["path"])
            frames = [(None, image_path)]
            if asset.get("kind") == "video" or row.get("kind") == "video":
                a, b = item.get("src") or [0., (item["f"][1]-item["f"][0])/float(l1["fps"])]
                frames = []
                for n, t in enumerate((a, (a+b)/2, max(a, b-1/float(l1["fps"])))):
                    picture = boards.grab(image_path, t, height=640)
                    if picture is None:
                        missing.add(item["id"])
                        continue
                    target = os.path.join(tmp, f"{len(candidates)}-{n}.jpg")
                    picture.convert("RGB").save(target, quality=90)
                    frames.append((t, target))
            for source_t, picture in frames:
                n = len(candidates)
                candidates.append(dict(row, id=n, wish=n,
                                       path=os.path.relpath(picture, work)))
                wishes.append(dict(wish))
                owners[n] = {"id": item["id"], "pic": item["pic"],
                             "program_s": mid, "source_s": at,
                             "picture_source_s": source_t}
        # check_fit already limits EACH board's model call to CHECK_S. More
        # than four boards require another executor wave. Capping the whole
        # collection at one board's timeout discarded later pictures while
        # the first film still had time left. Retain the job's hard deadline.
        kept, usd, report = supply.check_fit(work, candidates, wishes,
                                           deadline) if candidates else ([], 0., [])
        accepted = {r["id"] for r in kept}
        removed = missing | {owner["id"] for n, owner in owners.items() if n not in accepted}
        approved.update(signature for key, signature in signatures.items() if key not in removed)
        rec = {"checked": len(candidates), "usd": usd, "removed": sorted(removed),
               "errors": [r["check_error"] for r in report if r.get("check_error")],
               "placements": [{**owners[r["id"]], "check": r} for r in report if r.get("id") in owners]}
        if not removed:
            return comp, text, rec
        revised = taste.cut_text(text, sorted(removed), "picture does not match the words at this placement")
        return compiler.compile_score(revised, work_dir=work), revised, rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
