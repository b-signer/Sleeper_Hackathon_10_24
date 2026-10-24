# T10 plan: agent loop, budget, run records

Coordinator plan for T10 ([agent.md](agent.md)). Two work packages, **W1** (engine) and **W2** (edges), are built in parallel in separate git worktrees, then merged by the coordinator. Contracts (T02, [`../contracts.md`](../contracts.md)) are frozen: T10 emits exactly those shapes. T03 is merged (main `5994d76`): use `sleeper.fakes` (`fake_universe()`, `browse_output`, `repo_details_output`, `readme_output`, `fake_run()`) and `fixtures/fake/*` as the fake data; don't invent new fixtures. MVP only. Where this plan does not fix something, the implementer decides.

## 1. Done-when checklist (T10)

| # | Item | Owner | Test |
|---|---|---|---|
| 1 | Loop calls the configured model via OpenRouter (OpenAI-compatible chat completions) with the tools | W2 client, W1 loop | `test_agent_openrouter.py`, live |
| 2 | Tool calls from one turn run concurrently; `submit` runs after the others in that turn | W1 | `test_parallel_tool_calls` |
| 3 | Narration (`narration`), reasoning (`reasoning`), `tool_call`, `tool_result` events recorded as they happen | W1 emits, W2 writes | `test_events_*` |
| 4 | No web: `:online` models, search models and `plugins`/`web_search_options` refused in code | W2 | `test_no_web_*` |
| 5 | Usage and cost taken from the provider's `usage` (request sets `"usage": {"include": true}`) | W2 | `test_parses_usage_and_cost` |
| 6 | Budget line shown to the model every turn (turn n/max, tokens used/left, $ so far, phase) | W1 | `test_budget_line_each_turn` |
| 7 | Wrap-up warning when budget runs low; at the end only `submit` is offered and forced | W1 | `test_budget_phases`, `test_submit_only_phase` |
| 8 | Spend guard: a run that would push total spend past $45 is refused before it starts (no run folder) | W2 | `test_spend_guard_*`, `test_cli_run_refused_by_spend_guard` |
| 9 | Statuses `ok`, `budget_exhausted`, `invalid`, `timeout`, `error`, incl. a stalled model | W1 | `test_status_*` |
| 10 | Run folder `runs/<run_id>/`: `settings.json`, `events.jsonl` written live (another process can read it mid-run), `result.json` | W2 | `test_records_*` |
| 11 | `sleeper run` command | W2 | `test_cli_run_*` |
| 12 | Scripted fake model drives a full run on fake data, offline, producing valid records | W1 model+script, W2 CLI `--fake` | `test_status_ok_full_run`, `test_cli_run_fake` |
| 13 | Offline end-to-end test (CLI `--fake` -> valid run folder) | W2 (enabled after merge) | `test_cli_run_fake` |

## 2. Layout and file ownership

New package `src/sleeper/agent/`. No file is edited by both packages.

