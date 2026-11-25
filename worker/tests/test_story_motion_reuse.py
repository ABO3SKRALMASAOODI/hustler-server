import json
from types import SimpleNamespace

from footage import motion, search


def test_second_pass_reopens_verified_flow_on_identical_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTAGE_KEEP_SOURCES", str(tmp_path / "sources"))
    original = tmp_path / "scan.mp4"
    original.write_bytes(b"first original")
    calls = []
    def measure(path, a, b, ffmpeg=None):
        calls.append((a, b))
        return dict(p90=2.241, peak=38.75)
    monkeypatch.setattr(motion, "of_shot", measure)
    first = motion.of_shots(str(original), [(36., 41.)])
    retained = tmp_path / "retained.mp4"
    retained.write_bytes(original.read_bytes())
    assert motion.of_shots(str(retained), [(36., 41.)]) == first
    assert calls == [(36., 41.)]
    retained.write_bytes(b"different bytes")
    motion.of_shots(str(retained), [(36., 41.), (37., 41.)])
    assert len(calls) == 3


def test_youtube_search_keeps_valid_siblings_of_null_result(monkeypatch):
    data = {"entries": [{"id":"a","title":"archive"}, None, {"id":"b","duration":46}]}
    monkeypatch.setattr(search.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(data)))
    monkeypatch.setattr(search.fetch, "ytdlp_cmd", lambda: ["yt-dlp"])
    assert [r["id"] for r in search.run([{"q":"Sony Walkman"}])[0]] == ["a","b"]


def test_recovered_source_reuses_only_the_same_completed_scene_scan(tmp_path, monkeypatch):
    from footage import shots
    monkeypatch.setenv("FOOTAGE_KEEP_SOURCES", str(tmp_path / "sources"))
    original = tmp_path / "source.mp4"
    copy = tmp_path / "retained.mp4"
    original.write_bytes(b"film")
    copy.write_bytes(original.read_bytes())
    calls = []
    def scan(*args, **kw):
        calls.append(args)
        return SimpleNamespace(stderr="pts_time:5.1", returncode=1 if len(calls) == 1 else 0)
    monkeypatch.setattr(shots.subprocess, "run", scan)
    for file in (original, original, copy):
        assert shots.cuts(str(file), 8.) == [(0., 5.1), (5.1, 8.)]
    assert len(calls) == 2
    assert shots.cuts(str(copy), 7.)[-1][1] == 7.
    assert len(calls) == 3
