"""Failed media commands keep the stderr that explains them.

Production failure rows were 500-character excerpts made entirely of libass
font-loading chatter ("Error opening memory font 'LICENSE-Anton.txt'"), and an
OOM-killed final read as "ffmpeg failed (exit -9)" followed by that chatter.
"""
import os
import signal
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import media  # noqa: E402

FONTS = os.path.join(os.path.dirname(__file__), "..", "fonts")


def _script(tmp_path, body):
    path = tmp_path / "fake_ffmpeg.py"
    path.write_text(textwrap.dedent(body))
    return [sys.executable, str(path)]


def test_tail_drops_libass_chatter_and_keeps_the_real_cause():
    lines = ["[Parsed_subtitles_4 @ 0x1] Loading font file '/w/fonts/Anton.ttf'",
             "[Parsed_subtitles_4 @ 0x1] Error opening memory font 'LICENSE-Anton.txt'",
             "[Parsed_subtitles_4 @ 0x1] Using font provider fontconfig",
             "[Parsed_overlay_9 @ 0x2] Failed to configure input pad on Parsed_overlay_9",
             "Error reinitializing filters!"]
    tail = media.stderr_tail(lines)
    assert "font" not in tail.lower()
    assert tail.endswith("Error reinitializing filters!")
    assert "Failed to configure input pad" in tail


def test_tail_is_bounded_and_keeps_the_newest_lines():
    lines = [f"line {i} " + "x" * 90 for i in range(100)]
    tail = media.stderr_tail(lines)
    assert len(tail) <= media.STDERR_TAIL_CHARS
    assert tail.endswith(lines[-1])
    assert "line 0 " not in tail
    one = media.stderr_tail(["y" * 5000])
    assert len(one) <= media.STDERR_TAIL_CHARS and one.endswith("y")


@pytest.mark.parametrize("progress", [False, True])
def test_failed_command_error_carries_its_stderr_tail(tmp_path, progress):
    cmd = _script(tmp_path, """
        import sys
        for i in range(30):
            sys.stderr.write("[Parsed_subtitles_0 @ 0x1] Loading font file 'f%d.ttf'\\n" % i)
        sys.stderr.write("[Parsed_overlay_3 @ 0x2] Failed to configure input pad\\n")
        sys.exit(1)
    """)
    kw = {"progress_cb": lambda p: None, "expected_out_s": 5.0} if progress else {}
    with pytest.raises(media.MediaError) as exc:
        media.run(cmd, timeout=30, **kw)
    assert not isinstance(exc.value, media.MediaOOMError)
    assert exc.value.stderr_tail.endswith("Failed to configure input pad")
    assert "Loading font file" not in str(exc.value)
    assert "Failed to configure input pad" in str(exc.value)


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs SIGKILL")
@pytest.mark.parametrize("progress", [False, True])
def test_sigkill_with_a_moving_oom_counter_is_an_oom_error(tmp_path, monkeypatch,
                                                           progress):
    counts = iter([4, 5])
    monkeypatch.setattr(media, "_oom_kill_count", lambda: next(counts))
    cmd = _script(tmp_path, """
        import os, signal, sys
        sys.stderr.write("frame graph configured\\n"); sys.stderr.flush()
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    kw = {"progress_cb": lambda p: None, "expected_out_s": 5.0} if progress else {}
    with pytest.raises(media.MediaOOMError) as exc:
        media.run(cmd, timeout=30, **kw)
    msg = str(exc.value)
    assert "out-of-memory killer" in msg and "exit -9" in msg
    assert "frame graph configured" in exc.value.stderr_tail


def test_sigkill_without_oom_evidence_stays_a_plain_media_error(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(media, "_oom_kill_count", lambda: None)
    cmd = _script(tmp_path, """
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    with pytest.raises(media.MediaError) as exc:
        media.run(cmd, timeout=30, progress_cb=lambda p: None,
                  expected_out_s=5.0)
    assert not isinstance(exc.value, media.MediaOOMError)
    assert "exit -9" in str(exc.value)


def test_font_dir_holds_only_fonts_so_libass_has_nothing_to_complain_about():
    """libass loads EVERY file in fontsdir as a font; a licence text there logs
    'Error opening memory font' once per subtitles filter per render. Licences
    live in fonts/licenses/ (OFL terms travel with the fonts)."""
    entries = sorted(os.listdir(FONTS))
    stray = [e for e in entries if not e.lower().endswith((".ttf", ".otf"))
             and e != "licenses" and not e.startswith(".")]
    assert stray == []
    licences = os.listdir(os.path.join(FONTS, "licenses"))
    assert licences and all(n.startswith("LICENSE-") for n in licences)
    # every bundled family keeps its licence text
    families = {e.split("-")[0].replace("InterDisplay", "Inter")
                for e in entries if e.lower().endswith(".ttf")}
    for fam in families:
        assert f"LICENSE-{fam}.txt" in licences, fam
