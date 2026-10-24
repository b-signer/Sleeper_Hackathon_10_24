"""StubTools: a small ToolExecutor for any Universe. Test double for the loop tests; production uses
sleeper.agent.toolbox.Toolbox with DataTools (T09).

browse / repo_details / readme delegate to the sleeper.fakes helpers; papers finds none. submit checks the submission's shape
(contract), card and ranking repos are in the universe, citations point at earlier successful calls, and
the number of picks; it counts attempts. T09 replaces this; tests keep using it.
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import ValidationError

from sleeper.agent.tool_specs import tool_specs
from sleeper.contracts import (
    TOOL_ARGS,
    BrowseArgs,
    ErrorOutput,
    PapersArgs,
    PapersOutput,
    ReadmeArgs,
    RepoArgs,
    Submission,
    SubmitOutput,
    ToolError,
    ToolOutput,
    Universe,
)
from sleeper.fakes import browse_output, readme_output, repo_details_output


def _errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}" for err in e.errors()]


class StubTools:
    def __init__(self, universe: Universe, *, max_submit_attempts: int = 3, max_picks: int = 5) -> None:
        self.universe = universe
        self.max_submit_attempts = max_submit_attempts
        self.max_picks = max_picks
        self.outputs: dict[str, ToolOutput] = {}
        self.submit_attempts = 0
        self._lock = threading.Lock()

    def tool_specs(self) -> list[dict[str, Any]]:
        return tool_specs()

    def execute(self, name: str, args: dict[str, Any], call_id: str) -> ToolOutput:
        if name not in TOOL_ARGS:
            raise ValueError(f"unknown tool {name!r}")
        if name == "submit":
            with self._lock:
                out: ToolOutput = self._submit(args, call_id)
                self.outputs[call_id] = out
            return out
        try:
            parsed = TOOL_ARGS[name].model_validate(args)
        except ValidationError as e:
            out = ErrorOutput(call_id=call_id, tool=name, error=ToolError(code="BAD_ARGS", message="; ".join(_errors(e))))
        else:
            if isinstance(parsed, BrowseArgs):
                out = browse_output(self.universe, parsed, call_id)
            elif isinstance(parsed, ReadmeArgs):
                out = readme_output(self.universe, parsed.repo, call_id, parsed.max_chars)
            elif isinstance(parsed, PapersArgs):
                out = self._papers(parsed.repo, call_id)
            else:
                assert isinstance(parsed, RepoArgs)
                out = repo_details_output(self.universe, parsed.repo, call_id)
        with self._lock:
            self.outputs[call_id] = out
        return out

    def _papers(self, repo: str, call_id: str) -> PapersOutput | ErrorOutput:
        row = self.universe.get(repo)
        if row is None:
            error = ToolError(code="NOT_IN_UNIVERSE", message=f"{repo} is not in the universe")
            return ErrorOutput(call_id=call_id, tool="papers", error=error)
        return PapersOutput(call_id=call_id, repo=row.repo, papers=[], total_linked=0)

    def _submit(self, args: dict[str, Any], call_id: str) -> SubmitOutput:
        if self.submit_attempts >= self.max_submit_attempts:
            return SubmitOutput(call_id=call_id, ok=False, errors=["no submit attempts left"], attempts_left=0)
        self.submit_attempts += 1
        left = self.max_submit_attempts - self.submit_attempts
        try:
            sub = Submission.model_validate(args)
        except ValidationError as e:
            return SubmitOutput(call_id=call_id, ok=False, errors=_errors(e), attempts_left=left)
        errors = []
        if len(sub.cards) > self.max_picks:
            errors.append(f"too many picks: {len(sub.cards)} cards, at most {self.max_picks}")
        for repo in [*(c.repo for c in sub.cards), *(e.repo for e in sub.ranking)]:
            if self.universe.get(repo) is None:
                errors.append(f"{repo} is not in the universe")
        for card in sub.cards:
            for cid in sorted(card.cited_call_ids()):
                out = self.outputs.get(cid)
                if out is None:
                    errors.append(f"card {card.repo} cites {cid}, which does not exist")
                elif isinstance(out, ErrorOutput):
                    errors.append(f"card {card.repo} cites {cid}, which failed ({out.error.code}); error outputs can't be cited")
                elif isinstance(out, SubmitOutput):
                    errors.append(f"card {card.repo} cites {cid}, a submit output; cite research outputs")
        errors = list(dict.fromkeys(errors))
        return SubmitOutput(call_id=call_id, ok=not errors, errors=errors, attempts_left=left)
