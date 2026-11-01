"""Ticket #89 (1 Nov 2026): an MCP project assembled from inserted clips and
images (no upload of its own) asked enhance_cursor for a pass. The original
lookup raised, the generic "Could not run the cursor pass" text was read as
TRANSIENT_FAILURE, the caller was invited to retry something that can never
run, and the intake filed our failure as P0. A project without an original
is a precondition, answered as a correction with what does work."""
import agent_tools
import tool_outcome


class _DB:
    def __init__(self, row):
        self.row = row

    def run(self, fn, *args, **kw):
        return self.row


class _Ctx:
    project_id = 5251

    def __init__(self, row):
        self.db = _DB(row)

    def latest_edl(self):
        raise AssertionError("a project with no original must be refused first")


def test_tools_needing_the_original_refuse_a_clip_only_project():
    ctx = _Ctx(None)
    for call in (lambda: agent_tools.enhance_cursor(ctx, scale=2),
                 lambda: agent_tools.erase_region(ctx, x=0.1, y=0.1, w=0.2, h=0.1),
                 lambda: agent_tools.erase_burned_text(ctx)):
        out = call()
        assert out.startswith("REJECTED:"), out
        assert "has none" in out and "Nothing changed" in out
        outcome = tool_outcome.from_legacy(out)
        # the intake skips correction_needed: the caller's fact, not our failure
        assert outcome.status == "correction_needed"
        assert not outcome.retryable


def test_the_old_wording_was_a_transient_failure():
    old = ("Could not run the cursor pass (this project has no original "
           "video). Nothing changed — do NOT tell the user the cursor was "
           "enhanced.")
    assert tool_outcome.from_legacy(old).status == "transient_failure"


def test_a_project_with_an_original_is_not_refused():
    assert agent_tools._no_original_refusal(_Ctx({"storage_key": "originals/1/a.mp4"}),
                                            "the cursor pass") is None
