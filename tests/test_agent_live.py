"""Live OpenRouter checks (T10, W2). Real network, fractions of a cent. Run with: uv run pytest -m live -k agent"""

from __future__ import annotations

import json

import pytest

from sleeper.agent.openrouter import OpenRouterModel
from sleeper.agent.spend import fetch_key_usage
from sleeper.config import DEFAULT_DEV_MODEL, load_settings

TOOL = {
    "type": "function",
    "function": {
        "name": "repo_details",
        "description": "Metrics for one GitHub repo.",
        "parameters": {
            "type": "object",
            "properties": {"repo": {"type": "string", "description": "owner/name"}},
            "required": ["repo"],
        },
    },
}


def _key() -> str:
    s = load_settings()
    if s.offline or not s.openrouter_api_key:
        pytest.skip("offline or OPENROUTER_API_KEY not set")
    return s.openrouter_api_key


@pytest.mark.live
def test_openrouter_live_tool_call():
    model = OpenRouterModel(DEFAULT_DEV_MODEL, _key())
    try:
        reply = model.complete(
            [
                {"role": "system", "content": "You research GitHub repos using tools only."},
                {"role": "user", "content": "Call repo_details for acme/quiver and for acme/arrow, in parallel."},
            ],
            [TOOL],
            timeout_s=60,
        )
    finally:
        model.close()
    assert len(reply.tool_calls) >= 1
    assert all(c.name == "repo_details" for c in reply.tool_calls)
    assert "repo" in json.loads(reply.tool_calls[0].arguments)
    assert reply.usage.total_tokens > 0
    assert reply.usage.cost_usd > 0
    print(f"\ncalls={len(reply.tool_calls)} usage={reply.usage} reasoning={reply.reasoning!r}")
    print(f"raw_message keys={sorted(reply.raw_message)} finish={reply.finish_reason}")


@pytest.mark.live
def test_fetch_key_usage_live():
    spent = fetch_key_usage(_key())
    assert isinstance(spent, float) and spent >= 0
    print(f"\nkey usage ${spent:.4f}")
