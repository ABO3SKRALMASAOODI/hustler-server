"""Bake-off 25 Oct: a walled account's "record a demo of <url>" in an empty
project starts the free trial's URL First cut at once. It used to wait for
the concierge model (OpenAI-funded), whose failure degrades to a template
reply, so the most common first request was silently walled whenever the
wallet or the model failed."""
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video  # noqa: E402


class Cur:
    def __init__(self):
        self.inserts = []

    def execute(self, sql, args=None):
        if "INSERT INTO chat_messages" in sql:
            self.inserts.append(args)


CONCIERGE = {"can_act": True, "session_id": 9, "stage": "no_video"}


def _patch(monkeypatch, gated=True, job=41):
    calls = []
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda cur, uid: gated)

    def first_cut(cur, uid, pid, mid):
        calls.append((uid, pid, mid))
        return job
    monkeypatch.setattr(video, "_trial_url_first_cut", first_cut)
    return calls


def test_a_trial_url_request_starts_the_first_cut_without_the_concierge(monkeypatch):
    calls = _patch(monkeypatch)
    cur = Cur()
    job = video._trial_url_direct(cur, 5, 7, 11, "record a demo of https://valmera.io", CONCIERGE)
    assert job == 41 and calls == [(5, 7, 11)]
    session_id, content, meta = cur.inserts[0]
    assert session_id == 9 and content == video.TRIAL_URL_FIRST_CUT_REPLY
    assert meta.adapted["first_cut_job_id"] == 41


def test_everything_else_still_goes_to_the_concierge(monkeypatch):
    # no URL
    calls = _patch(monkeypatch)
    assert video._trial_url_direct(Cur(), 5, 7, 11, "make me a video", CONCIERGE) is None
    # a subscriber (not walled) keeps the concierge's judgement
    calls = _patch(monkeypatch, gated=False)
    assert video._trial_url_direct(Cur(), 5, 7, 11, "demo https://x.io", CONCIERGE) is None
    assert calls == []
    # a stage where the canvas cannot act (a video indexing, files in the tray)
    calls = _patch(monkeypatch)
    assert video._trial_url_direct(Cur(), 5, 7, 11, "demo https://x.io",
                                   dict(CONCIERGE, can_act=False)) is None
    assert calls == []
    # not admitted by the trial (flag off, budget spent, credit switch off)
    cur = Cur()
    _patch(monkeypatch, job=None)
    assert video._trial_url_direct(cur, 5, 7, 11, "demo https://x.io", CONCIERGE) is None
    assert cur.inserts == []
