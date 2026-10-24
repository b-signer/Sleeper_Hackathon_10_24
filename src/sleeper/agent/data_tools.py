"""Data tools (T09, W1): browse, repo_details and readme over any Universe.

browse and repo_details delegate to ``sleeper.fakes`` (they already work on any
universe). readme reads from a ``ReadmeSource``: T03's fake READMEs for the fake
universe, GitHub (through the T05 cached client) for real universes.

DataTools is thread-safe and never raises for model mistakes: those come back
as ErrorOutput (NOT_IN_UNIVERSE, NOT_FOUND, UPSTREAM).
"""

from __future__ import annotations

import threading
from typing import Callable

from sleeper import fakes
from sleeper.contracts.tools import (
    BrowseArgs,
    BrowseOutput,
    ErrorOutput,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    ToolError,
)
from sleeper.contracts.universe import Universe, UniverseRow
from sleeper.github import RAW_ACCEPT, GitHubClient, GitHubError, MissingTokenError, NotFound, OfflineCacheMiss, RateLimitExceeded

# Request shape as one constant pair, so it can follow T06's (plan §7.2).
README_PATH = "/repos/{repo}/readme"
README_ACCEPT = RAW_ACCEPT

FAKE_TOPIC = "fake"

ReadmeSource = Callable[[UniverseRow], "str | None"]
"""Full README text ("" for an empty README), or None if the repo has no README (404).
Raises ReadmeUnavailable if it can't be fetched."""


class ReadmeUnavailable(Exception):
    """README could not be fetched: offline cache miss, GitHub error, rate limit, no token."""


def fake_readme_source() -> ReadmeSource:
    return fakes.fake_readme


def _reason(e: GitHubError) -> str:
    if isinstance(e, OfflineCacheMiss):
        return "README not in the offline cache"
    if isinstance(e, MissingTokenError):
        return "no GitHub token configured"
    if isinstance(e, RateLimitExceeded):
        return "GitHub rate limit; try again later"
    status = getattr(e, "status", None)
    return f"GitHub returned {status}" if status else "GitHub request failed"


def github_readme_source(client: GitHubClient | None = None) -> ReadmeSource:
    """README via GitHub's raw readme endpoint, through the cached client.

    The default client is bounded (max_wait=30, max_attempts=2, timeout=10) and
    created lazily on the first call. Calls are serialized under a lock.
    """
    lock = threading.Lock()
    holder: list[GitHubClient] = [client] if client is not None else []

    def source(row: UniverseRow) -> str | None:
        with lock:
            if not holder:
                holder.append(GitHubClient(max_wait=30, max_attempts=2, timeout=10))
            try:
                data = holder[0].get(README_PATH.format(repo=row.repo), accept=README_ACCEPT)
            except NotFound:
                return None
            except GitHubError as e:
                raise ReadmeUnavailable(_reason(e)) from e
        if data is None:
            return ""
        return data if isinstance(data, str) else str(data)

    source.is_github = True  # type: ignore[attr-defined]  # lets tests tell sources apart
    return source


def default_readme_source(universe: Universe) -> ReadmeSource:
    if universe.header.topic == FAKE_TOPIC:
        return fake_readme_source()
    return github_readme_source()


def _error(call_id: str, code: str, message: str) -> ErrorOutput:
    return ErrorOutput(call_id=call_id, tool="readme", error=ToolError(code=code, message=message))


class DataTools:
    def __init__(self, universe: Universe, *, readme_source: ReadmeSource | None = None) -> None:
        self.universe = universe
        self._readme_source = readme_source
        self._source_lock = threading.Lock()

    def _source(self) -> ReadmeSource:
        if self._readme_source is None:
            with self._source_lock:
                if self._readme_source is None:
                    self._readme_source = default_readme_source(self.universe)
        return self._readme_source

    def browse(self, args: BrowseArgs, call_id: str) -> BrowseOutput:
        return fakes.browse_output(self.universe, args, call_id)

    def repo_details(self, args: RepoArgs, call_id: str) -> RepoDetailsOutput | ErrorOutput:
        return fakes.repo_details_output(self.universe, args.repo, call_id)

    def readme(self, args: ReadmeArgs, call_id: str) -> ReadmeOutput | ErrorOutput:
        row = self.universe.get(args.repo)
        if row is None:
            return _error(call_id, "NOT_IN_UNIVERSE", f"{args.repo} is not in the universe")
        if row.metrics.readme_chars == 0:
            return _error(call_id, "NOT_FOUND", f"{row.repo} has no README")
        try:
            full = self._source()(row)
        except ReadmeUnavailable as e:
            return _error(call_id, "UPSTREAM", f"could not fetch the README of {row.repo}: {e}")
        except Exception as e:  # never raise for a tool call
            return _error(call_id, "UPSTREAM", f"could not fetch the README of {row.repo}: {type(e).__name__}")
        if full is None:
            return _error(call_id, "NOT_FOUND", f"{row.repo} has no README")
        return ReadmeOutput(
            call_id=call_id,
            repo=row.repo,
            text=full[: args.max_chars],
            truncated=len(full) > args.max_chars,
            total_chars=len(full),
        )
