# WP7: Web UI

WP7 is the single local page where a judge watches the agent think, inspects its evidence, and sees the backtest. It reads only the run records (C4), so it can be built entirely on the recorded fake run from T0.3.

---

### T7.1 Server and event stream
**Goal:** Serve the page and give it run data, both live and recorded.
**Implements:** F8, C4, D14 · **Depends on:** T4.4 · **P0 · M**

**Done when:**
- The server can:
  - start a run from submitted settings and return its ID right away, while the run continues in the background;
  - list runs and the prebuilt universes (topic, T, size);
  - return a run's result, grounding scorecard and backtest result;
  - stream a run's events live as they are written;
  - replay a finished run's events at a chosen speed.
- A test streams the fake run and replays it at 4× speed.

---

### T7.2 Launcher, live trace, budget meter
**Goal:** Start a run and watch it think.
**Implements:** F8.1–F8.3 · **Depends on:** T7.1 · **P0 · M**

**Done when:**
- The launcher has every F8.1 input. In backtest mode, it shows the selected universe's T.
- The trace shows narration as a story, and each tool call as a one-line summary (for example `get_repo_stats acme/quiver → 340★, 81 commits/90d`) that expands to the full output.
- The budget meter updates live and marks cost as estimated when it is.

---

### T7.3 Cards with source drill-down
**Goal:** Every claim on a card can be checked in one click.
**Implements:** F8.4 · **Depends on:** T7.1 · **P0 · M**

**Done when:**
- Cards show every F4 field.
- The bear verdict shows as a badge, and killed cards are struck through.
- Grounding flags show next to the facts they concern.
- Clicking a key figure opens its cited tool output with the cited value highlighted.

---

### T7.4 Grounding and backtest panels
**Goal:** Show the evidence that the agent is honest and whether it worked.
**Implements:** F8.5, F8.6 · **Depends on:** T7.1, T0.3 (fake scorecard and backtest result) · **P0 · M**

**Done when:**
- A scorecard panel shows the grounding scorecard.
- A backtest panel shows three charts:
  - Mean growth for the agent (before and after the bear pass), each baseline, and the universe.
  - The random-portfolio distribution with the agent marked.
  - Agent rank against outcome, with Spearman ρ.
- Caveats are visible.
- Bad results are shown as plainly as good ones.

---

### T7.5 Runs list and offline replay
**Goal:** The demo can't be broken by the network.
**Implements:** F8.7, C5 · **Depends on:** T7.1 · **P0 · S**

**Done when:**
- The page lists all recorded runs, and any of them can be replayed.
- With the offline switch on and the network disconnected, a recorded run replays completely, cards and panels included.
- Every page asset is available offline.
