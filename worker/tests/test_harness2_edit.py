"""Harness v2: envelope (B3), commit (C7), layer ops (C8), keep ops (C9),
fit_length (C10) and undo (C13), on a real ToolContext over a fake db."""
import json
import math
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import agent_tools  # noqa: E402
from harness2 import commit, envelope, fit_length, ops as ops_mod  # noqa: E402
from w0c_support import tool_context  # noqa: E402


@pytest.fixture(autouse=True)
def _no_v1_writes(monkeypatch):
    """C7: v2 never calls ToolContext.write_edl or its guard chain."""
    def boom(*_a, **_k):
        raise AssertionError("v1 write_edl was called")
    monkeypatch.setattr(agent_tools.ToolContext, "write_edl", boom)
    commit._RECEIPTS.clear()
    yield
    commit._RECEIPTS.clear()


def _ctx(duration=60.0, keep=None, words=None):
    ctx, fake = tool_context({"keep": keep or [[0.0, duration]]},
                             duration=duration)
    ctx.index["words"] = words or []
    ctx.cut_sound_source = lambda: None
    return ctx, fake


# -- B3 envelope ----------------------------------------------------------

def test_envelope_statuses_and_text():
    env = envelope.Envelope(tool="edit", changed="v1 -> v2: x", applied=1)
    assert env.status == envelope.DONE
    env.repair("3 spans merged")
    assert env.status == envelope.DONE_WITH_REPAIRS
    env.skip(2, "no media with id x", "ids 3-5")
    assert env.status == envelope.PARTIAL
    text = env.text()
    assert "status: partial" in text and "op 2: no media with id x" in text
    assert "instead: ids 3-5" in text and "3 spans merged" in text
    env.running = {"handle": "job:4", "progress": "7/12 pieces"}
    assert env.status == envelope.WORKING
    nothing = envelope.Envelope(tool="edit")
    nothing.skip(0, "media exists nowhere", "a find() id")
    assert nothing.status == envelope.FAILED
    hard = envelope.failed("render", "the executor is down", "wait")
    assert hard.status == envelope.FAILED and "wait" in hard.text()
    assert set(hard.to_dict()) >= {"status", "changed", "measured", "repairs",
                                   "not_applied", "running", "findings",
                                   "see", "check", "undo_to"}


def test_envelope_carries_no_instruction_text():
    ctx, _fake = _ctx(duration=600.0)
    env = commit.edit(ctx, [{"keep": [[0, 100]]}], "big cut")
    text = env.text()
    assert "If the user did not" not in text and "Do NOT" not in text


# -- C7 commit ------------------------------------------------------------

def test_an_invalid_op_is_set_aside_and_the_rest_lands():
    ctx, fake = _ctx()
    env = commit.edit(ctx, [{"keep": [[0, 10], [20, 30]]},
                            {"warp": {"to": "moon"}},
                            {"tool": "no_such_verb", "args": {}},
                            {"text": {"text": "Hello", "start": 1,
                                      "end": 3}}], "mixed")
    assert env.status == envelope.PARTIAL
    assert {row["op"] for row in env.not_applied} == {1, 2}
    assert fake.inserts == [8]                 # ONE version for the call
    edl = fake.rows[-1]["json"]
    assert [list(map(float, k)) for k in edl["keep"]] == [[0, 10], [20, 30]]
    assert len(edl["texts"]) == 1
    assert env.undo_to == 7 and env.version == 8
    assert env.changed.startswith("v7 -> v8:")
    assert env.measured["program_s"] == pytest.approx(20.0, abs=0.1)


def test_a_repeated_operation_id_returns_the_first_result():
    ctx, fake = _ctx()
    first = commit.edit(ctx, [{"cut": [[5, 9]]}], "cut", operation_id="op-1")
    again = commit.edit(ctx, [{"cut": [[5, 9]]}], "cut", operation_id="op-1")
    assert fake.inserts == [8]
    assert again.version == first.version == 8
    assert again.changed == first.changed


def test_an_edit_that_changes_nothing_is_a_fact_not_an_error():
    ctx, fake = _ctx()
    env = commit.edit(ctx, [{"restore": [[0, 60]]}], "noop")
    assert env.status == envelope.DONE
    assert env.changed.startswith("already so: v7 unchanged")
    assert fake.inserts == []


def test_nothing_possible_is_failed_and_names_the_alternative():
    ctx, fake = _ctx()
    env = commit.edit(ctx, [{"keep": [[100, 200]]}], "out of range")
    assert env.status == envelope.FAILED and fake.inserts == []
    assert env.not_applied[0]["instead"]


# -- C8 layer ops ---------------------------------------------------------

