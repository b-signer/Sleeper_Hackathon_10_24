"""Skeptic review (T12, F5.1): after an accepted submit, one model call per card argues against the pick.

The reviewer sees only the card and the full outputs of the calls it cites (no history, no tools other
than `verdict`, no web), and must answer by calling `verdict`, which is forced with tool_choice. The
answer becomes a SkepticReview: `killed` cards stay on the list and the UI strikes them through.
Cited call IDs outside the card's citations are dropped. A failed or malformed review leaves the
card without one; it never fails the run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sleeper.agent.types import ChatModel, ModelReply
from sleeper.contracts import Card, SkepticReview, ToolOutput, Usage, UniverseHeader

log = logging.getLogger(__name__)

SKEPTIC_TIMEOUT_S = 60.0
MAX_OUTPUT_CHARS = 12_000  # per cited output; a long README must not blow up a cheap call
MAX_ARGUMENT_CHARS = 1200  # SkepticReview.argument limit

SYSTEM = """You are a skeptical reviewer. You will get a pick card arguing that {repo} is an underrated GitHub repository as of {as_of}, followed by the tool outputs it cites.
Use only these outputs. Do not use any outside knowledge about this repo, its authors, or anything after {as_of}. Your memory is not a source.
Make the strongest case that the pick is wrong. Consider: dependence on a single maintainer, stalled or declining commits, star bursts that look one-off, a README that promises more than the evidence shows, signs that the repo is not really in the topic ({topic}), and weak or cherry-picked evidence on the card.
Verdict: "killed" if a careful person should not pick this repo; "weakened" if the concerns are real but the pick is still plausible; "survives" otherwise.
Argument: at most 120 words. Any number you use must appear in the outputs. Cite the call IDs you rely on.
Answer by calling the verdict tool."""

VERDICT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "verdict",
        "description": "Record your verdict on the pick.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["survives", "weakened", "killed"]},
                "argument": {"type": "string", "description": "The case against the pick, at most 120 words"},
                "cited_call_ids": {"type": "array", "items": {"type": "string"}, "description": "Call IDs you rely on, e.g. c_004"},
            },
            "required": ["verdict", "argument", "cited_call_ids"],
            "additionalProperties": False,
        },
    },
}
FORCE_VERDICT = {"type": "function", "function": {"name": "verdict"}}


@dataclass
class SkepticOutcome:
    repo: str
    review: SkepticReview | None
    usage: Usage  # zero if the call failed before a reply
    error: str | None = None


def review_messages(card: Card, outputs: dict[str, ToolOutput], header: UniverseHeader) -> list[dict[str, Any]]:
    """System prompt plus one user message: the card, then each cited output (truncated)."""
    as_of = header.built_at.date().isoformat()
    system = SYSTEM.format(repo=card.repo, as_of=as_of, topic=header.topic_label)
    parts = ["## Card", card.model_dump_json(indent=1, exclude_none=True), "", "## Cited tool outputs"]
    for call_id in sorted(card.cited_call_ids()):
        out = outputs.get(call_id)
        text = out.model_dump_json(exclude_none=True) if out is not None else '{"error": "output not available"}'
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + " ...[truncated]"
        parts += [f"### {call_id}", text]
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]


def parse_review(reply: ModelReply, card: Card, model_id: str) -> SkepticReview:
    """The verdict call's arguments (or, failing that, JSON in the content) as a SkepticReview.
    Raises ValueError if neither parses."""
    raw = next((c.arguments for c in reply.tool_calls if c.name == "verdict"), None) or reply.content or ""
    try:
        args = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"verdict is not JSON: {raw[:120]!r}") from e
    if not isinstance(args, dict) or args.get("verdict") not in ("survives", "weakened", "killed"):
        raise ValueError(f"no valid verdict in {raw[:120]!r}")
    allowed = card.cited_call_ids()
    cited = [c for c in args.get("cited_call_ids") or [] if isinstance(c, str) and c in allowed]
    argument = str(args.get("argument") or "").strip()
    if len(argument) > MAX_ARGUMENT_CHARS:
        argument = argument[: MAX_ARGUMENT_CHARS - 3].rstrip() + "..."
    return SkepticReview(verdict=args["verdict"], argument=argument, cited_call_ids=list(dict.fromkeys(cited)), model=model_id)


def review_card(
    model: ChatModel,
    card: Card,
    outputs: dict[str, ToolOutput],
    header: UniverseHeader,
    *,
    timeout_s: float = SKEPTIC_TIMEOUT_S,
) -> SkepticOutcome:
    """One skeptic call for one card. Never raises."""
    try:
        reply = model.complete(
            review_messages(card, outputs, header), [VERDICT_TOOL], tool_choice=FORCE_VERDICT, timeout_s=timeout_s
        )
    except Exception as e:
        log.warning("skeptic call for %s failed: %s", card.repo, e)
        return SkepticOutcome(card.repo, None, Usage(), f"skeptic call failed: {e}")
    try:
        return SkepticOutcome(card.repo, parse_review(reply, card, model.model_id), reply.usage)
    except ValueError as e:
        log.warning("skeptic reply for %s unusable: %s", card.repo, e)
        return SkepticOutcome(card.repo, None, reply.usage, f"skeptic reply unusable: {e}")
