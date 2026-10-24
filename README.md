# Sleeper

An agent that finds underrated GitHub repos and rates them on popularity, momentum, quality and newness.

- [PRD](docs/prd.md): what we are building and why (MVP)
- [Decisions](docs/decisions.md): the few locked choices
- [Contracts](docs/contracts.md): the shared data shapes (T02)
- [Tickets](docs/tickets/README.md): 17 MVP tickets, schedule and checkpoints ([dependency graph](docs/tickets/dependency-graph.html))
- [Setup](docs/setup.md): keys and secrets. Do this first
- [Archive](docs/archive/): earlier plans. `v0.3-full-plan/` covers the backtest and the full grounding suite (stretch material)

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 itself).

```sh
uv sync                  # install
cp .env.example .env     # then fill in GITHUB_TOKEN and OPENROUTER_API_KEY
uv run sleeper --help    # command-line entry point
```

## Tests

```sh
uv run pytest            # offline tests only (the default)
uv run pytest -m live    # only the tests that hit real APIs (needs a filled-in .env)
```

Tests that hit real APIs are marked `@pytest.mark.live` and are skipped by default (`addopts = "-m 'not live'"` in `pyproject.toml`).
