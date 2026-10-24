# PRD: Sleeper: An Agent That Finds Underrated GitHub Repos (and the Papers Behind Them)

**Status:** v0.2, build-ready · **Updated:** 2026-10-24 · **Timebox:** 5.5 hours · **LLM budget:** $50 (OpenRouter)

---

## 0. How to use this document (read first if you are a coding agent)

- **Read order:** Sections 1–3 (what and why), 4 (locked decisions), 5 (facts and assumptions), then the section for your track (Section 20 maps tracks to sections and directories).
- **Contracts are frozen.** Section 8 (config), 11 (tool schemas), 14 (card and finish schema), 15 (run artifacts and event log), 17.6 (scorecard) and 18.7 (backtest output) are the interfaces between tracks. Implement them exactly. If one must change, change this document in the same commit and say so in the commit message.
- **Stay in your directory** (Section 7). Shared code lives only in `sleeper/schemas.py` and `sleeper/config.py`.
- **Requirement IDs** (`DATA-3`, `TOOL-2`, …) are stable. Reference them in code comments and commit messages where useful.
- **Definitions of done** are in Section 21. A task is done when its acceptance tests pass.
- **Do not guess facts about external APIs.** Section 5 lists what has been verified and what must still be verified. If an assumption fails, use the listed fallback and note it in this document.

## 1. Summary

Sleeper is a lightly prompted research agent. The user picks a topic and a star range, and the agent works out on its own how to find open-source projects that are **good but not yet widely discovered**. It chooses its own searches and screens, decides which repos to dig into, argues against its own picks, and returns a small set of **pick cards** backed by sourced numbers.

This domain lets us **prove whether it works**: we run the agent as of a past date T and measure what actually happened to its picks between T and today.

**Extension:** the same engine uses arXiv papers, first as signals for repos (a repo implementing a promising new paper), then as a second discovery mode (underrated papers).

**Design principle:** *free rein over strategy, no freedom over facts.* The agent decides how to research. Every repo and every number must come from a tool, and code checks this.

## 2. Why this domain

- **Inefficient discovery:** good projects are mostly found by luck (Hacker News, social media), so a careful agent has a real edge.
- **Fast, objective outcomes:** star and contributor growth are visible within 3–6 months.
- **Point-in-time data:** stargazer, commit, issue and release timestamps are public, so we can reconstruct what was knowable on a past date.
- **Large samples:** each run ranks 20+ repos, enough for a meaningful comparison.
- **Judge familiarity:** hackathon judges are developers and understand the value immediately.

## 3. Goals and non-goals

### Goals (each has a measurable success criterion in Section 24)
- G1: From minimal input, return 3–5 underrated repos with a clear, sourced case for each.
- G2: Make the agent's self-chosen strategy visible in a live trace.
- G3: Guarantee every number on a card comes from a tool call (verified in code).
- G4: Stay within a hard token budget per run.
- G5: **Show whether the agent's picks beat simple baselines in a point-in-time backtest.**
- G6 (extension): Use linked arXiv papers as evidence for repo picks.

### Non-goals
- Predicting viral hype or meme repos.
- Repos outside public GitHub.
- Free-text topics that build a new universe during a demo. Topics come from a predefined file (Section 10.1). Free-text topic support is P2.
- A full paper-discovery product during the hackathon (Tier 2 is a stretch; see Section 22).
- Authentication, multi-user support, deployment. This runs on a laptop.

## 4. Locked decisions

These replace the v0.1 open questions. Change them only by editing this table.

| Decision | Value | Rationale |
|---|---|---|
| Language and runtime | Python 3.12, managed with `uv` | Fastest for data and LLM glue; everyone can run it |
| Libraries | `httpx`, `pydantic` v2, `openai` SDK (pointed at OpenRouter), `fastapi` + `uvicorn`, `pytest` | Minimal and well known |
| UI | One static page: `web/index.html` + vanilla JS + Chart.js from a CDN (pinned version), served by FastAPI. No build step | Live trace over SSE is simple; no frontend toolchain to debug |
| Storage | SQLite HTTP cache (`data/cache.sqlite`, gitignored). JSON files for universes and runs | No database server |
| Backtest date T | `2026-04-01T00:00:00Z` | 6+ weeks after the agent model's documented cutoff (2026-02-16); 206 days of outcome data by 2026-10-24 |
| Scoring date | Date of the scoring run (planned 2026-10-24), stored in `backtest.json` | Outcomes are reproducible from the stored snapshot |
| Agent model (backtest **and** live) | `openai/gpt-5.6-terra` ($2 / $12 per M input/output tokens; cutoff 2026-02-16) | The live demo should use the configuration the backtest validated |
| Development model | `openai/gpt-5.6-luna` ($0.20 / $1.20; same cutoff) | Same family, so prompts transfer; 10× cheaper |
| Bear-pass and claim-judge model | `openai/gpt-5.6-luna` | Cheap. Its cutoff is before T, which the bear pass requires in backtest mode |
| Fallback agent model | `openai/gpt-5.6-luna` | Use it if terra is too slow or too expensive in testing |
| Star range at T | 50–2,000 | Small enough for cheap stargazer paging (≤ 21 pages per repo) |
| Created window | Created on or after 2025-01-01 and before T | Repos 0–15 months old at T |
| Topics | `rust-devtools`, `local-first`, `llm-evals` (Section 10.1) | Each has 150–450 candidate repos (measured) |
| Universe cap | 400 repos per topic, seeded random sample if larger | Fits one GitHub token's hourly limit for precompute |
| Run budget | 800,000 tokens per agent run (Section 12.3) | v0.1's 400k is too small to research 20 repos once context re-sending is counted |

Model facts come from OpenRouter's `/api/v1/models` on 2026-10-24. Confirm the cutoffs on the provider's model card before the first backtest (A5).

## 5. Verified facts and assumptions

### 5.1 Verified on 2026-10-24
| Fact | How it was verified | Consequence |
|---|---|---|
| The stargazers endpoint returns `401 Requires authentication` without a token | `curl` without a token | `GITHUB_TOKEN` is mandatory, even for development |
| GitHub search sizes (current stars ≥ 40, created 2025-01-01..2026-03-31, not forks): Rust + `topic:cli` 335, Rust + `topic:developer-tools` 153, `topic:local-first` 305, "llm evaluation" in name/description/topics 205, `topic:llm-evaluation` 43 | Search API `total_count` | Universes are a workable size; no slice will hit the 1,000-result cap |
| The ClickHouse public `github_events` dataset is **incomplete and stale**: `astral-sh/uv` shows 16,917 star events in total against 91,117 real stars, and the data ends 2026-07-02 | SQL against play.clickhouse.com | **Rejected** as a data source. Use the GitHub API only |
| The OpenRouter model list has a `knowledge_cutoff` field. Tool-calling models with cutoffs before T: `openai/gpt-5.6-luna`/`-terra`/`-sol` (2026-02-16), `openai/gpt-5.5` (2025-12-01), `openai/gpt-5.4-mini`/`-nano` (2025-08-31) | `/api/v1/models` | Supports the model decisions in Section 4. Many newer models list no cutoff and are not eligible for backtests |
| The OpenRouter key in use has **no spend limit** set | `/api/v1/key` | Set a $50 limit before any agent run (Section 19) |

### 5.2 Assumptions to verify in the first 20 minutes (with a token)
Write `scripts/verify_assumptions.py` and run it first. Each check prints PASS or FAIL.

