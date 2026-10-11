"""AT-0C-3 for track 1D: every pinned fault x every 1D device spec gives
ZERO render failures (a creative device may degrade; it never fails a
render), the program keeps its exact length, and the report names a rung
for every item.

The gate every later wave inherits (tests/test_w0c_faults.py) covers the
Wave 0 specs; this file adds the 1D devices: frame accents (timeline, alpha
and region paths, a card region), a strobe of stills and clip frames, and a
junction at a real cut for each compile path (mask wipe, xfade, polar blur,
matte cut, datamosh).
"""

import os

import pytest

import faultinject
from w1d_support import (IDX, accent, lossless, make_montage, make_source,
                         probe, render)

FAULTS = ("none",) + faultinject.FAULTS


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1d_faults")
    import subprocess
    still = str(d / "still.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x2a6fdb:s=320x180:d=1", "-frames:v", "1",
                    still], check=True)
    return {"src": make_source(str(d / "src.mp4")),
            "montage": make_montage(str(d / "montage.mp4")),
            "still": still, "dir": str(d)}


def _accents(m):
    card = {"id": "pc", "start": 0.5, "end": 7.5, "entrance": "none",
            "exit": "none"}
    edl = {"keep": [[0.0, 4.0], [5.0, 9.0]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"},
           "effects": {"picture_cards": [card]},
           "fx": [accent("n", 20, 25.0, device="accent_negative"),
                  accent("w", 40, 25.0, kind="white_hit", decay_f=3,
                         region={"shape": "card"},
                         device="accent_white_hit"),
                  accent("b", 60, 25.0, kind="box_invert", n=2, seed=2,
                         device="accent_box_invert"),
                  accent("o", 80, 25.0, kind="rgb_slice", n=2,
                         stage="over_type", device="accent_rgb_slice")]}
    return edl, src_of(m, "src"), {}, 8.0


def _strobe(m):
    edl = {"keep": [[0.0, 8.0]], "captions": {"enabled": False},
           "fx": [{"id": "st", "type": "strobe", "stage": "pre_captions",
                   "start": 2.0, "end": 2.6, "device": "strobe_frames",
                   "frames": [{"source": {"kind": "asset",
                                          "asset_key": "img/1/still.png"}},
                              {"source": {"kind": "source", "src_t": 6.0},
                               "negate": True},
                              {"source": {"kind": "program"}}]}]}
    return edl, src_of(m, "src"), {"img/1/still.png": m["still"]}, 8.0


def _junctions(style):
    def spec(m):
        edl = {"keep": [[0.4, 2.6], [3.4, 5.6], [6.4, 8.6]],
               "captions": {"enabled": False},
               "fx": [{"id": "j1", "type": "junction", "stage": "junction",
                       "at": 2.2, "style": style, "duration_s": 0.48},
                      {"id": "j2", "type": "junction", "stage": "junction",
                       "at": 4.4, "style": "iris", "duration_s": 0.4,
                       "origin": {"kind": "face"},
                       "device": "junction_iris"}]}
        return edl, src_of(m, "montage"), {}, 6.6
    return spec


def src_of(m, key):
    return m[key]


SPECS = {"accents": _accents, "strobe": _strobe,
         "jn_columns": _junctions("columns"),
         "jn_slices": _junctions("slices"),
         "jn_zoom": _junctions("zoom_blur"),
         "jn_matte": _junctions("matte_cut"),
         "jn_mosh": _junctions("datamosh")}


@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("spec", sorted(SPECS))
def test_every_fault_times_every_1d_spec_renders(spec, fault, media,
                                                 tmp_path, monkeypatch):
    lossless(monkeypatch)
    edl, src, locals_, want = SPECS[spec](media)
    out = str(tmp_path / "out.mp4")
    names = () if fault == "none" else (fault,)
    with faultinject.inject(*names):
        dur, rep = render(edl, src, out, idx=IDX, asset_locals=locals_)
    assert dur == pytest.approx(want, abs=0.07), (spec, fault, dur)
    p = probe(out)
    assert p.get("width") and p.get("height")
    ids = {i["id"] for i in edl["fx"]}
    assert ids <= {r["id"] for r in rep}, rep
    assert all(r["rung"] in ("full", "reduced", "substitute", "omit")
               for r in rep)
    # a device degrades on its own row: the whole fx graph never falls
    # back to a render without fx (that drops every device on the edit)
    assert not any("graph failed" in str(r.get("reason")) for r in rep), rep
    if fault == "none":
        assert all(r["rung"] != "omit" for r in rep), rep
    if fault == "ffmpeg_filter_missing":
        assert all(r["rung"] == "omit" for r in rep), rep
    if fault == "oom" and spec in ("strobe", "jn_columns"):
        assert any(r["rung"] == "omit" for r in rep), rep
