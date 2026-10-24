# Work packages and tickets

This folder breaks the [PRD](../prd.md) into work packages (WP) and tickets (T). Each ticket is self-contained, so a person or an agent can pick it up cold.

## How to work a ticket

1. **Read** the [PRD](../prd.md), the [decisions](../decisions.md), the contracts produced by T0.2, and your ticket. Nothing else is required. The [archive](../archive/) is background only and not binding.
2. **Decide the how yourself.** A ticket states the outcome and how to check it. Its **Hints** are optional notes from earlier drafts. Ignore them if you find a better way, as long as the acceptance criteria hold.
3. **Stay in your area.** Don't edit code owned by another ticket. If you need something from it, use its contract or leave a note on that ticket.
4. **Contracts are binding (C6).** If your work needs a contract change, change the contract and update its tests in the same commit, and say so in the commit message.
5. **Done means:**
   - Every acceptance criterion passes and is demonstrated, either by a test or by the command plus its output.
   - Offline tests still pass.
   - No secrets are committed.
   - Anything you learned that others need (a failed assumption, a new trap) is **appended** to the learnings log in [`decisions.md`](../decisions.md) §5.
6. Tests that hit real APIs are kept separate from offline tests and run on purpose, never by default.

## Ticket format

```
### Tn.m Title
Goal · Implements (PRD IDs) · Depends on · Priority / size
Done when: observable acceptance criteria
Out of scope
Hints (optional, not binding)
```

Sizes are time budgets: **S** is about 30 minutes, **M** about 45, **L** about 90, for one focused agent with a person reviewing. A ticket marked 👤 needs a human (accounts, keys, judgment).

## Work packages

| WP | Purpose | File |
|---|---|---|
| WP0 | Foundations: project skeleton, contracts, fake data, assumption checks, spend safety | [WP0-foundations.md](WP0-foundations.md) |
| WP1 | GitHub data as of T | [WP1-github-data.md](WP1-github-data.md) |
| WP2 | Topic universes | [WP2-universe.md](WP2-universe.md) |
| WP3 | Agent tools | [WP3-tools.md](WP3-tools.md) |
| WP4 | Agent runtime and bear pass | [WP4-agent.md](WP4-agent.md) |
| WP5 | Grounding checks | [WP5-grounding.md](WP5-grounding.md) |
| WP6 | Backtest evaluation | [WP6-backtest.md](WP6-backtest.md) |
| WP7 | Web UI | [WP7-ui.md](WP7-ui.md) |
| WP8 | Release: live picks, demo | [WP8-release.md](WP8-release.md) |
| WP9 | Extension: arXiv papers | [WP9-papers.md](WP9-papers.md) |

## Ticket index

**Core** (✓) is the smallest set that still gives a working demo: backtest mode on two topics (one if forced to cut, which then misses G5), the numbers check, the universe + random + momentum baselines, and offline replay. Protect it first. The other P0 tickets are expected but come after Core.

