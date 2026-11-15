"""Speech edits keep whole source words even between non-frame-aligned edges."""
from types import SimpleNamespace

import numpy as np
import pytest

from score import compiler, lower, shape, sound
from scene import audio
from scene.spine import samples
import word_edges


def make_spine(fps, groups):
    # Touching words leave no padding room. Every omitted word has a distinct
    # audio value, so leaking a fragment is observable independently of timing.
    words = [{"w": f"word{i}", "t0": i * .237, "t1": (i + 1) * .237}
             for i in range(120)]
    ctx = compiler.Ctx({"video": {"duration": 28.44}, "words": words,
                        "first_film": {"frame": "9:16"}})
    ctx.fps, ctx.gap, ctx.film = fps, .25, {"tail": .3}
    ctx.beats = []
    for i, ws in enumerate(groups):
        b = compiler.Beat({"id": f"b{i}"}, i)
        b.ws = ws
        ctx.beats.append(b)
    compiler.build_spine(ctx)
    return ctx


@pytest.mark.parametrize("fps", [24, 30, 60])
def test_picture_piece_cannot_read_past_cleared_fractional_source_end(fps):
    import math
    from score import source_guard
    n = math.ceil(1.63 * fps)
    ctx = SimpleNamespace(fps=fps, spine=[
        {"f": [0, n], "src": [81.57, 83.2], "at_s": 0.0}])
    pieces = compiler.follow_spine(ctx, 0, n)
    assert pieces == [(0, n, 81.57, 83.2)]
    l1 = {"frame": [1080, 1920], "items": [
        {"id": "last", "k": "shot", "pic": "main", "f": [0, n],
         "src": list(pieces[0][2:]), "crop": [0., 0., 1., 1.]}]}
    index = {"_first_film_source_guard": True,
             "video": {"width": 1080, "height": 1920, "duration": 83.2},
             "shots": [{"start": 81., "end": 83.2}],
             "spatial": {"samples": [{"t": 82., "faces": [[.2, .2, .8, .8]]}]}}
    # The unchanged source-boundary guard accepts the actual picture pieces.
    assert source_guard.apply(l1, index, {})[0]['source'] == [81.57, 83.2]


@pytest.mark.parametrize("fps", [24, 30, 60])
def test_omitted_words_and_reorders_keep_exact_audio_clock(fps, tmp_path, monkeypatch):
    groups = [[i, i + 1] for i in range(0, 100, 3)]
    groups = groups[12:] + groups[:12]
    ctx = make_spine(fps, groups)
    spans = ctx.spine
    total = 0.
    for span, ws in zip(spans, groups):
        assert span["src"] == pytest.approx([ws[0] * .237, (ws[-1] + 1) * .237])
        assert not word_edges.introduced([], [span["src"]], ctx.words, ctx.src_dur)
        assert span["at_s"] == pytest.approx(total)
        assert abs(span["f"][0] / fps - total) <= .5 / fps
        total += span["src"][1] - span["src"][0]
    assert total <= ctx.dur_f / fps < total + 1 / fps
    l1 = {"spine": spans, "fps": fps, "dur_f": ctx.dur_f, "frame": [1080, 1920]}
    bake = SimpleNamespace(work=str(tmp_path), locals={})
    keep, inserts = lower.lower_spine(l1, bake, {})
    assert not word_edges.introduced([], keep, ctx.words, ctx.src_dur)
    for insert in inserts:
        a = insert["source_start_s"]
        assert not word_edges.introduced([], [[a, a + insert["duration_s"]]], ctx.words, ctx.src_dur)
    # The actual two audio consumers must not read the dropped word into the
    # final fractional video frame, or accumulate a frame of drift per cut.
    rate = audio.SR
    src = np.repeat(np.arange(1, 121, dtype=np.float32), round(.237 * rate))
    src = np.column_stack([src, src])
    monkeypatch.setattr(shape, "pcm", lambda _: src)
    monkeypatch.setattr(audio, "pcm", lambda _: src)
    dry = shape.dry_spine(l1, "source")
    source_path = tmp_path / "source.pcm"
    source_path.touch()
    monkeypatch.setattr(sound, "pcm", lambda *a: src)
    designed = sound.spine_audio(spans, fps, str(source_path), len(dry), rate=rate, ch=2)
    np.testing.assert_array_equal(designed, dry)
    word_times = sound.words_in_programme(l1, {"words": ctx.words})
    assert min(t[0] for t in word_times) == pytest.approx(0)
    assert max(t[1] for t in word_times) == pytest.approx(total)
    plan = SimpleNamespace(dur=ctx.dur_f, fps=fps, doc=l1, assets={"main": {"abs": "source"}})
    voice, _, _ = audio.split_audio(plan)
    for span, ws in zip(spans, groups):
        at, _, count = samples(span, fps, rate, len(dry))
        for offset, w in enumerate(ws):
            mid = at + round((offset + .5) * .237 * rate)
            assert dry[mid, 0] == w + 1
            assert voice[mid, 0] == w + 1
        assert set(dry[at:at + count, 0]).issubset({w + 1 for w in ws})


def test_overlapping_asr_words_are_kept_whole():
    ctx = make_spine(30, [[2], [7]])
    ctx.words[1]["t1"] = ctx.words[2]["t0"] + .03
    ctx.spine, ctx.spine_talk = [], []
    compiler.build_spine(ctx)
    assert not word_edges.introduced([], [s["src"] for s in ctx.spine], ctx.words, ctx.src_dur)


def test_picture_authored_in_seconds_still_keeps_background_words_whole():
    ctx = make_spine(30, [[2], [7]])
    beat = compiler.Beat({"id": "picture"}, 0)
    beat.src = [.5, 1.64]
    ctx.beats, ctx.spine, ctx.spine_talk = [beat], [], []
    compiler.build_spine(ctx)
    assert ctx.spine[0]["src"] == pytest.approx([.474, 1.659])
    assert not word_edges.introduced([], [s["src"] for s in ctx.spine], ctx.words, ctx.src_dur)
