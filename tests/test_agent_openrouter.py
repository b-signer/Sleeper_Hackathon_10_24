"""OpenRouterModel and the no-web guard (T10, W2). Offline: respx mocks every request."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from sleeper.agent import openrouter
from sleeper.agent.openrouter import OPENROUTER_URL, PRICES, OpenRouterModel, WebAccessRefused, assert_no_web
from sleeper.agent.types import ModelError, ModelReply
from sleeper.contracts import Usage

LUNA = "openai/gpt-5.6-luna"
TERRA = "openai/gpt-5.6-terra"
CHAT = f"{OPENROUTER_URL}/chat/completions"
TOOLS = [
    {
        "type": "function",
        "function": {"name": "browse", "description": "List repos.", "parameters": {"type": "object", "properties": {}}},
    }
]
MESSAGES = [{"role": "system", "content": "You research repos."}, {"role": "user", "content": "Go."}]


def canned(**over):
    message = {
        "role": "assistant",
        "content": "Screening by momentum, then checking two repos.",
        "reasoning": "Momentum first; the details tool has the metrics.",
        "reasoning_details": [{"type": "reasoning.summary", "summary": "Momentum first.", "index": 0}],
        "tool_calls": [
            {"id": "call_a", "type": "function", "function": {"name": "browse", "arguments": '{"sort_by": "momentum"}'}},
            {"id": "call_b", "type": "function", "function": {"name": "repo_details", "arguments": '{"repo": "acme/quiver"}'}},
        ],
    }
    body = {
        "id": "gen-1",
        "model": LUNA,
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "cost": 0.000336,
            "prompt_tokens_details": {"cached_tokens": 1024},
        },
    }
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(openrouter, "_sleep", lambda s: None)


def test_no_web_refuses_online_and_search_models():
    for bad in (f"{LUNA}:online", "perplexity/sonar", "perplexity/sonar-pro", "openai/gpt-4o-search-preview"):
        with pytest.raises(WebAccessRefused):
            assert_no_web(bad)
    assert_no_web(LUNA)
    assert_no_web(TERRA)
    with pytest.raises(WebAccessRefused):
        OpenRouterModel("x:online", "k")


@respx.mock
def test_request_body():
    route = respx.post(CHAT).mock(return_value=httpx.Response(200, json=canned()))
    OpenRouterModel(LUNA, "test-key").complete(MESSAGES, TOOLS, tool_choice="required")
    req = route.calls.last.request
    body = json.loads(req.content)
    assert body["model"] == LUNA
    assert body["messages"] == MESSAGES
    assert body["tools"] == TOOLS
    assert body["tool_choice"] == "required"
    assert body["parallel_tool_calls"] is True
    assert body["usage"]["include"] is True
    assert "plugins" not in body and "web_search_options" not in body
    assert req.headers["Authorization"] == "Bearer test-key"


@respx.mock
def test_parses_usage_and_cost():
    respx.post(CHAT).mock(return_value=httpx.Response(200, json=canned()))
    r = OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS)
    assert isinstance(r, ModelReply)
    assert r.content == "Screening by momentum, then checking two repos."
    assert r.reasoning == "Momentum first; the details tool has the metrics."
    assert r.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in r.tool_calls] == [
        ("call_a", "browse", '{"sort_by": "momentum"}'),
        ("call_b", "repo_details", '{"repo": "acme/quiver"}'),
    ]
    assert r.usage == Usage(
        prompt_tokens=1200, completion_tokens=80, cached_tokens=1024, total_tokens=1280, cost_usd=0.000336
    )
    assert r.raw_message["reasoning_details"][0]["summary"] == "Momentum first."  # passed back as is


@respx.mock
def test_reasoning_from_details_and_cost_fallback():
    body = canned()
    msg = body["choices"][0]["message"]
    del msg["reasoning"]
    msg["tool_calls"] = None
    msg["content"] = None
    del body["usage"]["cost"]
    respx.post(CHAT).mock(return_value=httpx.Response(200, json=body))
    r = OpenRouterModel(LUNA, "k").complete(MESSAGES, [])
    assert r.reasoning == "Momentum first."
    assert r.tool_calls == [] and r.content is None
    p_in, p_out = PRICES[LUNA]
    assert r.usage.cost_usd == pytest.approx((1200 * p_in + 80 * p_out) / 1e6)


@respx.mock
def test_retries_429_then_ok():
    route = respx.post(CHAT).mock(
        side_effect=[httpx.Response(429, json={"error": {"message": "slow down"}}), httpx.Response(200, json=canned())]
    )
    r = OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS)
    assert route.call_count == 2
    assert len(r.tool_calls) == 2


@respx.mock
def test_5xx_exhausted_is_http_error():
    route = respx.post(CHAT).mock(return_value=httpx.Response(502, json={"error": {"message": "bad gateway"}}))
    with pytest.raises(ModelError) as e:
        OpenRouterModel(LUNA, "k", max_retries=2).complete(MESSAGES, TOOLS)
    assert e.value.kind == "http"
    assert route.call_count == 3


@respx.mock
def test_network_error_retried():
    route = respx.post(CHAT).mock(side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=canned())])
    OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS)
    assert route.call_count == 2


@respx.mock
def test_read_timeout_is_timeout_error():
    route = respx.post(CHAT).mock(side_effect=httpx.ReadTimeout("stalled"))
    with pytest.raises(ModelError) as e:
        OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS, timeout_s=5)
    assert e.value.kind == "timeout"
    assert route.call_count == 1  # a stalled model is not retried


@respx.mock
def test_200_with_error_body_is_bad_response():
    respx.post(CHAT).mock(return_value=httpx.Response(200, json={"error": {"code": 502, "message": "upstream died"}}))
    with pytest.raises(ModelError) as e:
        OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS)
    assert e.value.kind == "bad_response"
    assert "upstream died" in str(e.value)


@respx.mock
def test_200_without_choices_is_bad_response():
    respx.post(CHAT).mock(return_value=httpx.Response(200, json={"id": "x", "choices": []}))
    with pytest.raises(ModelError) as e:
        OpenRouterModel(LUNA, "k").complete(MESSAGES, TOOLS)
    assert e.value.kind == "bad_response"


@respx.mock
def test_401_is_auth_and_400_is_http():
    respx.post(CHAT).mock(
        side_effect=[httpx.Response(401, json={"error": {"message": "no"}}), httpx.Response(400, text="bad")]
    )
    m = OpenRouterModel(LUNA, "k")
    with pytest.raises(ModelError) as e:
        m.complete(MESSAGES, TOOLS)
    assert e.value.kind == "auth"
    with pytest.raises(ModelError) as e:
        m.complete(MESSAGES, TOOLS)
    assert e.value.kind == "http"


def test_missing_key_is_auth_error():
    for key in (None, ""):
        with pytest.raises(ModelError) as e:
            OpenRouterModel(LUNA, key)
        assert e.value.kind == "auth"


def test_key_not_in_repr():
    assert "secret-key" not in repr(OpenRouterModel(LUNA, "secret-key"))