def test_a_300_op_layer_batch_lands_in_one_version():
    ctx, fake = _ctx()
    ops = [{"upsert": "texts", "id": f"t{i}",
            "value": {"text": f"n{i}", "start": round(i * 0.19, 2),
                      "end": round(i * 0.19 + 0.5, 2)}} for i in range(300)]
    env = commit.edit(ctx, ops, "300 texts")
    assert not env.not_applied, env.not_applied[:2]
    assert fake.inserts == [8]
    assert len(fake.rows[-1]["json"]["texts"]) == 300


def test_one_bad_layer_op_in_a_chunk_is_set_aside_alone():
    ctx, fake = _ctx()
    ops = [{"upsert": "texts", "id": "a",
            "value": {"text": "A", "start": 1, "end": 2}},
           {"upsert": "texts", "value": {"text": "no id"}},
           {"upsert": "texts", "id": "b",
            "value": {"text": "B", "start": 3, "end": 4}}]
    env = commit.edit(ctx, ops, "texts")
    assert [r["op"] for r in env.not_applied] == [1]
    assert {t["id"] for t in fake.rows[-1]["json"]["texts"]} == {"a", "b"}


def test_a_stale_base_is_rebased_on_the_head_with_a_note():
    ctx, fake = _ctx()
    commit.edit(ctx, [{"cut": [[50, 60]]}], "someone else")      # v8
    env = commit.edit(ctx, [{"text": {"text": "Hi", "start": 1, "end": 2}}],
                      "late writer", base_version=7)
    assert env.version == 9
    assert any("base v7 is behind the head v8" in r for r in env.repairs)
    edl = fake.rows[-1]["json"]
    assert edl["texts"] and float(edl["keep"][-1][1]) <= 50.01


# -- C9 keep, cut, restore, speed ------------------------------------------

def test_refused_keep_inputs_land_with_repair_lines():
    ctx, fake = _ctx(duration=60.0)
    env = commit.edit(ctx, [{"keep": [[30, 40], [10, 5], [35, 45],
                                      [50, 75], [20, 20.01], "junk"]}],
                      "messy keep")
    assert env.version == 8 and not env.not_applied
    keep = [list(map(float, k)) for k in fake.rows[-1]["json"]["keep"]]
    assert keep == [[5, 10], [30, 45], [50, 60]]
    text = " | ".join(env.repairs)
    for needle in ("turned round", "held to the source", "dropped",
                   "merged", "not [start, end] pairs"):
        assert needle in text, (needle, text)


def test_edges_move_out_of_words():
    words = [{"w": "hello", "t0": 9.6, "t1": 10.4},
             {"w": "world.", "t0": 10.5, "t1": 11.2}]
    ctx, fake = _ctx(duration=60.0, words=words)
    env = commit.edit(ctx, [{"keep": [[0, 10.0], [20, 30]]}], "mid-word")
    keep = fake.rows[-1]["json"]["keep"]
    assert float(keep[0][1]) >= 10.4 - 1e-6
    assert env.version == 8


def test_cut_restore_and_speed():
    ctx, fake = _ctx(duration=60.0)
    commit.edit(ctx, [{"cut": [[10, 20]]}], "cut")
    commit.edit(ctx, [{"restore": [[12, 15]]}], "restore")
    keep = [list(map(float, k)) for k in fake.rows[-1]["json"]["keep"]]
    assert keep == [[0, 10], [12, 15], [20, 60]]
    env = commit.edit(ctx, [{"speed": [{"start": 30, "end": 40,
                                        "factor": 2.0}]}], "speed")
    assert env.version == 10 and fake.rows[-1]["json"]["speed"]
    everything = commit.edit(ctx, [{"cut": [[0, 60]]}], "all")
    assert everything.status == envelope.FAILED


def test_p5857_plans_of_3500_and_3510_seconds_land():
    """The model's own plans (jobs 138842 / 138843) were refused by v1's
    guards; through v2 they land as one version each."""
    duration = 4858.2
    rng = random.Random(5857)
    for target in (3500.8, 3510.3):
        ctx, fake = _ctx(duration=duration)
        spans, t = [], 0.0
        while t < duration - 60:
            length = rng.uniform(15, 70)
            gap = rng.uniform(5, 30)
            spans.append([round(t, 2), round(min(duration, t + length), 2)])
            t += length + gap
        total = sum(b - a for a, b in spans)
        scale = target / total
        spans = [[a, a + (b - a) * scale] for a, b in spans]
        env = commit.edit(ctx, [{"keep": spans}], f"{target} s plan")
        assert env.version == 8, env.text()
        assert env.measured["program_s"] == pytest.approx(target, abs=2.0)


# -- C10 fit_length -------------------------------------------------------

