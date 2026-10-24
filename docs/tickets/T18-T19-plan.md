# T18 + T19 build plan

Two workers run in parallel, each in an isolated git worktree: **W-T18** (topic relevance and quality score) and **W-T19** (real runs from the UI). The manager merges both. Background: [findings](../findings/2026-10-24-real-data-baseline.md), problems 1, 2 and 7.

## 0. Facts checked before planning (2026-10-24, main @ `1dd462d`)

- **The GitHub cache on this machine is cold.** `.cache/github` holds 154 entries, all from the 25-repo baseline. The T08 universes were built on another machine. So a rebuild here is close to a full build: about 1,000 to 1,600 core requests per universe (T07/T08 numbers), not "cheap". GraphQL is never reused across builds anyway unless `built_at` is the same (T08 learning).
- **Percentile scoring ignores log scaling.** Quality is a mean of within-universe percentiles. `log(x)` is monotonic, so log-scaling commits changes nothing. Damping has to be a **cap** (values above the cap tie) or a switch to absolute scores.
- Current spread (old universes): `commits_90d` median 44 / 58, 90th percentile 752 / 976, max 19,771. `releases` median 17 / 9, 90th percentile 77 / 80, max 1,924. `readme_chars` is an input today.
- **The server already runs real models.** `app_from_settings` uses `agent_runner` (it calls `execute_run(settings, fake=..., cfg=..., on_start=...)`), and the launcher defaults to the dev model when online. The T19 gap is UX and safety: no Replay/Live choice, live is the default, a spend refusal shows as a raw `400` string, and no test covers the live path. `FakeRunner` is only used in tests.
- **Concurrent work:**
  - **T11** (branch `worktree-agent-a156584ac191d8824`, commit `e54e22a`) touches `agent/prompt.py`, `agent/tool_specs.py`, `cli.py` (+20 lines: `review` subcommand), `review.py` and `tests/test_review.py`. Its `prompt.py` has a `TOPIC_SCOPE` dict with on/off-topic definitions per topic.
  - **T12** (`origin/feat/t12-skeptic`) touches `agent/loop.py`, `agent/runner.py` (adds a `skeptic=` kwarg, which is backwards compatible), `agent/scripted.py`, `agent/skeptic.py`, `tests/test_skeptic.py`, `docs/decisions.md`, `docs/tickets/README.md` and `dependency-graph.html`. **No server or UI files.**
- `fixtures/fake/` has its own hand-written scores and `SCORING` text (`fakes.py`), independent of `scoring.py`. T18 does not touch the fixtures.

## 1. Checklists against "Done when"

### T18

- [ ] **Relevance:** 20 seeded-random rows per universe, at least 17 clearly on-topic (human judgement; the worker prints the sample, and the manager or Roee signs off).
- [ ] The relevance method is documented, and if an LLM is used, its cost is recorded (in the verdict file and in `header.builder`).
- [ ] Off-topic drops are counted in `header.counts.excluded["off_topic"]`. The Universe contract is unchanged and the files validate.
- [ ] **Quality:** commits and releases are capped (damped), `readme_chars` is no longer an input, and at most a small "has a real README" bonus remains.
- [ ] `SCORING` in `scoring.py`, `docs/scoring.md` and every rebuilt universe's `header.scoring` say the same thing.
- [ ] Momentum is unchanged (D13).
- [ ] Both universes are rebuilt and committed. `uv run pytest -q` passes.

### T19

- [ ] The launcher has a clear **Replay** (default, free, offline) vs **Live run** (real model, costs money) choice.
- [ ] A live run streams its trace and cards through `execute_run(settings, on_start=...)` (via `agent_runner`).
- [ ] A spend-guard refusal shows as a readable message, not a raw error string.
- [ ] Offline replay still works with `SLEEPER_OFFLINE=1` and no network.
- [ ] `uv run pytest -q` passes.
- [ ] Checkpoint B: the team watches one real run in the UI (manager, at integration).

## 2. Design decisions

### T18 relevance: LLM classifier with committed verdicts

