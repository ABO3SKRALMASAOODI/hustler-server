"""Probe-lane child for the Wave 0 integration fault matrix.

tests/w0b_probe.py renders cases through the real renderer in a child with
``VALMERA_FX_PROBE=1``; this wrapper first points the device resolver at a
fixture card set (``W0I_CARDS_DIR``) so device items resolve against real
cards in the child, then hands over to w0b_probe.main.

    python w0i_probe.py <spec.json> <out.json>
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def main(spec, out):
    import devices
    cards = os.environ.get("W0I_CARDS_DIR", "").strip()
    if cards:
        devices.CARDS_DIR = cards
        devices._cached_cards.cache_clear()
    import w0b_probe
    w0b_probe.main(spec, out)


def run(cases, workroot, cards_dir, sidecar_dir, timeout=1800):
    """Parent side: ``cases`` in a probe-lane child over ``cards_dir`` and a
    local sidecar store at ``sidecar_dir``; returns w0b_probe's results."""
    import json
    import subprocess
    os.makedirs(workroot, exist_ok=True)
    spec = os.path.join(workroot, "spec.json")
    out = os.path.join(workroot, "results.json")
    with open(spec, "w") as f:
        json.dump({"workroot": workroot, "cases": cases}, f)
    worker = os.path.dirname(HERE)
    env = dict(os.environ, VALMERA_FX_PROBE="1", W0I_CARDS_DIR=cards_dir,
               VALMERA_SIDECAR_DIR=sidecar_dir)
    env.pop("VALMERA_FAULTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [worker, os.path.dirname(worker)]
        + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, os.path.abspath(__file__), spec, out],
                       cwd=worker, env=env, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"probe child failed:\n{r.stdout[-3000:]}\n"
                           f"{r.stderr[-3000:]}")
    with open(out) as f:
        return json.load(f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
