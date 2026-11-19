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
