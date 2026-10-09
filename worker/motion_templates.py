"""Motion template registry: specs, parameter validation and render jobs.

Every library composition is one file in worker/motion/templates/<name>.html
whose first line is an HTML comment carrying its spec as JSON:

    <!--MG-SPEC {"title": "...", "category": "type", "description": "...",
       "params": {"text": {"type": "str", "required": true, "max": 120}, ...},
       "duration": 2.5, "layer": "above_captions", "sfx": [{"at": 0, "kind": "whoosh"}]} -->

The rest of the file is the composition body (style + markup + script)
driven by worker/motion/runtime.js. Keeping spec and composition together
means a template is added by dropping in one file.

Parameter types: str, text (multi-line), int, float, bool, color (#RRGGBB),
enum (``values``), list (of strings; ``max_items``/``max``), rows (list of
objects with string ``fields``), asset (a project storage key the renderer
resolves to a local file served to the page at ``assets/<param>``).

``normalize_params`` is deliberately LENIENT (it runs inside validate_edl on
stored EDLs, so a template that later drops a parameter must not invalidate
old edits): unknown keys are dropped, numbers clamped, defaults filled.
``check_params`` is the STRICT counterpart used by write tools to tell the
editor exactly what it got wrong.
"""

import json
import os
import re

import motion_engine

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SPEC_RE = re.compile(r"^\s*<!--MG-SPEC\s+(\{.*?\})\s*-->", re.S)
HTML_SPEC = {
    "title": "Authored HTML motion graphic",
    "category": "custom",
    "description": ("Your own HTML/CSS/JS composition on the deterministic MG runtime. "
                    "Use when no library template expresses the idea."),
    "params": {},
    "duration": 3.0,
    "layer": "above_captions",
    "sfx": [],
}
_REGISTRY = {"mtime": None, "specs": {}}


def _load():
    d = motion_engine.TEMPLATES_DIR
    try:
        names = sorted(f for f in os.listdir(d) if f.endswith(".html"))
    except OSError:
        names = []
    stamp = tuple((n, os.stat(os.path.join(d, n)).st_mtime_ns) for n in names)
    if _REGISTRY["mtime"] == stamp:
        return _REGISTRY["specs"]
    specs = {}
    for n in names:
        path = os.path.join(d, n)
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(12000)
        m = _SPEC_RE.match(head)
        if not m:
            continue
        try:
            spec = json.loads(m.group(1))
        except ValueError as e:
            raise RuntimeError(f"motion template {n}: bad MG-SPEC JSON ({e})")
        spec.setdefault("params", {})
        spec.setdefault("duration", 3.0)
        spec.setdefault("layer", "above_captions")
        spec.setdefault("sfx", [])
        spec["name"] = n[:-5]
        specs[n[:-5]] = spec
    _REGISTRY.update(mtime=stamp, specs=specs)
    return specs


def names():
    return sorted(_load())


def spec(name):
    if name == "html":
        return dict(HTML_SPEC, name="html")
    s = _load().get(name)
    if s is None:
        close = [n for n in names() if name and (name in n or n in name)][:5]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ValueError(f"unknown motion template '{name}'.{hint} "
                         f"Available: {', '.join(names())}, html")
    return s


def catalog(category=None):
    """Compact list for tool descriptions / list_motion_templates."""
    out = []
    for n in names():
        s = _load()[n]
        if s.get("internal"):
            continue
        if category and s.get("category") != category:
            continue
        params = {}
        for k, p in s["params"].items():
            d = {"type": p.get("type", "str")}
            for key in ("required", "default", "values", "min", "max", "max_items", "fields", "hint"):
                if key in p:
                    d[key] = p[key]
            params[k] = d
        out.append({"name": n, "title": s.get("title", n), "category": s.get("category"),
                    "description": s.get("description", ""), "duration": s.get("duration"),
                    "layer": s.get("layer"), "params": params,
                    "sfx": [c.get("kind") for c in s.get("sfx") or []]})
    return out


