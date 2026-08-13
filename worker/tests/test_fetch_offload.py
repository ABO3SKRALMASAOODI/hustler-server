"""YouTube acquisition escapes a walled dispatcher without losing review."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import agent_tools                                              # noqa: E402
import config                                                   # noqa: E402
import http_server                                              # noqa: E402
import remote                                                   # noqa: E402
import song_find                                                # noqa: E402
import storage                                                  # noqa: E402
import url_media                                                # noqa: E402


def test_executor_exposes_the_stateless_fetch_runner():
    assert http_server.COMPUTE_RUNNERS["fetch"] is url_media.run_fetch_job
    assert http_server.COMPUTE_RUNNERS["search"] is song_find.run_search_job


def test_fetch_runner_uploads_media_and_actual_review_frames(
        monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    uploaded = []
    sampled = []

    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(url_media, "fetch", lambda *a, **k: {
        "path": str(source), "kind": url_media.KIND_VIDEO,
        "bytes": 5, "duration_s": 10.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": True, "filename": "shot.mp4",
        "source_url": "https://youtube.com/watch?v=x",
        "extractor": "Youtube", "title": "shot", "uploader": "maker",
    })
    monkeypatch.setattr(url_media, "storage_key",
                        lambda *a: "fetched/9/main.mp4")
    monkeypatch.setattr(storage, "upload_file",
                        lambda path, key, ctype: uploaded.append(
                            (path, key, ctype)))

    def frame_at(_path, at, out, width=None):
        sampled.append((round(at, 2), width))
        with open(out, "wb") as fh:
            fh.write(b"jpg")

    monkeypatch.setattr(url_media.media, "frame_at", frame_at)
    got = url_media.run_fetch_job(None, {
        "project_id": 9,
        "payload": {"url": "https://youtube.com/watch?v=x", "review": True},
    })

    assert got["ok"] and got["storage_key"] == "fetched/9/main.mp4"
    assert sampled == [(0.8, 768), (3.4, 768), (6.1, 768), (8.7, 768)]
    assert len(got["review_keys"]) == 4
    assert uploaded[0][1] == "fetched/9/main.mp4"
    assert all(row[1].startswith("scratch/9/fetch-review/")
               for row in uploaded[1:])


def test_remote_fetch_falls_through_a_cloud_wall_to_modal(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "https://cloud")
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    calls = []

    def cloud(job):
        calls.append(("cloud", job["type"]))
        return {"ok": False, "error": "Sign in to confirm you're not a bot",
                "access_blocked": True}

    def modal(job, function_override=None):
        calls.append(("modal", function_override))
        return {"ok": True, "storage_key": "fetched/3/song.mp3"}

    monkeypatch.setattr(remote, "_run_cloud", cloud)
    monkeypatch.setattr(remote, "_run_modal", modal)
    got = remote.run_fetch_remote(
        3, {"url": "https://youtube.com/watch?v=x"}, user_id=4)

    assert got["ok"] and got["fetch_provider"] == "modal"
    assert calls == [("cloud", "fetch"), ("modal", "heavy")]


class _Db:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return None


class _Ctx:
    sight_out = False
    direct_sight = False

    def __init__(self, workdir):
        self.workdir = str(workdir)
        self.project_id = 7
        self.job = {"user_id": 8}
        self.urls_fetched = []
        self.pending_images = []
        self.db = _Db()


def test_walled_youtube_routes_remote_without_a_doomed_local_attempt(
        monkeypatch, tmp_path):
    ctx = _Ctx(tmp_path)
    monkeypatch.setattr(agent_tools.ytaccess, "youtube_walled", lambda: True)
    monkeypatch.setattr(agent_tools.remote, "fetch_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.url_media, "fetch",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("known-walled dispatcher must be skipped")))
    monkeypatch.setattr(agent_tools.remote, "run_fetch_remote",
                        lambda *a, **k: {
                            "ok": True, "kind": url_media.KIND_AUDIO,
                            "storage_key": "fetched/7/song.mp3", "bytes": 9,
                            "duration_s": 45.0, "filename": "song.mp3",
                            "source_url": "https://youtube.com/watch?v=x",
                            "extractor": "Youtube", "fetch_provider": "modal",
                        })
    monkeypatch.setattr(
        agent_tools.storage, "upload_file",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("executor already uploaded the media")))

    out = agent_tools.fetch_url(
        ctx, "https://youtube.com/watch?v=x", as_kind="music")

    assert "storage_key=fetched/7/song.mp3" in out
    assert ctx.urls_fetched[0]["fetch_provider"] == "modal"


def test_non_youtube_music_never_enters_the_new_remote_path(
        monkeypatch, tmp_path):
    ctx = _Ctx(tmp_path)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(agent_tools.remote, "fetch_available", lambda: True)
    monkeypatch.setattr(agent_tools.ytaccess, "youtube_walled", lambda: True)
    monkeypatch.setattr(
        agent_tools.remote, "run_fetch_remote",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("SoundCloud/direct music must remain local")))
    monkeypatch.setattr(agent_tools.url_media, "fetch", lambda *a, **k: {
        "path": str(source), "kind": url_media.KIND_AUDIO, "bytes": 5,
        "duration_s": 12.0, "filename": "song.mp3",
        "source_url": "https://api.soundcloud.com/tracks/1",
        "extractor": "Soundcloud",
    })
    monkeypatch.setattr(agent_tools.storage, "upload_file", lambda *a, **k: None)

    out = agent_tools.fetch_url(
        ctx, "https://api.soundcloud.com/tracks/1", as_kind="music")
    assert "storage_key=" in out
    assert ctx.urls_fetched[0]["fetch_provider"] == "worker"


def test_walled_footage_discovery_uses_remote_search(monkeypatch, tmp_path):
    ctx = _Ctx(tmp_path)
    monkeypatch.setattr(agent_tools.song_find, "footage_available", lambda: True)
    monkeypatch.setattr(agent_tools.song_find, "footage_allowed_for",
                        lambda user_id: True)
    monkeypatch.setattr(agent_tools.ytaccess, "youtube_walled", lambda: True)
    monkeypatch.setattr(agent_tools.remote, "fetch_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.song_find, "search_footage",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("known-walled discovery must leave Render")))
    monkeypatch.setattr(agent_tools.remote, "run_search_remote",
                        lambda *a, **k: {
                            "ok": True,
                            "hits": [{
                                "title": "Starship Flight Test",
                                "uploader": "SpaceX", "duration_s": 90,
                                "url": "https://youtube.com/watch?v=flight",
                            }],
                            "fetch_provider": "cloud_run",
                        })

    out = agent_tools.find_footage(ctx, "starship launch")
    assert "youtube.com/watch?v=flight" in out
    assert "as_kind='clip'" in out


def test_search_runner_returns_access_wall_as_provider_fallback_data(
        monkeypatch):
    monkeypatch.setattr(
        song_find, "search_footage",
        lambda *a, **k: (_ for _ in ()).throw(
            song_find.SongFindError(
                "Sign in to confirm you're not a bot")))
    got = song_find.run_search_job(None, {
        "project_id": 7,
        "payload": {"query": "rocket", "mode": "footage"},
    })
    assert not got["ok"] and got["access_blocked"]
