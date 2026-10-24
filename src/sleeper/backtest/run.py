"""S1 end to end: prepare a past universe, run the agent on it, score the run.

Three steps, each its own command, so a slow or paid step never has to be repeated:

1. ``prepare``: current universe + star history -> ``data/universes/backtest/<topic>@<date>.json``
   and its ``.report.json``.
2. ``run_backtest``: the normal agent run (``execute_run``, as the CLI and the UI use) on the past
   universe, with the backtest prompt and READMEs as of the date. Recorded under ``runs/`` like
   any run; ``settings.universe_file`` points at the past universe.
3. ``score``: an outcome snapshot (taken once per past universe, then reused) and the evaluation,
   written as ``backtest.json`` in the run folder.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from sleeper.backtest import outcomes
from sleeper.backtest.evaluate import evaluate, write_backtest
from sleeper.backtest.past_universe import (
    BACKTEST_DIR,
    Refetch,
    as_of_end,
    asof_readme_source,
    build_past_universe,
    write_past_universe,
)
from sleeper.backtest.prompt import backtest_prompt
from sleeper.backtest.types import BacktestResult, PastUniverseReport, PromptFn, Scorer, StarHistory
from sleeper.config import Settings, load_settings
from sleeper.contracts import RunEvent, RunResult, RunSettings, Universe

REPORT_SUFFIX = ".report.json"


def prepare(
    current: Universe,
    as_of: date,
    history: StarHistory,
    *,
    source_path: str = "",
    scorer: Scorer | None = None,
    refetch: Refetch | None = None,
    out_dir: str | Path = BACKTEST_DIR,
) -> tuple[Path, PastUniverseReport]:
    past, report = build_past_universe(
        current, as_of, history, scorer=scorer, refetch=refetch, source_path=source_path
    )
    return write_past_universe(past, report, out_dir), report


def load_past(path: str | Path) -> tuple[Universe, PastUniverseReport | None]:
    """A past universe and its report, if the report sits next to it."""
    p = Path(path)
    past = Universe.model_validate_json(p.read_text(encoding="utf-8"))
    rp = p.with_name(p.stem + REPORT_SUFFIX)
    report = PastUniverseReport.model_validate_json(rp.read_text(encoding="utf-8")) if rp.is_file() else None
    return past, report


def run_backtest(
    settings: RunSettings,
    past_path: str | Path,
    *,
    fake: bool = False,
    cfg: Settings | None = None,
    base_prompt: PromptFn | None = None,
    readme_as_of: bool = True,
    on_start: Callable[[str], None] | None = None,
    echo: Callable[[RunEvent], None] | None = None,
) -> RunResult:
    """One agent run on a past universe. ``base_prompt`` defaults to the live prompt (T11).

    ``readme_as_of`` (real runs): READMEs as they were at the date, so the agent can't read later
    text. Fake runs use the fake READMEs.
    """
    from sleeper.agent.data_tools import DataTools
    from sleeper.agent.runner import execute_run
    from sleeper.agent.toolbox import Toolbox

    past, _ = load_past(past_path)
    as_of = outcomes.as_of_date(past)
    settings = settings.model_copy(update={"topic": past.header.topic, "universe_file": str(past_path)})
    readme_source = asof_readme_source(as_of_end(as_of)) if readme_as_of and not fake else None
    tools = Toolbox(past, DataTools(past, readme_source=readme_source), max_picks=settings.max_picks)
    return execute_run(
        settings,
        fake=fake,
        cfg=cfg,
        tools=tools,
        universe=past,
        system_prompt=backtest_prompt(settings, past.header, as_of, base=base_prompt),
        on_start=on_start,
        echo=echo,
    )


def score(
    run_id: str,
    *,
    cfg: Settings | None = None,
    current: Universe | None = None,
    current_source: str = "",
    github: bool = False,
    refresh_snapshot: bool = False,
    baselines: list[str] | None = None,
    seed: int = 7,
) -> tuple[BacktestResult, Path]:
    """Score a finished backtest run; returns the result and the backtest.json path.

    The snapshot next to the past universe is reused unless ``refresh_snapshot``. Taking a new one
    needs ``current`` (a newer universe of the same topic, offline) or ``github=True``.
    """
    cfg = cfg or load_settings()
    run_dir = cfg.runs_dir / run_id
    result = RunResult.model_validate_json((run_dir / "result.json").read_text(encoding="utf-8"))
    if not result.settings.universe_file:
        raise ValueError(f"run {run_id} has no universe_file; was it started with `sleeper backtest run`?")
    past_path = Path(result.settings.universe_file)
    past, report = load_past(past_path)

    snap_path = outcomes.snapshot_path(past_path)
    if snap_path.is_file() and not refresh_snapshot:
        snapshot = outcomes.load_snapshot(snap_path)
    elif current is not None:
        snapshot = outcomes.snapshot_from_universe(past, current, source=f"universe:{current_source}")
        outcomes.save_snapshot(snapshot, snap_path)
    elif github:
        from sleeper.github import GitHubClient

        with GitHubClient() as gh:
            snapshot = outcomes.snapshot_from_github(past, gh)
        outcomes.save_snapshot(snapshot, snap_path)
    else:
        raise ValueError(f"no outcome snapshot at {snap_path}; pass a current universe or use GitHub")

    bt = evaluate(
        result, past, snapshot, baselines=baselines, seed=seed, extra_caveats=report.caveats if report else ()
    )
    return bt, write_backtest(bt, run_dir)
