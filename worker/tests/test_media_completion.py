import subprocess
import sys
import threading

import pytest

import media


def test_completed_media_wakes_the_watchdog_before_join(monkeypatch):
    """No wall-clock threshold: join must observe the completion event set."""
    events = []
    original_event, original_thread = threading.Event, threading.Thread

    class TrackedEvent:
        def __init__(self):
            self.event = original_event()
            events.append(self)
        def is_set(self): return self.event.is_set()
        def set(self): return self.event.set()
        def wait(self, timeout=None): return self.event.wait(timeout)

    class TrackedThread(original_thread):
        def join(self, *args, **kwargs):
            assert events[0].is_set(), 'Finished media must release the watchdog wait'
            return super().join(*args, **kwargs)

    monkeypatch.setattr(media.threading, 'Event', TrackedEvent)
    monkeypatch.setattr(media.threading, 'Thread', TrackedThread)
    media.run([sys.executable, '-c', "print('out_time_ms=100000', flush=True)"],
              progress_cb=lambda f: None, expected_out_s=.1)
    assert events[0].is_set()


def test_failed_progress_callback_reaps_media_process(monkeypatch):
    processes = []
    original = subprocess.Popen
    def tracked(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        return proc
    def failed(_fraction):
        raise RuntimeError('progress receiver stopped')
    monkeypatch.setattr(media.subprocess, 'Popen', tracked)
    with pytest.raises(RuntimeError, match='progress receiver stopped'):
        media.run([sys.executable, '-c',
            "import time; print('out_time_ms=100000', flush=True); time.sleep(30)"],
            progress_cb=failed, expected_out_s=30)
    assert processes[0].poll() is not None
