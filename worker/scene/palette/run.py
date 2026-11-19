"""python -m scene.palette.run apply --l1 L1 --work DIR --out L1_OUT [--asks ASKS.json] [--log LOG.json]
   python -m scene.palette.run menu [--family type,behind,hud]

Lays the scene's ``devices`` and ``alien`` asks (or a JSON list in a file) by layer, each with its fallback chain,
and writes the scene the engine draws. Anything a device makes (a matte, a relit take) goes inside ``DIR``."""
import argparse
import json
import sys

from . import apply, menu


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scene.palette.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("menu")
    m.add_argument("--family")
    a = sub.add_parser("apply")
    a.add_argument("--l1", required=True)
    a.add_argument("--work", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--asks")
    a.add_argument("--log")
    args = ap.parse_args(argv)
    if args.cmd == "menu":
        print(menu(args.family.split(",") if args.family else None))
        return 0
    l1 = json.load(open(args.l1, encoding="utf-8"))
    asks = json.load(open(args.asks, encoding="utf-8")) if args.asks else None
    out, log = apply(l1, args.work, asks)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f)
    if args.log:
        with open(args.log, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    for r in log:
        fell = f" (asked {r['asked']})" if r.get("asked") != r.get("dev") else ""
        print(("laid  " if r["done"] else "left  ") + f"{r.get('dev') or r.get('asked')} {r['id']}{fell}" + ("" if r["done"] else f": {r.get('why')}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
