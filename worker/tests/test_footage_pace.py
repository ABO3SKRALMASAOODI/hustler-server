"""A run's YouTube fetches share one pace (FOOTAGE_PACE_DIR, opt-in): the processes of one workflow run draw from one
file-locked bucket instead of firing their requests at once; a worker with no pace set fetches as before."""
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from footage import fetch  # noqa: E402


def test_no_pace_is_set_for_a_customer_so_nothing_waits(monkeypatch):
    monkeypatch.delenv("FOOTAGE_PACE_DIR", raising=False)
    monkeypatch.setenv("FOOTAGE_PACE_S", "30")
    monkeypatch.setattr(fetch.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("waited")))
    assert fetch.pace() == 0.0


def test_the_run_s_fetches_are_spaced_across_threads_and_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTAGE_PACE_DIR", str(tmp_path / "pace"))
    monkeypatch.setenv("FOOTAGE_PACE_S", "0.25")
    monkeypatch.setenv("FOOTAGE_PACE_BURST", "2")
    starts = []
    lock = threading.Lock()

    def one():
        fetch.pace()
        with lock:
            starts.append(fetch.time.time())
    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    # two go at once (the burst), then one every 0.25 s: 6 requests take about 1 s, not 0 s
    assert starts[-1] - starts[0] >= 0.9
    assert all(b - a >= 0.2 for a, b in zip(starts[1:], starts[2:]))
    # another process of the same run reads the same schedule: it waits behind these
    code = ("import sys,time;sys.path.insert(0,%r);from footage import fetch;print(fetch.pace())"
            % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) > 0.0


def test_every_youtube_rung_of_the_ladder_waits_its_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTAGE_PACE_DIR", str(tmp_path / "pace"))
    monkeypatch.setenv("FOOTAGE_PACE_S", "5")
    monkeypatch.setenv("FOOTAGE_WAYBACK", "0")
    monkeypatch.setattr(fetch, "_rungs", lambda url: [([], "default"), ([], "tv")] if fetch.is_youtube(url) else [([], "direct")])
    waits = []
    monkeypatch.setattr(fetch.time, "sleep", waits.append)
    bot = "ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies"
    monkeypatch.setattr(fetch.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=bot))
    try:
        fetch.run_ladder("https://www.youtube.com/watch?v=abcdefghijk", [])
    except fetch.FetchError:
        pass
    assert len(waits) == 1 and 4.0 < waits[0] <= 5.1                                 # the second rung waited for its slot
    waits.clear()
    try:
        fetch.run_ladder("https://publisher.example/film.mp4", [])                      # not YouTube: not paced
    except fetch.FetchError:
        pass
    assert waits == []
