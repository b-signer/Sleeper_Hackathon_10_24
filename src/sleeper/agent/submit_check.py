"""Submission validation for the submit tool (T09, plan §5).

`check_submission(args, ctx)` runs the rules in order and collects every error, each a specific, fixable
message that names the card, the field, the call id and the expected value:

1. shape (the contract); on failure nothing else is checked
2. number of picks
3. card and ranking repos are in the universe
4. citations exist and are not errors or submit outputs
5. scores match the cited repo_details / browse row (within SCORE_ABS_TOL)
6. key figures point at a number of the card's repo and match it (key_figure_matches)
7. every repo researched in depth is ranked
8. dedupe, at most MAX_ERRORS messages
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from sleeper.contracts import (
    CRITERIA,
    BrowseOutput,
    Card,
    ErrorOutput,
    ReadmeOutput,
    RepoDetailsOutput,
    Submission,
    SubmitOutput,
    ToolOutput,
    Universe,
)

MAX_ERRORS = 20
SCORE_ABS_TOL = 0.5  # models round 81.2 to 81
KEY_FIGURE_REL_TOL = 0.01  # normal display rounding: 1,230 for 1,234
KEY_FIGURE_ABS_TOL = 0.051  # 2.4 for 2.43
COPY_HINT = "copy the number as it appears at source_path, in the source's unit (no k/months conversion)"


@dataclass(frozen=True)
class SubmitContext:
    universe: Universe
    max_picks: int
    outputs: Mapping[str, ToolOutput]
    researched: Mapping[str, tuple[str, str]]  # lower-case repo -> (canonical repo, call_id), as Toolbox.researched


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer lookup. Raises KeyError if the path does not exist."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = doc
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                raise KeyError(pointer)
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")) or int(token) >= len(node):
                raise KeyError(pointer)
            node = node[int(token)]
        else:
            raise KeyError(pointer)
    return node


def key_figure_matches(value: float, source: float) -> bool:
    return abs(value - source) <= max(KEY_FIGURE_REL_TOL * abs(source), KEY_FIGURE_ABS_TOL)


def _fmt(x: float) -> str:
    return f"{x:g}" if isinstance(x, float) else str(x)


def _shape_errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}" for err in e.errors()]


def _browse_row_index(out: BrowseOutput, repo: str) -> int | None:
    key = repo.lower()
    return next((i for i, r in enumerate(out.rows) if r.repo.lower() == key), None)


def _usable(cid: str, ctx: SubmitContext) -> ToolOutput | None:
    """The cited output if it can be cited (rule 4 reports the rest)."""
    out = ctx.outputs.get(cid)
    if out is None or isinstance(out, ErrorOutput | SubmitOutput):
        return None
    return out


def _check_citations(where: str, card: Card, ctx: SubmitContext) -> list[str]:
    errors = []
    for cid in sorted(card.cited_call_ids()):
        out = ctx.outputs.get(cid)
        if out is None:
            errors.append(f"{where} cites {cid}, which does not exist")
        elif isinstance(out, ErrorOutput):
            errors.append(f"{where} cites {cid}, which failed ({out.error.code}); error outputs can't be cited")
        elif isinstance(out, SubmitOutput):
            errors.append(f"{where} cites {cid}, a submit output; cite research outputs")
    return errors


def _check_score(where: str, card: Card, criterion: str, ctx: SubmitContext) -> list[str]:
    rating = getattr(card.criteria, criterion)
    field = f"{where} criteria.{criterion}.score"
    cid = rating.score_call_id
    out = _usable(cid, ctx)
    if out is None:
        return []
    if isinstance(out, RepoDetailsOutput):
        if out.repo.lower() != card.repo.lower():
            return [f"{field}: {cid} is repo_details for {out.repo}, not {card.repo}; cite this repo's own score"]
        source = getattr(out.scores, criterion)
    elif isinstance(out, BrowseOutput):
        i = _browse_row_index(out, card.repo)
        if i is None:
            return [f"{field}: {cid} (browse) has no row for {card.repo}; cite a repo_details or browse output that shows it"]
        source = getattr(out.rows[i].scores, criterion)
    else:
        return [f"{field}: {cid} is a {out.tool} output with no scores; cite repo_details or a browse row for {card.repo}"]
    if abs(rating.score - source) > SCORE_ABS_TOL:
        return [f"{field} {_fmt(rating.score)} does not match {_fmt(source)} in {cid}"]
    return []


def _check_key_figure(where: str, card: Card, criterion: str, ctx: SubmitContext) -> list[str]:
    kf = getattr(card.criteria, criterion).key_figure
    field = f"{where} criteria.{criterion}.key_figure"
    cid, path = kf.source_call_id, kf.source_path
    out = _usable(cid, ctx)
    if out is None:
        return []
    try:
        source = resolve_pointer(out.model_dump(mode="json"), path)
    except KeyError:
        return [f"{field}: no number at {path or '(empty path)'} in {cid} (no such path)"]
    if source is None:
        return [f"{field}: no number at {path} in {cid} (null)"]
    if isinstance(source, bool) or not isinstance(source, int | float):
        return [f"{field}: no number at {path or '(empty path)'} in {cid} (a {type(source).__name__})"]
    # The number must belong to the card's repo.
    if isinstance(out, RepoDetailsOutput | ReadmeOutput):
        if out.repo.lower() != card.repo.lower():
            return [f"{field}: {cid} is {out.tool} for {out.repo}, not {card.repo}; cite this repo's own number"]
    elif isinstance(out, BrowseOutput):
        parts = path.split("/")
        row = None
        if len(parts) >= 4 and parts[1] == "rows" and parts[2].isdigit() and int(parts[2]) < len(out.rows):
            row = out.rows[int(parts[2])]
        if row is None:
            return [f"{field}: {path} in {cid} (browse) is not inside a row; point under /rows/<i>/ at {card.repo}'s row"]
        if row.repo.lower() != card.repo.lower():
            return [f"{field}: {path} in {cid} is {row.repo}'s row, not {card.repo}'s"]
    if not key_figure_matches(kf.value, source):
        return [f"{field}: value {_fmt(kf.value)} does not match {_fmt(source)} at {path} in {cid}; {COPY_HINT}"]
    return []


def _cap(errors: list[str]) -> list[str]:
    errors = list(dict.fromkeys(errors))
    if len(errors) > MAX_ERRORS:
        rest = len(errors) - (MAX_ERRORS - 1)
        errors = [*errors[: MAX_ERRORS - 1], f"... and {rest} more"]
    return errors


def check_submission(args: dict[str, Any], ctx: SubmitContext) -> tuple[Submission | None, list[str]]:
    """(submission, []) if valid; else (None on a shape error, or the parsed submission, [messages])."""
    try:
        sub = Submission.model_validate(args)
    except ValidationError as e:
        return None, _cap(_shape_errors(e))

    errors: list[str] = []
    if len(sub.cards) > ctx.max_picks:
        errors.append(f"too many picks: {len(sub.cards)} cards, at most {ctx.max_picks}")

    for repo in dict.fromkeys([*(c.repo for c in sub.cards), *(e.repo for e in sub.ranking)]):
        if ctx.universe.get(repo) is None:
            errors.append(f"{repo} is not in the universe")

    for i, card in enumerate(sub.cards):
        where = f"cards[{i}] ({card.repo})"
        errors += _check_citations(where, card, ctx)
        for criterion in CRITERIA:
            errors += _check_score(where, card, criterion, ctx)
            errors += _check_key_figure(where, card, criterion, ctx)

    ranked = {e.repo.lower() for e in sub.ranking}
    for key, (repo, cid) in sorted(ctx.researched.items(), key=lambda kv: int(kv[1][1][2:])):
        if key not in ranked:
            errors.append(f"ranking: missing {repo}, which you looked at closely ({cid})")

    errors = _cap(errors)
    return sub, errors
