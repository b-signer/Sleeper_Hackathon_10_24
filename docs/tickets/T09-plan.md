# T09 plan: agent tools

Coordinator plan for T09 ([agent.md](agent.md)). Two work packages are built in parallel in separate git worktrees off main (`06ef6a3`):

- **W1, data tools:** browse, repo_details, readme.
- **W2, submit:** dispatch, call bookkeeping and submission validation.

The coordinator then merges them and makes the real tools the default. Contracts (T02, [`../contracts.md`](../contracts.md)) are frozen. The T10 interfaces (`sleeper.agent.types.ToolExecutor`, `tool_specs()`) are fixed. MVP only. Where this plan is silent, the implementer decides.

## 1. Done-when checklist (T09)

| # | Item | Owner | Test | Done |
|---|---|---|---|---|
| 1 | Browse filters and sorts by any criterion score or metric; ≤ 50 rows plus a total count (delegates to `fakes.browse_output`, which works on any universe) | W1 | `test_browse_*` | ✅ |
| 2 | Repo details: full metrics and scores for one repo | W1 | `test_repo_details_*` | ✅ |
| 3 | README: text capped at `max_chars` (500–12000, default 6000); real universes fetch through the T05 cached client; fake universe uses T03's fake READMEs; no README -> `NOT_FOUND` | W1 | `test_readme_*` | ✅ |
| 4 | Deep tools reject repos outside the universe (`NOT_IN_UNIVERSE`) | W1 | `test_*_not_in_universe` | ✅ |
| 5 | Submit rejects, with a specific message: contract failure; repo outside the universe; citation to a missing, failed or submit call; key figure or score not matching its cited output; ranking missing a card repo or a repo researched in depth; too many picks | W2 | `test_check_*` | ✅ |
| 6 | Unique output ids that cards cite: the loop assigns `c_NNN` (T10); the toolbox records every output by id | W2 | `test_toolbox_records_outputs` | ✅ |
| 7 | Retries: 3 submit attempts, with `attempts_left` counting down | W2 | `test_toolbox_submit_attempts` | ✅ |
| 8 | Outputs are compact; approximate token sizes stated (target: research ~15 repos within 300k) | W1 measures, coordinator logs | `test_output_sizes` | ✅ |
| 9 | Tests run on fake data; the same code works on real universes with no change | W1, W2, coordinator | all; integration step 5 | ✅ |
| 10 | Real tools are the default in `execute_run`; `sleeper run --fake` and the T14 server still work | coordinator | integration | ✅ |

Integrated on `feat/t09-agent-tools` (W1 + W2 merged cleanly; 242 offline tests pass). `execute_run` now defaults to `Toolbox(universe, DataTools(universe), max_picks=...)`. The fake run, the T14 server (in-process, with `execute_run` as its runner) and a live luna run all end `ok`, and luna's submit was accepted first time. The token estimate and the T11 advice are in the decisions learnings log.

## 2. Shared interface (decided by the coordinator)

**One real executor:** `Toolbox` (W2) implements `ToolExecutor`. It owns:
- dispatch by tool name
- argument parsing with `TOOL_ARGS` (bad args -> `BAD_ARGS`)
- the call bookkeeping: every output by call_id, the repos researched in depth, and submit attempts. Only outputs that pass through `Toolbox.execute` are recorded. ErrorOutputs the loop builds itself (invalid JSON args, `BUDGET_EXHAUSTED` in submit-only, "the run is over"; see `loop.py` `run_tools`) never reach `Toolbox.outputs`, so citing one gets "does not exist". That is expected: errors can't be cited either way.
- submit validation, delegated to `check_submission`

It calls W1's `DataTools` for the three data tools. `DataTools` is stateless apart from caches: it gets **already-parsed** args and returns outputs. It knows nothing about the history. For now, browse and repo_details delegate to `sleeper.fakes.browse_output` / `repo_details_output`. They already work on any `Universe`; porting them into the real module is post-hackathon cleanup.

