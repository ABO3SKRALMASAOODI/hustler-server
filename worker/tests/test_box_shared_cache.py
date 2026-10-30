"""E3 (30 Oct 2026): the box's slots share one render cache directory.

Each slot kept its motion clips, plate moments and picture-check answers in
its own container layer: lost on every release roll, and invisible to the
other five interactive slots a short's next render lands on. boxctl now
mounts one host directory into every slot (box.json "shared_cache"), points
MOTION_CACHE_DIR there and turns RENDER_MEMO on; a slot created without the
current setup counts as drifted and is replaced by the next drained roll.
Without the setting nothing changes.

The box change ships box-only (claude/render-cache-box-1030); these tests
skip on a checkout whose boxctl predates it, so the worker bundle and the
box push can land in either order.
"""

import importlib.util
import json
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "..", "box")


@pytest.fixture
def boxctl(tmp_path, monkeypatch):
    cfg = tmp_path / "box.json"
    cfg.write_text(json.dumps({"slots": []}))
    monkeypatch.setenv("BOX_CONFIG", str(cfg))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_RELEASE", str(tmp_path / "release.json"))
    spec = importlib.util.spec_from_file_location(
        f"boxctl_{time.monotonic_ns()}", os.path.join(BOX_DIR, "boxctl.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "cache_args"):
        pytest.skip("this checkout's boxctl predates the shared cache")
    module._cfg_path = cfg
    return module


def _set(boxctl, **shared):
    boxctl._cfg_path.write_text(json.dumps({"slots": [],
                                            "shared_cache": shared}))


def test_without_a_shared_cache_slots_run_as_before(boxctl):
    assert boxctl.cache_args() == []
    assert boxctl.cache_label() == ""
    slot = {"name": "interactive1", "lane": "interactive", "port": 9101}
    info = {"image": boxctl.slot_image(slot, "abc"), "running": True,
            "port": "9101", "cache": ""}
    assert not boxctl._slot_drifted(slot, info, "abc")


def test_the_shared_cache_is_mounted_with_its_settings(boxctl, tmp_path):
    shared = tmp_path / "cache"
    _set(boxctl, dir=str(shared), motion_max_gib=12, memo=True)
    args = boxctl.cache_args()
    assert shared.is_dir()
    assert ["-v", f"{shared}:/var/cache/valmera"] == args[:2]
    env = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert "MOTION_CACHE_DIR=/var/cache/valmera/motion" in env
    assert f"MOTION_CACHE_MAX_BYTES={12 * 1024 ** 3}" in env
    assert "RENDER_MEMO=1" in env
    assert f"valmera.cache={boxctl.cache_label()}" in args
    _set(boxctl, dir=str(shared), memo=False)
    assert "RENDER_MEMO=1" not in boxctl.cache_args()


def test_a_slot_without_the_current_cache_setup_is_rolled(boxctl, tmp_path):
    _set(boxctl, dir=str(tmp_path / "cache"))
    slot = {"name": "batch1", "lane": "batch", "port": 9109}
    info = {"image": boxctl.slot_image(slot, "abc"), "running": True,
            "port": "9109", "cache": ""}
    assert boxctl._slot_drifted(slot, info, "abc")
    info["cache"] = boxctl.cache_label()
    assert not boxctl._slot_drifted(slot, info, "abc")
    _set(boxctl, dir=str(tmp_path / "cache"), motion_max_gib=20)
    assert boxctl._slot_drifted(slot, info, "abc")


def test_clean_ages_out_only_untouched_entries(boxctl, tmp_path):
    shared = tmp_path / "cache"
    (shared / "motion").mkdir(parents=True)
    old = shared / "motion" / "old.mov"
    new = shared / "motion" / "new.mov"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    now = time.time()
    os.utime(old, (now - 5 * 86400, now - 5 * 86400))
    os.utime(new, (now - 3600, now - 3600))
    conf = {"dir": str(shared), "max_age_days": 4}
    assert boxctl.clean_shared_cache(conf, tight=False, now=now) == 1
    assert not old.exists() and new.exists()
    # a tight disk keeps six hours
    os.utime(new, (now - 7 * 3600, now - 7 * 3600))
    assert boxctl.clean_shared_cache(conf, tight=True, now=now) == 1
    assert boxctl.clean_shared_cache(None, tight=True, now=now) == 0


def test_the_repository_plan_turns_the_shared_cache_on(boxctl):
    with open(os.path.join(BOX_DIR, "host", "box.json")) as fh:
        plan = json.load(fh)
    shared = plan.get("shared_cache") or {}
    assert shared.get("dir") == "/var/lib/valmera/cache"
    assert shared.get("memo") is True
    assert 4 <= float(shared.get("motion_max_gib")) <= 20


def test_container_info_reads_a_cache_label_that_holds_separators(boxctl,
                                                                  tmp_path):
    # 30 Oct 01:07: the label "dir|12|1" was split as if "|" ended it, the
    # port read "12|1|9101", every slot counted as drifted and the box
    # rolled all 24 slots on every update run.
    _set(boxctl, dir=str(tmp_path / "cache"))
    label = boxctl.cache_label()
    assert "|" in label
    info = boxctl.parse_container_info(
        f"valmera-exec:abc|true|2026-10-30T01:07:52Z|9109|{label}\n")
    assert info["port"] == "9109" and info["cache"] == label
    slot = {"name": "batch1", "lane": "batch", "port": 9109}
    info["image"] = boxctl.slot_image(slot, "abc")
    assert not boxctl._slot_drifted(slot, info, "abc")
    plain = boxctl.parse_container_info("img|true|t|9101|\n")
    assert plain["port"] == "9101" and plain["cache"] == ""
