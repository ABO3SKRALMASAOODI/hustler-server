"""chain.alien_bank lays a scene's palette devices (``devices``) through scene.palette.run, and an alien-only scene
through scene.alien.run as before; a scene that asks for nothing is drawn as it is."""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain  # noqa: E402


def _ctx(tmp_path):
    return types.SimpleNamespace(supply=str(tmp_path), run_id="t", log=lambda *a: None, log_raw=lambda *a: None)


def test_devices_go_through_the_palette_and_alien_alone_through_the_bank(tmp_path, monkeypatch):
    ran = []

    def fake_sh(ctx, cmd, label=None, heavy=False, model=False, timeout=None):
        ran.append(cmd[-1])
        out = cmd[-1].split("--out ")[1].split(" ")[0]
        open(out, "w").write("{}")
        return ""
    monkeypatch.setattr(chain, "sh", fake_sh)
    monkeypatch.setattr(chain, "engine_kind", lambda: "scene")
    base = {"l1": 1, "dur_f": 10, "items": []}
    for name, extra, tool, suffix in (("d", {"devices": [{"dev": "glitch", "at": 2}], "alien": [{"dev": "arc"}]}, "scene.palette.run", ".devices.json"),
                                      ("a", {"alien": [{"dev": "arc"}]}, "scene.alien.run", ".alien.json")):
        p = str(tmp_path / f"{name}.json")
        json.dump(dict(base, **extra), open(p, "w"))
        out = chain.alien_bank(_ctx(tmp_path), p)
        assert out.endswith(suffix) and os.path.exists(out) and tool in ran[-1]
    p = str(tmp_path / "n.json")
    json.dump(base, open(p, "w"))
    n = len(ran)
    assert chain.alien_bank(_ctx(tmp_path), p) == p and len(ran) == n
