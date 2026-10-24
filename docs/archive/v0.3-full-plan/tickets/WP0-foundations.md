# WP0: Foundations

WP0 gives every other ticket a working project, binding contracts, fake data to build against, and a safe spending setup. Nothing else can start in earnest until WP0 lands, so keep it small and fast.

---

### T0.1 Project skeleton
**Goal:** Anyone can clone the repo, install it with one command and run an empty test suite.
**Implements:** C4, C7, D1 · **Depends on:** none · **P0 · S**

**Done when:**
- One install command and one test command work on a clean machine, and both are written in the root README.
- There is a single command-line entry point (`python -m sleeper …` or similar) that prints help.
- There is a template for environment variables with every required setting: the GitHub token, the OpenRouter key, the model IDs, and the offline switch. The real environment file, the response cache, and scratch run output are git-ignored.
- Offline tests and live-API tests are kept apart, and live tests do not run by default.

**Out of scope:** any feature code.

---

### T0.2 Shared contracts
**Goal:** Define, once, the data shapes that cross ticket boundaries, so that tickets can be built in parallel.
**Implements:** C6, and the content requirements of F1.6, F2.1, F2.5, F2.7, F3, F4, F5.2, F6, F7, C4 · **Depends on:** T0.1 · **P0 · M**

**Contracts to define:**
- Run settings
- Universe file, with its header (F1.7) and rows
- Each tool's input and output, plus a common error shape and error codes
- Pick card and submission
- Run record files, including the event log's event types, and the result with its status values
- Grounding scorecard
- Backtest result

**Done when:**
- Every contract is machine-checkable (for example, typed models with validation) and has a short readable reference generated or written next to it (e.g. `docs/contracts.md`).
- Each contract carries every field the PRD requires. Each PRD requirement that a contract implements is referenced next to its field.
- There is a round-trip test for each contract, using an example instance.
- Every timestamp field is marked either **data** (must be before T in backtest mode) or **metadata** (for example `as_of`, run times). The contract also says how date-only and month values (`2026-02-11`, `2026-03`) are compared with T. T1.5 enforces exactly this.
- Run settings include the run limits from D16 and the ranking target from F2.5.
- The contracts are frozen **in two stages**, each announced in a commit message, with changes after a freeze following C6:
  - **Stage 1 (~0:50):** run settings, universe, tools, card and submission, events and result, grounding scorecard.
  - **Stage 2 (~1:30):** backtest result.

**Out of scope:** any behavior. Shapes only.

**Hints:** v0.2 (`archive/prd-v0.2-signer.md`, §8, §10.3, §11, §14, §15, §17.6, §18.7) has worked examples of every contract. They are not binding, but they are a good starting point.

---

### T0.3 Fake data (fixtures)
**Goal:** Let tools, the agent loop, grounding and the UI be built and tested with no network access.
**Implements:** C5, F6.7 · **Depends on:** T0.2 · **P0 · M** · *Target: ~1:15*

**Done when:**
- A fake universe of about 10 repos exists, valid against the universe contract, with varied profiles (rising, stalled, single-maintainer, burst-starred).
- Every deep tool has a canned output for each fake repo.
- A **planted repo** is defined, with a row and exact outputs for every deep tool. It is designed to look attractive to screens (high activity, low stars).
- One **recorded fake run** exists (event log + result) that passes contract validation. It includes at least one card with an error the grounding checks should catch.
- A deliberately **leaky** output exists (contains a timestamp after T) for T1.5's test.
- A fake **grounding scorecard** and a fake **backtest result** exist for the recorded run, so the UI (T7.4) can be built before WP5 and WP6 land. The fake backtest result arrives with the stage-2 contract.
- A **12-repo outcome fixture** exists, with stars at T, stars now, and hand-computed percentile, spread and Spearman values, for T6.3.

---

### T0.4 Verify external assumptions
**Goal:** Turn assumptions A1–A7 into verified facts, or switch to their fallbacks, before anyone builds on them.
**Implements:** decisions.md §3 (A1–A4, A6, A7; A5 belongs to T6.4) · **Depends on:** T0.1, T0.5 · **P0 · M**

**Done when:**
- One script runs every check and prints PASS or FAIL for each, with evidence.
- Results, including any fallback chosen, are recorded in `decisions.md` §3.
- Do A1 first. T1.2 depends on it.
- Owners of affected tickets are told about any FAIL.

---

### T0.5 👤 Accounts, keys and spend limit
**Goal:** Make it impossible to overspend, and make sure everyone has credentials.
**Implements:** C3, D11 · **Depends on:** none · **P0 · S** · *Do this before any LLM call.*

**Done when:**
- The OpenRouter key has a $50 credit limit, confirmed through the key info endpoint.
- Each teammate has a personal GitHub token with public read-only access, and nobody shares one.
- Everyone has a local environment file filled in from the template.

---

### T0.6 Runtime core
**Goal:** Give the agent loop, the bear pass, the judge, the probes and the baselines the shared runtime they all need, so none of them builds its own.
**Implements:** C3, C6, F3.9, F3.10 · **Depends on:** T0.2 · **P0 · M**

**Done when:**
- **Tool registry:** tools register once, and the tool definitions sent to the model are generated from the T0.2 contracts. One dispatcher runs a call and returns its output or a contract error.
- **Run context:** one per run. It assigns unique call IDs in call order (F3.9), keeps every output for citation, and tracks which repos have been **surfaced** and which have been **researched in depth**. T3.x, T3.4, T4.x and T5.1 all read these from here.
- **Metered LLM client:** every LLM call in the project goes through it. It records token usage and cost, appends to the spend ledger shared across runs, enforces a cap per caller (agent, bear, judge, probe, baseline), and refuses calls once total spend would pass the D11 guard. Total spend is read from the shared OpenRouter key (for example its usage endpoint), so it counts every teammate's machine, not just the local ledger.
- **Scripted fake model:** a fake model, driven by a script, can stand in for any LLM caller, with no network. T4.1, T4.7, T5.2 and T6.4 use it in tests.
- Each part has tests.