```
loop (T10) --execute(name, args, call_id)--> Toolbox (W2)
                                              |- browse/repo_details/readme --> DataTools (W1) --> ReadmeSource (W1) --> GitHubClient (T05) | sleeper.fakes
                                              `- submit --> check_submission(args, SubmitContext) (W2)
```

### 2.1 W1 provides: `src/sleeper/agent/data_tools.py`

```python
README_PATH = "/repos/{repo}/readme"          # request shape as one constant pair, so it can follow T06's (§7.2)
README_ACCEPT = RAW_ACCEPT                       # from sleeper.github

ReadmeSource = Callable[[UniverseRow], str | None]
# Full README text ("" for an empty README), or None if the repo has no README (404).
# Raises ReadmeUnavailable if it can't be fetched.

class ReadmeUnavailable(Exception): ...          # offline cache miss, GitHub error, rate limit, no token

def fake_readme_source() -> ReadmeSource: ...    # sleeper.fakes.fake_readme
def github_readme_source(client: GitHubClient | None = None) -> ReadmeSource: ...
def default_readme_source(universe: Universe) -> ReadmeSource: ...   # header.topic == "fake" -> fake, else github

class DataTools:
    def __init__(self, universe: Universe, *, readme_source: ReadmeSource | None = None) -> None: ...
    # readme_source=None -> default_readme_source(universe), created lazily under a lock on the first readme
    # (no GitHub client until then, and never two clients)
    def browse(self, args: BrowseArgs, call_id: str) -> BrowseOutput: ...
    def repo_details(self, args: RepoArgs, call_id: str) -> RepoDetailsOutput | ErrorOutput: ...
    def readme(self, args: ReadmeArgs, call_id: str) -> ReadmeOutput | ErrorOutput: ...
```

Thread-safe: the loop runs one turn's calls concurrently. Model mistakes come back as `ErrorOutput` (`NOT_IN_UNIVERSE`, `NOT_FOUND`, `UPSTREAM`), never as exceptions.

### 2.2 W2 provides: `src/sleeper/agent/toolbox.py` and `src/sleeper/agent/submit_check.py`

```python
# toolbox.py
class DataToolsLike(Protocol):                   # structural: W1's DataTools matches it, tests can fake it
    def browse(self, args: BrowseArgs, call_id: str) -> BrowseOutput: ...
    def repo_details(self, args: RepoArgs, call_id: str) -> RepoDetailsOutput | ErrorOutput: ...
    def readme(self, args: ReadmeArgs, call_id: str) -> ReadmeOutput | ErrorOutput: ...

class Toolbox:                                   # implements sleeper.agent.types.ToolExecutor
    def __init__(self, universe: Universe, data: DataToolsLike, *, max_picks: int = 5,
                 max_submit_attempts: int = 3) -> None: ...
    outputs: dict[str, ToolOutput]               # every output by call_id, errors and submits included
    researched: dict[str, tuple[str, str]]       # lower-case repo -> (canonical repo, call_id) of its successful
                                                 # repo_details/readme; if several, the numerically smallest call_id
    submit_attempts: int
    def tool_specs(self) -> list[dict[str, Any]]: ...   # sleeper.agent.tool_specs.tool_specs()
    def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolOutput: ...

# submit_check.py
@dataclass(frozen=True)
class SubmitContext:
    universe: Universe
    max_picks: int
    outputs: Mapping[str, ToolOutput]
    researched: Mapping[str, tuple[str, str]]     # as Toolbox.researched

