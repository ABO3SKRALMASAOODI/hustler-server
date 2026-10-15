"""Wave 1 integration on the backend's MCP batch path.

An MCP ``apply_edit_batch`` is written by the backend (apply_edit_batch_core),
not by the worker's ToolContext, so the pure worker rules it shares load by
path. These pin what the Wave 1 integration added there:

* black and white is a device (worker/mono_policy): a batch that makes the
  whole short flat B&W is applied with the advisory, and a mono SECTION is
  the device itself (no note);
* the keep-change re-solvers are built in (worker/hooks): the backend runs
  them without loading tools_ext.

    cd backend && python -m pytest tests -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402
from tests.test_w0c_mcp_music_gate import api              # noqa: E402,F401


def _effects_batch(effects, base=1):
    return dict(base_version=base, operation_id="w1-mono-0123456789abcdef",
                operations=[dict(action="set", layer="effects",
                                 value=effects)])


def test_a_flat_bw_mcp_batch_carries_the_device_advisory(api):
    effects = dict(api.edls[1].get("effects") or {}, grade="bw")
    result, status = video.apply_edit_batch_core(
        1, 2, _effects_batch(effects), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["effects"]["grade"] == "bw"
    assert any("B&W lands as a device" in n for n in result.get("notes", []))


def test_a_mono_section_is_the_device_and_needs_no_note(api):
    batch = dict(base_version=1, operation_id="w1-mono-section-0123456789",
                 operations=[dict(action="upsert", layer="fx", id="gw1",
                                  value=dict(id="gw1", type="grade_window",
                                             stage="world", start=0.0,
                                             end=3.4, saturation=0.0,
                                             ramp_out_f=3))])
    result, status = video.apply_edit_batch_core(1, 2, batch, origin="mcp")
    assert status == 200, result
    assert not result.get("notes")


def test_the_built_in_keep_change_resolvers_run_in_the_backend():
    old = {"keep": [[0.0, 10.0]], "motion": [{"id": "tw1"}],
           "fx": [{"id": "rg", "type": "region",
                   "region": {"shape": "glyph", "ref": "tw1"}}]}
    new = {"keep": [[0.0, 10.0]], "motion": [],
           "fx": [dict(old["fx"][0])]}
    edl, notes = video.whooks.on_keep_change(old, new)
    assert edl["fx"] == [] and any("rg" in n for n in notes)
