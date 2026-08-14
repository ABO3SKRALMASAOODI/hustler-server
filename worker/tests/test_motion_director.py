"""Emphasis motion is a coherent rhythm, not the loudest adjacent words."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402


class _Ctx:
    has_main_video = True
    user_message = "make this a premium founder reel"

    def __init__(self, words, plan=None):
        self.index = {"words": words}
        self.edit_plan = plan or {}
        self.written = None

    @staticmethod
    def latest_edl():
        return {"json": {"keep": [[0, 30]], "effects": {}}}

    def write_edl(self, edl, _description):
        self.written = edl
        return "EDL v1 -> v2: motion pass"


def _word(text, at):
    return {"w": text, "t0": at, "t1": at + .25}


def test_auto_motion_distributes_stress_and_varies_magnitude(monkeypatch):
    words = [_word(text, at) for text, at in (
        ("growth", 2.0), ("business", 2.2), ("revenue", 2.4),
        ("customers", 8.0), ("profit", 15.0), ("future", 22.0),
        ("result", 28.0))]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(
        agent_tools.perception, "word_stress",
        lambda _p, _w: [1.0, .99, .98, .80, .72, .64, .55])
    monkeypatch.setattr(
        agent_tools, "_face_at_source_moments",
        lambda _ctx, _edl, moments: {moment: (.5, .4) for moment in moments})
    ctx = _Ctx(words, {"motion_direction":
                       "restrained premium pushes on argument turns"})

    out = agent_tools.punch_in_on_emphasis(ctx)
    zooms = ctx.written["effects"]["zooms"]

    assert out.startswith("EDL v1 -> v2")
    assert len(zooms) == 2  # calm brief lowers density for this 30s program
    assert all(b["start"] - a["start"] >= 5.8
               for a, b in zip(zooms, zooms[1:]))
    assert max(z["strength"] for z in zooms) <= .10
    assert "instead of clustering" in out


def test_explicit_motion_values_remain_authoritative(monkeypatch):
    words = [_word(f"word{i}", at)
             for i, at in enumerate((2.0, 8.0, 15.0, 22.0))]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(agent_tools.perception, "word_stress",
                        lambda _p, _w: [1, .9, .8, .7])
    monkeypatch.setattr(agent_tools, "_face_at_source_moments",
                        lambda *_args: {})
    ctx = _Ctx(words)

    agent_tools.punch_in_on_emphasis(ctx, count=4, strength=.23)
    zooms = ctx.written["effects"]["zooms"]
    assert len(zooms) == 4
    assert {z["strength"] for z in zooms} == {.23}


def test_sequence_energy_guides_emphasis_without_forcing_a_motion_quota(
        monkeypatch):
    words = [_word("opening", 2.0), _word("proof", 15.0)]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(agent_tools.perception, "word_stress",
                        lambda _p, _w: [.6, .6])
    monkeypatch.setattr(agent_tools, "_face_at_source_moments",
                        lambda *_args: {})
    ctx = _Ctx(words, {
        "steps": ["shape emphasis"],
        "motion_direction": "restrained emphasis only on the strongest turn",
        "sequence_map": [
            {"role": "setup", "anchor": "opening",
             "purpose": "establish context", "visual": "stay settled",
             "energy": .1, "source_start_s": 1.5, "source_end_s": 3.0},
            {"role": "proof", "anchor": "proof",
             "purpose": "land the evidence", "visual": "earned push-in",
             "energy": .9, "source_start_s": 14.5, "source_end_s": 16.0},
        ],
    })

    agent_tools.punch_in_on_emphasis(ctx, count=1)
    zoom = ctx.written["effects"]["zooms"][0]
    assert 14.8 <= zoom["start"] <= 15.1


def _language(density, intensity, contrast=1.0):
    return {
        "principle": "one earned convergence at evidence peaks",
        "density": density, "intensity": intensity, "contrast": contrast,
        "stillness_rule": "hold whenever the speaker is establishing context",
        "motifs": [{"id": "proof_push",
                    "behavior": "ease toward the speaker and proof word",
                    "trigger": "a concrete result lands",
                    "domains": ["camera", "type"]}],
    }


def test_structured_language_directs_density_magnitude_and_contrast(
        monkeypatch):
    words = [_word(f"proof{i}", at) for i, at in enumerate(
        (2.0, 6.0, 10.0, 14.0, 18.0, 22.0, 27.0))]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(agent_tools.perception, "word_stress",
                        lambda _p, _w: [1, .92, .84, .76, .68, .60, .52])
    monkeypatch.setattr(
        agent_tools, "_face_at_source_moments",
        lambda _ctx, _edl, moments: {moment: (.5, .4) for moment in moments})

    sparse = _Ctx(words, {"motion_language": _language(0, 0)})
    agent_tools.punch_in_on_emphasis(sparse)
    sparse_zooms = sparse.written["effects"]["zooms"]
    assert len(sparse_zooms) == 2
    assert max(row["strength"] for row in sparse_zooms) == .07

    expressive = _Ctx(words, {"motion_language": _language(1, 1)})
    result = agent_tools.punch_in_on_emphasis(expressive)
    expressive_zooms = expressive.written["effects"]["zooms"]
    assert len(expressive_zooms) == 4
    assert max(row["strength"] for row in expressive_zooms) == .19
    assert min(row["strength"] for row in expressive_zooms) < .19
    assert "no style adjectives were used" in result


def test_hold_binding_is_authoritative_even_on_a_louder_word(monkeypatch):
    words = [_word("loudsetup", 2.0), _word("proof", 15.0)]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(agent_tools.perception, "word_stress",
                        lambda _p, _w: [1.0, .55])
    monkeypatch.setattr(agent_tools, "_face_at_source_moments",
                        lambda *_args: {})
    ctx = _Ctx(words, {
        "motion_language": _language(.5, .5),
        "sequence_map": [
            {"source_start_s": 1.5, "source_end_s": 3.0,
             "energy": .9, "motion_motif": "hold"},
            {"source_start_s": 14.5, "source_end_s": 16.0,
             "energy": .6, "motion_motif": "proof_push"},
        ],
    })
    result = agent_tools.punch_in_on_emphasis(ctx, count=1)
    zoom = ctx.written["effects"]["zooms"][0]
    assert 14.8 <= zoom["start"] <= 15.1
    assert zoom["motion_motif"] == "proof_push"
    assert "motif=proof_push, beat=2" in result
