# Contracts (T02)

The data shapes that cross ticket boundaries, defined once in [`src/sleeper/contracts/`](../src/sleeper/contracts/) as pydantic v2 models. **Frozen at T02.** To change one, follow C5: edit the model and this page in the same commit, and tell the affected owners.

```python
from sleeper.contracts import Universe, Submission, RunResult, parse_event
u = Universe.model_validate_json(path.read_text())   # validate a file
```

Rules for every model: unknown fields are rejected; timestamps carry a timezone (ISO 8601, `Z`); `None` means unknown, never guessed; repos are `owner/name`; tool output IDs are `c_001`, `c_002`, … JSON Schemas: `uv run sleeper schema --out schemas/`.

## Run settings (`settings.py`)

`RunSettings`: `topic`, `universe_file`?, `stars_min` (50), `stars_max` (2000), `created_after` (2025-01-01), `max_picks` (5, range 1–5), `min_ranked` (10), `model`, `token_budget` (300k), `max_turns` (25), `max_wall_clock_s` (600), `seed` (7).

## Universe file (`universe.py`)

One JSON file: `Universe = {header, rows}`. Checks: no duplicate repos, `header.counts.rows == len(rows)`. `universe.get("owner/name")` is a case-insensitive lookup.

- `UniverseHeader`: `schema_version` (1), `topic`, `topic_label`, `queries` (GitHub searches used), `stars_min`, `stars_max`, `created_after`, `created_before`?, `built_at` (metrics are as of this time), `builder` (version and git SHA), `seed`, `max_size` (200), `github_requests`?, `build_seconds`?, `scoring` (one paragraph on how the scores are computed), `counts`.
- `UniverseCounts`: `search_total`, `excluded` (reason → count, e.g. `fork`, `archived`, `awesome_list`), `eligible`, `sampled`, `rows`.
- `UniverseRow`: `repo`, `repo_id`, `url`, `metrics`, `scores`.
- `RepoMetrics` (T06): `stars`, `stars_gained_30d`, `commits_90d`, `authors_90d`, `releases`, `last_release_at`, `last_commit_at`, `readme_chars`, `created_at`, `age_days`, `description`, `language`, `license`. Only `stars`, `created_at` and `age_days` are required.
- `CriteriaScores`: `popularity`, `momentum`, `quality`, `newness`, each 0–100. Higher is better, computed by code (D8).

## Agent tools (`tools.py`)

Every output starts with `call_id` and `tool`. `TOOL_ARGS` maps each tool name to its argument model (to generate the model-facing tool schemas). `TOOL_OUTPUT_ADAPTER` parses any output.

| Tool | Args | Output |
|---|---|---|
| `browse` | `filters` [{`field`, `op` (`> >= < <= ==` or `between` with [lo, hi]), `value`}], `keyword`?, `language`?, `sort_by` (a field or `random`), `sort_dir`, `limit` (1–50, 25), `offset` | `BrowseOutput`: `total`, `offset`, `rows` [{`repo`, `description` (≈120 chars), `language`, `stars`, `scores`, `extra` (values of other fields used in filters/sort)}] |
| `repo_details` | `repo` | `RepoDetailsOutput`: `repo`, `url`, `metrics`, `scores` |
| `readme` | `repo`, `max_chars` (500–12000, 6000) | `ReadmeOutput`: `repo`, `text`, `truncated`, `total_chars` |
| `submit` | a `Submission` (below) | `SubmitOutput`: `ok`, `errors` (specific messages, non-empty iff not ok), `attempts_left` |

Filter and sort fields (`ScreenField`): `stars`, `stars_gained_30d`, `commits_90d`, `authors_90d`, `releases`, `readme_chars`, `age_days`, `popularity`, `momentum`, `quality`, `newness`.

**Errors:** `ErrorOutput` = {`call_id`, `tool`, `error`: {`code`, `message`}}. Codes: `BAD_ARGS`, `NOT_IN_UNIVERSE`, `NOT_FOUND`, `UPSTREAM`, `BUDGET_EXHAUSTED`. Error outputs can't be cited.

## Pick card and submission (`cards.py`)

- `Card`: `repo`, `pitch` (one line, ≤200), `criteria` (all four), `case` (1–4 `Claim`), `risks` (0–3 `Risk`), `proven_wrong_if` (1–2 signals), `confidence` (0–1). `card.cited_call_ids()` lists every ID it cites.
- `CriterionRating`: `score`, `score_call_id` (the output showing it), `key_figure`.
- `KeyFigure`: `label`, `value`, `unit`?, `source_call_id`, `source_path` (JSON Pointer into that output, e.g. `/metrics/stars`).
- `Claim`: `text`, `source_call_ids` (at least 1). `Risk`: same, but citations may be empty for a non-factual risk.
- `Submission`: `cards` (1–5), `ranking` [{`rank`, `repo`, `note`?}], `summary` (≤800). Checks: ranks are exactly 1..n, no duplicates, every card's repo is ranked.

The model only checks shape. The submit tool (T09) checks context: repos in the universe, citations to existing successful calls, `max_picks`, and every repo looked at closely is ranked.

## Run records (`run.py`)

`runs/<run_id>/` holds `settings.json` (`RunSettings`), `events.jsonl` (one event per line) and `result.json` (`RunResult`). `run_id` is `YYYYMMDD-HHMMSS_<topic>`. Use `dump_event` / `parse_event` for lines.

Every event has `seq` (0, 1, 2, …), `ts`, `run_id`, `type`:

| `type` | Payload |
|---|---|
| `run_started` | `settings`, `universe` (the header), `git_sha`?, `prompt_sha256`? |
| `status` | `phase` (`starting`, `researching`, `wrapping_up`, `submit_only`, `reviewing`, `fact_checking`, `finished`), `message`? |
| `narration` | `turn`, `text` (the live trace, F2.2) |
| `reasoning` | `turn`, `text` (provider reasoning summary, if any) |
| `tool_call` | `turn`, `call_id`, `tool`, `args` (raw, as the model sent them) |
| `tool_result` | `turn`, `call_id`, `ok`, `output` (full tool output), `approx_tokens`, `duration_ms`?, `cache_hit`? |
| `usage` | `turn`, `max_turns`, `usage`, `token_budget`, `tokens_remaining` (cumulative, for the budget meter) |
| `skeptic_review` | `repo`, `review` (T12) |
| `fact_check` | `repo`, `flags` (T13) |
| `run_finished` | `status`, `error`? |

- `Usage`: `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens`, `cost_usd`.
- `RunResult`: `schema_version`, `run_id`, `status` (`ok`, `budget_exhausted`, `invalid`, `timeout`, `error`), `error`?, `settings`, `started_at`, `finished_at`, `turns`, `usage` (including review calls), `cards` [`ResultCard`], `ranking`, `summary`?, `ranking_below_min`, `submit_attempts`, `last_submit_errors`.
- `ResultCard` = `Card` + `skeptic`? + `fact_flags` (both filled in after submission, never by the agent).
- `SkepticReview`: `verdict` (`survives`, `weakened`, `killed`), `argument`, `cited_call_ids`, `model`?.
- `FactCheckFlag`: `card_path` (e.g. `case[0].text`), `shown`, `source_call_id`?, `source_path`?, `found`?, `message`.
