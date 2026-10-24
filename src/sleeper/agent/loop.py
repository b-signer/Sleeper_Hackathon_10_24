"""The agent loop (T10): settings -> model turns with tools -> a RunResult, every step emitted as an event.

One turn: check the wall clock, call the model with the tools for the current budget phase, count usage,
emit narration/reasoning, emit and run the tool calls (non-submit ones concurrently, then submit ones in
order), emit usage, update the phase, and append the budget line for the next turn. Model and tool
failures never raise: they end the run with status "error" or "timeout". No files are written here;
the sink records events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sleeper.agent.budget import Budget
from sleeper.agent.prompt import system_prompt as default_system_prompt
from sleeper.agent.types import ChatModel, EventSink, ModelError, ModelReply, ToolCallRequest, ToolExecutor
from sleeper.contracts import (
    TOOL_ARGS,
    ErrorOutput,
    FactCheckEvent,
    FactCheckFlag,
    Narration,
    Reasoning,
    ResultCard,
    RunFinished,
    RunResult,
    RunSettings,
    RunStarted,
    RunStatus,
    StatusEvent,
    Submission,
    SubmitOutput,
    ToolCall,
    ToolError,
    ToolOutput,
    ToolResult,
    Universe,
    UsageEvent,
)
from sleeper.factcheck import check_card

log = logging.getLogger(__name__)

MODEL_TIMEOUT_S = 120.0
MAX_EMPTY_TURNS = 3  # consecutive replies without tool calls before the run counts as stalled
NUDGE = "You did not call a tool. Call a tool; to finish, call submit."
KICKOFF = "Begin. Research the universe with the tools, then submit your picks."
FORCE_SUBMIT = {"type": "function", "function": {"name": "submit"}}


class _WallClockExceeded(Exception):
    """Tool calls were still running when the wall-clock limit passed."""


@dataclass
class _Call:
    req: ToolCallRequest
    call_id: str
    args: dict[str, Any] | None  # None: arguments were not a JSON object
    output: ToolOutput | None = None
    text: str | None = None  # tool message content when there is no output (unknown tool)


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        args = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
    return args if isinstance(args, dict) else None


def _assistant_message(reply: ModelReply) -> dict[str, Any]:
    if reply.raw_message:
        return reply.raw_message
    msg: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls
        ]
    return msg


def run_agent(
    settings: RunSettings,
    universe: Universe,
    model: ChatModel,
    tools: ToolExecutor,
    sink: EventSink,
    *,
    system_prompt: str | None = None,
    git_sha: str | None = None,
    spend_headroom_usd: float | None = None,
    now: Callable[[], float] = time.monotonic,
) -> RunResult:
    """Run the agent to a status. Emits run_started ... run_finished through `sink`; never raises for
    model or tool failures."""
    return _Run(settings, universe, model, tools, sink, system_prompt, git_sha, spend_headroom_usd, now).run()


class _Run:
    def __init__(
        self,
        settings: RunSettings,
        universe: Universe,
        model: ChatModel,
        tools: ToolExecutor,
        sink: EventSink,
        system_prompt: str | None,
        git_sha: str | None,
        spend_headroom_usd: float | None,
        now: Callable[[], float],
    ) -> None:
        self.settings = settings
        self.universe = universe
        self.model = model
        self.tools = tools
        self.sink = sink
        self.prompt = system_prompt if system_prompt is not None else default_system_prompt(settings, universe.header)
        self.git_sha = git_sha
        self.now = now
        self.budget = Budget(settings.token_budget, settings.max_turns, spend_headroom_usd)
        self.phase = "researching"
        self.n_calls = 0
        self.submit_attempts = 0
        self.last_submit_errors: list[str] = []
        self.accepted: Submission | None = None
        self.out_of_attempts = False
        self.end_reason: str | None = None  # why an invalid / budget_exhausted run ended
        self.outputs: dict[str, ToolOutput] = {}  # call_id -> output, for the fact check
        self.fact_flags: dict[str, list[FactCheckFlag]] = {}  # card repo -> T13 flags

    # ---- events ----

    def emit(self, event_type: type[Any], **payload: Any) -> None:
        self.sink.emit(event_type, **payload)

    def emit_usage(self) -> None:
        b = self.budget
        self.emit(
            UsageEvent,
            turn=b.turns,
            max_turns=b.max_turns,
            usage=b.usage,
            token_budget=b.token_budget,
            tokens_remaining=b.tokens_remaining,
        )

    # ---- main ----

    def run(self) -> RunResult:
        started_at = datetime.now(UTC)
        status: RunStatus
        error: str | None = None
        try:
            self.start = self.now()
            self.emit(
                RunStarted,
                settings=self.settings,
                universe=self.universe.header,
                git_sha=self.git_sha,
                prompt_sha256=hashlib.sha256(self.prompt.encode()).hexdigest(),
            )
            self.emit(StatusEvent, phase="starting")
            self.emit(StatusEvent, phase="researching")
            status, error = self.loop()
            if self.accepted is not None:
                self.fact_check()
        except Exception as e:  # a bug or a failing sink: still end the run cleanly
            log.exception("agent loop failed")
            status, error = "error", f"unexpected error: {type(e).__name__}: {e}"
        if status in ("invalid", "budget_exhausted") and error is None:
            error = self.end_reason
        try:
            self.emit(StatusEvent, phase="finished", message=error)
            self.emit(RunFinished, status=status, error=error)
        except Exception:
            log.exception("could not emit run_finished")
        sub = self.accepted
        return RunResult(
            run_id=self.sink.run_id,
            status=status,
            error=error,
            settings=self.settings,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            turns=self.budget.turns,
            usage=self.budget.usage,
            cards=[ResultCard(**c.model_dump(), fact_flags=self.fact_flags.get(c.repo, [])) for c in sub.cards] if sub else [],
            ranking=sub.ranking if sub else [],
            summary=sub.summary if sub else None,
            ranking_below_min=bool(sub) and len(sub.ranking) < self.settings.min_ranked,
            submit_attempts=self.submit_attempts,
            last_submit_errors=[] if sub else self.last_submit_errors,
        )

    def loop(self) -> tuple[RunStatus, str | None]:
        s = self.settings
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": f"{KICKOFF}\n{self.budget.line()}"},
        ]
        all_specs = self.tools.tool_specs()
        submit_specs = [t for t in all_specs if t.get("function", {}).get("name") == "submit"]
        empty_turns = 0
        if self.budget.phase != self.phase:  # e.g. max_turns=1: submit only from the start
            self.phase = self.budget.phase
            self.emit(StatusEvent, phase=self.phase, message=self.budget.line())
        while True:
            elapsed = self.now() - self.start
            if elapsed >= s.max_wall_clock_s:
                return "timeout", f"wall clock limit reached ({s.max_wall_clock_s}s)"
            if self.budget.exhausted:
                used = "turns" if self.budget.turns >= s.max_turns else "tokens"
                if self.submit_attempts:
                    self.end_reason = f"budget ran out ({used}) after {self.submit_attempts} rejected submit(s)"
                    return "invalid", None
                self.end_reason = f"budget ran out ({used}) before any submit"
                return "budget_exhausted", None
            submit_only = self.phase == "submit_only"
            try:
                reply = self.model.complete(
                    messages,
                    submit_specs if submit_only else all_specs,
                    tool_choice=FORCE_SUBMIT if submit_only else "auto",
                    timeout_s=min(s.max_wall_clock_s - elapsed, MODEL_TIMEOUT_S),
                )
            except ModelError as e:
                if e.kind == "timeout":
                    if self.now() - self.start >= s.max_wall_clock_s:
                        return "timeout", f"wall clock limit reached ({s.max_wall_clock_s}s): {e}"
                    return "error", f"model stalled (timeout): {e}"
                return "error", f"model error ({e.kind}): {e}"
            except Exception as e:
                return "error", f"model call failed: {type(e).__name__}: {e}"

            self.budget.add(reply.usage)
            turn = self.budget.turns
            if reply.content and reply.content.strip():
                self.emit(Narration, turn=turn, text=reply.content)
            if reply.reasoning and reply.reasoning.strip():
                self.emit(Reasoning, turn=turn, text=reply.reasoning)
            messages.append(_assistant_message(reply))

            nudge = ""
            if reply.tool_calls:
                empty_turns = 0
                try:
                    messages.extend(self.run_tools(turn, reply.tool_calls, submit_only))
                except _WallClockExceeded:
                    self.emit_usage()
                    return "timeout", f"wall clock limit reached ({s.max_wall_clock_s}s) while tools were running"
            else:
                empty_turns += 1
                nudge = NUDGE + "\n"
            self.emit_usage()

            if self.accepted is not None:
                return "ok", None
            if self.out_of_attempts:
                self.end_reason = f"submit rejected {self.submit_attempts} times; no attempts left"
                return "invalid", None
            if empty_turns >= MAX_EMPTY_TURNS:
                return "error", f"model stalled: {empty_turns} replies in a row without a tool call"

            phase = self.budget.phase
            if phase != self.phase:
                self.phase = phase
                self.emit(StatusEvent, phase=phase, message=self.budget.line())
            messages.append({"role": "user", "content": nudge + self.budget.line()})

    # ---- tools ----

    def run_tools(self, turn: int, requests: list[ToolCallRequest], submit_only: bool) -> list[dict[str, Any]]:
        """Emit tool_call events, run the calls, emit tool_result as each finishes. Returns the tool messages."""
        calls = []
        for req in requests:
            self.n_calls += 1
            call = _Call(req, f"c_{self.n_calls:03d}", _parse_args(req.arguments))
            self.emit(ToolCall, turn=turn, call_id=call.call_id, tool=req.name, args=call.args if call.args is not None else {"_raw": req.arguments})
            calls.append(call)

        later, parallel = [], []
        for c in calls:
            name = c.req.name
            if name not in TOOL_ARGS:
                c.text = f"error: unknown tool {name!r}. Available tools: {', '.join(TOOL_ARGS)}."
                self.emit(StatusEvent, phase=self.phase, message=f"model called unknown tool {name!r} ({c.call_id})")
            elif c.args is None:
                self.finish(turn, c, self.error(c, "BAD_ARGS", "arguments are not a valid JSON object"), 0)
            elif submit_only and name != "submit":
                self.finish(turn, c, self.error(c, "BUDGET_EXHAUSTED", "budget exhausted: only submit is allowed now"), 0)
            elif name == "submit":
                later.append(c)
            else:
                parallel.append(c)

        if parallel:
            # Bounded by the wall clock: a hung tool must not hold the run past max_wall_clock_s.
            remaining = self.settings.max_wall_clock_s - (self.now() - self.start)
            pool = ThreadPoolExecutor(max_workers=len(parallel))
            try:
                futures = {pool.submit(self.execute, c): c for c in parallel}
                for f in as_completed(futures, timeout=max(remaining, 0.1)):
                    out, ms = f.result()
                    self.finish(turn, futures[f], out, ms)
            except FuturesTimeout:
                raise _WallClockExceeded from None
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        for c in later:
            if self.accepted is not None or self.out_of_attempts:
                out = self.error(c, "BAD_ARGS", "the run is over: a submission was already accepted or no attempts are left")
                self.finish(turn, c, out, 0)
                continue
            out, ms = self.execute(c)
            self.finish(turn, c, out, ms)
            if isinstance(out, SubmitOutput):
                self.submit_attempts += 1
                if out.ok:
                    self.accepted = Submission.model_validate(c.args)
                else:
                    self.last_submit_errors = list(out.errors)
                    self.out_of_attempts = out.attempts_left == 0

        return [
            {
                "role": "tool",
                "tool_call_id": c.req.id,
                "content": c.text if c.output is None else c.output.model_dump_json(exclude_none=True),
            }
            for c in calls
        ]

    def error(self, c: _Call, code: str, message: str) -> ErrorOutput:
        return ErrorOutput(call_id=c.call_id, tool=c.req.name, error=ToolError(code=code, message=message))

    def execute(self, c: _Call) -> tuple[ToolOutput, int]:
        t0 = time.monotonic()
        try:
            out = self.tools.execute(c.req.name, c.args or {}, c.call_id)
        except Exception as e:
            log.warning("tool %s (%s) raised: %s", c.req.name, c.call_id, e)
            out = self.error(c, "UPSTREAM", f"tool failed: {type(e).__name__}: {e}")
        return out, int((time.monotonic() - t0) * 1000)

    def fact_check(self) -> None:
        """T13: flag card numbers that don't match the outputs they cite. Never fails the run."""
        self.emit(StatusEvent, phase="fact_checking")
        for card in self.accepted.cards:
            try:
                flags = check_card(card, self.outputs)
            except Exception:
                log.exception("fact check failed for %s", card.repo)
                continue
            self.fact_flags[card.repo] = flags
            self.emit(FactCheckEvent, repo=card.repo, flags=flags)

    def finish(self, turn: int, c: _Call, out: ToolOutput, duration_ms: int) -> None:
        c.output = out
        self.outputs[c.call_id] = out
        self.emit(
            ToolResult,
            turn=turn,
            call_id=c.call_id,
            ok=not isinstance(out, ErrorOutput),
            output=out,
            approx_tokens=len(out.model_dump_json(exclude_none=True)) // 4,
            duration_ms=duration_ms,
        )
