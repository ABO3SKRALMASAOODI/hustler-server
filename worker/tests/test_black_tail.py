"""A programme that ends on black is measured whole and named with its fix
(studio project 2592, 22 Oct 2026: a 19-minute gaming edit ran on over the
game's black screen; only a vision reviewer noticed, after the turn's last
render, and the picture check read at most the last 1.5 s)."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import render_qc  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
FPS, W, H = 30, 160, 90


def _programme(path, lit_s, black_s):
    """lit_s of moving picture, then black_s of black, a preview's GOP."""
    parts, inputs = [], []
    if lit_s:
        inputs += ["-f", "lavfi", "-i",
                   f"testsrc2=s={W}x{H}:r={FPS}:d={lit_s}"]
        parts.append(f"[{len(parts)}:v]")
    if black_s:
        inputs += ["-f", "lavfi", "-i",
                   f"color=c=black:s={W}x{H}:r={FPS}:d={black_s}"]
        parts.append(f"[{len(parts)}:v]")
    graph = "".join(parts) + f"concat=n={len(parts)}:v=1:a=0,format=yuv420p[v]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs,
                    "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "48", "-keyint_min", "24", path],
                   check=True, capture_output=True)


def _plan(program_s, endcard=None):
    return {"program_s": float(program_s), "fps": FPS, "W": W, "H": H,
            "endcard": endcard, "tail": {},
            "out_to_src": lambda t: 6100.0 + t}


@needs_ffmpeg
def test_a_long_black_ending_is_traced_to_its_first_black_frame(tmp_path):
    path = str(tmp_path / "p.mp4")
    _programme(path, 10, 6)
    p = _plan(16.0)
    rows = render_qc.boundary_stall(path, p, 1e12)
    # the old check: only its 1.5 s window, as a stall before a card that
    # this programme does not have
    assert [r[0] for r in rows] == ["black"]
    assert rows[0][2] - rows[0][1] == pytest.approx(1.5, abs=.05)
    tail = render_qc.black_tail(path, p, 1e12, stall_rows=rows)
    t0, t1, exact = tail
    assert exact is True
    assert t0 == pytest.approx(10.0, abs=1.5 / FPS)
    assert t1 == pytest.approx(16.0)
    text = render_qc.black_tail_finding(tail, p)
    assert text.startswith("BLACK TAIL 10.0")
    assert "6.00s of black picture" in text or "5.97s" in text \
        or "6.03s" in text
    assert "cut_output_range(start=10.0" in text and "end=16.00" in text
    assert "source 6110.0" in text
    assert "end card" not in text
    # findings: one BLACK TAIL line, not also the 1.5 s stall row
    res = {"stall": rows, "black_tail": tail, "jumps": [], "clipped": []}
    out = render_qc.findings(res, p)
    assert sum(f.startswith("BLACK TAIL") for f in out) == 1
    assert not any(f.startswith("STALL BEFORE END CARD") for f in out)


@needs_ffmpeg
def test_a_short_black_ending_needs_no_trace(tmp_path):
    path = str(tmp_path / "p.mp4")
    _programme(path, 8, 0.6)
    p = _plan(8.6)
    rows = render_qc.boundary_stall(path, p, 1e12)
    tail = render_qc.black_tail(path, p, 1e12, stall_rows=rows)
    assert tail[2] is True
    assert tail[0] == pytest.approx(8.0, abs=1.5 / FPS)


@needs_ffmpeg
def test_a_live_ending_has_no_black_tail(tmp_path):
    path = str(tmp_path / "p.mp4")
    _programme(path, 6, 0)
    p = _plan(6.0)
    rows = render_qc.boundary_stall(path, p, 1e12)
    assert render_qc.black_tail(path, p, 1e12, stall_rows=rows) is None
    assert render_qc.black_tail_finding(None, p) is None


@needs_ffmpeg
def test_an_all_black_programme_is_black_from_its_start(tmp_path):
    path = str(tmp_path / "p.mp4")
    _programme(path, 0, 5)
    p = _plan(5.0)
    tail = render_qc.black_tail(path, p, 1e12)
    assert tail == [0.0, 5.0, True]


def test_a_trace_longer_than_its_bound_says_at_least(monkeypatch):
    p = _plan(600.0, endcard={"s": 5.0})
    rows = [["black", 598.5, 600.0, "picture", "x"]]
    monkeypatch.setattr(
        render_qc, "_frames_with_times",
        lambda *a, **k: [(480.0 + i * 2, __import__("numpy").zeros(
            (4, 4), "uint8")) for i in range(59)])
    tail = render_qc.black_tail("x.mp4", p, 1e12, stall_rows=rows)
    assert tail == [480.0, 600.0, False]
    text = render_qc.black_tail_finding(tail, p)
    assert "at least 120.00s of black picture before the end card" in text