def _coerce(name, key, p, v, strict):
    t = p.get("type", "str")
    where = f"{name}.{key}"
    if t in ("str", "text"):
        if not isinstance(v, (str, int, float)):
            raise ValueError(f"{where} must be text")
        v = str(v)
        if t == "str":
            v = " ".join(v.split())
        mx = int(p.get("max", 200))
        if len(v) > mx:
            if strict:
                raise ValueError(f"{where} is {len(v)} characters; keep it to {mx}")
            v = v[:mx]
        return v
    if t == "int":
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            raise ValueError(f"{where} must be a number")
        lo, hi = p.get("min"), p.get("max")
        if lo is not None:
            v = max(int(lo), v)
        if hi is not None:
            v = min(int(hi), v)
        return v
    if t == "float":
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{where} must be a number")
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError(f"{where} must be finite")
        lo, hi = p.get("min"), p.get("max")
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        return round(v, 4)
    if t == "bool":
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    if t == "color":
        v = str(v).strip()
        if not HEX.match(v):
            raise ValueError(f"{where} must be #RRGGBB")
        return v.upper()
    if t == "enum":
        vals = p.get("values") or []
        if v not in vals:
            if strict or "default" not in p:
                raise ValueError(f"{where} must be one of {vals}")
            return p["default"]
        return v
    if t == "list":
        if isinstance(v, str):
            v = [x.strip() for x in v.split("|") if x.strip()]
        if not isinstance(v, list):
            raise ValueError(f"{where} must be a list of strings")
        mx_items, mx = int(p.get("max_items", 8)), int(p.get("max", 80))
        if strict and len(v) > mx_items:
            raise ValueError(f"{where} has {len(v)} items; maximum {mx_items}")
        out = []
        for x in v[:mx_items]:
            x = " ".join(str(x).split())
            if strict and len(x) > mx:
                raise ValueError(f"{where} item '{x[:20]}…' exceeds {mx} characters")
            out.append(x[:mx])
        return out
    if t == "rows":
        if not isinstance(v, list):
            raise ValueError(f"{where} must be a list of objects")
        fields = p.get("fields") or ["label"]
        mx_items, mx = int(p.get("max_items", 6)), int(p.get("max", 60))
        if strict and len(v) > mx_items:
            raise ValueError(f"{where} has {len(v)} rows; maximum {mx_items}")
        out = []
        for row in v[:mx_items]:
            if not isinstance(row, dict):
                raise ValueError(f"{where} rows must be objects with {fields}")
            out.append({f: " ".join(str(row.get(f, "")).split())[:mx] for f in fields})
        return out
    if t == "asset":
        v = str(v).strip()
        if not v or len(v) > 500:
            raise ValueError(f"{where} must be a project asset key")
        return v
    raise ValueError(f"{where}: unsupported parameter type {t}")


def normalize_params(name, params, html=None, strict=False):
    """Validated params for a template (lenient by default; see module doc)."""
    params = dict(params or {})
    if name == "html":
        if not (html or "").strip():
            raise ValueError("template 'html' needs an html composition")
        blob = json.dumps(params, ensure_ascii=False)
        if len(blob) > 20000:
            raise ValueError("params for an html composition exceed 20 KB")
        return json.loads(blob)
    s = spec(name)
    out = {}
    unknown = [k for k in params if k not in s["params"]]
    if unknown and strict:
        raise ValueError(f"{name} has no parameter(s) {unknown}; it accepts "
                         f"{sorted(s['params'])}")
    for key, p in s["params"].items():
        if key in params and params[key] is not None:
            out[key] = _coerce(name, key, p, params[key], strict)
        elif p.get("required"):
            raise ValueError(f"{name} needs '{key}' ({p.get('hint') or p.get('type', 'str')})")
        elif "default" in p:
            out[key] = p["default"]
    return out


def check_params(name, params, html=None):
    """Strict validation for write tools; raises ValueError with guidance."""
    return normalize_params(name, params, html=html, strict=True)


def asset_params(name, params):
    """{param: storage_key} for asset-typed params of a template."""
    if name == "html":
        return {k: v for k, v in (params or {}).items()
                if k.startswith("asset_") and isinstance(v, str)}
    s = spec(name)
    return {k: params[k] for k, p in s["params"].items()
            if p.get("type") == "asset" and params.get(k)}


def build_job(item, out_w, out_h, fps, asset_locals=None, plate=None):
    """motion_engine.RenderJob for one MotionItem dict. ``plate`` is the
    measured picture under it (motion_layer.measure_plates), or None."""
    name = item["template"]
    dw, dh = motion_engine.design_size(out_w, out_h)
    full = float(item.get("full_duration_s") or (float(item["end"]) - float(item["start"])))
    span = float(item["end"]) - float(item["start"])
    phase = float(item.get("phase_s") or 0.0)
    params = dict(item.get("params") or {})
    assets = {}
    for key, storage_key in asset_params(name, params).items():
        local = (asset_locals or {}).get(storage_key)
        if local:
            ext = os.path.splitext(local)[1].lower() or ".bin"
            assets[f"{key}{ext}"] = local
            params[key] = f"{motion_engine.ORIGIN}/assets/{key}{ext}"
        else:
            params[key] = None
    body = item.get("html") if name == "html" else motion_engine.template_body(name)
    html = motion_engine.build_document(body, params=params, duration=full, fps=fps,
                                        design_w=dw, design_h=dh, plate=plate)
    box = None
    if item.get("box"):
        x0, y0, x1, y1 = item["box"]
        box = [x0 * dw, y0 * dh, x1 * dw, y1 * dh]
    return motion_engine.RenderJob(html=html, out_w=out_w, out_h=out_h, fps=fps,
                                   duration=span, t0=phase, box=box, assets=assets,
                                   label=f"motion '{item.get('id')}' ({name})")
