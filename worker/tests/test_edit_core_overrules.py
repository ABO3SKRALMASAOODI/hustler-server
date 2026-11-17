"""EDIT-CORE, 16 Nov 2026: the editing model's work stands.

Owner: "Rejections will only constrain the agent and drive it crazy until it
stops. The product should be reliable, meaning the product should get the
outcome ... smart tool with smart model making smart decisions, not
refusals." Each test drives a path that used to refuse, hold, overrule or
roll back the editor's edit, and proves the edit now saves (with what the
path knew passed back as information), or that a fixable problem is
repaired and reported.
"""
import inspect
from types import SimpleNamespace as NS

import pytest

from w0c_support import tool_context

import agent_loop
import agent_tools
import followup_recipes
import music_gate
import request_contract
from schemas import default_edl


def _ctx(duration=20.0, words=(), user_message=""):
    ctx, fake = tool_context(default_edl(duration), duration=duration,
                             user_message=user_message)
    ctx.index["words"] = list(words)
    ctx.cut_sound_source = lambda: None        # no source sound: words only
    return ctx, fake


# ---- word edges: repaired and reported, never refused ----------------------

WORDS = [{"w": "the", "t0": 4.00, "t1": 4.20},
         {"w": "story", "t0": 4.20, "t1": 4.90},
         {"w": "began", "t0": 5.10, "t1": 5.60},
         {"w": "there", "t0": 12.00, "t1": 12.40}]


def test_an_exact_time_cut_inside_a_word_saves_at_the_nearest_word_edge():
    ctx, fake = _ctx(words=WORDS)
    edl = default_edl(20.0)
    edl["keep"] = [[4.30, 12.10]]              # inside 'story', inside 'there'
    out = ctx.write_edl(edl, "exact trim")
    assert out.startswith("EDL v7 -> v8"), out
    assert "CUT EDGES REPAIRED (saved)" in out
    saved = fake.rows[-1]["json"]["keep"]
    assert saved == [[4.2, 12.0]]
    # each move named with its exact seconds and the word it kept or left out
    assert "start 4.300s -> 4.200s (0.100 s earlier; kept 'story' whole)" in out
    assert "end 12.100s -> 12.000s (0.100 s earlier; left 'there' out whole)" in out


def test_an_edit_batch_that_clips_a_word_also_saves_repaired():
    ctx, fake = _ctx(words=WORDS)
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 4.85], [5.15, 20.0]]   # end inside 'story', start inside 'began'
    out = agent_tools.ToolContext.write_edl(ctx, edl, "apply_edit_batch")
    assert out.startswith("EDL v7 -> v8"), out
    assert fake.rows[-1]["json"]["keep"] == [[0.0, 4.9], [5.1, 20.0]]
    assert "kept 'story' whole" in out and "kept 'began' whole" in out


def test_a_cut_inside_one_word_that_closes_names_the_word_and_its_span():
    ctx, fake = _ctx(words=WORDS)
    n = len(fake.rows)
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 4.30], [4.36, 20.0]]   # both edges inside 'story'
    out = ctx.write_edl(edl, "trim inside a word")
    assert out.startswith("NO CHANGE"), out
    assert len(fake.rows) == n
    assert "may not be supported" not in out
    assert "CUT EDGES REPAIRED" in out
    assert "inside the word 'story' (4.200-4.900s)" in out
    assert "cut 4.200-4.900s whole" in out


def test_clean_edges_get_no_note_and_an_unchanged_keep_is_not_measured(monkeypatch):
    ctx, fake = _ctx(words=WORDS)
    edl = default_edl(20.0)
    edl["keep"] = [[4.2, 12.0]]
    out = ctx.write_edl(edl, "clean trim")
    assert out.startswith("EDL v7 -> v8") and "CUT EDGES" not in out
    monkeypatch.setattr(agent_tools, "_measured_edge_moves",
                        lambda *a: pytest.fail("measured an unchanged keep"))
    edl = fake.rows[-1]["json"]
    edl = dict(edl, captions={"mode": "from_transcript"})
    assert ctx.write_edl(edl, "captions").startswith("EDL v8 -> v9")


# ---- the write saves what the brief, the ledger or a word list read --------

