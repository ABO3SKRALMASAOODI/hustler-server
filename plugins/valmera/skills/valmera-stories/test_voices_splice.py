"""Real voices from every footage record are spliced once, after all the footage is in (stories round 1, 19 Nov: two Jobs
voices a top-up cut were "cut but not placed", the film played the narrator alone)."""
import json
import os
import subprocess
from types import SimpleNamespace


def _record(root, name, voices):
    d = root / name
    (d / "voices").mkdir(parents=True)
    for v in voices:
        (d / v["wav"]).write_bytes(b"RIFF")
    (d / "footage.json").write_text(json.dumps(dict(voices=voices)))


def _v(after, said, wav, url="u", t=(1.0, 4.0), seconds=3.0):
    return dict(after=after, person="Steve Jobs", said=said, wav=f"voices/{wav}", file=None, seconds=seconds,
                source=dict(url=url, title="CNBC 1997", channel="CNBC"), time_range=list(t), words=[])


SC = dict(segments=[dict(say="a"), dict(say="b"), dict(say="c"), dict(say="d"), dict(say="e"),
                    dict(say="We have to let go of this notion that for Apple to win Microsoft has to lose."),
                    dict(say="f"), dict(say="g")])


def test_voice_choice_takes_every_record_one_voice_a_line_the_persons_own_sentence_first(tmp_path):
    import wf3 as W3
    _record(tmp_path, "footage", [_v(5, "I think it is a great day", "a.wav", t=(10, 13))])
    _record(tmp_path, "footage2", [_v(5, "we have to let go of this notion that for Apple to win Microsoft has to lose", "b.wav", t=(441, 449)),
                                   _v(6, "we are going to be fine", "c.wav", t=(434, 441))])
    _record(tmp_path, "footage10", [_v(2, "something else", "d.wav", t=(1, 2))])
    ctx = SimpleNamespace(supply=str(tmp_path))
    assert W3.footage_records(ctx) == ["footage", "footage2", "footage10"]
    got = W3.voice_choice(ctx, SC, 80.0)
    assert [(c["record"], c["voice"]["after"]) for c in got][:1] == [("footage2", 5)]       # the line's own sentence
    assert len(got) == W3.VOICES_MOST and len({c["voice"]["after"] for c in got}) == len(got)
    assert W3.voice_choice(ctx, SC, 12.0) == got[:1]          # the share of the narration: the first always, then the cap


def test_splice_voices_places_top_up_voices_and_credits_only_the_placed(tmp_path, monkeypatch):
    import wf3 as W3
    _record(tmp_path, "footage", [])
    _record(tmp_path, "footage6", [_v(5, "x", "b.wav", t=(441.24, 449.56)), _v(10, "y", "c.wav", t=(434.48, 441.4))])
    narr = tmp_path / "narration"
    narr.mkdir()
    (narr / "words.json").write_text(json.dumps(dict(duration=80.0, words=[dict(w="a", start=0.0, end=0.5, seg=0)])))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "splice" in cmd:
            rec = json.load(open(os.path.join(cmd[-1], "footage.json")))
            assert all(os.path.exists(os.path.join(cmd[-1], v["wav"])) for v in rec["voices"])
            placed = [dict(after=5, person="Steve Jobs", start=30.0, end=38.3, said="x", file=None, wav="w",
                           source=dict(url="u", title="CNBC 1997", channel="CNBC"), time_range=[441.24, 449.56])]
            (narr / "words.json").write_text(json.dumps(dict(duration=88.3, words=[dict(w="a", start=0.0, end=0.5, seg=0)])))
            return subprocess.CompletedProcess(cmd, 0, json.dumps(dict(voices=placed)), "")
        return subprocess.CompletedProcess(cmd, 0, "levelled", "")
    monkeypatch.setattr(subprocess, "run", run)
    pack = dict(credits=[dict(page="u", time_range=[441.24, 449.56], voice="Steve Jobs"), dict(page="u", time_range=[434.48, 441.4], voice="Steve Jobs"),
                         dict(page="v", time_range=[0, 1])])
    ctx = SimpleNamespace(supply=str(tmp_path), log=lambda m: None)
    words, dur = W3.splice_voices(ctx, SC, pack, str(narr), {})
    assert dur == 88.3 and pack["voices"][0]["after"] == 5
    assert [c.get("time_range") for c in pack["credits"] if c.get("voice")] == [[441.24, 449.56]]
    assert any("splice" in c for c in calls) and any("level-voices" in c for c in calls)
    rec = W3.voices_record(ctx, pack)
    assert rec["placed"] == 1 and rec["found"] == 2
    assert [r for r in rec["rejected"] if "not placed" in r] == ["Steve Jobs after 10: cut but not placed in the narration"]


def test_the_stories_top_ups_hear_voices_with_the_engines_repair_and_marketing_is_unchanged(tmp_path, monkeypatch):
    import wf3 as W3
    seen = []

    def footage(ctx, script, durs, **kw):
        seen.append((os.environ.get("FOOTAGE_VOICE_REPAIR"), kw.get("want_voices")))
        return None
    monkeypatch.setattr(W3.chain, "footage", footage)
    monkeypatch.delenv("FOOTAGE_VOICE_REPAIR", raising=False)
    monkeypatch.setattr(W3, "footage_kw", lambda reel_kw, **more: dict(more))
    ctx = SimpleNamespace(supply=str(tmp_path))
    script = dict(script=dict(segments=[dict(say="a", see=[])], named=[]))
    assert W3.topup_once(ctx, script, {}, [1.0], [0], [], "footage2", {}, dict(x=1), want_voices=True) == 0
    assert W3.topup_once(ctx, script, {}, [1.0], [0], [], "footage3", {}, dict(x=1)) == 0
    assert seen == [("1", True), (None, None)] and "FOOTAGE_VOICE_REPAIR" not in os.environ
