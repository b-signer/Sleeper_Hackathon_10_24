"""S1 baselines: simple rules the agent's picks have to beat.

A baseline picks repos from the past universe using only what that universe knows as of its date.
They live in a registry so a new one is a ``register(...)`` call, with no change to the evaluator.
Baselines read the universe's columns (``scores.momentum``, ``metrics.stars``), not how they are
computed, so a new scoring formula (T18) changes what they pick but not this code.

Random portfolios are not a baseline here: they are a distribution, computed in ``stats``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sleeper.contracts import Universe, UniverseRow


class Baseline(Protocol):
    name: str

    def select(self, past: Universe, n: int, seed: int) -> list[str]:
        """Up to ``n`` repo names ("owner/name") from ``past``. ``seed`` is for baselines that sample."""
        ...


BASELINES: dict[str, Baseline] = {}


def register(b: Baseline) -> Baseline:
    """Add (or replace) a baseline under its name. Returns it, so it can be used as a decorator result."""
    BASELINES[b.name] = b
    return b


@dataclass(frozen=True)
class TopBy:
    """The top ``n`` rows by a sort key (rows sorted ascending by ``key``, so negate for "highest first")."""

    name: str
    key: Callable[[UniverseRow], tuple]

    def select(self, past: Universe, n: int, seed: int) -> list[str]:
        return [r.repo for r in sorted(past.rows, key=self.key)[: max(0, n)]]


@dataclass(frozen=True)
class UniverseAverage:
    """Every repo in the universe; ``n`` is ignored."""

    name: str = "universe_average"

    def select(self, past: Universe, n: int, seed: int) -> list[str]:
        return [r.repo for r in past.rows]


def _neg(x: float | None) -> float:
    return float("inf") if x is None else -x  # unknown sorts last


register(UniverseAverage())
# "Top recent stars" (S1): the universe's momentum score, ties by lifetime star velocity, then name.
register(TopBy("momentum_top", lambda r: (-r.scores.momentum, _neg(r.metrics.stars_per_day), r.repo.lower())))
register(TopBy("popularity_top", lambda r: (-r.metrics.stars, r.repo.lower())))
