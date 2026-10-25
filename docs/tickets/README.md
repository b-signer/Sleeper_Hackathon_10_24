# MVP tickets

The [PRD](../prd.md) broken into 17 tickets, each self-contained so a person or an agent can pick it up cold. The full v0.3 plan (51 tickets, with a backtest) is in [`../archive/v0.3-full-plan/`](../archive/v0.3-full-plan/) for stretch work.

## How to work a ticket

1. **Read** the [PRD](../prd.md), the [decisions](../decisions.md), the contracts from T02, and your ticket.
2. **Decide the how yourself.** The ticket states the outcome and how to check it. Hints are optional.
3. **Build against fake data first** (T03), so you never wait on GitHub or on another ticket.
4. **Done means:** every "done when" item is demonstrated, by a test or by a command and its output. Offline tests pass, no secrets are committed, and any findings others need go in the [decisions learnings log](../decisions.md).

Sizes are time budgets: **S** is about 30 minutes, **M** about 45, **L** about 90. 👤 marks a ticket that needs a person.

## Index

**Status as of 2026-10-24, after T09, T15 and T16 were merged.** Update this column when you start or finish a ticket. Build notes: [T09-plan.md](T09-plan.md), [T10-plan.md](T10-plan.md).

| ID | Status | Title | Track | Pri | Size | Depends on | File |
|---|---|---|---|---|---|---|---|
| T01 | ✅ Done | Project skeleton | Foundations | P0 | S | none | [foundations](foundations.md) |
| T02 | ✅ Done | Shared contracts | Foundations | P0 | M | T01 | [foundations](foundations.md) |
| T03 | ✅ Done | Fake data | Foundations | P0 | S | T02 | [foundations](foundations.md) |
| T04 👤 | ✅ Done | Keys and spend limit | Foundations | P0 | S | none | [foundations](foundations.md) |
| T05 | ✅ Done | GitHub access layer | Data | P0 | M | T01 | [data](data.md) |
| T06 | ✅ Done | Repo metrics | Data | P0 | M | T05 | [data](data.md) |
| T07 | ✅ Done | Universe builder and criteria scores | Data | P0 | M | T02, T06 | [data](data.md) |
| T08 👤 | 🔨 Built, awaiting human check | Build the demo universes | Data | P0 | S (waiting) | T07 | [data](data.md) |
| T09 | ✅ Done | Agent tools | Agent | P0 | M | T02, T03 | [agent](agent.md) |
| T10 | ✅ Done | Agent loop, budget, run records | Agent | P0 | L | T02, T03, T04 | [agent](agent.md) |
| T11 | ✅ Done (luna; confirm on T18 universes) | Prompt and tuning (Checkpoint B) | Agent | P0 | M | T09, T10, T08 | [agent](agent.md) |
| T12 | ✅ Done | Skeptic review | Agent | P1 | S | T10 | [agent](agent.md) |
| T13 | ✅ Done | Fact check | Agent | P1 | S | T03 | [agent](agent.md) |
| T14 | ✅ Done | Server, event stream, replay | UI | P0 | M | T02 (T03 for tests) | [ui](ui.md) |
| T15 | ✅ Done | Launcher, live trace, budget meter | UI | P0 | M | T14 | [ui](ui.md) |
| T16 | ✅ Done | Pick cards view | UI | P0 | M | T14 | [ui](ui.md) |
| T17 👤 | ⏳ Waiting | Demo runs, picks on record, rehearsal | Demo | P0 | M | all P0 | [demo](demo.md) |
| T18 | ✅ Done | Topic relevance and quality score (real-data findings) | Data | P0 | M | T07 | [data](data.md) |
| T19 | ✅ Done | Real runs from the UI | UI | P0 | S | T15, T09 | [ui](ui.md) |
| S1 | 🔨 In progress (Benny): framework built, needs GH Archive data | Rough backtest | Stretch | | L | T07, T10 | [demo](demo.md) |
| S2 | ✅ Done | Linked papers | Stretch | | M | T09 | [demo](demo.md) |

## Schedule and checkpoints

To see the graph, open [`dependency-graph.html`](dependency-graph.html) in a browser.

```
0:00–0:30  T04 👤 keys + spend limit · T01 skeleton · T02 contracts (freeze ~0:45)
0:30–1:00  T03 fake data (~1:00) · T05 GitHub layer · T14 server (on contracts)
1:00–2:00  T06 metrics · T09 tools · T10 agent loop · T15 trace · T16 cards
   ▶ Checkpoint A ~2:00  fake-model run end to end, visible in the UI and replayable
2:00–3:15  T07 universe builder → T08 👤 build 2 topics (~2:45) · T11 tuning on luna
   ▶ Checkpoint B ~3:15  real run on luna ends ok, 3–5 cards with all four criteria, shown in the UI
3:15–4:15  T12 skeptic · T13 fact check · UI polish · first demo run on terra
   ▶ Checkpoint C ~4:15  a good demo run recorded as the offline backup
4:15–5:30  T17 👤 demo runs, picks committed, rehearsal · S1/S2 only if C landed early
```

**Critical paths.** Agent: T01 → T02 → T03 → T10 → Checkpoint A → T11 → Checkpoint B → T17. Data: T05 → T06 → T07 → T08 must land by about 2:45, or T11 tunes on a partial universe.

**Cut order if behind:**
1. S1 and S2
2. T13 fact check
3. T12 skeptic review
4. The click-through from a number to its source (T16)
5. The second topic

**Never cut:**
- Facts only from tools (C1)
- The spend limit
- Offline replay

## Owners

| Track | Tickets | Owner |
|---|---|---|
| Foundations, then UI | T01–T04, T14–T16 | |
| Data | T05–T08 | |
| Agent | T09–T13 | |
| Demo | T17 | |
