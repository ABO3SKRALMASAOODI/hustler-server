"""Motion graphics in apply_edit_batch: atomic, validated, project-scoped."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import edit_batch  # noqa: E402
from schemas import default_edl  # noqa: E402

KEYS = {"media/1/photo.jpg": {"duration_s": None}}


def _hook(**extra):
    return {"id": "hook", "template": "hook_title", "start": 0.0, "end": 2.4,
            "params": {"text": "We were promised / *flying cars*"}, **extra}


def test_motion_items_upsert_by_id_and_validate():
    out = edit_batch.apply_batch(default_edl(20), [
        {"action": "upsert", "layer": "motion", "id": "hook", "value": _hook()}], 20, KEYS)
    assert [m["id"] for m in out["motion"]] == ["hook"]
    assert out["motion"][0]["params"]["text"].startswith("We were promised")
    again = edit_batch.apply_batch(out, [
        {"action": "upsert", "layer": "motion", "id": "hook", "value": {"id": "hook", "end": 2.0}}], 20, KEYS)
    assert again["motion"][0]["end"] == 2.0 and again["motion"][0]["template"] == "hook_title"
    gone = edit_batch.apply_batch(again, [{"action": "remove", "layer": "motion", "id": "hook"}], 20, KEYS)
    assert gone["motion"] == []


def test_motion_batches_refuse_foreign_media_unknown_params_and_unmasked_behind():
    photo = {"id": "card", "template": "image_card", "start": 1.0, "end": 4.0,
             "params": {"image": "media/2/someone-elses.jpg"}}
    with pytest.raises(ValueError, match="media attached to this project"):
        edit_batch.apply_batch(default_edl(20), [
            {"action": "upsert", "layer": "motion", "id": "card", "value": photo}], 20, KEYS)
    with pytest.raises(ValueError, match="subject mask"):
        edit_batch.apply_batch(default_edl(20), [
            {"action": "upsert", "layer": "motion", "id": "hook",
             "value": _hook(layer="behind_subject")}], 20, KEYS)
    with pytest.raises(ValueError):
        edit_batch.apply_batch(default_edl(20), [
            {"action": "upsert", "layer": "motion", "id": "hook",
             "value": _hook(params={"text": "Hi", "colour_typo": "#fff"})}], 20, KEYS)
