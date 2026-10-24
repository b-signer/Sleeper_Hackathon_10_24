# Agent

### T09 Agent tools
**Goal:** Give the agent the capabilities in PRD F3.
**Implements:** F3.1–F3.5, C1 · **Depends on:** T02, T03 · **P0 · M**

**Done when:**
- **Browse and screen:** filters and sorts the universe by any criterion score or metric, and returns at most about 50 rows plus a total count.
- **Repo details:** returns the full metrics and scores for one repo.
- **README:** returns the README text, capped at a length the agent can choose within limits.
- **Submit:** validates the submission. It rejects each of these with a specific message:
  - a card that fails the contract
  - a repo outside the universe
  - a citation to a missing or failed tool call
  - a ranking that leaves out a card's repo or a repo the agent looked at closely
  - more than the maximum number of picks
- Every output gets a unique ID that cards cite. Deep tools reject repos outside the universe.
- Outputs are compact enough to research about 15 repos within the D9 budget. State the approximate token sizes.
- Tests run on fake data, and the tools work on real universes with no code change.

---

### T10 Agent loop, budget, run records
**Goal:** Run the agent from settings to a validated submission, inside budget, recording everything.
**Implements:** F2, C1–C3, D3, D9, D10, D12 · **Depends on:** T02, T03, T04 · **P0 · L**

**Done when:**
- The loop calls the configured model through OpenRouter with the tools, and runs tool calls from the same turn concurrently.
- Narration and tool calls are recorded as events as they happen.
- Web access is impossible: web-enabled models and plugins are refused in code.
- Budget:
  - token usage and cost are taken from the provider's usage report;
  - the agent sees how much budget is left each turn;
  - as the budget runs low it is told to wrap up, and at the end it can only submit;
  - a run that would push total spend past the D10 guard is refused before it starts.
- The run ends with the correct F2.5 status in each case.
- Each run saves its settings, event log and result, and another process can read the event log while the run is still going.
- There is a command to start a run.
- A scripted fake model drives a full run on fake data, with no network, and produces valid records. **Checkpoint A** (~2:00) is this run showing in the UI.

---

### T11 Prompt and tuning
**Goal:** A system prompt that produces good, well-cited picks within budget.
**Implements:** F2, PRD §4, C1 · **Depends on:** T09, T10, T08 (or a partial universe) · **P0 · M** · *Checkpoint B ~3:15*

**Done when:**
- The prompt explains the task, the four criteria, the definition of underrated, the citation rule, "your memory is not a source", and how the budget works.
- **Checkpoint B:** on the cheap model with a real universe, 2 consecutive runs end `ok`, with 3–5 cards, at least 10 repos ranked, and every card number traceable to a cited output.
- One run on the demo model also ends `ok`.
- **Beats the real-data baseline** ([findings](../findings/2026-10-24-real-data-baseline.md)) on the same universe, with each item checked by a person reading the cards:
  - **On topic:** off-topic repos (e.g. a DJ tool in "Rust developer tools") are skipped and never picked.
  - **Depth:** the agent screens broadly before deciding, and looks closely at clearly more than the baseline's 10 repos when the universe is large.
  - **Honest quality case:** README length and raw commit counts aren't presented as quality on their own.
  - **Observable "wrong if":** each one is a concrete, checkable event in the coming months, stated as failure, not success.
  - **Confidence varies** between picks and is explained.
  - **No backtest wording** ("as-of date").
- READMEs are read at about 2,500–3,000 characters by default, so a 200-repo run fits the budget (T09 token estimate).

**Hints:** `archive/prd-v0.2-signer.md` Appendix A has a draft prompt. The baseline run's prompt is `src/sleeper/agent/prompt.py` (a placeholder).

---

### T12 Skeptic review
**Goal:** A separate check that argues against each pick.
**Implements:** F5.1 · **Depends on:** T10 · **P1 · S**

**Done when:**
- After a valid submission, each card is reviewed on the cheap model, using only the card and the tool outputs it cites.
- The verdict and a short argument are added to the result.
- Review calls count toward the run's spend.

**Hints:** v0.2 Appendix B.1 has a draft prompt.

---

### T13 Fact check
**Goal:** Automatically flag any card number that doesn't match its sources.
**Implements:** F6.1, C1 · **Depends on:** T03 · **P1 · S**

**Done when:**
- Every number on a card is compared with the values in the outputs it cites, allowing for display rounding such as `1.2k` or `85%`.
- Mismatches are written to the result, where the UI can show them.
- Tests on the fake run flag a tampered number and pass correct ones.
