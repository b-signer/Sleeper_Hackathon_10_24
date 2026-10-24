# Criteria scores

How the four 0–100 scores in every universe row (`CriteriaScores`, D8) are computed, by `src/sleeper/scoring.py` (T07). The same paragraph is written into each universe file's `header.scoring`.

Scores are percentiles within this universe (0 = lowest repo, 100 = highest; ties share the average rank), and a criterion built from several metrics is the mean of their percentiles, rounded to 0.1. Higher always means more of the criterion. Popularity: stars. Momentum: lifetime star velocity (stars_per_day) and forks created in the last 30 days (forks_30d), equally weighted (D13; star_events_30d is not used because the events feed caps it). Quality: commits in the last 90 days, distinct commit authors in the last 90 days, number of releases, recency of the last commit, and README length, equally weighted, so an active, multi-author, released and documented repo scores high. Newness: creation date, so the newest repo scores 100. A metric that is unknown (None) for a repo counts as the median (50) for that repo, neither rewarded nor penalised; the other repos' percentiles are computed among the known values only.

Notes:

- Percentiles are relative: the same repo can score differently in another topic's universe, or after a rebuild.
- "Underrated" is the agent's judgement, not a score. A sleeper typically shows low popularity with high momentum, quality or newness.
- Building a universe: `uv run sleeper build-universe <topic>` (topics in [`topics.toml`](../topics.toml)), written to `data/universes/<topic>.json`. See `src/sleeper/universe.py` for the search, exclusion and sampling steps.
