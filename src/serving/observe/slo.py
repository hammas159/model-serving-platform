"""Per-version SLO tracking and the auto-rollback decision.

Latency is kept as a bounded window of samples rather than a running mean, because the
mean is the one latency statistic that never matters. p95 and p99 are where users live,
and you cannot recover a percentile from an average.

The rollback rule requires a **minimum sample count** before it can fire. Without it,
one slow request in the first three would roll back a healthy deployment - and a
canary system that cries wolf gets switched off, which is worse than not having one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class SLO:
    max_p95_ms: float = 500.0
    max_error_rate: float = 0.02
    # No verdict is reached below this many requests.
    min_samples: int = 50


@dataclass
class VersionStats:
    latencies: deque = field(default_factory=lambda: deque(maxlen=5_000))
    requests: int = 0
    errors: int = 0

    def record(self, latency_ms: float, *, error: bool = False) -> None:
        self.requests += 1
        if error:
            self.errors += 1
        else:
            self.latencies.append(latency_ms)

    def percentile(self, p: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        # Nearest-rank: unambiguous, and correct for small samples where
        # interpolation invents a value no request actually had.
        index = max(0, min(len(ordered) - 1, int(round(p / 100 * len(ordered))) - 1))
        return round(ordered[index], 3)

    @property
    def error_rate(self) -> float:
        return round(self.errors / self.requests, 4) if self.requests else 0.0

    def summary(self) -> dict:
        return {
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "p50_ms": self.percentile(50),
            "p95_ms": self.percentile(95),
            "p99_ms": self.percentile(99),
        }


@dataclass
class SLOMonitor:
    slo: SLO = field(default_factory=SLO)
    stats: dict[str, VersionStats] = field(default_factory=dict)

    def record(self, key: str, latency_ms: float, *, error: bool = False) -> None:
        self.stats.setdefault(key, VersionStats()).record(latency_ms, error=error)

    def get(self, key: str) -> VersionStats:
        return self.stats.setdefault(key, VersionStats())

    def breached(self, key: str) -> str | None:
        """Return the reason for a breach, or None. Silent below min_samples."""
        s = self.get(key)
        if s.requests < self.slo.min_samples:
            return None
        if s.error_rate > self.slo.max_error_rate:
            return f"error rate {s.error_rate:.2%} exceeds {self.slo.max_error_rate:.2%}"
        p95 = s.percentile(95)
        if p95 > self.slo.max_p95_ms:
            return f"p95 {p95:.0f}ms exceeds {self.slo.max_p95_ms:.0f}ms"
        return None

    def compare(self, champion_key: str, challenger_key: str) -> dict:
        """Side-by-side, which is the only form in which canary numbers mean anything.

        A challenger at 3% errors is fine if the champion is at 4%, and a disaster if
        the champion is at 0.1%. An absolute threshold cannot tell those apart.
        """
        champ, chall = self.get(champion_key), self.get(challenger_key)
        return {
            "champion": champ.summary(),
            "challenger": chall.summary(),
            "error_rate_delta": round(chall.error_rate - champ.error_rate, 4),
            "p95_delta_ms": round(chall.percentile(95) - champ.percentile(95), 3),
        }
