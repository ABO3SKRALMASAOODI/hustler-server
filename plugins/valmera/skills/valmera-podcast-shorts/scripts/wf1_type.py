"""Bind proposed typography to the words of this take, in programme frames."""
import json
from pathlib import Path
import unicodedata

import palette_menu


def token(word):
    # Strip surrounding punctuation. Interior decimal points, money and percent
    # signs change a figure's meaning, so keep them.
    word = unicodedata.normalize("NFKC", str(word)).casefold()
    return word.strip(' \t\n.,!?:;"“”‘’()[]')


def phrase(words, text):
    want = [token(w) for w in str(text).split() if token(w)]
    if want:
        for i in range(len(words) - len(want) + 1):
            if [token(w[0]) for w in words[i:i + len(want)]] == want:
                return words[i:i + len(want)]
    return []


def type_ask(ask, said):
    """Use the speaker's text at the Director's address for every ink-bearing field."""
    dev = ask["dev"]
    if dev not in (*palette_menu.REPLACES, *palette_menu.DECORATES, "lamp", "diecut", "answer", "word_blend"):
        return ask
    opts = dict(ask.get("args") or {})
    opts.update({k: v for k, v in ask.items() if k != "args"})
    at = opts.get("f", opts.get("at", 0))
    if isinstance(at, dict):
        at = at.get("f", 0)
    a = int(at[0] if isinstance(at, (list, tuple)) else at)
    end = int(at[1]) if isinstance(at, (list, tuple)) else a + 30
    # Single-frame effect addresses still have room for a spoken phrase.
    end = min(max(end, a + 12), int(opts.get("bound") or max(end, a + 30)))
    local = [w for w in said if w[1] < end and w[2] > a]
    if not local:
        return dict(dev="settle", id=ask["id"], beat=ask.get("beat"), f=a)
    local = [w for w in local if w[3] == local[0][3]]
    wanted = opts.get("t") or opts.get("text") or opts.get("name") or opts.get("word")
    if not wanted:
        wanted = " ".join(str(w.get("t", "") if isinstance(w, dict) else w)
                          for w in (opts.get("words") or opts.get("lines") or []))
    picked = phrase(local, wanted or "") or local[:4]
    text = " ".join(w[0] for w in picked)
    a, end = max(a, picked[0][1]), max(a + 2, picked[-1][2])
    # A number display lands on its spoken value. Growing the type provides
    # the movement without printing intermediate measurements.
    if dev == "counter":
        opts["spoken_steps"] = [[max(a, w[1]), w[0]] for w in picked]
    elif dev in ("donut", "bar"):
        dev = "verb"
        opts["verb"] = "punch"
    elif dev == "name":
        # Identity captions have their own path through the frame reader below.
        # Emphasise the spoken caption here when the camera is on the listener.
        dev = "highlight"
    if dev == "verb" and opts.get("verb") in (None, "scramble"):
        opts["verb"] = "focus"
    for key in (*palette_menu.TEXT_FIELDS, "note", "lines", "words", "key", "phrase",
                "picks", "lands", "args", "from", "to", "value", "bars", "fmt"):
        opts.pop(key, None)
    opts.update(dev=dev, t=text, at=[a, end], f=[a, end], bound=end)
    if "until" in opts or dev in palette_menu.DECORATES:
        opts["until"] = end
    if dev == "type_run":
        opts["words"] = [w[0] for w in picked]
    if dev == "answer":
        opts["picks"] = [dict(p, word=hit[0]) for p in ask.get("picks") or []
                         for hit in said if hit[1] <= p.get("f", -1) < hit[2]]
    return opts


