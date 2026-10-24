"""Past universes (S1): a current universe file rebuilt as it would have looked on an earlier date.

Use::

    from sleeper.backtest.history import CsvStarHistory
    from sleeper.backtest.past_universe import build_past_universe, write_past_universe

    past, report = build_past_universe(current, date(2026, 4, 1), CsvStarHistory("stars.csv"),
                                       source_path="data/universes/local-first.json")
    write_past_universe(past, report)  # data/universes/backtest/local-first@2026-04-01.json

The repo set comes from the current universe, so the builder's topic relevance and exclusions
(T07, T18) carry over. Scores come from ``scorer`` (default ``sleeper.scoring.score_universe``,
looked up at call time), recomputed as percentiles within the past set. "As of" means the end of
the as-of day, UTC (``as_of_end``).

How each row is rebuilt. Unknown is None, never guessed; when a value can't be shown to be the
same at the as-of date as now, it is None.

- Dropped: repos created after ``as_of_end`` (``created_after_as_of``), and repos the history has
  no value for (``no_history``). Counted in ``report.dropped``.
- ``stars``: from the history. ``age_days`` = whole days from ``created_at`` to ``as_of_end``;
  ``stars_per_day`` = stars / max(1, age_days), as metrics.py computes it. ``created_at`` is kept.
- ``forks``: None. The current count includes forks made after the date.
- ``star_events_30d`` / ``star_events_capped``: None. The events feed only covers the last 30 days.
- ``readme_chars``: kept (the current README), with a caveat. Use ``asof_readme_source`` so the
  agent reads the README as it was at the date.
- ``description``, ``language``, ``license``: kept (current), with a caveat.

Window and activity fields, without ``refetch``, from the current row only:

- ``last_commit_at``: kept if it is on or before ``as_of_end`` (no commit since, so it was also the
  last commit then); else None.
- ``commits_90d`` / ``authors_90d``: 0 if the current ``last_commit_at`` is before the as-of 90-day
  window (no commits in it); else None. The current window counts are never reused: they cover a
  different 90 days.
- ``releases`` / ``last_release_at``: kept if the current latest release is on or before
  ``as_of_end`` (no release since, so the count then was the count now); ``releases == 0`` is kept
  as 0; otherwise both None. Deleted releases are not visible either way.
- ``forks_30d``: None.

With ``refetch(repo, as_of_end)`` (``make_refetcher``, which runs T06's ``fetch_repo_metrics`` with
``as_of``), ``forks_30d``, ``commits_90d``, ``authors_90d`` and ``last_commit_at`` come from it (its
windows end at the as-of date; forks deleted since are missed). Its ``releases`` and
``last_release_at`` are current, not windowed, so the release rule above is applied to them. Its
``stars``, ``forks``, star events and ``readme_chars`` are current and ignored. A refetch that fails
for a repo falls back to the no-refetch rules for that repo, and the caveats count such repos.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sleeper import __version__
from sleeper.agent.data_tools import ReadmeSource, ReadmeUnavailable, _reason
from sleeper.backtest.types import PastRowNote, PastUniverseReport, Scorer, StarHistory
from sleeper.contracts.universe import RepoMetrics, Universe, UniverseRow
from sleeper.github import RAW_ACCEPT, GitHubClient, GitHubError, NotFound
from sleeper.metrics import COMMIT_WINDOW_DAYS
from sleeper.universe import UNIVERSES_DIR, write_universe

log = logging.getLogger(__name__)

BACKTEST_DIR = UNIVERSES_DIR / "backtest"

Refetch = Callable[[str, datetime], RepoMetrics]

CAVEAT_README = (
    "readme_chars is the current README length, not the length at the as-of date; the agent should read "
    "READMEs through asof_readme_source so it sees the text as it was then."
)
CAVEAT_DISPLAY = "description, language and license are current values, not as of the date."
CAVEAT_NO_REFETCH = (
    "30/90-day activity was not refetched as of the date: forks_30d is unknown, and commits, authors, "
    "releases and last commit are only kept where the current data shows they did not change after the date."
)
CAVEAT_UNKNOWN = "forks and star events are unknown as of the date (current counts include later activity)."


def as_of_end(as_of: date) -> datetime:
    """The end of the as-of day, UTC, to the second: what "as of" means throughout the backtest."""
    return datetime.combine(as_of, time(23, 59, 59), tzinfo=UTC)


def _default_scorer() -> Scorer:
    import sleeper.scoring

    return sleeper.scoring.score_universe  # looked up now, so a rewritten scorer applies


def _scoring_text(scorer: Scorer | None, fallback: str) -> str:
    if scorer is None:
        import sleeper.scoring

        text = getattr(sleeper.scoring, "SCORING", None)
    else:
        text = getattr(sys.modules.get(getattr(scorer, "__module__", "") or ""), "SCORING", None)
    return text if isinstance(text, str) and text.strip() else fallback


def _on_or_before(t: datetime | None, end: datetime) -> bool:
    return t is not None and t <= end


def _releases_rule(releases: int | None, last_release_at: datetime | None, end: datetime) -> dict:
    if releases == 0:
        return {"releases": 0, "last_release_at": None}
    if releases is not None and _on_or_before(last_release_at, end):
        return {"releases": releases, "last_release_at": last_release_at}
    return {"releases": None, "last_release_at": None}


def _activity_from_current(m: RepoMetrics, end: datetime) -> dict:
    out: dict = {"forks_30d": None, "last_commit_at": None, "commits_90d": None, "authors_90d": None}
    if _on_or_before(m.last_commit_at, end):
        out["last_commit_at"] = m.last_commit_at
        if m.last_commit_at < end - timedelta(days=COMMIT_WINDOW_DAYS):
            out["commits_90d"] = out["authors_90d"] = 0  # last commit predates the window: known zero
    return out


def _activity_from_refetch(r: RepoMetrics, end: datetime) -> dict:
    last = r.last_commit_at if _on_or_before(r.last_commit_at, end) else None  # guard: never a later date
    return {"forks_30d": r.forks_30d, "last_commit_at": last, "commits_90d": r.commits_90d, "authors_90d": r.authors_90d}


def build_past_universe(
    current: Universe,
    as_of: date,
    history: StarHistory,
    *,
    scorer: Scorer | None = None,
    refetch: Refetch | None = None,
    source_path: str = "",
) -> tuple[Universe, PastUniverseReport]:
    """``current`` rebuilt as of the end of ``as_of`` (UTC), rescored, plus a report of what changed."""
    end = as_of_end(as_of)
    dropped: dict[str, int] = {}
    kept: list[tuple[UniverseRow, RepoMetrics]] = []
    notes: list[PastRowNote] = []
    refetch_failed = 0

    for row in current.rows:
        m = row.metrics
        if m.created_at > end:
            dropped["created_after_as_of"] = dropped.get("created_after_as_of", 0) + 1
            continue
        stars = history.stars_at(row.repo, as_of)
        if stars is None:
            dropped["no_history"] = dropped.get("no_history", 0) + 1
            continue

        activity: dict | None = None
        releases = _releases_rule(m.releases, m.last_release_at, end)
        if refetch is not None:
            try:
                r = refetch(row.repo, end)
            except Exception as e:  # noqa: BLE001  # one repo failing must not sink the backtest
                log.warning("refetch as of %s failed for %s: %s", as_of, row.repo, e)
                refetch_failed += 1
            else:
                activity = _activity_from_refetch(r, end)
                releases = _releases_rule(r.releases, r.last_release_at, end)
        if activity is None:
            activity = _activity_from_current(m, end)

        age_days = max(0, (end - m.created_at).days)
        past = RepoMetrics(
            stars=stars,
            stars_per_day=round(stars / max(1, age_days), 4),
            forks=None,
            star_events_30d=None,
            star_events_capped=None,
            **activity,
            **releases,
            readme_chars=m.readme_chars,
            created_at=m.created_at,
            age_days=age_days,
            description=m.description,
            language=m.language,
            license=m.license,
        )
        kept.append((row, past))
        notes.append(PastRowNote(repo=row.repo, stars_now=m.stars, stars_as_of=stars))

    scores = (scorer or _default_scorer())([p for _, p in kept])
    if len(scores) != len(kept):
        raise ValueError(f"scorer returned {len(scores)} scores for {len(kept)} rows")
    rows = [
        row.model_copy(update={"metrics": past, "scores": s}) for (row, past), s in zip(kept, scores, strict=True)
    ]

    src = current.header
    n_dropped = sum(dropped.values())
    excluded = dict(src.counts.excluded)
    for reason, n in dropped.items():
        excluded[f"backtest_{reason}"] = n
    counts = src.counts.model_copy(
        update={"rows": len(rows), "eligible": max(len(rows), src.counts.eligible - n_dropped), "excluded": excluded}
    )
    header = src.model_copy(
        update={
            "built_at": end,  # built_at means "metrics are as of this time"
            "builder": f"sleeper {__version__} backtest as-of {as_of.isoformat()} from {src.built_at.isoformat()}",
            "created_before": as_of if src.created_before is None or src.created_before > as_of else src.created_before,
            "scoring": _scoring_text(scorer, src.scoring),
            "github_requests": None,
            "build_seconds": None,
            "counts": counts,
        }
    )
    universe = Universe(header=header, rows=rows)

    caveats = [
        f"Star counts at {as_of.isoformat()} come from {history.name}.",
        (
            f"The repo set is the current universe (built {src.built_at.isoformat()}) minus repos created after the "
            "date; repos that existed then but were deleted, renamed away or fell out of the star range since are "
            "missing, and stars_min/stars_max in the header refer to current stars."
        ),
        CAVEAT_UNKNOWN,
        CAVEAT_README,
        CAVEAT_DISPLAY,
    ]
    if extra := getattr(history, "CAVEAT", None):
        caveats.insert(1, extra)
    if refetch is None:
        caveats.append(CAVEAT_NO_REFETCH)
    elif refetch_failed:
        caveats.append(f"Refetch failed for {refetch_failed} repo(s); their activity uses the no-refetch rules.")

    report = PastUniverseReport(
        topic=src.topic,
        as_of=as_of,
        source_universe=source_path,
        source_built_at=src.built_at,
        history=history.name,
        rows_in=len(current.rows),
        rows_out=len(rows),
        dropped=dropped,
        refetched_windows=refetch is not None,
        rows=notes,
        caveats=caveats,
    )
    return universe, report


def make_refetcher(gh: GitHubClient) -> Refetch:
    """``refetch`` for build_past_universe: T06's metrics with their windows ending at the as-of time."""
    from sleeper.metrics import fetch_repo_metrics

    def refetch(repo: str, when: datetime) -> RepoMetrics:
        return fetch_repo_metrics(gh, repo, as_of=when).metrics

    return refetch


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def asof_readme_source(as_of: datetime, client: GitHubClient | None = None) -> ReadmeSource:
    """README as it was at ``as_of``: the README at the last default-branch commit on or before it.

    Two requests per repo through the cached client (both stable, so cache hits are safe):
    ``/repos/{repo}/commits?until=<as_of>&per_page=1`` for the commit, then the raw
    ``/repos/{repo}/readme?ref=<sha>``. No commit by then, or a 404 on either, is "no README"
    (None); other failures raise ReadmeUnavailable, as github_readme_source does.
    """
    lock = threading.Lock()
    holder: list[GitHubClient] = [client] if client is not None else []
    until = _iso(as_of)

    def source(row: UniverseRow) -> str | None:
        with lock:
            if not holder:
                holder.append(GitHubClient(max_wait=30, max_attempts=2, timeout=10))
            gh = holder[0]
            try:
                commits = gh.get(f"/repos/{row.repo}/commits", {"until": until, "per_page": 1})
                if not isinstance(commits, list) or not commits or not commits[0].get("sha"):
                    return None
                data = gh.get(f"/repos/{row.repo}/readme", {"ref": commits[0]["sha"]}, accept=RAW_ACCEPT)
            except NotFound:
                return None
            except GitHubError as e:
                raise ReadmeUnavailable(_reason(e)) from e
        if data is None:
            return ""
        return data if isinstance(data, str) else str(data)

    source.is_github = True  # type: ignore[attr-defined]
    source.as_of = as_of  # type: ignore[attr-defined]
    return source


def write_past_universe(universe: Universe, report: PastUniverseReport, out_dir: str | Path = BACKTEST_DIR) -> Path:
    """Write ``<topic>@<date>.json`` and ``<topic>@<date>.report.json`` in ``out_dir``. Returns the universe path."""
    out_dir = Path(out_dir)
    stem = f"{report.topic}@{report.as_of.isoformat()}"
    path = write_universe(universe, out_dir / f"{stem}.json")
    rpath = out_dir / f"{stem}.report.json"
    tmp = rpath.with_suffix(".json.tmp")
    tmp.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(rpath)
    return path
