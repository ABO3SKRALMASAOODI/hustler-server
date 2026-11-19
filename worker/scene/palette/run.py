"""python -m scene.palette.run apply --l1 L1 --work DIR --out L1_OUT [--asks ASKS.json] [--log LOG.json]
   python -m scene.palette.run menu

Lays the scene's ``devices`` (and its ``alien`` asks) into plain scene items before the engine draws it. Anything
made on the way (the take's matte, a still's depth layers) is written inside ``DIR/palette/``. The log lists each
ask, the device that landed it (after any fallback) and the landings the sound workflow may amplify (``events``)."""
import argparse
import json
import sys

from . import apply, events, menu


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
    out["device_events"] = events(log)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f)
    if args.log:
        with open(args.log, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    for r in log:
        tried = "".join(f" [{t['dev']}: {t['why']}]" for t in r.get("tried") or [])
        print(("done  " if r.get("done") else "bare  ") + f"{r.get('asked')}->{r.get('dev')} {r['id']}{tried}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
