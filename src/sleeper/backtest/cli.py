"""`sleeper backtest ...` (S1). Kept out of sleeper/cli.py, which only registers it."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


def add_parser(sub: argparse._SubParsersAction) -> None:
    bt = sub.add_parser("backtest", help="S1 rough backtest: picks made as of a past date vs what happened since")
    s = bt.add_subparsers(dest="backtest_command", metavar="<step>")

    sql = s.add_parser("sql", help="print the GH Archive BigQuery SQL that exports star events since the date")
    sql.add_argument("--universe", required=True, metavar="PATH", help="current universe file")
    sql.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    sql.add_argument("--until", help="YYYY-MM-DD (default: today)")

    prep = s.add_parser("prepare", help="build the past universe from a current one and a star history")
    prep.add_argument("--universe", required=True, metavar="PATH", help="current universe file")
    prep.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    prep.add_argument("--history", required=True, metavar="CSV", help="star history file")
    prep.add_argument(
        "--history-kind",
        choices=["events", "stars"],
        default="events",
        help="events: repo,watch_events_since (from `backtest sql`); stars: repo,date,stars (default: events)",
    )
    prep.add_argument("--refetch", action="store_true", help="refetch 30/90-day activity as of the date (GitHub)")
    prep.add_argument("--out", metavar="DIR", help="output directory (default: data/universes/backtest)")

    run = s.add_parser("run", help="run the agent on a past universe (recorded under runs/)")
    run.add_argument("--past", required=True, metavar="PATH", help="past universe from `backtest prepare`")
    run.add_argument("--model", help="OpenRouter model id (default: SLEEPER_DEV_MODEL)")
    run.add_argument("--max-picks", type=int, metavar="N")
    run.add_argument("--token-budget", type=int, metavar="N")
    run.add_argument("--fake", action="store_true", help="scripted model: offline, free")
    run.add_argument("--current-readmes", action="store_true", help="read today's READMEs instead of as of the date")
    run.add_argument("--quiet", action="store_true", help="don't print the live trace")

    sc = s.add_parser("score", help="score a backtest run; writes backtest.json in its run folder")
    sc.add_argument("run_id")
    src = sc.add_mutually_exclusive_group()
    src.add_argument("--current", metavar="PATH", help="take the outcome snapshot from a newer universe (offline)")
    src.add_argument("--github", action="store_true", help="take the outcome snapshot from GitHub")
    sc.add_argument("--refresh-snapshot", action="store_true", help="retake the snapshot even if one exists")
    sc.add_argument("--seed", type=int, default=7)


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _universe(path: str):
    from sleeper.contracts import Universe

    return Universe.model_validate_json(Path(path).read_text(encoding="utf-8"))


def main(args: argparse.Namespace) -> int:
    try:
        cmd = args.backtest_command
        if cmd == "sql":
            return _sql(args)
        if cmd == "prepare":
            return _prepare(args)
        if cmd == "run":
            return _run(args)
        if cmd == "score":
            return _score(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"sleeper backtest {cmd}: {e}", file=sys.stderr)
        return 2
    print("usage: sleeper backtest {sql,prepare,run,score} ...  (see --help)", file=sys.stderr)
    return 2


def _sql(args: argparse.Namespace) -> int:
    from sleeper.backtest.history import gharchive_bigquery_sql

    u = _universe(args.universe)
    print(gharchive_bigquery_sql([r.repo for r in u.rows], _date(args.as_of), _date(args.until) if args.until else None))
    return 0


def _prepare(args: argparse.Namespace) -> int:
    from sleeper.backtest.history import CsvStarHistory, EventCountHistory
    from sleeper.backtest.past_universe import BACKTEST_DIR, make_refetcher
    from sleeper.backtest.run import prepare

    current, as_of = _universe(args.universe), _date(args.as_of)
    if args.history_kind == "events":
        history = EventCountHistory.from_universe(args.history, as_of, current)
    else:
        history = CsvStarHistory(args.history)

    def go(refetch=None):
        return prepare(
            current, as_of, history, source_path=args.universe, refetch=refetch, out_dir=args.out or BACKTEST_DIR
        )

    if args.refetch:
        from sleeper.github import GitHubClient

        with GitHubClient() as gh:
            path, report = go(make_refetcher(gh))
    else:
        path, report = go()
    print(f"past universe: {path} ({report.rows_out} of {report.rows_in} rows; dropped {report.dropped or 'none'})")
    for c in report.caveats:
        print(f"  caveat: {c}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from sleeper.agent.openrouter import WebAccessRefused
    from sleeper.agent.spend import SpendGuardError
    from sleeper.agent.types import ModelError
    from sleeper.backtest.run import load_past, run_backtest
    from sleeper.cli import _echo_event
    from sleeper.config import load_settings
    from sleeper.contracts import RunSettings

    cfg = load_settings()
    past, _ = load_past(args.past)
    chosen = {"max_picks": args.max_picks, "token_budget": args.token_budget}
    settings = RunSettings(
        topic=past.header.topic,
        model="scripted/fake" if args.fake else (args.model or cfg.dev_model),
        **{k: v for k, v in chosen.items() if v is not None},
    )
    try:
        result = run_backtest(
            settings,
            args.past,
            fake=args.fake,
            cfg=cfg,
            readme_as_of=not args.current_readmes,
            echo=None if args.quiet else _echo_event,
        )
    except (WebAccessRefused, SpendGuardError, ModelError) as e:
        print(f"sleeper backtest run: refused: {e}", file=sys.stderr)
        return 2
    print(f"run folder: {cfg.runs_dir / result.run_id}")
    print(f"status: {result.status}; next: sleeper backtest score {result.run_id} --github")
    return 0 if result.status == "ok" else 1


def _score(args: argparse.Namespace) -> int:
    from sleeper.backtest.run import score

    current = _universe(args.current) if args.current else None
    bt, path = score(
        args.run_id,
        current=current,
        current_source=args.current or "",
        github=args.github,
        refresh_snapshot=args.refresh_snapshot,
        seed=args.seed,
    )
    print(f"backtest: {path}  (as of {bt.as_of}, label: {bt.label})")
    for g in bt.groups:
        mg = "n/a" if g.mean_growth is None else f"{g.mean_growth:+.3f}"
        pct = "" if g.random_percentile is None else f", random pct {g.random_percentile:.0f}"
        print(f"  {g.name:<20} n={g.n_scored:<3} mean ln-growth {mg}{pct}")
    if bt.ranking_spearman is not None:
        print(f"  ranking spearman {bt.ranking_spearman:+.3f} (p={bt.ranking_p_value:.3f})")
    for c in bt.caveats:
        print(f"  caveat: {c}")
    return 0
