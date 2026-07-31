"""Correcting one line of the transcript must not strip the rest of it.

Round 69 put two new facts on every word: the SPEAKER who said it, and
whether it is a hesitation ("um"/"uh"). Both are load-bearing —
remove_filler_words cuts the video at filler timestamps, and "keep only the
interviewer" is answerable from speaker labels alone.

The studio's transcript editor rebuilds the ENTIRE word list from the
sentence partition whenever one sentence is corrected (it has to: the edited
sentence changes the word count, so every later sentence's wi0/wi1 moves).
That rebuild used to copy exactly three fields per word, which after round 69
would mean fixing a single typo silently took the whole project back to
undiarized, un-taggable — and remove_filler_words back to the no-op it was
before. Nothing would have failed; the data would just be gone.

    cd backend && python -m pytest tests/test_transcript_edit_fields.py -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402


def _index():
    """Two speakers, one hesitation, three sentences."""
    words = [
        {"w": "So,", "t0": 0.0, "t1": 0.3, "speaker": 0, "filler": False},
        {"w": "um", "t0": 0.3, "t1": 0.6, "speaker": 0, "filler": True},
        {"w": "hello.", "t0": 0.6, "t1": 1.0, "speaker": 0, "filler": False},
        {"w": "Hi", "t0": 1.2, "t1": 1.4, "speaker": 1, "filler": False},
        {"w": "there.", "t0": 1.4, "t1": 1.8, "speaker": 1, "filler": False},
        {"w": "Right", "t0": 2.0, "t1": 2.4, "speaker": 0, "filler": False},
        {"w": "then.", "t0": 2.4, "t1": 2.8, "speaker": 0, "filler": False},
    ]
    sentences = [
        {"id": "s1", "text": "So, um hello.", "t0": 0.0, "t1": 1.0,
         "wi0": 0, "wi1": 2, "speaker": 0},
        {"id": "s2", "text": "Hi there.", "t0": 1.2, "t1": 1.8,
         "wi0": 3, "wi1": 4, "speaker": 1},
        {"id": "s3", "text": "Right then.", "t0": 2.0, "t1": 2.8,
         "wi0": 5, "wi1": 6, "speaker": 0},
    ]
    return {"words": words, "sentences": sentences, "speakers": 2}


def test_editing_one_sentence_keeps_every_other_word_intact():
    out, updated = video._apply_transcript_edit(_index(), "s2", "Hey there!")
    assert updated is not None
    ws = out["words"]
    # the untouched neighbours keep BOTH new fields
    assert [w["speaker"] for w in ws[:3]] == [0, 0, 0]
    assert [w["filler"] for w in ws[:3]] == [False, True, False]
    assert ws[-2]["speaker"] == 0 and ws[-1]["speaker"] == 0
    # and the video is still diarized as far as every reader is concerned
    assert any(w.get("filler") for w in ws), "the hesitation survived"
    assert {w["speaker"] for w in ws} == {0, 1}


def test_the_rewritten_words_inherit_that_sentence_speaker():
    out, _ = video._apply_transcript_edit(_index(), "s2", "Hey there friend")
    s2 = next(s for s in out["sentences"] if s["id"] == "s2")
    new = out["words"][s2["wi0"]:s2["wi1"] + 1]
    assert [w["w"] for w in new] == ["Hey", "there", "friend"]
    assert all(w["speaker"] == 1 for w in new), \
        "the user retyped THAT person's line, not somebody else's"


def test_a_typed_hesitation_is_tagged_like_a_transcribed_one():
    out, _ = video._apply_transcript_edit(_index(), "s3", "Right um then")
    s3 = next(s for s in out["sentences"] if s["id"] == "s3")
    new = out["words"][s3["wi0"]:s3["wi1"] + 1]
    assert [w["filler"] for w in new] == [False, True, False]


def test_word_indices_still_tile_the_list_after_the_edit():
    """The whole reason the rebuild exists — it must not regress."""
    out, _ = video._apply_transcript_edit(_index(), "s1", "So hello again")
    cursor = 0
    for s in out["sentences"]:
        assert s["wi0"] == cursor
        assert s["wi1"] >= s["wi0"]
        cursor = s["wi1"] + 1
    assert cursor == len(out["words"])


def test_an_old_index_without_the_fields_still_edits():
    idx = _index()
    for w in idx["words"]:
        w.pop("speaker"), w.pop("filler")
    for s in idx["sentences"]:
        s.pop("speaker")
    out, updated = video._apply_transcript_edit(idx, "s2", "Hey there")
    assert updated is not None
    assert out["words"][0].get("speaker") is None
