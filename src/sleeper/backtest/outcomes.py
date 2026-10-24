"""S1 outcome snapshot: what happened to every repo of a past universe since its as-of date.

The snapshot is taken once per past universe and saved next to it, so every run on that universe
is scored from the same numbers. Repos with no current star count (deleted, private, renamed and
not resolvable) are recorded as missing, never as zero stars.

- ``snapshot_from_universe``: stars now from a current universe file. Cheap and offline.
- ``snapshot_from_github``: stars now from GitHub, batched GraphQL with a REST fallback by repo id.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sleeper.backtest.types import Outcome, OutcomeSnapshot
from sleeper.contracts import Universe, UniverseRow
from sleeper.github import GitHubClient, GitHubHTTPError, NotFound

log = logging.getLogger(__name__)

GRAPHQL_BATCH = 50  # aliased repositories per GraphQL query
SNAPSHOT_SUFFIX = ".outcomes.json"
# REST statuses that mean "this repo is gone for us" rather than "try again later".
GONE_STATUSES = {403, 404, 410, 451}


def growth(stars_as_of: int | None, stars_now: int | None) -> float | None:
    """ln((now+1)/(as_of+1)): log growth that tolerates zero stars. None if either is unknown."""
    if stars_as_of is None or stars_now is None:
        return None
    return math.log((stars_now + 1) / (stars_as_of + 1))


def as_of_date(past: Universe) -> date:
    """The as-of date of a past universe: the UTC date part of its header ``built_at``."""
    return past.header.built_at.astimezone(UTC).date()


def _snapshot(past: Universe, stars_now: dict[str, int], *, source: str, taken_at: datetime | None) -> OutcomeSnapshot:
    """Build the snapshot from {lowercased past repo name: stars now}. Absent repos are missing."""
    outcomes, missing = [], []
    for row in past.rows:
        now = stars_now.get(row.repo.lower())
        if now is None:
            missing.append(row.repo)
        then = row.metrics.stars
        outcomes.append(Outcome(repo=row.repo, stars_as_of=then, stars_now=now, growth=growth(then, now)))
    return OutcomeSnapshot(
        topic=past.header.topic,
        as_of=as_of_date(past),
        taken_at=taken_at or datetime.now(UTC),
        source=source,
        outcomes=outcomes,
        missing=missing,
    )


# ---- From a current universe ------------------------------------------------


def snapshot_from_universe(
    past: Universe, current: Universe, *, source: str, taken_at: datetime | None = None
) -> OutcomeSnapshot:
    """Stars now from ``current``, matched by repo id first (survives renames), then by name.

    ``taken_at`` defaults to the current universe's ``built_at``, since that is when its stars were read.
    """
    by_id = {r.repo_id: r for r in current.rows}
    stars_now: dict[str, int] = {}
    for row in past.rows:
        match = by_id.get(row.repo_id) or current.get(row.repo)
        if match is not None:
            stars_now[row.repo.lower()] = match.metrics.stars
    return _snapshot(past, stars_now, source=source, taken_at=taken_at or current.header.built_at)


# ---- From GitHub ------------------------------------------------------------


def _batch_query(rows: list[UniverseRow]) -> str:
    # Repo names match ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$, so json.dumps quoting is safe GraphQL.
    parts = []
    for i, row in enumerate(rows):
        owner, _, name = row.repo.partition("/")
        parts.append(
            f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
            "{ databaseId nameWithOwner stargazerCount }"
        )
    return "query {\n  " + "\n  ".join(parts) + "\n}"


def _stars_by_id(gh: GitHubClient, repo_id: int) -> int | None:
    """REST lookup by numeric id, which follows renames and transfers. None if the repo is gone."""
    try:
        data: Any = gh.get(f"/repositories/{repo_id}")
    except NotFound:
        return None
    except GitHubHTTPError as e:
        if e.status in GONE_STATUSES:
            return None
        raise
    return data.get("stargazers_count") if isinstance(data, dict) else None


def snapshot_from_github(past: Universe, gh: GitHubClient, *, taken_at: datetime | None = None) -> OutcomeSnapshot:
    """Stars now for every past row from GitHub.

    One GraphQL query per GRAPHQL_BATCH repos by owner/name. A row whose name no longer resolves, or
    resolves to a different repo id (the name was reused), is retried by id over REST. Still not
    found means missing. Rate limits and network failures raise: a half-taken snapshot is worse than none.
    """
    stars_now: dict[str, int] = {}
    retry: list[UniverseRow] = []
    rows = list(past.rows)
    for start in range(0, len(rows), GRAPHQL_BATCH):
        batch = rows[start : start + GRAPHQL_BATCH]
        data = gh.graphql(_batch_query(batch), allow_partial=True)
        for i, row in enumerate(batch):
            node = data.get(f"r{i}")
            if node and node.get("databaseId") == row.repo_id and node.get("stargazerCount") is not None:
                stars_now[row.repo.lower()] = node["stargazerCount"]
            else:
                retry.append(row)
    for row in retry:
        stars = _stars_by_id(gh, row.repo_id)
        if stars is not None:
            stars_now[row.repo.lower()] = stars
        else:
            log.info("no current star count for %s (id %s): recorded as missing", row.repo, row.repo_id)
    return _snapshot(past, stars_now, source="github", taken_at=taken_at)


# ---- Files ------------------------------------------------------------------


def snapshot_path(past_universe_path: str | Path) -> Path:
    """Default snapshot path: ``<past universe stem>.outcomes.json`` next to the past universe."""
    p = Path(past_universe_path)
    return p.with_name(p.stem + SNAPSHOT_SUFFIX)


def save_snapshot(snapshot: OutcomeSnapshot, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(path: str | Path) -> OutcomeSnapshot:
    return OutcomeSnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))
