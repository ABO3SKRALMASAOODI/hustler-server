import json

from footage import fetch


def test_exact_original_reused_without_network_or_mutation(tmp_path, monkeypatch):
    original = tmp_path / "podcast.mp4"
    original.write_bytes(b"original" * 1000)
    url = "https://www.youtube.com/watch?v=anAmTGcewX8"
    monkeypatch.setenv("FOOTAGE_LOCAL_SOURCES", json.dumps([dict(url=url, path=str(original))]))
    monkeypatch.setattr(fetch, "run_ladder", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network")))
    assert fetch.proxy(url, str(tmp_path / "scan"))[0] == str(original)
    assert fetch.streams(url)["url"] == str(original)
    assert fetch.whole(url, str(tmp_path / "whole")) == str(original)
    assert original.read_bytes() == b"original" * 1000
    assert fetch.local_source(url + "other") is None


def test_missing_or_invalid_manifest_does_not_claim_a_cache(tmp_path, monkeypatch):
    for value in ("broken", "{}", "[null]", json.dumps([dict(url="u", path=str(tmp_path / "gone"))])):
        monkeypatch.setenv("FOOTAGE_LOCAL_SOURCES", value)
        assert fetch.local_source("u") is None
