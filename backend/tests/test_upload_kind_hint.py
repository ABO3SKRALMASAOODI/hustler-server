"""An upload refused for its file type says where that file DOES go (R2
journeys, 30 Oct 2026: an audio-only podcast sent over MCP as the main video
was told only "File type .m4a not supported")."""
import pytest

import storage


@pytest.mark.parametrize("name,kind,hint", [
    ("voice_memo.m4a", "original", "upload it as music (kind 'music')"),
    ("episode.mp3", "clip", "upload it as music (kind 'music')"),
    ("cover.png", "original", "upload it as an image (kind 'image')"),
    ("clip.mp4", "music", "upload it as a clip (kind 'clip')"),
])
def test_refusal_names_the_right_kind(name, kind, hint):
    with pytest.raises(ValueError) as e:
        storage.validate_upload(name, 1000, kind)
    msg = str(e.value)
    assert msg.startswith("File type ") and "not supported. Allowed:" in msg
    assert hint in msg


def test_unknown_types_keep_the_plain_refusal():
    with pytest.raises(ValueError) as e:
        storage.validate_upload("notes.docx", 1000, "original")
    assert str(e.value) == ("File type .docx not supported. Allowed: .m4v, .mkv, "
                            ".mov, .mp4, .webm.")


def test_allowed_files_pass():
    assert storage.validate_upload("a.m4a", 1000, "music")[0] == ".m4a"
    assert storage.validate_upload("a.mov", 1000, "original")[0] == ".mov"
