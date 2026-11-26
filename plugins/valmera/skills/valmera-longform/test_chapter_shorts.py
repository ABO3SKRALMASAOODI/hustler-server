import json

import chapter_shorts as shorts


def test_chapter_selection_preserves_exact_words_clocks_and_citations():
    script = dict(chapters=[dict(id=f"c{i}", title=f"Chapter {i}") for i in range(6)],
                  segments=[dict(chapter=f"c{i//2}", say=f"Sentence {i}.", facts=[f"f{i}"],
                                 beat="turn" if i % 2 else "hook") for i in range(12)])
    words = [dict(seg=i, t0=i * 2 + .1, t1=i * 2 + 1.8, w=s["say"])
             for i, s in enumerate(script["segments"])]
    def ask(*args, **kwargs):
        return json.dumps(dict(moments=[dict(chapter=f"c{i}", first=2*i, last=2*i+1,
                                             hook=f"Hook {i}") for i in range(6)])), {}
    picks, _ = shorts.moments(script, words, ask)
    assert len(picks) == 6
    assert picks[2]["facts"] == ["f4", "f5"]
    assert picks[2]["start"] == 8.1 and picks[2]["end"] == 11.8
    ass = shorts.subtitles(picks[2])
    assert "Hook 2" in ass and "Sentence 4." in ass and "Sentence 3." not in ass
    assert "0:00:02.00,0:00:03.70,Caption" in ass
