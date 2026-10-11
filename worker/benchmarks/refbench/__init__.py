"""Refbench: reference-graded acceptance for every engine track (ROADMAP §4.5,
worker/devices/INTERFACES.md §8).

    spec      case YAML schema (§8.1) and validation
    vfl       the Valmera Footage Library: windows, fixture files, hashes
    labels    label store + CVAT import/export
    engine    engine-mode cases: window EDL + ops -> local render
    standing  the standing checks every case gets
    boards    2-up stills, side-by-side clips, strips
    judges    the three lens judges' prompt packets and verdict merge
    verdict   verdict JSON (§8.2)
    runner    CLI: python -m benchmarks.refbench <command>
    at0d2     detector precision/recall against hand labels (AT-0D-2)

Internal only: references and fixtures never enter git, product storage or
a showcase.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(os.path.dirname(HERE))
CASES_DIR = os.path.join(HERE, "cases")
JUDGES_DIR = os.path.join(HERE, "judges")
VFL_JSON = os.path.join(os.path.dirname(HERE), "footage", "vfl.json")


def fixtures_dir():
    return os.environ.get("VALMERA_FIXTURES_DIR") or os.path.expanduser(
        "~/Documents/Valmera/.valmera/fixtures")


def references_dir():
    return os.environ.get("VALMERA_REFERENCES_DIR") or os.path.expanduser(
        "~/.openclaw/workspace/references")


def out_dir():
    """Where verdicts and boards go: $VALMERA_REFBENCH_OUT, else a scratch
    directory under the system temp (never git, never product storage)."""
    d = os.environ.get("VALMERA_REFBENCH_OUT")
    if not d:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "valmera-refbench")
    os.makedirs(d, exist_ok=True)
    return d


def reference_path(ref):
    """The reference file for '22' / '22_' / 22, or None."""
    prefix = f"{int(str(ref).split('_')[0]):02d}_"
    try:
        for name in sorted(os.listdir(references_dir())):
            if name.startswith(prefix) and name.endswith(".mp4"):
                return os.path.join(references_dir(), name)
    except (OSError, ValueError):
        pass
    return None
