"""Shared data contracts (T02, C5). Reference: docs/contracts.md.

Import from here: `from sleeper.contracts import Universe, Card, RunEvent, ...`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter

from sleeper.contracts.cards import (
    Card,
    CardCriteria,
    Claim,
    CriterionRating,
    KeyFigure,
    RankingEntry,
    Risk,
    Submission,
)
from sleeper.contracts.common import CRITERIA, SCHEMA_VERSION, CallId, Contract, Criterion, JsonPointer, Repo, Score
from sleeper.contracts.run import (
    EVENT_ADAPTER,
    FactCheckEvent,
    FactCheckFlag,
    Narration,
    Phase,
    Reasoning,
    ResultCard,
    RunEvent,
    RunFinished,
    RunResult,
    RunStarted,
    RunStatus,
    SkepticEvent,
    SkepticReview,
    StatusEvent,
    ToolCall,
    ToolResult,
    Usage,
    UsageEvent,
    Verdict,
    dump_event,
    parse_event,
)
from sleeper.contracts.settings import RunSettings
from sleeper.contracts.tools import (
    TOOL_ARGS,
    BrowseArgs,
    BrowseOutput,
    BrowseRow,
    ErrorCode,
    ErrorOutput,
    Filter,
    LinkedPaper,
    PapersArgs,
    PapersOutput,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    ScreenField,
    SubmitArgs,
    SubmitOutput,
    ToolError,
    ToolName,
    ToolOutput,
)
from sleeper.contracts.universe import CriteriaScores, RepoMetrics, Universe, UniverseCounts, UniverseHeader, UniverseRow

TOOL_OUTPUT_ADAPTER: TypeAdapter[ToolOutput] = TypeAdapter(ToolOutput)


def json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema for each top-level contract (used by `sleeper schema`)."""
    models: dict[str, type[BaseModel]] = {
        "run_settings": RunSettings,
        "universe": Universe,
        "submission": Submission,
        "run_result": RunResult,
        **{f"tool_args.{name}": model for name, model in TOOL_ARGS.items()},
    }
    out = {name: m.model_json_schema() for name, m in models.items()}
    out["tool_output"] = TOOL_OUTPUT_ADAPTER.json_schema()
    out["run_event"] = EVENT_ADAPTER.json_schema()
    return out

