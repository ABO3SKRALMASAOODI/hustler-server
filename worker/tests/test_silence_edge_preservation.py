"""Silence cleanup preserves speech at the edit's established cut edges."""
from copy import deepcopy

import agent_tools
import audit


def test_cleanup_keeps_retained_word_despite_overlapping_alignment():
    class Context:
        duration = 6.0
        index = {
            "video": {"duration": 6.0, "fps": 25},
            "silences": [[0.8, 3.5], [3.8, 6.0]],
            "words": [
                {"w": "income", "t0": 0.0, "t1": 0.8},
                {"w": "income", "t0": 0.505, "t1": 0.745},
                {"w": "continues", "t0": 3.5, "t1": 3.8},
            ],
        }
        edl = {"keep": [[0.51, 2.0], [3.0, 6.0]]}

        def latest_edl(self):
            return {"version": 1, "json": deepcopy(self.edl)}

        def write_edl(self, edl, description):
            self.edl = deepcopy(edl)
            return "EDL v2"

    ctx = Context()
    assert agent_tools.cut_silences(ctx, min_silence_s=0.8,
                                    padding_s=0.15).startswith("EDL v")
    assert ctx.edl["keep"][0][0] == 0.51
    assert ctx.edl["keep"][0][1] >= 0.745
    # A real pause is removed, without bringing an earlier deleted region back.
    assert not any(a < 2.5 < b for a, b in ctx.edl["keep"])
    once = deepcopy(ctx.edl["keep"])
    agent_tools.cut_silences(ctx, min_silence_s=0.8, padding_s=0.15)
    assert ctx.edl["keep"] == once


def test_old_edges_stay_fixed_but_explicit_replacement_can_resnap():
    words = [{"w": "that", "t0": 10.565, "t1": 11.765},
             {"w": "it", "t0": 11.51, "t1": 11.83}]
    previous = [[8.0, 11.03], [11.51, 15.0]]
    assert audit.snap_keep_to_words(previous, words, 20,
                                    prev_keep=previous) == previous
    assert audit.snap_keep_to_words(previous, words, 20) != previous
