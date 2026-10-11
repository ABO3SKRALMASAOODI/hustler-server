"""The fixture catalogue: house-corpus stand-ins and music fixtures.

``<fixtures>/catalog.json`` (schema ``valmera.refbench.fixtures.catalog/1``,
written by the fixture acquisition pass beside ``CATALOG.md``) lists the
interim HC-A..HC-I stand-ins, the b-roll pool and the owner-named music
(roles HC-*, BROLL, HM). VFL windows stay in ``benchmarks/footage/vfl.json``;
this module only reads the catalogue, it never fetches or writes media.

    python -m benchmarks.refbench catalog list [--role HC-C]
    python -m benchmarks.refbench catalog verify      (sha256 of every file)

Internal only: catalogue items are capability-test inputs, never product
storage, git or a shipped render (each item says ``internal_only``).
"""

import json
import os

from . import fixtures_dir

SCHEMA = "valmera.refbench.fixtures.catalog/1"


def path():
    return os.path.join(fixtures_dir(), "catalog.json")


def load(p=None):
    """The parsed catalogue, or None when the fixture store has none."""
    p = p or path()
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        data = json.load(fh)
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{p}: schema {data.get('schema')!r}, "
                         f"expected {SCHEMA}")
    return data


def items(role=None, data=None):
    """Catalogue items, optionally only one role ('HC-C', 'HM', 'BROLL')."""
    data = data or load() or {}
    out = data.get("items") or []
    if role:
        out = [i for i in out if i.get("role") == role
               or role in (i.get("secondary_roles") or [])]
    return out


def item(item_id, data=None):
    for i in items(data=data):
        if i.get("id") == item_id:
            return i
    raise KeyError(f"no catalogue item {item_id!r}")


def media_path(it, data=None):
    """Absolute path of an item's primary file."""
    data = data or load() or {}
    root = os.path.expanduser(data.get("root") or fixtures_dir())
    return os.path.join(root, it["primary"]["path"])


def best(role, data=None):
    """The ids the catalogue marks best for a role (its first items when
    none are marked)."""
    data = data or load() or {}
    r = (data.get("roles") or {}).get(role) or {}
    return list(r.get("best") or r.get("items") or [])


def verify(data=None):
    """{item id: "ok" | "missing" | "sha mismatch"} over every primary and
    variant file."""
    from .vfl import sha256_file
    data = data or load() or {}
    root = os.path.expanduser(data.get("root") or fixtures_dir())
    out = {}
    for it in items(data=data):
        files = [it["primary"]] + list(it.get("variants") or [])
        res = "ok"
        for f in files:
            p = os.path.join(root, f["path"])
            if not os.path.exists(p):
                res = "missing"
                break
            if f.get("sha256") and sha256_file(p) != f["sha256"]:
                res = "sha mismatch"
                break
        out[it["id"]] = res
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="refbench catalog")
    ap.add_argument("cmd", choices=["list", "verify"])
    ap.add_argument("--role", default=None)
    a = ap.parse_args(argv)
    data = load()
    if data is None:
        print(f"no catalogue at {path()}")
        return 1
    if a.cmd == "verify":
        res = verify(data)
        bad = {k: v for k, v in res.items() if v != "ok"}
        print(f"{len(res) - len(bad)}/{len(res)} items ok")
        for k, v in sorted(bad.items()):
            print(f"  {k}: {v}")
        return 1 if bad else 0
    for it in items(a.role, data):
        pr = it.get("primary") or {}
        print(f"{it['id']:<62} {it.get('status', ''):<20} "
              f"{pr.get('duration_s', '')}s")
    return 0
