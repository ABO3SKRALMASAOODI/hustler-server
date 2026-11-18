"""EDIT-CORE golden fixes (16 Nov 2026): the three fix branches land together.

A (signal) added the noise_clean layer, C (caps) the export layer; both
grew the same enums and lists. One batch that cleans the noise and sets a
DVD size must keep both, both must reach every surface, and neither may
change the picture's identity (an audio or file-size change never re-renders
the picture)."""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import agent_tools                                         # noqa: E402
import edit_batch                                          # noqa: E402
import render_plan                                         # noqa: E402
import schemas                                             # noqa: E402


def _apply(edl, ops, dur):
    out = edit_batch.apply_batch(copy.deepcopy(edl), ops, dur, set())
    return out if isinstance(out, dict) and "keep" in out else out[0]


def test_one_batch_cleans_the_noise_and_sets_the_dvd_size():
    dur = 60.0
    base = schemas.validate_edl({"keep": [[0.0, dur]]}, dur).model_dump()
    edl = _apply(base, [
        {"action": "set", "layer": "noise_clean", "value": 0.8},
        {"action": "upsert", "layer": "export",
         "value": {"max_size_mb": 4500}},
    ], dur)
    model = schemas.validate_edl(edl, dur)
    assert model.noise_clean.strength == 0.8
    assert model.export.max_size_mb == 4500.0
    # both are sound/file settings: the picture is the same picture
    assert render_plan.picture_signature(edl) == \
        render_plan.picture_signature(base)
    # but the program is a different program
    assert schemas.edl_signature(edl) != schemas.edl_signature(base)


def test_both_layers_reach_v1_mcp_and_harness2():
    schema = agent_tools.TOOLS["apply_edit_batch"][2]
    layers = schema["operations"]["items"]["properties"]["layer"]["enum"]
    assert {"noise_clean", "export"} <= set(layers)
    assert {"noise_clean", "export"} <= set(edit_batch.LAYERS)
    assert "clean_noise" in agent_tools.TOOLS
    assert "set_export_settings" in agent_tools.TOOLS
    from harness2 import ops, schema_ops
    assert "export" in ops.HANDLERS and "audio" in ops.HANDLERS
    assert "export" in schema_ops.op_names()