1. **Method: a cheap luna classifier per candidate**, not keywords. A Rust music player and a Rust linter share every keyword and tag. Tighter searches alone would drop `topic:cli`, which is 335 of 578 eligible Rust repos. The worker may also tighten queries in `topics.toml`, but the classifier is what meets the 17/20 bar.
2. **Topic definition lives in `topics.toml`:** a new optional `scope` string per topic. Copy the wording from T11's `TOPIC_SCOPE` (`git show worktree-agent-a156584ac191d8824:src/sleeper/agent/prompt.py`), so the builder and the agent use the same definition. Unifying them is a follow-up after both merge, not part of this ticket.
3. **Where it runs:** in `build_universe`, step 3, for each candidate in shuffle order **before** fetching its metrics. An off-topic repo costs one README request, not about 5.6. It counts as `excluded["off_topic"]` and is backfilled by the next repo in the shuffle, like `not_found`.
   - Input: repo name, description and GitHub topics (from the search item), plus the first ~1,500 characters of the README. The README is fetched with the same request as `metrics._readme_chars` (`/repos/{repo}/readme` with `RAW_ACCEPT`), so it shares the cache.
   - Output: JSON `{"verdict": "on" | "off" | "unsure", "reason": "<one line>"}`. Only `on` is kept.
4. **Determinism and caching:** verdicts go in a committed file, `data/relevance/<topic>.json`. Each entry holds `repo_id`, `repo`, `verdict`, `reason`, `model`, `prompt_sha256` and `cost_usd`, and the file has totals.
   - A verdict is reused when `repo_id`, `model` and `prompt_sha` match. So a rebuild re-classifies nothing and works offline.
   - The file is not under `data/universes/`, which `list_universes` globs.
5. **Cost controls:**
   - Model: `cfg.dev_model` (luna). Calls go through `OpenRouterModel.complete(messages, tools=[])`, which gives cost per call and the no-web guard.
   - Hard cap per build: `$0.50` by default, aborting with a clear error if exceeded.
   - Before the first uncached call, it runs `sleeper.agent.spend.current_spend(cfg)` and refuses if `spent + cap > SPEND_GUARD_USD`.
   - Recording: the total cost and count go in the verdict file and as a suffix on `header.builder`, e.g. `sleeper 0.1.0 @ abc123; relevance openai/gpt-5.6-luna 412 checked, $0.08`. This needs no contract change.
6. **Injection for tests:** `build_universe(..., relevance=<callable or None>)`. A topic without `scope` skips the check. Tests pass a fake classifier.
7. **No `cli.py` change.** The CLI's call to `build_universe` picks up the default classifier from the topic. A worker who wants `--built-at` or `--no-relevance` must flag it first, because `cli.py` collides with T11.

### T18 quality score: capped percentiles

Keep the percentile framework, with these changes:

- **Cap the volume inputs before taking percentiles:** `min(commits_90d, 50)`, `min(releases, 10)` and `min(authors_90d, 5)`.
  - Everything at or above the cap ties, so 981 commits scores the same as 200 on that input.
  - These caps are starting points. Tune them on the rebuilt data so that roughly the top 25–40% tie.
- **Drop `readme_chars`.** A has-README input is `100` if `readme_chars >= 500`, `0` if it is below 500 or zero, and `50` if unknown.
- **Weights:** commits 25%, authors 25%, releases 20%, last-commit recency 20%, has-README 10%.
- Momentum, popularity and newness are unchanged. `None` still counts as the median.
- Update `SCORING`, `docs/scoring.md` and the tests that assumed README length moves quality.

### T18 rebuild

- Build with a fresh `built_at` (the cache is cold anyway), seed 7 and max 200, with the topics' existing star and date windows.
- Set `SLEEPER_CACHE_DIR` to the **main checkout's** `.cache`, so the warm cache stays where the demo and T11 can use it.
- Build sequentially, rust-devtools first (smaller). Before each build, check `GET /rate_limit` (it is free) and only start with **≥ 2,500 core remaining**.
- **First, a smoke test:** `--max-size 25 --out <scratchpad>/smoke-<topic>.json` to measure the off-topic rate and the cost per check.

### T19: mode switch in the launcher, structured refusals

1. **The launcher gets a two-way switch: `Replay` (default) or `Live run`.**
   - **Replay:**
     - Pick a finished recorded run, defaulting to the newest finished run with cards. The fixture fake run is always there as a fallback.
     - Pick a speed. The button streams `/api/runs/{id}/replay`.
     - No POST, no spend, works offline.
   - **Live run:**
     - Choose a topic from the real universes (exclude topic `fake` unless the fake model is chosen), a model (luna or terra, with prices), max picks and a token budget (default 300k; show it).
     - Show the worst-case cost next to the button ("costs up to $0.10"), from the same formula as `spend.estimate_max_cost`.
     - A confirmation step comes before the POST.
     - The scripted fake model may stay as a last "offline test" option, but never as the default.
