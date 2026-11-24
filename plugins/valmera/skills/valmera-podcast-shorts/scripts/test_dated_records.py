from types import SimpleNamespace
import pytest

import wf1_short as wf
import wf1_display as display


def citation(url, speaker, words):
    return dict(url=url, publisher=speaker, quotations=[dict(url=url, sha256="checked",
                speaker=speaker, words=words, event="Publication", locator="Opening")])


@pytest.mark.parametrize("reverse", [True, False])
def test_scope_uses_selected_transaction_and_clock(reverse):
    other = citation("profile", "John Collison", "it would be an opt-in project")
    tender = citation("announcement", "Stripe", "liquidity to current and former employees")
    claim = dict(first=0,last=4,stance="characterization",speaker="Host",report="They seem to be staying private.",
                 scope_question="What liquidity was available while private?", scope_kind="dated_record")
    choice = dict(source="announcement", words=tender["quotations"][0]["words"], heading="Employee tender",
                  event_date="24 Feb 2026")
    refs = [dict(scope_for=0,statement="A listing was optional.",sources=[other]),
            dict(scope_for=0,statement="The tender offered employees liquidity.",record=choice,sources=[other,tender])]
    pack = dict(coverage=dict(speaker_claims=[claim]), referent_research=dict(references=refs[::-1] if reverse else refs))
    ed = display.editorial(SimpleNamespace(args={}), {}, pack)
    fact = display.claim_fact(ed["speaker_claims"][0], {}, ed["sources"], [])
    assert fact["source"]["url"] == "announcement"
    assert fact["excerpt"]["words"] == choice["words"]
    assert fact["record"]["event_date"] == "24 Feb 2026"
    assert fact["perspective"]["report"] == claim["report"]


def test_checked_primary_document_does_not_trigger_motion_render(tmp_path, monkeypatch):
    monkeypatch.setattr(wf.wf3, "load_pack", lambda ctx: {})
    (tmp_path / "pack.json").write_text('{"pack": {}}')
    ctx = SimpleNamespace(supply=str(tmp_path),log=lambda s: None)
    pic = dict(path="announcement.jpg",window=0,mode="document",document=dict(url="announcement",sha256="checked"))
    report = dict(placements=[dict(id=0,ok=True,motion_ok=False)],moving_share=.59)
    sc = dict(beats=dict(pictures=[pic], display_facts=[dict(facts=[dict(record=dict(source="announcement"))])]))
    assert not wf._rebuild_motion(ctx, {}, sc, report, defer=True)


def test_identical_delivery_without_segment_log_reuses_decoded_review(tmp_path, monkeypatch):
    import placement_check as pc
    from test_placement_check import _rows, _Clock, _approving
    path = tmp_path / "delivery.mp4"
    path.write_bytes(b"same encoded film")
    calls, decoded = [], []
    monkeypatch.setattr(pc, "evidence", lambda row, ff: decoded.append(row[3]) or [b"pixels"] * 3)
    for _ in range(2):
        pc.check(_rows(path, [3]), dict(segments=[dict(say="word")]), _Clock(),
                 str(tmp_path), "unused", call=_approving(calls), review="composed")
    assert len(calls) == len(decoded) == 1


def test_completed_record_does_not_research_another_listing_forecast(tmp_path):
    import wf1_context
    src = citation("filing", "Issuer", "the employee tender closed")
    ref = dict(scope_for=0, statement="Employees sold shares.", sources=[src],
               record=dict(source="filing", words="the employee tender closed", heading="Tender", event_date="2018"))
    claim = dict(first=0,last=0,stance="characterization",speaker="Host",report="They seem likely to wait.",
                 scope_question="Could existing holders sell?", scope_kind="dated_record")
    cov = dict(speaker_claims=[claim], words=[dict(start=0,end=3)], story=dict(episode_start=100))
    script = dict(claims=[dict(start=100,end=103,speaker="Host",subject="Issuer",report=claim["report"])])
    pack = dict(coverage=cov, referent_research=dict(references=[ref]))
    assert not wf1_context.complete_editorial(SimpleNamespace(args={}, supply=str(tmp_path)), script, pack, None)
