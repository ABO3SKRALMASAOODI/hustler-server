import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("inventory_test", Path(__file__).with_name("visual_inventory.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def test_inventory_removes_same_bytes_and_source_page(tmp_path):
    pictures = []
    for i, (data, page) in enumerate([(b"one", "a"), (b"one", "b"), (b"recrop", "a"), (b"two", "c")]):
        path = tmp_path / str(i)
        path.write_bytes(data)
        pictures.append(dict(path=str(path), page=page))
    assert [r["page"] for r in V.distinct_files({"lamps": pictures})] == ["a", "c"]


def test_inspected_inventory_survives_resume_and_rejects_mutation(tmp_path):
    ctx = SimpleNamespace(supply=tmp_path)
    calls = []
    def ask(system, content, **kwargs):
        calls.append(kwargs["what"])
        if kwargs["what"] == "visual inventory plan":
            return {"subjects": [{"name": "lamps", "searches": ["lamp", "table lamp"]}]}, {}
        return {"pictures": [{"id": 0, "usable": True, "group": 0, "shows": "A lamp"},
                             {"id": 1, "usable": True, "group": 0, "shows": "The same lamp cropped"}]}, {}
    def acquire(subjects, out, **kwargs):
        Path(out).mkdir(parents=True)
        rows = []
        for i in range(2):
            p = Path(out) / str(i)
            p.write_bytes(str(i).encode())
            rows.append(dict(path=str(p)))
        return {"lamps": rows}, [], 0
    got = V.prepare(ctx, {}, {}, ask, acquire, lambda *a, **kw: "frame")
    assert len(got["pictures"]["lamps"]) == 1
    before = len(calls)
    assert V.prepare(ctx, {}, {}, ask, acquire, None) == got
    assert len(calls) == before
    Path(got["pictures"]["lamps"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="no longer matches"):
        V.prepare(ctx, {}, {}, ask, acquire, None)


def test_writer_cannot_request_an_unseen_subject():
    inventory = {"pictures": {"lamps": []}}
    assert not V.script_problems({"segments": [{"pictures": ["lamps", "THE EXPORT"]}]}, inventory)
    assert V.script_problems({"segments": [{"pictures": ["phone preview"]}]}, inventory)