2. **Offline mode** (`config.offline`): Live run is disabled with a one-line reason. Replay works.
3. **Readable refusals:**
   - `POST /api/runs` returns `400` with `detail = {"reason": "spend_guard" | "offline" | "no_universe" | "no_api_key" | "web_model" | "other", "message": "<human sentence>"}`. The mapping happens in `server/app.py` and `server/__init__.py`, by exception type (`SpendGuardError`, `RunRefused`, `FileNotFoundError`, `ModelError`, `WebAccessRefused`).
   - The UI shows a titled box, e.g. **"Not started: spend limit"**, with the message (which already carries $ spent, $ estimate and the $45 guard).
   - The UI's `api()` must handle both a string and an object `detail`.
4. **Config:** `ServerConfig` gains `spend_guard_usd`. `ModelInfo` gains `max_run_usd` (the estimate at the default budget). Optional (P1): `GET /api/spend` → `{spent_usd, guard_usd, source}`, or `null` offline.
5. **Optional:** hide or disable the star-range inputs in Live mode. `RunSettings.stars_*` doesn't filter a prebuilt universe, so the fields mislead.
6. **Untouched:** `agent/runner.py` (T12 edits it), `agent/spend.py` (import only), `cli.py` and every T11 file.

## 3. File ownership (no overlaps)

| | W-T18 | W-T19 |
|---|---|---|
| **May edit** | `topics.toml`, `src/sleeper/universe.py`, `src/sleeper/scoring.py`, **new** `src/sleeper/relevance.py`, `docs/scoring.md`, `tests/test_universe.py`, **new** `tests/test_relevance.py`, `data/universes/*.json`, **new** `data/relevance/*.json` | `src/sleeper/server/__init__.py`, `app.py`, `runner.py`, `store.py`, `static/*`, `tests/test_server.py`, **new** `tests/test_server_live.py` (optional) |
| **Read or import only** | `metrics.py`, `github/*`, `agent/openrouter.py`, `agent/spend.py`, `config.py`, contracts | `agent/runner.py`, `agent/spend.py`, `agent/openrouter.py`, `config.py`, contracts |
| **Never touch** | `cli.py`, `agent/prompt.py`, `agent/tool_specs.py`, `review.py`, `agent/runner.py`, `agent/loop.py`, `fakes.py`, `fixtures/`, `server/*`, `docs/decisions.md`, `docs/tickets/*` | the same, plus everything in the W-T18 column |

Learnings and ticket statuses (`docs/decisions.md`, `docs/tickets/README.md`) are written by the **manager** at merge time, because T12 edits both. Workers put learnings in their report.

## 4. Acceptance tests

### W-T18 (offline, in `uv run pytest -q`)

- **Relevance classifier with a fake model:**
  - The JSON verdict is parsed, and malformed output counts as `unsure`, so the repo is dropped.
  - Verdicts are cached in the verdict file and reused: the second build makes zero model calls.
  - A changed prompt hash forces a re-check.
  - The cost cap aborts the build with a clear error.
  - The spend-guard refusal is exercised with a stubbed `current_spend`.
- **`build_universe` with a fake classifier:**
  - Off-topic repos are excluded, counted in `excluded["off_topic"]` and backfilled.
  - `counts.rows == len(rows)`, and the file validates against `Universe`.
  - A topic without `scope` makes no classifier calls.
- **Scoring:**
  - 981 vs 200 `commits_90d` give the same commits input.
  - `readme_chars` 3,000 vs 300,000 give the same quality, all else equal.
  - No README scores below a real README.
  - The momentum tests are unchanged.
  - `SCORING` matches `docs/scoring.md`.
- **Existing tests** stay green, including `test_cli_build_universe`, which must not need a key: it either has no `scope` or uses an injected classifier.

### W-T18 (live, with estimated cost)

- **Smoke builds** (25 rows each): about 150 GitHub requests and about $0.01 per topic.
- **Full rebuilds:**
  - rust-devtools: about 1,300 core requests (search, about 250–300 READMEs, 200 × about 5) plus GraphQL.
  - local-first: about 1,600–1,900 core requests (more off-topic candidates).
  - Total under about 3,500 core requests over about 30–40 minutes, and about $0.10–0.40 on luna (about $0.0002–0.0004 per check × about 300–700 checks).
- **Report:** the real numbers for requests, seconds, checks and $, and print the seeded 20-row sample per universe (`repo`, `description`, verdict reason) into the progress note for human sign-off.

### W-T19 (offline, in `uv run pytest -q`)

- `POST /api/runs` with a live model, with `execute_run` monkeypatched to raise `SpendGuardError` before `on_start`, returns `400` with `detail.reason == "spend_guard"` and a message. No run folder is created.
- The same holds for an offline refusal (`reason == "offline"`) and a missing universe (`no_universe`).
- A live-model POST (not offline, with `execute_run` monkeypatched to a stub that records `fake=False`, calls `on_start` and writes events and a result) streams `/events` to `end`, and `/result` returns the cards.
- `/api/config` includes `spend_guard_usd`, and `max_run_usd` for priced models.
- The existing test that every page asset loads with no external assets stays green, and the fixture replay still streams with `SLEEPER_OFFLINE=1`.

