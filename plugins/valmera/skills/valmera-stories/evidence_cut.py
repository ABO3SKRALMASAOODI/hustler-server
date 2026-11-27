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


def place(script, clock, timeline, supply, identities):
    """Anchor the identifying portrait to the name. Its identity survives a readability repair."""
    rows = list(timeline)
    for line, seg in enumerate(script["segments"]):
        words = clock.seg_words(line)
        for item in seg.get("show") or []:
            if not isinstance(item, dict) or item.get("role") != "name":
                continue
            wi = item.get("word_i")
            if type(wi) is not int or not 0 <= wi < len(words):
                continue
            name = item["text"]
            portraits = []
            for photos in supply.pics.values():
                for photo in photos:
                    identity = identities.get(fit.take_key(photo["path"], (0,0))) or {}
                    subjects = [identity.get("work")] + [p.get("name") for p in identity.get("people") or []]
                    if any(isinstance(n,str) and n.casefold() == name.casefold() for n in subjects):
                        portraits.append((photo,identity))
            if not portraits:
                continue
            a = max(0,F.Clock.f(words[wi]["t0"])-1)
            b = min(max(r[1] for r in timeline), a + round(1.5*F.FPS))
            photo,identity = max(portraits,key=lambda pair:supply.grade(pair[0]["path"],0,line))
            rec = dict(photo, pick_line=line, word_i=wi, tier="identity", depicted_name=name, identity=identity)
            import os
            if (os.path.basename(photo["path"]),line) in supply.deny:
                rec["portrait_repair"] = True
            # Join its already-placed image to the super, keeping a continuous push on that source.
            for lo,hi,kind,_,other in rows:
                if kind == "photo" and other["path"] == photo["path"] and lo <= b and hi >= a:
                    a,b = min(a,lo),max(b,hi)
            if a < b:
                rows = overlay(rows, [a,b,"photo",name,rec])
    return rows


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
