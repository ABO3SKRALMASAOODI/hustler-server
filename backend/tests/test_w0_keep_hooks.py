"""Wave 0 integration: a Studio keep change runs the device re-solvers.

The Studio's UI ops re-anchor program items through the shared
worker/timeline.remap_program_items (``_reanchor_after_op``); devices store
intent, so the same keep change then runs worker/hooks.on_keep_change, as
the agent's keep writes do (INTERFACES.md §6.4). An EDL with no fx is the
old pass exactly.

    cd backend && python -m pytest tests -q
"""

import copy
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402


def _edl(fx=None):
    edl = {"keep": [[0.0, 10.0], [12.0, 14.0]], "music": [],
           "effects": {"zooms": []}}
    if fx is not None:
        edl["fx"] = fx
    return edl


def test_a_keep_change_runs_the_device_re_solvers():
    seen = []

    def resolver(old_edl, new_edl, old_tl, new_tl):
        seen.append(old_edl["keep"])
        new_edl["fx"][0]["purpose"] = "re-solved"
        return new_edl, ["1 device re-solved"]
    old = _edl([{"id": "w", "type": "subject", "stage": "world",
                 "start": 7.0, "end": 8.0}])
    new = copy.deepcopy(old)
    new["keep"] = [[0.0, 4.0], [6.0, 10.0], [12.0, 14.0]]
    video.whooks.register("keep_change", resolver)
    try:
        out, desc = video._reanchor_after_op(old, new, "cut")
    finally:
        video.whooks.unregister("keep_change", resolver)
    assert out["fx"][0]["purpose"] == "re-solved"
    assert "1 device re-solved" in desc and len(seen) == 1


def test_no_fx_is_the_old_pass_exactly():
    calls = []

    def resolver(*a):
        calls.append(a)
        return a[1], []
    old = _edl()
    new = copy.deepcopy(old)
    new["keep"] = [[0.0, 4.0], [6.0, 10.0], [12.0, 14.0]]
    video.whooks.register("keep_change", resolver)
    try:
        out, _desc = video._reanchor_after_op(old, new, "cut")
    finally:
        video.whooks.unregister("keep_change", resolver)
    assert calls == [] and "fx" not in out
