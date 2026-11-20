"""python -m scene.palette.run apply --l1 L1 --work DIR --out L1_OUT [--asks ASKS.json] [--log LOG.json]
   python -m scene.palette.run menu

Lays the asks a scene carries (``devices``, then the older ``alien``) in layer order and writes the scene to draw.
What a device makes (a matte, an overlay) goes inside ``DIR/palette/``; the log says what landed for every ask."""
import argparse
import json
import sys

from . import apply, menu


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scene.palette.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("menu")
    a = sub.add_parser("apply")
    a.add_argument("--l1", required=True)
    a.add_argument("--work", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--asks")
    a.add_argument("--log")
    args = ap.parse_args(argv)
    if args.cmd == "menu":
        print(menu())
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
        what = r.get("landed") if r["done"] else "left: " + "; ".join(f"{t['dev']}: {t['why']}" for t in r.get("tried") or [])
        print(f"{r['id']} {r['asked']} -> {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
