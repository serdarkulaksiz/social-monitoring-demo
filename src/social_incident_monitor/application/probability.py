"""Small probability helpers shared by the detection rules and the topic finder."""

from __future__ import annotations

import math


def poisson_sf(observed: int, expected: float) -> float:
    """P(X >= observed) for X ~ Poisson(expected), accurate for very small probabilities."""
    if observed <= 0:
        return 1.0
    if expected <= 0:
        return 0.0
    log_expected = math.log(expected)

    def log_pmf(count: int) -> float:
        return count * log_expected - expected - math.lgamma(count + 1)

    if observed <= expected:
        lower = sum(math.exp(log_pmf(count)) for count in range(observed))
        return min(1.0, max(0.0, 1.0 - lower))
    # Upper tail summed directly: terms shrink quickly once count > expected.
    term = math.exp(log_pmf(observed))
    total = 0.0
    count = observed
    while term > 0.0 and term > total * 1e-16 and count < observed + 10_000:
        total += term
        count += 1
        term *= expected / count
    return min(1.0, total)
