"""Custom topics in the UI: draft queries from the user's words, count matches, build a universe in the background.

The work itself is in `sleeper.custom_topic`; this module adds what the server needs: refusals before any
spend or GitHub request (offline, no keys, spend guard), one build at a time, and a job the page polls
for progress. TopicService takes its steps as functions, so tests inject fakes, like `Runner` for runs.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from sleeper.config import Settings
from sleeper.contracts import Universe
from sleeper.custom_topic import (
    CUSTOM_MAX_SIZE,
    TOO_BROAD,
    TOO_FEW,
    QueryCount,
    TopicDraft,
    estimate_requests,
    make_topic,
    write_custom_universe,
)
from sleeper.server.runner import RunRefused
from sleeper.server.store import UniverseInfo
from sleeper.universe import Progress, Topic

MAX_LINES = 200
_FETCHED = re.compile(r"fetched metrics (\d+)/(\d+)")


class TopicPlan(BaseModel):
    """A draft with its match counts: what the launcher shows before the user builds."""

    label: str
    topic: str  # the id the universe will get, e.g. custom-python-static-site-generators
    queries: list[QueryCount]
    total: int  # sum over queries; repos found by two queries count twice
    max_size: int = CUSTOM_MAX_SIZE
    est_requests: int
    warning: str | None = None
    cost_usd: float | None = None  # the drafting model call, if there was one


class BuildJob(BaseModel):
    id: str
    topic: str
    label: str
    state: Literal["running", "done", "error"]
    started_at: AwareDatetime
    fetched: int = 0
    to_fetch: int = 0
    lines: list[str] = []
    universe: UniverseInfo | None = None
    error: str | None = None


class TopicService:
    def __init__(
        self,
        *,
        out_dir: Path,
        draft: Callable[[str], tuple[TopicDraft, float | None]],
        count: Callable[[Topic], list[QueryCount]],
        build: Callable[[Topic, Progress], Universe],
        refuse_draft: Callable[[], RunRefused | None] = lambda: None,
        refuse_github: Callable[[], RunRefused | None] = lambda: None,
    ) -> None:
        self.out_dir = out_dir
        self._draft = draft
        self._count = count
        self._build = build
        self._refuse_draft = refuse_draft
        self._refuse_github = refuse_github
        self._jobs: dict[str, BuildJob] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _raise(refusal: RunRefused | None) -> None:
        if refusal is not None:
            raise refusal

    def draft(self, text: str) -> TopicPlan:
        """The model's label and queries for `text`, with their match counts. Raises RunRefused or ValueError."""
        self._raise(self._refuse_draft())
        self._raise(self._refuse_github())
        draft, cost = self._draft(text)
        return self._plan(draft, cost)

    def count(self, draft: TopicDraft) -> TopicPlan:
        """Counts for queries the user edited. Raises RunRefused or ValueError."""
        self._raise(self._refuse_github())
        return self._plan(draft, None)

    def _plan(self, draft: TopicDraft, cost: float | None) -> TopicPlan:
        topic = make_topic(draft)
        counts = self._count(topic)
        total = sum(c.total for c in counts)
        warning = None
        if total == 0:
            warning = "No repos match. Try broader searches, e.g. a topic: tag without language:."
        elif total < TOO_FEW:
            warning = f"Only {total} repos match, too few to compare. Broaden the searches if you can."
        elif total > TOO_BROAD:
            warning = f"{total} matches: the search alone takes many requests. Narrow it if you can."
        return TopicPlan(
            label=topic.label,
            topic=topic.id,
            queries=counts,
            total=total,
            est_requests=estimate_requests(counts),
            warning=warning,
            cost_usd=cost,
        )

    def start_build(self, draft: TopicDraft) -> BuildJob:
        """Start a build in the background. Raises RunRefused, ValueError, or RuntimeError if one is running."""
        self._raise(self._refuse_github())
        topic = make_topic(draft)
        with self._lock:
            if any(j.state == "running" for j in self._jobs.values()):
                raise RuntimeError("a topic is already being built; wait for it to finish")
            job = BuildJob(
                id=uuid.uuid4().hex[:12], topic=topic.id, label=topic.label, state="running", started_at=datetime.now(UTC)
            )
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, topic), daemon=True, name=f"build-{topic.id}").start()
        return job.model_copy(deep=True)

    def job(self, job_id: str) -> BuildJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def _progress(self, job: BuildJob, msg: str) -> None:
        with self._lock:
            if m := _FETCHED.search(msg):
                job.fetched, job.to_fetch = int(m.group(1)), int(m.group(2))
                return
            if msg.lstrip().startswith("[") and "requests=" in msg:
                return  # the builder's per-repo line: all cache hits after the prefetch, so just noise
            job.lines = [*job.lines, msg.strip()][-MAX_LINES:]

    def _run(self, job: BuildJob, topic: Topic) -> None:
        try:
            universe = self._build(topic, lambda msg: self._progress(job, msg))
            path = write_custom_universe(universe, self.out_dir)
        except Exception as e:  # shown to the user in the launcher
            with self._lock:
                job.state, job.error = "error", str(e) or type(e).__name__
            return
        h = universe.header
        with self._lock:
            job.universe = UniverseInfo(
                file=path.as_posix(), topic=h.topic, topic_label=h.topic_label, rows=h.counts.rows, built_at=h.built_at
            )
            job.state = "done"


def topic_service_from_settings(cfg: Settings) -> TopicService:
    """The real service: cfg.dev_model drafts, GitHub (through the shared cache) counts and builds."""
    from sleeper.agent.openrouter import OpenRouterModel
    from sleeper.agent.spend import SPEND_GUARD_USD, current_spend
    from sleeper.custom_topic import build_custom_universe, count_matches, draft_topic
    from sleeper.github import GitHubClient

    def client() -> GitHubClient:
        return GitHubClient(cfg.github_token, offline=cfg.offline, cache_dir=cfg.cache_dir / "github", max_wait=120)

    def refuse_draft() -> RunRefused | None:
        if cfg.offline:
            return RunRefused("Offline mode (SLEEPER_OFFLINE=1): new topics need the network.", "offline")
        if not cfg.openrouter_api_key:
            return RunRefused("OPENROUTER_API_KEY is not set, so the model can't suggest searches.", "no_api_key")
        if current_spend(cfg) >= SPEND_GUARD_USD:
            return RunRefused(f"spend guard: ${SPEND_GUARD_USD:.0f} already spent (D10)", "spend_guard")
        return None

    def refuse_github() -> RunRefused | None:
        if cfg.offline:
            return RunRefused("Offline mode (SLEEPER_OFFLINE=1): new topics need the network.", "offline")
        if not cfg.github_token:
            return RunRefused("GITHUB_TOKEN is not set, so GitHub can't be searched.", "other")
        return None

    def draft(text: str) -> tuple[TopicDraft, float | None]:
        model = OpenRouterModel(cfg.dev_model, cfg.openrouter_api_key)
        try:
            d, usage = draft_topic(model, text)
        finally:
            model.close()
        return d, usage.cost_usd

    def count(topic: Topic) -> list[QueryCount]:
        gh = client()
        try:
            return count_matches(gh, topic)
        finally:
            gh.close()

    return TopicService(
        out_dir=cfg.cache_dir / "universes",
        draft=draft,
        count=count,
        build=lambda topic, progress: build_custom_universe(client, topic, progress=progress),
        refuse_draft=refuse_draft,
        refuse_github=refuse_github,
    )