def test_a_cut_on_a_length_the_ledger_reads_as_kept_saves_with_a_note():
    ctx, fake = _ctx()
    ctx.has_main_video = True
    ctx.ask_ledger = {"asks": [{"id": "a1", "length": "upload",
                                "said": "keep my full narration"}]}
    edl = default_edl(20.0)
    edl["keep"] = [[0.0, 15.0]]
    out = ctx.write_edl(edl, "cut the tail")
    assert out.startswith("EDL v7 -> v8"), out
    assert "NOTE (saved)" in out and "15.0 s of the 20.0 s" in out
    assert fake.rows[-1]["json"]["keep"] == [[0.0, 15.0]]


def test_a_follow_up_recipe_never_refuses_a_write():
    """#1065: 'add a title' refused add_motion_graphic as NOT ASKED; a
    'shorter' request refused a write that grew the video."""
    ctx, fake = _ctx(user_message="cut the boring parts")
    ctx.followup = {"keys": ["boring"], "label": "cut the boring parts",
                    "allowed": {"cuts"}, "direction": "shorter",
                    "start_edl": default_edl(20.0), "start_s": 20.0}
    assert followup_recipes.active(ctx)
    edl = default_edl(20.0)
    edl["captions"] = {"mode": "from_transcript"}       # outside the recipe's lane
    out = ctx.write_edl(edl, "captions too")
    assert out.startswith("EDL v7 -> v8"), out
    assert not hasattr(followup_recipes, "refusal")


def test_a_counter_with_a_days_label_saves():
    """counter_facts read the label and the transcript with word lists and
    refused; the editor reads the transcript itself."""
    ctx, fake = _ctx(words=[{"w": w, "t0": 5 + i * .2, "t1": 5.15 + i * .2}
                            for i, w in enumerate("dia 20 até dia 29".split())])
    edl = default_edl(20.0)
    edl["motion"] = [{"id": "date", "template": "counter", "start": 5.0, "end": 10.0,
                      "params": {"value": "20–29", "label": "dias de promoção"}}]
    out = ctx.write_edl(edl, "counter")
    assert out.startswith("EDL v7 -> v8"), out


def test_what_the_brief_excludes_is_a_note_never_a_refusal():
    ctx = NS(semantic_contract={"requirements": [
        {"id": "r1", "action": "avoid", "lanes": ["music"], "quote": "no music"},
        {"id": "r2", "action": "preserve", "lanes": ["captions"], "quote": "keep my subtitles"}]})
    before = {"captions": {"mode": "from_transcript", "style": {"preset": "clean"}}}
    after = {"captions": {"mode": "from_transcript", "style": {"preset": "beast"}},
             "music": [{"storage_key": "m.mp3"}]}
    note = request_contract.write_note(ctx, before, after, {"captions"})
    assert note.startswith("NOTE (saved)")
    assert "keep my subtitles" in note and "no music" in note and "music" in note
    assert "never a" in request_contract.preflight.__doc__ and "hold" in request_contract.preflight.__doc__


# ---- nothing the editor saved is rolled back or overruled after the turn ---

class _Settle(NS):
    def latest_edl(self):
        return {"version": 9, "json": self.edl}


def test_an_unmet_ask_keeps_the_editors_edit(monkeypatch):
    import edit_scope
    from score import asks
    monkeypatch.setattr(edit_scope, "disclose", lambda ctx, text: text)
    monkeypatch.setattr(asks, "true_reply", lambda ctx, ledger, verdict, text: text)
    db = NS(run=lambda *a, **k: pytest.fail("rolled back"))
    edl = {"keep": [[0.0, 10.0]], "texts": [{"id": "t1", "text": "NEW"}]}
    ctx = _Settle(edl=edl, db=db, turn_start_edl={"version": 8, "json": {"keep": [[0.0, 10.0]]}},
                  ask_ledger={"asks": [{"id": "a1", "kind": "edit", "want": "w"}]},
                  ask_verdict={"edl_version": 9, "asks": [{"id": "a1", "state": "not_done", "line": "Not yet."}],
                               "unasked": [{"fix": "restore", "evidence": "e", "what": "x", "at_s": 1.0}]},
                  last_preview={}, ask_check_required=True, versions_written=[9])
    final, outcome = agent_loop._asks_settle(ctx, "I added the title.", "fulfilled")
    assert ctx.latest_edl()["json"] is edl and ctx.versions_written == [9]
    assert outcome == "partial" and "Not yet." in final


