"""Offline prefix-cache model. No provider clients, credentials, or network calls.

Caller supplies the rendered token sequence and its eligible boundaries. The
legacy policy implements 1024/128 blocks; modern Responses uses message/explicit
boundaries. Server routing/hidden tokens are unobserved: calibration is mandatory.
"""
import hashlib
from dataclasses import dataclass, field


@dataclass
class Prompt:
    at: float
    model: str
    role: str
    tokens: tuple
    key: str = ''
    ttl: int = 600
    modern: bool = False
    boundaries: dict = field(default_factory=dict)  # token offset -> TTL seconds
    lookup: tuple = ()
    complete: bool = True
    actual: int | None = None
    reported_input: int = 0
    ready_at: float | None = None
    scope: str = 'account'


class Simulator:
    def __init__(self):
        self.entries = {}
        self.seen = set()

    @staticmethod
    def digest(tokens):
        return hashlib.sha256(','.join(map(str, tokens)).encode()).hexdigest()

    def run(self, p):
        route = (p.scope, p.model, '' if p.modern else p.key)
        if p.modern:
            lookups = p.lookup or tuple(p.boundaries)
            writes = p.boundaries
        else:
            lookups = range(1024, len(p.tokens) + 1, 128)
            writes = dict.fromkeys(lookups, p.ttl)
        candidates = []
        for n in lookups:
            if 1024 <= n <= len(p.tokens):
                identity = (*route, n, self.digest(p.tokens[:n]))
                candidates.append((n, identity))
        cached, expired, pending = 0, False, False
        for n, identity in sorted(candidates, reverse=True):
            record = self.entries.get(identity)
            if record:
                ready, until = record
                if ready <= p.at <= until:
                    cached = n; break
                expired |= until < p.at
                pending |= ready > p.at
        written = short = long = 0
        # Each token is charged once at the longest covering write lifetime.
        for n, ttl in sorted(writes.items(), reverse=True):
            if n < 1024 or n > len(p.tokens):
                continue
            identity = (*route, n, self.digest(p.tokens[:n]))
            ready = p.ready_at if p.ready_at is not None else p.at
            if n > cached:
                # Existing cache lifetime does not restart on reads in modern.
                old = self.entries.get(identity)
                if not old or old[1] < p.at:
                    self.entries[identity] = (ready, ready + ttl)
                written = max(written, n - cached)
                if ttl >= 43200:
                    long = max(long, n - cached)
            elif not p.modern:
                self.entries[identity] = (ready, ready + ttl)
        short = written - long
        cause = ('hit' if cached else 'under_1024' if len(p.tokens) < 1024 else
                 'no_eligible_boundary' if not candidates else 'expired' if expired else
                 'concurrent_cold_write' if pending else 'cold_or_prefix_changed')
        return dict(cached=cached, write_30m=short, write_12h=long,
                    cause=cause, complete=p.complete)


def calibration(prompts, results, tolerance=.02):
    known = [(p, r) for p, r in zip(prompts, results) if p.actual is not None]
    total = sum(p.reported_input or len(p.tokens) for p, _ in known)
    actual = sum(p.actual for p, _ in known)
    predicted = sum(r['cached'] for _, r in known)
    absolute_error = sum(abs(p.actual-r['cached']) for p, r in known)
    complete = bool(known) and len(known) == len(prompts) and all(p.complete for p, _ in known)
    return dict(calls=len(prompts), input_tokens=total, actual_cached=actual,
                simulated_cached=predicted, actual_share=actual/total if total else None,
                simulated_share=predicted/total if total else None,
                mean_absolute_error_share=absolute_error/total if total else None,
                complete=complete, passed=complete and total > 0 and absolute_error/total <= tolerance)
