"""Regression cases from p5968 and p6062."""
from types import SimpleNamespace

import agent_tools
import motion_qa
from score import asks
from tools_ext import verify_tools, recipe_tools


def test_retry_after_keep_rebuilt_the_sound_layer():
    ctx = SimpleNamespace(latest_edl=lambda: {"json": {"sfx": [
        {"id": "hit2", "at": 3}, {"id": "hit7", "at": 7}]}})
    assert agent_tools.remove_sfx(ctx, "old") == (
        "sfx old is already absent; nothing to remove; current sfx ids: hit2, hit7.")
    miss = agent_tools.move_sfx(ctx, "old", 6.5)
    assert "hit2, hit7" in miss and "hit7 at 7s" in miss
    assert "already absent" in agent_tools.remove_text(ctx, "old")


def test_flash_from_saved_device_proof_cannot_block():
    proof = {"devices": [{"id": "st1", "metrics": {
        "flash_risk": {"value": 15, "bar": "<=3", "pass": False},
        "land_err_f": {"pass": True}}}]}
    assert motion_qa.verdict(proof["devices"]) is True
    assert not motion_qa.critic_evidence({"fx": [{"id": "st1", "type": "strobe",
                                                "start": 1}]}, proof_result=proof)


def test_requested_devices_come_from_model_and_fulfilled_items_survive():
    ctx = SimpleNamespace(user_message="avoid zooms or sound effects",
                          ask_ledger={"asks": [{"devices": ["flash"]}]})
    assert verify_tools._asked(ctx, []) == {"flash"}
    assert recipe_tools._flash_ok(ctx)
    rows = [{"state": "done", "fulfilled_by": ["fx:st1"]}]
    assert asks._unasked_only(rows, [{"items": ["fx:st1"]},
                                      {"items": ["fx:st1", "fx:st2"]},
                                      {"items": []}]) == [{"items": ["fx:st2"]}, {"items": []}]


def test_partial_removal_applies_survivors_once(monkeypatch):
    from tools_ext import broll_tools, _write
    ctx = SimpleNamespace(latest_edl=lambda: {"version": 4, "json": {
        "fx": [{"type": "broll", "id": "cut2"}, {"type": "other", "id": "other"}]}})
    writes = []
    monkeypatch.setattr(_write, "commit", lambda ctx, ops, summary: writes.append(ops) or summary)
    assert "already absent" in broll_tools.remove_broll(ctx, ["cut1"])
    assert writes == []
    result = broll_tools.remove_broll(ctx, ["cut1", "cut2"])
    assert writes == [[{"action": "remove", "layer": "fx", "id": "cut2"}]]
    assert "cut1 already absent" in result and "removed cutaway(s) cut2" in result
