# Demo and stretch

### T17 👤 Demo runs, picks on record, rehearsal
**Goal:** A 4-minute demo that works even if the network or the live run fails.
**Implements:** PRD §7, §8, F8.1 · **Depends on:** all P0 tickets · **P0 · M** · *Start the recorded backup by ~4:15*

**Done when:**
- At least one demo-model run per topic ends `ok`. The best is recorded as the offline backup, and replay has been tested with no network on the demo machine.
- Final picks are saved with a timestamp, run ID and model, then committed (F8.1).
- The demo script (PRD §8) has been rehearsed once end to end, with a fallback point: if the live run is slow, switch to replay.
- Each success criterion in PRD §7 is marked met or not met.

---

### S1 Rough backtest (stretch)
**Goal:** Some evidence that the agent's picks would have done well.
**Depends on:** T07, T10 · *Only if Checkpoint C landed early*

**Done when:**
- For one topic, star counts at a past date are rebuilt from an outside source, since GitHub no longer lists stargazers (for example GH Archive), and the agent runs on that past view of the data. Check feasibility first. It's likely post-hackathon.
- Its picks are compared on star growth since that date against the universe average and a "top recent stars" baseline.
- The results are shown even if they are bad, with caveats: the model may know these repos, and only part of the data is as of the past date.

**Hints:** the full design and its traps are in `archive/v0.3-full-plan/` and `archive/prd-v0.2-signer.md` §9 and §18.

**Framework (built, `src/sleeper/backtest/`):** what's left is the real star history.
1. `sleeper backtest sql --universe data/universes/<topic>.json --as-of YYYY-MM-DD` prints BigQuery SQL for the public `githubarchive` dataset. Save its result as CSV.
2. `sleeper backtest prepare --universe ... --as-of ... --history <csv> [--refetch]` writes `data/universes/backtest/<topic>@<date>.json` plus a report of what couldn't be rebuilt.
3. `sleeper backtest run --past <that file> [--model ...]` is a normal run (`execute_run`) with the live prompt plus an as-of block, and READMEs as of the date.
4. `sleeper backtest score <run_id> --github` writes `backtest.json` in the run folder (agent vs universe average, momentum and popularity baselines, random-portfolio percentiles, ranking Spearman, caveats).

It doesn't copy anything from T11, T18 or T19: the live prompt (`sleeper.agent.prompt.system_prompt`) and the scorer (`sleeper.scoring.score_universe`, `SCORING`) are looked up at call time, the repo set comes from the current universe (so T18's relevance filter carries over), and runs go through `execute_run` like the UI. Rerun `prepare` after T18 rebuilds the universes. Offline demo: `tests/test_backtest_run.py`.

---

### S2 Linked papers (stretch)
**Goal:** Use arXiv papers a repo references as extra evidence.
**Depends on:** T09

**Done when:** a tool lists the arXiv papers linked from a repo's README, with title, date and citation count, and cards can cite it.
