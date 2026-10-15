"""Render reliability and efficiency (Oct 2026, rel).

1. A graph whose output clock stands still is killed after
   FFMPEG_PROGRESS_STALL_S, even while ffmpeg keeps printing progress
   blocks: Oct 15 preview 69514 sat at 0.0/3.0 s for its whole 3000 s
   wall clock on an interactive shard.
2. Image inserts are downscaled once, before the graph, to the pixels the
   graph can use (the cover size of the output frame). The looped input
   re-decodes the picture for every output frame, so a 12 MP photo cost
   ~30 full JPEG decodes per second of output; measured on 10 photos the
   final went from 244 s to 60 s and its peak memory from 1.9 to 1.3 GB.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
import media  # noqa: E402
import renderer  # noqa: E402


def test_a_standing_output_clock_is_a_hang(monkeypatch):
    monkeypatch.setattr(config, "FFMPEG_PROGRESS_STALL_S", 2)
    monkeypatch.setattr(config, "FFMPEG_STALL_TIMEOUT_S", 300)
    script = ("import time\n"
              "for _ in range(400):\n"
              "    print('out_time_ms=0', flush=True)\n"
              "    print('progress=continue', flush=True)\n"
              "    time.sleep(0.05)\n")
    import time
    started = time.monotonic()
    with pytest.raises(media.MediaError) as caught:
        media.run([sys.executable, "-c", script],
                  progress_cb=lambda _f: None, expected_out_s=3.0)
    assert "output clock stalled" in str(caught.value)
    assert "no progress" in str(caught.value)      # classified stalled_io
    assert time.monotonic() - started < 15


def test_a_slow_but_advancing_encode_is_left_alone(monkeypatch):
    monkeypatch.setattr(config, "FFMPEG_PROGRESS_STALL_S", 1)
    script = ("import time\n"
              "for i in range(30):\n"
              "    print(f'out_time_ms={i * 100000}', flush=True)\n"
              "    time.sleep(0.1)\n"
              "print('progress=end', flush=True)\n")
    seen = []
    media.run([sys.executable, "-c", script], progress_cb=seen.append,
              expected_out_s=3.0)
    assert seen and max(seen) > 0.9


def test_a_final_may_hold_its_output_clock_through_a_source_gap(monkeypatch):
    """Review fix: a final from a long original decodes through the gap
    between distant keeps with its output clock standing (no seek when the
    earliest keep is in the first seconds, or more clusters than bounded
    reads). That is slow, not hung: finals keep the wall-clock, silence and
    overrun watchdogs, but not the output-clock one."""
    monkeypatch.setattr(config, "FFMPEG_PROGRESS_STALL_S", 1)
    monkeypatch.setattr(config, "FFMPEG_STALL_TIMEOUT_S", 300)
    script = ("import time\n"
              "for _ in range(50):\n"
              "    print('out_time_ms=0', flush=True)\n"
              "    time.sleep(0.1)\n"
              "print('out_time_ms=3000000', flush=True)\n"
              "print('progress=end', flush=True)\n")
    seen = []
    token = renderer._RENDER_JOB_TYPE.set("final")
    try:
        renderer._render_media_run([sys.executable, "-c", script],
                                   progress_cb=seen.append,
                                   expected_out_s=3.0)
    finally:
        renderer._RENDER_JOB_TYPE.reset(token)
    assert seen and max(seen) > 0.9
    # The same encode on a preview is still a hang.
    token = renderer._RENDER_JOB_TYPE.set("preview")
    try:
        with pytest.raises(media.MediaError) as caught:
            renderer._render_media_run([sys.executable, "-c", script],
                                       progress_cb=lambda _f: None,
                                       expected_out_s=3.0)
    finally:
        renderer._RENDER_JOB_TYPE.reset(token)
    assert "output clock stalled" in str(caught.value)


def _photo(path, size, mode="RGB"):
    from PIL import Image
    Image.new(mode, size, (200, 120, 40) if mode == "RGB" else
              (200, 120, 40, 128)).save(path)
    return path


def test_large_still_is_downscaled_to_the_cover_size(tmp_path):
    from PIL import Image
    src = _photo(str(tmp_path / "phone.jpg"), (4032, 3024))
    out = renderer._insert_still(src, {"kind": "image"}, 1080, 1920,
                                 str(tmp_path))
    assert out != src
    with Image.open(out) as im:
        w, h = im.size
    # Covers 1080x1920 in either orientation, and nothing much beyond.
    assert h >= 1920 and w >= 1920 * 4032 / 3024 - 2
    assert w * h < 4032 * 3024 * 0.5
    # Cached: the second call reuses the same file.
    assert renderer._insert_still(src, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == out


def test_crop_keeps_the_pixels_the_crop_needs(tmp_path):
    from PIL import Image
    src = _photo(str(tmp_path / "wide.jpg"), (6000, 4000))
    item = {"kind": "image", "crop": [0.25, 0.25, 0.75, 0.75]}
    out = renderer._insert_still(src, item, 1080, 1920, str(tmp_path))
    with Image.open(out) as im:
        w, h = im.size
    # The middle half of the picture must still cover the frame.
    assert h * 0.5 >= 1920 - 2 and w * 0.5 >= 1080 - 2


def test_small_or_unusual_stills_are_used_as_they_are(tmp_path):
    small = _photo(str(tmp_path / "small.jpg"), (1200, 1600))
    assert renderer._insert_still(small, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == small
    gif = str(tmp_path / "anim.gif")
    _photo(gif, (4000, 4000))
    assert renderer._insert_still(gif, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == gif
    broken = str(tmp_path / "broken.jpg")
    open(broken, "wb").write(b"not an image")
    assert renderer._insert_still(broken, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == broken


def test_stills_pillow_cannot_convert_faithfully_are_left_to_ffmpeg(tmp_path):
    """Review fix: a 16-bit grayscale scan (an archive photo) clipped to
    white in Pillow's convert("RGB"); CMYK, animated and EXIF-carrying
    PNG/WebP pictures must reach ffmpeg exactly as uploaded."""
    import numpy as np
    from PIL import Image
    scan = str(tmp_path / "scan16.png")
    ramp = np.linspace(0, 65535, 4000 * 3000).reshape(3000, 4000)
    Image.fromarray(ramp.astype(np.uint16)).save(scan)
    with Image.open(scan) as im:
        assert im.mode.startswith("I")
    assert renderer._insert_still(scan, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path), cover=True) == scan
    cmyk = str(tmp_path / "print.jpg")
    Image.new("CMYK", (5000, 4000), (0, 50, 100, 0)).save(cmyk)
    assert renderer._insert_still(cmyk, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == cmyk
    anim = str(tmp_path / "loop.webp")
    frames = [Image.new("RGB", (4000, 4000), (i * 60, 0, 0))
              for i in range(3)]
    frames[0].save(anim, save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    assert renderer._insert_still(anim, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == anim
    tagged = str(tmp_path / "tagged.png")
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (5000, 4000), (1, 2, 3)).save(tagged,
                                                   exif=exif.tobytes())
    assert renderer._insert_still(tagged, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) == tagged
    # An ordinary 8-bit grayscale photo is still downscaled.
    gray = str(tmp_path / "gray.jpg")
    Image.new("L", (6000, 4000), 128).save(gray)
    assert renderer._insert_still(gray, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path)) != gray


def test_transparency_survives_the_downscale(tmp_path):
    from PIL import Image
    src = _photo(str(tmp_path / "logo.png"), (5000, 5000), "RGBA")
    out = renderer._insert_still(src, {"kind": "image"}, 1080, 1920,
                                 str(tmp_path))
    assert out.endswith(".png")
    with Image.open(out) as im:
        assert im.mode == "RGBA"


def test_exif_orientation_is_preserved(tmp_path):
    from PIL import Image
    src = str(tmp_path / "rotated.jpg")
    im = Image.new("RGB", (4032, 3024), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6                      # rotate 90 CW on display
    im.save(src, exif=exif.tobytes())
    out = renderer._insert_still(src, {"kind": "image"}, 1080, 1920,
                                 str(tmp_path))
    assert out != src
    with Image.open(out) as small:
        assert small.getexif().get(0x0112) == 6


def test_vm_memory_peak_is_sampled_from_meminfo(tmp_path):
    import time
    import resource_usage
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 12000000 kB\nMemAvailable: 11000000 kB\n")
    sampler = resource_usage.VmMemorySampler(str(tmp_path), interval_s=0.01)
    meminfo.write_text("MemTotal: 12000000 kB\nMemAvailable: 4000000 kB\n")
    deadline = time.monotonic() + 2.0
    while (sampler._peak_bytes or 0) < 8000000 * 1024 \
            and time.monotonic() < deadline:
        time.sleep(0.01)
    meminfo.write_text("MemTotal: 12000000 kB\nMemAvailable: 11500000 kB\n")
    assert sampler.finish() == round(8000000 / 1024, 1) or \
        abs(sampler.finish() - 8000000 / 1024) < 1.0
    # No meminfo (a developer machine): no number, never an error.
    assert resource_usage.VmMemorySampler(str(tmp_path / "none"),
                                          interval_s=0.01).finish() is None


def test_final_exports_are_labelled_final_not_draft():
    token = renderer._RENDER_JOB_TYPE.set("final")
    try:
        assert renderer._quality_label() == "final"
    finally:
        renderer._RENDER_JOB_TYPE.reset(token)
    token = renderer._RENDER_JOB_TYPE.set("preview")
    try:
        assert renderer._quality_label() == renderer._PREVIEW_QUALITY.get()
    finally:
        renderer._RENDER_JOB_TYPE.reset(token)


def test_caption_review_frames_are_bounded():
    times = [i * 0.5 for i in range(4000)]          # a 33-minute program
    picked = renderer._bounded_review_times(times, config.CAPTION_REVIEW_MAX_FRAMES)
    assert len(picked) == config.CAPTION_REVIEW_MAX_FRAMES <= 64
    assert picked[0] == times[0] and picked[-1] == times[-1]
    assert picked == sorted(picked)
    short = [1.0, 2.5, 7.25]                          # a short: untouched
    assert renderer._bounded_review_times(short, 64) == short
    assert renderer._bounded_review_times(times, 0) == times
    # The production call site applies the cap.
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "renderer.py")).read()
    assert "_bounded_review_times(\n                    caption_review_times(" in src


def test_cover_mode_stills_are_cut_to_the_exact_frame(tmp_path):
    from PIL import Image
    src = _photo(str(tmp_path / "phone.jpg"), (4032, 3024))
    out = renderer._insert_still(src, {"kind": "image"}, 1080, 1920,
                                 str(tmp_path), cover=True)
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
    # Never for an insert crop, a rotation or an EXIF-rotated photo: those
    # keep the cover-size still and the graph does the geometry.
    for item in ({"kind": "image", "crop": [0.1, 0.1, 0.9, 0.9]},
                 {"kind": "image", "rotation": 90}):
        kept = renderer._insert_still(src, item, 1080, 1920, str(tmp_path),
                                      cover=True)
        with Image.open(kept) as im:
            assert im.size != (1080, 1920)
    rotated = str(tmp_path / "rotated.jpg")
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (4032, 3024)).save(rotated, exif=exif.tobytes())
    kept = renderer._insert_still(rotated, {"kind": "image"}, 1080, 1920,
                                  str(tmp_path), cover=True)
    with Image.open(kept) as im:
        assert im.size != (1080, 1920)


def test_media_children_volunteer_for_the_oom_killer(monkeypatch, tmp_path):
    written = {}

    class FakeProc:
        pid = 4242

    real_open = open

    def fake_open(path, mode="r", *a, **k):
        if str(path) == "/proc/4242/oom_score_adj":
            class W:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def write(self_inner, value):
                    written["value"] = value
            return W()
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    media._prefer_child_for_oom(FakeProc())
    assert int(written["value"]) == media.MEDIA_CHILD_OOM_SCORE_ADJ > 0
    # Unavailable /proc (macOS, a locked-down sandbox): silently nothing.
    monkeypatch.setattr("builtins.open", real_open)
    class Missing:
        pid = 999999999
    media._prefer_child_for_oom(Missing())


def test_index_greeting_leaves_a_reasoning_model_room(monkeypatch):
    """147 of 204 greetings in two weeks came back empty at max_tokens=220."""
    import indexer
    import llm
    seen = {}

    def ask_text(system, user, **kw):
        seen.update(kw)
        return {"text": "Your video is ready to edit.", "prompt_tokens": 1,
                "completion_tokens": 1}

    class Db:
        def run(self, *_a, **_k):
            return None

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "ask_text", ask_text)
    out = indexer._greet_via_llm(Db(), 1, "12 shots", False, False,
                                 {"words": [{"w": "Hello"}]})
    assert out
    assert seen["max_tokens"] >= 1000
    assert seen["reasoning_effort"] == "low" and seen["retry_empty"] is True