### W-T19 (live, with estimated cost)

- One real luna run through the server API on the **current** `rust-devtools` universe:
  - Settings: `token_budget` 60,000, `max_turns` 6, `min_ranked` 5. Worst case ≈ $0.02, expected ≈ $0.01. About 10–20 GitHub README requests.
  - Check: start → `/events` streams to `end` → `/result` has cards.
- One refusal check that costs nothing: terra with `token_budget` 20,000,000. The estimate of about $64 is over the $45 guard, so the result is a `400` with `spend_guard`. Only `GET /key` is called.
- If Playwright is available, also screenshot both modes and the refusal box.

## 5. Integration (manager, phase 2)

1. **Review each branch diff** against the ownership table. `git diff main...<branch> --stat` must list only owned files.
2. **Merge T19 first.** It is small, code only and has no data. Then run `uv run pytest -q`.
3. **Merge T18.** It brings code and data, and conflicts with nothing in T19. Then run `uv run pytest -q`.
   - If the worker didn't finish the rebuilds, the manager runs them on main, using the rate-limit check and the commands in §2.
   - Validate both files: `uv run python -c "from sleeper.contracts import Universe; ..."`, then check `counts`, `excluded.off_topic`, `header.scoring` and `builder`.
4. **Relevance sign-off:** print 20 seeded-random rows per universe (seed 18) and judge them (≥ 17/20 each). Record the result.
5. **Real luna run on a rebuilt universe** (with T11's prompt if it has merged; otherwise note that it is the old prompt):
   - Command: `uv run sleeper run --topic rust-devtools --token-budget 120000 --max-turns 12 --min-ranked 8`. About $0.01–0.04.
   - Compare against baseline problems 1 and 2: no off-topic picks, and no README length or raw commit volume argued as quality.
6. **UI check:**
   - `uv run sleeper serve`. Replay is the default and the fixture replays.
   - Live run with luna, 120k budget (about $0.04 max). The trace streams and the cards render.
   - The terra 20M-budget refusal box is readable.
   - Then `SLEEPER_OFFLINE=1 uv run sleeper serve`: Live is disabled and replay works with no network.
7. **Docs:**
   - Update `docs/tickets/README.md` statuses for T18 and T19, and mark T08 for a re-check.
   - Append the workers' learnings to `docs/decisions.md`: the relevance method and cost, the quality caps, the cold cache, and the 400 detail shape.
   - Tell the T11 owner that the universes changed: the rows differ, quality is redefined, and `excluded.off_topic` exists. T11's tuning conclusions on old universes need one re-run.
   - Tell T12 nothing. There is no overlap.

## 6. Risks

| Risk | Mitigation |
|---|---|
| The shared GitHub token is exhausted (cold cache, T11 runs and two rebuilds) | Check `/rate_limit` before each build (≥ 2,500 remaining) and build sequentially. The client waits on a limit rather than crashing. Smoke-test first. Expect about 3,500 requests in total |
| The classifier is too strict (local-first falls well below 200) or too loose (< 17/20) | Tune `scope` on the 25-row smoke builds. Fewer than 200 rows is acceptable (D7 says "about"). Treat `unsure` as off |
| The classifier is nondeterministic | Verdicts are cached and committed, so rebuilds reuse them. The prompt hash is recorded |
| LLM cost runs away | $0.50 cap per build, the D10 guard check before the first call, and a one-line prompt and reply |
| Caps chosen badly, so most repos tie and quality flattens | Tune the caps on the data so roughly 25–40% tie. Check the quality spread in the rebuilt universe (the quartiles should still differ) |
| The new universes break T11's tuned prompt or examples | Re-run T11's check after the merge (step 5). The contract is unchanged, so no code breaks |
| `cli.py` conflict with T11 | Neither worker edits `cli.py` |
| T19 breaks offline replay (demo safety) | Replay is the default, there is an offline test, and the manager checks with `SLEEPER_OFFLINE=1` (step 6) |
| T12 merges and changes `execute_run` | It only adds `skeptic=`, a backwards-compatible kwarg. T19 calls `agent_runner` and doesn't edit `runner.py` |
| The worktree has no `.env` or cache | Workers copy `.env` (never print it) and set `SLEEPER_CACHE_DIR` to the main checkout's `.cache` |