def test_a_missing_ledger_keeps_the_edit_and_says_what_the_film_keeps(monkeypatch):
    """The check could not run: the editor's reply goes out as written (no
    hedge), the request is recorded partial, and the edit is never rolled back."""
    import edit_scope
    monkeypatch.setattr(edit_scope, "disclose", lambda ctx, text: text + " [kept: keep]")
    db = NS(run=lambda *a, **k: pytest.fail("rolled back"))
    ctx = _Settle(edl={"keep": [[0.0, 5.0]]}, db=db, ask_ledger=None,
                  ask_check_required=True, versions_written=[9])
    final, outcome = agent_loop._asks_settle(ctx, "I cut it to five seconds.", "fulfilled")
    assert final == "I cut it to five seconds. [kept: keep]"
    assert outcome == "partial" and ctx.versions_written == [9]
    assert ctx.latest_edl()["json"] == {"keep": [[0.0, 5.0]]}


def test_the_turn_has_no_rollback_hold_or_pre_dispatch_refusal_left():
    removed = ("_kept_craft_refusal", "_trial_render_refusal", "_broll_throughout_hold",
               "_unmet_ask_hold", "_look_only_cut_refusal", "_typed_hold_length",
               "_trial_preview_cap", "_count_trial_preview")
    for name in removed:
        assert not hasattr(agent_loop, name), name
    loop = inspect.getsource(agent_loop._run_loop)
    for text in removed + ("followup_recipes.refusal", "restore_unmet", "undo_harm",
                           "music_offer", "turn_end_offer"):
        assert text not in loop, text
    assert not hasattr(agent_tools, "finishing_checkpoint")
    assert not hasattr(agent_tools, "_length_held_refusal")
    assert "look_guard.after_write(ctx, look_before)" in inspect.getsource(agent_tools._execute)


def test_the_brief_note_rides_on_the_render_result_never_holds_it():
    loop = inspect.getsource(agent_loop._run_loop)
    dispatch = loop[loop.index("for tc in msg.tool_calls:"):]
    assert "result = result + \"\\n\" + _brief_note" in dispatch
    note = None
    ctx = NS(request_contract_required=True, semantic_contract=None)
    try:
        note = request_contract.preflight(ctx)
    except Exception:  # noqa: BLE001 - no brief can be compiled here
        note = None
    assert note is None or note.startswith("NOTE:")


# ---- music: Valmera may choose (owner, 13 Nov 2026) ------------------------

def test_valmeras_own_choice_is_stamped_never_refused():
    prov, err = music_gate.decide(
        "music/7/library-x.mp3", {"kind": "music", "meta": {"library_slug": "x"}},
        is_picked=lambda s: False, seen_before=lambda k: False)
    assert (prov, err) == ("valmera_chosen", None)
    record, err = music_gate.resolve_pick(NS(user_message="", project_id=7, db=None), "x")
    assert err is None and music_gate.pick_provenance(record) == "valmera_chosen"


def test_no_agent_facing_text_sends_the_editor_to_ask_about_music():
    import agent_prompt
    import claude_trial
    import music_library
    from tools_ext import rhythm_tools
    surfaces = [agent_tools.TOOLS["ask_user"][1], agent_tools.TOOLS["add_music"][1],
                agent_tools.TOOLS["apply_look"][1],
                agent_tools.TOOLS["apply_look"][2]["music"]["description"],
                music_library.TOOL_SPECS["add_library_music"][1],
                music_library.TOOL_SPECS["list_music_library"][1],
                rhythm_tools._no_track(), claude_trial.FIRST_RESULT_NOTE,
                agent_prompt.project_state_block("v", "i", "e", [], [])]
    for name in agent_prompt.skill_names():
        surfaces.append(agent_prompt.read_skill_text(name) or "")
    from tools_ext import montage_tools
    surfaces.append(montage_tools.TOOL_SPECS["shape_music"][1])
    surfaces.append(_mcp_instructions_text())
    for text in surfaces:
        low = " ".join(text.lower().split())
        for phrase in ("never choose music", "never pick for them", "agents never choose",
                       "offer 2-4", "never choose a track", "music is never chosen",
                       "never yours", "never choose one yourself", "the user makes the choice",
                       "the agent never chooses", "user tracks only",
                       "never music the user did not", "user picks the track"):
            assert phrase not in low, (phrase, text[:120])


def _mcp_instructions_text():
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(os.path.dirname(here), "backend", "routes", "mcp.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("g. MUSIC:")
    return src[start:start + 900]
