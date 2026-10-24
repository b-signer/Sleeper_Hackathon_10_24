"""Custom topics: a topic the user types, turned into a small universe while they wait.

1. ``draft_topic``: a cheap model turns the user's words into a short label and 2-4 GitHub
   repository-search queries, the same kind topics.toml holds.
2. ``count_matches``: one search request per query (``per_page=1``) reports how many repos match,
   so the user sees a query that is too broad or empty before building.
3. ``build_custom_universe``: the normal T07 builder with a smaller sample (CUSTOM_MAX_SIZE). The
   metrics of the repos it will sample are fetched first on a few threads, each with its own
   GitHub client, so they land in the shared response cache; ``build_universe`` then reads them
   from there. Same search, exclusions, seeded sample order, scoring and Universe contract.

Custom universes are written to ``<SLEEPER_CACHE_DIR>/universes`` (git-ignored), not data/universes.
The star range and created window are the product defaults (D7), as for every topic.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sleeper.agent.types import ChatModel
from sleeper.contracts import Universe, Usage
from sleeper.github import GitHubClient
from sleeper.metrics import fetch_repo_metrics
from sleeper.universe import (
    DEFAULT_SEED,
    Progress,
    Topic,
    build_universe,
    exclusion_reason,
    sample_order,
    search_qualifiers,
    search_topic,
)

CUSTOM_PREFIX = "custom-"
CUSTOM_MAX_SIZE = 40  # rows; about 6 GitHub requests each
PREFETCH_WORKERS = 6
PREFETCH_SLACK = 5  # extra candidates, for repos the builder drops after fetching (forks, archived)
MAX_QUERIES = 4
MAX_QUERY_CHARS = 150
MAX_LABEL_CHARS = 60
MAX_TEXT_CHARS = 300
TOO_BROAD = 3000  # matches across all queries; above this the search alone is dozens of requests
TOO_FEW = 15  # below this a run has little to compare; the launcher suggests broadening
REQUESTS_PER_REPO = 6

# Qualifiers the builder adds itself; a query that sets them would fight the product's window (D7).
_RESERVED = re.compile(r"\b(stars|created|fork|pushed|sort|order):", re.IGNORECASE)


class TopicDraft(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES)


class QueryCount(BaseModel):
    query: str
    total: int


def custom_topic_id(label: str) -> str:
    """'Python static site generators' -> 'custom-python-static-site-generators'."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:48].strip("-")
    return CUSTOM_PREFIX + (slug or "topic")


def _terms(query: str) -> tuple[str, ...]:
    return tuple(sorted(query.lower().split()))


def clean_queries(queries: list[str]) -> list[str]:
    """Trimmed, deduped queries. Raises ValueError with a message the user can act on."""
    out: list[str] = []
    for q in queries:
        q = " ".join(str(q).split())
        if not q:
            continue
        if len(q) > MAX_QUERY_CHARS:
            raise ValueError(f"query too long (max {MAX_QUERY_CHARS} characters): {q[:40]}...")
        if m := _RESERVED.search(q):
            raise ValueError(f"leave out {m.group(1)}: in {q!r}; the star range and dates are added for you")
        if _terms(q) not in {_terms(x) for x in out}:  # the same terms in another order are the same search
            out.append(q)
    if not out:
        raise ValueError("add at least one search query")
    if len(out) > MAX_QUERIES:
        raise ValueError(f"at most {MAX_QUERIES} queries")
    return out


def make_topic(draft: TopicDraft) -> Topic:
    label = " ".join(draft.label.split())[:MAX_LABEL_CHARS]
    return Topic(id=custom_topic_id(label), label=label, queries=tuple(clean_queries(draft.queries)))


# ---- 1. draft ---------------------------------------------------------------------------

DRAFT_SYSTEM = """You turn a topic someone wants to research into GitHub repository searches.

Sleeper looks for underrated open-source repos in a topic. Given the user's words, reply with:
- label: a short name for the topic, 2-5 words, Title case not required (e.g. "Rust developer tools").
- queries: 2-4 GitHub repository-search queries that together find repos in this topic and few others.

Query syntax that works well (combine terms with spaces, which means AND):
- topic:<github-topic-tag>, e.g. topic:static-site-generator (tags are lowercase with hyphens)
- "<phrase>" in:name,description, e.g. "static site" in:name,description
- language:<Language> when the topic is about one language, e.g. language:Python
Prefer one or two topic: queries for the obvious GitHub tags plus one in:name,description phrase query.
Each query must match the topic on its own: the queries are combined with OR, so one broad query (a lone
generic tag like topic:terminal or topic:python) floods the results with unrelated repos. When a tag is
broader than the topic, narrow it with a second term, e.g. topic:terminal topic:file-manager.
Never add stars:, created:, fork:, pushed:, sort: or order: qualifiers; they are added automatically.
If the request is not a software topic, still give your best label and queries for it."""

