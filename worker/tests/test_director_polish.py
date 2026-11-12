"""The defects the first Director films were rated down for (7 Nov), each at its root."""
import json
import os
import subprocess
from types import SimpleNamespace as NS

import pytest

import schemas
from score import compiler, lower, supply
from tests.test_score_compiler import REST, _film, talk_index, wordless_index


@pytest.fixture(autouse=True)
def _mechanics_only(monkeypatch):
    """These tests pin how each line of a Score compiles. What a film may carry is the taste governor's
    (score/taste.py), which has its own tests in test_score_taste.py, so it is off here."""
    import score.compiler as _compiler
    monkeypatch.setattr(_compiler, "GOVERN", False)

FILM = {"k": "film", "v": 1, "fps": 30, "frame": "9:16", "spine": "main", "band": "P2", "seed": 7}


def _words(c, role=None):
    return [i for i in c.l1["items"] if i["k"] == "word" and (role is None or i.get("role") == role)]


# 1. p5363: a blank cream rectangle over the first frames

def test_a_negative_with_no_one_to_register_to_is_the_whole_frame_never_a_made_up_box():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "neg_card", "at": "@0.1", "f": 2}, {"g": "boxes", "at": "@1"}, {"g": "glitch", "at": "@2"}], "rest": REST}])
    c = compiler.compile_score(t, index=wordless_index(10))
    fl = [i for i in c.l1["items"] if i["k"] == "flash"]
    assert fl and all(i.get("rect") != [0.12, 0.19, 0.88, 0.68] for i in fl)
    neg = [i for i in fl if i["id"].startswith("b01.") and i.get("mode") == "neg" and "rect" not in i]
    assert neg, fl


def test_a_negative_on_a_face_stays_registered_to_it():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main", "do": [{"g": "neg_card", "at": "@0.1", "f": 2}], "rest": REST}])
    c = compiler.compile_score(t, index=talk_index(10))
    r = [i for i in c.l1["items"] if i["k"] == "flash"][0]["rect"]
    assert r[0] < 0.36 + 0.01 and r[2] > 0.64 - 0.01 and r[3] < 0.5


# 2. p5302: captions across the speaker's head

def test_type_never_sits_on_a_head_even_when_the_seat_is_there():
    idx = talk_index(12)
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": "AQUI NA LAR NOVO", "at": "@0..@3", "size": 0.06, "seat": [0.5, 0.2]}], "rest": REST}],
              dna={"seat": [0.5, 0.2]})
    c = compiler.compile_score(t, index=idx)
    head = compiler.head_of([0.36, 0.12, 0.64, 0.3])
    ws = _words(c)
    assert ws
    for w in ws:
        r = compiler.word_rect(c_ctx(c), w["t"], w["v"], w["x"], w["y"])
        assert not compiler.rects_touch(r, head), (w["t"], r, head)


def c_ctx(c):
    return NS(voices=c.l1["voices"], W=c.l1["frame"][0], H=c.l1["frame"][1])


def test_a_person_seen_without_a_face_keeps_type_off_his_head():
    per = {"samples": [{"t": float(k) + 0.25, "face": None, "person": [0.35, 0.3, 0.65, 0.95], "luma": 0.5} for k in range(12)]}
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": "ESTÁ CHEGANDO", "at": "@0..@3", "size": 0.05, "seat": [0.5, 0.38]}], "rest": REST}])
    c = compiler.compile_score(t, index=talk_index(12, faces=False), perception=per)
    head = [0.35 + 0.28 * 0.3, 0.3 + 0.02 * 0.65, 0.65 - 0.28 * 0.3, 0.3 + 0.2 * 0.65]
    for w in _words(c):
        assert not compiler.rects_touch(compiler.word_rect(c_ctx(c), w["t"], w["v"], w["x"], w["y"]), head), w


def test_perception_finds_a_small_face_the_index_missed(tmp_path):
    """The index reads a 360 px still with a 24 px smallest face; the film's own pass looks again."""
    cv2 = pytest.importorskip("cv2")
    from score import perceive
    seer = perceive.Subjects(10)
    if not seer.cascades:
        pytest.skip("no Haar data on this machine")
    import numpy as np
    gray = np.full((960, 540), 200, np.uint8)
    assert seer.face(gray) is None                      # nothing in a flat wall
    assert perceive.head_of_person([0.4, 0.2, 0.6, 0.9])[3] < 0.2 + 0.2 * 0.7 + 1e-6


# 3. white captions on white walls: every stretch under a word is read

def _two_tone(tmp_path):
    """A 4 s upload, dark for its first 3 s and white after: one shot, the light changes under the words."""
    src = tmp_path / "source.mp4"
    ff = compiler.FFMPEG
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x101010:s=108x192:d=3:r=30", "-f", "lavfi", "-i", "color=c=0xF4F4F4:s=108x192:d=1:r=30",
                    "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(src)], check=True)
    idx = talk_index(4.0, faces=False)
    idx["video"]["duration"] = 4.0
    with open(tmp_path / "index.json", "w") as f:
        json.dump(idx, f)
    return str(tmp_path)


