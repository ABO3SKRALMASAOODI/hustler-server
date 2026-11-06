"""An MP4 measured as cut short before its moov box is the customer's copy, and
the chat says so (ticket 189, 5 Nov 2026): it used to be retried, then answered
"nothing is wrong with your file ... you don't need to upload it again". The
measurement is media.UnfinishedFile; ffprobe's words alone prove nothing."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import failure_policy  # noqa: E402
import main  # noqa: E402
import media  # noqa: E402

ERROR = ("ffprobe failed: [mov,mp4,m4a,3gp,3g2,mj2 @ 0x5f487f47d040] moov atom "
         "not found | /tmp/valmera/index_137254/src.mp4: Invalid data found "
         "when processing input")


def _notes(monkeypatch, err, job=None):
    posted = []

    class Db:
        def run(self, fn, *args):
            if fn is main.dbx.get_project:
                return {"chat_session_id": 9}
            if fn is main.dbx.add_job_failure_message:
                posted.append(args)
            return None

    monkeypatch.setattr(main.dbx, "get_project", main.dbx.get_project)
    main._notify_failure(Db(), job or {
        "id": 137254, "type": "index", "project_id": 5498,
        "payload": {"asset_id": 40278}}, err)
    return posted


def test_a_missing_moov_is_not_retried():
    decision = failure_policy.classify(media.UnfinishedFile("no moov"), "index")
    assert decision.kind == "unfinished_file"
    assert decision.retryable is False


def test_the_measured_refusal_needs_no_text_at_all():
    err = media.UnfinishedFile("anything")
    for job_type in ("index", "preview", "final"):
        decision = failure_policy.classify(err, job_type)
        assert decision.kind == "unfinished_file" and not decision.retryable
    assert failure_policy.user_reason(
        err, failure_policy.classify(err, "index")) == "the file is incomplete"


@pytest.mark.parametrize("job_type", ["final", "preview", "preview_check"])
def test_a_missing_moov_in_our_own_render_is_not_blamed_on_the_customer(
        job_type):
    # There the file may be OUR intermediate; only the index reads the upload.
    decision = failure_policy.classify(media.MediaError(ERROR), job_type)
    assert decision.kind != "unfinished_file"


def test_the_customer_is_told_the_truth_once(monkeypatch):
    posted = _notes(monkeypatch, media.UnfinishedFile("no moov"))
    assert len(posted) == 1
    note = posted[0][1]
    assert note == main.INDEX_TRUNCATED_NOTE
    assert "incomplete" in note and "nothing is wrong" not in note.lower()
    assert "on our side" not in note
    assert "moov" not in note and "ffprobe" not in note


def test_a_proxy_only_job_stays_silent(monkeypatch):
    assert _notes(monkeypatch, media.MediaError(ERROR), {
        "id": 1, "type": "index", "project_id": 5498,
        "payload": {"asset_id": 40278, "proxy_only": True}}) == []


def test_another_media_failure_keeps_its_note(monkeypatch):
    posted = _notes(monkeypatch, media.MediaError("ffmpeg exploded"))
    assert posted[0][1] == main.INDEX_SERVICE_FAIL_NOTE


def test_the_reason_words_name_the_file():
    err = media.UnfinishedFile("no moov")
    decision = failure_policy.classify(err, "index")
    assert failure_policy.user_reason(err, decision) == "the file is incomplete"
    assert failure_policy.user_reason(
        media.MediaError("no video stream found"),
        failure_policy.classify(media.MediaError("no video stream found"),
                                "index")) == (
        "the file has no video track we could read")
