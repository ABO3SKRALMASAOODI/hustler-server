"""Deterministic failures must never tell a user to repeat the same action."""

import main as worker_main


class _FakeDb:
    def __init__(self):
        self.messages = []

    def run(self, fn, *args):
        if fn is worker_main.dbx.get_project:
            return {"chat_session_id": 77}
        if fn is worker_main.dbx.add_message:
            self.messages.append(args)
            return None
        raise AssertionError(f"unexpected DB helper: {fn}")


def _failure_note(error):
    fake = _FakeDb()
    worker_main._notify_failure(
        fake,
        {"id": 9, "type": "shorts_plan", "project_id": 3},
        RuntimeError(error),
    )
    assert len(fake.messages) == 1
    return fake.messages[0][2]


def test_short_source_failure_has_a_non_looping_recovery():
    note = _failure_note("shorts need a longer source")
    assert "Edit it directly here" in note
    assert "Make shorts again" in note


def test_thin_transcript_failure_points_to_a_specific_chat_edit():
    note = _failure_note("no clip-worthy moments")
    assert "build one short" in note
    assert "specific idea" in note
