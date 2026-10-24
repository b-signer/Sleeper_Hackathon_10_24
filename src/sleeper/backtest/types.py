"""S1 backtest: the shared shapes every backtest module codes against.

The backtest is built from swappable parts so incoming changes elsewhere land without edits here:

- **History** (``StarHistory``): where past star counts come from. GitHub no longer lists
  stargazers (decisions.md), so this is an outside source: a file exported from GH Archive
  (BigQuery), or a fixture in tests.
- **Scorer** (``Scorer``): turns metrics into the four criteria. Defaults to
  ``sleeper.scoring.score_universe``, looked up at call time, so T18's new quality score
  applies to past universes automatically.
- **Prompt** (``PromptFn``): the live system prompt. Defaults to
  ``sleeper.agent.prompt.system_prompt``, looked up at call time, so T11's tuned prompt is what
  gets backtested. The backtest only appends an as-of block to it.
- **Run** goes through ``sleeper.agent.runner.execute_run``, the same entry point the CLI and the
  T19 UI use, so the backtest never forks the agent loop.

Backtest records live in this package, not in ``sleeper.contracts``: the MVP contracts are frozen
and the backtest is a stretch goal (D1).
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Literal, Protocol, Sequence, runtime_checkable

from pydantic import AwareDatetime, Field

from sleeper.contracts import CriteriaScores, RepoMetrics, RunSettings, UniverseHeader
from sleeper.contracts.common import Contract, Repo

BACKTEST_SCHEMA_VERSION = 1


@runtime_checkable
class StarHistory(Protocol):
    """Star counts at past dates, from an outside source."""

    name: str  # e.g. "gharchive-bigquery-export:path/to/file.csv"; goes into caveats

    def stars_at(self, repo: str, when: date) -> int | None:
        """Stars of "owner/name" at the end of ``when`` (UTC), or None if unknown. Case-insensitive."""
        ...


Scorer = Callable[[Sequence[RepoMetrics]], list[CriteriaScores]]
PromptFn = Callable[[RunSettings, UniverseHeader], str]


class PastRowNote(Contract):
    """How one row of a past universe was rebuilt."""

    repo: Repo
    stars_now: int = Field(ge=0, description="Stars in the source (current) universe")
    stars_as_of: int | None = Field(None, ge=0, description="Stars at the as-of date from the history; None = unknown")
    estimated: bool = Field(False, description="True if stars_as_of came from a fallback, not the history source")


class PastUniverseReport(Contract):
    """Sidecar written next to a past universe file: what was rebuilt and what could not be."""

    schema_version: Literal[1] = BACKTEST_SCHEMA_VERSION
    topic: str
    as_of: date
    source_universe: str = Field(description="Path of the current universe it was derived from")
    source_built_at: AwareDatetime
    history: str = Field(description="StarHistory.name")
    rows_in: int = Field(ge=0)
    rows_out: int = Field(ge=0)
    dropped: dict[str, int] = Field(
        default_factory=dict, description="Rows dropped by reason, e.g. {'created_after_as_of': 40, 'no_history': 3}"
    )
    refetched_windows: bool = Field(False, description="True if 30/90-day metrics were refetched as of the date")
    rows: list[PastRowNote] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class Outcome(Contract):
    repo: Repo
    stars_as_of: int | None = Field(None, ge=0)
    stars_now: int | None = Field(None, ge=0, description="None = deleted, private or unreachable now")
    growth: float | None = Field(None, description="ln((stars_now+1)/(stars_as_of+1)); None if either is unknown")


class OutcomeSnapshot(Contract):
    """'What happened since the as-of date', captured once per past universe so every run is scored alike."""

    schema_version: Literal[1] = BACKTEST_SCHEMA_VERSION
    topic: str
    as_of: date
    taken_at: AwareDatetime
    source: str = Field(description="Where stars_now came from, e.g. 'github' or 'universe:data/universes/x.json'")
    outcomes: list[Outcome]
    missing: list[Repo] = Field(default_factory=list, description="Repos with no current star count (not zero)")


class GroupScore(Contract):
    """Mean growth of one set of repos (the agent's picks, or a baseline's)."""

    name: str = Field(description="'agent_picks', 'agent_ranking_top', 'universe_average', 'momentum_top', ...")
    repos: list[Repo] = Field(default_factory=list)
    n_scored: int = Field(ge=0, description="Repos in the set with a known growth")
    mean_growth: float | None = None
    random_percentile: float | None = Field(
        None, ge=0, le=100, description="Percentile of mean_growth among seeded random portfolios of the same size"
    )


class BacktestResult(Contract):
    """Written as backtest.json in the run folder, next to result.json, so the UI can show it later."""

    schema_version: Literal[1] = BACKTEST_SCHEMA_VERSION
    run_id: str
    topic: str
    as_of: date
    snapshot_taken_at: AwareDatetime
    groups: list[GroupScore]
    ranking_spearman: float | None = Field(None, description="Spearman rho of agent rank vs growth (ranked repos only)")
    ranking_p_value: float | None = Field(None, ge=0, le=1, description="Permutation p-value for ranking_spearman")
    random_portfolios: int = Field(10_000, gt=0)
    seed: int
    label: Literal["directional", "significant"] = Field(
        description="'significant' only if ranking_p_value < 0.05; otherwise 'directional'"
    )
    caveats: list[str] = Field(default_factory=list)
