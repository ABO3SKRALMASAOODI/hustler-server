"""A file with no moov is recognised by measuring it, before anything reads it
(account 2123, 5 Nov 2026: three index jobs of two attempts each on one file).
Real ffmpeg, real ffprobe: nothing here is a hand-made fixture but the cut."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import failure_policy  # noqa: E402
import indexer  # noqa: E402
import main  # noqa: E402
import media  # noqa: E402
import storage  # noqa: E402


def encode(path, *extra):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x240:r=30:d=4", "-f", "lavfi", "-i",
         "sine=f=440:d=4", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", *extra, str(path)], check=True)
    return str(path)


def state(path):
    total = os.path.getsize(path)

    def read(offset, length):
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)
    return media.mp4_state(read, total)


@pytest.fixture(scope="module")
def whole(tmp_path_factory):
    return encode(tmp_path_factory.mktemp("media") / "whole.mp4")


def cut(whole, tmp_path, fraction):
    with open(whole, "rb") as fh:
        data = fh.read()
    path = tmp_path / "cut.mp4"
    path.write_bytes(data[:int(len(data) * fraction)])
    return str(path)


def test_a_whole_file_is_complete_and_probes(whole):
    assert state(whole) == "complete"
    assert media.probe(whole)["duration"] > 3


def test_faststart_and_fragmented_files_are_complete(tmp_path):
    assert state(encode(tmp_path / "a.mp4", "-movflags", "+faststart")) == "complete"
    assert state(encode(tmp_path / "b.mp4", "-movflags",
                        "frag_keyframe+empty_moov")) == "complete"
    assert state(encode(tmp_path / "c.mov")) == "complete"


@pytest.mark.parametrize("fraction", [0.1, 0.5, 0.9])
def test_a_copy_cut_anywhere_is_unfinished_and_ffprobe_agrees(whole, tmp_path, fraction):
    path = cut(whole, tmp_path, fraction)
    assert state(path) == "unfinished"
    with pytest.raises(media.MediaError):
        media.probe(path)


def test_a_cut_through_the_moov_proves_nothing_alone(whole, tmp_path):
    # moov sits last in a default mp4: 30 bytes short cuts into it, and that
    # file opens and plays. The walk may not refuse it.
    with open(whole, "rb") as fh:
        data = fh.read()
    path = tmp_path / "cut.mp4"
    path.write_bytes(data[:-30])
    assert state(str(path)) == "cut_moov"
    assert media.probe(str(path))["duration"] > 3


def test_other_containers_are_not_judged(tmp_path):
    assert state(encode(tmp_path / "a.mkv")) == "unknown"
    path = tmp_path / "a.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=64x64:r=10:d=1", "-c:v", "libvpx", str(path)],
                   check=True)
    assert state(str(path)) == "unknown"


def test_the_customers_real_boxes(monkeypatch):
    # Asset 40278: ftyp 28, wide 8, mdat 590,450,793, in 15,728,640 bytes.
    import struct
    boxes = {0: struct.pack(">I4s", 28, b"ftyp"), 28: struct.pack(">I4s", 8, b"wide"),
             36: struct.pack(">I4s", 590_450_793, b"mdat")}
    assert media.mp4_state(lambda o, n: boxes[o].ljust(n, b"\0"), 15_728_640) == \
        "unfinished"


class _Db:
    def run(self, fn, *args):
        return None


ASSET = {"id": 40278, "kind": "original", "bytes": 15_728_640,
         "storage_key": "originals/5498/3c071d485b9f.mp4", "meta": {}}


def serve(monkeypatch, path):
    def read_range(key, offset, length):
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)
    monkeypatch.setattr(storage, "read_range", read_range)
    monkeypatch.setattr(storage, "object_bytes", lambda key: os.path.getsize(path))
    monkeypatch.setattr(storage, "presign_get", lambda key, expires=0: path)


def test_the_index_refuses_before_it_reads_the_file(whole, tmp_path, monkeypatch):
    path = cut(whole, tmp_path, 0.3)
    serve(monkeypatch, path)
    with pytest.raises(media.UnfinishedFile) as err:
        indexer._refuse_unfinished_source(ASSET)
    decision = failure_policy.decision_for(err.value, "index")
    assert decision.kind == "unfinished_file" and decision.retryable is False
    assert decision.max_attempts == 0


def test_a_failed_read_or_a_music_file_is_never_refused(monkeypatch):
    monkeypatch.setattr(storage, "object_bytes", lambda key: 15_728_640)
    monkeypatch.setattr(storage, "read_range", lambda *a: None)
    indexer._refuse_unfinished_source(ASSET)
    indexer._refuse_unfinished_source(dict(ASSET, kind="music"))
    monkeypatch.setattr(storage, "object_bytes", lambda key: None)
    indexer._refuse_unfinished_source(ASSET)


def test_the_walk_is_sized_from_the_object_never_its_row(whole, monkeypatch):
    # A row that disagrees with the object must not prove a good file cut.
    serve(monkeypatch, whole)
    indexer._refuse_unfinished_source(dict(ASSET, bytes=1000))


def test_a_cut_moov_that_opens_is_indexed_and_one_that_cannot_is_refused(
        whole, tmp_path, monkeypatch):
    with open(whole, "rb") as fh:
        data = fh.read()
    path = tmp_path / "tail.mp4"
    path.write_bytes(data[:-30])
    serve(monkeypatch, str(path))
    indexer._refuse_unfinished_source(ASSET)
    monkeypatch.setattr(media, "probe", lambda src: (_ for _ in ()).throw(
        media.MediaError("error reading header")))
    with pytest.raises(media.UnfinishedFile):
        indexer._refuse_unfinished_source(ASSET)


def test_ffprobes_words_alone_never_condemn_a_file(tmp_path):
    """A streamed index prints "moov atom not found" when a read of a whole file
    breaks before its moov. Measured on real ffprobe over HTTP, 6 Nov 2026."""
    err = media.MediaError("ffprobe failed: [mov,mp4,m4a,3gp,3g2,mj2 @ 0x1] moov "
                           "atom not found | https://x/o: Invalid data found")
    decision = failure_policy.classify(err, "index")
    assert decision.kind != "unfinished_file"
    assert decision.retryable is True and decision.max_attempts == 2
