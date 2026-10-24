# WP6: Backtest evaluation

WP6 answers honestly whether the agent's picks, made as of T, did better than simple baselines by today.

---

### T6.1 Outcome snapshot
**Goal:** Capture "what happened since T" once per universe, so all scoring is consistent and can be repeated.
**Implements:** F7.2 · **Depends on:** T2.3 · **P0 · S**

**Done when:**
- One command fetches the current star count for **every** repo in a universe. If affordable, it also fetches commits since T and whether there was any release since T.
- It saves a dated snapshot. Every run on that universe is scored from the same snapshot.
- Repos that have been deleted or made private are recorded as missing, not as zero, and the count goes into the caveats.
- Growth for each repo is `ln((stars_now+1)/(stars_T+1))`.

---

### T6.2 Baselines and scoring command (Checkpoint C)
**Goal:** The comparisons the agent has to beat.
**Implements:** F7.3 · **Depends on:** T6.1, T0.6, T6.3, T4.8 · **P0 · M**

**Done when:** for a scored run, with N = the number of picks before the bear pass:
- **Universe average** (P0)
- **Random portfolios** (P0): 10,000 seeded random sets, giving a distribution. Build one distribution at N and one at the size of the final (post-bear) set
- **Momentum** (P0): the top N repos by stars gained in the last 30 days
- **Activity** (P1): the top N repos by commits in the last 90 days
- **Naive LLM** (P1): the agent model gets the universe list (name, description at T, a few key metrics) in one prompt, with no tools, and picks N. It has its own small budget.

Each baseline yields a mean growth and its picks.

**Also owns the scoring command:** one command takes a run ID and writes its backtest result, wiring together T6.1, this ticket, T6.3 and T6.4's probe flags.

**Checkpoint C (~4:15):** one fully scored backtest run, end to end, recorded as the fallback demo run.

---

### T6.3 Statistics
**Goal:** Turn picks and outcomes into the numbers in F7.4.
**Implements:** F7.4 · **Depends on:** T0.2, T0.3 · **P0 · M**

**Done when:**
- It computes:
  - The random-portfolio percentile for the agent before the bear pass, the agent's final picks, and each baseline
  - Ranking spread (top third minus bottom third)
  - Spearman ρ with a permutation p-value (10,000 shuffles)
  - Killed picks compared with surviving picks
  - A pooled result across runs
- Results are labeled "directional" unless the pooled ranking test gives p < 0.05.
- It writes the backtest result contract.
- Tests reproduce hand-computed values on the 12-repo fixture from T0.3.

---

### T6.4 Contamination controls
**Goal:** Evidence that the models didn't already know the future.
**Implements:** F7.5, C1, A5 · **Depends on:** T0.5, T0.6 · **P0 · M** · *Canary done by ~2:15, well before the campaign*

**Done when:**
- **Cutoff canary:** for each model that influences picks, with no tools, the model is asked about the 10 most-starred topic repos created **after** T. The check passes if the model gives specific, non-guessable correct facts about fewer than 2 of them. Otherwise the model is replaced, following `decisions.md` §2.
- **Probe:** for each pick and 10 random ranked repos, the model is asked what it knows and to estimate today's stars. A repo is flagged if the model gives non-guessable specifics, or if it estimates current stars within 2× when the repo grew more than 3×.
- Flags go into the backtest result. No repo is silently dropped.
- The report says plainly that knowledge from before T is fair game, and that only knowledge from after T counts as contamination. Using current-star search to choose canary repos is fine, because the canary is an evaluation step.

**Hints:** v0.2 Appendix B.3 has a draft prompt.

---

### T6.5 👤 Backtest campaign
**Goal:** Produce the results we will present.
**Implements:** F7.1, F7.6, F7.7, G5 · **Depends on:** T2.3, T4.8, T5.1, T6.1–T6.4 · **P0 · M** · *Start right after Checkpoint C (~4:15). Results by ~4:50.*

**Done when:**
- Contamination canaries pass.
- At least 2 topics have 2 scored runs each on the agent model, with different seeds. Runs go in parallel (each run takes up to 20 minutes). There is also 1 planted run, which is not scored.
- Each run has its grounding scorecard and backtest result.
- Spend stays within the budget split in `decisions.md`.
- Caveats (F7.6) are written into the results.
- The best demo run is chosen and kept as the replay backup. "Best" means most representative, not best-looking.