| ID | Assumption | Test | Fallback if it fails |
|---|---|---|---|
| A1 | `GET /repos/{o}/{r}/stargazers` with `Accept: application/vnd.github.star+json` returns `starred_at`, **oldest first**, and accepts `page=N` jumps | On a repo with about 1,500 stars: page 1's first `starred_at` is near `created_at`, and timestamps rise across pages 1, 5 and 15 | GraphQL `stargazers(orderBy:{field:STARRED_AT,direction:ASC})` with cursor paging (no jumps, so slower) |
| A2 | GraphQL `defaultBranchRef.target.history(since:, until:) { totalCount }` matches the REST commits list count for the same window | Compare both on one repo | Count with REST: `commits?since=&until=&per_page=1` and the last page number from the `Link` header |
| A3 | GraphQL `search(type: ISSUE, query: "repo:o/r is:issue created:A..B") { issueCount }` works and is not capped at 30 requests per minute like REST search | Run 40 aliased searches in a few queries | REST search behind a 30-per-minute rate limiter |
| A4 | `GET /repos/{o}/{r}/readme?ref={sha}` returns the README as of that commit | Compare the README at an old SHA with the current one on a repo whose README changed | `GET /repos/{o}/{r}/contents/README.md?ref={sha}` |
| A5 | The agent and bear models really know nothing after T | Provider model card shows a cutoff before T, and the cutoff canary passes (Section 18.3) | Pick another eligible model from 5.1 |
| A6 | Parallel tool calls work through OpenRouter for the chosen models | Two-tool smoke test with `tool_choice: "auto"` that asks for 3 calls at once | Allow one tool call per turn and raise `max_turns` |
| A7 | OpenRouter responses include `usage` (prompt, completion and cached tokens) | Smoke test | Estimate tokens at 4 characters per token; flag the cost as estimated |

## 6. System overview

```
topics.yaml ──► universe builder ──► data/universe/<topic>__<T>.json   (rows + precomputed metrics)
                    │   GitHub REST/GraphQL via the cached client, as-of-T recipes (Section 9)
                    ▼
CLI / FastAPI ──► agent loop ◄──► tools (list_universe, screen, get_repo_stats, read_readme, compute, finish, …)
                    │   every event is appended to runs/<run_id>/events.jsonl
                    ├──► finish validation ──► bear pass ──► result.json
                    ├──► grounding checks ──► grounding.json
                    └──► (backtest) outcome scorer + baselines ──► backtest.json
web/index.html ◄── SSE + JSON ◄── FastAPI (also replays recorded runs)
```

**Modes**
- **Backtest:** `as_of = T`. Every tool returns data as it was at T (Section 9.2). All models that influence picks (agent, bear) must have a cutoff before T.
- **Live:** `as_of = null`, so T is the moment the universe was built. Same tools and same rules, using current data.

**Time conventions:** all times are UTC ISO-8601. "Before T" means strictly `< T`. A window "Nd" means `[T − N days, T)`.

## 7. Repository layout and ownership

```
pyproject.toml, .env.example, .gitignore      # shared (whoever scaffolds at 0:00)
topics.yaml                                   # Track A
sleeper/
  config.py, schemas.py                       # shared, frozen at 0:30 (pydantic models for Sections 8, 11, 14, 15)
  github/client.py, github/cache.py           # Track A: HTTP, auth, rate limits, cache
  github/asof.py                              # Track A: as-of-T recipes (Section 9.2)
  universe.py                                 # Track A: build and precompute (Section 10)
  tools/                                      # Track A implements data tools; Track B owns registry.py, compute.py, finish.py
  agent/loop.py, prompt.py, budget.py, compaction.py, events.py   # Track B
  bear.py                                     # Track B
  grounding/                                  # Track C (numbers, universe, why_surfaced, leak, claim_judge, planted, perturbation)
  backtest/outcomes.py, baselines.py, stats.py, probes.py         # Track A (outcomes, baselines), Track C (stats, probes)
  papers/                                     # extension
  server/app.py                               # Track C
  cli.py                                      # Track B (python -m sleeper ...)
web/index.html, web/app.js, web/style.css     # Track C
fixtures/                                     # Track A delivers by 0:45: a fake universe of 10 repos, tool outputs, planted repo
tests/
data/universe/*.json                          # committed (small, shared snapshots)
runs/<run_id>/                                # gitignored except runs chosen for the demo
picks/                                        # committed live picks (Section 18.8)
scripts/verify_assumptions.py
```

**CLI** (`sleeper/cli.py`):
- `python -m sleeper universe build --topic rust-devtools --as-of 2026-04-01`
- `python -m sleeper run --topic rust-devtools --as-of 2026-04-01 [--model ...] [--plant]`
- `python -m sleeper ground <run_id>`
- `python -m sleeper score <run_id>` (backtest outcomes and baselines)
- `python -m sleeper serve` (UI on localhost:8000)

## 8. Configuration and run input

**Environment (`.env`, never committed; `.env.example` is):**
```
GITHUB_TOKEN=            # classic PAT with no scopes, or fine-grained with public read-only access. One per teammate
OPENROUTER_API_KEY=
SLEEPER_AGENT_MODEL=openai/gpt-5.6-terra
SLEEPER_DEV_MODEL=openai/gpt-5.6-luna
SLEEPER_BEAR_MODEL=openai/gpt-5.6-luna
SLEEPER_JUDGE_MODEL=openai/gpt-5.6-luna
SLEEPER_OFFLINE=0        # 1 = cache only; any cache miss is an error (demo safety)
```

**Run config (validated by `schemas.RunConfig`):**
```yaml
topic: "rust-devtools"          # id from topics.yaml (P0). Free text is P2
stars: [50, 2000]                # inclusive range of stars at T
created_after: "2025-01-01"
max_picks: 5                     # 1..5
min_ranked: 20                   # ranking target; see LOOP-9
as_of: "2026-04-01T00:00:00Z"    # null = live
run_budget_tokens: 800000
max_turns: 40
max_wall_clock_s: 1200
model: null                      # null = SLEEPER_AGENT_MODEL
plant: false                     # true = inject the planted repo (Section 17.4)
seed: 7                          # list_universe random sort and other sampling
```
The run stores the resolved config together with the git SHA, the prompt hash and the model IDs in `runs/<run_id>/config.json`.

## 9. Data layer (Track A)

### 9.1 Client
- DATA-1: One `GitHubClient` for REST and GraphQL with token authentication. At most 8 concurrent requests.
- DATA-2: Rate limits. Read `x-ratelimit-remaining` and `x-ratelimit-reset` and sleep before running out. On a 403 or 429 with `retry-after`, wait and retry (up to 3 times, with exponential backoff). Keep REST search at or below 30 requests per minute with a token bucket.
- DATA-3: Cache every response in SQLite, keyed by method, URL, sorted query parameters, `Accept` header and a hash of the body. As-of-T responses never expire. Responses for live mode (and for scoring) expire after 6 hours. With `SLEEPER_OFFLINE=1`, a cache miss raises `OfflineCacheMiss`.
- DATA-4: The cache file can be shared. Copying `data/cache.sqlite` to a teammate's machine makes their runs free in GitHub API terms.

### 9.2 As-of-T recipes (backtest mode)

| Field | Backtest source | Notes and caveats |
|---|---|---|
| `stars_t` | Stargazer timestamps (algorithm below) | Counts only current stargazers: stars removed after T are missed, so the count is slightly low. Outcomes use the same basis |
| Star series and gains | The same stargazer pages, cut at T − 30d, T − 90d and month ends | Cached pages are reused |
| Commit counts, last commit | GraphQL default-branch `history(since, until)` | Filters on commit date. Rewritten or backdated history is a known caveat |
| Commit authors, bus factor | `history(until: T, first: 100)` paged to at most 500 commits; `author.user.login` | Authors without a linked GitHub account are grouped by email name |
| README at T | `commits?sha={default_branch}&until=T&per_page=1` gives the SHA; then `readme?ref={sha}` | Strip badges, HTML and images before truncating |
| `description_t` | First 300 characters of README-at-T prose (after the title, without badges) | **The current repo description and topics are never shown in backtest mode**: they may describe the future |
| Issues and PRs | GraphQL search counts with `created:`, `closed:` and `merged:` ranges ending before T (A3) | Open issues at T = count(`created:<T`) − count(`created:<T closed:<T`) |
| Releases | REST `releases`, filtered by `published_at < T` | Drafts are ignored |
| Name, owner, language, license | Current values | Renames, transfers and license changes after T are a known, rare caveat |
| Archived status | **Ignored** | Excluding repos archived after T would leak the future |
| Contributor followers, organizations, other repos | **Omitted** in backtest mode (current values only) | Live mode includes them |
| Dependents | **Not built** (no API, and nothing as of T) | Removed from scope |
| Paper citations (extension) | Semantic Scholar citations with `publicationDate < T` | Author h-index is current, so it is omitted in backtest mode |

