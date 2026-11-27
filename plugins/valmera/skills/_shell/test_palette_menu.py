"""The palette's menu reaches a director's prompt from the engine's own registry, and a display row a model gave a
``perform`` device becomes the palette ask that draws it on the scene's word clock."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import palette_menu  # noqa: E402


def test_the_menu_comes_from_the_engines_registry():
    palette_menu._MENU.clear()
    text = palette_menu.menu(py=sys.executable)
    if not text:                       # an environment without the worker's packages: the rows stay plain, never a stop
        return
    got = palette_menu.names(text)
    for need in ("counter", "name", "ladder", "highlight", "typewriter", "behind", "track", "verb"):
        assert need in got, need
    assert not ({"answer", "lamp", "rhyme", "arc"} & got)          # the type, depth and HUD families only
    assert set(palette_menu.REPLACES) | set(palette_menu.DECORATES) >= got - {"readalong"}


def test_a_perform_row_becomes_an_ask_on_its_own_words():
    row = {"seg": 3, "text": "1,200 miles", "role": "figure",
           "perform": {"dev": "counter", "from": 0, "to": 1200, "sep": True, "lands": {"seg": 3, "word": "1,200"}}}
    ask = palette_menu.ask_for(row, 3, "1,200 miles", 577, 611, 2)
    assert ask["dev"] == "counter" and ask["id"] == "dv02" and ask["beat"] == "figure"
    assert ask["at"] == {"seg": 3, "word": "1,200 miles", "f": 577} and ask["lands"] == {"seg": 3, "word": "1,200"}
    assert ask["args"]["to"] == 1200 and ask["args"]["t"] == "1,200 miles" and "dev" not in ask["args"]
    hl = palette_menu.ask_for({"perform": {"dev": "highlight", "t": "here"}}, 1, "wouldn't be here", 250, 284, 0)
    assert hl["until"] == 284 and hl["args"]["t"] == "here"          # a decoration lives as long as the row it marks


def test_every_device_on_the_menu_has_one_category_and_a_bounded_end():
    """A device that draws its words replaces the row (chapter says its own title) and ends by the row's window unless
    the model named its end; a decoration ends inside its row or its sentence (the caption read along is one sentence,
    never the rest of the film)."""
    assert "chapter" in palette_menu.REPLACES and "chapter" not in palette_menu.DECORATES
    assert not set(palette_menu.REPLACES) & set(palette_menu.DECORATES)
    a, b, limit = 250, 284, 330
    for dev in palette_menu.REPLACES:
        ask = palette_menu.ask_for({"perform": {"dev": dev}}, 1, "wouldn't be here", a, b, 0, limit=limit)
        assert ask["bound"] == b and "until" not in ask, dev
        own = palette_menu.ask_for({"perform": {"dev": dev, "until": {"seg": 1, "word": "here"}}}, 1, "wouldn't be here", a, b, 0, limit=limit)
        assert own["until"] == {"seg": 1, "word": "here"} and own["bound"] == limit, dev
    for dev in palette_menu.DECORATES:
        ask = palette_menu.ask_for({"perform": {"dev": dev}}, 1, "wouldn't be here", a, b, 0, limit=limit)
        assert a < ask["until"] <= limit and ask["bound"] == ask["until"], dev
        if dev in palette_menu.ON_THE_ROW:
            assert ask["until"] == b, dev
    assert palette_menu.ask_for({"perform": {"dev": "readalong"}}, 1, "wouldn't be here", a, b, 0, limit=limit)["until"] == limit


def test_a_device_that_draws_part_of_its_row_leaves_the_rest_in_the_caption():
    ask = palette_menu.ask_for({"perform": {"dev": "behind", "t": "LOVE", "small": "I"}}, 4, "I love you", 600, 640, 0)
    assert palette_menu.drawn(ask, "I love you") == [True, True, False]
    span = [{"w": "I"}, {"w": "love"}, {"w": "you"}]
    assert [r["w"] for r in palette_menu.omit_of(span, "I love you", [True, True, False])] == ["I", "love"]
    um = [{"w": "I"}, {"w": "uh"}, {"w": "love"}, {"w": "you"}]
    assert [r["w"] for r in palette_menu.omit_of(um, "I love you", [True, True, False])] == ["I", "uh", "love"]
    whole = palette_menu.ask_for({"perform": {"dev": "typewriter"}}, 4, "I love you", 600, 640, 0)
    assert all(palette_menu.drawn(whole, "I love you"))                    # handed the row: it draws every word
    fig = palette_menu.ask_for({"perform": {"dev": "counter", "from": 0, "to": 1200, "t": "1,200"}}, 3, "1,200 miles", 577, 611, 0)
    assert all(palette_menu.drawn(fig, "1,200 miles"))                     # a figure device draws its row and its unit


def test_the_laid_scene_is_redone_when_the_scene_it_was_laid_from_changes(tmp_path, monkeypatch):
    """A rebuilt score rewrites l1.json: the palette lays it again; the same scene is drawn from the cache."""
    import json
    from types import SimpleNamespace
    import chain
    l1 = tmp_path / "l1.json"
    l1.write_text(json.dumps({"devices": [{"dev": "verb", "t": "ONE"}], "items": []}))
    ctx = SimpleNamespace(run_id="t", supply=str(tmp_path))
    runs = []

    def fake_sh(ctx, cmd, **kw):
        src = json.load(open(l1))
        out = cmd[-1].split("--out ")[1].split(" ")[0].strip("'")
        runs.append(src["devices"][0]["t"])
        open(out, "w").write(json.dumps({"laid": src["devices"][0]["t"]}))
        return ""
    monkeypatch.setattr(chain, "sh", fake_sh)
    monkeypatch.setattr(chain, "engine_kind", lambda: "scene")
    monkeypatch.setattr(chain.brand, "worker_dir", lambda: str(tmp_path))
    out = chain.alien_bank(ctx, str(l1))
    assert json.load(open(out))["laid"] == "ONE"
    assert chain.alien_bank(ctx, str(l1)) == out and runs == ["ONE"]       # the same scene: drawn from the cache
    l1.write_text(json.dumps({"devices": [{"dev": "verb", "t": "TWO"}], "items": []}))
    out2 = chain.alien_bank(ctx, str(l1))
    assert json.load(open(out2))["laid"] == "TWO" and runs == ["ONE", "TWO"]  # rebuilt: laid again, never the old palette


def test_the_podcast_render_cache_goes_with_the_laid_scene(tmp_path):
    import importlib
    from types import SimpleNamespace
    pod = os.path.join(os.path.dirname(HERE), "valmera-podcast-shorts", "scripts")
    sys.path.insert(0, pod)
    try:
        wf1 = importlib.import_module("wf1_short")
    except Exception as e:      # noqa: BLE001 - the podcast package needs its own environment
        import pytest
        pytest.skip(f"wf1_short does not import here: {e}")
    sup = tmp_path / "supply"
    sup.mkdir()
    for name in ("film_engine.mp4", "l1.alien.json", "l1.alien.json.part", "l1.alien.json.key", "alien_bank.json", "l1.json"):
        (sup / name).write_text("x")
    wf1.invalidate_render(SimpleNamespace(supply=str(sup), run_dir=str(tmp_path)))
    assert sorted(os.listdir(sup)) == ["l1.json"]


def test_a_worker_without_the_palette_lays_the_alien_bank_from_the_directors_asks(tmp_path, monkeypatch):
    """Before the palette's executor release the synced worker has only scene.alien: the Director's asks for the bank's
    own devices reach it with their [a, z] span, the others wait, and the scene drawn carries no devices field."""
    import json
    from types import SimpleNamespace
    import chain
    l1 = tmp_path / "l1.json"
    l1.write_text(json.dumps({"devices": [{"dev": "lamp", "f": [10, 40], "t": "LOVE"}, {"dev": "diecut", "at": 50, "until": 70, "t": "NO"},
                                          {"dev": "neg_world", "f": [0, 30]}], "alien": [{"dev": "arc", "at": [0, 90]}], "items": []}))
    seen = {}

    def fake_sh(ctx, cmd, **kw):
        line = cmd[-1]
        seen["mod"] = line.split(" -m ")[1].split(" ")[0]
        src = line.split("--l1 ")[1].split(" ")[0].strip("'")
        seen["scene"] = json.load(open(src))
        open(line.split("--out ")[1].split(" ")[0].strip("'"), "w").write("{}")
        return ""
    monkeypatch.setattr(chain, "sh", fake_sh)
    monkeypatch.setattr(chain, "engine_kind", lambda: "scene")
    monkeypatch.setattr(chain.brand, "worker_dir", lambda: str(tmp_path))
    chain.lay_devices(SimpleNamespace(run_id="t", supply=str(tmp_path), log=print), str(l1))
    assert seen["mod"] == "scene.alien.run" and "devices" not in seen["scene"]
    assert [(a["dev"], a["at"]) for a in seen["scene"]["alien"]] == [("arc", [0, 90]), ("lamp", [10, 40]), ("diecut", [50, 70])]


def test_an_analogy_dial_keeps_its_own_end_and_leaves_the_row_in_the_caption():
    """analogy_dial (worker scene/palette/instrument.py, 60a31204) draws a dial beside the row and none of its words:
    the ask keeps the `until` the model named for its needle, bounded by the sentence, and the caption keeps every word."""
    a, b, limit = 250, 284, 330
    until = {"seg": 1, "word": "here"}
    ask = palette_menu.ask_for({"perform": {"dev": "analogy_dial", "until": until, "change": 7, "trend": "up"}},
                               1, "wouldn't be here", a, b, 0, limit=limit)
    assert ask["until"] == until and ask["bound"] == limit and ask["args"]["trend"] == "up"
    assert palette_menu.drawn(ask, "wouldn't be here") == [False, False, False]
    assert "analogy_dial" not in palette_menu.REPLACES
