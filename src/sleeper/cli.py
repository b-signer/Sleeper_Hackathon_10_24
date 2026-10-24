"""Command-line entry point: `sleeper`."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sleeper import __version__
from sleeper.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sleeper",
        description="Find underrated GitHub repos and rate them on popularity, "
        "momentum, quality and newness.",
    )
    parser.add_argument("--version", action="version", version=f"sleeper {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("config", help="show the loaded settings (secrets masked)")
    schema = sub.add_parser("schema", help="write the JSON Schema of every shared contract")
    schema.add_argument("--out", default="schemas", help="output directory (default: schemas)")
    metrics = sub.add_parser("metrics", help="print one repo's T06 metrics as JSON")
    metrics.add_argument("repo", help="owner/name")
    metrics.add_argument("--as-of", help="ISO time for the 30/90-day windows (default: now)")
    build = sub.add_parser("build-universe", help="build a topic's universe file (T07)")
    build.add_argument("topic", help="topic id from topics.toml, e.g. local-first")
    build.add_argument("--out", help="output file (default: data/universes/<topic>.json)")
    build.add_argument("--seed", type=int, default=None, help="sampling seed (default: 7)")
    build.add_argument("--max-size", type=int, default=None, help="maximum rows (default: 200)")
    build.add_argument("--topics", help="topics file (default: topics.toml at the repo root)")
    build.add_argument("--stars-min", type=int, help="override the topic's minimum stars (default 50)")
    build.add_argument("--stars-max", type=int, help="override the topic's maximum stars (default 2000)")
    build.add_argument("--created-after", help="YYYY-MM-DD, override (default 2025-01-01)")
    build.add_argument("--created-before", help="YYYY-MM-DD, optional upper bound on the creation date")
    build.add_argument(
        "--refresh-search", action="store_true", help="refetch search results instead of using the cache"
    )
    return parser


def _cmd_build_universe(args: argparse.Namespace) -> int:
    import dataclasses
    import sys
    from datetime import date

    from sleeper.github import GitHubClient
    from sleeper.universe import (
        DEFAULT_MAX_SIZE,
        DEFAULT_SEED,
        build_universe,
        default_out_path,
        load_topics,
        write_universe,
    )

    topics = load_topics(args.topics)
    if args.topic not in topics:
        print(f"unknown topic {args.topic!r}; known: {', '.join(topics)}", file=sys.stderr)
        return 2
    over = {
        k: v
        for k, v in {
            "stars_min": args.stars_min,
            "stars_max": args.stars_max,
            "created_after": date.fromisoformat(args.created_after) if args.created_after else None,
            "created_before": date.fromisoformat(args.created_before) if args.created_before else None,
        }.items()
        if v is not None
    }
    topic = dataclasses.replace(topics[args.topic], **over)
    seed = DEFAULT_SEED if args.seed is None else args.seed
    max_size = DEFAULT_MAX_SIZE if args.max_size is None else args.max_size
    with GitHubClient() as gh:
        universe = build_universe(gh, topic, seed=seed, max_size=max_size, refresh_search=args.refresh_search)
    path = write_universe(universe, args.out or default_out_path(topic.id))
    print(path)
    return 0


def _cmd_config() -> int:
    s = load_settings()

    def mask(value: str | None) -> str:
        return "set" if value else "missing"

    print(f"GITHUB_TOKEN        {mask(s.github_token)}")
    print(f"OPENROUTER_API_KEY  {mask(s.openrouter_api_key)}")
    print(f"SLEEPER_DEV_MODEL   {s.dev_model}")
    print(f"SLEEPER_DEMO_MODEL  {s.demo_model}")
    print(f"SLEEPER_OFFLINE     {int(s.offline)}")
    print(f"SLEEPER_CACHE_DIR   {s.cache_dir}")
    print(f"SLEEPER_RUNS_DIR    {s.runs_dir}")
    return 0


def _cmd_schema(out: str) -> int:
    import json
    from pathlib import Path

    from sleeper.contracts import json_schemas

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        (out_dir / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
        print(out_dir / f"{name}.schema.json")
    return 0


def _cmd_metrics(repo: str, as_of: str | None) -> int:
    import json
    from datetime import datetime, timezone

    from sleeper.github import GitHubClient
    from sleeper.metrics import fetch_repo_metrics

    when = datetime.fromisoformat(as_of) if as_of else None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    with GitHubClient() as gh:
        r = fetch_repo_metrics(gh, repo, as_of=when)
        out = {
            "repo": r.repo,
            "repo_id": r.repo_id,
            "url": r.url,
            "is_fork": r.is_fork,
            "is_archived": r.is_archived,
            "metrics": r.metrics.model_dump(mode="json"),
            "requests": {"network": gh.stats.network_requests, "cache_hits": gh.stats.cache_hits},
        }
    print(json.dumps(out, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        return _cmd_config()
    if args.command == "schema":
        return _cmd_schema(args.out)
    if args.command == "metrics":
        return _cmd_metrics(args.repo, args.as_of)
    if args.command == "build-universe":
        return _cmd_build_universe(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