| ID | Title | Pri | Core | Size | Depends on |
|---|---|---|---|---|---|
| T0.1 | Project skeleton | P0 | ✓ | S | none |
| T0.2 | Shared contracts (two-stage freeze) | P0 | ✓ | M | T0.1 |
| T0.3 | Fake data (fixtures) | P0 | ✓ | M | T0.2 |
| T0.4 | Verify external assumptions | P0 | ✓ | M | T0.1, T0.5 |
| T0.5 👤 | Accounts, keys and spend limit | P0 | ✓ | S | none |
| T0.6 | Runtime core (tool registry, run context, metered LLM client, fake model) | P0 | ✓ | M | T0.2 |
| T1.1 | GitHub access layer | P0 | ✓ | M | T0.1 |
| T1.2 | Stars as of T | P0 | ✓ | M | T1.1, A1 |
| T1.3 | Activity as of T | P0 | ✓ | M | T1.1 |
| T1.4 | README and description as of T | P0 | ✓ | S | T1.1 |
| T1.5 | Future-data guard | P0 | ✓ | S | T0.2 |
| T1.6 | Live-mode data | P0 | | S | T1.2–T1.4 |
| T1.7 | Contributors as of T | P1 | | S | T1.1 |
| T2.1 | Topic definitions | P0 | ✓ | S | none |
| T2.2a | Universe candidates | P0 | ✓ | M | T1.1, T1.5, T2.1 (code); T1.2 (real build) |
| T2.2b | Universe metrics | P0 | ✓ | M | T2.2a, T1.3, T1.4 |
| T2.3 👤 | Build and commit backtest universes | P0 | ✓ | M (waiting) | T1.2, T2.2a, T2.2b |
| T2.4 | Build live universes (2 topics) | P0 | | S | T1.6, T2.2b |
| T3.1 | Browse and screen tools | P0 | ✓ | M | T0.3, T0.6 |
| T3.2 | Deep-dive and README tools | P0 | ✓ | M | T0.3, T0.6, then T1.2–T1.4 |
| T3.3 | Calculator tool | P0 | ✓ | S | T0.2 |
| T3.4 | Submit tool and validation | P0 | ✓ | M | T0.2, T0.6 |
| T3.5 | Planted-repo injection | P1 | | S | T0.3, T3.2 |
| T3.6 | Contributors tool | P1 | | S | T1.7, T3.2 |
| T4.1 | Agent loop | P0 | ✓ | L | T0.3, T0.6 (stub tools; then T3.1, T3.4) |
| T4.2 | Budget and spend control | P0 | ✓ | M | T4.1 |
| T4.3 | Bounded context | P0 | ✓ | M | T4.1 |
| T4.4 | Run records | P0 | ✓ | M | T0.2 |
| T4.5 | Command-line entry and `run` command | P0 | ✓ | S | T4.1 |
| T4.6 | System prompt and tuning (Checkpoint B) | P0 | ✓ | M | T4.1, T4.3, T3.2, first finished universe |
| T4.7 | Bear pass | P0 | | M | T0.6, T4.4 |
| T4.8 | Run pipeline integration (Checkpoint A) | P0 | ✓ | S | T4.1, T4.4, T4.7, T5.1, T7.1, T7.3 |
| T5.1 | Deterministic grounding checks and scorecard | P0 | ✓ | L | T0.3 |
| T5.2 | Claim judge and labeled set | P0 | | M | T0.3, T0.6 |
| T5.3 | Planted-repo test | P1 | | S | T3.5, T5.1 |
| T5.4 | Perturbation test | P2 | | M | T4.1, T5.1 |
| T5.5 👤 | Tool accuracy spot-check | P1 | | S | T2.3 |
| T6.1 | Outcome snapshot | P0 | ✓ | S | T2.3 |
| T6.2 | Baselines and scoring command (Checkpoint C) | P0 | ✓ | M | T6.1, T0.6, T6.3, T4.8 |
| T6.3 | Statistics | P0 | ✓ | M | T0.2, T0.3 |
| T6.4 | Contamination controls | P0 | | M | T0.5, T0.6 |
| T6.5 👤 | Backtest campaign | P0 | ✓ | M | T2.3, T4.8, T5.1, T6.1–T6.4 |
| T7.1 | Server and event stream | P0 | ✓ | M | T4.4 |
| T7.2 | Launcher, live trace, budget meter | P0 | ✓ | M | T7.1 |
| T7.3 | Cards with source drill-down | P0 | ✓ | M | T7.1 |
| T7.4 | Grounding and backtest panels | P0 | ✓ | M | T7.1, T0.3 fake results |
| T7.5 | Runs list and offline replay | P0 | ✓ | S | T7.1 |
| T8.1 | Live picks on the record | P1 | | S | T4.5, T2.4 |
| T8.2 👤 | Demo rehearsal and backup | P0 | ✓ | M | Core |
| T9.1 | Linked papers as signals | Ext | | M | T3.2 |
| T9.2 | Paper discovery mode | Stretch | | L | T9.1 |

