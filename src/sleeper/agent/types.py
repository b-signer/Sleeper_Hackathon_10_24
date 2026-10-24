"""Interfaces between the agent loop (T10), the model client and the tools (T09).

Frozen for T10 (docs/tickets/T10-plan.md). Change only by agreement with the coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sleeper.contracts import RunEvent, ToolOutput, Usage


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool call as the model sent it."""

    id: str  # provider's tool_call id, echoed back in the tool message
    name: str  # tool name as sent (may be unknown)
    arguments: str  # raw JSON string as sent (may be invalid JSON)


@dataclass(frozen=True)
class ModelReply:
    """One assistant turn."""

    content: str | None  # assistant text: the narration (F2.2)
    tool_calls: list[ToolCallRequest]
    usage: Usage  # this call only; cost_usd as reported by the provider
    reasoning: str | None = None  # reasoning summary, if the provider returned one
    finish_reason: str | None = None
    raw_message: dict[str, Any] = field(default_factory=dict)  # assistant message to append to history as is; {} = loop builds it


ModelErrorKind = Literal["timeout", "http", "auth", "bad_response"]


class ModelError(Exception):
    """A model call failed after the client's own retries. kind="timeout" covers a stalled model."""

    def __init__(self, kind: ModelErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class ChatModel(Protocol):
    model_id: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | dict[str, Any] = "auto",
        timeout_s: float = 120.0,
    ) -> ModelReply:
        """One chat completion in OpenAI message format. Raises ModelError."""
        ...


class ToolExecutor(Protocol):
    def tool_specs(self) -> list[dict[str, Any]]:
        """OpenAI-style `tools` array, one entry per tool in TOOL_ARGS."""
        ...

    def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolOutput:
        """Run one known tool. Model mistakes (bad args, repo not in universe, rejected submit) come back
        as ErrorOutput / SubmitOutput(ok=False), never as exceptions. Tracks submit attempts itself
        (SubmitOutput.attempts_left). Must be thread-safe: calls from one turn run concurrently."""
        ...


class EventSink(Protocol):
    run_id: str

    def emit(self, event_type: type[Any], /, **payload: Any) -> RunEvent:
        """Build the event (adds seq, ts=now UTC, run_id), record it immediately, return it. Thread-safe."""
        ...
