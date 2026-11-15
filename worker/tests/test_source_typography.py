import json
from types import SimpleNamespace

import pytest

import source_typography as st
from score import taste


def index():
    return {"visual_storyboard": {"evidence": [
        {"evidence_id": f"e{i}", "representative_t": i,
         "visible_text": text, "semantic_description": "Narrative type on the picture"}
        for i, text in enumerate(["Your moment", "comes from", "you"])]},
        "sentences": [{"t0": 0, "t1": 3, "text": "Your moment comes from you."}]}


@pytest.mark.parametrize("track,expected", [("complete", True), ("partial", False), ("none", False)])
def test_only_a_grounded_complete_existing_track_replaces_duplicate_captions(track, expected):
    def ask(system, user, **kwargs):
        data = json.loads(user)
        assert data["frames"][0]["text"] == "Your moment"
        assert data["sentences"][0]["text"] == "Your moment comes from you."
        return {"text": json.dumps(dict(caption_track=track, designed_layout=True,
                                        evidence_ids=["e0", "e1", "e2"]))}
    idx = index()
    idx["source_typography"] = st.classify(idx, ask)
    assert st.captioned(idx) is expected
    if expected:
        assert taste.upload_captions(SimpleNamespace(index=idx, words=[])) == 1


@pytest.mark.parametrize("answer", ["bad json", '{"caption_track":"complete","evidence_ids":["made-up","e1","e2"]}',
                                    '{"caption_track":"complete","evidence_ids":["e0","e0","e0"]}'])
def test_missing_or_invented_evidence_cannot_remove_a_caption_track(answer):
    assert st.classify(index(), lambda *a, **k: {"text": answer}) == {}


def test_no_model_call_when_the_index_has_no_printed_text():
    assert st.classify({}, lambda *a, **k: pytest.fail("no evidence to classify")) == {}


def test_picture_layout_can_be_grounded_without_claiming_burned_captions():
    idx = index()
    for row in idx["visual_storyboard"]["evidence"]:
        row["visible_text"] = ""
        row["semantic_description"] = "Three photographs arranged across the canvas"
    answer = dict(caption_track="none", designed_layout=True, evidence_ids=["e0", "e1", "e2"])
    assert st.classify(idx, lambda *a, **k: {"text": json.dumps(answer)})["designed_layout"]
    answer["caption_track"] = "complete"
    assert st.classify(idx, lambda *a, **k: {"text": json.dumps(answer)}) == {}


def test_pending_evidence_can_be_classified_when_it_arrives(monkeypatch):
    import llm
    ctx = SimpleNamespace(index={"visual_storyboard_pending": True})
    def ask(*a, **k):
        return {"text": json.dumps(dict(caption_track="complete", designed_layout=True,
                                        evidence_ids=["e0", "e1", "e2"]))}
    monkeypatch.setattr(llm, "ask_text", ask)
    assert st.prepare(ctx) == {}
    assert "source_typography" not in ctx.index
    ctx.index.update(index(), visual_storyboard_pending=False)
    assert st.prepare(ctx)["caption_track"] == "complete"


def test_first_cut_preserves_existing_type_but_still_builds_the_edit(monkeypatch):
    from test_first_cut_visual import _talking_chain

    def prepare(ctx):
        value = dict(caption_track="complete", designed_layout=True)
        ctx.index = {**ctx.index, "source_typography": value}
        ctx.index["video"] = {**ctx.index["video"], "width": 1280, "height": 720}
        return value
    monkeypatch.setattr(st, "prepare", prepare)
    calls = _talking_chain(monkeypatch, q1=True)
    assert "add_captions" not in calls
    assert "add_motion_graphic" not in calls
    assert "auto_reframe" not in calls
    assert "set_frame" in calls
    assert "set_master_loudness" in calls
    assert "source_broll" in calls
