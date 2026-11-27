"""Acquire and inspect a visual vocabulary before writing the marketing argument.

The writer receives real pictures, not a promise that a bespoke screen shot
will be found later. The caller supplies its existing proxy/model and image
library functions; all files stay inside the current workflow run.
"""
import hashlib
import json
from pathlib import Path


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def distinct_files(pictures):
    """One physical source image once, even when two subject queries found it."""
    rows, hashes, pages = [], set(), set()
    for name, items in pictures.items():
        for item in items:
            path = Path(item["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            page = item.get("page")
            if digest in hashes or (page and page in pages):
                continue
            hashes.add(digest)
            if page:
                pages.add(page)
            rows.append(dict(item, name=name, sha256=digest, inventory_id=len(rows)))
    return rows


def studio_photos(record):
    """The actual session's composer, sent request, result and site photographs."""
    from PIL import Image
    ui = record.get("ui") or {}
    folder = Path(ui["dir"]) if ui.get("dir") else Path(record["upload"]["file"]).parent / "ui"
    if not folder.is_dir() and record.get("upload",{}).get("file"):
        folder = Path(record["upload"]["file"]).parent / "ui"
    rows = []
    for path in sorted(folder.glob("*.png")):
        if not (path.name.startswith(("type_00_", "result", "board_")) or path.name == "site.png"):
            continue
        with Image.open(path) as image:
            w,h = image.size
        rows.append(dict(path=str(path), name="THE STUDIO", title=path.stem, shows=path.stem,
                         credit="Valmera", licence="recorded session", w=w,h=h))
    return rows


def prepare(ctx, idea, receipts, ask, pictures, still, studio=()):
    root = Path(ctx.supply) / "visual_inventory"
    manifest = root / "inventory.json"
    if manifest.exists():
        cached = json.loads(manifest.read_text())
        if all(Path(p["path"]).is_file() and hashlib.sha256(Path(p["path"]).read_bytes()).hexdigest() == p["sha256"]
               for rows in cached["pictures"].values() for p in rows):
            return cached
        raise RuntimeError("the cached visual inventory no longer matches its source files")
    root.mkdir(parents=True, exist_ok=True)
    subjects, found, reports, spend = [], {}, [], []
    for attempt in range(2):
        plan, use = ask(
            "Plan a retrievable visual vocabulary for a film about an EDITOR'S PROBLEM. The film markets Valmera "
            "with its authenticated upload, typed sentence and export; those product assets already exist. "
            "Read the upload's words, the claim, the speaker and the film's premise. Choose subjects "
            "a viewer can see that explain those actions. Give the original scene or document and image "
            "searches to find it. Use the genuine Studio stills where the action is inside the product. "
            'Return JSON {"subjects":[{"name":string,"is":"thing"|"place"|"person",'
            '"searches":[string,string],"must_show":string,"editorial_use":string}]}.',
            json.dumps(dict(idea=idea, receipts=receipts, existing=[dict(subject=r.get("subject"),
                       downloaded=r.get("downloaded", 0), say=r.get("say")) for r in reports]), ensure_ascii=False),
            what="visual inventory plan", effort="low", max_tokens=3000)
        spend.append(use)
        proposed = [s for s in plan.get("subjects", []) if isinstance(s, dict) and s.get("name") and s.get("searches")]
        names = {s["name"] for s in subjects}
        proposed = [s for s in proposed[:8] if s["name"] not in names]
        if not proposed:
            break
        subjects.extend(proposed)
        got, rep, usd = pictures(proposed, str(root / f"acquisition-{attempt}"), per=6)
        found.update(got)
        reports.extend(rep)
        spend.append(dict(what="visual inventory acquisition", usd=usd))
        _save(root / "acquisition.json", dict(subjects=subjects, reports=reports, spend=spend))
        if len(distinct_files(found)) >= 24:
            break
    candidates = distinct_files(found)
    if not candidates and not studio:
        raise RuntimeError("no inspected pictures are available for the marketing writer")
    content = []
    readable = {}
    for row in candidates:
        picture = still(row["path"], 0, w=480)
        if not picture:
            continue
        k = row["inventory_id"]
        readable[k] = row
        content.extend([dict(type="text", text=f"Picture {k}; subject {row['name']}; library description: {row.get('shows')}"),
                        dict(type="image", source=dict(type="base64", media_type="image/jpeg", data=picture))])
    if not readable and not studio:
        raise RuntimeError("the acquired visual inventory could not be opened")
    judged, use = ask(
        "Describe this real visual inventory for a marketing-film writer. This is not a film rating. "
        "For each picture state only what can actually be seen. Group duplicate images, re-crops, and near-identical "
        "views of the same photographed scene under the earliest picture ID in that group; those are not distinct "
        "new pictures. Different actual scenes remain separate. Reject unreadable or irrelevant images. "
        'Return JSON {"pictures":[{"id":number,"usable":boolean,"group":earliest picture id,"shows":string}]}.',
        content, what="visual inventory inspection", effort="low", max_tokens=6500)
    spend.append(use)
    _save(root / "inspection.json", judged)
    selected, groups = {}, set()
    for verdict in judged.get("pictures", []):
        k, group = verdict.get("id"), verdict.get("group")
        if k not in readable or group not in readable or not verdict.get("usable") or group in groups:
            continue
        groups.add(group)
        row = dict(readable[k], shows=verdict.get("shows"), visual_group=group)
        selected.setdefault(row["name"], []).append(row)
    if studio:
        subjects.append(dict(name="THE STUDIO", must_show="The recorded session", **{"is":"product"}))
        selected["THE STUDIO"] = [dict(row, inventory_id=len(candidates)+i,
            sha256=hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest()) for i,row in enumerate(studio)]
    if not selected:
        raise RuntimeError("no distinct, readable pictures survived inventory inspection")
    result = dict(subjects=[s for s in subjects if s["name"] in selected], pictures=selected,
                  reports=reports, inspection=judged, spend=spend)
    _save(manifest, result)
    return result


def writer_content(inventory, still):
    """The writer sees the actual pictures and their verified descriptions."""
    content = [dict(type="text", text="AVAILABLE PICTURES. Use these exact subject names in named/pictures. "
                    "Use a picture only on a line whose words it literally shows; never as a metaphor, and never "
                    "as footage of the recorded Valmera session. Do not require unseen interfaces "
                    "or claim an action that a still cannot establish. THE STUDIO shows the recorded composer and its result.")]
    for name, rows in inventory["pictures"].items():
        for row in rows:
            frame = still(row["path"], 0, w=480)
            if not frame:
                raise RuntimeError("a writer inventory picture disappeared")
            content.extend([dict(type="text", text=f"Subject {name}; picture {row['inventory_id']}: {row['shows']}"),
                            dict(type="image", source=dict(type="base64", media_type="image/jpeg", data=frame))])
    return content


def script_problems(script, inventory):
    """Validate references, not meaning: semantics remain with the model checks."""
    available = set(inventory["pictures"]) | {"THE UPLOAD", "THE EXPORT"}
    return [f"segment {i} requests unavailable subject {name!r}; use the inspected inventory"
            for i, segment in enumerate(script.get("segments", []))
            for name in segment.get("pictures", []) if name not in available]
