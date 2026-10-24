"""A scripted ChatModel for offline runs and tests, and a full fake run script (`fake_script`).

A script is a list of steps, one per model call: a ModelReply, a callable that builds one from the
messages so far (e.g. to cite the call ids the loop assigned), or an exception to raise.
"""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from sleeper.agent.types import ModelError, ModelReply, ToolCallRequest
from sleeper.contracts import (
    TOOL_OUTPUT_ADAPTER,
    Card,
    CardCriteria,
    Claim,
    CriterionRating,
    ErrorOutput,
    KeyFigure,
    RankingEntry,
    ReadmeOutput,
    RepoDetailsOutput,
    Risk,
    Submission,
    ToolOutput,
    Universe,
    Usage,
)
from sleeper.fakes import PRICE_PER_M

ScriptStep = ModelReply | Callable[[list[dict[str, Any]]], ModelReply] | Exception

_call_ids = itertools.count(1)


def reply(
    text: str | None,
    *calls: tuple[str, dict[str, Any] | str],
    prompt_tokens: int = 3000,
    completion_tokens: int = 200,
    cost_usd: float | None = None,
) -> ModelReply:
    """A ModelReply with tool calls (name, args). Args may be a raw string (e.g. invalid JSON).

    Cost defaults to luna prices (sleeper.fakes.PRICE_PER_M).
    """
    if cost_usd is None:
        cost_usd = round((prompt_tokens * PRICE_PER_M[0] + completion_tokens * PRICE_PER_M[1]) / 1e6, 8)
    return ModelReply(
        content=text,
        tool_calls=[
            ToolCallRequest(id=f"call_{next(_call_ids)}", name=name, arguments=args if isinstance(args, str) else json.dumps(args))
            for name, args in calls
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost_usd,
        ),
        finish_reason="tool_calls" if calls else "stop",
    )


