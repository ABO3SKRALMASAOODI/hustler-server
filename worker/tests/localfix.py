"""Local-only test inputs: Valmera Footage Library fixtures and references.

worker/devices/INTERFACES.md §8. CI has neither, so a test that needs them
is marked ``@local_only(path, ...)`` and skips there. Fixture files never go
into git; references are internal comparison inputs only (never uploaded,
published, or written to product storage).

    from localfix import fixture, local_only

    @local_only(fixture("V02", "thiel_flying_cars.mp4"))
    def test_...():
"""

import os

import pytest

FIXTURES_DIR = os.environ.get("VALMERA_FIXTURES_DIR") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/fixtures")
REFERENCES_DIR = os.environ.get("VALMERA_REFERENCES_DIR") or \
    os.path.expanduser("~/.openclaw/workspace/references")


def fixture(*parts):
    return os.path.join(FIXTURES_DIR, *parts)


def reference(prefix):
    """The reference file whose name starts with ``prefix`` ("22_"), or a
    path that does not exist."""
    try:
        for name in sorted(os.listdir(REFERENCES_DIR)):
            if name.startswith(prefix) and name.endswith(".mp4"):
                return os.path.join(REFERENCES_DIR, name)
    except OSError:
        pass
    return os.path.join(REFERENCES_DIR, f"{prefix}missing.mp4")


def local_only(*paths):
    missing = [p for p in paths if not os.path.exists(p)]
    return pytest.mark.skipif(
        bool(missing) or not paths,
        reason="local-only: needs " + ", ".join(missing or ["fixtures"]))
