# Decisions

The few decisions every ticket depends on. Everything else is the ticket owner's call. To change a decision, edit this file in the same commit and tell the affected owners.

## Locked decisions

| # | Decision | Value | Why |
|---|---|---|---|
| D1 | Scope | **Live mode only.** GitHub repos only. No backtest in the MVP | Time. Product and demo first |
| D2 | Language | Python 3.12, one package manager for the repo (suggested: `uv`) | Fastest for data and LLM glue |
| D3 | LLM access | OpenRouter, through an OpenAI-compatible client | One key, many models, usage and cost reporting |
| D4 | Development model | `openai/gpt-5.6-luna` ($0.20 / $1.20 per M input/output tokens) | Cheap; same family as the demo model |
| D5 | Demo model | `openai/gpt-5.6-terra` ($2 / $12 per M input/output tokens) | Better research quality for the demo |
| D6 | Data source | GitHub REST and GraphQL APIs, with a classic no-scope token. Responses are cached locally | Higher rate limits. Stargazer lists are **not** available (see learnings) |
| D7 | Universe size | About 200 repos per topic, filtered by stars (default 50–2,000) and creation date (default 2025-01-01 or later) | Small enough to build quickly and screen cheaply |
| D8 | Criteria | Popularity, momentum, quality, newness (PRD §4). Code computes the scores from the metrics, for example as a percentile within the universe | Reproducible, and the model never invents scores |
| D13 | Momentum inputs | `stars_per_day` (stars / age) and `forks_30d` drive the momentum score. `star_events_30d` from the events feed is extra evidence, flagged as a lower bound when the feed is capped. There is no "stars gained in the last 30 days" | GitHub no longer lists stargazers (decided 2026-10-24) |
| D9 | Run limits | About 300k tokens, 25 turns and 10 minutes per run (defaults, adjustable) | Bounds the cost per run |
| D10 | Spend | $50 hard limit set on the OpenRouter key. The code refuses runs that would push past about $45 | Hackathon budget |
| D11 | UI | One local web page served by the Python app, updating live during a run. No frontend build step | Simple to run and to replay offline |
| D12 | Agent web access | Never | Breaks the facts-from-tools rule |

**Suggested budget split:** about $10 for development, $10 for demo and tuning runs, and the rest in reserve.

## Verified facts (2026-10-24)

- GitHub stargazer lists are unavailable through the API with any token (REST `404`, GraphQL count 0). Repo metadata, search, forks, commits, releases and the events feed all work with a classic no-scope token.
- Topic search sizes (stars ≥ 40, created 2025-01-01 to 2026-03-31, not forks): Rust + `topic:cli` 335, Rust + `topic:developer-tools` 153, `topic:local-first` 305, "llm evaluation" 205. The universes are a workable size.
- The OpenRouter key has a $50 limit (set, T04). The account holds only $5 of credit, so it needs topping up.

## Learnings log

Append one dated line per finding, naming your ticket. Don't rewrite other people's lines.
- 2026-10-24 (T04): OpenRouter key works, with a $50 key limit and $0 used. **The account has only $5 of credit**, so top it up to $50 before tuning runs.
- 2026-10-24 (T04): `gpt-5.6-luna` and `gpt-5.6-terra` are both available with tool calling. Luna returned 3 tool calls in one turn, so parallel calls work. `usage` includes token counts and exact `cost` (`"usage": {"include": true}`).
- 2026-10-24 (T04): python.org's Python on macOS fails SSL verification until its certificates are installed (see `docs/setup.md`).
- 2026-10-24 (T04): Fine-grained GitHub tokens got `403` on stargazers. This turned out to be the general stargazer block (next line). We standardize on a classic no-scope token.
- 2026-10-24 (T04): **GitHub no longer lists stargazers through the API, for any token.** REST returns `404` and GraphQL returns `totalCount: 0`. Tested with a classic no-scope token and a broad OAuth token, on repos like ripgrep. So "stars gained in the last N days" can't come from stargazers. The earlier fine-grained `403` was the same restriction. Repo metadata, search, forks and events all work.
- 2026-10-24 (T04): Workable sources of recent attention: (1) the repo events feed (`/repos/{o}/{r}/events`), which has `WatchEvent` (star) timestamps but only covers the last 30 days and at most 300 events, so busy repos hit the cap within days and their star counts come out low; (2) forks sorted newest, with exact `created_at`, paged; (3) lifetime star velocity, `stars / age_days`, at no extra cost. Decided as D13.
- 2026-10-24 (T06): `sleeper.metrics.fetch_repo_metrics(gh, "owner/name", as_of=...)` returns `RepoMetricsResult` (`repo`, `repo_id`, `url`, `metrics`, `is_fork`, `is_archived`). **Requests per repo: typically 3** (1 GraphQL query of about 1 point, plus README and one events page over REST), **at most 18** (up to 9 more GraphQL pages for 90-day commit authors, 4 more for 30-day forks, 2 more events pages). So 200 repos cost roughly 400–800 core requests and 200–1,000 GraphQL points, well within one hour's limits. Caps return `None`, not a lower bound: `authors_90d` when a repo has more than 1,000 commits in 90 days, `forks_30d` when it has more than 500 forks in 30 days.
- 2026-10-24 (T06): **GraphQL `forks` leaves out forks owned by spam or suspended accounts. REST `/forks` and the `forkCount` total include them.** On ripgrep, only 44 of the 99 newest REST forks appear in GraphQL; the rest belong to accounts that return `404`. `forks_30d` uses GraphQL, which gives a cleaner momentum signal, so it can come out below a count of the REST list. `forks` (the total) still matches the UI.
- 2026-10-24 (T06): The events feed fills up fast. Its 300 events covered only about 8 hours on astral-sh/uv and well under 30 days on hippo-memory, which has 820 commits in 90 days. Busy repos report `star_events_capped: true` with a low `star_events_30d`, so treat the number as a lower bound (D13). The REST responses are cached with no expiry (T05), so set `as_of` to the build time. Windows count only items up to `as_of`.
