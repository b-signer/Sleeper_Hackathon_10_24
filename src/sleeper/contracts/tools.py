"""Agent tools (F3): arguments the model sends, outputs the harness returns.

Every output starts with `call_id` (F3.5), which cards cite. Error outputs can't be cited.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from sleeper.contracts.cards import Submission
from sleeper.contracts.common import CallId, Contract, Repo
from sleeper.contracts.universe import CriteriaScores, RepoMetrics

ToolName = Literal["browse", "repo_details", "readme", "submit"]

# Fields browse can filter and sort on: the numeric metrics and the four criteria scores.
ScreenField = Literal[
    "stars",
    "stars_per_day",
    "forks_30d",
    "star_events_30d",
    "commits_90d",
    "authors_90d",
    "releases",
    "readme_chars",
    "age_days",
    "popularity",
    "momentum",
    "quality",
    "newness",
]


# ---- Errors ---------------------------------------------------------------

ErrorCode = Literal[
    "BAD_ARGS",  # args failed validation
    "NOT_IN_UNIVERSE",  # deep tool called on a repo outside the universe
    "NOT_FOUND",  # e.g. repo has no README
    "UPSTREAM",  # GitHub or cache failure
    "BUDGET_EXHAUSTED",  # only submit is allowed now
]


class ToolError(Contract):
    code: ErrorCode
    message: str


class ErrorOutput(Contract):
    call_id: CallId
    tool: ToolName
    error: ToolError


# ---- browse (F3.1) ---------------------------------------------------------


class Filter(Contract):
    field: ScreenField
    op: Literal[">", ">=", "<", "<=", "==", "between"]
    value: float | tuple[float, float]

    @model_validator(mode="after")
    def _check(self) -> Filter:
        if (self.op == "between") != isinstance(self.value, tuple):
            raise ValueError("'between' takes [low, high]; other ops take one number")
        return self


class BrowseArgs(Contract):
    filters: list[Filter] = Field(default_factory=list, description="Combined with AND")
    keyword: str | None = Field(None, description="Case-insensitive match on repo name or description")
    language: str | None = None
    sort_by: ScreenField | Literal["random"] = "stars"
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = Field(25, ge=1, le=50)
    offset: int = Field(0, ge=0)


class BrowseRow(Contract):
    repo: Repo
    description: str | None = Field(None, description="Truncated to ~120 characters")
    language: str | None = None
    stars: int
    scores: CriteriaScores
    extra: dict[str, float | None] = Field(
        default_factory=dict, description="Values of other fields used in filters or sort_by"
    )


class BrowseOutput(Contract):
    call_id: CallId
    tool: Literal["browse"] = "browse"
    total: int = Field(ge=0, description="Rows matching the filters, before limit/offset")
    offset: int = Field(ge=0)
    rows: list[BrowseRow] = Field(max_length=50)


# ---- repo details (F3.2) ---------------------------------------------------


class RepoArgs(Contract):
    repo: Repo


class RepoDetailsOutput(Contract):
    call_id: CallId
    tool: Literal["repo_details"] = "repo_details"
    repo: Repo
    url: str
    metrics: RepoMetrics
    scores: CriteriaScores


# ---- README (F3.3) ---------------------------------------------------------


class ReadmeArgs(Contract):
    repo: Repo
    max_chars: int = Field(6000, ge=500, le=12000)


class ReadmeOutput(Contract):
    call_id: CallId
    tool: Literal["readme"] = "readme"
    repo: Repo
    text: str
    truncated: bool
    total_chars: int = Field(ge=0, description="Full README length before the cap")


# ---- submit (F3.4) ---------------------------------------------------------

SubmitArgs = Submission


class SubmitOutput(Contract):
    call_id: CallId
    tool: Literal["submit"] = "submit"
    ok: bool
    errors: list[str] = Field(default_factory=list, description="Specific, fixable messages when ok is false")
    attempts_left: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> SubmitOutput:
        if self.ok == bool(self.errors):
            raise ValueError("ok=true needs no errors; ok=false needs at least one")
        return self


ToolOutput = BrowseOutput | RepoDetailsOutput | ReadmeOutput | SubmitOutput | ErrorOutput

# Argument model per tool, e.g. to generate the OpenAI-style tool schemas (T09).
TOOL_ARGS: dict[str, type[Contract]] = {
    "browse": BrowseArgs,
    "repo_details": RepoArgs,
    "readme": ReadmeArgs,
    "submit": SubmitArgs,
}
