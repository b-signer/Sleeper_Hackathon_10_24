# Sleeper

An agent that finds underrated GitHub repos and rates them on popularity, momentum, quality and newness.

- [PRD](docs/prd.md): what we are building and why (MVP)
- [Decisions](docs/decisions.md): the few locked choices
- [Contracts](docs/contracts.md): the shared data shapes (T02)
- [Scoring](docs/scoring.md): how the four criteria scores are computed (T07). Build a universe with `uv run sleeper build-universe <topic>` (topics in [`topics.toml`](topics.toml))
- [Tickets](docs/tickets/README.md): 17 MVP tickets, schedule and checkpoints ([dependency graph](docs/tickets/dependency-graph.html))
- [Setup](docs/setup.md): keys and secrets. Do this first
- [Findings](docs/findings/2026-10-24-real-data-baseline.md): the first real-data run, what works, what doesn't, and the baseline to beat
- [Archive](docs/archive/): earlier plans. `v0.3-full-plan/` covers the backtest and the full grounding suite (stretch material)

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 itself).

```sh
uv sync                  # install
cp .env.example .env     # then fill in GITHUB_TOKEN and OPENROUTER_API_KEY
uv run sleeper --help    # command-line entry point
```

## Web UI (T14–T16)

```sh
uv run sleeper serve     # http://127.0.0.1:8000
```

Pick a topic and model, start a run, and watch the trace live; the picks appear when it finishes. Click any number or `c_…` citation on a card to see the tool output it came from. Past runs can be reopened or replayed (1×, 4×, 16×). Deep link: `/#run=<run_id>` (add `&tab=trace` to stay on the trace).

- **Scripted fake (offline, free)** runs T10's scripted agent on the fake universe. Real models spend from the OpenRouter budget (the spend guard still applies). With `SLEEPER_OFFLINE=1`, only the scripted model can run.
- The list always includes the recorded fake run and the fake universe, so the demo works with no network. Real universes are read from `SLEEPER_UNIVERSES_DIR` (default `data/universes/`, where `sleeper build-universe` writes them).
- API reference: the top of [`src/sleeper/server/app.py`](src/sleeper/server/app.py).

## Tests

```sh
uv run pytest            # offline tests only (the default)
uv run pytest -m live    # only the tests that hit real APIs (needs a filled-in .env)
```

Tests that hit real APIs are marked `@pytest.mark.live` and are skipped by default (`addopts = "-m 'not live'"` in `pyproject.toml`).