DRAFT_TOOL = {
    "type": "function",
    "function": {
        "name": "topic",
        "description": "The topic's label and its GitHub search queries.",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_QUERIES},
            },
            "required": ["label", "queries"],
        },
    },
}
FORCE_DRAFT = {"type": "function", "function": {"name": "topic"}}


def draft_topic(model: ChatModel, text: str, *, timeout_s: float = 60.0) -> tuple[TopicDraft, Usage]:
    """One model call. Raises ValueError if the text is empty or the reply is unusable, ModelError on API failure."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("describe the topic first")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"keep the topic under {MAX_TEXT_CHARS} characters")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": DRAFT_SYSTEM},
        {"role": "user", "content": f"Topic: {text}"},
    ]
    reply = model.complete(messages, [DRAFT_TOOL], tool_choice=FORCE_DRAFT, timeout_s=timeout_s)
    raw = next((c.arguments for c in reply.tool_calls if c.name == "topic"), None) or reply.content or ""
    try:
        args = json.loads(raw)
        queries = [q for q in args["queries"] if not _RESERVED.search(str(q))]
        draft = TopicDraft(label=str(args["label"]).strip()[:MAX_LABEL_CHARS], queries=clean_queries(queries)[:MAX_QUERIES])
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"the model's suggestion was unusable ({e}); try rewording the topic") from e
    return draft, reply.usage


# ---- 2. count -----------------------------------------------------------------------------


def count_matches(gh: GitHubClient, topic: Topic) -> list[QueryCount]:
    """Repos each query matches inside the product's star range and created window (one request each)."""
    quals = search_qualifiers(topic, topic.stars_min, topic.stars_max)
    out = []
    for q in topic.queries:
        data = gh.get("/search/repositories", params={"q": f"{q} {quals}", "per_page": 1})
        out.append(QueryCount(query=q, total=int(data.get("total_count", 0))))
    return out


def estimate_requests(counts: list[QueryCount], max_size: int = CUSTOM_MAX_SIZE) -> int:
    """Rough GitHub requests for a build: search pages, plus about REQUESTS_PER_REPO per sampled repo."""
    search = sum(-(-min(c.total, 1000) // 100) or 1 for c in counts)
    return search + REQUESTS_PER_REPO * min(max_size, sum(c.total for c in counts))


# ---- 3. build -----------------------------------------------------------------------------


def _prefetch(
    client_factory: Callable[[], GitHubClient],
    repos: list[str],
    built_at: datetime,
    workers: int,
    progress: Progress,
) -> None:
    """Fill the response cache with each repo's metrics as of built_at. Failures are left to the builder."""
    def one(repo: str) -> str:
        gh = client_factory()
        try:
            fetch_repo_metrics(gh, repo, as_of=built_at)
            return repo
        finally:
            gh.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, r) for r in repos]
        for done, f in enumerate(as_completed(futures), 1):
            try:
                f.result()
            except Exception as e:  # the builder fetches it again and records why it was dropped
                progress(f"  prefetch failed: {type(e).__name__}: {e}")
            progress(f"  fetched metrics {done}/{len(repos)}")


def build_custom_universe(
    client_factory: Callable[[], GitHubClient],
    topic: Topic,
    *,
    max_size: int = CUSTOM_MAX_SIZE,
    workers: int = PREFETCH_WORKERS,
    progress: Progress = lambda msg: None,
    built_at: datetime | None = None,
) -> Universe:
    built_at = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    t0 = time.monotonic()
    clients: list[GitHubClient] = []
    lock = threading.Lock()

    def counted() -> GitHubClient:  # every client, so the header counts all requests, not just the builder's
        c = client_factory()
        with lock:
            clients.append(c)
        return c

    gh = counted()
    try:
        found = search_topic(gh, topic, progress=progress)
        eligible = [it for it in found.items.values() if exclusion_reason(it) is None]
        if not eligible:
            raise ValueError("no repos matched these searches in the 50-2000 star range created since 2025; broaden them")
        order = [it["full_name"] for it in sample_order(eligible, DEFAULT_SEED)[: max_size + PREFETCH_SLACK]]
        progress(f"{len(eligible)} eligible repos; fetching metrics for {len(order)} on {workers} threads")
        _prefetch(counted, order, built_at, workers, progress)
        # Same topic, seed and built_at: the search and metrics now come from the cache.
        universe = build_universe(gh, topic, seed=DEFAULT_SEED, max_size=max_size, built_at=built_at, progress=progress)
        header = universe.header.model_copy(
            update={
                "github_requests": sum(c.stats.network_requests for c in clients),
                "build_seconds": round(time.monotonic() - t0, 1),
            }
        )
        return universe.model_copy(update={"header": header})
    finally:
        gh.close()


def write_custom_universe(universe: Universe, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{universe.header.topic}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(universe.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
