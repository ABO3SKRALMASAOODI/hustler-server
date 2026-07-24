"""Round 37 — full-frame CORRUPT / glitch screens (add_corrupt_screen).

A user building a promo wanted the podcast/meme to "break" into a corrupt
screen and then cut to the next section. There was no primitive for it — the
agent could only fake it with generate_image (broken for weeks) or a colour
card, neither of which looks like signal corruption. add_corrupt_screen
synthesizes the glitch clip locally with ffmpeg lavfi (no generation API, no
source footage, no credits) and splices it in like a title card.

Pinned here:
  1. _corrupt_filtergraph builds a non-empty graph for every style, both
     orientations, sound on/off, and the audio branch appears iff sound.
  2. The graph actually RENDERS through ffmpeg to a valid video (+audio when
     asked) — this is where a comma-escaping slip in a geq/mod/pow expression
     would surface, since the worker passes the graph as one subprocess argv.
  3. add_corrupt_screen validates style (with the common aliases) and its
     numeric args before it ever touches ffmpeg / storage / the db.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools as t  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FFPROBE = shutil.which("ffprobe") is not None

CASES = [
    ("digital", 1280, 720),
    ("vhs", 1280, 720),
    ("static", 1280, 720),
    ("digital", 1080, 1920),      # vertical
    ("static", 1080, 1920),
]


def test_registered_everywhere():
    assert "add_corrupt_screen" in t.TOOLS
    assert "add_corrupt_screen" in t.WRITE_TOOLS
    assert t.REQUIRED_ARGS["add_corrupt_screen"] == ["at_output_s"]
    fn, _desc, schema = t.TOOLS["add_corrupt_screen"]
    assert fn is t.add_corrupt_screen
    assert set(schema) == {"at_output_s", "duration_s", "style",
                           "intensity", "sound"}


def test_filtergraph_all_styles_build():
    for style, ow, oh in CASES:
        fg, has_a = t._corrupt_filtergraph(style, ow, oh, 0.5, 0.7, True)
        assert isinstance(fg, str) and fg
        assert "[vout]" in fg
        assert has_a is True and "[aout]" in fg
        # output dims land at the very end of the video chain
        assert f"scale={ow}:{oh}:flags=neighbor" in fg


def test_audio_branch_follows_sound_flag():
    fg_on, a_on = t._corrupt_filtergraph("digital", 1280, 720, 0.5, 0.7, True)
    fg_off, a_off = t._corrupt_filtergraph("digital", 1280, 720, 0.5, 0.7, False)
    assert a_on is True and "anoisesrc" in fg_on and "[aout]" in fg_on
    assert a_off is False and "anoisesrc" not in fg_off and "[aout]" not in fg_off


def test_intensity_is_clamped():
    # out-of-range intensity must not crash the builder or produce a broken graph
    for k in (-5.0, 0.0, 0.5, 1.0, 9.0):
        fg, _ = t._corrupt_filtergraph("digital", 1280, 720, 0.5, k, True)
        assert "[vout]" in fg


def _probe_streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout
    return out


def test_filtergraph_renders_through_ffmpeg():
    if not (HAVE_FFMPEG and HAVE_FFPROBE):
        return  # environment without ffmpeg (skip, don't fail)
    d = tempfile.mkdtemp()
    for style, ow, oh in CASES:
        sound = style != "static" or ow == 1280   # exercise both branches
        fg, has_a = t._corrupt_filtergraph(style, ow, oh, 0.3, 0.7, sound)
        out = os.path.join(d, f"{style}_{ow}x{oh}.mp4")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-filter_complex", fg, "-map", "[vout]"]
        if has_a:
            cmd += ["-map", "[aout]", "-c:a", "aac"]
        cmd += ["-t", "0.3", "-r", str(t.CORRUPT_FPS), "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                "-shortest", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, f"{style} {ow}x{oh} failed: {r.stderr[-400:]}"
        assert os.path.getsize(out) > 0
        streams = _probe_streams(out)
        assert f"video,{ow},{oh}" in streams, f"{style}: {streams!r}"
        if has_a:
            assert "audio" in streams, f"{style}: audio missing: {streams!r}"


def test_rejects_unknown_style():
    # style is checked first, before any ctx use — ctx=None is safe here
    out = t.add_corrupt_screen(None, at_output_s=1.0, style="wat")
    assert out.startswith("REJECTED") and "style" in out


def test_style_aliases_accepted():
    # 'glitch'/'snow' normalize to digital/static, so validation passes the
    # style gate and fails LATER on a bad numeric arg (proving the alias took).
    out = t.add_corrupt_screen(None, at_output_s="notanumber", style="glitch")
    assert out.startswith("REJECTED") and "at_output_s" in out
    out = t.add_corrupt_screen(None, at_output_s="notanumber", style="snow")
    assert out.startswith("REJECTED") and "at_output_s" in out


def test_rejects_bad_numeric_args():
    assert t.add_corrupt_screen(None, at_output_s="x",
                                style="digital").startswith("REJECTED")
    assert t.add_corrupt_screen(None, at_output_s=1.0, duration_s="x",
                                style="digital").startswith("REJECTED")
    assert t.add_corrupt_screen(None, at_output_s=1.0, duration_s=0.5,
                                style="digital",
                                intensity="x").startswith("REJECTED")