def test_a_word_gets_its_edge_when_any_stretch_under_it_fails(tmp_path):
    work = _two_tone(tmp_path)
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3.9", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": "SENSACIONAL", "at": "@1.0..@3.6", "size": 0.06, "ink": "#FFFFFF", "seat": [0.5, 0.5]}], "rest": REST}])
    c = compiler.compile_score(t, work_dir=work, perception={})
    ws = [w for w in _words(c) if w["t"] == "SENSACIONAL"]
    assert ws and any(w.get("edge") for w in ws), ws


def test_a_word_over_a_picture_that_cannot_be_read_carries_the_edge():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": "MOMENTO", "at": "@0..@3", "size": 0.06, "seat": [0.5, 0.7]}], "rest": REST}])
    c = compiler.compile_score(t, index=wordless_index(10))         # no work folder: nothing to read
    assert all(w.get("edge") for w in _words(c))


# 4. type over words printed in the upload, read from the index at that second

def test_type_leaves_a_sign_the_index_read_in_one_part_of_the_take():
    idx = wordless_index(10)
    box = [0.25, 0.44, 0.75, 0.56]
    idx["spatial"] = {"samples": [{"t": float(k), "faces": [], "text": [box] if 4 <= k <= 6 else []} for k in range(11)]}
    t = _film([{"k": "b", "id": "b01", "at": "@4..@6", "job": "x", "cut": "pulse 9", "pic": "main",
                "do": [{"g": "big", "t": "DIA 22", "at": "@4..@6", "size": 0.1, "seat": [0.5, 0.5]}], "rest": REST}])
    c = compiler.compile_score(t, index=idx, perception={})
    for w in _words(c):
        assert not compiler.rects_touch(compiler.word_rect(c_ctx(c), w["t"], w["v"], w["x"], w["y"]), box), w


# 5. a card that reaches both sides

def test_a_card_lands_inside_the_side_margins_at_its_largest():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@3", "job": "x", "cut": "pulse 9", "pic": {"plate": "#E11F79"},
                "do": [{"g": "card", "pic": "main", "at": "@0..@3", "w": 1.3, "h": 0.3, "x": 0.5, "y": 0.5, "in": "settle"}], "rest": REST}])
    c = compiler.compile_score(t, index=wordless_index(10))
    cards = [i for i in c.l1["items"] if i["k"] == "card" and i.get("w", 1) < 1]
    assert cards
    for it in cards:
        s = it.get("s", 1)
        peak = max(s) if isinstance(s, list) else s
        x = it["x"][-1] if isinstance(it["x"], list) else it["x"]
        assert x - it["w"] * peak / 2 >= compiler.SAFE_X - 0.005 and x + it["w"] * peak / 2 <= 1 - compiler.SAFE_X + 0.005, it


# 6. p5363: a 4:3 cartoon's crop cut its title and its characters

def _cartoon_index():
    idx = wordless_index(12)
    idx["video"].update(width=1440, height=1080)
    title, chars = [0.4167, 0.2926, 0.6083, 0.3519], [0.2583, 0.4889, 0.75, 0.6556]
    idx["spatial"] = {"samples": [{"t": float(k), "faces": [], "text": [title, chars]} for k in range(13)]}
    return idx, title, chars


def test_a_crop_of_a_wider_upload_holds_its_printed_title_whole_and_fits_on_a_blurred_fill():
    from score import perceive
    idx, title, chars = _cartoon_index()
    per = {"text": perceive.text_clusters(idx["spatial"])}
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": "pulse 9", "pic": {"src": "main", "crop": "centre", "cam": "push 1.04"}, "do": [], "rest": REST}])
    c = compiler.compile_score(t, index=idx, perception=per)
    shots = [i for i in c.l1["items"] if i["k"] == "shot" and i.get("pic") == "main"]
    assert shots
    for s in shots:
        for r in (s["crop"] if isinstance(s["crop"][0], list) else [s["crop"]]):
            for m in (title, chars):
                inside = r[0] <= m[0] + 1e-3 and r[1] <= m[1] + 1e-3 and m[2] <= r[2] + 1e-3 and m[3] <= r[3] + 1e-3
                assert inside or not compiler.rects_touch(r, m), (r, m)
    edl, _loc, rep = lower.lower(c.l1, os.getcwd())
    assert edl["frame"]["mode"] == "pad_blur"
    schemas.validate_edl(edl, 12.0)
    win, frame = lower.base_window(c.l1)        # the picture fitted whole: the source is a band of the frame
    assert frame["mode"] == "pad_blur" and win[0] == 0.0 and win[2] == 1.0 and win[1] < -0.6
    for z in edl["effects"]["zooms"]:            # the camera never pushes past the held crop: both stay in view
        for p in z["path"]:
            w = 1 / (1 + p["s"])
            for m in (title, chars):
                q = lower.in_window(m, win)
                assert p["cx"] - w / 2 <= q[0] + 2e-3 and q[2] <= p["cx"] + w / 2 + 2e-3, (p, q)
                assert p["cy"] - w / 2 <= q[1] + 2e-3 and q[3] <= p["cy"] + w / 2 + 2e-3, (p, q)