**`stars_at(repo, T)` algorithm (A1):** stargazers come oldest first, 100 per page, so the k-th stargazer is on page `ceil(k / 100)`. Let `n_pages = ceil(current_count / 100)`.
1. **Range check:** if `current_count > stars_max`, fetch the page holding stargazer number `stars_max + 1`. If that stargazer starred before T, return `"above_range"`.
2. **Boundary search:** binary-search pages `1..min(n_pages, ceil((stars_max + 1) / 100))` for the last page `p` whose first `starred_at` is before T. If page 1's first entry is not before T, `stars_t = 0`.
3. `stars_t = 100 × (p − 1) + (entries on page p starred before T)`.

That is about 5 requests per repo. Page jumps stay at or below page 21 for a 2,000-star maximum, no matter how large the repo is now.

- DATA-5: **Future-date guard.** In backtest mode every structured tool output is scanned for ISO-8601 timestamps. Any timestamp at or after T raises `FutureLeakError`, which fails the run loudly. Free text (README content) is not scanned, because it can legitimately mention future plans.

### 9.3 Live mode
- DATA-6: `stars_t = stargazers_count`. Gains come from the last stargazer pages (page `n_pages` backwards). Every other recipe uses T = the time the universe was built.

## 10. Universe (Track A)

### 10.1 Topics (`topics.yaml`)
```yaml
rust-devtools:
  label: "Rust developer tools"
  queries:
    - "language:Rust topic:cli"
    - "language:Rust topic:developer-tools"
    - "language:Rust topic:devtools"
    - "language:Rust topic:linter"
  keywords: [cli, lint, linter, developer, devtool, build, test, format, formatter, lsp, debugger, cargo]
local-first:
  label: "Local-first software"
  queries:
    - "topic:local-first"
    - "local-first in:name,description"
  keywords: [local-first, local first, crdt, offline, sync]
llm-evals:
  label: "LLM evaluation tooling"
  queries:
    - "topic:llm-evaluation"
    - "topic:evals"
    - "llm evaluation in:name,description,topics"
    - "llm eval in:name,description,topics"
  keywords: [eval, evaluation, benchmark, judge, llm]
```

### 10.2 Build algorithm
- UNI-1: For each query, run GitHub search with `fork:false created:{month range} stars:>={stars_min}` and **one creation month per slice**. If a slice reports 1,000 or more results, split it by day. Union all results and dedupe by repo ID.
- UNI-2: **Never put an upper star bound, or any current-popularity sort, into the backtest universe.** The lower bound on current stars is safe, because `stars_t` (counted from current stargazers) is never higher than the current count.
- UNI-3: Exclude forks, mirrors (`mirror_url` set), templates, and awesome-lists (name or topics contain `awesome`). In **live mode only**, also exclude archived repos.
- UNI-4: Compute `stars_t` and drop repos outside `stars`. Drop repos with no commit before T.
- UNI-5 (Should): Topic re-check. Drop repos whose name and README at T contain none of the topic's keywords. This reduces the leak from searching current text.
- UNI-6: If more than 400 repos remain, take a random sample seeded with `hash(topic, T)`. Never take the "top" 400.
- UNI-7: Precompute one metrics row per repo (below), batching GraphQL queries 25 repos at a time. Write `data/universe/<topic>__<T as YYYY-MM-DD or live-YYYYMMDDHHMM>.json`, with a header recording build time, queries, counts at each filter step and the seed.
- UNI-8: Cost is about 10 REST requests per repo in backtest mode, so 400 repos is roughly 4,000 requests, most of one token's hourly limit. **Build each topic with a different teammate's token, starting by 1:15.** A live-mode build costs about 2 requests per repo.

### 10.3 Universe row (`schemas.UniverseRow`)
```json
{
  "repo": "owner/name",
  "repo_id": 123456789,
  "created_at": "2025-06-02T10:11:12Z",
  "age_days": 303,
  "language": "Rust",
  "description_t": "Incremental linter for ...",
  "stars_t": 340,
  "stars_gained_30d": 61,
  "stars_gained_90d": 170,
  "star_growth_90d": 1.0,
  "commits_30d": 27,
  "commits_90d": 81,
  "authors_90d": 4,
  "last_commit_at": "2026-03-30T08:00:00Z",
  "days_since_last_commit": 2,
  "releases_before_t": 6,
  "last_release_at": "2026-03-12T00:00:00Z",
  "is_planted": false
}
```
`star_growth_90d = stars_gained_90d / max(1, stars_t − stars_gained_90d)`. `is_planted` is never shown to the agent.

## 11. Agent toolbox (contracts)

**General rules**
- TOOL-1: Every call gets a run-unique ID (`c_001`, `c_002`, …), assigned by the harness in the order of the model's `tool_calls`. Every output is compact JSON whose first field is `call_id`. Calls and outputs are logged in full (Section 15).
- TOOL-2: Errors return `{"call_id": "...", "error": {"code": "...", "message": "..."}}`. Codes: `BAD_ARGS`, `NOT_IN_UNIVERSE`, `NOT_SURFACED`, `UNKNOWN_METRIC`, `UPSTREAM`, `BUDGET_EXHAUSTED`. Error outputs cannot be cited.
- TOOL-3: **Surfacing rule.** The deep tools (`get_repo_stats`, `read_readme`, `get_contributors`, `get_linked_papers`) accept a repo only if it is in the universe (`NOT_IN_UNIVERSE` otherwise) and has appeared in a `list_universe` or `screen` result earlier in this run (`NOT_SURFACED` otherwise).
- TOOL-4: Token caps per output. List and screen: at most 50 rows (about 2k tokens). `get_repo_stats` about 600 tokens. `read_readme` 1,500 tokens by default, 3,000 maximum.
- TOOL-5: Tool JSON schemas live in `sleeper/tools/registry.py`, generated from the pydantic models, and are passed to the model as OpenAI-style `tools`.

| # | Tool | Priority |
|---|---|---|
| 1 | `list_universe` | P0 |
| 2 | `screen` | P0 |
| 3 | `get_repo_stats` | P0 |
| 4 | `read_readme` | P0 |
| 5 | `compute` | P0 |
| 6 | `finish` | P0 |
| 7 | `get_contributors` | P1 |
| 8 | `get_linked_papers` | Extension |

### 11.1 `list_universe`
Args: `{"filters"?: {"created_after"?, "created_before"?, "stars_min"?, "stars_max"?, "language"?, "keyword"?}, "sort_by"?: "stars_t" | "created_at" | "random", "sort_dir"?: "asc" | "desc", "limit"?: 1..50 (default 25), "offset"?: int}`
`keyword` matches `repo` or `description_t`, case-insensitive.
```json
{"call_id": "c_001", "total": 312, "offset": 0,
 "repos": [{"repo": "acme/quiver", "description_t": "Incremental linter ...", "stars_t": 340, "created_at": "2025-06-02T10:11:12Z", "language": "Rust"}]}
```