def finish_layout(path):
    """Resolve backing plates and identity captions on the palette's final cuts."""
    from scene.palette.kit import cut
    doc = json.loads(Path(path).read_text())
    items = doc["items"]
    words = [it for it in items if it["k"] == "word" and it.get("t", "").strip()]
    visuals = [it for it in items if it["k"] in ("shot", "card")
               and (doc.get("assets", {}).get(it.get("pic")) or {}).get("kind") in ("image", "video")]
    result = []
    for it in items:
        picture = it.get("name_picture")
        on = it.get("display_on")
        pool = it.get("mode") == "pool"
        if not picture and on is None and not pool:
            result.append(it)
            continue
        a, b = it["f"]
        candidates = visuals if picture else [w for w in words if
                      (w.get("role") == "cap" if pool else w.get("display_ink_on") == on)]
        candidates = [v for v in candidates if v["f"][0] < b and v["f"][1] > a]
        edges = sorted({a, b, *(f for v in candidates for f in v["f"] if a < f < b)})
        for start, end in zip(edges, edges[1:]):
            live = [v for v in candidates if v["f"][0] <= start < v["f"][1]]
            # All the visible documentary pictures must still be the identified
            # asset. A montage or a cut to the listener ends the name with it.
            if live and (not picture or all(v["pic"] == picture for v in live)):
                part = cut(it, start, end, new_id=f"{it['id']}~{start}")
                if pool:
                    part["rect"] = it["rect"]
                result.append(part)
    doc["items"] = result
    out = Path(path).with_suffix(".type.json")
    out.write_text(json.dumps(doc))
    return str(out)


def repair_contrast(doc, scene, failed, ruler):
    """Give each affected caption page its own opaque backing under the ink."""
    from wf1_layout import caption_pages, _bounds
    failed = set(failed)
    for start, end, words in caption_pages([i for i in doc["items"] if i.get("role") == "cap" and i.get("t", "").strip()]):
        if not any(w["id"] in failed for w in words):
            continue
        boxes = [box for _, box, _ in _bounds(doc, words, ruler)]
        if not boxes:
            continue
        left = min(b[0] for b in boxes) - .012
        top = min(b[1] for b in boxes) - .008
        right = max(b[2] for b in boxes) + .012
        bottom = max(b[3] for b in boxes) + .008
        for w in words:
            w.update(ink="#FFFFFF", stroke=dict(c="#101010", w=1.6))
        # The stage-10 fill is above picture grades and negative flashes and
        # below the caption ink. Its box exists only for this page.
        z = max((i.get("z", 0) for i in doc["items"] if i.get("k") == "flash"), default=0) + 1
        doc["items"].append(dict(k="flash", mode="fill", id=f"caption-back-{start}", f=[start, end],
                                 z=z, rect=[left, top, right, bottom], c="#101010", a=1))
    out = Path(scene).with_suffix(".contrast.json")
    out.write_text(json.dumps(doc))
    return str(out)


def measure_contrast(movie, doc, ffmpeg, ruler):
    """Read actual programme frames once, sampling the ground of each laid caption."""
    import subprocess
    import numpy as np
    from wf1_layout import value

    samples = {}
    for w in doc["items"]:
        if w.get("role") == "cap" and w.get("t", "").strip():
            frame = (w["f"][0] + w["f"][1]) // 2
            samples.setdefault(frame, []).append(w)
    bad = []
    frames = sorted(samples)
    if frames:
        select = "+".join(f"eq(n,{f})" for f in frames)
        cmd = [ffmpeg, "-v", "error", "-i", movie, "-an", "-vf", f"select='{select}',scale=270:480",
               "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
        pixels = subprocess.run(cmd, capture_output=True, check=True).stdout
        images = np.frombuffer(pixels, dtype=np.uint8).reshape(len(frames), 480, 270)
        for frame, image in zip(frames, images):
            for word in samples[frame]:
                x, y = value(word, "x", frame, .5), value(word, "y", frame, .65)
                right = x + ruler.width(word["v"], word["t"]) / doc["frame"][0]
                band = image[max(0, int((y-.045)*480)):min(480, int((y+.012)*480)),
                             max(0, int(x*270)):min(270, max(int(x*270)+2, int(right*270)))]
                ground = np.median(band) / 255 if band.size else None
                rgb = bytes.fromhex(word.get("ink", "#FFFFFF").lstrip("#"))
                light = (.2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]) > 127.5
                if ground is None or (light and ground > .6) or (not light and ground < .4):
                    bad.append(word["id"])
    return dict(captions_sampled=sum(map(len, samples.values())), unreadable=len(bad), word_ids=bad, ok=not bad)
