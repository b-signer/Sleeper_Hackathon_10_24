# Decisions, facts and known traps

This file holds the **big decisions** that every work package depends on, the external facts we have **verified**, and the **traps** we already know about. Everything not listed here is the ticket owner's call.

**Changing a decision:** edit this file in the same commit as the change, give the reason in the commit message, and tell the owners of affected tickets.

## 1. Locked decisions

| # | Decision | Value | Why |
|---|---|---|---|
| D1 | Language and runtime | Python 3.12. One package manager for the whole repo (suggested: `uv`) | Fastest for data and LLM glue; everyone can run it |
| D2 | LLM access | OpenRouter, through an OpenAI-compatible client | One key, many models, `usage` and cost reporting |
| D3 | Agent model (backtest **and** live) | `openai/gpt-5.6-terra` ($2 / $12 per M input/output tokens; cutoff 2026-02-16) | The live demo should use the configuration the backtest validated |
| D4 | Cheap model (development, bear pass, claim judge, probes, fallback agent) | `openai/gpt-5.6-luna` ($0.20 / $1.20; same cutoff) | Same family, so prompts transfer; 10× cheaper; cutoff before T |
| D5 | Backtest date T | `2026-04-01T00:00:00Z` | 6+ weeks after the model cutoff; about 206 days of outcomes by 2026-10-24 |
| D6 | Star range at T | 50–2,000 | Keeps as-of-T star reconstruction cheap |
| D7 | Created window | On or after 2025-01-01 and before T | Repos are 0–15 months old at T |
| D8 | Topics | `rust-devtools`, `local-first`, `llm-evals` (cut `llm-evals` first) | Each has 150–450 candidate repos (measured, §2) |
| D9 | Universe cap | About 400 repos per topic | Fits one GitHub token's hourly limit for a backtest build |
| D10 | Per-run token budget | About 800k tokens (`prompt + completion`, summed over agent calls) | Every turn re-sends the context, so 400k is too small to research 20 repos |
| D11 | Total LLM spend | $50 hard limit, set on the OpenRouter key; the code refuses runs past about $45 | Hackathon budget |
| D12 | GitHub data source | GitHub REST and GraphQL APIs only | The alternative was verified to be broken (§2) |
| D13 | Storage | Local files plus a local response cache. No database server | Runs on a laptop |
| D14 | UI | A local web page served by the Python app. It updates live while a run is in progress | One process; replay works offline |
| D16 | Run limits (defaults; adjustable in run settings) | At most 40 turns, 20 minutes of wall-clock time, and 2 retries for an invalid submission | Bound the worst case; v0.2 sizing |
| D15 | Agent web access | Never | Leaks the future in backtests and breaks grounding in both modes |

**Suggested budget split:**

| Use | Allocation |
|---|---|
| Development | $10 |
| Backtest runs | $15 |
| Live demo | $8 |
| Bear, judge, probes and naive baseline | $3 |
| Reserve | $14 |

## 2. Verified facts (2026-10-24)

| Fact | Consequence |
|---|---|
| The GitHub stargazers endpoint returns `401` without a token | A GitHub token is mandatory, even for development. One per teammate |
| Search sizes (current stars ≥ 40, created 2025-01-01..2026-03-31, not forks): Rust + `topic:cli` 335, Rust + `topic:developer-tools` 153, `topic:local-first` 305, "llm evaluation" in name/description/topics 205, `topic:llm-evaluation` 43 | The universes are a workable size. If you slice by creation month, no slice hits the 1,000-result search cap |
| The ClickHouse public `github_events` dataset is incomplete (`astral-sh/uv`: 16,917 star events against 91,117 real stars) and ends 2026-07-02 | Rejected (D12) |
| OpenRouter's model list has a `knowledge_cutoff` field. Tool-calling models with cutoffs before T: `gpt-5.6-luna`/`-terra`/`-sol` (2026-02-16), `gpt-5.5` (2025-12-01), `gpt-5.4-mini`/`-nano` (2025-08-31) | The fallback models if D3/D4 fail. Many newer models list no cutoff and are not eligible for backtests |
| The OpenRouter key in use has **no spend limit** | Set one before any agent run (T0.5) |

## 3. Assumptions still to verify (T0.4 owns A1–A4, A6 and A7; T6.4 owns A5)

| ID | Assumption | If it fails |
|---|---|---|
| A1 | Stargazers with `starred_at` come oldest first and allow jumping to page N | Use GraphQL stargazers ordered by date with cursor paging (slower) |
| A2 | GraphQL commit-history counts over a time window match REST | Count with REST |
| A3 | GraphQL issue and PR search counts work and avoid REST search's limit of 30 requests per minute | Use REST search behind a rate limiter |
| A4 | The README can be fetched as of a given commit | Fetch `README.md` contents at that commit |
| A5 | The agent and bear models know nothing after T | Pick another model from §2 |
| A6 | Parallel tool calls work through OpenRouter for D3/D4 | Allow one call per turn and allow more turns |
| A7 | Responses include token `usage` (and ideally cost) | Estimate tokens and mark cost as estimated |

Record the result of each check here when it runs.

## 4. Known traps

These traps were found while drafting v0.2. Each one would leak the future or break a run. They are constraints, not implementation instructions.

| Trap | Why it matters |
|---|---|
| Putting an **upper** star bound, or a sort by current popularity, into backtest universe searches | Drops repos that grew after T, which are exactly the winners. A **lower** bound on current stars is safe, because stars at T counted from current stargazers can't exceed the current count |
| Filtering out **archived** repos in backtests | Some were archived after T |
| Showing the **current** repo description or topics in backtests | They may describe the future. Use README text as of T instead |
| Showing contributors' **current** followers, organizations or repo counts in backtests | These are current values, not values at T |
| Stars at T counted from current stargazers | Slightly low, because people who removed their star are invisible. Outcomes must use the same basis |
| A future-data check that rejects a timestamp **equal to** T | Run metadata legitimately contains `as_of = T`. Data timestamps must be strictly before T; metadata is exempt |
| Commit dates can be rewritten or backdated | A known caveat; report it, don't try to fix it |
| Repo name, owner, language and license are current values | Rarely wrong; list as a caveat |
| No API for dependents | Dependents are out of scope |
| Paper author h-index is a current value | Omit it in backtest mode |

## 5. Learnings log

If you learn something other tickets need (a failed assumption, a new trap, a changed number), **append** one dated line here, naming your ticket. Never rewrite other people's lines, so parallel edits don't conflict.

- 2026-10-24 (v0.3 drafting): the future-data guard must exempt metadata equal to T (see §4).
