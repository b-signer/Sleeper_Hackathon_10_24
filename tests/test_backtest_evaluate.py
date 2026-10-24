"""S1 evaluation: groups, ranking test, caveats and refusals, on the recorded fake run."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sleeper.backtest.evaluate import BACKTEST_FILE, evaluate, write_backtest
from sleeper.backtest.types import BacktestResult, Outcome, OutcomeSnapshot
from sleeper.contracts import RankingEntry, RunResult, Universe
from sleeper.fakes import fake_universe

RESULT = Path(__file__).parents[1] / "fixtures/fake/runs/20261024-140000_fake/result.json"
AS_OF = date(2026, 4, 1)
# Hand-picked growths. Cards: quiver, tidepool, phantom-build. Ranking: those, then cargo-sprout, bytewise.
GROWTH: dict[str, float | None] = {
    "acme/quiver": 1.0,
    "lumen-dev/tidepool": 0.5,
    "fernwork/cargo-sprout": 0.8,
    "solo-hacker/bytewise": 0.2,
    "oldmill/fmt-lite": -0.1,
    "bigco/turbo-lint": 0.3,
    "ghostly/phantom-build": None,  # deleted since the as-of date
    "docsfirst/manpage-rs": 0.0,
    "weekend/todo-term": 0.1,
    "mystery/unknown-cli": 0.0,
}


@pytest.fixture
def past() -> Universe:
    u = fake_universe().model_copy(deep=True)
    u.header.built_at = datetime(2026, 4, 1, 23, 59, tzinfo=UTC)
    return u


@pytest.fixture
def result() -> RunResult:
    return RunResult.model_validate_json(RESULT.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot(past) -> OutcomeSnapshot:
    outcomes = [
        Outcome(repo=r.repo, stars_as_of=r.metrics.stars, stars_now=None if GROWTH[r.repo] is None else 1, growth=GROWTH[r.repo])
        for r in past.rows
    ]
    return OutcomeSnapshot(
        topic="fake",
        as_of=AS_OF,
        taken_at=datetime(2026, 10, 24, tzinfo=UTC),
        source="test",
        outcomes=outcomes,
        missing=["ghostly/phantom-build"],
    )


def _groups(bt: BacktestResult) -> dict:
    return {g.name: g for g in bt.groups}


def test_groups(result, past, snapshot):
    bt = evaluate(result, past, snapshot, n_samples=20_000)
    g = _groups(bt)
    assert list(g) == ["agent_picks", "agent_ranking_top", "universe_average", "momentum_top", "popularity_top"]
    picks = g["agent_picks"]
    assert picks.repos == ["acme/quiver", "lumen-dev/tidepool", "ghostly/phantom-build"]
    assert (picks.n_scored, picks.mean_growth) == (2, pytest.approx(0.75))
    # 36 pairs from the 9 known growths: one beats 1.5 (1.0+0.8), one ties (1.0+0.5) -> (34 + 0.5) / 36.
    assert picks.random_percentile == pytest.approx(100 * 34.5 / 36, abs=1.0)
    assert g["agent_ranking_top"].repos == picks.repos
    avg = g["universe_average"]
    assert (avg.n_scored, avg.mean_growth, avg.random_percentile) == (9, pytest.approx(2.8 / 9), None)
    mom = g["momentum_top"]
    assert mom.repos == ["bigco/turbo-lint", "acme/quiver", "ghostly/phantom-build"]
    assert (mom.n_scored, mom.mean_growth) == (2, pytest.approx(0.65))
    pop = g["popularity_top"]
    assert pop.repos == ["bigco/turbo-lint", "ghostly/phantom-build", "oldmill/fmt-lite"]
    assert pop.mean_growth == pytest.approx(0.1)
    assert (bt.run_id, bt.topic, bt.as_of, bt.seed, bt.random_portfolios) == (result.run_id, "fake", AS_OF, 7, 20_000)


def test_ranking_spearman(result, past, snapshot):
    bt = evaluate(result, past, snapshot, n_samples=20_000)
    # Known growth at ranks 1, 2, 4, 5: growths 1.0, 0.5, 0.8, 0.2 -> d^2 sum 2, rho = 1 - 12/60.
    assert bt.ranking_spearman == pytest.approx(0.8)
    assert bt.ranking_p_value == pytest.approx(1 / 3, abs=0.02)  # exact: 8 of 24 orderings
    assert bt.label == "directional"


def test_significant_label(result, past, snapshot):
    ranked = [r.repo for r in past.rows if GROWTH[r.repo] is not None]
    result.ranking = [RankingEntry(rank=i + 1, repo=repo) for i, repo in enumerate(ranked)]
    for o in snapshot.outcomes:
        if o.repo in ranked:
            o.growth = -float(ranked.index(o.repo))  # perfectly ordered
    bt = evaluate(result, past, snapshot, n_samples=2000)
    assert bt.ranking_spearman == pytest.approx(1.0)
    assert bt.ranking_p_value < 0.05 and bt.label == "significant"


def test_deterministic_and_baseline_subset(result, past, snapshot):
    a = evaluate(result, past, snapshot, n_samples=500, seed=3, baselines=["momentum_top"])
    assert a == evaluate(result, past, snapshot, n_samples=500, seed=3, baselines=["momentum_top"])
    assert [g.name for g in a.groups] == ["agent_picks", "agent_ranking_top", "momentum_top"]
    with pytest.raises(ValueError, match="unknown baselines"):
        evaluate(result, past, snapshot, baselines=["nope"])


def test_caveats(result, past, snapshot):
    for r in past.rows:
        r.metrics.forks_30d = None
    bt = evaluate(result, past, snapshot, n_samples=200, extra_caveats=["history: test export"])
    text = "\n".join(bt.caveats)
    assert "memory" in text
    assert "Only star counts are rebuilt as of 2026-04-01" in text and "forks_30d" in text
    assert "1 of 10 repos have no current star count" in text
    assert "Small sample: 3 picks and 4 ranked repos" in text
    assert bt.caveats[-1] == "history: test export"


@pytest.mark.parametrize("status", ["invalid", "budget_exhausted", "error", "timeout"])
def test_refuses_runs_that_did_not_finish(result, past, snapshot, status):
    result.status = status
    with pytest.raises(ValueError, match="status"):
        evaluate(result, past, snapshot)


def test_refuses_run_without_cards(result, past, snapshot):
    result.cards = []
    with pytest.raises(ValueError, match="no cards"):
        evaluate(result, past, snapshot)


def test_refuses_mismatched_snapshot(result, past, snapshot):
    with pytest.raises(ValueError, match="snapshot"):
        evaluate(result, past, snapshot.model_copy(update={"as_of": date(2026, 4, 2)}))
    with pytest.raises(ValueError, match="snapshot"):
        evaluate(result, past, snapshot.model_copy(update={"topic": "other"}))


def test_refuses_run_on_another_topic(result, past, snapshot):
    result.settings.topic = "other"
    with pytest.raises(ValueError, match="topic"):
        evaluate(result, past, snapshot)


def test_write_backtest(tmp_path, result, past, snapshot):
    bt = evaluate(result, past, snapshot, n_samples=200)
    path = write_backtest(bt, tmp_path / result.run_id)
    assert path == tmp_path / result.run_id / BACKTEST_FILE
    back = BacktestResult.model_validate_json(path.read_text(encoding="utf-8"))
    assert back == bt
    assert all(g.mean_growth is None or math.isfinite(g.mean_growth) for g in back.groups)
