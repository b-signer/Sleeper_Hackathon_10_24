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
| D6 | Data source | GitHub REST and GraphQL APIs, with a token. Responses are cached locally | The stargazers endpoint needs a token (verified) |
| D7 | Universe size | About 200 repos per topic, filtered by stars (default 50–2,000) and creation date (default 2025-01-01 or later) | Small enough to build quickly and screen cheaply |
| D8 | Criteria | Popularity, momentum, quality, newness (PRD §4). Code computes the scores from the metrics, for example as a percentile within the universe | Reproducible, and the model never invents scores |
| D9 | Run limits | About 300k tokens, 25 turns and 10 minutes per run (defaults, adjustable) | Bounds the cost per run |
| D10 | Spend | $50 hard limit set on the OpenRouter key. The code refuses runs that would push past about $45 | Hackathon budget |
| D11 | UI | One local web page served by the Python app, updating live during a run. No frontend build step | Simple to run and to replay offline |
| D12 | Agent web access | Never | Breaks the facts-from-tools rule |

**Suggested budget split:** about $10 for development, $10 for demo and tuning runs, and the rest in reserve.

## Verified facts (2026-10-24)

- The GitHub stargazers endpoint returns `401` without a token, so every teammate needs their own GitHub token.
- Topic search sizes (stars ≥ 40, created 2025-01-01 to 2026-03-31, not forks): Rust + `topic:cli` 335, Rust + `topic:developer-tools` 153, `topic:local-first` 305, "llm evaluation" 205. The universes are a workable size.
- The OpenRouter key has **no spend limit** yet. Set one before any run (T04).

## Learnings log

Append one dated line per finding, naming your ticket. Don't rewrite other people's lines.