def _p5857_like_track(duration, fps=4.0, seed=60):
    rng = random.Random(seed)
    n = int(duration * fps)
    motion = []
    level = 0.2
    for i in range(n):
        if i % int(fps * 30) == 0:
            level = rng.choice([0.05, 0.1, 0.4, 0.7])
        motion.append(max(0.0, level + rng.uniform(-0.05, 0.05)))
    return {"version": 1, "fps": fps, "motion": motion, "audio": None}


def _words_every(duration, every=40.0):
    words, t = [], 7.0
    while t < duration - 5:
        for k, w in enumerate(("we", "are", "here", "now.")):
            words.append({"w": w, "t0": round(t + k * 0.4, 2),
                          "t1": round(t + k * 0.4 + 0.3, 2)})
        t += every
    return words


def test_fit_length_lands_p5857_in_the_band(monkeypatch):
    duration = 4858.2
    track = _p5857_like_track(duration)
    words = _words_every(duration)
    monkeypatch.setattr(ops_mod.Engines, "activity_track",
                        staticmethod(lambda _ctx: (track, None)))
    ctx, fake = _ctx(duration=duration, words=words)
    import activity
    seg = activity.segment(track, min_active_s=2.0)
    keep = [[max(0.0, a - 1.5), min(duration, b + 1.5)]
            for a, b, *_ in seg["active"]]
    env = commit.edit(ctx, [{"keep": keep},
                            {"fit_length": {"max_s": 3590, "min_s": 3480,
                                            "by": "activity"}}],
                      "just under an hour")
    edl = fake.rows[-1]["json"]
    from schemas import program_duration
    length = float(program_duration(edl))
    assert 3480 <= length <= 3590, (length, env.repairs)
    pieces = [list(map(float, k)) for k in edl["keep"]]
    assert all(b - a >= 2.0 - 1e-6 for a, b in pieces)
    for a, b in pieces:
        for w in words:
            assert not (w["t0"] < a < w["t1"]), (a, w)
            assert not (w["t0"] < b < w["t1"]), (b, w)
    assert any("fit_length" in r for r in env.repairs)


def test_fit_length_spends_quiet_wordless_seconds_first():
    fps = 4.0
    score = [0.1] * int(40 * fps) + [0.9] * int(40 * fps)
    keep = [[0.0, 40.0], [40.0, 80.0]]
    new, lines, m = fit_length.fit(keep, score, fps, [], 80.0, max_s=60.0,
                                   min_s=58.0)
    length = sum(b - a for a, b in new)
    assert 58.0 <= length <= 60.0
    assert sum(b - a for a, b in new if a >= 40.0) == pytest.approx(40.0)


def test_fit_length_never_cuts_inside_a_sentence():
    fps = 4.0
    score = [0.0] * int(60 * fps)
    words = [{"w": w, "t0": 1.0 + i * 0.5, "t1": 1.4 + i * 0.5}
             for i, w in enumerate("one two three four five six seven "
                                   "eight nine ten.".split())]
    new, _lines, _m = fit_length.fit([[0.0, 60.0]], score, fps, words, 60.0,
                                     max_s=50.0)
    a = new[0][0]
    assert a <= 1.0 or a >= words[-1]["t1"]


def test_fit_length_restores_the_most_active_seconds_when_short():
    fps = 4.0
    score = [0.1] * int(20 * fps) + [0.9] * int(20 * fps) + [0.1] * int(
        20 * fps)
    new, lines, m = fit_length.fit([[18.0, 22.0]], score, fps, [], 60.0,
                                   min_s=10.0, max_s=12.0)
    length = sum(b - a for a, b in new)
    assert 10.0 <= length <= 12.5
    assert new[0][0] >= 18.0 - 0.01        # grew into the active side


# -- C13 undo -------------------------------------------------------------

def test_undo_appends_a_copy_of_an_earlier_version():
    ctx, fake = _ctx()
    commit.edit(ctx, [{"cut": [[10, 20]]}], "cut")          # v8
    commit.edit(ctx, [{"cut": [[30, 40]]}], "cut")          # v9
    env = commit.undo(ctx)                                    # copy of v8
    assert env.version == 10 and fake.inserts == [8, 9, 10]
    assert fake.rows[-1]["json"]["keep"] == fake.rows[1]["json"]["keep"]
    assert "a copy of v8" in env.changed and env.undo_to == 9
    env = commit.undo(ctx, to=7)
    assert env.version == 11
    assert fake.rows[-1]["json"]["keep"] == fake.rows[0]["json"]["keep"]
    missing = commit.undo(ctx, to=99)
    assert missing.status == envelope.FAILED and "versions 1-11" in \
        missing.not_applied[0]["instead"]
