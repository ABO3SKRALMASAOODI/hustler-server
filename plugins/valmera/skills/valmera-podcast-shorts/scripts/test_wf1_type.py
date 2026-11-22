import json
from pathlib import Path

import wf1_film
from wf1_type import finish_layout, phrase, repair_contrast, type_ask


def test_counter_and_director_type_use_words_at_the_ask():
    said = [["about", 60, 65, 0], ["a", 65, 70, 0], ["year", 70, 83, 0],
            ["nine", 875, 890, 1], ["months", 890, 909, 1]]
    counter = type_ask(dict(id="n", dev="counter", f=[60, 82],
                            args={"from": 0, "to": 9, "fmt": "{n} MONTHS", "lands": 875}), said)
    assert (counter["t"], counter["f"]) == ("about a year", [60, 83])
    assert not {"from", "to", "fmt", "lands"}.intersection(counter)
    pages = type_ask(dict(id="p", dev="type_run", words=["dreamer", "vision", "ambition"], f=[875, 909]), said)
    assert pages["words"] == ["nine", "months"]
    name = type_ask(dict(id="who", dev="name", name="Alexandr Wang", title="Scale CEO", f=60), said)
    assert name["dev"] == "highlight" and "Alexandr" not in str(name)
    assert not phrase([["6.8", 0, 5, 0]], "68")


def test_plate_and_identification_follow_the_final_picture(tmp_path):
    wf1_film.F._engines()
    path = tmp_path / "scene.json"
    doc = dict(assets={p:dict(kind="video") for p in ("wang", "listener")}, items=[
        dict(id="first", k="shot", pic="wang", f=[0, 6]),
        dict(id="cut", k="shot", pic="listener", f=[6, 15]),
        dict(id="name", k="word", t="Alexandr Wang", name_picture="wang", f=[1, 12]),
        dict(id="plate", k="card", display_on=2, f=[2, 15]),
        dict(id="emphasis", k="word", role="big", on=2, display_ink_on=2, t="scale", f=[4, 9])])
    path.write_text(json.dumps(doc))
    out=json.loads(Path(finish_layout(path)).read_text())
    assert [i["f"] for i in out["items"] if i["id"].startswith("name")] == [[1, 6]]
    assert [i["f"] for i in out["items"] if i["id"].startswith("plate")] == [[4, 9]]


def test_failed_caption_repairs_whole_page_and_keeps_other_pages(tmp_path):
    F = wf1_film.F
    voices=dict(cap=dict(font="InterDisplay-Bold.ttf", px=58, track=0))
    words=[dict(k="word", role="cap", id=f"w{i}", v="cap", x=.25+i*.08, y=.65, t=t, f=f)
           for i,(t,f) in enumerate([("quite",[0,12]),("a",[3,12]),("year",[12,17])])]
    scene=dict(items=words, frame=[1080,1920], voices=voices)
    path=repair_contrast(scene, tmp_path/"scene.json", ["w1"], F.Ruler(voices))
    doc=json.loads(Path(path).read_text())
    plate=doc["items"][-1]
    assert plate["f"] == [0,12] and plate["mode"] == "fill" and plate["c"] == "#101010"
    assert [w.get("ink") for w in doc["items"][:3]] == ["#FFFFFF","#FFFFFF",None]


def test_measure_reads_the_ink_seat_on_encoded_frames(tmp_path):
    import shutil
    import subprocess
    from types import SimpleNamespace
    import pytest
    from wf1_type import measure_contrast
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")
    path = str(tmp_path / "bright.mp4")
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=white:s=270x480:r=30:d=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)
    doc = dict(frame=[270,480], items=[dict(id="word", k="word", role="cap", f=[2,12], t="year",
                                               v="cap", x=.2, y=.6, ink="#FFFFFF")])
    ruler = SimpleNamespace(width=lambda *_: 50)
    assert measure_contrast(path, doc, ff, ruler)["word_ids"] == ["word"]
    word = doc["items"][0]
    doc["items"] = [dict(word, id=f"word{i}", f=[i, i+1], ink="#101010") for i in range(135)]
    result = measure_contrast(path, doc, ff, ruler)
    assert result["captions_sampled"] == 135 and result["ok"]