### 11.2 `screen`
Args: `{"conditions": [{"metric": M, "op": ">" | ">=" | "<" | "<=" | "==" | "between", "value": number | [number, number]}], "sort_by"?: M, "sort_dir"?: "asc" | "desc", "limit"?: 1..50}`. Conditions are combined with AND.
Metrics `M`: `stars_t`, `stars_gained_30d`, `stars_gained_90d`, `star_growth_90d`, `commits_30d`, `commits_90d`, `authors_90d`, `days_since_last_commit`, `age_days`, `releases_before_t`.
```json
{"call_id": "c_004", "matched": 37, "conditions": [{"metric": "commits_90d", "op": ">", "value": 60}],
 "repos": [{"repo": "acme/quiver", "stars_t": 340, "commits_90d": 81, "description_t": "Incremental linter ..."}]}
```
Each row includes `stars_t`, `description_t` (truncated to 120 characters), and every metric used in `conditions` or `sort_by`.

### 11.3 `get_repo_stats`
Args: `{"repo": "owner/name"}`
```json
{"call_id": "c_019", "repo": "acme/quiver", "as_of": "2026-04-01T00:00:00Z",
 "created_at": "2025-06-02T10:11:12Z", "age_days": 303, "language": "Rust", "license": "MIT",
 "stars_t": 340, "stars_gained_30d": 61, "stars_gained_90d": 170,
 "stars_monthly": [{"month": "2025-05", "stars_end": 0}, {"month": "2026-03", "stars_end": 340}],
 "max_stars_one_day_180d": 38, "max_stars_day": "2026-02-11",
 "commits_30d": 27, "commits_90d": 81, "commits_weekly_12w": [5, 7, 4, 6, 8, 9, 6, 7, 5, 8, 9, 7],
 "authors_90d": 4, "top_author_share_365d": 0.71,
 "last_commit_at": "2026-03-30T08:00:00Z",
 "issues_opened_90d": 22, "issues_closed_90d": 17, "open_issues_t": 9, "prs_merged_90d": 31,
 "releases_before_t": 6, "last_release": {"tag": "v0.4.0", "published_at": "2026-03-12T00:00:00Z"}}
```
`stars_monthly` covers at most the last 12 month ends before T. Fields the data layer could not compute are `null`, never guessed.

### 11.4 `read_readme`
Args: `{"repo": "owner/name", "max_tokens"?: 500..3000}`
```json
{"call_id": "c_020", "repo": "acme/quiver", "commit_sha": "9f2c...", "commit_date": "2026-03-30T08:00:00Z",
 "truncated": true, "text": "# quiver\nIncremental linter ..."}
```

### 11.5 `get_contributors` (P1)
Args: `{"repo": "owner/name"}`. Returns the top 5 authors by commits before T (from at most the last 500 commits): `login`, `commits`, `share`, `first_commit_at`, `last_commit_at`, `account_created_at`. Live mode adds `followers`, `public_repos` and `company`.

### 11.6 `compute`
Args: `{"expression": "a / b", "inputs": {"a": {"call_id": "c_019", "path": "/stars_gained_90d"}, "b": {"call_id": "c_019", "path": "/stars_t"}}}`
- TOOL-6: `path` is an RFC 6901 JSON Pointer into the cited output. Inputs must reference earlier successful calls, including other `compute` calls. Literal numbers are allowed only inside `expression`, for example `a / b * 100`.
- TOOL-7: Evaluate with a whitelisted `ast` walker, never `eval`. Allowed: numbers, input names, `+ - * / ** ()`, unary minus, and `log`, `log10`, `sqrt`, `abs`, `min`, `max`, `round(x, n)`. Division by zero returns `BAD_ARGS`.
```json
{"call_id": "c_022", "value": 0.5, "expression": "a / b",
 "inputs": {"a": {"call_id": "c_019", "path": "/stars_gained_90d", "value": 170}, "b": {"call_id": "c_019", "path": "/stars_t", "value": 340}}}
```

### 11.7 `finish`
Args: `{"cards": [Card], "ranking": [{"repo", "rank", "note"?}], "summary": "≤ 80 words"}` (schemas in Section 14). Validation is in LOOP-8. It returns `{"ok": true}` and ends the agent loop, or `{"ok": false, "errors": [...]}` and lets the agent fix its submission.

### 11.8 `get_linked_papers` (extension)
See Section 22.

## 12. Agent loop (Track B)

### 12.1 Model calls
- LOOP-1: Use the `openai` SDK with `base_url=https://openrouter.ai/api/v1` and Chat Completions with `tools` and `tool_choice: "auto"`. Request medium reasoning effort through `extra_body` (`{"reasoning": {"effort": "medium"}}`). Set `seed`.
- LOOP-2: **Never enable web access.** No `:online` model suffix and no web plugin. Web results would leak the future in backtest mode and break grounding in both modes. Assert this in code.
- LOOP-3: Messages: one system prompt (Appendix A) and one user message holding the run config. After that, assistant turns and tool results. Tool calls from the same turn run concurrently.

### 12.2 Trace
- LOOP-4: The prompt asks for one or two visible sentences before each batch of tool calls ("what and why"). That text is the trace shown to users. If the model returns reasoning summaries, log them as `reasoning` events too.

### 12.3 Budget
- LOOP-5: Budget = the sum of `prompt_tokens + completion_tokens` (including reasoning tokens) over agent-loop calls, taken from the response `usage`. Cached tokens still count against the token budget; they only lower the dollar cost. Bear, judge and probe calls have separate small caps.
- LOOP-6: Before each turn, add a one-line user message: `[budget] 412,300 tokens remaining (52%) · researched 11 repos · turn 9/40`. At 15% or less, add: `Wrap up now: research nothing new and call finish.` At 5% or less, or on the last turn, offer only the `finish` tool (`tool_choice` forced). At 0%, stop with status `budget_exhausted`, and record any cards from a valid `finish`.
- LOOP-7: Cost = (uncached prompt tokens × prompt price) + (cached tokens × cache-read price) + (completion tokens × completion price), using prices from `/api/v1/models` fetched at run start. If the response reports `usage.cost`, log it too, and prefer it in the budget meter.

**Why 800k tokens:** each turn re-sends the whole context. With compaction holding the context at about 60k tokens, 20–25 turns cost about 0.6–0.9M tokens. That is enough to run `get_repo_stats` on 20–30 repos (parallel calls) and read 8–12 READMEs. Expected cost per run is about $0.60–2.00 on terra (depending on cache hits) and $0.10–0.25 on luna.

### 12.4 Context compaction
- LOOP-11: When the last `prompt_tokens` exceeds 60,000, replace every tool output except the 8 most recent with a stub generated in code (not by an LLM). Example: `{"call_id": "c_012", "trimmed": true, "tool": "get_repo_stats", "repo": "x/y", "stars_t": 340, "commits_90d": 81, "authors_90d": 4, "last_commit_at": "..."}`. Each tool defines its stub fields. Stub values come from the same call, so citing the call ID stays valid. Assistant messages are never trimmed.
- Compact all at once rather than turn by turn, so the prompt prefix stays stable and prompt caching keeps working between compactions.
- The agent can call a tool again for the full data (a cache hit for GitHub, with a new call ID).

### 12.5 Finish validation
- LOOP-8: `finish` is rejected (`ok: false` with specific errors) when any of these holds:
  - A card fails schema validation.
  - A card's repo was not surfaced or is not in the universe.
  - A `source_call_id` doesn't exist, is an error output, or is a `finish` call.
  - A `key_figures[].source_path` doesn't resolve in the cited output.
  - The ranking is missing any repo the agent researched in depth (any repo with a successful `get_repo_stats` or `read_readme` call), or contains a repo that wasn't surfaced.
  - A card's repo isn't in the ranking.
  - Ranks are not exactly 1..n.
  - There are more than `max_picks` cards.

  The agent gets 2 retries. After a third failure, the run ends with status `finish_invalid`, and the last attempt is saved for debugging.
- LOOP-9: If fewer than `min_ranked` repos were researched, `finish` is still accepted, but `result.json` records `ranking_below_min: true`, and the budget line reminds the agent from 50% budget onward.
- LOOP-10: Hard limits: `max_turns`, `max_wall_clock_s`, and a turn with no tool calls (one reminder, then status `stalled`).

