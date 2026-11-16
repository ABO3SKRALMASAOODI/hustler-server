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


@pytest.mark.parametrize("ids,accepted", [(["e0", "e1", "e2"], True),
                                         (["made-up", "e1", "e2"], False)])
def test_scout_classifies_type_in_its_existing_deadline_bounded_call(tmp_path, monkeypatch, ids, accepted):
    from score import scout
    idx = index()
    calls = []
    answer = dict(scope="film", wishes=[], fixes=[], source_typography=dict(
        caption_track="complete", designed_layout=True, evidence_ids=ids))

    def stream(model, system, content, **kwargs):
        calls.append(kwargs)
        prompt = content[0]["text"]
        assert '"id": "e0"' in prompt and "Your moment" in prompt
        assert "source_typography in the same JSON object" in prompt
        assert "Return JSON only:" not in prompt
        return SimpleNamespace(text=json.dumps(answer), usd=0, seconds=.1,
                               first_s=.1, stop="end")

    monkeypatch.setattr(scout.call, "stream", stream)
    result = scout.run(str(tmp_path), idx, {},
                       dict(dur=3, W=720, H=1280, shots=[]), deadline=123)
    assert len(calls) == 1 and calls[0]["deadline"] == 123
    assert calls[0]["purpose"] == "director_scout"
    assert bool(result["source_typography"]) is accepted
    if accepted:
        ctx = SimpleNamespace(index={**idx, "source_typography": result["source_typography"]})
        import llm
        monkeypatch.setattr(llm, "ask_text", lambda *a, **k: pytest.fail("duplicate model call"))
        assert st.prepare(ctx)["caption_track"] == "complete"


def test_director_entry_does_not_spend_its_deadline_on_a_separate_type_call(monkeypatch):
    import first_cut
    from score import film
    ctx = SimpleNamespace(index=index())
    monkeypatch.setattr(first_cut, "_await_first_cut_storyboard", lambda *a: None)
    monkeypatch.setattr(st, "prepare", lambda *a: pytest.fail("unbounded classification before Scout"))
    expected = object()
    monkeypatch.setattr(film, "attempt", lambda *a: expected)
    assert first_cut._director_film(ctx, None, {}, 0, {}, None, False) is expected


def test_refreshing_director_evidence_never_calls_a_model(monkeypatch):
    import llm
    ctx = SimpleNamespace(index={"visual_storyboard_pending": True,
                                 "first_film": {"frame": "9:16"}, "shots": [{"id": 9}]})
    monkeypatch.setattr(llm, "ask_text", lambda *a, **k: pytest.fail("unbounded classification before Direct"))
    st.refresh_index(ctx, SimpleNamespace(run=lambda *a: {"json": {
        **index(), "visual_storyboard_pending": False}}), "source-sha")
    assert len(st.observations(ctx.index)) == 3
    assert ctx.index["first_film"] == {"frame": "9:16"}
    assert ctx.index["shots"] == [{"id": 9}]


def test_prepared_film_carries_scout_type_evidence_into_direct_without_a_second_call(tmp_path, monkeypatch):
    import time
    import db
    import llm
    from score import bed, brief, direct, film, preparation, scout, supply

    source = tmp_path / "source.mp4"
    source.touch()
    original = {"visual_storyboard_pending": True, "words": []}
    refined = {**original, "shots": [{"id": 81}]}
    per = dict(dur=3, W=720, H=1280, shots=[])
    ctx = SimpleNamespace(index=original, project_id=1, proxy_path=lambda: str(source))
    monkeypatch.setattr(preparation, "restore", lambda *a: (refined, per))
    monkeypatch.setattr(brief, "boards_for", lambda *a, **k: (None, []))
    monkeypatch.setattr(brief, "build", lambda *a: None)
    monkeypatch.setattr(film, "_asks", lambda *a: [])
    monkeypatch.setattr(film, "_say", lambda *a: None)
    monkeypatch.setattr(film, "footage_start", lambda *a: None)
    monkeypatch.setattr(film, "_acquirer", lambda *a: None)
    monkeypatch.setattr(supply, "run", lambda *a, **k: None)
    monkeypatch.setattr(bed, "available", lambda: False)
    monkeypatch.setattr(llm, "ask_text", lambda *a, **k: pytest.fail("second typography model call"))
    calls = []

    def read(fn, *args):
        if fn is db.latest_asset:
            return dict(storage_key="original", sha256="source-sha")
        assert fn is db.get_index_by_sha
        return {"json": {**index(), "visual_storyboard_pending": False}}

    def stream(*args, **kwargs):
        calls.append(kwargs["purpose"])
        return SimpleNamespace(text=json.dumps(dict(scope="film", wishes=[], fixes=[],
            source_typography=dict(caption_track="complete", designed_layout=True,
                                   evidence_ids=["e0", "e1", "e2"]))),
            usd=0, seconds=.1, first_s=.1, stop="end")

    class ReachedDirect(Exception):
        pass

    def generate(work, idx, *args, **kwargs):
        assert idx["source_typography"]["caption_track"] == "complete"
        assert idx["first_film"] == {"frame": "9:16"}
        assert idx["shots"] == [{"id": 81}]
        assert idx is ctx.index
        raise ReachedDirect

    monkeypatch.setattr(scout.call, "stream", stream)
    monkeypatch.setattr(direct, "direct", generate)
    with pytest.raises(ReachedDirect):
        film._make(ctx, SimpleNamespace(run=read), {},
                   {"models": {"scout": "gpt-6-luna", "direct": "gpt-6-sol"},
                    "effort": {"direct": "low"}, "film_usd": .7},
                   time.monotonic() + 150, str(tmp_path / "film"), {"stages": {}})
    assert calls == ["director_scout"]


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


