"""Toolbox: the real ToolExecutor (T09, plan §2.2).

Dispatches the tools, validates data-tool arguments (BAD_ARGS), records every output by call_id, tracks
the repos researched in depth and the submit attempts, and validates submissions with check_submission.
The data tools run outside the lock, so one turn's calls stay parallel; submit runs under the lock.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from pydantic import ValidationError

from sleeper.agent.submit_check import SubmitContext, check_submission
from sleeper.agent.tool_specs import tool_specs
from sleeper.contracts import (
    TOOL_ARGS,
    BrowseArgs,
    BrowseOutput,
    ErrorOutput,
    PapersArgs,
    PapersOutput,
    ReadmeArgs,
    ReadmeOutput,
    RepoArgs,
    RepoDetailsOutput,
    SubmitOutput,
    ToolError,
    ToolOutput,
    Universe,
)


class DataToolsLike(Protocol):
    def browse(self, args: BrowseArgs, call_id: str) -> BrowseOutput: ...
    def repo_details(self, args: RepoArgs, call_id: str) -> RepoDetailsOutput | ErrorOutput: ...
    def readme(self, args: ReadmeArgs, call_id: str) -> ReadmeOutput | ErrorOutput: ...
    def papers(self, args: PapersArgs, call_id: str) -> PapersOutput | ErrorOutput: ...


def _call_num(call_id: str) -> int:
    try:
        return int(call_id.rsplit("_", 1)[-1])
    except ValueError:
        return 1 << 62


class Toolbox:
    def __init__(self, universe: Universe, data: DataToolsLike, *, max_picks: int = 5, max_submit_attempts: int = 3) -> None:
        self.universe = universe
        self.data = data
        self.max_picks = max_picks
        self.max_submit_attempts = max_submit_attempts
        self.outputs: dict[str, ToolOutput] = {}
        self.researched: dict[str, tuple[str, str]] = {}
        self.submit_attempts = 0
        self._lock = threading.Lock()

    def tool_specs(self) -> list[dict[str, Any]]:
        return tool_specs()

    def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolOutput:
        if name not in TOOL_ARGS:
            raise ValueError(f"unknown tool {name!r}")
        if name == "submit":
            with self._lock:
                sub_out = self._submit(args, call_id)
                self.outputs[call_id] = sub_out
            return sub_out
        try:
            parsed = TOOL_ARGS[name].model_validate(args)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}" for err in e.errors())
            out: ToolOutput = ErrorOutput(call_id=call_id, tool=name, error=ToolError(code="BAD_ARGS", message=msg))
        else:
            if isinstance(parsed, BrowseArgs):
                out = self.data.browse(parsed, call_id)
            elif isinstance(parsed, ReadmeArgs):
                out = self.data.readme(parsed, call_id)
            elif isinstance(parsed, PapersArgs):
                out = self.data.papers(parsed, call_id)
            else:
                assert isinstance(parsed, RepoArgs)
                out = self.data.repo_details(parsed, call_id)
        self._record(out, call_id)
        return out

    def _record(self, out: ToolOutput, call_id: str) -> None:
        with self._lock:
            self.outputs[call_id] = out
            if isinstance(out, RepoDetailsOutput | ReadmeOutput | PapersOutput):
                key = out.repo.lower()
                prev = self.researched.get(key)
                if prev is None or _call_num(call_id) < _call_num(prev[1]):
                    self.researched[key] = (out.repo, call_id)

    def _submit(self, args: dict[str, Any], call_id: str) -> SubmitOutput:
        if self.submit_attempts >= self.max_submit_attempts:
            return SubmitOutput(call_id=call_id, ok=False, errors=["no submit attempts left"], attempts_left=0)
        self.submit_attempts += 1
        left = self.max_submit_attempts - self.submit_attempts
        ctx = SubmitContext(
            universe=self.universe, max_picks=self.max_picks, outputs=dict(self.outputs), researched=dict(self.researched)
        )
        _, errors = check_submission(args, ctx)
        return SubmitOutput(call_id=call_id, ok=not errors, errors=errors, attempts_left=left)
