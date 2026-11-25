"""Place the evidence the line cites on the clock of the accepted narration."""
from urllib.parse import urlsplit

import film as F
import fit


def slice_shot(row, start, stop):
    a, b, kind, name, rec = row
    if (a, b) == (start, stop):
        return row
    rec = dict(rec)
    if rec.get("src"):
        lo, hi = rec["src"]
        rec["src"] = [lo + (hi - lo) * (f - a) / (b - a) for f in (start, stop)]
    return [start, stop, kind, name, rec]


def overlay(rows, replacement):
    """Make room for an anchored shot without restarting the sources on either side."""
    a, b = replacement[:2]
    out = []
    for row in rows:
        l, r = row[:2]
        if r <= a or b <= l:
            out.append(row)
            continue
        if l < a:
            out.append(slice_shot(row, l, a))
        if b < r:
            out.append(slice_shot(row, b, r))
    return sorted([*out, replacement], key=lambda row: row[0])


def exhibit(fact, line, start, end, text=None):
    source = fact.get("source") or urlsplit(fact["url"]).hostname
    return [start, end, "designed", None, dict(
        path=f"exhibit:{line}:{fact['id']}", text=text or fact["quote"], role="source",
        source_label=source, source_url=fact["url"], fact=fact["id"], pick_line=line, tier="exhibit")]


def place(script, clock, timeline, supply, identities):
    """A checked portrait on its name, an exhibit where a line ran out of evidence."""
    facts = {f["id"]: f for f in script.get("facts") or [] if f.get("quote") and f.get("url")}
    out = []
    for line, seg in enumerate(script["segments"]):
        rows = [r for r in timeline if r[4].get("pick_line") == line]
        if not rows:
            continue
        start, end = rows[0][0], rows[-1][1]
        show = [x for x in seg.get("show") or [] if isinstance(x, dict)]
        figures = [x for x in show if x.get("role") == "figure"]
        fact = next((facts[k] for k in seg.get("facts") or [] if k in facts), None)
        selected = seg.get("exhibit") or {}
        excerpt = None
        if selected.get("fact") in (seg.get("facts") or []) and selected["fact"] in facts:
            candidate = facts[selected["fact"]]
            if selected.get("excerpt") and selected["excerpt"] in candidate["quote"]:
                fact, excerpt = candidate, selected["excerpt"]
        if fact:
            repeats = {r[4]["path"] for r in rows if r[4].get("use", 0) and r[4]["use"] > 1}
            rebuilt = []
            for r in rows:
                rec = r[4]
                # The print supplies direct evidence where a numbered claim otherwise
                # had only illustrative takes, or the cut exhausted its own material.
                supporting = figures and r[2] == "clip" and rec["path"] in repeats and supply.grade(rec["path"], rec.get("take_i", 0), line) < 4
                if (r[2] == "designed" and (rec.get("fallback") or rec.get("role") in ("figure", "name", "key"))) or supporting or (
                        rec.get("again") and rec.get("path") in repeats and r[1] - r[0] >= F.FPS):
                    if rebuilt and rebuilt[-1][4].get("fact") == fact["id"]:
                        rebuilt[-1][1] = r[1]
                    else:
                        rebuilt.append(exhibit(fact, line, r[0], r[1], excerpt))
                else:
                    rebuilt.append(r)
            rows = rebuilt
        ws = clock.seg_words(line)
        passages = selected.get("passages") or []
        if passages and excerpt:
            for row in list(rows):
                a, b, _, _, rec = row
                if rec.get("tier") != "exhibit" or b - a < 2 * F.FPS:
                    continue
                visible = []
                for part in passages:
                    wi = part["word_i"]
                    if 0 <= wi < len(ws):
                        on = max(a, F.Clock.f(ws[wi]["t0"]) - 1)
                        if on + F.FPS // 2 < b:
                            visible.append((on, part["text"]))
                if len(visible) >= 2:
                    visible[0] = (a, visible[0][1])
                    for (lo, text), hi in zip(visible, [v[0] for v in visible[1:]] + [b]):
                        if lo < hi:
                            rows = overlay(rows, exhibit(fact, line, lo, hi, text))
        for brief in seg.get("see") or []:
            wi = brief.get("word_i")
            if not brief.get("keep_full_frame") or type(wi) is not int or not 0 <= wi < len(ws):
                continue
            name = brief.get("thing")
            photos = [r for r in supply.pics.get(name, []) if supply.fits(r["path"], line)]
            if not photos:
                continue
            a = max(start, F.Clock.f(ws[wi]["t0"]))
            current = next((r for r in rows if r[0] <= a < r[1]), None)
            def exact(rec):
                return brief.get("must_show") in ([rec.get("checked_brief")] + (rec.get("checked_briefs") or []))
            chosen = max(photos, key=lambda r: (exact(r), not r.get("checked_brief"), r.get("grade") or 0))
            later = [F.Clock.f(ws[x["word_i"]]["t0"]) for x in seg["see"]
                     if type(x.get("word_i")) is int and wi < x["word_i"] < len(ws)]
            b = min([end, *later])
            if current and current[3] == name and current[2] in ("clip", "photo"):
                if current[4]["path"] == chosen["path"] or not exact(chosen):
                    a = min(b, current[1])
            if b > a:
                rows = overlay(rows, [a, b, "photo", name, dict(chosen, tier="detail", pick_line=line)])
        for item in show:
            wi = item.get("word_i")
            if item.get("role") != "name" or not isinstance(wi, int) or not 0 <= wi < len(ws):
                continue
            a = max(start, F.Clock.f(ws[wi]["t0"]) - 1)
            b = min(end, a + round(1.5 * F.FPS))
            if a >= b:
                continue
            name = item["text"]
            portraits = []
            for group in supply.pics.values():
                for photo in group:
                    identity = identities.get(fit.take_key(photo["path"], (0, 0))) or {}
                    if (identity.get("work") or "").casefold() == name.casefold() and supply.fits(photo["path"], line):
                        portraits.append(photo)
            if portraits:
                rec = dict(portraits[0], pick_line=line, tier="identity", depicted_name=name)
                # Move a nearby placement of the same portrait to the actual name.
                if fact:
                    rows = [exhibit(fact, line, r[0], r[1], excerpt) if r[4]["path"] == rec["path"] else r for r in rows]
                replacement = [a, b, "photo", name, rec]
            else:
                replacement = [a, b, "designed", None, dict(path=f"name:{line}:{a}", text=name, role="name",
                               pick_line=line, tier="identity", depicted_name=name)]
            rows = overlay(rows, replacement)
        out.extend(rows)
    out.extend(r for r in timeline if r[4].get("pick_line") is None)
    return sorted(out, key=lambda r: r[0])