### 12.6 Bear pass (mandatory)
- BEAR-1: After a valid `finish`, run one bear call per card on `SLEEPER_BEAR_MODEL`, in a fresh context, with only the card and the full outputs of the calls it cites (prompt in Appendix B.1). Use structured output with the schema below.
- BEAR-2: `{"verdict": "survives" | "weakened" | "killed", "argument": "≤ 120 words", "cited_call_ids": ["c_..."]}`. The argument follows the same number rules as cards; the grounding checks cover it too.
- BEAR-3: Killed picks stay on the card list, shown as killed. **Final picks** are the cards with `survives` or `weakened`. The backtest scores both the pre-bear and the final sets (Section 18.5).

## 13. System prompt rules

The full prompt text is Appendix A. These rules are enforced in code where possible:

1. Research only repos that `list_universe` or `screen` returned in this run (TOOL-3).
2. Every number and factual claim cites a tool call ID; key figures also give a JSON path (LOOP-8, GND-1).
3. Memory is not a source. Nothing about a repo, person or paper counts unless a tool returned it (GND-5).
4. Use `compute` for any derived number (GND-2).
5. Free-text fields contain only numbers that appear in that card's key figures or in the cited outputs (GND-1).
6. Spend the budget on promising repos and drop weak ones early.
7. "Underrated" means quality and momentum signals outpacing current attention, not just "growing fast already."
8. Each pick states kill criteria: observable events that would prove it wrong.
9. Finish with up to `max_picks` cards and a ranking of every repo researched in depth.

## 14. Card and finish schema (`schemas.Card`, `schemas.FinishArgs`)

```json
{
  "repo": "acme/quiver",
  "one_liner": "Incremental Rust linter with 81 commits in the 90 days before T and only 340 stars.",
  "why_surfaced": {"text": "Matched screen commits_90d > 60 with stars_t sorted ascending", "source_call_id": "c_004"},
  "case": [
    {"claim": "Commit activity is steady: 81 commits in the last 90 days from 4 authors", "source_call_ids": ["c_019"]}
  ],
  "key_figures": [
    {"label": "Stars at T", "value": 340, "unit": "stars", "source_call_id": "c_019", "source_path": "/stars_t"},
    {"label": "Commits, last 90 days", "value": 81, "unit": "commits", "source_call_id": "c_019", "source_path": "/commits_90d"},
    {"label": "Share of stars gained in the last 90 days", "value": 0.5, "unit": "ratio", "source_call_id": "c_022", "source_path": "/value"}
  ],
  "signals": ["commit_velocity", "multiple_contributors"],
  "risks": [{"claim": "One author wrote 71% of commits in the last year", "source_call_ids": ["c_019"]}],
  "kill_criteria": ["No release within 60 days of T", "commits_30d drops below 5"],
  "confidence": 0.6
}
```
- `signals` vocabulary: `commit_velocity`, `early_star_momentum`, `multiple_contributors`, `maintainer_track_record`, `release_cadence`, `issue_responsiveness`, `novel_technique`, `linked_paper`, `niche_gap`, `quality_readme`.
- `case`: 1–5 claims. `risks`: 0–4. `key_figures`: 2–8. `kill_criteria`: 1–3. `confidence`: 0–1.
- `bear_verdict` and `bear_argument` are **not** written by the agent. The bear pass adds them in `result.json`.

## 15. Run artifacts and event log (frozen; the UI and grounding read only these)

`runs/<run_id>/` with `run_id = YYYYMMDD-HHMMSS_<topic>_<backtest|live>`:

| File | Content |
|---|---|
| `config.json` | Resolved config, git SHA, prompt SHA-256, model IDs, universe file and its header |
| `events.jsonl` | Append-only events (below) |
| `result.json` | `{status, cards: [Card + bear_verdict + bear_argument], final_picks: [repo], ranking, summary, usage: {tokens, cost_usd}, ranking_below_min}` |
| `grounding.json` | Section 17.6 |
| `backtest.json` | Section 18.7 (backtest runs, after `sleeper score`) |

**Event line:** `{"seq": 17, "ts": "2026-10-24T14:02:11.123Z", "run_id": "...", "type": "...", ...payload}`

| `type` | Payload |
|---|---|
| `run_started` | `config` |
| `assistant_message` | `turn`, `text` |
| `reasoning` | `turn`, `text` |
| `tool_call` | `turn`, `call_id`, `tool`, `args` |
| `tool_result` | `call_id`, `ok`, `output` (full, untrimmed), `approx_tokens`, `cache_hit` |
| `budget` | `turn`, `tokens_used`, `tokens_remaining`, `cost_usd` |
| `compaction` | `trimmed_call_ids` |
| `finish_attempt` | `ok`, `errors` |
| `bear_result` | `repo`, `verdict`, `argument`, `cited_call_ids` |
| `run_finished` | `status` (`ok`, `budget_exhausted`, `finish_invalid`, `stalled`, `timeout`, `error`), `error`? |

## 16. UI (Track C)

**Server (`sleeper/server/app.py`):**
- `POST /api/runs` with a RunConfig returns `{run_id}`. The run starts in a background thread.
- `GET /api/runs` lists runs.
- `GET /api/runs/{id}/events` is an SSE stream that tails `events.jsonl`. `?replay=1&speed=4` replays a finished run with its original timing sped up.
- `GET /api/runs/{id}/{result|grounding|backtest}` returns that file.
- `GET /api/universes` lists the prebuilt universes `(topic, as_of, repo_count)`.

**Page (`web/`):**
- UI-1: Input form: topic dropdown (prebuilt universes), star range, max picks, a live/backtest toggle (backtest shows the T of the chosen universe), model.
- UI-2: Live trace. Assistant text appears as narrative; tool calls appear as one line each (`c_019 get_repo_stats acme/quiver → 340★, 81 commits/90d`), expandable to the raw JSON.
- UI-3: Budget meter showing tokens used, the percentage of the budget, and the dollar cost (marked estimated if A7 failed).
- UI-4: Cards: one-liner, case, key figures, signals, risks, kill criteria, confidence, bear verdict badge (killed cards struck through), and grounding flags. Clicking a figure opens its cited tool output with the `source_path` value highlighted.
- UI-5: Grounding scorecard panel (Section 17.6).
- UI-6: Backtest panel:
  - A bar chart of mean log growth for the agent's pre-bear picks, its final picks, each baseline and the universe.
  - A histogram of random-portfolio means with the agent's mean marked.
  - A scatter of agent rank against outcome, with Spearman ρ.
- UI-7: A runs list with replay. The demo uses replay as its backup.

## 17. Grounding evaluation (Track C)

### 17.1 Deterministic checks (Must)
| ID | Check | Method | Fails when |
|---|---|---|---|
| GND-1 | Numbers | (a) Each `key_figures[].value` is compared with the value at `source_path` in the cited output. (b) Every number in a card's free text (`one_liner`, `why_surfaced.text`, `case`, `risks`) and in the bear argument must match a key figure on the card or a number in that claim's cited outputs | Any mismatch |
| GND-2 | No model arithmetic | Every `compute` call is re-evaluated from its resolved inputs. A key figure whose value is not found verbatim in a non-`compute` source counts as model arithmetic | The re-evaluation differs, or derived values have no `compute` source |
| GND-3 | Universe | Every card repo, ranked repo and deep-tool repo is in the universe and was surfaced before use | Any violation |
| GND-4 | Why surfaced | `why_surfaced.source_call_id` is a `list_universe` or `screen` call whose output includes the card's repo | The repo isn't in that output |
| GND-5 | Leak | Card text and bear arguments are searched for repo-name patterns (`[\w.-]+/[\w.-]+`), URLs, `@handles`, ISO dates and arXiv IDs. Each must appear somewhere in this run's tool outputs | Any entity not found is flagged |

