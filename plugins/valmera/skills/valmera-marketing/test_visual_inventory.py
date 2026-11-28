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
        assert sum(x.get("type") == "image" and x["source"]["data"] == "frame" for x in content) == 2
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


def test_resume_adds_recorded_studio_to_an_already_inspected_inventory(tmp_path):
    ctx = SimpleNamespace(supply=tmp_path)
    original = tmp_path / "lamp.png"
    original.write_bytes(b"lamp")
    row = V.distinct_files({"lamp": [{"path": str(original)}]})[0]
    manifest = tmp_path / "visual_inventory/inventory.json"
    V._save(manifest, {"subjects": [{"name": "lamp"}], "pictures": {"lamp": [row]}})
    sent = tmp_path / "type_00_sent.png"
    sent.write_bytes(b"sent")
    studio = [dict(path=str(sent), name="THE STUDIO", title="type_00_sent")]
    got = V.prepare(ctx, {}, {}, None, None, None, studio=studio)
    assert got["pictures"]["lamp"] == [row]
    assert got["pictures"]["THE STUDIO"][0]["path"] == str(sent)
    assert got["pictures"]["THE STUDIO"][0]["inventory_id"] != row["inventory_id"]
    assert sum(subject["name"] == "THE STUDIO" for subject in got["subjects"]) == 1
    assert V.prepare(ctx, {}, {}, None, None, None, studio=studio) == json.loads(manifest.read_text()) == got
