# WP5: Grounding checks

WP5 proves in code that the agent's facts are real, and produces each run's grounding scorecard. Its checks read only the run records (C4).

---

### T5.1 Deterministic grounding checks and scorecard
**Goal:** Every card fact traces back to a tool output, or it gets flagged.
**Implements:** F6.1–F6.5, G3 · **Depends on:** T0.3 · **P0 · L** · *F6.1 is never cut.*

**Done when:**
- One command runs all the checks on a run and writes the grounding scorecard (contract from T0.2) with totals, flags and a one-sentence summary.
- **Numbers (F6.1):** key figures are compared exactly with their cited values. Numbers in free text (cards, ranking notes, the run summary, bear arguments) are matched, allowing for display rounding and scaling, with the exemptions listed in the PRD.
- **Arithmetic (F6.2):** every calculator call is recomputed. A derived figure that has no calculator source is flagged.
- **Universe (F6.3)**, **why surfaced (F6.4)** and **leak (F6.5)** are checked as specified.
- Tests:
  - Flags a tampered key figure, a figure that cites the wrong place, and an uncited number in free text.
  - Accepts `1.2k` against 1,180 and `85%` against 0.85.
  - Flags a repo name that appears in no tool output.
  - Flags an uncited number in a **bear argument**.
  - Doesn't flag a future date in kill criteria.
  - The recorded fake run from T0.3 gets the expected scorecard.

---

### T5.2 Claim judge and labeled set
**Goal:** An independent check that claims say what their sources say.
**Implements:** F6.6 · **Depends on:** T0.3, T0.6 · **P0 · M**

**Done when:**
- Each claim and risk is judged on the cheap model, using only its cited outputs. The judge returns a label and a one-sentence reason, and these feed into the scorecard.
- A hand-labeled set of 15 claims exists, 5 for each label. The contradicted ones are made by tampering with real claims.
- The judge's accuracy on that set is reported on the scorecard. Below 80%, judge results are shown as advisory.
- Judge calls have their own small budget.

---

### T5.3 Planted-repo test
**Goal:** Show that the agent doesn't invent facts about a repo it can't know.
**Implements:** F6.7 · **Depends on:** T3.5, T5.1 · **P1 · S**

**Done when:**
- For a planted run, the scorecard reports passed, failed or inconclusive:
  - **Passed:** every fact the agent wrote about the planted repo (on a card or in a ranking note) matches the planted data, and the leak check finds no outside facts.
  - **Inconclusive:** the agent never researched the planted repo. Rerun once.
- Fake-run tests cover all three outcomes.

---

### T5.4 Perturbation test
**Goal:** Show that card claims follow the data rather than the model's prior.
**Implements:** F6.8 · **Depends on:** T4.1, T5.1 · **P2 · M**

**Done when:**
- The test takes a finished card, reruns the agent on a tiny universe (that repo plus 4 others) with one fact changed, and checks the card again.
- It passes if the changed figure appears with its new value, or the claims that depended on it disappear or flip.
- The result goes on the scorecard.

---

### T5.5 👤 Tool accuracy spot-check
**Goal:** Independent evidence that the data layer is right.
**Implements:** F6.9 · **Depends on:** T2.3 · **P1 · S**

**Done when:** a person has checked stars at T, commits in the last 90 days and the last commit for 10 universe repos, against the GitHub UI or star-history charts. The results, including any mismatches and why they happened, are recorded in the repo.
