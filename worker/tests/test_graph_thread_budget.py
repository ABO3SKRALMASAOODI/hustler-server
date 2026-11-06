"""A graph with hundreds of scalers must not run out of threads part-way
through the programme (ticket #77, project 5520: 82 dissolves, 448 scalers on
15 cores, the picture ended 33 s before the sound on every retry)."""

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import renderer                                            # noqa: E402


def _threads(args):
    return (int(args[args.index("-filter_complex_threads") + 1])
            if "-filter_complex_threads" in args else None)


def test_a_normal_graph_is_left_alone(tmp_path):
    args = renderer._graph_args("[0:v]scale=64:64,format=yuv420p[vout]",
                                str(tmp_path))
    assert args[0] == "-filter_complex" and len(args) == 2


@pytest.mark.parametrize("cpus", [4, 15, 64])
def test_a_big_graph_stays_under_the_thread_budget(tmp_path, monkeypatch, cpus):
    monkeypatch.setattr(renderer, "_cpus", lambda: cpus)
    scalers = 942                 # what the 82 dissolves of p5520 count as
    graph = ";".join("[a]scale=64:64,format=yuv420p[b]"
                     for _ in range(scalers // 2))
    threads = _threads(renderer._graph_args(graph, str(tmp_path)))
    assert threads and threads < cpus
    assert threads * scalers <= renderer.GRAPH_THREAD_BUDGET


def test_more_scalers_than_the_budget_still_get_one_thread(tmp_path):
    graph = "scale" * (renderer.GRAPH_THREAD_BUDGET + 1)
    assert _threads(renderer._graph_args(graph, str(tmp_path))) == 1


def test_the_long_graph_file_keeps_the_limit(tmp_path):
    graph = ";".join(["[a]scale=64:64,format=yuv420p[b]"] * 4000)
    args = renderer._graph_args(graph, str(tmp_path))
    assert _threads(args) == 1 and os.path.isfile(args[-1])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_ffmpeg_takes_the_limit_as_written(tmp_path):
    out = str(tmp_path / "o.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
           "color=c=red:s=64x64:r=30:d=0.2",
           "-filter_complex_threads", "2", "-filter_complex",
           "[0:v]scale=32:32,format=yuv420p[vout]", "-map", "[vout]", out]
    subprocess.run(cmd, check=True, timeout=60)
    assert os.path.getsize(out) > 0
