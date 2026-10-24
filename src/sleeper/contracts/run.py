"""Run records (C3): the event log, written line by line, and the final result.

Layout: runs/<run_id>/settings.json (RunSettings), events.jsonl (one RunEvent per line),
result.json (RunResult). The UI reads only these files.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, TypeAdapter

from sleeper.contracts.cards import Card, RankingEntry
from sleeper.contracts.common import SCHEMA_VERSION, CallId, Contract, Repo
from sleeper.contracts.settings import RunSettings
from sleeper.contracts.tools import ToolOutput
from sleeper.contracts.universe import UniverseHeader

RunStatus = Literal["ok", "budget_exhausted", "invalid", "timeout", "error"]  # F2.5

Phase = Literal["starting", "researching", "wrapping_up", "submit_only", "reviewing", "fact_checking", "finished"]

Verdict = Literal["survives", "weakened", "killed"]


class Usage(Contract):
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    cached_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cost_usd: float = Field(0.0, ge=0)


# ---- Optional review slots (T12, T13) --------------------------------------


class SkepticReview(Contract):
    """F5.1: written by the skeptic pass, never by the agent."""

    verdict: Verdict
    argument: str = Field(max_length=1200)
    cited_call_ids: list[CallId] = Field(default_factory=list)
    model: str | None = None


class FactCheckFlag(Contract):
    """F6.1: a number on a card that doesn't match the outputs it cites."""

    card_path: str = Field(description="Where on the card, e.g. 'criteria.momentum.key_figure.value' or 'case[0].text'")
    shown: str = Field(description="The number as shown on the card, e.g. '1.2k'")
    source_call_id: CallId | None = None
    source_path: str | None = None
    found: str | None = Field(None, description="The value in the cited output, if one was found")
    message: str


class ResultCard(Card):
    skeptic: SkepticReview | None = None
    fact_flags: list[FactCheckFlag] = Field(default_factory=list)


# ---- Events (events.jsonl) -------------------------------------------------


class _Event(Contract):
    seq: int = Field(ge=0, description="0, 1, 2, ... per run, no gaps")
    ts: AwareDatetime
    run_id: str = Field(min_length=1)


class RunStarted(_Event):
    type: Literal["run_started"] = "run_started"
    settings: RunSettings
    universe: UniverseHeader
    git_sha: str | None = None
    prompt_sha256: str | None = None


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    phase: Phase
    message: str | None = None


class Narration(_Event):
    """F2.2: the one or two sentences before a batch of tool calls. This is the live trace."""

    type: Literal["narration"] = "narration"
    turn: int = Field(ge=1)
    text: str


class Reasoning(_Event):
    """Reasoning summary, if the provider returns one."""

    type: Literal["reasoning"] = "reasoning"
    turn: int = Field(ge=1)
    text: str


class ToolCall(_Event):
    type: Literal["tool_call"] = "tool_call"
    turn: int = Field(ge=1)
    call_id: CallId
    tool: str = Field(description="Tool name as the model sent it (may be unknown)")
    args: dict[str, Any] = Field(description="Raw args as the model sent them (may be invalid)")


class ToolResult(_Event):
    type: Literal["tool_result"] = "tool_result"
    turn: int = Field(ge=1)
    call_id: CallId
    ok: bool = Field(description="False for error outputs, which can't be cited")
    output: ToolOutput = Field(description="Full output, untrimmed")
    approx_tokens: int = Field(ge=0)
    duration_ms: int | None = Field(None, ge=0)
    cache_hit: bool | None = None


class UsageEvent(_Event):
    """F7.4 budget meter. Cumulative for the run so far."""

    type: Literal["usage"] = "usage"
    turn: int = Field(ge=0)
    max_turns: int = Field(gt=0)
    usage: Usage
    token_budget: int = Field(gt=0)
    tokens_remaining: int


class SkepticEvent(_Event):
    type: Literal["skeptic_review"] = "skeptic_review"
    repo: Repo
    review: SkepticReview


class FactCheckEvent(_Event):
    type: Literal["fact_check"] = "fact_check"
    repo: Repo
    flags: list[FactCheckFlag]


class RunFinished(_Event):
    type: Literal["run_finished"] = "run_finished"
    status: RunStatus
    error: str | None = None


RunEvent = Annotated[
    RunStarted
    | StatusEvent
    | Narration
    | Reasoning
    | ToolCall
    | ToolResult
    | UsageEvent
    | SkepticEvent
    | FactCheckEvent
    | RunFinished,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)


def parse_event(line: str | bytes) -> RunEvent:
    """Parse one line of events.jsonl."""
    return EVENT_ADAPTER.validate_json(line)


def dump_event(event: RunEvent) -> str:
    """One events.jsonl line (no trailing newline)."""
    return event.model_dump_json()


# ---- Result (result.json) --------------------------------------------------


class RunResult(Contract):
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    status: RunStatus
    error: str | None = Field(None, description="Set when status is 'error' (or explains timeout/invalid)")
    settings: RunSettings
    started_at: AwareDatetime
    finished_at: AwareDatetime
    turns: int = Field(ge=0)
    usage: Usage = Field(description="Agent loop plus review calls (T12 counts toward spend)")
    cards: list[ResultCard] = Field(default_factory=list, description="Empty unless a submission was accepted")
    ranking: list[RankingEntry] = Field(default_factory=list)
    summary: str | None = None
    ranking_below_min: bool = False
    submit_attempts: int = Field(0, ge=0)
    last_submit_errors: list[str] = Field(default_factory=list, description="For 'invalid' runs: why the last try failed")
