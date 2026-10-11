"""The three lens judges (ROADMAP §4.5 "Judging").

Refbench does not call a model itself: it writes one PACKET per lens
(``judge_<lens>.json``: the filled prompt from ``judges/<lens>.md`` plus the
image and clip paths), and reads verdicts back (``judge_<lens>.verdict.json``
or ``import_verdicts``). Any judge that can read images (an agent, a VLM
call, a human) fills them in. Until all lenses answer, the case's judged
pass is ``None`` (pending), never a silent pass.

Pass rule (``2of3_no_major``): at least two lenses say ``matches`` or
``exceeds`` and none flags a major defect.
"""

import json
import os

from . import JUDGES_DIR

VERDICTS = ("exceeds", "matches", "below")


def prompt(lens, **fields):
    with open(os.path.join(JUDGES_DIR, f"{lens}.md")) as fh:
        text = fh.read()
    for k, v in fields.items():
        text = text.replace("{" + k + "}", v if isinstance(v, str)
                            else json.dumps(v, ensure_ascii=False))
    return text


def packets(case, out_dir, *, board=None, metrics=None, standing=None,
            edl_summary=None):
    """Write judge_<lens>.json for each lens in the case; return paths."""
    refs = ", ".join(f"{r['ref']}@{'≈' if r.get('approx') else ''}"
                     f"{r.get('t')}" for r in case.get("reference", []))
    fields = {"case_id": case["id"], "title": case.get("title", ""),
              "reference": refs or "none",
              "devices": case.get("devices") or case.get("title", ""),
              "metrics": {k: {"value": v.get("value"), "bar": v.get("bar"),
                              "pass": v.get("pass")}
                          for k, v in (metrics or {}).items()},
              "beyond": case.get("beyond", ""),
              "allow": case.get("allow", []),
              "edl_summary": edl_summary or {},
              "standing": standing or {}}
    out = []
    for lens in case.get("judges", {}).get("lenses", []):
        pk = {"case": case["id"], "lens": lens,
              "prompt": prompt(lens, **fields),
              "images": [p for p in (board or {}).get("images", [])],
              "clip": (board or {}).get("clip"),
              "reply_to": os.path.join(out_dir, f"judge_{lens}.verdict.json")}
        p = os.path.join(out_dir, f"judge_{lens}.json")
        with open(p, "w") as fh:
            json.dump(pk, fh, indent=1, ensure_ascii=False)
        out.append(p)
    return out


def parse(reply, lens=None):
    """A judge's reply (dict or JSON text, possibly fenced) -> verdict dict.
    Raises ValueError on anything malformed."""
    if isinstance(reply, str):
        s = reply.strip()
        if s.startswith("```"):
            s = s.strip("`")
            s = s[s.find("{"):]
        reply = json.loads(s[s.find("{"):s.rfind("}") + 1])
    v = dict(reply)
    if lens and v.get("lens", lens) != lens:
        raise ValueError(f"reply is for lens {v.get('lens')}, not {lens}")
    v.setdefault("lens", lens)
    if v.get("verdict") not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if not isinstance(v.get("major_defect"), bool):
        raise ValueError("major_defect must be true/false")
    v["notes"] = str(v.get("notes", ""))[:600]
    return {k: v[k] for k in ("lens", "verdict", "major_defect", "notes")}


def collect(case, out_dir):
    """Read whatever verdict files exist for the case's lenses."""
    got = []
    for lens in case.get("judges", {}).get("lenses", []):
        p = os.path.join(out_dir, f"judge_{lens}.verdict.json")
        if os.path.exists(p):
            with open(p) as fh:
                got.append(parse(fh.read(), lens))
    return got


def combine(verdicts, lenses, rule="2of3_no_major"):
    """True / False, or None while any lens is missing."""
    by = {v["lens"]: v for v in verdicts}
    if any(lens not in by for lens in lenses):
        return None
    if any(v["major_defect"] for v in by.values()):
        return False
    ok = sum(1 for v in by.values() if v["verdict"] in ("matches", "exceeds"))
    if rule == "all_no_major":
        return ok == len(lenses)
    if rule == "owner":
        return None
    return ok >= 2