T4.8 needs T4.7 only if the bear pass exists. Without it, the pipeline skips that stage.

## Schedule and checkpoints

To see the dependency graph, open [`dependency-graph.html`](dependency-graph.html) in a browser. The tables in this file are the source of truth.


Everything after WP0 builds against **fake data and stub tools first**, so no track waits on GitHub or on another track. The times are targets. They only hold if the data critical path (T1.1 → T1.2 → T2.2a → T2.3) has a dedicated owner from minute 0, with nothing else assigned.

```
0:00–0:20  T0.5 👤 keys + spend limit · T0.1 skeleton · T2.1 topics
0:20–0:50  T0.2 contracts, stage-1 freeze ~0:50 · T1.1 GitHub layer · T0.4 (A1 first)
0:50–1:30  T0.3 fixtures (~1:15) · T0.6 runtime core · T1.2–T1.5 · T3.3 · T4.4
           T0.2 stage-2 freeze ~1:30
1:30–2:15  T2.2a → T2.3 👤 candidate builds running by ~2:00 (one token per topic)
           T3.1 · T3.2 (fakes) · T3.4 · T4.1 (stub tools first) · T4.7 · T5.1 · T5.2 · T6.3 · T6.4 canary
           T7.1–T7.3 · T7.5
2:15–3:15  T4.2 · T4.3 · T4.5 · T4.8 · T2.2b metrics · T1.6 + T2.4 live builds · T3.2 on real data
           T3.5 · T1.7 · T3.6
   ▶ Checkpoint A ~2:45  fake-model run → bear → grounding → UI, end to end (T4.8)
3:15–4:00  T2.3 first final universe ~3:15 (others by ~3:45) · T4.6 tuning on luna · T6.1 · T6.2 · T7.4
           T5.3 · T5.5 👤 · T9.1 only after Checkpoint B and only if everything is green
   ▶ Checkpoint B ~3:30  real runs on luna end ok, grounding clean (T4.6)
   ▶ Checkpoint C ~4:15  one fully scored backtest run, recorded as the fallback demo (T6.2)
4:00–4:50  T6.5 👤 backtest campaign (runs in parallel) · live runs · T8.1 live picks
4:50–5:30  T8.2 👤 rehearsal (on the fallback run if the campaign is late)
```

**Critical path:** T0.1 → T1.1 → T1.2 → T2.2a → (rate-limited build) → T2.2b → T2.3 → T6.1 → T6.2 (Checkpoint C) → T6.5 → T8.2. If a checkpoint slips more than 20 minutes, apply the cut order right away. Don't wait until the end.

**Cut order if behind:**
1. WP9
2. T5.4
3. T3.6 and T1.7
4. Naive-LLM baseline
5. Activity baseline
6. T5.3 planted test
7. UI polish
8. Live mode (demo in backtest mode)
9. The third topic (`llm-evals`)

**Never cut:**
- C1 (no look-ahead) and T1.5
- The surfacing rule (F3.10)
- The numbers check (F6.1)
- The spend limit
- A backtest with the universe, random and momentum baselines plus ranking spread

## Owners

**Fill this in before dispatching anything.** Each owner is the person who reviews and merges an agent's work. The data critical-path owner has nothing else.

| Area | Tickets | Owner |
|---|---|---|
| Data critical path | T1.1, T1.2, T2.1, T2.2a, T2.3 | |
| Data support | T1.3–T1.7, T2.2b, T2.4, T6.1 | |
| Agent | T0.6, T3.x, T4.x | |
| Eval | T5.x, T6.2–T6.5 | |
| UI and demo | T7.x, T8.x | |
| Foundations (first ~1:30, then rejoin a track) | T0.1–T0.5 | |
| Extension | T9.x | |
