"""Universe builder (T07, F1): topic -> GitHub search -> exclusions -> sample -> metrics -> scores.

Use::

    from sleeper.github import GitHubClient
    from sleeper.universe import build_universe, load_topics

    topic = load_topics()["local-first"]
    with GitHubClient() as gh:
        universe = build_universe(gh, topic, seed=7, max_size=200)

Steps:

1. Search. Each topic query runs as a REST repository search with the star
   range, the created window and ``fork:true`` (so forks come back and are
   counted, not silently dropped). GitHub returns at most 1,000 results per
   search, so a slice reporting more is split in half by star range until it
   fits. Results are unioned across queries and deduped by repo id.
2. Exclude, from the search results alone (no extra requests): forks, archived
   repos and awesome lists (``awesome`` in the name or a topic). One reason per
   repo, in that order, counted in ``counts.excluded``.
3. Sample. The eligible repos, sorted by id, are shuffled with
   ``random.Random(seed)``; metrics are fetched in that order until ``max_size``
   rows are collected. So with more than ``max_size`` eligible repos the
   universe is a seeded random sample (not the top N), and a repo dropped while
   fetching (``not_found``, ``fetch_error``, or newly forked/archived) is
   replaced by the next one in the shuffle.
4. Metrics (T06) for each sampled repo, all with the same ``as_of = built_at``.
5. Scores (scoring.py), percentiles within the final rows.

``counts.eligible = counts.search_total - sum(counts.excluded)``.
"""

from __future__ import annotations

import random
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from sleeper import __version__
from sleeper.contracts.settings import RunSettings
from sleeper.contracts.universe import Universe, UniverseCounts, UniverseHeader, UniverseRow
from sleeper.github import GitHubClient, GitHubHTTPError, GraphQLError, NotFound
from sleeper.metrics import RepoMetricsResult, fetch_repo_metrics
from sleeper.scoring import SCORING, score_universe

REPO_ROOT = Path(__file__).resolve().parents[2]
TOPICS_FILE = REPO_ROOT / "topics.toml"
UNIVERSES_DIR = Path("data") / "universes"

SEARCH_CAP = 1000  # GitHub search returns at most this many results per query
SEARCH_PAGE = 100

DEFAULT_STARS_MIN: int = RunSettings.model_fields["stars_min"].default
DEFAULT_STARS_MAX: int = RunSettings.model_fields["stars_max"].default
DEFAULT_CREATED_AFTER: date = RunSettings.model_fields["created_after"].default
DEFAULT_SEED: int = RunSettings.model_fields["seed"].default
DEFAULT_MAX_SIZE: int = UniverseHeader.model_fields["max_size"].default

Progress = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---- topics ------------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    queries: tuple[str, ...]
    stars_min: int = DEFAULT_STARS_MIN
    stars_max: int = DEFAULT_STARS_MAX
    created_after: date = DEFAULT_CREATED_AFTER
    created_before: date | None = None


def load_topics(path: str | Path | None = None) -> dict[str, Topic]:
    """Read the topics file (default: topics.toml at the repo root)."""
    raw = tomllib.loads(Path(path or TOPICS_FILE).read_text(encoding="utf-8"))
    topics = {}
    for tid, t in raw.items():
        queries = tuple(t.get("queries") or ())
        if not queries:
            raise ValueError(f"topic {tid!r} has no queries")
        extra = {k: t[k] for k in ("stars_min", "stars_max", "created_after", "created_before") if k in t}
        topics[tid] = Topic(id=tid, label=t.get("label") or tid, queries=queries, **extra)
    return topics


def default_out_path(topic_id: str) -> Path:
    return UNIVERSES_DIR / f"{topic_id}.json"


# ---- search ------------------------------------------------------------------


def search_qualifiers(topic: Topic, stars_min: int, stars_max: int) -> str:
    created = (
        f"created:{topic.created_after.isoformat()}..{topic.created_before.isoformat()}"
        if topic.created_before
        else f"created:>={topic.created_after.isoformat()}"
    )
    return f"stars:{stars_min}..{stars_max} {created} fork:true"


@dataclass
class SearchResult:
    items: dict[int, dict] = field(default_factory=dict)  # repo id -> search item, first seen wins
    per_query: dict[str, int] = field(default_factory=dict)  # query -> repos it returned
    slices: list[str] = field(default_factory=list)  # every full search string sent


def _search_slice(gh: GitHubClient, q: str, refresh: bool, splittable: bool) -> tuple[int, list[dict], bool]:
    """All results of one search string (at most SEARCH_CAP). Returns (total_count, items, incomplete).

    If ``splittable`` and the search reports more than SEARCH_CAP results, stops after
    the first page: the caller splits the slice instead.
    """
    items: list[dict] = []
    total, incomplete, page = 0, False, 1
    while True:
        data = gh.request(
            "GET", "/search/repositories", params={"q": q, "per_page": SEARCH_PAGE, "page": page}, refresh=refresh
        ).data
        total = data.get("total_count", 0)
        incomplete = incomplete or bool(data.get("incomplete_results"))
        batch = data.get("items") or []
        items.extend(batch)
        if (splittable and total > SEARCH_CAP) or len(batch) < SEARCH_PAGE or len(items) >= min(total, SEARCH_CAP):
            return total, items, incomplete
        page += 1


