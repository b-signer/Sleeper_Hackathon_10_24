# WP4: Agent runtime and bear pass

WP4 runs the agent end to end, inside its budget, records everything, then puts each pick through the bear pass.

---

### T4.1 Agent loop
**Goal:** Drive a model through tool use from the run settings to a valid submission or a clear failure status.
**Implements:** F2.1–F2.7, D2, D15, D16 · **Depends on:** T0.3, T0.6. Build against stub tools through the registry, and swap in T3.1 and T3.4 when they land · **P0 · L**

**Done when:**
- The loop calls the configured model through OpenRouter with the tool definitions and runs tool calls from the same turn concurrently.
- Narration (F2.3) and any reasoning summaries the model returns are captured as events.
- **No web access** is possible: web-enabled model variants and plugins are refused in code, with a test.
- The run ends with the correct F2.7 status when:
  - the submission is valid;
  - submissions stay invalid after the retry limit;
  - the model stops calling tools (one reminder first, then the run ends as stalled);
  - the turn limit or the wall-clock limit is reached;
  - an unexpected error happens.
- The **scripted fake model** (T0.6) drives a full run on fake data, with no network, and produces a valid run record and result. This is tested.

**Hints:** A6 (parallel tool calls) decides whether the agent can call several tools per turn. Use reproducible settings (a seed) where the provider supports them.

---

### T4.2 Budget and spend control
**Goal:** The per-run token budget and the total spend limit are hard limits.
**Implements:** C3, D10, D11, G4 · **Depends on:** T4.1 · **P0 · M**

**Done when:**
- Tokens used are counted from the provider's reported usage, including cached and reasoning tokens. Cost is computed from current prices, or taken from the provider's reported cost when available. If usage is missing, cost is marked as estimated.
- Before each turn, the agent sees how much budget is left, how many repos it has researched, and the turn count.
- As the budget runs low, the agent is told to wrap up. Near the end, it can only submit. At zero, the run stops as `budget_exhausted` and keeps any valid submission.
- If the agent is behind the ranking target by mid-budget, it is reminded.
- A run whose estimated worst-case cost would push total spend past the D11 guard is refused before it starts. The spend ledger lives in T0.6. This ticket adds the check before the run.
- Tests with simulated usage cover each threshold.

**Hints:** v0.2 used warnings at 15% and 5% remaining.

---

### T4.3 Bounded context
**Goal:** Long runs fit the budget without breaking citations or prompt caching.
**Implements:** F3.11, D10 · **Depends on:** T4.1 · **P0 · M**

**Done when:**
- The context sent to the model stays at or below about 60k tokens (F3.11) for the whole run.
- Citations made before shortening stay valid, and the full outputs are always kept in the run record.
- The agent can still get any data it needs after shortening.
- Shortening is predictable and is not done by an LLM, because citations depend on it.
- Shortening doesn't wreck token cost: report the cache-hit rate on a real run.
- A test shows that a 25-repo fake run stays under the limit and still submits successfully.

**Hints:** v0.2 shortened all but the 8 most recent outputs once the prompt passed 60k tokens.

---

### T4.4 Run records
**Goal:** Every run leaves a complete, append-only record that the UI and the grounding checks rely on.
**Implements:** C4, F2.7 · **Depends on:** T0.2 · **P0 · M**

**Done when:**
- Each run has its own folder with an ID that is unique and sorts by time. The folder contains:
  - the resolved settings, with the code version, a prompt hash, model IDs and the universe used;
  - the event log, written as the run happens;
  - the result: status, cards with bear verdicts, final picks, ranking, summary, token usage and cost, and the below-target flag.
- Every contract event type is emitted at the right moment.
- Tool results in the log are **full**, never shortened.
- Another process can read the log while the run is still going (T7.1 depends on this).

---

### T4.5 Command-line interface
**Goal:** Every operation can be scripted.
**Implements:** C4 · **Depends on:** T4.1 · **P0 · S**

**Owns:** the command-line entry point and the `run` command. Each owning ticket adds its own subcommand: universe build (T2.2a), grounding (T5.1), scoring (T6.2), server (T7.1).

**Done when:** these commands exist, documented in `--help` and the README:
- build a universe
- run the agent (topic, mode and T, model, planted on or off, seed)
- run grounding on a run
- score a backtest run
- start the UI server

---

### T4.6 System prompt and tuning
**Goal:** A system prompt that produces good, well-cited research within the budget.
**Implements:** F2, §5 "underrated", C2 · **Depends on:** T4.1, T4.3, T3.2 on real data, and the first finished universe (backtest or live) · **P0 · M** · *Checkpoint B ~3:30*

**Done when:**
- The prompt is versioned, and its hash is logged with each run.
- It states the task, the definition of underrated, the citation and no-memory rules, and how the budget works.
- Tuning starts on whichever universe finishes first, backtest or live.
- **Checkpoint B:** on the cheap model, with real data, 2 consecutive runs end `ok`, with at least 20 repos ranked (F2.5) and no grounding failures from T5.1 (or every failure understood).
- One run on the agent model also ends `ok`.

**Hints:** v0.2 Appendix A has a complete draft prompt.

---

### T4.7 Bear pass
**Goal:** An independent skeptic reviews every card.
**Implements:** F5.1–F5.4 · **Depends on:** T0.6, T4.4 · **P0 · M**

**Done when:**
- After a valid submission, each card is reviewed by the cheap model in a fresh context that holds only the card and the full tool outputs it cites. The review returns a verdict and an argument with citations (F5.2), and the output is validated against a schema.
- Verdicts are added to the result. Final picks are the cards that were not killed.
- Bear calls have their own small budget, and their spend counts toward the total.
- A test using the fake model covers all three verdicts.

**Hints:** v0.2 Appendix B.1 has a draft prompt.

---

### T4.8 Run pipeline integration
**Goal:** A single command, or a single UI launch, takes a run from start to its finished records with nothing done by hand.
**Implements:** C4, F5, F6 · **Depends on:** T4.1, T4.4, T4.7, T5.1, T7.1, T7.3 · **P0 · S**

**Done when:**
- After a valid submission, the bear pass runs, then grounding (T5.1, and T5.2 if present), and the result and scorecard are written.
- Each stage that fails is recorded, and the stages after it still run where possible.
- **Checkpoint A:** a fake-model run goes through the whole pipeline end to end and shows in the UI, by about 2:45.