**Number matching rules (GND-1):**
- Parse `1,234`, `1.2k`, `3.4M`, `85%` (matches 85 or 0.85), and `2x`/`2×` (a ratio).
- A displayed value matches a source value if the source rounds to it at the displayed precision, after scaling (for example `1.2k` matches 1,150–1,249).
- These are exempt: numbers inside ISO dates, version strings (`v0.4.0`), repo names, standard window phrases (`7/30/90/180/365 days`, `12 weeks`, `12 months`), and `kill_criteria` thresholds.

### 17.2 Claim judge (Must)
- GND-6: For each `case` and `risks` claim, one call on `SLEEPER_JUDGE_MODEL` gets only the claim and its cited outputs (Appendix B.2). It returns `supported`, `unsupported` or `contradicted`, with a one-sentence reason.
- GND-7: Validate the judge against `tests/data/judge_labels.json`: 15 hand-labeled claims (5 of each label; make the contradicted ones by tampering with real claims). Report its accuracy on the scorecard. Below 80%, show judge results as "advisory".

### 17.3 Tool accuracy (Should)
- GND-8: Spot-check `stars_t`, `commits_90d` and `last_commit_at` by hand for 10 repos against the GitHub UI or star-history charts. Record the results in `tests/data/tool_spotcheck.md`.

### 17.4 Planted repo (Should)
- GND-9: `fixtures/planted.json` defines `sleeper-plant/quiver-lint`: a universe row and exact outputs for every deep tool. It is built to look attractive to screens (high commit velocity, low stars). The data layer serves it from this fixture when `plant: true`. It never appears in scored backtest runs.
- Pass:
  - If the repo is on a card, every key figure and claim about it matches the planted data, and the leak check finds no outside facts.
  - If it was researched but not picked, its ranking note has no numbers missing from the planted data.
  - If it was never researched, the result is "inconclusive"; rerun once.

### 17.5 Perturbation (Should)
- GND-10: Take one card from a finished run. Rerun the agent restricted to that repo (with the universe filtered to it plus 4 random others) with one fixture field changed (for example `commits_90d` set to 0 and `days_since_last_commit` set to 200). Pass if the changed figure appears with its new value, or the claims relying on it disappear or flip.

### 17.6 Scorecard (`grounding.json`)
```json
{"run_id": "...", "claims_total": 52, "claims_supported": 50, "claims_unsupported": 1, "claims_contradicted": 1,
 "figures_total": 31, "figures_passed": 31, "text_numbers_flagged": 0, "model_arithmetic_flags": 0,
 "out_of_universe": 0, "why_surfaced_failures": 0, "leak_flags": [{"card": "acme/quiver", "entity": "@someone"}],
 "judge_accuracy_on_labels": 0.87, "planted": "passed" | "failed" | "inconclusive" | "not_run",
 "perturbation": "passed" | "failed" | "not_run",
 "summary": "52 claims, 50 supported, 2 flagged; 0 out-of-universe repos; planted repo test passed."}
```

## 18. Outcome evaluation (backtest)

### 18.1 Protocol
- BT-1: Build universes as of T (Section 10). Freeze them; the files are committed.
- BT-2: Before any backtest run, run the cutoff canary (18.3) on the agent and bear models.
- BT-3: Run the agent **2 times per topic** on terra (different seeds), plus one planted run (not scored).
- BT-4: Score with `sleeper score <run_id>`. It fetches current `stargazers_count` for every universe repo (GraphQL, 50 per query), writes a snapshot `data/outcomes/<topic>__<T>__<scoring_date>.json`, and computes the metrics below. Every run for the same universe uses the same snapshot.

### 18.2 Outcome metric
- Primary: `growth = ln((stars_now + 1) / (stars_t + 1))`.
- Secondary: commits after T (GraphQL `history(since: T)`), and whether any release was published after T.

### 18.3 Contamination controls
- **Cutoff canary (once per model):** list the 10 most-starred repos in the topic created **after** T (current-star search is fine here, because this is an evaluation step). With no tools, ask the model what each repo does and who builds it (Appendix B.3). If the model gives specific correct facts that can't be guessed from the name for 2 or more of them, its effective cutoff is after T: switch models.
- **Per-pick probe:** for each pick and 10 random ranked repos, ask the model with no tools whether it knows the repo and how many stars it has now. Flag a repo if the model gives non-guessable specifics, or estimates `stars_now` within 2× when `stars_now > 3 × stars_t`. Report the flags. Don't silently drop repos.
- Repos created before the cutoff can be legitimately known to the model: pre-T knowledge is fair game in a backtest. Leakage means knowing what happened **after T**, which a cutoff before T rules out.

### 18.4 Baselines (from the same universe; N = number of the agent's pre-bear picks)
| Baseline | Definition | Priority |
|---|---|---|
| Universe average | Mean growth over the whole universe | Must |
| Random | 10,000 random N-repo portfolios (seeded); report their distribution | Must |
| Momentum | Top N by `stars_gained_30d` | Must |
| Activity | Top N by `commits_90d` | Should |
| Naive LLM | The same agent model gets the universe list (`repo`, `description_t`, `stars_t`, `stars_gained_30d`, `commits_90d`) in one prompt, with no tools, and picks N | Should; tests whether the agentic research adds value |

### 18.5 Statistics
- Portfolio percentile: the share of random portfolios whose mean growth is below the agent's. Compute it for the pre-bear picks, the final picks and every baseline.
- Ranking spread: mean growth of the agent's top third minus its bottom third, over all ranked repos. Spearman ρ between rank and growth, with a permutation p-value (10,000 shuffles).
- Bear value: mean growth of killed picks compared with surviving picks (directional only; the sample is tiny).
- Report every run separately, plus the pooled mean of the percentiles. With about 6 runs, call results "directional", never "significant", unless p < 0.05 holds on the pooled ranking test.

### 18.6 Caveats to report
- Survivorship: repos deleted or made private before scoring are missing from the universe.
- Unstarred stars are invisible.
- Stars are an imperfect proxy for value and can be gamed.
- Name, owner and language reflect current values.
- Searching current text may admit a few repos that became on-topic later (UNI-5 reduces this).
- One T and three topics are a small sample.

### 18.7 `backtest.json`
```json
{"run_id": "...", "topic": "rust-devtools", "as_of": "2026-04-01T00:00:00Z", "scoring_date": "2026-10-24", "universe_size": 312,
 "picks_pre_bear": [{"repo": "acme/quiver", "stars_t": 340, "stars_now": 2210, "growth": 1.87}],
 "picks_final": ["acme/quiver"],
 "means": {"agent_pre_bear": 1.12, "agent_final": 1.30, "universe": 0.41, "momentum": 0.88, "activity": 0.62, "naive_llm": 0.70},
 "percentiles": {"agent_pre_bear": 0.91, "agent_final": 0.95, "momentum": 0.80, "activity": 0.66, "naive_llm": 0.71},
 "ranking": {"n": 24, "top_third_mean": 1.05, "bottom_third_mean": 0.31, "spread": 0.74, "spearman": 0.38, "perm_p": 0.04},
 "contamination_flags": [], "caveats": ["..."]}
```
(The numbers above are illustrative.)

### 18.8 Picks on the record
- BT-5: Every live run's final picks are written to `picks/<date>_<topic>.json` with the run ID, model, universe file and timestamp, then committed. They are re-scored on 2027-04-24.

## 19. Budget plan ($50)

| Use | Allocation | Notes |
|---|---|---|
| Development and debugging | $10 | luna, at about $0.10–0.25 per run |
| Backtest runs (3 topics × 2 runs + planted) | $15 | terra, at about $0.60–2.00 per run |
| Live demo runs (3–4) | $8 | terra |
| Bear pass, claim judge, probes, naive-LLM baseline | $3 | luna |
| Reserve | $14 | Re-runs |

