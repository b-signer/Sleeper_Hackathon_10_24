"""S1 backtest statistics: hand-computed values and seeded determinism."""

from __future__ import annotations

import itertools
import math

import pytest

from sleeper.backtest.stats import (
    average_ranks,
    mean,
    permutation_p_value,
    random_portfolio_percentile,
    spearman,
)


def test_mean():
    assert mean([1.0, 2.0, 6.0]) == 3.0
    assert mean([]) is None


def test_average_ranks_ties():
    assert average_ranks([10, 20, 20, 5]) == [2.0, 3.5, 3.5, 1.0]
    assert average_ranks([1, 1, 1]) == [2.0, 2.0, 2.0]


def test_spearman_hand_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # Ranks y = [1, 3, 2]; d = [0, 1, 1]; rho = 1 - 6*2/(3*8) = 0.5.
    assert spearman([1, 2, 3], [5, 9, 7]) == pytest.approx(0.5)


def test_spearman_with_ties_is_pearson_on_average_ranks():
    # y ranks [1, 2.5, 2.5, 4] vs x ranks [1, 2, 3, 4]: cov 4.5/4, sx^2 5/4, sy^2 4.5/4.
    assert spearman([1, 2, 3, 4], [1, 2, 2, 3]) == pytest.approx(4.5 / math.sqrt(5 * 4.5))


def test_spearman_undefined():
    assert spearman([1], [2]) is None
    assert spearman([1, 2, 3], [7, 7, 7]) is None
    with pytest.raises(ValueError):
        spearman([1, 2], [1])


def test_permutation_p_value_matches_exact_enumeration():
    xs, ys = [1, 2, 3, 4], [1, 3, 2, 4]
    rho = spearman(xs, ys)  # 0.8
    perms = list(itertools.permutations(ys))
    exact = sum(abs(spearman(xs, list(p))) >= abs(rho) - 1e-12 for p in perms) / len(perms)  # 8/24
    p = permutation_p_value(xs, ys, n=20_000, seed=1)
    assert exact == pytest.approx(1 / 3)
    assert p == pytest.approx(exact, abs=0.02)


def test_permutation_p_value_deterministic_and_never_zero():
    xs, ys = list(range(12)), [0, 1, 3, 2, 4, 6, 5, 7, 8, 10, 9, 11]
    a = permutation_p_value(xs, ys, n=2000, seed=3)
    assert a == permutation_p_value(xs, ys, n=2000, seed=3)
    assert 0 < a < 0.01
    assert permutation_p_value([1, 2], [3, 3]) is None


def test_random_percentile_hand_values():
    pool = [0.0, 1.0, 2.0, 3.0]
    # k=4: every sample is the whole pool (mean 1.5), so the group ties all of them.
    assert random_portfolio_percentile(1.5, pool, 4, n_samples=100, seed=0) == 50.0
    # k=1: four equally likely samples; group 2.0 has 2 below, 1 tie -> 62.5% expected.
    assert random_portfolio_percentile(2.0, pool, 1, n_samples=20_000, seed=0) == pytest.approx(62.5, abs=1.5)
    assert random_portfolio_percentile(10.0, pool, 2, n_samples=500, seed=0) == 100.0
    assert random_portfolio_percentile(-1.0, pool, 2, n_samples=500, seed=0) == 0.0


def test_random_percentile_without_replacement():
    # With replacement, k=2 from [0, 1] could give mean 0 or 1; without, it's always 0.5.
    assert random_portfolio_percentile(0.5, [0.0, 1.0], 2, n_samples=200, seed=5) == 50.0


def test_random_percentile_deterministic_and_bounds():
    pool = [0.1 * i for i in range(30)]
    a = random_portfolio_percentile(1.7, pool, 5, n_samples=1000, seed=9)
    assert a == random_portfolio_percentile(1.7, pool, 5, n_samples=1000, seed=9)
    assert random_portfolio_percentile(1.0, pool, 0) is None
    assert random_portfolio_percentile(1.0, pool, 31) is None
