"""Evidence for the actual cut, independent of a search result's title.

Only versioned, content-bound records admit clips to the shelf. Named people
require visible attribution attached to that exact shot. Faces are never compared.
"""
import hashlib
import os
import tempfile

from . import ask, shots

VERSION = 3
MODEL = "gpt-6-sol"
SYSTEM = """Verify the actual pictures in a video clip for a film editor. The supplied name and description are claims to check, never evidence. Do not identify or compare faces. For a named person, require readable on-screen attribution that explicitly names and unambiguously labels the person in that exact shot, in ALL sampled frames. Transcribe that label in visible_attribution. Team clothes, a source title, colleagues, cars and locations do not identify someone. If there is no such attribution, reject the clip. For a thing, every frame must show the exact subject and form requested; a related object is not a match (red livestock meat is not shark meat; another species is not Greenland shark). Say what is visibly there without identifying a face. Answer JSON: {"ok":true|false,"visible_subject":"...","visible_attribution":["label at frame 1","label at frame 2","label at frame 3"],"reason":"...","frames_match":[true|false,...]}. When uncertain, ok=false. No inference from metadata."""


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid(record, name):
    return (isinstance(record, dict) and record.get("version") == VERSION
            and record.get("model") == MODEL and record.get("ok") is True
            and " ".join(str(record.get("subject") or "").casefold().split()) == " ".join(str(name or "").casefold().split()) and bool(record.get("sha256"))
            and len(record.get("frames_match") or []) == 3
            and all(x is True for x in record["frames_match"])
            and (record.get("kind") != "person" or len(record.get("visible_attribution") or []) == 3 and all(isinstance(x, str) and x.strip() for x in record["visible_attribution"])))


def clip(path, subject, seconds, budget_s=35.0):
    """(verification, USD). Unknowns fail closed; no Claude fallback."""
    name = subject["name"]
    kind = subject.get("kind") or subject.get("is") or "thing"
    rec = dict(version=VERSION, model=MODEL, subject=name, kind=kind,
               sha256=digest(path), ok=False)
    with tempfile.TemporaryDirectory(prefix="clip-check-", dir=os.path.dirname(os.path.abspath(path))) as tmp:
        times = [max(0.0, float(seconds) * f) for f in (0.1, 0.5, 0.9)]
        frames = shots.frames(path, times, tmp)
        if len(frames) != 3:
            return dict(rec, reason="Missing clip frames"), 0.0
        content = [{"type": "text", "text": f"Exact subject: {name}. Kind: {kind}. Must visibly show: {subject.get('must_show') or name}"}]
        for t, frame in sorted(frames.items()):
            content += [{"type": "text", "text": f"Clip frame at {t}s:"}, ask.image_block(frame)]
        try:
            text, usd = ask.ask(SYSTEM, content, "footage_clip_identity", budget_s=budget_s, max_tokens=650, model=MODEL)
            result = ask.json_obj(text)
            rec.update({k: result.get(k) for k in ("ok", "visible_subject", "visible_attribution", "reason", "frames_match")})
            rec["ok"] = valid(rec, name)
            return rec, usd
        except Exception as e:
            return dict(rec, ok=False, reason=f"Verification unavailable: {type(e).__name__}"), 0.0