@pytest.mark.parametrize("pending", [True, False])
def test_director_refresh_reads_once_without_waiting_or_replacing_corrected_words(monkeypatch, pending):
    import llm
    ctx = SimpleNamespace(index={"visual_storyboard_pending": True,
                                 "words": [{"w": "verified spelling"}]})
    reads = []
    def read(fn, sha):
        reads.append(sha)
        return {"json": {**index(), "visual_storyboard_pending": pending,
                         "words": [{"w": "old transcript"}]}}
    answer = dict(caption_track="complete", designed_layout=True, evidence_ids=["e0", "e1", "e2"])
    monkeypatch.setattr(llm, "ask_text", lambda *a, **k: {"text": json.dumps(answer)})
    result = st.refresh(ctx, SimpleNamespace(run=read), "source-sha")
    assert reads == ["source-sha"]
    assert ctx.index["words"] == [{"w": "verified spelling"}]
    assert bool(result) is not pending
    assert ctx.index["visual_storyboard_pending"] is pending


@pytest.mark.parametrize("track", ["complete", "partial", "none"])
def test_first_cut_preserves_existing_type_but_still_builds_the_edit(monkeypatch, track):
    import first_cut
    from test_first_cut_visual import _talking_chain
    frames = []
    run = first_cut._run
    def record(ctx, steps, name, args, started):
        if name == "set_frame":
            frames.append(args)
        return run(ctx, steps, name, args, started)
    monkeypatch.setattr(first_cut, "_run", record)

    def prepare(ctx):
        value = dict(caption_track=track, designed_layout=True)
        ctx.index = {**ctx.index, "source_typography": value}
        ctx.index["video"] = {**ctx.index["video"], "width": 1280, "height": 720}
        return value
    monkeypatch.setattr(st, "prepare", prepare)
    calls = _talking_chain(monkeypatch, q1=True)
    if track == "complete":
        assert "add_captions" not in calls
        assert "add_motion_graphic" not in calls
    else:
        assert "add_captions" in calls
    assert "auto_reframe" not in calls
    assert "set_frame" in calls
    assert "set_master_loudness" in calls
    assert "source_broll" in calls
    assert frames == [{"ratio": "9:16", "mode": "pad_blur"}]


def test_fallback_joins_completed_storyboard_before_adding_type(monkeypatch):
    import db
    import llm
    import test_first_cut_visual as fixtures
    monkeypatch.setattr(fixtures._EdlCtx, "project_id", 1, raising=False)
    original = fixtures._talk_index
    monkeypatch.setattr(fixtures, "_talk_index", lambda: {
        **original(), "visual_storyboard_pending": True})
    reads, asks = [], []

    def read(fn, *args):
        reads.append(fn)
        if fn is db.latest_asset:
            return {"sha256": "source-sha"}
        assert fn is db.get_index_by_sha and args == ("source-sha",)
        return {"json": {**index(), "visual_storyboard_pending": False}}

    def ask(*args, **kwargs):
        asks.append(kwargs["purpose"])
        return {"text": json.dumps(dict(caption_track="complete", designed_layout=True,
                                        evidence_ids=["e0", "e1", "e2"]))}

    monkeypatch.setattr(llm, "ask_text", ask)
    calls = fixtures._talking_chain(monkeypatch, q1=True, worker_db=SimpleNamespace(run=read))
    assert reads == [db.latest_asset, db.get_index_by_sha]
    assert asks == ["source_typography"]
    assert "add_captions" not in calls
    assert "add_motion_graphic" not in calls
    assert "set_master_loudness" in calls
    assert "source_broll" in calls