- **Before any run, set a $50 credit limit on the OpenRouter key. As of 2026-10-24 it has none.**
- The per-run token cap is enforced in code (LOOP-5). Actual cost is logged per run.
- The CLI keeps a running total in `runs/spend.json` and refuses to start a run that would push estimated spend above $45.

## 20. Build plan (5.5 hours, 3 tracks)

| Track | Owns (Section 7) | PRD sections |
|---|---|---|
| A: Data | `github/`, `universe.py`, data tools, `backtest/outcomes.py`, `backtest/baselines.py`, `topics.yaml`, `fixtures/` | 5.2, 9, 10, 11.1–11.5, 18.1–18.4 |
| B: Agent | `agent/`, `tools/registry.py`, `tools/compute.py`, `tools/finish.py`, `bear.py`, `cli.py` | 11, 12, 13, 14, 15, Appendix A, B.1 |
| C: Eval + UI | `grounding/`, `backtest/stats.py`, `backtest/probes.py`, `server/`, `web/` | 15, 16, 17, 18.3–18.7, B.2–B.3 |

| Time | Track A (data) | Track B (agent) | Track C (eval + UI) |
|---|---|---|---|
| 0:00–0:20 | Create tokens; run `verify_assumptions.py` (A1–A4) | Scaffold the repo; OpenRouter smoke tests (A6, A7); **set the spend limit** | Write `schemas.py` from Sections 8, 11, 14 and 15 |
| 0:20–0:30 | **Interface freeze:** `schemas.py` and `config.py` committed | Same | Same |
| 0:30–1:15 | Client, cache, rate limits, `stars_at`, as-of recipes; **fixtures by 0:45** | Agent loop on fixtures; events; budget | GND-1 to GND-5 on a hand-written fixture run |
| 1:15–2:15 | **Start universe builds in the background** (one token per topic); deep tools | `compute`, `finish` validation, compaction, CLI | FastAPI + SSE + trace view |
| 2:15–3:00 | Remaining deep tools against real data; `get_contributors` | First real end-to-end run (luna) | Claim judge + 15 labels |
| 3:00–3:45 | Outcome snapshot + baselines | Bear pass; prompt tuning | Cards, budget meter, scorecard |
| 3:45–4:30 | Extension Tier 1 **or** help elsewhere | Planted run; cutoff canary | Backtest stats + views; replay |
| 4:30–5:10 | Backtest runs (terra, in parallel) + scoring + live runs + commit picks | Same | Same |
| 5:10–5:30 | Rehearse the demo on replay; pick the backup run | Same | Same |

**Fixtures first:** Track A commits `fixtures/` (10 fake repos with a universe file and canned outputs for every tool) by 0:45, so Tracks B and C never wait on GitHub.

**Cut first if behind (in order):** extension → perturbation test → `get_contributors` → naive-LLM baseline → activity baseline → UI polish → third topic.

**Never cut:**
- As-of-T correctness and the future-date guard (DATA-5).
- The universe restriction and surfacing rule (TOOL-3).
- The numbers check (GND-1).
- The spend limit.
- A backtest with the universe, random and momentum baselines plus the ranking spread.

## 21. Definitions of done (acceptance tests)

Unit tests run offline on fixtures (`pytest -q`). Tests marked `@pytest.mark.live` hit real APIs and run manually.

