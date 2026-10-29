"""No internal note ever reaches a user-visible reply, and the reply matches
what the turn changed (30 Oct 2026).

The incident: a customer's reply read
  "*(system: this turn DID modify the edit — see the editing steps above)*
   Nothing was changed this turn. The current cut is still v12 ..."
The honesty guard prepended its own internal note to a model draft that
still denied the turn's real writes. Now the guard answers from the turn
facts, and read_tools.add_message strips any internal note from every
assistant message the worker posts.
"""
import ast
import os
from types import SimpleNamespace

import agent_loop
import read_tools

INCIDENT_DRAFT = (
    "Nothing was changed this turn. The current cut is still v12, with "
    "fillers, dead air and the doubled \"we\" removed and captions on, and I "
    "rendered it for review. The two framing findings are still open: the "
    "speaker's head sits close to the top edge in a few moments.")


# ── the choke point: every assistant message ──────────────────────────────

class _Cur:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.sink.append(params)

    def fetchone(self):
        return {"id": 1}


class _Conn:
    def __init__(self):
        self.rows = []

    def cursor(self):
        return _Cur(self.rows)


def _posted(role, text):
    conn = _Conn()
    read_tools.add_message(conn, 7, role, text, {"k": 1})
    return conn.rows[0][2]


def test_add_message_strips_every_internal_note_form():
    forms = [
        "*(system: this turn DID modify the edit — see the editing steps above)*\n\n",
        "(system: no changes were made this turn) ",
        "[system: CONTINUATION — this SAME request was resumed (after a drain)] ",
        "[system note: the user's own messages are written in Latin script] ",
        "_(SYSTEM: note)_ ",
        "(internal note: verifier repair pending) ",
    ]
    for note in forms:
        out = _posted("assistant", note + "Your 27-second cut is ready.")
        assert out == "Your 27-second cut is ready.", (note, out)
        assert not read_tools.has_internal_note(out)


def test_note_in_the_middle_and_nested_brackets():
    text = ("I tightened the pauses. [system: REQUEST BUDGET REACHED (6 "
            "preview renders). Stop editing.] The preview is on the right.")
    assert _posted("assistant", text) == (
        "I tightened the pauses. The preview is on the right.")


def test_unterminated_note_is_cut_to_its_paragraph():
    text = "*(system: this turn DID modify the edit\n\nThe hook now holds 3 s."
    assert _posted("assistant", text) == "The hook now holds 3 s."


def test_a_reply_that_was_only_a_note_is_never_posted_empty():
    out = _posted("assistant", "*(system: no changes were made this turn)*")
    assert out and not read_tools.has_internal_note(out)


def test_ordinary_replies_and_other_roles_are_untouched():
    plain = ("Done: captions (bigger, at chest height) and a 9:16 crop. "
             "Ratio: 9:16 [vertical].")
    assert _posted("assistant", plain) == plain
    # activity rows and user messages are not replies; never rewritten
    raw = "render_preview{} → (system: queued)"
    assert _posted("activity", raw) == raw
    assert _posted("user", "(system: hello)") == "(system: hello)"


# ── the honesty guard answers from the turn facts ─────────────────────────

def _ctx(versions_written=(13, 14), start=12, latest=14, previewed=True,
         urls=None, images=None):
    preview = ({"edl_version": latest, "duration_s": 26.6}
               if previewed else None)
    return SimpleNamespace(
        job={"id": 501}, versions_written=list(versions_written),
        images_generated=list(images or []), urls_fetched=list(urls or []),
        web_recordings=None, audio_extracted=None, last_preview=preview,
        last_selfcheck=None, agent_model="claude-haiku-5-5",
        write_calls=["cut_silences", "add_captions"] if versions_written else [],
        latest_edl=lambda: {"version": latest, "json": {}})


def _stub_model(monkeypatch, redraft):
    calls = []

    def create(client, model, msgs, **kw):
        calls.append(msgs)
        msg = SimpleNamespace(content=redraft)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=None)
    monkeypatch.setattr(agent_loop.llm, "create_with_dialect", create)
    monkeypatch.setattr(agent_loop.llm, "record", lambda *a, **k: None)
    return calls


def _honesty():
    return {"false_claims": 0, "corrective_note": False}


def test_wrote_but_denied_twice_replies_from_the_facts(monkeypatch):
    _stub_model(monkeypatch, INCIDENT_DRAFT)       # the redraft denies again
    ctx = _ctx()
    h = _honesty()
    out = agent_loop._enforce_honesty(ctx, None, [], [], INCIDENT_DRAFT, 12, h,
                                      user_text="clean up my footage")
    assert "(system:" not in out and not read_tools.has_internal_note(out)
    # it says what changed ...
    assert out.startswith("I changed the edit in this turn: it is now "
                          "version 14 (it was version 12)")
    assert "preview on the right shows it" in out
    # ... and nothing that contradicts it survives
    assert not agent_loop.DENY_CLAIM.search(out)
    assert "still v12" not in out
    assert not agent_loop._reply_violations(out, True, True, True)
    # the draft's true sentences are kept
    assert "framing findings are still open" in out
    assert h["corrective_note"] is True and h["discarded_drafts"]


def test_wrote_with_no_preview_never_claims_one(monkeypatch):
    draft = ("Nothing was changed. I re-rendered the preview. The captions "
             "sit at chest height.")
    _stub_model(monkeypatch, draft)
    out = agent_loop._enforce_honesty(_ctx(previewed=False), None, [], [],
                                      draft, 12, _honesty())
    assert "preview on the right" not in out
    assert not agent_loop.RENDER_CLAIM.search(out)
    assert not agent_loop.DENY_CLAIM.search(out)
    assert "The captions sit at chest height." in out
    assert not agent_loop._reply_violations(out, True, False, True)


def test_asset_only_turn_says_the_edit_did_not_change(monkeypatch):
    draft = "I rendered the preview with your song."     # no preview exists
    _stub_model(monkeypatch, draft)
    ctx = _ctx(versions_written=(), latest=12, previewed=False,
               urls=[{"filename": "song.mp3", "storage_key": "media/1/s.mp3"}])
    out = agent_loop._enforce_honesty(ctx, None, [], [], draft, 12,
                                      _honesty())
    assert out.startswith("I didn't change the edit in this turn, but the "
                          "linked media was downloaded")
    assert "(system:" not in out
    assert not agent_loop._reply_violations(out, False, False, True)


def test_a_redraft_that_agrees_is_used_as_is(monkeypatch):
    good = "I cut the pauses and added captions; the preview is on the right."
    _stub_model(monkeypatch, good)
    out = agent_loop._enforce_honesty(_ctx(), None, [], [], INCIDENT_DRAFT,
                                      12, _honesty())
    assert out == good


# ── no code path composes an internal note into a reply ───────────────────

def test_agent_loop_has_no_user_reply_built_with_a_system_note():
    """Notes the MODEL reads start with '[system' and go into `messages`;
    the only '(system:' string the loop ever built was the leaked one."""
    src = open(os.path.join(os.path.dirname(agent_loop.__file__),
                            "agent_loop.py")).read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "(system:" not in node.value, node.value[:80]
