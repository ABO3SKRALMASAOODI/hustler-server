"""P0 #60 (31 Oct 2026): the render box range-read a 1.1 GB original.

Preview 126723 (a 32 s approval preview of a 15-min, 1.1 GB original whose
audio and video sit ~150 MB apart in the file) streamed the source from R2
because it crossed CLOUDFLARE_STREAM_SOURCE_MIN_BYTES (512 MB). ffmpeg seeks
between the two tracks on every chunk, each seek a new HTTPS connection
(184 connections for 3 s of output), so the render took 910 s and the First
cut gave up at its 900 s render wait. The box has its own disk: it stages a
source in full below BOX_STREAM_SOURCE_MIN_BYTES; Cloudflare is unchanged.
"""
import config
import execution_inputs
import renderer

GB = 1024 ** 3
P0_60_ORIGINAL = {"storage_key": "originals/5284/14867ae57c76.mp4",
                  "bytes": 1119968852, "duration_s": 902.445}
LONG_PROXY = {"storage_key": "proxies/1/a.mp4", "bytes": 900 * 1024 ** 2,
              "duration_s": 3 * 3600.0}


def test_box_stages_the_p0_60_original(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "box")
    assert renderer._stream_cloudflare_source(P0_60_ORIGINAL) is False
    assert execution_inputs.executor_streams_source(P0_60_ORIGINAL) is False
    # long but small: staged too (and cached per slot below 4 GB)
    assert execution_inputs.executor_streams_source(LONG_PROXY) is False


def test_box_still_streams_a_source_too_big_to_stage(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "box")
    huge = dict(P0_60_ORIGINAL, bytes=config.BOX_STREAM_SOURCE_MIN_BYTES)
    assert renderer._stream_cloudflare_source(huge) is True
    monkeypatch.setattr(config, "BOX_STREAM_SOURCE_MIN_BYTES", 0)
    assert execution_inputs.executor_streams_source(
        dict(P0_60_ORIGINAL, bytes=40 * GB)) is False


def test_cloudflare_and_local_routing_unchanged(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    assert renderer._stream_cloudflare_source(P0_60_ORIGINAL) is True
    assert renderer._stream_cloudflare_source(LONG_PROXY) is True
    assert renderer._stream_cloudflare_source(
        {"storage_key": "o/a.mp4", "bytes": 80 * 1024 ** 2,
         "duration_s": 60.0}) is False
    monkeypatch.delenv("EXECUTOR_PROVIDER")
    assert renderer._stream_cloudflare_source(P0_60_ORIGINAL) is False
    for bad in (None, {}, {"bytes": "x"}):
        monkeypatch.setenv("EXECUTOR_PROVIDER", "box")
        assert execution_inputs.executor_streams_source(bad) is False
