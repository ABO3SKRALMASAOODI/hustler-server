"""The Director once a chapter on a long film (film_long.director_chapters): each chapter gets its own sheet and its
asks ride the scene under ``devices``."""
import json
from types import SimpleNamespace

import film_long as FL

SEGS = [("one", "The first company failed in a month."), ("one", "He kept the receipts."),
        ("two", "Ten years later he bought the building."), ("two", "Nobody laughed this time.")]


def test_each_chapter_gets_its_own_directors_devices(tmp_path):
    words, t = [], 0.0
    for i, (_, say) in enumerate(SEGS):
        for w in say.split():
            words.append(dict(w=w, start=t, end=t + 0.3, seg=i))
            t += 0.36
        t += 0.4
    (tmp_path / "words.json").write_text(json.dumps(dict(words=words, duration=t)))
    sc = dict(title="t", segments=[dict(chapter=c, say=s) for c, s in SEGS])
    dur = FL.F.Clock.f(t + FL.F.LEAD_F / FL.F.FPS) + 10
    l1 = dict(dur_f=dur, assets={}, items=[])
    pack = dict(words=str(tmp_path / "words.json"), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg")
    ctx = SimpleNamespace(supply=str(tmp_path), log=lambda _: None, args={})
    rec = FL.director_chapters(ctx, sc, pack, l1, dict(pictures=[]))
    assert [r["chapter"] for r in rec] == ["one", "two"] and all(r["asked"] for r in rec)
    assert (tmp_path / "director_ch00.json").exists() and (tmp_path / "director_ch01.json").exists()
    ids = [a["id"] for a in l1["devices"]]
    assert len(ids) == len(set(ids)) and any(i.startswith("c0") for i in ids) and any(i.startswith("c1") for i in ids)
    half = FL.F.Clock.f(sum(1 for w in words if w["seg"] < 2) * 0.36)
    assert any((a["f"][0] if isinstance(a["f"], list) else a["f"]) >= half for a in l1["devices"] if a["id"].startswith("c1"))
