# WP8: Release, live picks and demo

---

### T8.1 Live picks on the record
**Goal:** Make public, timestamped predictions that we can check later.
**Implements:** F9.1 · **Depends on:** T4.5, T2.4 · **P1 · S**

**Done when:**
- The final picks from every live run are saved automatically. Each saved record includes the run ID, model, universe, and timestamp.
- The saved picks are committed.
- The repo has a short note saying they will be re-scored on 2027-04-24.

---

### T8.2 👤 Demo rehearsal and backup
**Goal:** A 4-minute demo that works even if everything else fails.
**Implements:** PRD §9, success criteria · **Depends on:** the demo core (README) · **P0 · M** · *4:50–5:30*

**Done when:**
- The demo script (PRD §9) has been rehearsed at least once, end to end, on the demo machine.
- The backup replay works offline on that machine.
- The live run has a fallback point: if the live run is slow, switch to replay.
- The backtest numbers in the script come from the actual results, whatever they are.
- Each success criterion in PRD §8 is marked met or not met, with evidence.
