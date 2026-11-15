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
        # Key isolates accounting even on modern models with automatic routing.
        route = (p.scope, p.model, p.key)
        if p.modern:
            lookups = p.lookup or tuple(p.boundaries)
            writes = p.boundaries
        else:
            lookups = range(1024, len(p.tokens) + 1, 128)
            writes = dict.fromkeys(lookups, p.ttl)
        candidates = []
        for n in lookups:
            if 1024 <= n <= len(p.tokens):
                candidates.append((n, (*route, n, self.digest(p.tokens[:n]))))
        matched = long_coverage = 0
        expired = pending = False
        for n, identity in sorted(candidates, reverse=True):
            record = self.entries.get(identity)
            if record:
                ready, short_until, long_until = record
                if ready <= p.at <= max(short_until, long_until):
                    matched = max(matched, n)
                    if long_until >= p.at:
                        long_coverage = max(long_coverage, n)
                expired |= max(short_until, long_until) < p.at
                pending |= ready > p.at
        selected = {n: ttl for n, ttl in writes.items() if 1024 <= n <= len(p.tokens)}
        end = max(selected, default=0)
        long_end = max((n for n, ttl in selected.items() if ttl >= 43200), default=0)
        # A 12h upgrade is a fresh long write, not a discounted short-cache read.
        upgrade = max(0, min(matched, long_end) - long_coverage)
        cached = matched - upgrade
        long_write = max(0, long_end - long_coverage)
        short_write = max(0, end - max(matched, long_end))
        ready = p.ready_at if p.ready_at is not None else p.at
        for n, ttl in selected.items():
            identity = (*route, n, self.digest(p.tokens[:n]))
            old = self.entries.get(identity)
            effective_long = n <= long_end
            if old and old[0] > p.at:
                continue  # concurrent cold writes cannot be read yet
            if not old or max(old[1:]) < p.at:
                old = (ready, -1, -1)
            short_until, long_until = old[1:]
            if effective_long:
                if long_until < p.at:
                    long_until = ready + ttl if ttl >= 43200 else ready + 43200
            else:
                # Reads slide short coverage; they never restart the 12h clock.
                short_until = max(short_until, ready + ttl)
            self.entries[identity] = (old[0], short_until, long_until)
        cause = ('hit' if cached else 'under_1024' if len(p.tokens) < 1024 else
                 'no_eligible_boundary' if not candidates else 'retention_upgrade' if upgrade else
                 'expired' if expired else 'concurrent_cold_write' if pending else 'cold_or_prefix_changed')
        return dict(cached=cached, write_30m=short_write, write_12h=long_write,
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
