"""The owner-ear sound approval flow (ROADMAP 4F; the 0C queue).

Owner rule: sound effects come only from recordings the OWNER approved by
ear. A user who asks for a sound the library lacks gets one clause and a
filed request (sound_requests). This module is the other half — the
owner's desk — and it is deliberately NOT a tool: no agent, MCP client or
department verb can reach it (tests pin that no registered tool imports or
wraps it). Approving takes the owner's own typed attestation that they
listened.

    python -m sound_approval stage <request id> <file> --source <url>
        --license CC0 --author <name> [--title ...]
    python -m sound_approval approve <request id> <candidate> --role <role>
        --use "<when to use it>"          (asks the owner to type the line)
    python -m sound_approval decline <request id> [--note ...]

``stage`` copies a CC0 candidate into the audition folder (outside the
library) and measures it; ``approve`` moves it into worker/sound_library/
with a manifest row and its licence trail (the commit that ships it is the
owner's), and marks the request approved when a database is given.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

AUDITION_DIR = os.environ.get(
    "VALMERA_SOUND_AUDITION",
    os.path.expanduser("~/Documents/Valmera/.valmera/sound_audition"))
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sound_library")
LICENCES = ("CC0 1.0",)       # the library's licence policy: CC0 only
_ID = re.compile(r"^[a-z0-9_]{2,40}$")


class ApprovalError(Exception):
    pass


def attestation(rid):
    """The exact line the owner types: approval is a person's act."""
    return f"I listened to {rid} and approve it"


def _canon(licence):
    t = str(licence or "").strip().lower()
    if t in ("cc0", "cc0 1.0", "cc0-1.0", "public domain dedication"):
        return "CC0 1.0"
    return None


def stage(rid, path, *, source_url, licence, author, title=None,
          audition_dir=None):
    """Copy a candidate recording into the audition folder; returns its
    candidate id. CC0 only (the library's trail); the file is converted to
    FLAC 48 kHz like every library recording."""
    lic = _canon(licence)
    if lic not in LICENCES:
        raise ApprovalError("the library takes CC0 recordings only")
    if not source_url:
        raise ApprovalError("a candidate needs its source page (the trail)")
    d = os.path.join(audition_dir or AUDITION_DIR, rid)
    os.makedirs(d, exist_ok=True)
    n = len([f for f in os.listdir(d) if f.endswith(".flac")]) + 1
    cid = f"c{n}"
    out = os.path.join(d, f"{cid}.flac")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-ac", "2",
                    "-ar", "48000", "-c:a", "flac", out], check=True)
    meta = {"request": rid, "candidate": cid, "source_url": source_url,
            "license": lic, "author": author, "title": title,
            "staged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        import sfx_mix
        hit, hp = sfx_mix.measure_recording(out)
        meta.update(hit_lufs=hit, hit_lufs_hp150=hp)
    except Exception:  # noqa: BLE001 — measured again on approval
        pass
    with open(os.path.join(d, f"{cid}.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return cid


def approve(rid, cid, *, role, use, attest, db=None, audition_dir=None,
            lib_dir=None):
    """Move an auditioned candidate into the library. ``attest`` must be
    exactly ``attestation(rid)`` — typed by the owner at the CLI."""
    if attest != attestation(rid):
        raise ApprovalError("approval needs the owner's typed attestation")
    if not _ID.match(str(role or "")):
        raise ApprovalError("role is a short lowercase word (whoosh, "
                            "impact, ...)")
    if not str(use or "").strip():
        raise ApprovalError("say when to use it (the manifest's 'use')")
    d = os.path.join(audition_dir or AUDITION_DIR, rid)
    src = os.path.join(d, f"{cid}.flac")
    meta_path = os.path.join(d, f"{cid}.json")
    if not (os.path.exists(src) and os.path.exists(meta_path)):
        raise ApprovalError(f"no staged candidate {cid} for {rid}")
    meta = json.load(open(meta_path))
    lib = lib_dir or LIB_DIR
    man_path = os.path.join(lib, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else []
    n = 1 + sum(1 for r in manifest if r.get("role") == role)
    rec_id = f"{role}_{n}"
    while any(r.get("id") == rec_id for r in manifest):
        n += 1
        rec_id = f"{role}_{n}"
    dst = os.path.join(lib, f"{rec_id}.flac")
    shutil.copy(src, dst)
    import hashlib
    with open(dst, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()[:16]
    row = {"id": rec_id, "role": role, "use": str(use).strip()[:300],
           "file": os.path.basename(dst), "sha": sha,
           "title": meta.get("title"), "author": meta.get("author"),
           "source_url": meta.get("source_url"),
           "license": meta.get("license"), "approved_request": rid}
    for k in ("peak_s", "hit_lufs", "hit_lufs_hp150", "duration_s",
              "max_s"):
        if meta.get(k) is not None:
            row[k] = meta[k]
    manifest.append(row)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    if db is not None:
        import sound_requests
        sound_requests.set_status(db, rid, "approved",
                                  note=f"joined the library as {rec_id}")
    return rec_id


def decline(rid, *, db, note=None):
    import sound_requests
    return sound_requests.set_status(db, rid, "declined", note=note)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="sound_approval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("rid")
    s.add_argument("path")
    s.add_argument("--source", required=True)
    s.add_argument("--license", required=True)
    s.add_argument("--author", required=True)
    s.add_argument("--title")
    a = sub.add_parser("approve")
    a.add_argument("rid")
    a.add_argument("cid")
    a.add_argument("--role", required=True)
    a.add_argument("--use", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "stage":
        print(stage(args.rid, args.path, source_url=args.source,
                    licence=args.license, author=args.author,
                    title=args.title))
        return 0
    if args.cmd == "approve":
        if not sys.stdin.isatty():
            print("approval is typed by the owner at a terminal",
                  file=sys.stderr)
            return 2
        line = input(f"Type exactly: {attestation(args.rid)}\n> ")
        print(approve(args.rid, args.cid, role=args.role, use=args.use,
                      attest=line))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
