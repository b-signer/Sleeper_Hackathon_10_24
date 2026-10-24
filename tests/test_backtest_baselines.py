"""S1 baselines: built-in selections and the registry."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from sleeper.backtest import baselines as bl
from sleeper.backtest.baselines import BASELINES, register
from sleeper.contracts import Universe
from sleeper.fakes import fake_universe as _fake_universe


def fake_universe() -> Universe:
    # Deep copy: fake rows share RepoMetrics objects with sleeper.fakes, and tests mutate them.
    return _fake_universe().model_copy(deep=True)


@pytest.fixture
def u() -> Universe:
    return fake_universe()


def test_builtins_registered():
    assert {"universe_average", "momentum_top", "popularity_top"} <= set(BASELINES)


def test_universe_average_takes_all(u):
    assert BASELINES["universe_average"].select(u, 3, 7) == [r.repo for r in u.rows]


def test_momentum_top_breaks_ties_by_stars_per_day(u):
    # tidepool and phantom-build both score 62.5 on momentum; phantom-build has the higher stars/day.
    assert BASELINES["momentum_top"].select(u, 4, 7) == [
        "bigco/turbo-lint",
        "acme/quiver",
        "ghostly/phantom-build",
        "lumen-dev/tidepool",
    ]


def test_momentum_top_ties_then_name():
    u = fake_universe()
    for r in u.rows:
        r.scores.momentum, r.metrics.stars_per_day = 50.0, 1.0
    assert BASELINES["momentum_top"].select(u, 2, 7) == ["acme/quiver", "bigco/turbo-lint"]


def test_popularity_top(u):
    assert BASELINES["popularity_top"].select(u, 3, 7) == ["bigco/turbo-lint", "ghostly/phantom-build", "oldmill/fmt-lite"]
    assert len(BASELINES["popularity_top"].select(u, 50, 7)) == len(u.rows)
    assert BASELINES["popularity_top"].select(u, 0, 7) == []


def test_register_new_baseline(u, monkeypatch):
    monkeypatch.setattr(bl, "BASELINES", dict(BASELINES))

    @dataclass
    class Oldest:
        name: str = "oldest"

        def select(self, past: Universe, n: int, seed: int) -> list[str]:
            return [r.repo for r in sorted(past.rows, key=lambda r: -r.metrics.age_days)[:n]]

    register(Oldest())
    assert bl.BASELINES["oldest"].select(u, 1, 7) == ["weekend/todo-term"]
