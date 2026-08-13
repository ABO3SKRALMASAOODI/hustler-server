"""Fetched visual assets are shown to the agent before timeline placement."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import url_media                                               # noqa: E402


class _Ctx:
    sight_out = True
    direct_sight = False

    def __init__(self, workdir):
        self.workdir = str(workdir)
        self.pending_images = []


def test_downloaded_video_queues_spread_frames_from_the_actual_file(
        monkeypatch, tmp_path):
    source = tmp_path / "download.mp4"
    source.write_bytes(b"source")
    sampled = []
    delivered = {}

    def fake_frame(_source, at, dest, width=None):
        sampled.append((at, width))
        with open(dest, "wb") as fh:
            fh.write(b"jpeg")

    def fake_deliver(_ctx, frames, labels, question, subject):
        delivered.update(frames=frames, labels=labels,
                         question=question, subject=subject)

    monkeypatch.setattr(agent_tools.media, "frame_at", fake_frame)
    monkeypatch.setattr(agent_tools, "_deliver_frames", fake_deliver)
    count = agent_tools._queue_download_review(
        _Ctx(tmp_path), str(source), url_media.KIND_VIDEO, 10.0,
        "chosen b-roll")

    assert count == 4
    assert [x[0] for x in sampled] == [0.8, 3.4, 6.1, 8.7]
    assert all(x[1] == 768 for x in sampled)
    assert delivered["labels"] == ["0.8s", "3.4s", "6.1s", "8.7s"]
    assert "subject" in delivered["question"]
    assert "chosen b-roll" in delivered["subject"]


def test_downloaded_image_is_copied_out_of_ephemeral_fetch_dir(
        monkeypatch, tmp_path):
    fetch_dir = tmp_path / "fetch"
    fetch_dir.mkdir()
    source = fetch_dir / "image.png"
    source.write_bytes(b"png payload")
    delivered = {}

    def fake_deliver(_ctx, frames, labels, _question, _subject):
        delivered.update(frames=frames, labels=labels)

    monkeypatch.setattr(agent_tools, "_deliver_frames", fake_deliver)
    count = agent_tools._queue_download_review(
        _Ctx(tmp_path), str(source), url_media.KIND_IMAGE, label="web image")

    assert count == 1
    copied = delivered["frames"][0]
    assert copied != str(source)
    assert os.path.dirname(copied) == str(tmp_path)
    assert open(copied, "rb").read() == b"png payload"
    assert delivered["labels"] == ["full image"]


def test_audio_downloads_do_not_create_visual_review_work(tmp_path):
    assert agent_tools._queue_download_review(
        _Ctx(tmp_path), str(tmp_path / "song.mp3"),
        url_media.KIND_AUDIO, 30.0) == 0
