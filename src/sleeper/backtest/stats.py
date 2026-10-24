"""S1 statistics: random-portfolio percentiles and a rank correlation with a permutation test.

Standard library only. Everything random takes a seed and uses its own ``random.Random``, so the
same inputs and seed always give the same numbers.
"""

from __future__ import annotations

import math
import random
from typing import Sequence

TIE_TOL = 1e-12  # means computed in different orders can differ in the last bits; treat as ties


def mean(values: Sequence[float]) -> float | None:
    """Arithmetic mean, or None for an empty sequence."""
    return math.fsum(values) / len(values) if values else None


def random_portfolio_percentile(
    group_mean: float, pool_growths: Sequence[float], k: int, n_samples: int = 10_000, seed: int = 7
) -> float | None:
    """Percentile (0-100) of ``group_mean`` among means of random k-subsets of the pool.

    Each sample draws k growths without replacement. Percentile = % of samples with a mean strictly
    below ``group_mean`` plus half the % that tie. None if k is 0 or larger than the pool.
    """
    if k <= 0 or k > len(pool_growths) or n_samples <= 0:
        return None
    rng = random.Random(seed)
    pool = list(pool_growths)
    below = ties = 0
    for _ in range(n_samples):
        m = math.fsum(rng.sample(pool, k)) / k
        if math.isclose(m, group_mean, rel_tol=0, abs_tol=TIE_TOL):
            ties += 1
        elif m < group_mean:
            below += 1
    return 100 * (below + ties / 2) / n_samples


def average_ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks, ties sharing the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = math.fsum(d * d for d in dx)
    syy = math.fsum(d * d for d in dy)
    if sxx == 0 or syy == 0:
        return None
    return math.fsum(a * b for a, b in zip(dx, dy, strict=True)) / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman rho (Pearson on average ranks). None if fewer than 2 pairs or either side is constant."""
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    if len(xs) < 2:
        return None
    return _pearson(average_ranks(xs), average_ranks(ys))


def permutation_p_value(xs: Sequence[float], ys: Sequence[float], n: int = 10_000, seed: int = 7) -> float | None:
    """Two-sided permutation p-value for ``spearman(xs, ys)``.

    Shuffles ys n times; p = (1 + #{|rho_perm| >= |rho_obs|}) / (n + 1), so it is never 0.
    None when rho itself is undefined.
    """
    rho = spearman(xs, ys)
    if rho is None or n <= 0:
        return None
    rx, ry = average_ranks(xs), average_ranks(ys)  # a shuffle of ranks has the same ranks, so rank once
    rng = random.Random(seed)
    target = abs(rho) - TIE_TOL
    hits = 0
    for _ in range(n):
        rng.shuffle(ry)
        r = _pearson(rx, ry)
        if r is not None and abs(r) >= target:
            hits += 1
    return (hits + 1) / (n + 1)
