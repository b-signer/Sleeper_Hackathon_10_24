# WP3: Agent tools

WP3 gives the agent the capabilities in PRD F3. Each tool's input and output shape is fixed by the T0.2 contracts. Build against fake data first, then switch to the real data layer.

Rules that apply to every tool (F3.9–F3.12):
- Every output gets a unique ID for the run, and that ID is what cards cite.
- Errors use the shared error shape and cannot be cited.
- Outputs are compact.
- A value that can't be determined is reported as unknown.

---

### T3.1 Browse and screen tools
**Goal:** Let the agent explore the universe and run its own screens.
**Implements:** F3.1, F3.2, F3.9, F3.11 · **Depends on:** T0.3, T0.6 · **P0 · M**

**Done when:**
- **Browse:** filters by created date range, stars, language and keyword (matched against the name and description at T); sorts by stars, created date, or a seeded random order; returns pages of at most 50 rows with a total count.
- **Screen:** applies any combination of conditions on the F3.2 metrics (comparisons and ranges, combined with AND), sorts by any of those metrics, and returns at most 50 rows plus the number matched. Each row includes the metrics used to screen or sort.
- Both tools mark the repos they return as **surfaced** in the run context (T0.6).
- Outputs pass the future-data guard (T1.5) in backtest mode.
- Invalid arguments and unknown metrics return specific errors.
- Tests run on the fake universe.

---

### T3.2 Deep-dive and README tools
**Goal:** Let the agent study one repo in detail as of T.
**Implements:** F3.3, F3.4, F3.10, C1 · **Depends on:** T0.3, T0.6, then T1.2–T1.4 · **P0 · M**

**Done when:**
- The deep-dive tool returns everything listed in F3.3. The README tool returns the README at T, capped at a length the agent can choose within limits.
- Both refuse repos outside the universe, and repos not yet surfaced, with distinct errors (tested).
- Every output passes through the future-data guard (T1.5) in backtest mode.
- They work on fake data with no network, and on real data through WP1, with no code change, only a setting.
- Outputs meet the F3.11 size limits (about 600 tokens per deep dive; README 1.5k by default, 3k at most). A test measures them on fake data.

---

### T3.3 Calculator tool
**Goal:** Derived numbers come from code, not from the model.
**Implements:** F3.5, F6.2 · **Depends on:** T0.2 · **P0 · S**

**Done when:**
- The tool takes an arithmetic expression whose inputs are references to values inside earlier successful tool outputs, including earlier calculator results. Literal constants are allowed only inside the expression.
- Its output shows the expression, every resolved input value, and the result. That is enough for T5.1 to recompute it.
- It supports the operations needed for ratios, percentages, differences, growth rates, logarithms and rounding.
- Tests: it rejects code injection, attribute access and unknown names, and it **never** executes arbitrary code. Division by zero, bad references and references to error outputs return clear errors.

---

### T3.4 Submit tool and validation
**Goal:** Accept only submissions that meet F2.5 and F4. Otherwise, tell the agent exactly what to fix.
**Implements:** F2.5, F2.6, F4, F3.6 · **Depends on:** T0.2, T0.6 · **P0 · M**

**Rejects, each with a specific error message:**
- A card that fails the card contract
- A card for a repo that is out of the universe or was not surfaced
- A citation to a call that doesn't exist, returned an error, or was itself a submission
- A key-figure reference that doesn't resolve inside its cited output, or a key figure whose value differs from the cited value
- A ranking that leaves out a repo researched in depth, includes a repo that was not surfaced, leaves out a card's repo, or has ranks other than exactly 1..n
- More than the maximum number of picks

**Done when:**
- There is one test per rejection rule, plus one test for a valid submission.
- A valid submission with fewer than the target number of ranked repos is accepted, and the result is marked as below target.
- The retry limit comes from the run settings (D16). T4.1 enforces it.

---

### T3.5 Planted-repo injection
**Goal:** Allow a run to include the planted fake repo, for grounding tests.
**Implements:** F6.7 · **Depends on:** T0.3, T3.2 · **P1 · S**

**Done when:**
- A run setting adds the planted repo to the universe. The tools then return its fixture data, which is indistinguishable in shape from real data.
- The planted flag is never visible to the agent.
- The planted repo can never appear in a scored backtest. A guard enforces this.

---

### T3.6 Contributors tool
**Goal:** Expose T1.7 to the agent.
**Implements:** F3.7 · **Depends on:** T1.7, T3.2 · **P1 · S**

**Done when:** the tool follows the same surfacing, universe and future-data rules as T3.2, with tests.
