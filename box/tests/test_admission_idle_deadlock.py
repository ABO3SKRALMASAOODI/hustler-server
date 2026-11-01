"""box/admission.py, 1 Nov 2026 outage: the memory reserve must never stop an
IDLE box. 31 idle slots had crept to MemAvailable 3.46 GiB against a 3.5 GiB
reserve, so every call (light and heavy, customers' included) waited 50
minutes with nothing running. With nothing occupying a slot, one call always
starts; once something runs, the reserve applies again as before.

Run locally: python -m pytest box/tests
"""

import importlib.util
import os
import time

import pytest

from test_admission_capacity import _box, _submit, _assign  # noqa: F401

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def adm(tmp_path, monkeypatch):
    for key, name in (("BOX_STATE_DB", "adm.sqlite"),
                      ("BOX_RELEASE", "release.json"),
                      ("BOX_STEAL_LOG", "steal.log"),
                      ("BOX_ENV_FILE", "executor.env"),
                      ("BOX_CONFIG", "box.json")):
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        f"box_idle_{time.monotonic_ns()}",
        os.path.join(BOX_DIR, "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._mem = {"gib": 3.46}
    monkeypatch.setattr(module, "mem_available_gib",
                        lambda: module._mem["gib"])
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    return module


@pytest.mark.parametrize("lane,jtype", [("interactive", "preview"),
                                        ("batch", "index"),
                                        ("batch", "final")])
def test_an_idle_box_short_of_its_reserve_still_starts_one_call(adm, lane,
                                                                 jtype):
    box = _box(adm, mem_reserve_gib=3.5)
    w = _submit(box, lane=lane, priority="customer", jtype=jtype)
    _assign(box)
    assert w["slot"], "an idle box must start a call even below its reserve"


def test_below_the_reserve_only_one_heavy_call_starts_then_the_rest_wait(adm):
    box = _box(adm, mem_reserve_gib=3.5)
    first = _submit(box, lane="batch", priority="customer", jtype="index")
    second = _submit(box, lane="batch", priority="customer", jtype="index")
    _assign(box)
    assert first["slot"] and not second["slot"]


def test_with_enough_memory_nothing_changes(adm):
    adm._mem["gib"] = 20.0
    box = _box(adm, mem_reserve_gib=3.5)
    ws = [_submit(box, lane="interactive", priority="customer")
          for _ in range(3)]
    _assign(box)
    assert all(w["slot"] for w in ws)
