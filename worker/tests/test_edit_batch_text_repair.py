"""apply_edit_batch repairs text values it can repair (p5993, 19 Nov 2026).

A paying user's stand-up promo: the show line built word by word over 2 s
clips. Two batches were refused and each cost the editor a turn: five texts
set without ids ("EDL shape invalid: texts.0.id Field required") and a word
span of 0.25 s ("shorter than 0.3s"). The batch gives the ids and stretches
the span, and its notes say what it repaired.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import edit_batch  # noqa: E402
from schemas import default_edl  # noqa: E402

LINE = "The comedy show you want to see this weekend".split()


def _line(ids=True, step=0.25, start=0.5, hold_to=32.59):
    out = []
    for k in range(len(LINE)):
        t = {"text": " ".join(LINE[:k + 1]), "start": round(start + k * step, 2),
             "end": round(start + (k + 1) * step, 2), "template": "title",
             "x": 0.5, "y": 0.5, "entrance": "none", "exit": "none"}
        if k == len(LINE) - 1:
            t["end"] = hold_to
        if ids:
            t["id"] = f"promo_{k + 1}"
        out.append(t)
    return out


def test_texts_set_without_ids_are_given_ids_and_said():
    notes = []
    value = [dict(t, end=32.59) for t in _line(ids=False, step=1.7)][:5]
    out = edit_batch.apply_batch(default_edl(32.6), [
        {"action": "set", "layer": "texts", "value": value}], 32.6, {},
        notes=notes)
    ids = [t["id"] for t in out["texts"]]
    assert ids == ["tx1", "tx2", "tx3", "tx4", "tx5"]
    assert any("came without an id" in n and "tx1" in n for n in notes), notes
    # an id already in the layer is never reused
    again = edit_batch.apply_batch(out, [
        {"action": "set", "layer": "texts",
         "value": out["texts"] + [{"text": "Sunday", "start": 3.0, "end": 9.0}]}],
        32.6, {})
    assert sorted(t["id"] for t in again["texts"]) == \
        ["tx1", "tx2", "tx3", "tx4", "tx5", "tx6"]


def test_a_word_by_word_build_under_the_minimum_plays_without_overlap():
    notes = []
    out = edit_batch.apply_batch(default_edl(32.6), [
        {"action": "set", "layer": "texts", "value": _line()}], 32.6, {},
        notes=notes)
    texts = sorted(out["texts"], key=lambda t: t["start"])
    assert len(texts) == len(LINE)
    assert [t["start"] for t in texts] == [t["start"] for t in _line()]
    # the words follow each other: no two on screen together, order kept
    for a, b in zip(texts, texts[1:]):
        assert b["start"] >= a["end"] - 1e-6, (a, b)
    assert [t["text"] for t in texts] == [" ".join(LINE[:k + 1])
                                          for k in range(len(LINE))]
    assert texts[0]["start"] == 0.5 and texts[-1]["end"] == 32.59
    assert notes == []


def test_a_short_text_at_the_end_keeps_its_onset_inside_the_programme():
    out = edit_batch.apply_batch(default_edl(10.0), [
        {"action": "upsert", "layer": "texts", "id": "end",
         "value": {"text": "Sunday", "start": 9.9, "end": 10.0}}], 10.0, {})
    t = out["texts"][0]
    assert t["end"] == 10.0 and t["start"] == 9.9


def test_overlapping_texts_that_were_meant_together_are_left_together():
    out = edit_batch.apply_batch(default_edl(10.0), [
        {"action": "set", "layer": "texts", "value": [
            {"id": "a", "text": "TOP", "start": 1.0, "end": 1.2, "y": 0.2},
            {"id": "b", "text": "BOTTOM", "start": 1.0, "end": 3.0, "y": 0.8}]}],
        10.0, {})
    by = {t["id"]: t for t in out["texts"]}
    assert (by["a"]["start"], by["a"]["end"]) == (1.0, 1.3)
    assert (by["b"]["start"], by["b"]["end"]) == (1.0, 3.0)


def test_twenty_spoken_words_and_a_separate_title_keep_their_times():
    words = [{"id": f"w{i}", "text": str(i), "start": 1+i/4, "end": 1.25+i/4}
             for i in range(20)]
    title = {"id": "later", "text": "Doors open", "start": 6, "end": 8}
    out = edit_batch.apply_batch(default_edl(8), [{"action": "set", "layer": "texts",
                                  "value": words + [title]}], 8, {})
    assert [(t["start"], t["end"]) for t in out["texts"]] == [
        (t["start"], t["end"]) for t in words + [title]]


def test_repaired_word_run_keeps_its_authored_position_size_and_final_hold():
    from score import asks

    words = [dict(word, x=0.5, y=0.48, template="lower_third", font_size=0.09,
                  text_align="center") for word in _line(ids=False, step=0.2)]
    edl = edit_batch.apply_batch(default_edl(32.6), [
        {"action": "set", "layer": "texts", "value": words}], 32.6, {})
    for title, word in zip(asks.facts(edl)["titles"], words, strict=True):
        for field in ("text", "template", "start", "end", "x", "y", "font_size", "text_align"):
            assert title[field] == word[field]