def check_submission(args: dict[str, Any], ctx: SubmitContext) -> tuple[Submission | None, list[str]]: ...
# (submission, []) if valid, else (None or the parsed submission, [specific messages]); at most MAX_ERRORS (20)
def resolve_pointer(doc: Any, pointer: str) -> Any: ...   # RFC 6901 on output.model_dump(mode="json"); KeyError if absent
```

Behaviour of `execute`:
- The lock is held only while recording, never during a data tool call, so calls stay parallel. Submit runs under the lock; the loop already runs it after the other calls of the turn.
- An unknown name raises `ValueError`; the loop filters unknown names before calling.
- For browse, repo_details and readme only: args that fail `TOOL_ARGS[name]` validation return `ErrorOutput(BAD_ARGS)` naming the field.
- **Submit always returns a `SubmitOutput`, never `BAD_ARGS`.** The loop counts attempts and ends the run at `attempts_left == 0` only on a `SubmitOutput` (`loop.py` `run_tools`). So the raw args go straight to `check_submission`, and a shape error is an ordinary rejection that uses up an attempt (rule 1). If attempts are already used up, return `ok=False`, `["no submit attempts left"]`, `attempts_left=0`; otherwise count the attempt and run `check_submission`.

## 3. File ownership (no shared files)

| File | Owner |
|---|---|
| `src/sleeper/agent/data_tools.py`, `tests/test_data_tools.py` | W1 |
| `src/sleeper/agent/toolbox.py`, `src/sleeper/agent/submit_check.py`, `tests/test_toolbox.py`, `tests/test_submit_check.py` | W2 |
| `src/sleeper/agent/runner.py`, `scripted.py`, `stub_tools.py`, `tool_specs.py` (`DESCRIPTIONS["submit"]`: the score/key-figure copy rules), `docs/*` | coordinator, at integration only |

Workers touch nothing else, including `contracts/*`, `agent/types.py`, `agent/tool_specs.py`, `agent/loop.py`, `fakes.py`, `fixtures/`, `github/*`, `server/*`, `cli.py`, `pyproject.toml` and `uv.lock`. If you think one must change, report it instead.

## 4. W1: data tools

**Goal:** browse, repo_details and readme over any `Universe`. READMEs come from GitHub (cached, offline-capable) for real universes and from T03's fake READMEs for the fake universe.

**Steps:**
- [x] **browse / repo_details (scope cut):** delegate to `sleeper.fakes.browse_output` / `repo_details_output`. They already work on any `Universe` (filters, `between`, keyword, language, unknowns sorted last, seeded `random`, offset/limit, `extra`, ~120-char descriptions; case-insensitive lookup, canonical name, `NOT_IN_UNIVERSE`). Don't port them now.
- [x] **readme:**
  - not in the universe -> `NOT_IN_UNIVERSE`
  - `metrics.readme_chars == 0` -> `NOT_FOUND` without a request
  - the source returns None (404) -> `NOT_FOUND`; a 2xx with an empty body is `""`, a valid empty README, not `NOT_FOUND`
  - `ReadmeUnavailable` -> `UPSTREAM` with a short reason (e.g. "README not in the offline cache")
  - otherwise `text = full[:max_chars]`, `truncated`, `total_chars = len(full)`
  - the text is served raw, with no cleaning
- [x] **github_readme_source:** `client.get(README_PATH.format(repo=row.repo), accept=README_ACCEPT)` returns the text (`None` body on a 2xx -> `""`). `NotFound` -> None. Any other `GitHubError` (`OfflineCacheMiss`, `MissingTokenError`, `RateLimitExceeded`, HTTP) -> `ReadmeUnavailable`. The default client is `GitHubClient(max_wait=30, max_attempts=2, timeout=10)`, so a rate limit or slow GitHub can't hang a run (it reads token, offline flag and cache dir from env as usual). Serialize calls with a lock: the client builds its httpx client lazily and is not obviously thread-safe. Cache hits are fast anyway.
- [x] Measure output sizes and state approximate tokens (chars/4) in your report: browse with 10/25/50 rows on a 200-row synthetic universe, repo_details, readme at default `max_chars`.

**Acceptance tests (`tests/test_data_tools.py`, offline):**
- `test_browse_and_details_delegate`: one equality check each: `DataTools(u).browse(args, "c_001") == fakes.browse_output(u, args, "c_001")` for a filtered+sorted args set, and `repo_details` for `ACME/Quiver` equals `fakes.repo_details_output` (canonical name). An outside repo -> `NOT_IN_UNIVERSE`.
- `test_readme_fake`: text equals `fakes.fake_readme(row)[:max_chars]`; `truncated`/`total_chars` right at `max_chars=500` and 12000; `mystery/unknown-cli` -> `NOT_FOUND`; an outside repo -> `NOT_IN_UNIVERSE`.
- `test_readme_github_ok`, `test_readme_github_empty_body_is_empty_text`, `test_readme_github_404_is_not_found`, `test_readme_offline_cache_miss_is_upstream`, `test_readme_offline_served_from_cache`: use a `GitHubClient` with a tmp `cache_dir` and an `httpx.MockTransport` or respx; for the cache test, fetch once online into the tmp cache, then build an offline client on the same cache and read without network.
- `test_readme_zero_chars_no_request`: a real-style row with `readme_chars=0` -> `NOT_FOUND` and the source is never called.
- `test_default_readme_source`: fake universe -> fake source; a copy of its header with `topic="rust-devtools"` -> github source, and no client or network until first use.
- `test_lazy_source_created_once`: 8 threads' first `readme` calls on a `DataTools` with `readme_source=None` create the source exactly once (count the factory calls).
- `test_parallel_readmes`: 8 threads calling `readme` on the fake universe all succeed.
- `test_output_sizes`: build a 200-row synthetic universe (fake rows renamed `owner{i}/repo{i}`, ~120-character descriptions, header counts fixed). Check `browse(limit=50)` JSON ≤ 16k characters, `repo_details` ≤ 1.5k, and `readme` at the default `max_chars` ≤ 6.3k.
- `@pytest.mark.live test_readme_live`: one real README (e.g. `astral-sh/uv`) via `github_readme_source()`, non-empty.

## 5. W2: toolbox and submit validation

**Goal:** the real `ToolExecutor`: dispatch, bookkeeping and strict, specific submission checks.

**Validation rules (`check_submission`)**, in order. Collect every error, don't stop at the first. Prefix errors with where they are, e.g. `cards[0] (acme/quiver) criteria.momentum.key_figure: ...`.
1. **Shape:** `Submission.model_validate(args)`; on failure return pydantic errors as `"<loc>: <msg>"` lines and stop.
2. **Picks:** `len(cards) <= max_picks` -> `"too many picks: 6 cards, at most 5"`.
3. **Universe:** every card repo and ranking repo is in the universe (case-insensitive) -> `"x/y is not in the universe"`.
4. **Citations:** every id in `card.cited_call_ids()` exists in `outputs`, is not an `ErrorOutput` and not a `SubmitOutput` -> `"cites c_008, which failed (NOT_IN_UNIVERSE); error outputs can't be cited"`, `"cites c_099, which does not exist"`, or `"cites c_013, a submit output"`.
5. **Scores:** for each criterion, `score_call_id` must be a `RepoDetailsOutput` for the card's repo or a `BrowseOutput` with a row for it, and `score` must equal that output's score within `SCORE_ABS_TOL = 0.5`, so a model that rounds 81.2 to 81 passes; each rejection costs an attempt and ~25k tokens -> `"criteria.momentum.score 80 does not match 72.5 in c_004"`.
6. **Key figures:**
   - `resolve_pointer(output.model_dump(mode="json"), source_path)` must exist and be a number (not bool, not null) -> `"no number at /metrics/forks_30d in c_004 (null)"`.
   - The value it points into must belong to the card's repo: for repo_details/readme outputs, `output.repo` must be the card repo; for browse, the pointer must be under `/rows/<i>/` and `rows[i].repo` must be the card repo.
   - `value` must match within the key figure tolerance (§7, assumption 4) -> `"value 15 does not match 12 at /metrics/forks_30d in c_004; copy the number as it appears at source_path, in the source's unit (no k/months conversion)"`.
7. **Ranking:** the contract already checks that ranks are 1..n, there are no duplicates, and card repos are ranked. Also check every repo in `researched` is ranked -> `"ranking: missing fernwork/cargo-sprout, which you looked at closely (c_005)"` (exact wording of T03's recorded run; canonical repo spelling). Ranking fewer than `min_ranked` repos is **not** an error (the result flags it).
8. Dedupe errors and cap them at `MAX_ERRORS = 20` (append `"... and N more"`).

Claim and risk citations are checked only by rule 4: a claim may cite another repo's output (comparisons). Numbers inside claim text are T13's job, not T09's.

**Steps:**
- [x] `submit_check.py`: `SubmitContext`, `resolve_pointer`, `check_submission`, constants.
- [x] `toolbox.py`: `Toolbox` per §2.2. Record into `researched` only for successful `RepoDetailsOutput`/`ReadmeOutput`: `(output.repo, call_id)`, keeping the numerically smallest call_id when calls finish out of order.
- [x] Until W1 lands, test against an in-test `FakesData` adapter whose three methods call `sleeper.fakes.browse_output`/`repo_details_output`/`readme_output`.

**Acceptance tests (offline):**
- `tests/test_toolbox.py`:
  - `test_toolbox_is_tool_executor`: `tool_specs()` equals `sleeper.agent.tool_specs.tool_specs()`.
  - `test_toolbox_bad_args`: browse with `limit=500`, and repo_details with no repo, -> `BAD_ARGS` naming the field.
  - `test_toolbox_submit_bad_shape_is_submit_output`: submit with `{"cards": []}` -> a `SubmitOutput(ok=False)` (never `ErrorOutput`) with `attempts_left == 2`.
  - `test_toolbox_records_outputs`: every executed output is in `outputs` by call_id.
  - `test_toolbox_researched`: successful repo_details/readme add `(canonical repo, call_id)`, keeping the smallest call_id even when `c_010` is recorded before `c_009`; browse, `NOT_FOUND` and `NOT_IN_UNIVERSE` don't.
  - `test_toolbox_submit_attempts`: 3 rejected submits -> `attempts_left` 2, 1, 0; a 4th -> "no submit attempts left", `attempts_left=0`.
  - `test_toolbox_parallel`: 8 concurrent executes all recorded.
  - `test_toolbox_runs_fake_script`: `run_agent` with `fake_script(fake_universe())`, `Toolbox(u, FakesData(u))` and an in-memory sink (copy `ListSink` from `tests/test_agent_loop.py` into your test file, or use `RunRecorder.create(tmp_path, settings)`) -> status `ok`. This proves T10's scripted run passes the strict validator.
- `tests/test_submit_check.py`, built from real outputs (e.g. run the needed tools through a `Toolbox` first, or follow `fakes.fake_run()`'s card helpers):
  - `test_check_accepts_valid`
  - `test_check_shape_error`
  - `test_check_too_many_picks`
  - `test_check_repo_not_in_universe`
  - `test_check_missing_citation`, `test_check_cites_error_output`, `test_check_cites_submit_output`
  - `test_check_score_mismatch` (off by more than 0.5), `test_check_score_rounded_ok` (81.2 shown as 81), `test_check_score_from_browse_row_ok`
  - `test_check_key_figure_mismatch` (the message includes the copy hint), `test_check_key_figure_rounding_ok` (e.g. `stars_per_day` 2.43 shown as 2.4)
  - `test_check_key_figure_bad_pointer`, `test_check_key_figure_null`, `test_check_key_figure_other_repo`
  - `test_check_ranking_missing_researched`
  - `test_check_messages_are_specific`: each message names the card/field, the call id and the expected value where relevant
  - `test_check_recorded_fake_run`: rebuild `SubmitContext` from T03's `fixtures/fake/runs/20261024-140000_fake/events.jsonl` (outputs from `tool_result` events before each submit, `researched` from successful repo_details/readme). The first submit's args (from its `tool_call` event) are rejected with exactly `"ranking: missing fernwork/cargo-sprout, which you looked at closely (c_005)"` among the errors; the second is accepted.

## 6. Coordinator integration

1. Review both diffs against §3; merge **W1, then W2** onto `feat/t09-agent-tools` off the latest main. No overlap is expected.
2. `runner.execute_run`: the default `tools` becomes `Toolbox(universe, DataTools(universe), max_picks=settings.max_picks)`. Keep the signature (T14's server calls `execute_run(settings, on_start=...)`; the CLI passes `fake`, `cfg`, `echo`). `--fake` uses the real Toolbox too: scripted model, real tools, fake universe.
3. **StubTools:** keep it as the loop's test double (T10 loop tests use it). Mark it "test double; production uses Toolbox" in its docstring, and stop using it in `runner.py`. Retiring it is optional post-hackathon cleanup.
4. Add an integration test: `execute_run(fake=True)` -> `ok` with the real Toolbox. `uv run pytest -q`; `uv run sleeper run --topic fake --fake`; start the server and launch one fake run through it.
5. **Live:**
   - One cheap luna run on the fake universe through the real tools: `uv run sleeper run --topic fake --model openai/gpt-5.6-luna --token-budget 60000 --max-turns 8 --min-ranked 5` (cents).
   - `uv run pytest -m live -k readme`.
   - If a T08 universe has landed, one luna run on it with the same caps. That is the first real check of GitHub READMEs, and of rule 6 on real data.
6. Update `tool_specs.DESCRIPTIONS["submit"]` so the model knows the rules: copy scores from the cited output; key figure values as they appear at `source_path`, in the source's unit; rank every repo you looked at closely.
7. Token check: using W1's measured sizes, estimate the cumulative prompt tokens for 15 repo_details + 15 READMEs at 6000 chars over ~8 turns (each turn re-sends the history). If it is above 300k, record that T11's prompt should recommend `max_chars` 2000–3000 (the contract default 6000 stays).
8. Learnings in `decisions.md`: tool output token sizes, the token estimate, the tolerances, README fetch behaviour. Mark T09 done in `docs/tickets/README.md` and tick this checklist. Commit on the branch; don't push.

## 7. Assumptions to confirm (flagged for the critic)

1. **Fake universe detection** is by `universe.header.topic == "fake"`. Real topics never use that id.
2. **README request shape:** W1 fetches `GET /repos/{o}/{r}/readme` with the raw media type. If T06 (Benny) fetched READMEs differently (e.g. the JSON endpoint with base64 `content`), T09's request misses T06's cache entries, so it needs network or a cache warm-up for offline use. In that case `total_chars` may also differ from `metrics.readme_chars` (bytes vs characters, or a README edited since the build). **Benny hasn't pushed T06 yet; the user is asking him which request it uses.** The request is kept as one constant pair (`README_PATH`, `README_ACCEPT`), so it is a one-line change at integration if T06 differs.
3. **Offline demo:** real-universe READMEs not already cached return `UPSTREAM` offline. The offline demo path is replaying a recorded run (C4), so this is acceptable. A `warm` step for READMEs could be added in T17 if wanted.
4. **Tolerances (decided):** a key figure is a number copied from the cited output, so it must match it up to normal display rounding: |value − source| ≤ max(0.01 × |source|, 0.051). This accepts 2.4 for 2.43, 340 for 340, and 1,230 for 1,234. It rejects 1,200 for 1,234 (2.8%), 15 for 12, and unit conversions (0.5 for 15 days as "months"). Rounded text like "1.2k" in claims is T13's concern. Scores must match within 0.5 (models round 81.2 to 81).
5. **"Researched in depth"** means at least one successful repo_details or readme. Browse doesn't count, and neither do failed calls.
6. **Claims** may cite any successful output, including other repos' (comparisons). Only key figures and scores are tied to the card's repo.
7. **The GitHub client in tools** uses `max_wait=30`, `max_attempts=2` and `timeout=10`, with calls serialized under a lock and the source created lazily once. The T10 loop also bounds tool calls by the wall clock.
8. **Submit attempts** are counted per run by the Toolbox (3), matching T10's loop, which ends the run as `invalid` at `attempts_left == 0`.
