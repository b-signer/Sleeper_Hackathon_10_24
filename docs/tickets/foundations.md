# Foundations

### T01 Project skeleton
**Goal:** Anyone can clone the repo, install it with one command, and run the tests.
**Implements:** C6 · **Depends on:** none · **P0 · S**

**Done when:**
- One install command and one test command are documented in the root README.
- One command-line entry point prints help.
- A template for environment variables lists the GitHub token, the OpenRouter key, the model IDs and an offline switch.
- Real secrets, the response cache and scratch run output are git-ignored.
- Tests that hit real APIs are separate from offline tests and don't run by default.

---

### T02 Shared contracts
**Goal:** Define once the data shapes that cross ticket boundaries, so tickets can be built in parallel.
**Implements:** C5, F1.3, F2–F4, F7, C3 · **Depends on:** T01 · **P0 · M** · *Freeze ~0:45*

**Contracts:**
- Run settings
- Universe file and its rows, including metrics and the four criteria scores
- Each tool's input and output, plus a common error shape
- Pick card and submission
- Run event log (event types) and run result (status values)

**Done when:**
- Every contract can be validated by code and has a short readable reference next to it.
- Each field the PRD requires is present.
- A round-trip test exists for each contract.
- The freeze is announced in a commit message. After the freeze, changes follow C5.

**Hints:** `archive/prd-v0.2-signer.md` §11, §14 and §15 have worked examples. They are a starting point only.

---

### T03 Fake data
**Goal:** Tools, the agent loop and the UI can all be built and tested with no network.
**Implements:** C4 · **Depends on:** T02 · **P0 · S** · *Target ~1:00*

**Done when:**
- A fake universe of about 10 repos exists, valid against the contract, with varied profiles: rising, stalled, single-maintainer, already popular.
- Canned tool outputs exist for every fake repo.
- One **recorded fake run** exists (event log plus result), valid against the contracts, with 3 cards. The UI is built on it.

---

### T04 👤 Keys and spend limit
**Goal:** Make overspending impossible and give everyone working credentials.
**Implements:** C2, D10 · **Depends on:** none · **P0 · S** · *Before any LLM call*

**Done when:**
- The OpenRouter key has a $50 limit, confirmed through the key info endpoint.
- Each teammate has their own GitHub token and a filled-in local environment file.
