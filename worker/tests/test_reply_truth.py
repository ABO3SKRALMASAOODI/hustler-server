"""A reply never says "done" for what the film does not show (worker/reply_truth.py, 14 Nov 2026).

The drafts are gpt-5.5's on the Gauntlet's p5518 and p5533 replays."""

import json
from types import SimpleNamespace

import llm
import reply_truth
from score import asks

P5518_DRAFT = ("تم إزالة العلامة المائية مع الحفاظ على نفس توقيت الفيديو، والنسخة الحالية مدتها "
               "حوالي ٥.٦ ثوانٍ. بقيت العناوين والكابشنات الموجودة في أول قصّة كما هي.")
P5518_CLAIM = "تم إزالة العلامة المائية مع الحفاظ على نفس توقيت الفيديو"
P5518_HONEST = "ما زالت العلامة المائية ظاهرة على الوجه وعلى الملابس، ولم أتمكن من إزالتها دون تشويه الصورة"
P5533_DRAFT = ("Done — I darkened the non-floor background across the full clip to match the curtain "
               "backdrop, while keeping the wooden floor visible and unchanged. The timing and full "
               "24.8-second dance take are preserved.")


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []


def _fake_create(client, model, messages, **kw):
    client.calls.append((model, messages, kw))
    args = json.dumps({"claims": client.rows}, ensure_ascii=False)
    call = SimpleNamespace(function=SimpleNamespace(name="record_reply_truth", arguments=args))
    msg = SimpleNamespace(tool_calls=[call], content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                           usage=None)


class _Ctx:
    def __init__(self, preview=True):
        self.last_preview = {"edl_version": 8, "screening_pages": [{"key": "k1", "frames": []}]} \
            if preview else None
        self.job = {"id": 1}

    def latest_edl(self):
        return {"version": 8, "json": {"fx": [{"type": "broll", "start": 45.52, "end": 48.02, "credit": {
            "page_url": "https://www.pexels.com/video/a-person-adding-honey-in-tea-123/"}}]}}


def _wire(monkeypatch, tmp_path):
    page = tmp_path / "p.jpg"
    page.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(asks, "frame_pages", lambda ctx, preview, limit=3: [(str(page), "sheet 1")])
    monkeypatch.setattr(asks, "stored_preview", lambda ctx, v: None)
    monkeypatch.setattr(llm, "create_with_dialect", _fake_create)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)


def test_a_false_done_is_told_truthfully_in_the_customers_language(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    client = _Client([{"quote": P5518_CLAIM, "status": "contradicted",
                       "seen": "sheet 1 tiles 1-16: the logo still on her face, VisionStory on the clothes",
                       "honest": P5518_HONEST},
                      {"quote": "بقيت العناوين والكابشنات الموجودة في أول قصّة كما هي",
                       "status": "shown", "seen": "titles visible", "honest": ""}])
    honesty = {}
    out = reply_truth.enforce(_Ctx(), client, "gpt-6.1-sol", "شيل العلامة المائية", P5518_DRAFT,
                              honesty)
    assert P5518_CLAIM not in out and P5518_HONEST in out
    assert "بقيت العناوين" in out                      # the true sentence is kept as it was
    assert honesty["reply_truth"]["contradicted"][0]["quote"] == P5518_CLAIM
    model, messages, kw = client.calls[0]
    assert model == "gpt-6.1-sol"
    assert kw["tool_choice"]["function"]["name"] == "record_reply_truth"
    assert any(p.get("type") == "image_url" for p in messages[1]["content"])


def test_a_clause_quote_replaces_its_whole_sentence():
    draft = ("Added three cutaways: the Himalayas, honey in tea and bees, timed to your narration. "
             "You remain the focus.")
    out, swapped = reply_truth.corrected(draft, [
        {"quote": "the Himalayas, honey in tea and bees", "status": "contradicted", "seen": "",
         "honest": "Added two cutaways, the Himalayas and bees, timed to your narration."}])
    assert out == "Added two cutaways, the Himalayas and bees, timed to your narration. You remain the focus."


def test_the_check_reads_what_lies_between_the_tiles(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(asks, "asset_names", lambda ctx, edl: {})
    client = _Client([])
    reply_truth.enforce(_Ctx(), client, "m", "add b roll", "Added honey in tea at 45 s.")
    text = client.calls[0][1][1]["content"][0]["text"]
    assert "[45.5-48.0 s] broll: a person adding honey in tea" in text


def test_an_honest_line_whose_quote_drifted_is_added_after_the_lead():
    out, swapped = reply_truth.corrected(P5533_DRAFT, [
        {"quote": "keeping the dancers unchanged", "status": "contradicted", "seen": "",
         "honest": "The dark box also dims both dancers."}])
    assert swapped and out.startswith("Done — I darkened") and "also dims both dancers" in out


def test_nothing_changes_when_the_film_agrees_or_cannot_be_seen(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    client = _Client([{"quote": "Done", "status": "shown", "seen": "", "honest": ""},
                      {"quote": "The timing", "status": "cannot_tell", "seen": "", "honest": ""}])
    assert reply_truth.enforce(_Ctx(), client, "m", "req", P5533_DRAFT) == P5533_DRAFT
    client = _Client([])
    assert reply_truth.enforce(_Ctx(preview=False), client, "m", "req", P5533_DRAFT) == P5533_DRAFT
    assert client.calls == []                         # no film, no call


def test_a_failing_check_posts_the_draft(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm, "create_with_dialect", boom)
    assert reply_truth.enforce(_Ctx(), _Client([]), "m", "req", P5533_DRAFT) == P5533_DRAFT


def test_strict_question_preserves_question_but_rewrites_unproved_preamble(monkeypatch,tmp_path):
    _wire(monkeypatch,tmp_path)
    client = _Client([{'quote':'The background is done.', 'status':'contradicted',
                       'honest':'The wall is still visible.', 'seen':'wall remains'}])
    out = reply_truth.enforce(_Ctx(),client,'m','req',
                              'The background is done. Which music would you like?',strict=True)
    assert out=='The wall is still visible. Which music would you like?'


def test_strict_question_fails_closed_on_provider_error_and_quote_drift(monkeypatch,tmp_path):
    _wire(monkeypatch,tmp_path)
    monkeypatch.setattr(reply_truth,'check',lambda *a,**k:None)
    assert reply_truth.enforce(_Ctx(),_Client([]),'m','req',P5533_DRAFT,strict=True)==reply_truth.UNVERIFIED
    out,_ = reply_truth.corrected(P5533_DRAFT,[{'quote':'wrong quoted words',
        'status':'contradicted','honest':'The dancers were dimmed.'}],strict=True)
    assert out==reply_truth.UNVERIFIED


def test_strict_question_without_frames_does_not_accept_a_claim_as_shown(monkeypatch,tmp_path):
    _wire(monkeypatch,tmp_path)
    client = _Client([{'quote':'Done.', 'status':'shown','honest':'','seen':''}])
    out = reply_truth.enforce(_Ctx(False),client,'m','req','Done. Which music?',strict=True)
    assert 'Done.' not in out and 'Which music?' in out
    question='Can you upload the original without the mark?'
    assert reply_truth.enforce(_Ctx(False),_Client([]),'m','req',question,strict=True)==question
