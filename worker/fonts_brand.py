"""Brand fonts and the font licence ledger (Wave 2 track 2B).

ROADMAP 2B: "upload validation, fontTools sanitise and subset, licence
attestation". Every face the typesetter sets must be one we may embed in a
rendered video:

* the bundled faces are OFL-1.1 or Apache-2.0 (``ledger()`` checks every
  file in motion/fonts and fonts/ has its licence text and a manifest or
  registry entry — the 2B-6 licence test);
* a user's brand font is accepted only when (a) its OS/2 ``fsType``
  permits embedding (installable, editable or print-and-preview; never
  "restricted licence"), (b) the user states the licence they hold and
  attests it, (c) it parses and carries Latin glyphs. It is then sanitised
  (hinting and private tables dropped) and subset to Latin + punctuation,
  which is what we store and render.

Storing a brand font as a project asset needs an asset kind the database
does not have yet (assets.kind: no 'font'); until the owner adds one,
``import_for_project`` validates and reports, and the sanitised file is
kept in the project's workdir for this session (open item, WAVE2_RELEASE).
"""

import hashlib
import json
import os

ALLOWED_LICENCES = ("OFL-1.1", "Apache-2.0", "commercial-licensed",
                    "own-design")
NEED_ATTEST = ("commercial-licensed", "own-design")
LATIN = ("U+0020-007E,U+00A0-00FF,U+0100-017F,U+2010-2027,U+2030-203A,"
         "U+20AC,U+2122,U+2212")
DROP_TABLES = ("DSIG", "fpgm", "prep", "cvt ", "hdmx", "VDMX", "LTSH",
               "PCLT", "kern_old")
HERE = os.path.dirname(os.path.abspath(__file__))


def embedding(font):
    """'installable' | 'restricted' | 'preview' | 'editable' from OS/2
    fsType (bits 1, 2, 3)."""
    try:
        fs = int(font["OS/2"].fsType)
    except Exception:  # noqa: BLE001
        return "installable"
    if fs & 0x0002 and not fs & 0x000C:
        return "restricted"
    if fs & 0x0008:
        return "editable"
    if fs & 0x0004:
        return "preview"
    return "installable"


def validate(path, licence, attest=False, max_mb=8.0):
    """(ok, facts | reason) for a font file a user supplied."""
    from fontTools.ttLib import TTFont
    if licence not in ALLOWED_LICENCES:
        return False, (f"licence must be one of {', '.join(ALLOWED_LICENCES)}"
                       " (the user's statement of their right to use it)")
    if licence in NEED_ATTEST and not attest:
        return False, ("a commercial or own-design font needs attest=true: "
                       "the user confirms they hold the right to embed it "
                       "in videos")
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, "the font file is missing"
    if size > max_mb * 1024 * 1024:
        return False, f"the font is {size / 1048576:.1f} MB (max {max_mb:g})"
    try:
        f = TTFont(path, lazy=False)
    except Exception as e:  # noqa: BLE001
        return False, f"not a TrueType/OpenType font ({str(e)[:80]})"
    emb = embedding(f)
    if emb == "restricted":
        return False, ("the font's own licence bits forbid embedding "
                       "(OS/2 fsType: restricted licence)")
    cmap = f.getBestCmap() or {}
    latin = sum(1 for c in range(0x41, 0x5B) if c in cmap) + \
        sum(1 for c in range(0x61, 0x7B) if c in cmap)
    if latin < 40:
        return False, "the font lacks the Latin alphabet"
    name = f["name"]
    fam = name.getDebugName(16) or name.getDebugName(1) or "Brand"
    return True, {"family": fam, "embedding": emb, "glyphs": len(
        f.getGlyphOrder()), "variable": "fvar" in f, "bytes": size,
        "licence": licence}


def sanitise(path, out_path):
    """Drop hinting and private tables, subset to Latin + punctuation, keep
    layout features and variations. Returns the output sha256."""
    from fontTools import subset
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.hinting = False
    opts.drop_tables += list(DROP_TABLES)
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    f = subset.load_font(path, opts)
    sub = subset.Subsetter(opts)
    uni = []
    for part in LATIN.split(","):
        a, _sep, b = part.replace("U+", "").partition("-")
        lo = int(a, 16)
        hi = int(b, 16) if b else lo
        uni.extend(range(lo, hi + 1))
    sub.populate(unicodes=uni)
    sub.subset(f)
    subset.save_font(f, out_path, opts)
    with open(out_path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def ledger():
    """The bundled faces with their licences: [{file, family, licence,
    licence_file, ok}] — every motion/fonts face must be OFL or Apache with
    its licence text present (the 2B-6 test)."""
    out = []
    mdir = os.path.join(HERE, "motion", "fonts")
    lic_dir = os.path.join(mdir, "licenses")
    try:
        man = json.load(open(os.path.join(mdir, "manifest.json")))
    except (OSError, ValueError):
        man = {"faces": []}
    listed = {f["file"]: f for f in man.get("faces") or []}
    lic_files = set(os.listdir(lic_dir)) if os.path.isdir(lic_dir) else set()
    for fn in sorted(os.listdir(mdir)):
        if not fn.lower().endswith((".ttf", ".otf")):
            continue
        entry = listed.get(fn)
        if entry:
            ok = (entry.get("licence") in ("OFL-1.1", "Apache-2.0") and
                  entry.get("licence_file") in lic_files)
            out.append({"file": fn, "family": entry["family"],
                        "licence": entry["licence"],
                        "licence_file": entry["licence_file"], "ok": ok})
        else:
            stem = fn.split("-")[0].split(".")[0]
            hit = sorted(x for x in lic_files if stem.lower() in x.lower())
            out.append({"file": fn, "family": stem, "licence": (
                "Apache-2.0" if any("Apache" in h for h in hit) else
                "OFL-1.1" if hit else None),
                "licence_file": hit[0] if hit else None, "ok": bool(hit)})
    return out


def import_for_project(ctx, asset_key, licence, family=None, attest=False):
    """The import_font verb: download, validate, sanitise; report."""
    import config
    import storage
    work = os.path.join(getattr(ctx, "workdir", None) or "/tmp", "brandfonts")
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "upload" + (os.path.splitext(str(asset_key))[1]
                                          or ".ttf"))
    try:
        storage.download_to(str(asset_key), src)
    except Exception as e:  # noqa: BLE001
        return f"REJECTED: could not read {asset_key} ({str(e)[:100]})."
    ok, facts = validate(src, licence, attest,
                         float(getattr(config, "BRAND_FONT_MAX_MB", 8)))
    if not ok:
        return f"REJECTED: {facts}."
    out = os.path.join(work, "brand.ttf")
    sha = sanitise(src, out)
    return (f"Font accepted: {family or facts['family']} "
            f"({facts['embedding']} embedding, {facts['licence']}, "
            f"{facts['glyphs']} glyphs, sanitised and subset, sha "
            f"{sha[:12]}). Setting it in typeset needs a font asset kind "
            "the product does not have yet; until then use a bundled voice "
            "whose face is closest.")
