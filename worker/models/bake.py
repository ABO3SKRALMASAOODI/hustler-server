"""Model baker: pinned, licence-checked model files for the executor images.

ROADMAP §3 rule A8 / §7, worker/devices/INTERFACES.md §1 (track 0A). Every
model a lane runs is described by one ``models/<name>.json`` and pinned in
``models.lock`` (url, sha256, bytes, licence). Images bake them at BUILD
time (never a runtime download), exactly as the batch image always did for
RVM. This lives in ``models/`` and not ``tools/`` because the image build
context excludes ``tools/`` (worker/.dockerignore).

    python models/bake.py --check                 # CI: specs, lock, licences, pins (no network)
    python models/bake.py --lock                  # dev: rewrite models.lock from the specs
    python models/bake.py --image batch --dest /app/models
                                                  # Dockerfile step: download + verify

A build REFUSES:
  * a model whose licence is not on the commercial-use allowlist (any
    "NC"/non-commercial/AGPL/research-only licence, ROADMAP §7 exclusions);
  * a GPL model not marked ``server_side_only`` (we run it, never ship it);
  * a model whose spec and models.lock disagree, or that has no sha256;
  * a download whose sha256 or size differs from the pin;
  * an unpinned line in ``requirements.d/<image>.txt``.

Standard library only: it runs before anything else is importable.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_NAME = "models.lock"
LOCK_VERSION = 1
IMAGES = ("interactive", "batch", "agent", "mcp", "shorts", "vision")

# Commercial use allowed (ROADMAP §7). GPL family only with server_side_only.
ALLOWED_LICENCES = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib",
    "CC0-1.0", "CC-BY-4.0", "public-domain", "OFL-1.1", "MPL-2.0",
    "GPL-3.0", "LGPL-2.1", "LGPL-3.0",
})
COPYLEFT_SERVER_ONLY = frozenset({"GPL-3.0", "LGPL-2.1", "LGPL-3.0"})
DENIED_MARKERS = ("NC", "NONCOMMERCIAL", "NON-COMMERCIAL", "AGPL",
                  "RESEARCH", "OPENRAIL")
# Official hosts only (ROADMAP §7). The sha256 pin is the real guard; this
# keeps a typo'd mirror from ever entering a spec.
ALLOWED_HOSTS = frozenset({
    "github.com", "raw.githubusercontent.com", "huggingface.co",
    "storage.googleapis.com",
})
REQUIRED = ("name", "model_id", "model_version", "kinds", "file", "url",
            "source", "licence", "sha256", "bytes", "images")


class BakeError(RuntimeError):
    pass


def lock_path(models_dir=HERE):
    return os.path.join(os.path.dirname(os.path.abspath(models_dir)),
                        LOCK_NAME)


def requirements_dir(models_dir=HERE):
    return os.path.join(os.path.dirname(os.path.abspath(models_dir)),
                        "requirements.d")


def load_specs(models_dir=HERE):
    specs = {}
    for fname in sorted(os.listdir(models_dir)):
        if not fname.endswith(".json") or fname.startswith("."):
            continue
        with open(os.path.join(models_dir, fname), encoding="utf-8") as f:
            spec = json.load(f)
        name = spec.get("name")
        if name != fname[:-5]:
            raise BakeError(f"{fname}: name must equal the file stem")
        specs[name] = spec
    return specs


def licence_problem(spec):
    """None when the licence permits our commercial, server-side use."""
    lic = str(spec.get("licence") or "").strip()
    upper = lic.upper()
    if not lic:
        return "no licence recorded"
    if any(m in upper.replace(" ", "-") for m in DENIED_MARKERS):
        return f"licence {lic} is not commercial-use (ROADMAP §7 exclusion)"
    if lic not in ALLOWED_LICENCES:
        return f"licence {lic} is not on the commercial-use allowlist"
    if spec.get("commercial_use") is False:
        return "spec says commercial use is not allowed"
    if lic in COPYLEFT_SERVER_ONLY and spec.get("server_side_only") is not True:
        return (f"{lic} model must be marked server_side_only (run on our "
                "servers, never distributed)")
    return None


def spec_problems(spec):
    out = [f"missing {k}" for k in REQUIRED if spec.get(k) in (None, "", [])]
    sha = str(spec.get("sha256") or "")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        out.append("sha256 must be 64 lowercase hex characters")
    url = urllib.parse.urlparse(str(spec.get("url") or ""))
    if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS:
        out.append(f"url must be https on an official host {sorted(ALLOWED_HOSTS)}")
    if "/main/" in url.path or "/master/" in url.path:
        out.append("url must be pinned to a commit or release, not a branch")
    try:
        if int(spec.get("bytes") or 0) <= 0:
            out.append("bytes must be positive")
    except (TypeError, ValueError):
        out.append("bytes must be an integer")
    bad = [i for i in spec.get("images") or () if i not in IMAGES]
    if bad:
        out.append(f"unknown images {bad}")
    if os.path.basename(str(spec.get("file") or "")) != spec.get("file"):
        out.append("file must be a bare file name")
    problem = licence_problem(spec)
    if problem:
        out.append(problem)
    return out


def lock_entry(spec):
    return {k: spec[k] for k in ("model_id", "model_version", "file", "url",
                                 "sha256", "bytes", "licence")}


def build_lock(specs):
    return {"lock_version": LOCK_VERSION,
            "generated_by": "worker/models/bake.py --lock",
            "models": {n: lock_entry(s) for n, s in sorted(specs.items())}}


def read_lock(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def requirement_problems(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for no, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-"):
                out.append(f"{os.path.basename(path)}:{no}: options are not allowed")
            elif "==" not in line or any(op in line for op in (">", "<", "~=", "*")):
                out.append(f"{os.path.basename(path)}:{no}: {line!r} is not pinned with ==")
    return out


def check(models_dir=HERE):
    """Every problem with the specs, the lock and requirements.d (no
    network). An empty list means the image build may proceed."""
    problems = []
    try:
        specs = load_specs(models_dir)
    except (OSError, ValueError, BakeError) as e:
        return [f"specs unreadable: {e}"]
    for name, spec in specs.items():
        problems += [f"{name}: {p}" for p in spec_problems(spec)]
    try:
        lock = read_lock(lock_path(models_dir))
    except (OSError, ValueError) as e:
        problems.append(f"{LOCK_NAME} unreadable: {e}")
    else:
        if lock.get("lock_version") != LOCK_VERSION:
            problems.append(f"{LOCK_NAME}: lock_version must be {LOCK_VERSION}")
        locked = lock.get("models") or {}
        for name, spec in specs.items():
            if locked.get(name) != lock_entry(spec):
                problems.append(f"{name}: spec and {LOCK_NAME} disagree "
                                "(run bake.py --lock and review the diff)")
        for name in sorted(set(locked) - set(specs)):
            problems.append(f"{name}: in {LOCK_NAME} but has no spec")
    rdir = requirements_dir(models_dir)
    if os.path.isdir(rdir):
        for fname in sorted(os.listdir(rdir)):
            if fname.endswith(".txt"):
                problems += requirement_problems(os.path.join(rdir, fname))
    return problems


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fetch(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "valmera-bake/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(dest, "wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            out.write(block)


def bake_one(spec, dest_dir, fetch=_fetch, attempts=3):
    """Download one model into dest_dir and verify it against its pin.
    An existing file with the pinned sha256 is kept; one that differs fails
    the build (never silently replaced)."""
    target = os.path.join(dest_dir, spec["file"])
    if os.path.exists(target):
        got = sha256_file(target)
        if got != spec["sha256"]:
            raise BakeError(f"{spec['name']}: {target} exists with sha256 "
                            f"{got}, pinned {spec['sha256']}")
        return target, "present"
    os.makedirs(dest_dir, exist_ok=True)
    last = None
    for attempt in range(1, attempts + 1):
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".bake-")
        os.close(fd)
        try:
            fetch(spec["url"], tmp)
            size = os.path.getsize(tmp)
            got = sha256_file(tmp)
            if size != int(spec["bytes"]) or got != spec["sha256"]:
                raise BakeError(f"{spec['name']}: downloaded {size} bytes "
                                f"sha256 {got}; pinned {spec['bytes']} bytes "
                                f"sha256 {spec['sha256']}")
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
            return target, "downloaded"
        except BakeError:
            raise
        except Exception as e:  # noqa: BLE001 — network: retry, then fail loudly
            last = e
            time.sleep(min(10, 2 * attempt))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    raise BakeError(f"{spec['name']}: download failed after {attempts} "
                    f"attempts: {last}")


def bake(image, dest_dir, models_dir=HERE, fetch=_fetch, install=None):
    if image not in IMAGES:
        raise BakeError(f"unknown image {image!r}; one of {IMAGES}")
    problems = check(models_dir)
    if problems:
        raise BakeError("refusing to bake:\n  " + "\n  ".join(problems))
    done = []
    for name, spec in sorted(load_specs(models_dir).items()):
        if image in spec["images"]:
            path, how = bake_one(spec, dest_dir, fetch=fetch)
            done.append((name, path, how))
    req = os.path.join(requirements_dir(models_dir), f"{image}.txt")
    if os.path.exists(req) and _has_requirements(req):
        (install or _pip_install)(req)
        done.append((f"requirements.d/{image}.txt", req, "installed"))
    return done


def _has_requirements(path):
    with open(path, encoding="utf-8") as f:
        return any(line.split("#", 1)[0].strip() for line in f)


def _pip_install(path):
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "--no-cache-dir", "-r", path])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--image", choices=IMAGES)
    ap.add_argument("--dest", default=HERE)
    ap.add_argument("--models-dir", default=HERE)
    args = ap.parse_args(argv)
    if args.lock:
        lock = build_lock(load_specs(args.models_dir))
        with open(lock_path(args.models_dir), "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {lock_path(args.models_dir)}")
        return 0
    if args.check or not args.image:
        problems = check(args.models_dir)
        for p in problems:
            print(f"bake: {p}", file=sys.stderr)
        print("bake: ok" if not problems else f"bake: {len(problems)} problem(s)")
        return 1 if problems else 0
    try:
        for name, path, how in bake(args.image, args.dest, args.models_dir):
            print(f"bake[{args.image}]: {name} {how} -> {path}")
    except BakeError as e:
        print(f"bake: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
