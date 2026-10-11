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


def test_media_already_in_the_edl_never_blocks_a_later_batch():
    # Valmera's own tools place keys that never appear in the project's
    # assets table (erase patches, subject mattes, fetched stock, library
    # sounds); re-checking the whole EDL refused EVERY later batch once one
    # was present (the Diamandis run: 6 of 9 editors). Only media a batch
    # introduces must be attached to the project.
    placed = {"media/9/fetched-stock.jpg": {"duration_s": None}}
    card = {"id": "card", "template": "image_card", "start": 1.0, "end": 4.0,
            "params": {"image": "media/9/fetched-stock.jpg"}}
    edl = edit_batch.apply_batch(default_edl(20), [
        {"action": "upsert", "layer": "motion", "id": "card", "value": card}], 20, placed)
    out = edit_batch.apply_batch(edl, [
        {"action": "upsert", "layer": "motion", "id": "hook", "value": _hook()}], 20, KEYS)
    assert {m["id"] for m in out["motion"]} == {"card", "hook"}
    moved = edit_batch.apply_batch(edl, [
        {"action": "upsert", "layer": "motion", "id": "card", "value": {"id": "card", "end": 3.0}}],
        20, KEYS)
    assert moved["motion"][0]["end"] == 3.0
    foreign = {"id": "other", "template": "image_card", "start": 5.0, "end": 7.0,
               "params": {"image": "media/2/someone-elses.jpg"}}
    with pytest.raises(ValueError, match="media attached to this project"):
        edit_batch.apply_batch(edl, [
            {"action": "upsert", "layer": "motion", "id": "other", "value": foreign}], 20, KEYS)
