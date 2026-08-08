"""sound_design_pass generates its sounds — the pack is gone, the pass
lives on.

The director pass (whoosh on junctions, impact on the strongest word,
riser into the energy rise) was built on the bundled sfx pack and died
with it: catalog empty -> tool hidden. Now the MOMENT-FINDING is unchanged
and the SOUNDS are generated per edit from a fixed palette, so the pass
gates on the sound provider instead. These tests drive one pass over a
two-segment stub edit and pin: a junction whoosh is found, the sound is
generated + uploaded under generated_sfx/, the EDL write discloses the
placement, and a failed generation drops its placement instead of failing
the pass.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402


class _Ctx:
    has_main_video = True
    project_id = 7
    index = {"words": []}

    def __init__(self, tmpdir):
        self.workdir = str(tmpdir)
        self.sfx_generated = []
        self._edl = {"keep": [[0.0, 10.0], [20.0, 30.0]], "sfx": []}
        self.written = None

    def latest_edl(self):
        return {"json": dict(self._edl)}

    def write_edl(self, edl, msg):
        self.written = (edl, msg)
        return f"EDL v1 -> v2: {msg}"


def _arm(monkeypatch, gen_ok=True):
    monkeypatch.setattr(agent_tools.eleven, "sound_gen_available",
                        lambda: True)
    monkeypatch.setattr(agent_tools, "_gen_budget_reject",
                        lambda ctx, usd, what: None)
    monkeypatch.setattr(agent_tools, "_log_generation",
                        lambda *a, **k: False)

    def fake_gen(prompt, out_path, duration_s=None):
        if not gen_ok:
            return False, "provider says no"
        with open(out_path, "wb") as f:
            f.write(b"mp3")
        return True, None
    monkeypatch.setattr(agent_tools.eleven, "generate_sfx", fake_gen)
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda path, key, ct: None)


def test_pass_places_a_generated_whoosh_on_the_junction(tmp_path,
                                                        monkeypatch):
    _arm(monkeypatch)
    ctx = _Ctx(tmp_path)
    out = agent_tools.sound_design_pass(ctx, "light")
    # The two keep segments join at output t=10 — the one junction.
    assert out.startswith("EDL v")
    assert "generated whoosh @ 10.0s" in out
    edl, msg = ctx.written
    assert len(edl["sfx"]) == 1
    assert edl["sfx"][0]["storage_key"].startswith("generated_sfx/7/")
    assert "generated placement" in msg
    # Billing bookkeeping rode the same success boundary.
    assert len(ctx.sfx_generated) == 1


def test_failed_generation_drops_the_placement_not_the_pass(tmp_path,
                                                            monkeypatch):
    _arm(monkeypatch, gen_ok=False)
    ctx = _Ctx(tmp_path)
    out = agent_tools.sound_design_pass(ctx, "light")
    assert "FAILED" in out and "do NOT claim" in out
    assert ctx.written is None            # nothing landed, nothing written
    assert ctx.sfx_generated == []        # and nothing was billed


def test_per_turn_generation_cap_bounds_the_pass(tmp_path, monkeypatch):
    _arm(monkeypatch)
    ctx = _Ctx(tmp_path)
    ctx.sfx_generated = [{"storage_key": f"x{i}"} for i in range(
        agent_tools.config.MAX_GENERATED_SFX_PER_TURN)]
    out = agent_tools.sound_design_pass(ctx, "strong")
    assert out.startswith("REJECTED") and "limit" in out