class ScriptedModel:
    """ChatModel that plays back a script. `calls` records every complete() for assertions."""

    def __init__(self, steps: list[ScriptStep], model_id: str = "scripted/fake") -> None:
        self.model_id = model_id
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | dict[str, Any] = "auto",
        timeout_s: float = 120.0,
    ) -> ModelReply:
        self.calls.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools), "tool_choice": tool_choice, "timeout_s": timeout_s}
        )
        if not self.steps:
            raise ModelError("bad_response", "scripted model: script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages)
        return step


# ---- Full fake run ----------------------------------------------------------

OUTSIDE_REPO = "rust-lang/cargo"  # not in the fake universe: shows a NOT_IN_UNIVERSE error

FAKE_NARRATION: tuple[str, ...] = (
    "Start broad: browse the universe by momentum to see which repos are gaining attention fastest.",
    "Momentum alone rewards hype. Screen for active, decent-quality repos that are not popular yet.",
    f"Look closely at the strongest candidates, and check whether {OUTSIDE_REPO} is in scope.",
    "Read the READMEs to judge whether these projects deliver what they claim.",
    "Submitting three picks and the ranking.",
    "One citation pointed at a failed call; citing the repo details instead and resubmitting.",
)
_TOKENS = [(3100, 160), (5200, 190), (9800, 240), (16900, 310), (24100, 1450), (25600, 1500)]

# Key figure per criterion: the first known metric wins (stars and age_days are always known).
_FIGURES: dict[str, list[tuple[str, str, str]]] = {
    "popularity": [("stars", "Stars", "stars")],
    "momentum": [("forks_30d", "Forks, last 30 days", "forks"), ("stars_per_day", "Stars per day", "stars/day"), ("stars", "Stars", "stars")],
    "quality": [("commits_90d", "Commits, last 90 days", "commits"), ("releases", "Releases", "releases"), ("stars", "Stars", "stars")],
    "newness": [("age_days", "Age", "days")],
}


def tool_outputs(messages: list[dict[str, Any]]) -> list[ToolOutput]:
    """Every tool output in the history so far (tool messages that parse as a ToolOutput)."""
    outs = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            outs.append(TOOL_OUTPUT_ADAPTER.validate_json(m["content"]))
        except ValidationError:
            continue  # e.g. the error text for an unknown tool
    return outs


def _criteria(d: RepoDetailsOutput) -> CardCriteria:
    ratings = {}
    for criterion, options in _FIGURES.items():
        metric, label, unit = next(o for o in options if getattr(d.metrics, o[0]) is not None)
        ratings[criterion] = CriterionRating(
            score=getattr(d.scores, criterion),
            score_call_id=d.call_id,
            key_figure=KeyFigure(
                label=label, value=getattr(d.metrics, metric), unit=unit, source_call_id=d.call_id, source_path=f"/metrics/{metric}"
            ),
        )
    return CardCriteria(**ratings)


def _submission(messages: list[dict[str, Any]], picks: list[str], *, cite_failed: bool) -> dict[str, Any]:
    outs = tool_outputs(messages)
    details = {o.repo: o for o in outs if isinstance(o, RepoDetailsOutput)}
    readmes = {o.repo: o for o in outs if isinstance(o, ReadmeOutput)}
    failed = next((o.call_id for o in outs if isinstance(o, ErrorOutput)), "c_999")
    ranked = [r for r in picks if r in details]
    cards = []
    for repo in ranked[:3]:
        d = details[repo]
        m = d.metrics
        case = [Claim(text=f"{m.stars} stars, {m.age_days} days old", source_call_ids=[d.call_id])]
        if repo in readmes:
            case.append(Claim(text="The README documents installation and usage", source_call_ids=[readmes[repo].call_id]))
        if m.commits_90d is not None:
            case.append(Claim(text=f"{m.commits_90d} commits in the last 90 days", source_call_ids=[d.call_id]))
        cards.append(
            Card(
                repo=d.repo,
                pitch=(m.description or d.repo)[:200],
                criteria=_criteria(d),
                case=case,
                risks=[Risk(text=f"Young: {m.age_days} days old", source_call_ids=[d.call_id])],
                proven_wrong_if=["No commits in the next 60 days"],
                confidence=0.6,
            )
        )
    if cite_failed:  # a citation to a failed call: the submit tool rejects it
        cards[0].case.append(Claim(text=f"Not a fork of {OUTSIDE_REPO}", source_call_ids=[failed]))
    sub = Submission(
        cards=cards,
        ranking=[RankingEntry(rank=i, repo=details[r].repo) for i, r in enumerate(ranked, 1)],
        summary="Young repos with steady commits and fast star growth at modest star counts.",
    )
    return sub.model_dump(mode="json", exclude_none=True)


def fake_script(universe: Universe) -> list[ScriptStep]:
    """A full run that ends `ok` after 6 turns, modelled on sleeper.fakes.fake_run().

    Browse; browse with filters; repo_details for the 5 highest-momentum repos (popularity <= 70 first) plus one outside the
    universe (NOT_IN_UNIVERSE); READMEs for the top 3 plus one without a README (NOT_FOUND) if any;
    a submit citing a failed call (rejected); a corrected submit (accepted). Works on any universe
    with at least one repo; the submissions are built from the tool outputs in the history.
    """
    rows = sorted(universe.rows, key=lambda r: (r.scores.popularity > 70, -r.scores.momentum))  # not yet popular first
    picks = [r.repo for r in rows[:5]]
    no_readme = next((r.repo for r in universe.rows if not r.metrics.readme_chars and r.repo not in picks[:3]), None)
    readmes = [*picks[:3], *([no_readme] if no_readme else [])]
    t = iter(_TOKENS)

    def tokens() -> dict[str, int]:
        p, c = next(t)
        return {"prompt_tokens": p, "completion_tokens": c}

    first_tokens, second_tokens = tokens(), tokens()
    steps: list[ScriptStep] = [
        reply(FAKE_NARRATION[0], ("browse", {"sort_by": "momentum", "limit": 10}), **first_tokens),
        reply(
            FAKE_NARRATION[1],
            ("browse", {
                "filters": [{"field": "commits_90d", "op": ">", "value": 20}, {"field": "popularity", "op": "<=", "value": 70}],
                "sort_by": "momentum",
                "limit": 10,
            }),
            **second_tokens,
        ),
        reply(FAKE_NARRATION[2], *[("repo_details", {"repo": r}) for r in [*picks, OUTSIDE_REPO]], **tokens()),
        reply(FAKE_NARRATION[3], *[("readme", {"repo": r}) for r in readmes], **tokens()),
    ]
    submit_tokens = [tokens(), tokens()]
    steps.append(lambda msgs: reply(FAKE_NARRATION[4], ("submit", _submission(msgs, picks, cite_failed=True)), **submit_tokens[0]))
    steps.append(lambda msgs: reply(FAKE_NARRATION[5], ("submit", _submission(msgs, picks, cite_failed=False)), **submit_tokens[1]))
    return steps