def source_page(P, start, stop, rec, clock):
    """A typeset transcription. Its source stays on screen throughout the quotation."""
    P.page([start, stop], "#F4EEDD", "#DED4BD", grain=.025)
    source = str(rec["source_label"])
    P.voices["source_imprint"] = dict(P.voices["cap"], px=38, shadow={})
    width = .78 * F.W
    v = f"source{start}"
    P.voices[v] = dict(P.voices["cap"], px=54, shadow={})
    text = "“" + rec["text"].strip() + "”"
    # Wrap using the renderer's font metrics. Size the entire excerpt to its own
    # area, leaving the lower caption seat and the upper figure seat free.
    for px in range(58, 23, -2):
        P.voices[v]["px"] = px
        lines = [""]
        for word in text.split():
            joined = (lines[-1] + " " + word).strip()
            if lines[-1] and P.R.width(v, joined) > width:
                lines.append(word)
            else:
                lines[-1] = joined
        if len(lines) * px * 1.25 < .31 * F.H:
            break
    P.voices["source_imprint"]["px"] = P.R.fit_px("source_imprint", source, width, 38)
    P.word([start, stop], source, "source_imprint", .10, .29, "#67452B", 1., z=2, role="source")
    for i, line in enumerate(lines):
        y = .35 + i * px * 1.25 / F.H
        # Change the ink of the existing line while the corresponding words
        # are heard. A second overprinted word would be independently reflowed
        # by the shared caption layout.
        times = [w for token in line.split() for w in F.said_span(clock, rec["pick_line"], token)
                 if start <= F.Clock.f(w["t0"]) < stop]
        if times:
            a = min(F.Clock.f(w["t0"]) for w in times)
            b = min(stop, max(F.Clock.f(w["t1"]) for w in times) + 3)
            spans = [(start, a, "#211C17"), (a, b, "#88421A"), (b, stop, "#211C17")]
        else:
            spans = [(start, stop, "#211C17")]
        for a, b, ink in spans:
            if a < b:
                P.word([a, b], line, v, .10, y, ink, 1., z=2, role="source")
    P.pictures.append(dict(f=[start, stop], kind="designed", name="source", path=rec["path"], shows=rec["text"],
                           mode="exhibit", source=rec["source_url"], fact=rec["fact"], tier="exhibit",
                           line=rec["pick_line"], spoken_line=rec["pick_line"]))


def retain_checked(timeline, receipts, supply):
    if not receipts:
        return timeline
    media = {r["path"]: r for group in supply.pics.values() for r in group}
    media.update({r["path"]: r for group in supply.footage.values() for r in group})
    for p in receipts:
        original = media.get(p.get("path"))
        if not original:
            continue
        a, b = p["f"]
        if b <= a or not any(r[0] <= a < r[1] and r[4].get("pick_line") == p["line"] for r in timeline):
            continue
        rec = dict(original, src=p.get("src"), take=p.get("take_bounds"), take_i=p.get("take"),
                   pick_line=p["line"], tier=p.get("tier"), use=p.get("use"))
        if p.get("keep_full_frame") or (p.get("mode") == "window" and p.get("crop") == [0, 0, 1, 1]):
            rec["keep_full_frame"] = True
        rec.update({k: p[k] for k in ("cont", "glimpse", "cutin", "burst", "depicted_name") if p.get(k)})
        timeline = overlay(timeline, [a, b, p["kind"], p.get("name"), rec])
    return timeline