| File | Owner | What |
|---|---|---|
| `src/sleeper/agent/__init__.py` | **shared, verbatim** | exactly the text in §3.1 |
| `src/sleeper/agent/types.py` | **shared, verbatim** | exactly the text in §3.2 (W1's copy wins on merge) |
| `src/sleeper/agent/loop.py` | W1 | `run_agent()` |
| `src/sleeper/agent/budget.py` | W1 | `Budget` (cumulative usage, phases, budget line) |
| `src/sleeper/agent/scripted.py` | W1 | `ScriptedModel`, `reply()`, `fake_script()` |
| `src/sleeper/agent/stub_tools.py` | W1 | `StubTools` (stand-in for T09), delegating to `sleeper.fakes` helpers |
| `src/sleeper/agent/tool_specs.py` | W1 | `tool_specs()`: OpenAI tool schemas from `TOOL_ARGS` (T09 reuses) |
| `src/sleeper/agent/prompt.py` | W1 | placeholder `system_prompt()` (T11 replaces the text) |
| `tests/test_agent_loop.py`, `tests/test_agent_budget.py`, `tests/test_agent_stub_tools.py` | W1 | |
| `src/sleeper/agent/openrouter.py` | W2 | `OpenRouterModel`, `assert_no_web()`, `WebAccessRefused`, `PRICES` |
| `src/sleeper/agent/spend.py` | W2 | `check_spend()`, `SpendGuardError`, `estimate_max_cost()`, `fetch_key_usage()`, `SPEND_GUARD_USD = 45.0` |
| `src/sleeper/agent/records.py` | W2 | `RunRecorder` (implements `EventSink`), `new_run_id()`, `read_events()` |
| `src/sleeper/agent/runner.py` | W2 | `execute_run()`, `load_universe()`: wires everything (CLI now, UI T15 later) |
| `src/sleeper/cli.py` | W2 only | `run` subcommand |
| `tests/test_agent_openrouter.py`, `tests/test_agent_spend.py`, `tests/test_agent_records.py`, `tests/test_cli_run.py`, `tests/test_agent_live.py` | W2 | |

Nobody touches `sleeper/fakes.py`, `fixtures/`, `pyproject.toml`/`uv.lock` (use `httpx`, already a dependency, for OpenRouter; `respx` is in dev deps for tests), `contracts/*`, `config.py`, `github/*`, or `docs/` (except: report learnings to the coordinator, who writes `decisions.md`). If you think a contract must change, don't change it: report it.

## 3. Interfaces

### 3.1 `src/sleeper/agent/__init__.py` (verbatim)

```python
"""Agent loop, budget and run records (T10). Tools (T09) plug in through sleeper.agent.types.ToolExecutor."""
```

### 3.2 `src/sleeper/agent/types.py` (verbatim; both packages create it byte-identical)

```python
"""Interfaces between the agent loop (T10), the model client and the tools (T09).

Frozen for T10 (docs/tickets/T10-plan.md). Change only by agreement with the coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sleeper.contracts import RunEvent, ToolOutput, Usage


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool call as the model sent it."""

    id: str  # provider's tool_call id, echoed back in the tool message
    name: str  # tool name as sent (may be unknown)
    arguments: str  # raw JSON string as sent (may be invalid JSON)


@dataclass(frozen=True)
class ModelReply:
    """One assistant turn."""

    content: str | None  # assistant text: the narration (F2.2)
    tool_calls: list[ToolCallRequest]
    usage: Usage  # this call only; cost_usd as reported by the provider
    reasoning: str | None = None  # reasoning summary, if the provider returned one
    finish_reason: str | None = None
    raw_message: dict[str, Any] = field(default_factory=dict)  # assistant message to append to history as is; {} = loop builds it


ModelErrorKind = Literal["timeout", "http", "auth", "bad_response"]


class ModelError(Exception):
    """A model call failed after the client's own retries. kind="timeout" covers a stalled model."""

    def __init__(self, kind: ModelErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class ChatModel(Protocol):
    model_id: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | dict[str, Any] = "auto",
        timeout_s: float = 120.0,
    ) -> ModelReply:
        """One chat completion in OpenAI message format. Raises ModelError."""
        ...


class ToolExecutor(Protocol):
    def tool_specs(self) -> list[dict[str, Any]]:
        """OpenAI-style `tools` array, one entry per tool in TOOL_ARGS."""
        ...

    def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolOutput:
        """Run one known tool. Model mistakes (bad args, repo not in universe, rejected submit) come back
        as ErrorOutput / SubmitOutput(ok=False), never as exceptions. Tracks submit attempts itself
        (SubmitOutput.attempts_left). Must be thread-safe: calls from one turn run concurrently."""
        ...


class EventSink(Protocol):
    run_id: str

    def emit(self, event_type: type[Any], /, **payload: Any) -> RunEvent:
        """Build the event (adds seq, ts=now UTC, run_id), record it immediately, return it. Thread-safe."""
        ...
```

### 3.3 W1 provides (consumed by W2's runner/CLI)

```python
# loop.py
def run_agent(
    settings: RunSettings,
    universe: Universe,
    model: ChatModel,
    tools: ToolExecutor,
    sink: EventSink,
    *,
    system_prompt: str | None = None,          # None -> prompt.system_prompt(settings, universe.header)
    git_sha: str | None = None,
    spend_headroom_usd: float | None = None,   # if run cost reaches this, go submit_only (spend guard mid-run)
    now: Callable[[], float] = time.monotonic, # injectable clock for timeout tests
) -> RunResult: ...
# Emits every event from run_started to run_finished through `sink`. Never raises for model/tool
# failures: they become status "error"/"timeout". Does not write files.

# scripted.py
ScriptStep = ModelReply | Callable[[list[dict[str, Any]]], ModelReply] | Exception
class ScriptedModel:                     # implements ChatModel; model_id = "scripted/fake"
    def __init__(self, steps: list[ScriptStep], model_id: str = "scripted/fake") -> None: ...
    calls: list[dict[str, Any]]          # one per complete(): {"messages", "tools", "tool_choice"} for assertions
def reply(text: str | None, *calls: tuple[str, dict[str, Any]], prompt_tokens: int = 3000,
          completion_tokens: int = 200, cost_usd: float | None = None) -> ModelReply: ...
def fake_script(universe: Universe) -> list[ScriptStep]: ...   # a full run that ends "ok" (see W1 steps)

# stub_tools.py
class StubTools:                                     # implements ToolExecutor; works on any Universe
    def __init__(self, universe: Universe, *, max_submit_attempts: int = 3) -> None: ...
# browse/repo_details/readme delegate to sleeper.fakes.browse_output / repo_details_output / readme_output

# tool_specs.py
def tool_specs(names: Iterable[str] | None = None) -> list[dict[str, Any]]: ...

# prompt.py
def system_prompt(settings: RunSettings, header: UniverseHeader) -> str: ...
```

### 3.4 W2 provides

```python
# openrouter.py
OPENROUTER_URL = "https://openrouter.ai/api/v1"
PRICES: dict[str, tuple[float, float]]   # model id -> (input, output) USD per M tokens; luna, terra (D4, D5)
class WebAccessRefused(ValueError): ...
def assert_no_web(model_id: str) -> None: ...        # raises WebAccessRefused for ":online", search models
class OpenRouterModel:                               # implements ChatModel
    def __init__(self, model_id: str, api_key: str | None, *, client: httpx.Client | None = None,
                 max_retries: int = 2) -> None: ...  # calls assert_no_web; missing key -> ModelError("auth")

# spend.py
SPEND_GUARD_USD = 45.0
class SpendGuardError(RuntimeError): ...
def fetch_key_usage(api_key: str, *, client: httpx.Client | None = None) -> float: ...  # GET /key -> data.usage (USD)
def local_spend(runs_dir: Path) -> float: ...        # sum of result.json usage.cost_usd (fallback ledger)
def estimate_max_cost(settings: RunSettings) -> float: ...
def check_spend(settings: RunSettings, cfg: Settings, *, spent_usd: float | None = None) -> float:
    """Returns headroom_usd = SPEND_GUARD_USD - spent. Raises SpendGuardError if spent + estimate > guard."""

# records.py
def new_run_id(topic: str, now: datetime | None = None) -> str: ...   # "YYYYMMDD-HHMMSS_<topic>"
class RunRecorder:                                   # implements EventSink
    @classmethod
    def create(cls, runs_dir: Path, settings: RunSettings, *, run_id: str | None = None,
               echo: Callable[[RunEvent], None] | None = None) -> RunRecorder: ...  # mkdir + settings.json
    run_id: str
    dir: Path
    def emit(self, event_type: type[Any], /, **payload: Any) -> RunEvent: ...  # append line + flush
    def write_result(self, result: RunResult) -> Path: ...                     # atomic result.json
    def close(self) -> None: ...
def read_events(path: Path) -> list[RunEvent]: ...   # tolerant of a partial last line (run in progress)

# runner.py
def load_universe(settings: RunSettings) -> Universe: ...
def execute_run(
    settings: RunSettings,
    *,
    fake: bool = False,                   # ScriptedModel(fake_script(u)) + StubTools on the fake universe, no network, no spend check
    cfg: Settings | None = None,          # default load_settings()
    model: ChatModel | None = None,
    tools: ToolExecutor | None = None,    # default StubTools(universe) until T09 lands
    universe: Universe | None = None,
    on_start: Callable[[str], None] | None = None,   # called with run_id once the folder exists (for T15)
    echo: Callable[[RunEvent], None] | None = None,
) -> RunResult: ...
# Order: web guard -> load universe -> spend guard -> RunRecorder.create -> run_agent -> write_result.
# Raises WebAccessRefused / SpendGuardError / FileNotFoundError before any run folder exists.
```

`load_universe`: `settings.universe_file` if set; topic `fake` -> `fixtures/fake/universe.json` (T03; `sleeper.fakes.fake_universe()` if the file is missing); otherwise `universes/<topic>.json` (T07/T08 may move this; one function to change). Missing file -> `FileNotFoundError` with a clear message.

W2 imports W1 modules **lazily inside `execute_run`**, so W2's worktree imports and tests run without W1's code.

## 4. Behaviour (shared understanding)

**Turn.** Check wall clock; `model.complete(messages, tools_for_phase, tool_choice, timeout_s=min(remaining_wall_clock, 120))`; add usage to budget; emit `narration` (non-empty content) and `reasoning` (if any); for each tool call emit `tool_call` (call ids `c_001`, `c_002`, … in order, run-unique), run them, emit `tool_result` as each finishes; append tool messages (`{"role": "tool", "tool_call_id", "content": output JSON, exclude_none}`); emit `usage` (cumulative; `tokens_remaining = token_budget - total_tokens`); emit `status` on phase changes; append the **budget line** as a user message for the next turn.

**Tool calls.** Non-submit calls of a turn run concurrently (thread pool), `submit` calls after them, sequentially. The loop itself handles: invalid JSON args -> `ErrorOutput(BAD_ARGS)`; any non-submit call in `submit_only` -> `ErrorOutput(BUDGET_EXHAUSTED)` without executing; an exception from `execute` -> `ErrorOutput(UPSTREAM)`; an unknown tool name (can't be an `ErrorOutput`, whose `tool` is a `ToolName`) -> error text to the model, a `status` event with the message, no `tool_result`. `approx_tokens = len(output_json) // 4`.

**Budget phases** (thresholds are W1's call; suggested): `researching` -> `wrapping_up` at ≤ 25% tokens left or ≤ 3 turns left (budget line tells the model to wrap up and submit) -> `submit_only` at ≤ 10% tokens left, last turn, or `spend_headroom_usd` reached: only the submit tool spec is sent, `tool_choice` forces `submit`. Hard stop when tokens ≥ budget or turns ≥ `max_turns`.

**Status** (first that applies):
- `ok`: a submit returned `ok=True`. The run ends right after it. Result gets cards (`ResultCard`), ranking, summary, `ranking_below_min`.
- `invalid`: submit attempts used up (`attempts_left == 0`, max 3), or the budget ran out after at least one rejected submit. `last_submit_errors` set.
- `budget_exhausted`: tokens or turns ran out with no submit attempted.
- `timeout`: wall clock passed `max_wall_clock_s` (checked before each model call; a `ModelError("timeout")` once the clock is past the limit also counts).
- `error`: any other `ModelError` or unexpected exception; a stalled model: a `ModelError("timeout")` within the wall clock, or 3 consecutive replies with no tool calls (the loop nudges after the first two: "call a tool; to finish, call submit").

Every run emits `run_started` first (settings, universe header, `git_sha`, `prompt_sha256`), `status` `starting`/`researching`, and `status` `finished` + `run_finished` last, and returns a valid `RunResult` whose `usage` equals the last `usage` event.

**Spend guard.** `spent` = OpenRouter `GET /api/v1/key` -> `data.usage`; if that fails, the local ledger (sum of `runs/*/result.json` costs) with a warning. Refuse if `spent + estimate_max_cost(settings) > 45`. Estimate: `token_budget` at the input price plus ~10% at the output price, unknown models priced as terra. Skipped for `fake`. `execute_run` passes `headroom = 45 - spent` to `run_agent` as `spend_headroom_usd`.

**No web.** `assert_no_web` refuses ids ending `:online` and ids containing `search` or `perplexity/`/`sonar`; the request body never contains `plugins` or `web_search_options`. Called in `OpenRouterModel.__init__` and at the top of `execute_run`.

**OpenRouter request.** `POST {OPENROUTER_URL}/chat/completions`, body `{model, messages, tools, tool_choice, parallel_tool_calls: true, usage: {include: true}}` (reasoning request params are W2's call). Parse `choices[0].message.content`, `.tool_calls`, `.reasoning` (or `reasoning_details` summaries), `finish_reason`, `usage.prompt_tokens/completion_tokens/total_tokens/cost`, `usage.prompt_tokens_details.cached_tokens`. `raw_message` = the returned message (keep `reasoning_details` so it is passed back). Retry 429/5xx/network up to `max_retries` with short backoff; read timeout -> `ModelError("timeout")`; 401/403 -> `"auth"`; other 4xx or exhausted retries -> `"http"`; 200 with `error` or no choices -> `"bad_response"`. Never log the key.

**Records.** `runs_dir` from `load_settings().runs_dir` (`SLEEPER_RUNS_DIR`, default `runs`). `events.jsonl`: one `dump_event` line per emit, written and flushed under a lock immediately. `result.json` written atomically (tmp + `os.replace`). If the run id folder exists, add a suffix (`-2`, …).

## 5. W1: engine (loop, budget, scripted model, stub tools)

**Goal:** `run_agent` drives a full run from settings to a status, with budget phases, concurrency and all event types, tested offline with a scripted model and stub tools.

**Owns:** `agent/__init__.py`, `agent/types.py` (verbatim), `agent/loop.py`, `agent/budget.py`, `agent/scripted.py`, `agent/stub_tools.py`, `agent/tool_specs.py`, `agent/prompt.py`, `tests/test_agent_loop.py`, `tests/test_agent_budget.py`, `tests/test_agent_stub_tools.py`.

**Consumes:** contracts; `types.py`; `sleeper.fakes` (`fake_universe()`, tool output helpers, and `fake_run()` as a reference for a realistic script and cards). Uses an in-test `ListSink` (implements `EventSink`, keeps events in a list, assigns seq/ts/run_id, round-trips each through `dump_event`/`parse_event`) instead of W2's `RunRecorder`.

**Steps:**
- [ ] `types.py`, `__init__.py` verbatim from §3.
- [ ] `tool_specs.py`: `{"type": "function", "function": {name, description, parameters}}` per tool from `TOOL_ARGS[...].model_json_schema()`; short descriptions. Make sure `$defs`/`$ref` schemas are accepted by OpenAI-style APIs (inline refs if in doubt).
- [ ] `stub_tools.py`: `StubTools`: validate args with `TOOL_ARGS` (-> `BAD_ARGS`), then browse/repo_details/readme via `sleeper.fakes.browse_output` / `repo_details_output` / `readme_output`; submit is the only new logic: validate `Submission`, check card repos are in the universe, cited call ids exist and were successful, `len(cards) <= max_picks`; track attempts. Thread-safe (lock around the outputs record). Keep it small: T09 replaces it.
- [ ] `prompt.py`: a short placeholder prompt (task, tools, cite call ids, memory is not a source, budget line meaning). T11 owns the real text.
- [ ] `budget.py`: cumulative `Usage`, turn count, phase, `line()`, `exhausted`.
- [ ] `scripted.py`: `ScriptedModel`, `reply()` (tool call ids like `call_1`; cost from luna prices, `sleeper.fakes.PRICE_PER_M`, if not given), `fake_script(u)` on `fake_universe()`, modelled on `fakes.fake_run()`'s turns: browse; browse with filters; several `repo_details` in parallel (incl. one repo outside the universe -> `NOT_IN_UNIVERSE`); `readme`s (incl. `mystery/unknown-cli` -> `NOT_FOUND`); a rejected submit; a valid submit with cards citing the real call ids (deterministic `c_001`… in order, so the script can hard-code them, or build cards from earlier tool messages via a callable step).
- [ ] `loop.py`: `run_agent` per §4.

**Acceptance tests (offline):**
- `test_status_ok_full_run`: `fake_script(fake_universe())` + `StubTools(fake_universe())` -> `status == "ok"`, 1–5 cards, ranking non-empty, `submit_attempts == 2`; events: seq contiguous from 0, all parse with `parse_event`, first `run_started`, last `run_finished(status="ok")`, every `tool_call` has a `tool_result` with the same `call_id`, one `usage` event per turn, `narration` texts equal the script's texts; `result.usage == last usage event's usage`; `RunResult.model_validate_json(result.model_dump_json())` round-trips.
- `test_parallel_tool_calls`: an executor whose `execute` waits on a `threading.Barrier(3, timeout=2)`; three calls in one turn all succeed (they could not if run serially).
- `test_submit_runs_after_other_calls`: submit + browse in one turn: browse's `tool_result` is emitted before submit's.
- `test_budget_line_each_turn`: every `complete()` call after the first has a user message with the budget line (turn n/max, tokens left).
- `test_budget_phases` (`test_agent_budget.py`): `Budget` moves researching -> wrapping_up -> submit_only at the thresholds; `exhausted` at budget; `line()` contains the numbers.
- `test_submit_only_phase`: large usage per turn -> a `status(phase="submit_only")` event; the next `complete()` got only the submit tool and a forced `tool_choice`; a browse call made then gets `ErrorOutput` code `BUDGET_EXHAUSTED`.
- `test_status_budget_exhausted`: script never submits, usage exceeds budget -> `budget_exhausted`.
- `test_status_turns_exhausted`: `max_turns=3`, no submit -> `budget_exhausted`.
- `test_status_invalid`: three rejected submits -> `invalid`, `last_submit_errors` non-empty, `submit_attempts == 3`.
- `test_status_timeout`: injected `now` jumps past `max_wall_clock_s` -> `timeout`.
- `test_status_error_model_error`: a `ModelError("http", ...)` step -> `error`, `result.error` set, `run_finished` still emitted.
- `test_status_error_stalled`: (a) `ModelError("timeout")` within the wall clock -> `error`; (b) 3 replies with no tool calls -> `error` mentioning stalled, with nudges in between.
- `test_bad_json_args`: arguments `"{not json"` -> `tool_result` with `ErrorOutput` `BAD_ARGS`; run continues to `ok`.
- `test_unknown_tool`: name `web_search` -> `status` event naming it, run continues.
- `test_spend_headroom_forces_submit_only`: `spend_headroom_usd=0.001` with cost per turn above it -> `submit_only`.
- `test_stub_tools_*` (small): outputs for `acme/quiver` equal `sleeper.fakes.canned_tool_outputs()` apart from `call_id`; deep tools reject repos outside the universe; bad args -> `BAD_ARGS`; submit rejects a citation to a missing or failed call id and too many picks; `tool_specs()` has 4 entries named like `TOOL_ARGS`.

## 6. W2: edges (OpenRouter, guards, records, runner, CLI)

**Goal:** Everything around the loop: the real model client, the no-web and spend guards, the run folder, the `execute_run` entry point and `sleeper run`.

**Owns:** `agent/__init__.py`, `agent/types.py` (verbatim), `agent/openrouter.py`, `agent/spend.py`, `agent/records.py`, `agent/runner.py`, `src/sleeper/cli.py` (`run` subcommand only), `tests/test_agent_openrouter.py`, `tests/test_agent_spend.py`, `tests/test_agent_records.py`, `tests/test_cli_run.py`, `tests/test_agent_live.py`.

**Consumes:** contracts, `config.load_settings`, `types.py`; `sleeper.fakes` / `fixtures/fake/` (fake universe; the recorded run `fixtures/fake/runs/20261024-140000_fake/` as test input for `read_events`); W1's `run_agent`, `ScriptedModel`, `fake_script`, `StubTools` (lazy imports in `execute_run`; tests needing them use `pytest.importorskip("sleeper.agent.loop")` so they skip in W2's worktree and run after merge).

**Steps:**
- [ ] `types.py`, `__init__.py` verbatim from §3.
- [ ] `openrouter.py` per §4 (httpx, sync). Optional `HTTP-Referer`/`X-Title` headers.
- [ ] `spend.py` per §4.
- [ ] `records.py`: `new_run_id`, `RunRecorder` (lock, append + flush per event, optional `echo`), `write_result`, `read_events`.
- [ ] `runner.py`: `load_universe`, `execute_run` per §3.4; `git_sha` from `git rev-parse --short HEAD` if available.
- [ ] `cli.py`: `sleeper run --topic T [--universe PATH] [--model M (default cfg.dev_model)] [--max-picks N] [--min-ranked N] [--token-budget N] [--max-turns N] [--max-wall-clock S] [--fake] [--quiet]`. `--fake` sets model `scripted/fake`. Prints narration/tool lines live via `echo` unless `--quiet`, then the run folder and status. Exit 0 if `ok`, 1 for other statuses, 2 if refused (web, spend, universe missing) with a one-line reason.

**Acceptance tests (offline unless marked):**
- `test_no_web_refuses_online_and_search_models`: `:online`, `perplexity/sonar`, `openai/gpt-4o-search-preview` raise `WebAccessRefused`; luna and terra pass; `OpenRouterModel("x:online", "k")` raises.
- `test_request_body` (respx): body has `model`, `tools`, `tool_choice`, `parallel_tool_calls`, `usage.include is True`, and no `plugins`/`web_search_options`; `Authorization: Bearer` header set.
- `test_parses_usage_and_cost`: canned response with 2 tool calls, content, `reasoning`, usage incl. `cost` and `prompt_tokens_details.cached_tokens` -> `ModelReply` fields and `Usage` match exactly.
- `test_retries_429_then_ok`, `test_5xx_exhausted_is_http_error`, `test_read_timeout_is_timeout_error`, `test_200_with_error_body_is_bad_response`, `test_missing_key_is_auth_error`.
- `test_spend_guard_refuses_past_45`: `check_spend(..., spent_usd=44.9)` raises `SpendGuardError`; `spent_usd=10` returns headroom 35.
- `test_estimate_max_cost`: terra > luna; unknown model priced as terra.
- `test_fetch_key_usage` (respx) parses `data.usage`; `test_spend_falls_back_to_local_ledger`: fetch fails -> sum of `result.json` costs in a tmp runs dir.
- `test_records_layout`: `create` writes `settings.json` (valid `RunSettings`); run id matches `^\d{8}-\d{6}_<topic>(-\d+)?$`; collision gets a suffix.
- `test_records_live_readable`: after each `emit`, a **separate process** (`subprocess.run([sys.executable, "-c", ...])`) reading `events.jsonl` sees every line so far, each parsing with `parse_event`.
- `test_records_thread_safe`: 200 emits from 4 threads -> seq 0..199, every line intact.
- `test_read_events_recorded_fake_run`: `read_events` on T03's recorded `events.jsonl` returns every event; `test_read_events_tolerates_partial_line`; `test_write_result_atomic_and_valid`.
- `test_load_universe`: topic `fake` -> the T03 universe (`header.topic == "fake"`); explicit `universe_file` is used; unknown topic -> `FileNotFoundError`.
- `test_execute_run_refuses_before_folder`: spend guard (monkeypatched `fetch_key_usage` -> 44.99) and web guard both raise and leave `runs_dir` empty.
- `test_cli_run_help`: `sleeper run --help` lists the options.
- `test_cli_run_refused_by_spend_guard`: exit code 2, message mentions the guard, no run folder.
- `test_cli_run_fake` (`importorskip("sleeper.agent.loop")`): `SLEEPER_RUNS_DIR=tmp_path`, `main(["run", "--topic", "fake", "--fake", "--quiet"]) == 0`; the run folder has `settings.json`, `events.jsonl` (all lines parse, last is `run_finished` ok), `result.json` (valid `RunResult`, status `ok`, ≥ 1 card). This is the offline end-to-end test.
- `@pytest.mark.live test_openrouter_live_tool_call`: luna, one tiny tool, short prompt asking to call it -> ≥ 1 tool call parsed, `usage.cost_usd > 0`, `total_tokens > 0`. Costs a fraction of a cent.
- `@pytest.mark.live test_fetch_key_usage_live`: returns a float ≥ 0.

## 7. Coordinator integration (after both report)

1. Review each branch's diff against its ownership list; check `types.py` and `__init__.py` are byte-identical to §3 (`git diff w1 w2 -- src/sleeper/agent/types.py`).
2. Merge **W1 first**, then **W2** into a T10 integration branch off `main` (`5994d76`). Only W2 touches `cli.py` (adding `run` next to T03's `fakes`), so the only possible conflict is the verbatim shared files: take W1's.
3. `uv run pytest -q` (now including `test_cli_run_fake`). Then `uv run sleeper run --topic fake --fake` and inspect `runs/<id>/` (tail `events.jsonl` from another shell while it runs).
4. Live: `uv run pytest -m live -k agent`, then one real run on luna with stub tools: `uv run sleeper run --topic fake --model openai/gpt-5.6-luna --token-budget 60000 --max-turns 8` (cents). Check: tool schemas accepted, parallel calls, narration present, cost from usage, budget line visible, status sensible.
5. Review (bugs, contract conformance, no key in logs or records), fix small issues directly, add learnings to `decisions.md`.
6. When T09 lands: `execute_run` defaults `tools` to T09's executor (implements `ToolExecutor`, can reuse `tool_specs()`); `StubTools` stays for tests. Tell T14/T15 owners: `execute_run(..., on_start=...)` is the entry point to launch a run from the UI, and `read_events` tails a live run.
