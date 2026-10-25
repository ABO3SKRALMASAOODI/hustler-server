"""A video link pasted into a project with no video becomes its main video.

FAILURES.md (25 Oct 2026): nine users pasted a YouTube or Drive link instead
of uploading (2136, 2373, 2345, 1633, 1655, 1746, 1775, 1800, 1801). The
concierge's capability list omitted link import in the no-video stage and is
declared exhaustive, so it denied it; none of them got a video in, while 114
of 114 real link fetches through the editor succeeded. Now the link is
registered as the original (the deferred-original shape) and the index job
fetches it; the subscribe wall answers it exactly as it answers an upload.

    cd backend && python -m pytest tests/test_link_import.py -q
"""
import os
import sys
import types

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video as v                                # noqa: E402


@pytest.mark.parametrize("text,url", [
    ("https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
    ("edit this https://www.youtube.com/watch?v=abc123&t=4s please",
     "https://www.youtube.com/watch?v=abc123&t=4s"),
    ("Can you cut this? https://drive.google.com/file/d/1AbC/view?usp=sharing.",
     "https://drive.google.com/file/d/1AbC/view?usp=sharing"),
    ("https://vimeo.com/123456 make a 60s teaser", "https://vimeo.com/123456"),
    ("https://cdn.example.com/media/wedding.MP4", "https://cdn.example.com/media/wedding.MP4"),
    ("https://www.dropbox.com/scl/fi/xyz/talk.mov?dl=0",
     "https://www.dropbox.com/scl/fi/xyz/talk.mov?dl=0"),
])
def test_video_links_are_recognised(text, url):
    assert v._pasted_video_link(text) == (url, None)


@pytest.mark.parametrize("text", [
    "https://drive.google.com/drive/folders/1xYz?usp=sharing",
    "https://www.dropbox.com/scl/fo/abc/folder?dl=0",
    "https://www.dropbox.com/sh/abc/AAA?dl=0",
])
def test_shared_folders_are_not_a_single_video(text):
    """2345 pasted a Drive FOLDER (17 Oct): there is no one video to import."""
    assert v._pasted_video_link(text) == (None, "folder")


@pytest.mark.parametrize("text", [
    "make me a video about cats", "see https://valmera.io/pricing",
    "https://open.spotify.com/track/abc", "https://soundcloud.com/a/b",
])
def test_other_text_is_not_a_video_link(text):
    assert v._pasted_video_link(text) == (None, None)


def test_a_bare_link_asks_for_nothing_but_a_request_does():
    url = "https://youtu.be/x"
    assert v._link_only(url, url)
    assert v._link_only(f"edit this {url}", url)
    assert not v._link_only(
        f"{url} cut it into a 60 second reel with captions", url)


class _Cur:
    def __init__(self, has_original=False):
        self.has_original = has_original
        self.sql, self._one, self.assets, self.jobs, self.messages = \
            [], None, [], [], []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        self._one = None
        if s.startswith("SELECT 1 FROM assets"):
            self._one = {"x": 1} if self.has_original else None
        elif s.startswith("INSERT INTO assets"):
            self.assets.append(params)
            self._one = {"id": 901}
        elif s.startswith("INSERT INTO chat_messages"):
            self.messages.append(params)
            self._one = {"id": 77}

    def fetchone(self):
        return self._one


def _register(monkeypatch, cur, gated, text="https://youtu.be/x"):
    monkeypatch.setattr(v, "_subscribe_gate_applies", lambda c, u: gated)
    monkeypatch.setattr(v, "_enqueue",
                        lambda c, p, u, t, payload: cur.jobs.append(
                            (t, payload)) or 555)
    return v._register_link_original(cur, {"chat_session_id": 40}, 7, 60,
                                     "https://youtu.be/x", text)


def test_a_paying_users_link_is_registered_and_indexed(monkeypatch):
    cur = _Cur()
    out = _register(monkeypatch, cur, gated=False,
                    text="https://youtu.be/x make a 30 s highlight")
    assert out == {"asset_id": 901, "subscribe_gated": False,
                   "index_job_id": 555}
    project_id, key, meta = cur.assets[0]
    assert project_id == 7 and key.startswith("originals/7/")
    assert "NULL" in next(q for q in cur.sql if q.startswith("INSERT INTO assets"))
    assert meta.adapted["link_src"] == "https://youtu.be/x"
    assert meta.adapted["upload_state"] == "pending"
    assert cur.jobs == [("index", {"asset_id": 901})]
    assert "start on your request automatically" in cur.messages[0][1]
    assert any("FOR UPDATE" in q for q in cur.sql)


def test_the_wall_answers_a_link_exactly_as_an_upload(monkeypatch):
    """No job, no fetch, the same chat wall; checkout's heal starts it."""
    cur = _Cur()
    locks = []
    monkeypatch.setattr(v, "_record_upload_subscription_lock",
                        lambda c, session, asset: locks.append(
                            (session, asset)) or 88)
    out = _register(monkeypatch, cur, gated=True)
    assert out["subscribe_gated"] is True and out["index_job_id"] is None
    assert out["message_id"] == 88
    assert locks == [(40, 901)]
    assert cur.jobs == []


def test_a_project_with_a_video_is_left_alone(monkeypatch):
    cur = _Cur(has_original=True)
    assert _register(monkeypatch, cur, gated=False) is None
    assert cur.assets == [] and cur.jobs == []


def test_the_concierge_no_longer_denies_link_import(monkeypatch):
    """The exact denial 2136, 2373 and 2345 got came from a no-video fact
    list that omitted links."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("URL_FETCH_ENABLED", "1")
    seen = {}

    class _Resp:
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(
            content='{"act": false, "reply": "Paste it here."}'))]
        usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1)

    def create(**kw):
        seen["system"] = kw["messages"][0]["content"]
        return _Resp()

    monkeypatch.setattr(v, "_concierge_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            create=create))))
    v._concierge_reply("no_video", [{"role": "user",
                                     "content": "can you edit a youtube link?"}],
                       [], can_act=True)
    assert "pasting its LINK" in seen["system"]
    assert "Never say links can't be imported" in seen["system"]
    assert "FOLDER" in seen["system"]
