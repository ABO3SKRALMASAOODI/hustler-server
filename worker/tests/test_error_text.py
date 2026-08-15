import pathlib
import sys


WORKER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

import error_text  # noqa: E402


def test_excerpt_preserves_exception_head_and_ffmpeg_tail():
    raw = "ffmpeg version 9.0 " + ("banner " * 500) + \
        "Error initializing filter: No such filter: important_failure"
    out = error_text.excerpt(raw, 500)
    assert len(out) <= 500
    assert out.startswith("ffmpeg version 9.0")
    assert "diagnostic middle omitted" in out
    assert out.endswith("No such filter: important_failure")


def test_excerpt_leaves_short_errors_unchanged():
    assert error_text.excerpt("name 'x' is not defined", 500) == \
        "name 'x' is not defined"
