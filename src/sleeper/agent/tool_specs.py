"""OpenAI-style tool schemas for the agent, generated from the contracts' TOOL_ARGS (T09 reuses this).

The pydantic schemas are flattened for picky providers: `$ref`s are inlined (no `$defs`), `title`s are
dropped, and fixed-length tuples (`prefixItems`) become plain arrays with min/max items.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sleeper.contracts import TOOL_ARGS

DESCRIPTIONS: dict[str, str] = {
    "browse": (
        "Screen the universe: filter (AND) and sort by any metric or criterion score, optional keyword (matches "
        "repo name or description) and language. Returns up to `limit` compact rows (repo, description, stars, "
        "scores) and the total match count. Cheap: use it several times with different sorts and filters to find "
        "candidates, and use the descriptions to screen out repos that are not in the topic. Keep limit <= 25 and "
        "page with offset."
    ),
    "repo_details": (
        "Full metrics (stars, star velocity, recent forks, commits and authors in 90 days, releases, last commit, "
        "age, description, license) and the four criterion scores for one repo in the universe. Cards copy their "
        "scores and key figures from this output."
    ),
    "readme": (
        "The repo's README text, capped at max_chars. Use max_chars 2500 (at most 3000) to keep the run in budget. "
        "Use it to judge what the project does, whether it fits the topic, and how mature it is."
    ),
    "papers": (
        "The arXiv papers linked anywhere in the repo's full README (not only the part readme returned): title, "
        "first-version date and citation count (null when unknown). Returns an empty list when there are none. "
        "Cheap. found=false means the link points at no real paper. Citations are the paper's, not the repo's."
    ),
    "submit": (
        "Submit your picks (cards citing call ids), the ranking of every repo you looked at closely, and a summary. "
        "Rules the submit tool checks: every cited call id must be an earlier successful research call; each "
        "criterion score must be copied from the cited repo_details output or browse row of that card's repo; "
        "each key_figure.value must be the number at source_path (a JSON Pointer such as /metrics/forks_30d) in "
        "the cited output of that card's repo, copied in the source's unit (no k or months conversion); the "
        "ranking must include every card repo and every repo you called repo_details, readme or papers on successfully. "
        "Rejected submissions come back with specific errors and use up one of 3 attempts; fix them and resubmit. "
        "Ends the run when accepted."
    ),
}


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_resolve(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        extra = {k: v for k, v in node.items() if k != "$ref"}
        return _resolve({**target, **extra}, defs)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("$defs", "title"):
            continue
        if key == "properties":
            out[key] = {name: _resolve(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _resolve(value, defs)
    items = out.pop("prefixItems", None)
    if items is not None:
        out["items"] = items[0] if all(i == items[0] for i in items) else {"anyOf": items}
        out["minItems"] = out["maxItems"] = len(items)
    return out


def parameters(name: str) -> dict[str, Any]:
    """The JSON Schema of one tool's arguments, with refs inlined."""
    schema = TOOL_ARGS[name].model_json_schema()
    return _resolve(schema, schema.get("$defs", {}))


def tool_specs(names: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """`tools` array for chat completions: one function per tool name (default: all of TOOL_ARGS)."""
    return [
        {"type": "function", "function": {"name": n, "description": DESCRIPTIONS[n], "parameters": parameters(n)}}
        for n in (TOOL_ARGS if names is None else names)
    ]