def test_a_crop_inside_one_window_is_the_camera_on_that_window():
    """A crop in source fractions is said on the picture as the renderer frames it: the full-height
    9:16 window of a 4:3 upload is a camera at rest, not a 2.4x push."""
    idx = wordless_index(12)
    idx["video"].update(width=1440, height=1080)
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": "pulse 9", "pic": {"src": "main", "crop": [0.4, 0, 0.82, 1]}, "do": [], "rest": REST}])
    c = compiler.compile_score(t, index=idx, perception={})
    edl, _loc, rep = lower.lower(c.l1, os.getcwd())
    assert edl["frame"]["mode"] == "crop" and abs(edl["frame"]["focus_x"] - 0.61) < 0.02
    assert all(max(p["s"] for p in z["path"]) < 0.05 for z in edl["effects"]["zooms"]), edl["effects"]["zooms"]
    schemas.validate_edl(edl, 12.0)


def test_a_same_shape_upload_frames_as_before():
    t = _film([{"k": "b", "id": "b01", "at": "@0..@6", "job": "x", "cut": "pulse 9", "pic": {"src": "main", "crop": [0.25, 0.25, 0.75, 0.75]}, "do": [], "rest": REST}])
    c = compiler.compile_score(t, index=wordless_index(12), perception={})
    edl, _loc, _rep = lower.lower(c.l1, os.getcwd())
    assert edl["frame"] == {"ratio": "9:16", "mode": "crop"}
    assert any(abs(max(p["s"] for p in z["path"]) - 1.0) < 0.01 for z in edl["effects"]["zooms"])


# 7. off-topic stock: a model looks at each found picture against its moment before the Bin

def _bin(tmp_path, n=3):
    from PIL import Image
    os.makedirs(tmp_path / "bin", exist_ok=True)
    items = []
    for i in range(n):
        Image.new("RGB", (64, 96), (40 * i, 80, 120)).save(tmp_path / "bin" / f"{i}.jpg")
        items.append(dict(id=i, kind="video", path=f"bin/{i}.jpg", wish=i, what=f"thing {i}", _looked=True, storage_key=f"k{i}"))
    idx = talk_index(10.0)
    with open(tmp_path / "index.json", "w") as f:
        json.dump(idx, f)
    wishes = [{"w": "0-4", "want": "a wardrobe"}, {"w": "5-9", "want": "the sale"}, {"at": 4.0, "want": "the store"}]
    return str(tmp_path), items, wishes


def test_a_found_picture_the_check_rejects_never_enters_the_bin(tmp_path, monkeypatch):
    work, items, wishes = _bin(tmp_path)
    seen = {}

    def fake(model, system, content, **kw):
        seen.update(model=model, purpose=kw["purpose"], images=sum(1 for c in content if c.get("type") == "image"), text=content[-1]["text"])
        return NS(text='{"fit":[{"n":0,"ok":true,"why":"a wardrobe"},{"n":1,"ok":false,"why":"chipboard and a screw"},{"n":2,"ok":true,"why":"a shop"}]}', usd=0.0021)
    monkeypatch.setattr(supply.call, "stream", fake)
    kept, usd, rep = supply.check_fit(work, items, wishes, 1e12)
    assert [k["id"] for k in kept] == [0, 2] and usd == 0.0021
    assert seen["model"] == supply.LOOK_MODEL and seen["purpose"] == "director_bin_check" and seen["images"] == 3
    assert "palavra" in seen["text"] and "a wardrobe" in seen["text"]
    ren = supply.renumber(work, kept)
    assert [r["id"] for r in ren] == [0, 1] and ren[1]["path"] == "bin/1.jpg" and ren[1]["storage_key"] == "k2"
    assert os.path.exists(os.path.join(work, "bin", "1.jpg"))


def test_no_verdict_keeps_only_what_the_look_chose(tmp_path, monkeypatch):
    work, items, wishes = _bin(tmp_path)
    items[1]["_looked"] = False                       # the library-order fallback: no model ever saw it

    def boom(*a, **k):
        raise TimeoutError("late")
    monkeypatch.setattr(supply.call, "stream", boom)
    kept, usd, rep = supply.check_fit(work, items, wishes, 1e12)
    assert [k["id"] for k in kept] == [0, 2] and usd == 0.0 and rep[0]["dropped"] == [1]