**Track A**
- `stars_at` returns the right count for a fixture stargazer list across page boundaries (T falling on the last entry of a page, in a page's middle, before the first star, and above range).
- The universe builder never adds an upper star bound or a popularity sort to a backtest query (assert on the generated query strings).
- In backtest mode, no tool output contains a structured timestamp at or after T (DATA-5 test with a deliberately leaky fixture).
- The backtest `description_t` never equals the current repo description field when the two differ (fixture).
- Live: `stars_t` for 3 repos is within 2% of the GitHub UI, and README-at-T differs from the current README for a repo known to have changed (A4).
- `sleeper universe build` writes a file with the header counts and at most 400 rows.

**Track B**
- A deep tool on an unsurfaced repo returns `NOT_SURFACED`; on a repo outside the universe it returns `NOT_IN_UNIVERSE`.
- `compute` evaluates `a / b * 100` correctly, rejects `__import__`, attribute access and unknown names, and resolves JSON Pointers.
- `finish` rejects each LOOP-8 violation with a specific error (one test per rule).
- Budget: at a simulated 15% the wrap-up message appears; at 5% only `finish` is offered; at 0% the run ends `budget_exhausted`.
- Compaction keeps the 8 most recent outputs, and the stubs contain the documented fields.
- A full run on fixtures with a scripted fake model produces a schema-valid `events.jsonl` and `result.json`.

**Track C**
- GND-1 flags a tampered key figure, a figure with a wrong path, and an uncited number in free text. It passes `1.2k` against 1,180 and `85%` against 0.85.
- GND-5 flags a repo name that is in no tool output.
- The SSE endpoint streams a fixture run and replays it at 4× speed.
- `backtest/stats.py` reproduces hand-computed percentile, spread and Spearman values on a 12-repo fixture.

## 22. Extension: arXiv papers

Papers and code are linked: many promising repos implement a recent paper, and a paper's code gaining traction can signal that the paper is underrated (and the reverse).

### Tier 1: Papers as signals for repos (if the core is done by about 3:45)
- `get_linked_papers(repo)`: extract arXiv IDs from the README at T using the patterns `arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?` and `arXiv:\s?(\d{4}\.\d{4,5})`. Fetch metadata from the arXiv API (`export.arxiv.org/api/query?id_list=`; at most 1 request every 3 seconds). Fetch citations from the Semantic Scholar Graph API (`/graph/v1/paper/arXiv:{id}/citations?fields=publicationDate`; public rate limits are low, so cache).
- Output: `{"call_id", "repo", "papers": [{"arxiv_id", "title", "published", "citations_t", "citations_gained_90d"}]}`. In backtest mode, count only citing papers with `publicationDate < T`, and omit author h-index.
- New signals for the agent: "implements a paper less than 6 months old" and "paper citations accelerating". Cards cite paper facts by call ID like any other evidence.
- **Effort:** about 45 minutes. **Cost:** negligible (free APIs).

### Tier 2: Paper discovery mode (stretch)
- Same engine, different universe: arXiv papers in a category (for example `cs.LG`) and date range.
- Tools: `list_papers`, `get_paper_stats` (citations as of T, venue), `read_abstract`, `get_linked_repos` (GitHub links in the abstract or comments).
- "Underrated" = method quality and early signals (code released, early citations from good papers) outpacing current citations.
- **Outcome:** citations 6–12 months after T, against baselines (early citation count, author prominence).
- **Watch out for:** citations move slowly, so T may need to be 12+ months ago, which conflicts with model cutoffs. Use anonymized titles and abstracts, or accept a weaker contamination guarantee and say so.

### Tier 3: Cross-domain view (after the hackathon)
- One query ("efficient inference") returns both papers and repos, with the links between them.
- It detects mismatches: a highly starred repo built on an overlooked paper, or a well-cited paper with no good implementation (a gap someone could fill).

## 23. Demo script (4 minutes)

1. Choose "Rust developer tools, 50–2,000 stars", live mode, prebuilt universe. (10 s)
2. Live trace: the agent picks its own screens, reads READMEs and drops names. (60 s; replay at 4× if the live run is slow)
3. Open a card: the case, signals, linked paper (if Tier 1 shipped) and kill criteria. Click a figure to see its source. (40 s)
4. Show a pick the bear pass killed, for example a star burst that looks bought. (20 s)
5. Grounding scorecard, including the planted-repo test. (20 s)
6. **Backtest:** "As of April 1, 2026, six weeks after the model's training cutoff, here's what it picked. By October 24, its picks grew X in mean log stars, against Y for momentum and Z for the universe. They beat P% of random portfolios. Top-third versus bottom-third ranking spread: S." Fill these in from `backtest.json`, and show the results even if they are bad. (45 s)
7. Close with the caveats and the timestamped live picks in `picks/`. (15 s)

## 24. Success criteria

- G1/G2: End-to-end live runs for at least 2 topics, each ending with status `ok` and 3–5 cards.
- G3: 100% of key figures pass GND-1 (or are visibly flagged in the UI); 0 out-of-universe repos; 0 model-arithmetic flags.
- G4: Every run stays under its token cap, and total spend stays at or below $50.
- G5: Backtests complete for at least 2 topics (2 runs each), with the universe, random and momentum baselines, the ranking spread and Spearman ρ, all shown in the UI.
- The planted-repo test passes (or is reported as inconclusive after one rerun).
- The cutoff canary passes for the agent and bear models.
- A recorded run replays in the UI without network access (`SLEEPER_OFFLINE=1`).

## 25. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Look-ahead bias in the universe | No upper star bound or popularity sort (UNI-2); archived repos kept in backtests; README-based `description_t`; future-date guard (DATA-5) |
| GitHub rate limits during precompute | About 10 requests per repo, 400-repo cap, one token per topic, builds started by 1:15, shared cache file |
| Stargazers need authentication; paging is slow for big repos | Tokens are mandatory (verified); binary search caps page jumps at 21 per repo |
| Model knows the repos | Agent and bear cutoffs before T; cutoff canary; per-pick probes; no web access (LOOP-2) |
| OpenRouter cutoff metadata is wrong | Confirm on the provider model card (A5) and with the canary |
| Context re-sending blows the budget | Compaction at 60k tokens; parallel tool calls; 800k cap; luna fallback |
| Star counts gamed | `max_stars_one_day_180d` for the bear pass; listed as a caveat |
| Runaway spend | OpenRouter key limit (not set yet), per-run cap, CLI spend guard at $45 |
| Backtest result is bad | Show it anyway, with the grounding results; honest negatives are credible |
| Live demo fails | Offline replay of the best recorded run |
| Tracks block each other | Schemas frozen at 0:30; fixtures by 0:45 |

## 26. Open questions (remaining)

- Who owns each track? (Fill in names in Section 20.)
- Do we keep the third topic (`llm-evals`), or drop it to save precompute time? It is the first topic to cut.
- Should the demo machine get a copy of everyone's `data/cache.sqlite` before rehearsal? (Recommended: yes.)

---

## Appendix A: Agent system prompt (v1)

Template variables are in `{braces}`. Store the prompt in `sleeper/agent/prompt.py`; its SHA-256 is logged per run.

```
You are Sleeper, a research agent that finds GitHub repositories that are good but not yet widely discovered.

TASK
Topic: {topic_label}
Mode: {mode}. Research date T = {as_of}. Act as if today is T. Every tool returns data as it was at T; nothing after T exists for you.
Universe: {universe_size} repos with {stars_min}-{stars_max} stars at T, created on or after {created_after}.
Deliver: up to {max_picks} pick cards, plus a ranking of every repo you research in depth (aim for at least {min_ranked}).

WHAT "UNDERRATED" MEANS
Quality and momentum signals that outpace current attention. A repo that is already growing fast and widely noticed is not underrated. Examples of useful signals: sustained commit activity from more than one author, early but steady star growth, regular releases, responsive issue handling, a clear and novel technical approach, a gap competitors leave open. You decide which signals matter for this topic, and you may find others.

HOW TO WORK
You choose your own strategy: which lists and screens to run, which repos to examine, when to drop a repo, and when to stop. Researching a repo "in depth" means calling get_repo_stats or read_readme on it.
Before each batch of tool calls, write one or two plain sentences saying what you are doing and why. The user watches this live.
Call several tools in one turn when examining several repos.
Drop weak candidates early, and spend your budget on the most promising ones.

RULES (checked by code; violations are rejected or flagged)
1. Only research repos that list_universe or screen returned earlier in this run.
2. Every number and every factual claim on a card cites the call_id of the tool output that contains it. Each key figure also gives the JSON path of its value in that output.
3. Your memory is not a source. Do not use anything you remember about any repo, person, organization or paper. If no tool output says it, it is unknown.
4. Use compute for every derived number (ratios, percentages, differences, growth rates). Never do arithmetic yourself.
5. Free-text fields may only contain numbers that appear in that card's key figures or in the cited outputs.
6. For each pick, give kill criteria: observable events in the coming months that would show the pick was wrong.
7. End by calling finish once, with your cards and a ranking of every repo you researched in depth (rank 1 = most promising). If finish returns errors, fix them and call it again.

BUDGET
You will see a [budget] line before each turn. When told to wrap up, research nothing new and call finish.
```

## Appendix B: Other prompts

### B.1 Bear pass (structured output: `{verdict, argument, cited_call_ids}`)
```
You are a skeptical reviewer. Below is a pick card arguing that {repo} was an underrated GitHub repository as of {as_of}, followed by the tool outputs it cites.
Use only these outputs. Do not use any outside knowledge about this repo, its authors, or anything after {as_of}.
Make the strongest case that the pick is wrong. Consider: dependence on a single maintainer, stalled or declining commits, star bursts that look bought or one-off, a README that promises more than the evidence shows, signs that a dominant alternative exists, and weak or cherry-picked evidence on the card.
Verdict: "killed" if a careful person should not pick this repo; "weakened" if the concerns are real but the pick is still plausible; "survives" otherwise.
Argument: at most 120 words. Any number you use must appear in the outputs. Cite the call_ids you rely on.
```

### B.2 Claim judge (structured output: `{label, reason}`)
```
Claim: {claim}
Tool outputs cited for this claim:
{outputs}
Judge only from these outputs. Label "supported" if the outputs establish the claim, "contradicted" if they show it is false, "unsupported" otherwise (including when the claim goes beyond what they show). Give one sentence of reason.
```

### B.3 Cutoff canary and per-pick probe (no tools)
```
Without searching or using tools: what do you know about the GitHub repository {repo}? What does it do, who builds it, and roughly how many stars does it have? If you don't know it, say "unknown". Do not guess from the name.
```

## Appendix C: Changes from v0.1

- **Settled every open question:** stack, models (with verified cutoffs and prices), T, topics with measured universe sizes, star range.
- **Fixed backtest leaks:**
  - The search filter on archived repos is dropped in backtests.
  - The current description and topics are replaced by README-at-T text.
  - Contributor follower counts are omitted.
  - A future-date guard is added.
  - It is explained why the lower bound on current stars is safe.
- **Replaced "stars as of T by paging" with a binary search** capped at 21 page jumps, and sized universe-build costs against GitHub rate limits.
- **Specified every tool:**
  - Args, output examples and error codes.
  - The surfacing rule.
  - `compute` with JSON Pointer inputs.
  - `finish` validation rules.
- **Made the agent loop concrete:** budget accounting, the wrap-up and forced-finish thresholds, compaction that preserves citations and prompt caching, and raised the budget from 400k to 800k tokens, with the reasoning.
- **Made grounding checks deterministic:** exact number-matching rules, JSON paths on key figures, and pass criteria for the planted-repo and perturbation tests.
- **Strengthened the backtest:**
  - Random-portfolio percentiles.
  - Spearman ρ with a permutation test.
  - The bear pass scored before and after.
  - A naive-LLM baseline.
  - A cutoff canary.
  - Two runs per topic.
  - A frozen outcome snapshot.
- **Added build contracts:**
  - The repo layout with ownership.
  - The event log and run artifacts.
  - The API endpoints.
  - Fixtures and an interface freeze.
  - Acceptance tests per track.
  - Full prompts.
- **Removed** `get_dependents` (no API, and nothing as of T) and the ClickHouse dataset (verified incomplete).
- **Flagged** that the OpenRouter key has no spend limit.
