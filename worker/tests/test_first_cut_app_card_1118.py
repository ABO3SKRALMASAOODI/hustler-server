"""A dark ending that still plays the piece's own sound is the piece, not an app's outro card
(18 Nov 2026, p5717: a music video's black director credit under the song's last bars was cut)."""
import shutil
import subprocess

import pytest

import first_cut

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _clip(path, tail_sound):
    # 4 s grey picture with a tone, then 2 s black with the tone continuing (or silence)
    tone = "sine=frequency=330:sample_rate=48000:duration=6"
    vol = "volume=enable='lt(t,4)':volume=1" if not tail_sound else "volume=1"
    if not tail_sound:
        vol = "volume='if(lt(t,4),1,0)':eval=frame"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=4:r=10",
                    "-f", "lavfi", "-i", "color=c=black:s=160x90:d=2:r=10",
                    "-f", "lavfi", "-i", tone,
                    "-filter_complex", f"[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a]{vol}[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)


def test_a_dark_tail_under_the_pieces_sound_is_kept(tmp_path):
    p = tmp_path / "song_tail.mp4"
    _clip(p, tail_sound=True)
    assert first_cut._edge_carries_the_piece(str(p), 4.0, 6.0, "tail", 6.0) is True


def test_a_silent_dark_tail_is_still_an_outro(tmp_path):
    p = tmp_path / "silent_tail.mp4"
    _clip(p, tail_sound=False)
    assert first_cut._edge_carries_the_piece(str(p), 4.0, 6.0, "tail", 6.0) is False
