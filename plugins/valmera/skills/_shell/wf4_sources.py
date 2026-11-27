"""Build the documentary's companion from its finished screenplay and edit records."""
import json
from collections import defaultdict
from pathlib import Path


def checkpoint(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def story_arc(script, *, cited=False):
    if cited:
        final = script.get("script") or {}
        segs = final.get("segments") or []
        beats = (script.get("spine") or {}).get("beats") or {}
        facts = {row["id"]: row for row in final.get("facts") or []}
        lines = []
        for label, role in (("Setup", "hook"), ("Turn", "turn"), ("Payoff", "payoff")):
            index = (beats.get(role) or {}).get("line")
            seg = segs[index] if isinstance(index, int) and 0 <= index < len(segs) and segs[index].get("beat") == role else next(
                (s for s in segs if s.get("beat") == role), {})
            pages = dict.fromkeys(facts[f]["url"] for f in seg.get("facts") or [] if f in facts and facts[f].get("url"))
            if seg.get("say"):
                lines.append(f"- **{label}** — " + " ".join(seg["say"].split()) + " " + " ".join(f"<{u}>" for u in pages))
        return lines
    plan = script.get("idea_full") or {}
    arc = plan.get("arc") if isinstance(plan.get("arc"), dict) else {}
    segments = (script.get("script") or {}).get("segments") or []
    lines = []
    for label, old_key, beat in (("Setup", "hook", "hook"), ("Escalation", "turn", "turn"),
                                 ("Payoff", "payoff", "payoff")):
        value = arc.get(label.lower()) or plan.get(old_key)
        if not value:
            value = next((s["say"] for s in segments if s.get("beat") == beat and s.get("say")), None)
        lines.append(f"- **{label}** — " + " ".join((value or "Unrecorded").splitlines()))
    return lines


def story_sidecar(folder, script, recording=None):
    lines = story_arc(script, cited=True)
    if len(lines) < 3 and recording:
        # A showcase has no narration. Describe the published demonstration from its recording.
        idea = script.get("idea_full") or {}
        result = script.get("export_check") or {}
        elements = [e.get("how") for e in result.get("elements") or [] if e.get("visible") and e.get("how")]
        parts = (idea.get("pain") or idea.get("hook"), recording.get("typed"),
                 " ".join(elements) or result.get("looks"))
        if all(parts):
            lines = [f"- **{role}** — " + " ".join(value.split()) for role, value in
                     zip(("Setup", "Turn", "Payoff"), parts)]
    if lines:
        (Path(folder) / "STORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def citations(script):
    dossier = script.get("dossier") or {}
    facts = {f["id"]: f for f in script.get("facts_cited") or []}
    # The verified dossier retains bylines and dates that older facts_cited rows omit.
    facts.update({f["id"]: f for f in dossier.get("facts") or []})
    documents = defaultdict(list)
    for doc in dossier.get("documents") or []:
        documents[doc.get("fact")].append(doc)
    uses = defaultdict(list)
    segments = (script.get("script") or {}).get("segments") or []
    for number, segment in enumerate(segments, 1):
        design = segment.get("design") or {}
        units = [design, *(design.get("bars") or []), *(design.get("points") or [])]
        ids = [*(segment.get("facts") or []), *(u.get("fact") for u in units)]
        for fid in dict.fromkeys(ids):
            if fid in facts:
                uses[fid].append(str(number))
    if not segments:
        uses.update({f["id"]: [] for f in script.get("facts_cited") or []})

    lines = []
    for fid, numbers in uses.items():
        fact = facts[fid]
        where = " · segments " + ", ".join(numbers) if numbers else ""
        lines += [f"### {fid}{where}", "", fact.get("claim") or "", ""]
        if fact.get("quote"):
            lines += ["> " + fact["quote"].replace("\n", "\n> "), ""]
        lines += [credit(fact), ""]
        if fact.get("year"):
            lines += [f"Event year: {fact['year']}", ""]
        for doc in documents[fid]:
            lines += ["Document: " + " · ".join(str(doc[k]) for k in ("title", "from", "date") if doc.get(k)), ""]
    return lines


def credit(record):
    fields = ("name", "subject", "title", "author", "channel", "credit", "date", "licence", "attribution")
    parts = [str(record[k]) for k in fields if record.get(k)]
    parts += [f"<{record[k]}>" for k in ("page", "url", "source_url") if record.get(k)]
    return " · ".join(dict.fromkeys(parts))


def media_units(supply, score, segments):
    shots = checkpoint(supply / "shots.json", [])
    for shot in shots:
        clip = shot["clip"]
        source = {**clip, **clip["source"]}
        image = clip.get("kind") == "image"
        parts = ["Image" if image else "Footage", f"segment {shot['seg'] + 1}",
                 f"frames {shot['f'][0]}–{shot['f'][1]}", credit(source)]
        if clip.get("kind") == "diagram":
            parts[0] = "Original explanatory diagram"
        if shot.get("companion_source"):
            parts.append("Companion excerpt · " + credit(shot["companion_source"]))
        if shot.get("real_voice"):
            parts.append(shot["real_voice"])
        if clip.get("editorial_label"):
            parts.append(clip["editorial_label"])
        if not image and clip.get("time_range") and shot.get("src"):
            start, stop = (clip["time_range"][0] + offset for offset in shot["src"])
            parts.append(f"source {round(start, 6)}–{round(stop, 6)} s")
        yield " · ".join(parts)

    pack = checkpoint(supply / "pack.json", {}).get("pack") or {}
    # Earlier scores identify the displayed photograph by local path, not by subject.
    photos = {p["path"]: p for group in (pack.get("pictures") or {}).values() for p in group}
    for beat in (score.get("beats") or {}).get("pictures") or []:
        photo = photos.get(beat.get("path"))
        if photo and beat.get("kind") == "photo":
            yield f"Image · frames {beat['f'][0]}–{beat['f'][1]} · {credit(photo)}"

    originals = {rec["path"]: rec for rec in (pack.get("designs") or {}).values()
                 if rec.get("source_url")}
    if originals and score.get("l1"):
        scene = checkpoint(score["l1"], {})
        for item in scene.get("items") or []:
            path = (scene["assets"].get(item.get("pic")) or {}).get("path")
            if path in originals:
                rec = originals[path]
                seg = rec["seg"]
                design = (segments[seg].get("design") or {}) if 0 <= seg < len(segments) else {}
                name = design.get("title") or design.get("who")
                details = " · ".join(filter(None, (design.get("fact"), name, credit(rec))))
                yield f"Source capture · segment {seg + 1} · {details}"


def write(folder, supply, results):
    script = results["script"]
    (Path(folder) / "STORY.md").write_text("\n".join(story_arc(script)) + "\n", encoding="utf-8")
    lines = [f"# {script.get('title') or 'Documentary'}", "", "## Story arc", "",
             *story_arc(script), "", "## Cited claims", "", *citations(script), "## On-screen sources", ""]
    segments = (script.get("script") or {}).get("segments") or []
    units = list(media_units(Path(supply), results.get("score") or {}, segments))
    if units:
        lines.extend("- " + unit for unit in units)
    else:
        lines.append("No shot-level source records were retained.")
    (Path(folder) / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
