"""Universe file (F1): a header saying how it was built, and one row per repo."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from sleeper.contracts.common import SCHEMA_VERSION, Contract, Repo, Score


class RepoMetrics(Contract):
    """T06 metrics. None means unknown, never guessed."""

    # Popularity
    stars: int = Field(ge=0)
    # Momentum
    stars_gained_30d: int | None = Field(None, ge=0)
    # Quality
    commits_90d: int | None = Field(None, ge=0)
    authors_90d: int | None = Field(None, ge=0)
    releases: int | None = Field(None, ge=0, description="Number of releases")
    last_release_at: AwareDatetime | None = None
    last_commit_at: AwareDatetime | None = None
    readme_chars: int | None = Field(None, ge=0, description="README length in characters; 0 = no README")
    # Newness
    created_at: AwareDatetime
    age_days: int = Field(ge=0)
    # Display
    description: str | None = None
    language: str | None = None
    license: str | None = Field(None, description="SPDX id, e.g. 'MIT'")


class CriteriaScores(Contract):
    """The four criteria (PRD §4), computed by code from the metrics (D8). Higher is better."""

    popularity: Score
    momentum: Score
    quality: Score
    newness: Score


class UniverseRow(Contract):
    repo: Repo
    repo_id: int = Field(description="GitHub numeric id (stable across renames)")
    url: str
    metrics: RepoMetrics
    scores: CriteriaScores


class UniverseCounts(Contract):
    search_total: int = Field(ge=0, description="Repos the GitHub search(es) matched")
    excluded: dict[str, int] = Field(
        default_factory=dict, description="Dropped repos by reason, e.g. {'fork': 3, 'archived': 2, 'awesome_list': 1}"
    )
    eligible: int = Field(ge=0, description="Left after exclusions")
    sampled: bool = Field(description="True if eligible > max size and a seeded random sample was taken")
    rows: int = Field(ge=0, description="Rows in this file")


class UniverseHeader(Contract):
    schema_version: Literal[1] = SCHEMA_VERSION
    topic: str = Field(min_length=1)
    topic_label: str
    queries: list[str] = Field(min_length=1, description="GitHub search queries used to find the repos")
    stars_min: int = Field(ge=0)
    stars_max: int = Field(ge=0)
    created_after: date
    created_before: date | None = None
    built_at: AwareDatetime = Field(description="When the build ran; metrics are as of this time")
    builder: str = Field(description="How it was built, e.g. 'sleeper 0.1.0 @ <git sha>'")
    seed: int
    max_size: int = Field(200, gt=0)
    github_requests: int | None = Field(None, ge=0)
    build_seconds: float | None = Field(None, ge=0)
    scoring: str = Field(description="One short paragraph: how each criterion score is computed")
    counts: UniverseCounts


class Universe(Contract):
    header: UniverseHeader
    rows: list[UniverseRow]

    @model_validator(mode="after")
    def _check(self) -> Universe:
        repos = [r.repo.lower() for r in self.rows]
        if len(repos) != len(set(repos)):
            raise ValueError("duplicate repo in universe rows")
        if self.header.counts.rows != len(self.rows):
            raise ValueError(f"header.counts.rows={self.header.counts.rows} but file has {len(self.rows)} rows")
        return self

    def get(self, repo: str) -> UniverseRow | None:
        """Case-insensitive lookup by 'owner/name'."""
        key = repo.lower()
        return next((r for r in self.rows if r.repo.lower() == key), None)
