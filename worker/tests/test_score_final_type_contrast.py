"""Type must read against the crop that is delivered, including late source guards."""
import copy
import json

import numpy as np

from score import compiler as S, lower, source_guard
from tests.test_score_compiler import talk_index


def test_final_source_crop_rechecks_caption_contrast(monkeypatch):
    monkeypatch.setattr(S, "GOVERN", False)
    pixels = np.zeros((1, S.THUMB_H, S.THUMB_W), dtype=np.float32)
    pixels[:, :, S.THUMB_W // 2:] = 1
    monkeypatch.setattr(S, "thumbs", lambda ctx, pic: pixels)
    before = []

    def move_crop(l1, *args, **kwargs):
        before.extend(copy.deepcopy(i) for i in l1["items"] if i["k"] == "word")
        for it in l1["items"]:
            if it["k"] == "shot":
                it["crop"] = [0.5, 0, 1, 1]
        return {"test_final_crop": True}

    monkeypatch.setattr(source_guard, "apply", move_crop)
    text = "\n".join(json.dumps(row) for row in [
        {"k": "film", "v": 1, "fps": 30, "frame": "9:16", "spine": "main", "band": "own", "own": {"med": 2, "p10": 1, "p90": 4}, "seed": 3},
        {"k": "dna", "plate": "#101014", "ink": "#FFFFFF", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"}, "seat": [0.5, 0.62]},
        {"k": "b", "id": "b01", "w": "0-3", "job": "speech", "pic": [{"src": "main", "crop": [0, 0, 0.5, 1]}], "type": "0-3"},
    ])
    result = S.compile_score(text, index=talk_index(5, faces=False))
    after = [i for i in result.l1["items"] if i["k"] == "word"]
    assert before and any(not i.get("edge") for i in before)
    assert all(i.get("edge") for i in after if i["id"].startswith("b01.c0."))
    assert result.meter["final_type_contrast"]["edged_words"] == [
        i["id"] for i in before if i["id"].startswith("b01.c0.") and not i.get("edge")]
    assert [{k: v for k, v in i.items() if k != "edge"} for i in after] == [
        {k: v for k, v in i.items() if k != "edge"} for i in before]
    captions = lower.lower_captions(result.l1, {}, src_dur=5)
    assert captions and any(c["style"]["outline_width"] > 0 for c in captions)


def test_final_contrast_preserves_readable_and_unknown_type(monkeypatch):
    from types import SimpleNamespace
    word = {"k": "word", "id": "cap.1", "t": "Speech", "v": "a", "ink": "#FFFFFF", "x": .3, "y": .6, "f": [0, 30]}
    shot = {"k": "shot", "id": "s1", "pic": "main", "f": [0, 30], "crop": [0, 0, 1, 1]}
    ctx = SimpleNamespace(fps=30, W=1080, H=1920, voices={"a": {"px": 58, "font": ""}})
    l1 = {"items": [shot, word]}
    before = copy.deepcopy(l1)
    for pixels in (None, np.zeros((1, S.THUMB_H, S.THUMB_W), dtype=np.float32)):
        monkeypatch.setattr(S, "thumbs", lambda ctx, pic: pixels)
        result = S.final_type_contrast(ctx, l1)
        assert result["edged_words"] == []
        assert result["measured_words"] == int(pixels is not None)
        assert l1 == before
