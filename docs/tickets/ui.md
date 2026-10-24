# UI

The UI reads only run records (C3), so all of it can be built on the recorded fake run from T03.

### T14 Server, event stream, replay
**Goal:** Serve the page and give it run data, both live and recorded.
**Implements:** F7.5, C3, C4, D11 · **Depends on:** T02, then T03 for tests · **P0 · M**

**Done when:**
- The server can:
  - start a run and return its ID right away, while the run continues in the background;
  - list runs and prebuilt universes;
  - return a run's result;
  - stream a run's events live;
  - replay a finished run at a chosen speed.
- With the offline switch on and no network, a recorded run replays completely, and every page asset loads.
- A test streams and replays the fake run.

---

### T15 Launcher, live trace, budget meter
**Goal:** Start a run and watch it think.
**Implements:** F7.1, F7.2, F7.4 · **Depends on:** T14 · **P0 · M**

**Done when:**
- The launcher offers topic (from prebuilt universes), star range, max picks and model.
- The trace shows narration as a story, and each tool call as one line (for example `repo details acme/quiver → 340★, 81 commits/90d`) that expands to the raw output.
- The budget meter shows tokens used and cost, updating live.
- A list of past runs lets you open or replay any of them.

---

### T16 Pick cards view
**Goal:** Cards that make the case at a glance and hold up to inspection.
**Implements:** F7.3, F4 · **Depends on:** T14 · **P0 · M**

**Done when:**
- Every F4 field is shown.
- The four criteria are shown visually and comparably across cards, so the "underrated" pattern (quality and momentum ahead of popularity) is visible at a glance.
- The ranking is shown.
- The skeptic verdict appears as a badge, with killed cards struck through (when T12 ships).
- Fact-check flags appear next to the number concerned (when T13 ships).
- **(P1)** Clicking a number opens its cited tool output with the value highlighted.

---

### T19 Real runs from the UI
**Goal:** The launcher can start a real agent run on a real universe, not only replay the recorded fake run.
**Implements:** F7.1, F7.2 · **Depends on:** T15, T09 · **P0 · S** · *From the [findings](../findings/2026-10-24-real-data-baseline.md) (problem 7)*

**Done when:**
- The launcher has a clear choice between **Replay** (free, offline) and **Live run** (real model, costs money), with replay as the default.
- A live run from the UI streams its trace and shows its cards, using `execute_run(settings, on_start=...)`.
- The spend guard's refusal shows as a readable message in the UI.
- The team has watched one real run end to end in the UI (Checkpoint B).

