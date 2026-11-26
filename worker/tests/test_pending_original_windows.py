"""A preview renders while the original is still uploading, even when the
edit's windows name the original (tickets #1851-#1853/#1857, p6329).

A slow link sent the upload proxy-first. The Director's First cut drew 59
cutaway windows whose asset_key is the project's own original. The preview's
main source had switched to the proxy, but each window fetched the original's
key, which was not in storage yet: HeadObject 404 on the approval render, the
proof and the user's preview. The proxy is the same footage on the same clock,
so those windows read it until the original lands.
"""
import json
import shutil
import subprocess

import pytest

import renderer

ORIGINAL = "originals/6329/708a093868882688aeb98442.mp4"


def _edl(key=ORIGINAL):
    return {"keep": [[0, 3]], "frame": {"ratio": "source"}, "overlays": [
        dict(id="ov.b01.s2", asset_key=key, kind="video", start=0.5,
             duration_s=1.0, x=.5, y=.5, scale=.4, fit="cover",
             source_start_s=1.5, source_crop=[.33, 0, .65, 1])]}


def _original(state="pending"):
    return {"storage_key": ORIGINAL, "meta": {"upload_state": state}}


def test_a_pending_original_named_by_a_window_reads_the_local_proxy(tmp_path):
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"x")
    src_asset = {"storage_key": "proxies/6329/8e5f.mp4"}
    got = renderer._pending_original_aliases(
        _edl(), _original(), src_asset, str(proxy), exists=lambda k: False)
    assert got == {ORIGINAL: str(proxy)}
    # Registered as ready but the object is not there (never landed): same.
    got = renderer._pending_original_aliases(
        _edl(), _original("ready"), src_asset, str(proxy),
        exists=lambda k: False)
    assert got == {ORIGINAL: str(proxy)}


def test_nothing_is_aliased_once_the_original_is_readable(tmp_path):
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"x")
    src_asset = {"storage_key": "proxies/6329/8e5f.mp4"}
    landed = renderer._pending_original_aliases(
        _edl(), _original("ready"), src_asset, str(proxy),
        exists=lambda k: True)
    assert landed == {}
    # The render reads the original itself (approval, final): no alias.
    assert renderer._pending_original_aliases(
        _edl(), _original(), {"storage_key": ORIGINAL}, str(proxy)) == {}
    # An edit that never names the original needs none.
    assert renderer._pending_original_aliases(
        _edl("media/6329/other.mp4"), _original(), src_asset, str(proxy),
        exists=lambda k: False) == {}
    # A streamed (remote) proxy has no local file to point at.
    assert renderer._pending_original_aliases(
        _edl(), _original(), src_asset, "https://bucket.invalid/p.mp4",
        exists=lambda k: False) == {}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
def test_preview_with_windows_on_an_uploading_original_renders(tmp_path,
                                                               monkeypatch):
    proxy, out = tmp_path / "proxy.mp4", tmp_path / "preview.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:r=24:d=3", "-c:v", "libx264",
                    "-threads", "1", "-pix_fmt", "yuv420p", str(proxy)],
                   check=True)

    def missing(*_a, **_k):
        raise RuntimeError("An error occurred (404) when calling the "
                           "HeadObject operation: Not Found")
    # The box path HEADs a window's object before reading it.
    monkeypatch.setenv("EXECUTOR_PROVIDER", "box")
    for name in ("object_bytes", "download_to", "presign_get", "head_bytes"):
        if hasattr(renderer.storage, name):
            monkeypatch.setattr(renderer.storage, name, missing)
    monkeypatch.setattr(renderer, "_job_cached_source", lambda *a, **k: None)
    index = {"video": {"duration": 3, "width": 320, "height": 180, "fps": 24,
                       "has_audio": False},
             "words": [], "sentences": [], "silences": [], "shots": []}

    with pytest.raises(Exception, match="404"):
        renderer.render_edl(_edl(), index, str(proxy), str(out),
                            str(tmp_path), True, suppress_outro=True)

    aliases = renderer._pending_original_aliases(
        _edl(), _original(), {"storage_key": "proxies/6329/8e5f.mp4"},
        str(proxy), exists=lambda k: False)
    renderer.render_edl(_edl(), index, str(proxy), str(out), str(tmp_path),
                        True, suppress_outro=True, asset_locals=aliases)
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(out)]))
    assert abs(float(probe["format"]["duration"]) - 3) < .15
