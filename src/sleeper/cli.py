"""Command-line entry point: `sleeper`."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sleeper import __version__
from sleeper.config import load_settings

if TYPE_CHECKING:
    from sleeper.contracts import RunEvent


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
    fakes = sub.add_parser("fakes", help="regenerate the fake universe, tool outputs and recorded run (T03)")
    fakes.add_argument("--out", default="fixtures/fake", help="output directory (default: fixtures/fake)")
    serve = sub.add_parser("serve", help="start the local web UI and API (T14)")
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    run = sub.add_parser("run", help="run the agent once on a topic's universe and record it under runs/ (T10)")
    run.add_argument("--topic", required=True, help="topic id (e.g. 'fake', 'rust-devtools')")
    run.add_argument("--universe", metavar="PATH", help="universe file (default: the topic's)")
    run.add_argument("--model", help="OpenRouter model id (default: SLEEPER_DEV_MODEL)")
    run.add_argument("--max-picks", type=int, metavar="N", help="cards to submit, 1-5 (default 5)")
    run.add_argument("--min-ranked", type=int, metavar="N", help="ranking target (default 10)")
    run.add_argument("--token-budget", type=int, metavar="N", help="token budget (default 300000)")
    run.add_argument("--max-turns", type=int, metavar="N", help="turn limit (default 25)")
    run.add_argument("--max-wall-clock", type=int, metavar="S", help="wall-clock limit in seconds (default 600)")
    run.add_argument("--fake", action="store_true", help="scripted model and the real tools on fake data: offline, free")
    run.add_argument("--quiet", action="store_true", help="don't print the live trace")
    return parser


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
    print(f"SLEEPER_UNIVERSES_DIR {s.universes_dir}")
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


def _cmd_fakes(out: str) -> int:
    from pathlib import Path

    from sleeper.fakes import write_fakes

    for path in write_fakes(Path(out)):
        print(path)
    return 0


def _cmd_serve(host: str, port: int) -> int:
    import uvicorn

    from sleeper.server import app_from_settings

    print(f"Sleeper UI on http://{host}:{port}")
    uvicorn.run(app_from_settings(load_settings()), host=host, port=port)
    return 0


def _short(value: object, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _echo_event(event: RunEvent) -> None:
    """One line per event: the live trace of `sleeper run`."""
    t = event.type
    if t == "run_started":
        h = event.universe
        line = f"run {event.run_id}: model {event.settings.model}, universe '{h.topic}' ({h.counts.rows} repos)"
    elif t == "status":
        line = f"[{event.phase}]" + (f" {event.message}" if event.message else "")
    elif t == "narration":
        line = f"  t{event.turn}: {_short(event.text, 400)}"
    elif t == "reasoning":
        line = f"  t{event.turn} (reasoning): {_short(event.text)}"
    elif t == "tool_call":
        line = f"    -> {event.call_id} {event.tool} {_short(event.args)}"
    elif t == "tool_result":
        out = event.output
        err = getattr(out, "error", None)
        if err is not None:
            line = f"    <- {event.call_id} {err.code}: {_short(err.message)}"
        elif out.tool == "submit":
            verdict = "accepted" if out.ok else f"rejected ({out.attempts_left} left): {_short(out.errors)}"
            line = f"    <- {event.call_id} submit {verdict}"
        else:
            line = f"    <- {event.call_id} ok, ~{event.approx_tokens} tokens"
    elif t == "usage":
        u = event.usage
        line = f"  turn {event.turn}/{event.max_turns}: {u.total_tokens:,}/{event.token_budget:,} tokens, ${u.cost_usd:.4f}"
    elif t == "skeptic_review":
        line = f"  skeptic on {event.repo}: {event.review.verdict}"
    elif t == "fact_check":
        line = f"  fact check on {event.repo}: {len(event.flags)} flag(s)"
    elif t == "run_finished":
        line = f"finished: {event.status}" + (f" ({event.error})" if event.error else "")
    else:
        line = f"[{t}]"
    print(line, flush=True)


def _cmd_run(args: argparse.Namespace) -> int:
    import sys

    from pydantic import ValidationError

    from sleeper.agent.openrouter import WebAccessRefused
    from sleeper.agent.runner import execute_run
    from sleeper.agent.spend import SpendGuardError
    from sleeper.agent.types import ModelError
    from sleeper.contracts import RunSettings

    cfg = load_settings()
    chosen = {
        "universe_file": args.universe,
        "max_picks": args.max_picks,
        "min_ranked": args.min_ranked,
        "token_budget": args.token_budget,
        "max_turns": args.max_turns,
        "max_wall_clock_s": args.max_wall_clock,
    }
    model = "scripted/fake" if args.fake else (args.model or cfg.dev_model)
    try:
        settings = RunSettings(topic=args.topic, model=model, **{k: v for k, v in chosen.items() if v is not None})
    except ValidationError as e:
        print(f"sleeper run: bad settings: {e}", file=sys.stderr)
        return 2
    try:
        result = execute_run(settings, fake=args.fake, cfg=cfg, echo=None if args.quiet else _echo_event)
    except (WebAccessRefused, SpendGuardError, FileNotFoundError, ModelError) as e:
        # ModelError can only escape here as a refusal (missing key) before the run folder exists
        print(f"sleeper run: refused: {e}", file=sys.stderr)
        return 2
    print(f"run folder: {cfg.runs_dir / result.run_id}")
    print(
        f"status: {result.status}"
        + (f" ({result.error})" if result.error else "")
        + f"; {result.turns} turns, {result.usage.total_tokens:,} tokens, ${result.usage.cost_usd:.4f}"
    )
    return 0 if result.status == "ok" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        return _cmd_config()
    if args.command == "schema":
        return _cmd_schema(args.out)
    if args.command == "fakes":
        return _cmd_fakes(args.out)
    if args.command == "serve":
        return _cmd_serve(args.host, args.port)
    if args.command == "run":
        return _cmd_run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
