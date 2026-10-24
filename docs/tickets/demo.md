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
- For one topic, stars at a past date are rebuilt from stargazer timestamps, and the agent runs on that past view of the data.
- Its picks are compared on star growth since that date against the universe average and a "top recent stars" baseline.
- The results are shown even if they are bad, with caveats: the model may know these repos, and only part of the data is as of the past date.

**Hints:** the full design and its traps are in `archive/v0.3-full-plan/` and `archive/prd-v0.2-signer.md` §9 and §18.

---

### S2 Linked papers (stretch)
**Goal:** Use arXiv papers a repo references as extra evidence.
**Depends on:** T09

**Done when:** a tool lists the arXiv papers linked from a repo's README, with title, date and citation count, and cards can cite it.
