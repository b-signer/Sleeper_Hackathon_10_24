"""Criteria scores (T07, D8): the four 0-100 scores, computed from the metrics.

Every score is built from percentiles within the universe, so it reads as "how
this repo compares with the other repos in this topic". See SCORING (also in
docs/scoring.md) for the rule in words.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import datetime

from sleeper.contracts.universe import CriteriaScores, RepoMetrics

SCORING = (
    "Scores are percentiles within this universe (0 = lowest repo, 100 = highest; ties share the "
    "average rank), and a criterion built from several metrics is the mean of their percentiles, "
    "rounded to 0.1. Higher always means more of the criterion. Popularity: stars. Momentum: "
    "lifetime star velocity (stars_per_day) and forks created in the last 30 days (forks_30d), "
    "equally weighted (D13; star_events_30d is not used because the events feed caps it). "
    "Quality: commits in the last 90 days, distinct commit authors in the last 90 days, number "
    "of releases, recency of the last commit, and README length, equally weighted, so an "
    "active, multi-author, released and documented repo scores high. Newness: creation date, "
    "so the newest repo scores 100. A metric that is unknown (None) for a repo counts as the "
    "median (50) for that repo, neither rewarded nor penalised; the other repos' percentiles "
    "are computed among the known values only."
)

UNKNOWN = 50.0  # percentile given to an unknown (None) metric


def percentiles(values: Sequence[float | None]) -> list[float]:
    """Mid-rank percentile of each value among the known (non-None) values, 0-100.

    The unique lowest value gets 0 and the unique highest 100; tied values share
    the average of their ranks. None gets UNKNOWN. A single known value gets 50.
    """
    known = sorted(v for v in values if v is not None)
    n = len(known)
    out: list[float] = []
    for v in values:
        if v is None or n <= 1:
            out.append(UNKNOWN)
            continue
        below = bisect_left(known, v)
        equal = bisect_right(known, v) - below
        rank = below + (equal - 1) / 2  # 0-based average rank
        out.append(100.0 * rank / (n - 1))
    return out


def _ts(t: datetime | None) -> float | None:
    return t.timestamp() if t is not None else None


def _mean(*cols: list[float]) -> list[float]:
    return [sum(vals) / len(vals) for vals in zip(*cols)]


def _clamp(x: float) -> float:
    return min(100.0, max(0.0, round(x, 1)))


def score_universe(metrics: Sequence[RepoMetrics]) -> list[CriteriaScores]:
    """Scores for each repo, in the same order. Percentiles are within ``metrics``."""
    m = list(metrics)
    if not m:
        return []
    p = lambda f: percentiles([f(x) for x in m])  # noqa: E731

    popularity = p(lambda x: x.stars)
    momentum = _mean(p(lambda x: x.stars_per_day), p(lambda x: x.forks_30d))
    quality = _mean(
        p(lambda x: x.commits_90d),
        p(lambda x: x.authors_90d),
        p(lambda x: x.releases),
        p(lambda x: _ts(x.last_commit_at)),  # later last commit = higher
        p(lambda x: x.readme_chars),
    )
    newness = p(lambda x: x.created_at.timestamp())  # created later = newer = higher
    return [
        CriteriaScores(popularity=_clamp(a), momentum=_clamp(b), quality=_clamp(c), newness=_clamp(d))
        for a, b, c, d in zip(popularity, momentum, quality, newness)
    ]
