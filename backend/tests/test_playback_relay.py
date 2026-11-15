"""P0 #43: a device that cannot reach the storage host still plays its video.

On 15 Nov an Android phone could not reach the B2 endpoint at all: its direct
upload sent 0 bytes (the upload relay carried it), then the player failed with
MediaError 4 on the original and on the proxy, and every re-presign was one
more address on the same unreachable host. The player's last retry now asks
for a relay address on this API, which the phone reached.
"""
import contextlib
import io
import os
import sys
import time

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import storage  # noqa: E402
from routes import video  # noqa: E402

SECRET = "test-secret-for-the-playback-relay-0123456789"
BYTES = bytes(range(256)) * 64 * 1024          # 16 MiB, every byte known
ASSET = {"id": 41507, "project_id": 5847, "kind": "proxy",
         "storage_key": "proxies/5847/x.mp4", "bytes": len(BYTES), "meta": {}}


class Body(io.BytesIO):
    closed_by_route = False

    def close(self):
        Body.closed_by_route = True
        super().close()


@pytest.fixture
def app(monkeypatch):
    owner = {"user": 3111}

    class Cur:
        def execute(self, sql, params):
            self.row = (dict(ASSET) if params == (ASSET["id"], owner["user"])
                        else None)

        def fetchone(self):
            return self.row

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: Cur()})()

    def open_range(key, first, last=None):
        assert key == ASSET["storage_key"]
        if first >= len(BYTES):
            return {"unsatisfiable": True, "total": len(BYTES)}
        end = len(BYTES) - 1 if last is None else min(last, len(BYTES) - 1)
        return {"body": Body(BYTES[first:end + 1]), "start": first,
                "end": end, "total": len(BYTES), "content_type": "video/mp4"}

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video.storage, "open_range", open_range)
    monkeypatch.setattr(video.storage, "presign_get",
                        lambda *a, **k: "https://s3.example/x.mp4?sig")
    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = SECRET
    flask_app.register_blueprint(video.video_bp)
    return flask_app


def _relay_path(app, user_id=3111):
    with app.test_request_context(f"/assets/{ASSET['id']}/url?relay=1"):
        response = video.asset_url.__wrapped__(user_id=user_id,
                                                asset_id=ASSET["id"])
    return response.get_json()["relay_path"]


def test_relay_answers_a_same_api_address_not_another_storage_link(app):
    path = _relay_path(app)
    assert path.startswith(f"/assets/{ASSET['id']}/stream?t=")
    assert "s3.example" not in path


def test_the_player_without_relay_still_gets_the_direct_link(app):
    with app.test_request_context(f"/assets/{ASSET['id']}/url"):
        response = video.asset_url.__wrapped__(user_id=3111,
                                               asset_id=ASSET["id"])
    assert response.get_json()["url"] == "https://s3.example/x.mp4?sig"


def test_a_video_element_plays_every_byte_through_capped_ranges(app):
    client = app.test_client()
    path = _relay_path(app)
    got, first = b"", 0
    while first < len(BYTES):
        res = client.get(path, headers={"Range": f"bytes={first}-"})
        assert res.status_code == 206
        assert res.headers["Accept-Ranges"] == "bytes"
        start, _, rest = res.headers["Content-Range"][6:].partition("-")
        end, _, total = rest.partition("/")
        assert int(start) == first and int(total) == len(BYTES)
        assert int(end) - int(start) + 1 <= video.STREAM_CHUNK_BYTES
        assert int(res.headers["Content-Length"]) == len(res.data)
        got += res.data
        first = int(end) + 1
    assert got == BYTES
    assert Body.closed_by_route


def test_no_range_header_starts_at_zero_and_a_suffix_reads_the_tail(app):
    client = app.test_client()
    path = _relay_path(app)
    res = client.get(path)
    assert res.status_code == 206 and res.data == BYTES[:video.STREAM_CHUNK_BYTES]
    res = client.get(path, headers={"Range": "bytes=-100"})
    assert res.data == BYTES[-100:]


def test_past_the_end_is_416(app):
    res = app.test_client().get(_relay_path(app),
                                headers={"Range": f"bytes={len(BYTES)}-"})
    assert res.status_code == 416
    assert res.headers["Content-Range"] == f"bytes */{len(BYTES)}"


def test_the_token_opens_one_asset_for_its_owner_only(app):
    client = app.test_client()
    path = _relay_path(app)
    token = path.split("t=", 1)[1]
    assert client.get(f"/assets/999/stream?t={token}").status_code == 401
    assert client.get(f"/assets/{ASSET['id']}/stream").status_code == 401
    login = jwt.encode({"sub": "3111"}, SECRET, algorithm="HS256")
    assert client.get(f"/assets/{ASSET['id']}/stream?t={login}").status_code == 401
    expired = jwt.encode({"sub": "3111", "aid": ASSET["id"],
                          "typ": "asset_stream", "exp": int(time.time()) - 5},
                         SECRET, algorithm="HS256")
    assert client.get(f"/assets/{ASSET['id']}/stream?t={expired}").status_code == 401
    with app.app_context():
        stranger = video._stream_token(999, ASSET["id"])
    assert client.get(
        f"/assets/{ASSET['id']}/stream?t={stranger}").status_code == 404


def test_storage_content_range_is_read_from_the_header():
    assert storage._content_range("bytes 0-99/1234") == (0, 99, 1234)
    assert storage._content_range("bytes 5-9/*") == (5, 9, None)
    assert storage._content_range("") is None
