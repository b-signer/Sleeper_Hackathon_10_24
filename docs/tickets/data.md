# Data

### T05 GitHub access layer
**Goal:** One way to call GitHub that survives rate limits, caches everything and can run fully offline.
**Implements:** C4, D6 · **Depends on:** T01 · **P0 · M**

**Done when:**
- Requests use the configured token. A missing token fails with a clear message.
- Low or exhausted rate limits cause waiting and retrying within a bounded time, never a crash. A simulated test shows this.
- Every response is cached on disk. A repeated request is served from the cache, and the cache file can be copied to another machine.
- In offline mode, nothing touches the network, and a cache miss raises a clear, distinct error.

---

### T06 Repo metrics
**Goal:** For one repo, the numbers behind the four criteria.
**Implements:** F1.3, PRD §4 · **Depends on:** T05 · **P0 · M**

**Must provide at least:**
- **Popularity:** stars
- **Momentum:** stars gained in the last 30 days
- **Quality:** commits in the last 90 days, distinct authors in the last 90 days, number of releases and date of the latest, date of the last commit, README length
- **Newness:** created date and age in days
- **Display:** description, language and license

**Done when:**
- Each metric is tested on fixture data.
- Values that can't be determined are reported as unknown, never guessed.
- A live check on 3 repos matches the GitHub UI.
- Requests per repo are bounded (state the number). In particular, stars gained recently must not require paging through every stargazer of a big repo.

---

### T07 Universe builder and criteria scores
**Goal:** Build and save a topic's universe, with metrics and scores for every repo.
**Implements:** F1.1–F1.4, D7, D8 · **Depends on:** T02, T06 · **P0 · M**

**Done when:**
- A topics file defines `rust-devtools` and `local-first` (and optionally `llm-evals`), each with a label and how to find its repos.
- One command builds a universe for a topic: matching public repos in the star range and created window, excluding forks, archived repos and "awesome" lists.
- If more than about 200 repos match, a seeded random sample is taken rather than the top 200, so the universe isn't biased toward popular repos.
- Every row has the T06 metrics and a score for each of the four criteria. The scores are computed by code from the metrics (D8) and documented in one short paragraph: how each score is computed, and why a higher score is better.
- The file is valid against the contract and records when and how it was built.
- A 200-repo build finishes within about one token's hourly rate limit. Report the time and request count.

**Hints:** topic search queries from `archive/prd-v0.2-signer.md` §10.1 return 150–450 repos each.

---

### T08 👤 Build the demo universes
**Goal:** Prebuilt universes that the demo and tuning runs use.
**Implements:** F1.4, C4 · **Depends on:** T07 · **P0 · S (mostly waiting)** · *Target ~2:45*

**Done when:**
- Universes for 2 topics are built and committed.
- A person has sanity-checked the header counts and a few rows.
- The response cache is copied to the demo machine.
