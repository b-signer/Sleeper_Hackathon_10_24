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
    fakes = sub.add_parser("fakes", help="regenerate the fake universe, tool outputs and recorded run (T03)")
    fakes.add_argument("--out", default="fixtures/fake", help="output directory (default: fixtures/fake)")
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        return _cmd_config()
    if args.command == "schema":
        return _cmd_schema(args.out)
    if args.command == "fakes":
        return _cmd_fakes(args.out)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
