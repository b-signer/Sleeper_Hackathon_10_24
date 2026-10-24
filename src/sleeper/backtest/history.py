"""Star histories (S1): star counts at past dates, from outside GitHub's API.

GitHub no longer lists stargazers (decisions.md), so past star counts come from a file. Three
``StarHistory`` implementations (types.py):

- ``CsvStarHistory``: a plain ``repo,date,stars`` file, any number of dates per repo. Exact if
  the file is (e.g. an export from a star-history service, or snapshots we took ourselves).
- ``EventCountHistory``: the realistic GH Archive route. BigQuery's public ``githubarchive``
  dataset has every WatchEvent (= star) since 2011, but no unstars, so it gives "stars gained
  since the as-of date", not a count. ``stars_at = max(0, stars_now - watch_events_since)``.
  ``gharchive_bigquery_sql`` writes the query that produces its CSV.
- ``FixtureHistory``: a dict, for tests.

Repo names are matched case-insensitively. Renamed repos are not followed: a file keyed by an old
name simply has no value for the new one (the backtest then drops the row as ``no_history``).
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

from sleeper.contracts.universe import Universe

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = [c.strip().lower() for c in reader.fieldnames or []]
        missing = [c for c in required if c not in fields]
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(missing)}; expected {','.join(required)}")
        reader.fieldnames = fields
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


class CsvStarHistory:
    """Stars from a ``repo,date,stars`` CSV (date YYYY-MM-DD). The value at a date is the latest row on or before it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.name = f"csv:{self.path.as_posix()}"
        points: dict[str, dict[date, int]] = {}
        for i, row in enumerate(_read_csv(self.path, ("repo", "date", "stars")), start=2):
            if not row["repo"]:
                continue
            try:
                d, n = date.fromisoformat(row["date"]), int(row["stars"])
            except ValueError as e:
                raise ValueError(f"{self.path}:{i}: bad row {row}: {e}") from e
            if n < 0:
                raise ValueError(f"{self.path}:{i}: negative stars")
            points.setdefault(row["repo"].lower(), {})[d] = n  # a repeated date: the last row wins
        self._series = {k: sorted(v.items()) for k, v in points.items()}

    def stars_at(self, repo: str, when: date) -> int | None:
        series = self._series.get(repo.lower())
        if not series:
            return None
        i = bisect_right([d for d, _ in series], when)
        return series[i - 1][1] if i else None  # no point on or before `when`: unknown


class EventCountHistory:
    """Approximate stars at one past date from GH Archive WatchEvent counts.

    The CSV (``repo,watch_events_since``) counts stars given after the as-of date up to the export,
    as written by ``gharchive_bigquery_sql``. ``stars_now`` is each repo's current star count
    (usually the current universe's). Approximate, and the caveat says so:

    - unstars are not in GH Archive, so stars removed since the date make the estimate too low
      (a repo that lost stars looks smaller in the past than it was);
    - ``stars_now`` and the export should be taken at about the same time; stars between the two
      shift the estimate by that many;
    - events are matched by name at event time, so a repo renamed since the date is undercounted.

    Only the exported as-of date is known; every other date returns None.
    """

    CAVEAT = (
        "Past star counts are estimated from GH Archive as stars now minus WatchEvents since the as-of "
        "date. GH Archive has no unstar events, so repos that lost stars since then are estimated too low; "
        "repos renamed since then are undercounted."
    )

    def __init__(self, path: str | Path, as_of: date, stars_now: Mapping[str, int]) -> None:
        self.path = Path(path)
        self.as_of = as_of
        self.name = f"gharchive-watch-events:{self.path.as_posix()}@{as_of.isoformat()}"
        self._now = {k.lower(): v for k, v in stars_now.items()}
        self._since: dict[str, int] = {}
        for i, row in enumerate(_read_csv(self.path, ("repo", "watch_events_since")), start=2):
            if not row["repo"]:
                continue
            try:
                n = int(row["watch_events_since"] or 0)
            except ValueError as e:
                raise ValueError(f"{self.path}:{i}: bad row {row}: {e}") from e
            key = row["repo"].lower()
            self._since[key] = self._since.get(key, 0) + n  # rows differing only in case add up

    @classmethod
    def from_universe(cls, path: str | Path, as_of: date, universe: Universe) -> EventCountHistory:
        """stars_now taken from a (current) universe's rows."""
        return cls(path, as_of, {r.repo: r.metrics.stars for r in universe.rows})

    def stars_at(self, repo: str, when: date) -> int | None:
        if when != self.as_of:
            return None
        key = repo.lower()
        now = self._now.get(key)
        if now is None:
            return None
        # A repo in the universe but absent from the export got no stars since the date (the query
        # only returns repos with at least one event), so its count then is its count now.
        return max(0, now - self._since.get(key, 0))


class FixtureHistory:
    """Dict-based history for tests: ``{repo: stars}`` (any date) or ``{repo: {date: stars}}``."""

    def __init__(self, mapping: Mapping[str, int | Mapping[date, int]], name: str = "fixture") -> None:
        self.name = name
        self._map = {k.lower(): v for k, v in mapping.items()}

    def stars_at(self, repo: str, when: date) -> int | None:
        v = self._map.get(repo.lower())
        if v is None or isinstance(v, int):
            return v
        past = [d for d in v if d <= when]
        return v[max(past)] if past else None


def gharchive_bigquery_sql(repos: Iterable[str], as_of: date, until: date | None = None) -> str:
    """BigQuery SQL for the public ``githubarchive`` dataset that writes the ``EventCountHistory`` CSV.

    Counts distinct users with a WatchEvent (= star) per repo on days strictly after ``as_of``
    (``stars_at`` means "at the end of the as-of day") up to ``until`` inclusive, or up to today if
    None. Repo names are lower-cased so they match case-insensitively. Repos with no events since
    the date are absent from the output (read as 0 by ``EventCountHistory``). Scans one daily table
    per day in the window, so a long window over ``githubarchive.day`` reads a lot of data.
    """
    names = sorted({r.strip().lower() for r in repos if r.strip()})
    if not names:
        raise ValueError("no repos")
    bad = [r for r in names if not _REPO_RE.match(r)]
    if bad:
        raise ValueError(f"not an owner/name repo: {bad[:3]}")  # also keeps quotes out of the SQL
    if until is not None and until <= as_of:
        raise ValueError("until must be after as_of")
    start = as_of.strftime("%y%m%d")  # githubarchive.day.20* -> suffix is YYMMDD
    end = f"'{until.strftime('%y%m%d')}'" if until else "FORMAT_DATE('%y%m%d', CURRENT_DATE())"
    in_list = ",\n    ".join(f"'{r}'" for r in names)
    return (
        f"-- sleeper S1 backtest: stars given after {as_of.isoformat()} "
        f"(through {until.isoformat() if until else 'today'}), per repo.\n"
        "-- Save the result as CSV (columns repo,watch_events_since) for EventCountHistory.\n"
        "SELECT\n"
        "  LOWER(repo.name) AS repo,\n"
        "  COUNT(DISTINCT actor.id) AS watch_events_since\n"
        "FROM `githubarchive.day.20*`\n"
        "WHERE\n"
        f"  _TABLE_SUFFIX > '{start}'\n"
        f"  AND _TABLE_SUFFIX <= {end}\n"
        "  AND type = 'WatchEvent'\n"
        "  AND LOWER(repo.name) IN (\n"
        f"    {in_list}\n"
        "  )\n"
        "GROUP BY repo\n"
        "ORDER BY repo\n"
    )
