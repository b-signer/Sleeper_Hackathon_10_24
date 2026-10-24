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
        "Screen the universe: filter (AND) and sort by any metric or criterion score, optional keyword/language. "
        "Returns up to `limit` compact rows and the total match count. Cheap: use it to find candidates."
    ),
    "repo_details": "Full metrics and the four criterion scores for one repo in the universe.",
    "readme": "The repo's README text, capped at max_chars (500-12000). Use it to judge what the project does.",
    "submit": (
        "Submit your picks (cards citing call ids), the ranking of every repo you looked at closely, and a summary. "
        "Rejected submissions come back with specific errors; fix them and resubmit. Ends the run when accepted."
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
