"""Repo metrics (T06): the numbers behind the four criteria, for one repo.

Use::

    from sleeper.github import GitHubClient
    from sleeper.metrics import fetch_repo_metrics

    with GitHubClient() as gh:
        r = fetch_repo_metrics(gh, "astral-sh/uv", as_of=built_at)
        r.repo, r.repo_id, r.url, r.metrics  # -> UniverseRow fields (T07)

Where each number comes from (``as_of`` fixes the 30/90-day windows):

- One GraphQL query: stars, forks, created date, description, language,
  license (SPDX), releases (count and latest), default-branch commits in the
  last 90 days (count, plus the first 100 for distinct authors), last commit,
  and the 100 newest forks.
- REST ``/repos/{o}/{r}/readme`` (raw): README length. 404 means no README (0).
- REST ``/repos/{o}/{r}/events``: WatchEvents (stars) in the last 30 days.
  The feed holds at most 300 events and 30 days; paging stops at the first
  event older than the window.

Requests per repo are bounded. Typical small repo: 3 (1 GraphQL + 2 REST).
Worst case: 1 + (COMMIT_PAGES - 1) + (FORK_PAGES - 1) GraphQL queries
(1 + 9 + 4 = 14, about 1 point each) and 1 + EVENT_PAGES REST calls (4),
so at most 18. Stargazer lists are never requested (unavailable, decisions.md).

Unknown values are None, never guessed: a failed sub-request leaves only its
fields None; a capped count (more commits than COMMIT_PAGES pages for
authors, more 30-day forks than FORK_PAGES pages) is None too. Only the
GraphQL repo query is required (it carries stars and created_at); if it
fails, the error propagates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sleeper.contracts.universe import RepoMetrics
from sleeper.github import RAW_ACCEPT, GitHubClient, GitHubError, NotFound

log = logging.getLogger(__name__)

COMMIT_WINDOW_DAYS = 90
RECENT_WINDOW_DAYS = 30  # forks and star events
PAGE = 100
COMMIT_PAGES = 10  # up to 1000 commits in 90 days for distinct authors
FORK_PAGES = 5  # up to 500 forks in 30 days
EVENT_PAGES = 3  # the events feed itself stops at 300 events
EVENTS_FEED_LIMIT = 300

_AUTHOR = "nodes { author { email name user { login } } } pageInfo { hasNextPage endCursor }"

REPO_QUERY = f"""
query($owner: String!, $name: String!, $since: GitTimestamp!, $until: GitTimestamp!) {{
  repository(owner: $owner, name: $name) {{
    databaseId nameWithOwner url stargazerCount forkCount createdAt description
    isEmpty isFork isArchived
    primaryLanguage {{ name }}
    licenseInfo {{ spdxId }}
    releases(first: 1, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      totalCount nodes {{ createdAt publishedAt }}
    }}
    defaultBranchRef {{
      target {{
        ... on Commit {{
          id
          last: history(first: 1, until: $until) {{ nodes {{ committedDate }} }}
          recent: history(first: {PAGE}, since: $since, until: $until) {{ totalCount {_AUTHOR} }}
        }}
      }}
    }}
    forks(first: {PAGE}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      nodes {{ createdAt }} pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""

COMMITS_PAGE_QUERY = f"""
query($id: ID!, $since: GitTimestamp!, $until: GitTimestamp!, $after: String!) {{
  node(id: $id) {{
    ... on Commit {{ history(first: {PAGE}, since: $since, until: $until, after: $after) {{ {_AUTHOR} }} }}
  }}
}}
"""

FORKS_PAGE_QUERY = f"""
query($owner: String!, $name: String!, $after: String!) {{
  repository(owner: $owner, name: $name) {{
    forks(first: {PAGE}, after: $after, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      nodes {{ createdAt }} pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""


@dataclass(frozen=True)
class RepoMetricsResult:
    """What T07 needs for a UniverseRow, minus the scores."""

    repo: str  # canonical "owner/name" (follows renames)
    repo_id: int
    url: str
    metrics: RepoMetrics
    is_fork: bool
    is_archived: bool


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _author_key(node: dict) -> str | None:
    a = node.get("author") or {}
    login = (a.get("user") or {}).get("login")
    ident = login or a.get("email") or a.get("name")
    return ident.lower() if ident else None


def fetch_repo_metrics(gh: GitHubClient, repo: str, *, as_of: datetime | None = None) -> RepoMetricsResult:
    """Fetch RepoMetrics for "owner/name" as of ``as_of`` (default: now, UTC, to the second).

    Raises NotFound if the repo does not exist, and GitHubError if the main
    GraphQL query fails. Everything else degrades to None.
    """
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    owner, _, name = repo.partition("/")
    since90 = as_of - timedelta(days=COMMIT_WINDOW_DAYS)
    since30 = as_of - timedelta(days=RECENT_WINDOW_DAYS)

    data = gh.graphql(
        REPO_QUERY,
        {"owner": owner, "name": name, "since": _iso(since90), "until": _iso(as_of)},
        allow_partial=True,
    )
    r = data.get("repository")
    if not r:
        raise NotFound(404, "POST", "/graphql", f"repository {repo} not found")
    canonical = r["nameWithOwner"]
    c_owner, _, c_name = canonical.partition("/")

    created_at = _dt(r["createdAt"])
    age_days = max(0, (as_of - created_at).days)
    stars = r["stargazerCount"]

    commits_90d, authors_90d, last_commit_at = _commits(gh, r, since90, as_of)
    releases, last_release_at = _releases(r)

    metrics = RepoMetrics(
        stars=stars,
        stars_per_day=round(stars / max(1, age_days), 4),
        forks=r.get("forkCount"),
        forks_30d=_forks_30d(gh, r, c_owner, c_name, since30, as_of),
        **_star_events(gh, canonical, since30, as_of),
        commits_90d=commits_90d,
        authors_90d=authors_90d,
        releases=releases,
        last_release_at=last_release_at,
        last_commit_at=last_commit_at,
        readme_chars=_readme_chars(gh, canonical),
        created_at=created_at,
        age_days=age_days,
        description=r.get("description") or None,
        language=(r.get("primaryLanguage") or {}).get("name"),
        license=_license(r),
    )
    return RepoMetricsResult(
        repo=canonical,
        repo_id=r["databaseId"],
        url=r["url"],
        metrics=metrics,
        is_fork=bool(r.get("isFork")),
        is_archived=bool(r.get("isArchived")),
    )


def _license(r: dict) -> str | None:
    spdx = (r.get("licenseInfo") or {}).get("spdxId")
    # GitHub reports unrecognised licence files as NOASSERTION: a licence exists but its id is unknown.
    return None if spdx in (None, "", "NOASSERTION") else spdx


def _releases(r: dict) -> tuple[int | None, datetime | None]:
    rel = r.get("releases")
    if rel is None:
        return None, None
    nodes = rel.get("nodes") or []
    latest = _dt(nodes[0].get("publishedAt") or nodes[0].get("createdAt")) if nodes else None
    return rel.get("totalCount"), latest


def _commits(
    gh: GitHubClient, r: dict, since: datetime, until: datetime
) -> tuple[int | None, int | None, datetime | None]:
    """(commits_90d, authors_90d, last_commit_at) on the default branch."""
    if r.get("isEmpty"):
        return 0, 0, None  # no commits at all: known zero
    target = (r.get("defaultBranchRef") or {}).get("target") or {}
    last_nodes = (target.get("last") or {}).get("nodes") or []
    last_commit_at = _dt(last_nodes[0]["committedDate"]) if last_nodes else None
    recent = target.get("recent")
    if recent is None:
        return None, None, last_commit_at
    count = recent.get("totalCount")
    authors = {k for n in recent.get("nodes") or [] if (k := _author_key(n))}
    page_info, pages = recent.get("pageInfo") or {}, 1
    try:
        while page_info.get("hasNextPage"):
            if pages >= COMMIT_PAGES:
                return count, None, last_commit_at  # too many commits to count authors exactly
            d = gh.graphql(
                COMMITS_PAGE_QUERY,
                {"id": target["id"], "since": _iso(since), "until": _iso(until), "after": page_info["endCursor"]},
            )
            hist = d["node"]["history"]
            authors |= {k for n in hist["nodes"] if (k := _author_key(n))}
            page_info, pages = hist["pageInfo"], pages + 1
    except (GitHubError, KeyError, TypeError) as e:
        log.warning("commit history paging failed for %s: %s", r.get("nameWithOwner"), e)
        return count, None, last_commit_at
    return count, len(authors), last_commit_at


def _forks_30d(gh: GitHubClient, r: dict, owner: str, name: str, since: datetime, until: datetime) -> int | None:
    """Forks created in (since, until]. Pages newest first and stops at the first older fork."""
    conn = r.get("forks")
    if conn is None:
        return None
    count, pages = 0, 1
    try:
        while True:
            for node in conn.get("nodes") or []:
                t = _dt(node["createdAt"])
                if t < since:
                    return count
                if t <= until:
                    count += 1
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return count
            if pages >= FORK_PAGES:
                return None  # more than FORK_PAGES*100 forks in the window: unknown, not guessed
            d = gh.graphql(FORKS_PAGE_QUERY, {"owner": owner, "name": name, "after": page_info["endCursor"]})
            conn, pages = d["repository"]["forks"], pages + 1
    except (GitHubError, KeyError, TypeError) as e:
        log.warning("fork paging failed for %s/%s: %s", owner, name, e)
        return None


def _star_events(gh: GitHubClient, repo: str, since: datetime, until: datetime) -> dict[str, Any]:
    """star_events_30d (distinct users with a WatchEvent in the window) and star_events_capped."""
    stargazers: set = set()
    seen = 0
    reached_window_start = False
    try:
        for ev in gh.paginate(f"/repos/{repo}/events", per_page=PAGE, max_pages=EVENT_PAGES):
            seen += 1
            t = _dt(ev.get("created_at"))
            if t is None:
                continue
            if t < since:
                reached_window_start = True
                break
            if t <= until and ev.get("type") == "WatchEvent":
                actor = ev.get("actor") or {}
                stargazers.add(actor.get("id") or actor.get("login"))
    except GitHubError as e:
        log.warning("events feed failed for %s: %s", repo, e)
        return {"star_events_30d": None, "star_events_capped": None}
    capped = seen >= EVENTS_FEED_LIMIT and not reached_window_start
    return {"star_events_30d": len(stargazers), "star_events_capped": capped}


def _readme_chars(gh: GitHubClient, repo: str) -> int | None:
    try:
        text = gh.get(f"/repos/{repo}/readme", accept=RAW_ACCEPT)
    except NotFound:
        return 0  # truly no README
    except GitHubError as e:
        log.warning("README fetch failed for %s: %s", repo, e)
        return None
    return len(text) if isinstance(text, str) else None