def search_topic(gh: GitHubClient, topic: Topic, *, refresh: bool = False, progress: Progress = _stderr) -> SearchResult:
    result = SearchResult()
    for query in topic.queries:
        ranges = [(topic.stars_min, topic.stars_max)]
        found = 0
        while ranges:
            lo, hi = ranges.pop(0)
            q = f"{query} {search_qualifiers(topic, lo, hi)}"
            total, items, incomplete = _search_slice(gh, q, refresh, splittable=lo < hi)
            if total > SEARCH_CAP and lo < hi:
                mid = (lo + hi) // 2
                progress(f"  search {q!r}: {total} > {SEARCH_CAP}, splitting stars {lo}..{mid} / {mid + 1}..{hi}")
                ranges[:0] = [(lo, mid), (mid + 1, hi)]
                continue
            result.slices.append(q)
            if total > SEARCH_CAP or incomplete:
                progress(f"  warning: {q!r} returned {len(items)} of {total} (capped or incomplete)")
            found += len(items)
            for it in items:
                result.items.setdefault(it["id"], it)
        result.per_query[query] = found
        progress(f"  search {query!r}: {found} repos ({len(result.items)} unique so far)")
    return result


# ---- exclusions and sampling ---------------------------------------------------


def is_awesome_list(name: str, topics: list[str] | None = None) -> bool:
    repo_name = name.rsplit("/", 1)[-1].lower()
    return "awesome" in repo_name or any("awesome" in t.lower() for t in topics or ())


def exclusion_reason(item: dict) -> str | None:
    """Why a search item is not eligible, or None. One reason, checked in a fixed order."""
    if item.get("fork"):
        return "fork"
    if item.get("archived"):
        return "archived"
    if is_awesome_list(item.get("full_name") or item.get("name") or "", item.get("topics")):
        return "awesome_list"
    return None


def sample_order(items: list[dict], seed: int) -> list[dict]:
    """Seeded shuffle of the eligible items: the order metrics are fetched in.

    Sorted by repo id first, so the order depends only on the set of repos and the seed.
    """
    ordered = sorted(items, key=lambda it: it["id"])
    random.Random(seed).shuffle(ordered)
    return ordered


# ---- build -------------------------------------------------------------------


def git_sha() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_universe(
    gh: GitHubClient,
    topic: Topic,
    *,
    seed: int = DEFAULT_SEED,
    max_size: int = DEFAULT_MAX_SIZE,
    built_at: datetime | None = None,
    refresh_search: bool = False,
    progress: Progress = _stderr,
    fetch: Callable[..., RepoMetricsResult] | None = None,
) -> Universe:
    fetch = fetch or fetch_repo_metrics  # looked up at call time, so tests can patch it
    t0 = time.monotonic()
    requests0 = gh.stats.network_requests
    built_at = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)

    progress(f"Building universe {topic.id!r} ({topic.label}), as of {built_at.isoformat()}")
    found = search_topic(gh, topic, refresh=refresh_search, progress=progress)
    excluded: dict[str, int] = {}
    eligible: list[dict] = []
    for it in found.items.values():
        reason = exclusion_reason(it)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            eligible.append(it)
    sampled = len(eligible) > max_size
    progress(
        f"  {len(found.items)} unique repos, excluded {excluded or 'none'}, {len(eligible)} eligible"
        + (f": seeded sample of {max_size} (seed {seed})" if sampled else "")
    )

    results: list[RepoMetricsResult] = []
    seen: set[int] = set()
    for it in sample_order(eligible, seed):
        if len(results) >= max_size:
            break
        name = it["full_name"]
        try:
            r = fetch(gh, name, as_of=built_at)
        except NotFound:
            reason = "not_found"
        except (GraphQLError, GitHubHTTPError) as e:
            progress(f"  {name}: {e}")
            reason = "fetch_error"
        else:
            if r.is_fork:
                reason = "fork"
            elif r.is_archived:
                reason = "archived"
            elif is_awesome_list(r.repo):
                reason = "awesome_list"
            elif r.repo_id in seen:
                reason = "duplicate"  # a rename made two search hits the same repo
            else:
                reason = None
                results.append(r)
                seen.add(r.repo_id)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        progress(
            f"  [{len(results):>3}/{min(max_size, len(eligible))}] {name}"
            + (f" (dropped: {reason})" if reason else f" stars={results[-1].metrics.stars}")
            + f"  requests={gh.stats.network_requests - requests0} cached={gh.stats.cache_hits}"
        )

    results.sort(key=lambda r: r.repo.lower())
    scores = score_universe([r.metrics for r in results])
    rows = [
        UniverseRow(repo=r.repo, repo_id=r.repo_id, url=r.url, metrics=r.metrics, scores=s)
        for r, s in zip(results, scores)
    ]
    search_total = len(found.items)
    counts = UniverseCounts(
        search_total=search_total,
        excluded=dict(sorted(excluded.items())),
        eligible=search_total - sum(excluded.values()),
        sampled=sampled,
        rows=len(rows),
    )
    header = UniverseHeader(
        topic=topic.id,
        topic_label=topic.label,
        queries=found.slices,
        stars_min=topic.stars_min,
        stars_max=topic.stars_max,
        created_after=topic.created_after,
        created_before=topic.created_before,
        built_at=built_at,
        builder=f"sleeper {__version__} @ {git_sha()}",
        seed=seed,
        max_size=max_size,
        github_requests=gh.stats.network_requests - requests0,
        build_seconds=round(time.monotonic() - t0, 1),
        scoring=SCORING,
        counts=counts,
    )
    universe = Universe(header=header, rows=rows)
    progress(
        f"Done: {counts.rows} rows, {header.github_requests} GitHub requests "
        f"({gh.stats.cache_hits} cache hits), {header.build_seconds}s"
    )
    return universe


def write_universe(universe: Universe, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(universe.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path

