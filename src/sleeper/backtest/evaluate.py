"""S1 evaluation: score one run made on a past universe against its outcome snapshot.

Reads only the contracts (``RunResult``, ``Universe``) and the backtest types, so prompt (T11),
scoring (T18) and UI (T19) changes don't touch it. Baselines come from the registry in
``baselines``; a new one is picked up by name.

Groups, all scored by mean growth ln((now+1)/(as_of+1)) over members with a known growth:
- ``agent_picks``: the card repos.
- ``agent_ranking_top``: the top len(cards) of the agent's ranking.
- one per baseline, picking len(cards) repos (``universe_average`` takes all).
Each group except ``universe_average`` also gets its percentile among seeded random portfolios of
the same scored size, drawn from every past-universe repo with a known growth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sleeper.backtest import baselines as bl
from sleeper.backtest.outcomes import as_of_date
from sleeper.backtest.stats import mean, permutation_p_value, random_portfolio_percentile, spearman
from sleeper.backtest.types import BacktestResult, GroupScore, OutcomeSnapshot
from sleeper.contracts import RepoMetrics, RunResult, Universe

BACKTEST_FILE = "backtest.json"
SIGNIFICANCE = 0.05
NO_PERCENTILE = {"universe_average"}  # the whole pool: its percentile says nothing


def _group(name: str, repos: Sequence[str], growth: dict[str, float], pool: list[float], n_samples: int, seed: int) -> GroupScore:
    scored = [growth[r.lower()] for r in repos if r.lower() in growth]
    m = mean(scored)
    pct = None
    if m is not None and name not in NO_PERCENTILE:
        pct = random_portfolio_percentile(m, pool, len(scored), n_samples=n_samples, seed=seed)
    return GroupScore(name=name, repos=list(repos), n_scored=len(scored), mean_growth=m, random_percentile=pct)


def _unknown_metrics(past: Universe) -> list[str]:
    """Optional metric fields that are unknown (None) in every row of the past universe."""
    optional = [f for f, info in RepoMetrics.model_fields.items() if not info.is_required()]
    return [f for f in optional if past.rows and all(getattr(r.metrics, f) is None for r in past.rows)]


def _caveats(past: Universe, snapshot: OutcomeSnapshot, n_cards: int, n_ranked: int, extra: Sequence[str]) -> list[str]:
    as_of = snapshot.as_of.isoformat()
    unknown = _unknown_metrics(past)
    unscored = sum(1 for o in snapshot.outcomes if o.growth is None)
    out = [
        "The model may know some of these repos from training data, including how they did after "
        f"{as_of}; picks can be informed by that memory, not only by the data shown.",
        f"Only star counts are rebuilt as of {as_of}. Other metrics are unknown or come from a later "
        "build of the universe"
        + (f"; unknown in every row: {', '.join(unknown)}." if unknown else "."),
        f"{len(snapshot.missing)} of {len(snapshot.outcomes)} repos have no current star count (deleted, "
        f"private or unreachable); {unscored} in total have no known growth and are left out, not counted as zero.",
        f"Small sample: {n_cards} picks and {n_ranked} ranked repos with a known growth. "
        "Treat the result as directional unless the ranking test is significant.",
        f"Stars now are from {snapshot.source}, taken {snapshot.taken_at.date().isoformat()}.",
    ]
    return out + list(extra)


def evaluate(
    result: RunResult,
    past: Universe,
    snapshot: OutcomeSnapshot,
    *,
    baselines: Sequence[str] | None = None,
    n_samples: int = 10_000,
    seed: int = 7,
    extra_caveats: Sequence[str] = (),
) -> BacktestResult:
    """Score ``result`` (a run on ``past``) against ``snapshot``. Raises ValueError on mismatched inputs."""
    if result.status != "ok":
        raise ValueError(f"run {result.run_id} has status {result.status!r}; only 'ok' runs can be backtested")
    if not result.cards:
        raise ValueError(f"run {result.run_id} has no cards")
    topic, as_of = past.header.topic, as_of_date(past)
    if snapshot.topic != topic or snapshot.as_of != as_of:
        raise ValueError(
            f"snapshot is for {snapshot.topic!r} as of {snapshot.as_of}, past universe is {topic!r} as of {as_of}"
        )
    if result.settings.topic != topic:
        raise ValueError(f"run {result.run_id} is on topic {result.settings.topic!r}, past universe is {topic!r}")
    names = list(bl.BASELINES) if baselines is None else list(baselines)
    unknown = [b for b in names if b not in bl.BASELINES]
    if unknown:
        raise ValueError(f"unknown baselines {unknown}; registered: {sorted(bl.BASELINES)}")

    growth = {o.repo.lower(): o.growth for o in snapshot.outcomes if o.growth is not None}
    pool = [growth[r.repo.lower()] for r in past.rows if r.repo.lower() in growth]
    n = len(result.cards)
    ranking = sorted(result.ranking, key=lambda e: e.rank)

    groups = [
        _group("agent_picks", [c.repo for c in result.cards], growth, pool, n_samples, seed),
        _group("agent_ranking_top", [e.repo for e in ranking[:n]], growth, pool, n_samples, seed),
    ]
    groups += [_group(b, bl.BASELINES[b].select(past, n, seed), growth, pool, n_samples, seed) for b in names]

    # Rank vs growth: x = -rank so a positive rho means "ranked higher, grew more".
    ranked = [(-e.rank, growth[e.repo.lower()]) for e in ranking if e.repo.lower() in growth]
    xs, ys = [x for x, _ in ranked], [y for _, y in ranked]
    rho = spearman(xs, ys) if ranked else None
    p = permutation_p_value(xs, ys, n=n_samples, seed=seed) if rho is not None else None

    return BacktestResult(
        run_id=result.run_id,
        topic=topic,
        as_of=as_of,
        snapshot_taken_at=snapshot.taken_at,
        groups=groups,
        ranking_spearman=rho,
        ranking_p_value=p,
        random_portfolios=n_samples,
        seed=seed,
        label="significant" if p is not None and p < SIGNIFICANCE else "directional",
        caveats=_caveats(past, snapshot, n, len(ranked), extra_caveats),
    )


def write_backtest(bt: BacktestResult, run_dir: str | Path) -> Path:
    """Write ``backtest.json`` in the run folder, next to result.json."""
    path = Path(run_dir) / BACKTEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(bt.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
