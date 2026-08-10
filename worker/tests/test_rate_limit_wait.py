"""Round 91 — a rate-limited agent step WAITS instead of dying.

One agent turn can exceed the provider's whole TPM tier by itself (round
80), so a long turn hitting 429 mid-flight is expected operation — and it
used to kill the turn: minutes of finished edits ended in "I'm being
rate-limited, resend that". llm.rate_limit_wait decides the sleep; the loop
retries. Aug 10 widened the patience: four flat 15s waits (60s total) was
less than one real TPM burst — job 4120 died mid-burst with the burst still
minutes from over. Bounds pinned here: only genuine rate limits wait;
Retry-After is honoured up to 60s and never SHRINKS the wait; at most 6
waits, growing 12s per attempt capped at 45s; never into the turn's last
20s; never during a deploy drain.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm                                                     # noqa: E402


class _RL(Exception):
    status_code = 429
    response = None

    def __str__(self):
        return "Error 429: rate limit reached"


class _Text(Exception):
    def __str__(self):
        return "Too Many Requests, slow down"


class _Other(Exception):
    def __str__(self):
        return "Error 400: invalid request"


class _WithRA(Exception):
    status_code = 429

    class response:                                    # noqa: N801
        headers = {"retry-after": "30"}

    def __str__(self):
        return "too many requests"


def test_only_rate_limits_wait():
    assert llm.rate_limit_wait(_Other(), 1, 600) is None
    assert llm.rate_limit_wait(_RL(), 1, 600) == 12.0
    assert llm.rate_limit_wait(_Text(), 1, 600) == 12.0


def test_waits_grow_with_attempts():
    assert llm.rate_limit_wait(_RL(), 2, 600) == 24.0
    assert llm.rate_limit_wait(_RL(), 3, 600) == 36.0
    assert llm.rate_limit_wait(_RL(), 4, 600) == 45.0, "growth caps at 45s"
    assert llm.rate_limit_wait(_RL(), 6, 600) == 45.0


def test_retry_after_is_honoured_and_capped():
    assert llm.rate_limit_wait(_WithRA(), 1, 600) == 30.0

    class _Huge(_WithRA):
        class response:                                # noqa: N801
            headers = {"retry-after": "600"}
    assert llm.rate_limit_wait(_Huge(), 1, 600) == 60.0

    class _Tiny(_WithRA):
        class response:                                # noqa: N801
            headers = {"retry-after": "1"}
    # A shorter server hint never undercuts the backoff schedule: attempt 3
    # would wait 36s on its own, and a 1s Retry-After mid-burst is exactly
    # how the old schedule kept walking back into the same wall.
    assert llm.rate_limit_wait(_Tiny(), 3, 600) == 36.0


def test_bounds():
    assert llm.rate_limit_wait(_RL(), 7, 600) is None, "wait-count cap"
    assert llm.rate_limit_wait(_RL(), 1, 15) is None, "turn nearly over"
    assert llm.rate_limit_wait(_RL(), 1, 600, shutting_down=True) is None
    # With 22s left the wait shrinks to leave 10s of working budget.
    assert llm.rate_limit_wait(_RL(), 1, 22) == 12.0
