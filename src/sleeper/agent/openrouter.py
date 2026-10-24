"""OpenRouterModel: the real ChatModel, over OpenRouter's OpenAI-compatible chat completions API.

- Plain httpx, sync. One POST per turn to {OPENROUTER_URL}/chat/completions.
- The request asks for exact usage and cost ("usage": {"include": true}); the reply's
  Usage carries the provider's cost, falling back to PRICES only if cost is missing.
- No web (D12): assert_no_web refuses ":online" and search models, and the request
  body never carries "plugins" or "web_search_options".
- 429, 5xx and network errors are retried (max_retries, short backoff). A read timeout
  is not retried: it becomes ModelError("timeout") at once (a stalled model).
- The API key is sent in the Authorization header only, never logged or recorded.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

from sleeper.agent.types import ModelError, ModelReply, ToolCallRequest
from sleeper.contracts import Usage

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Model id -> (input, output) USD per million tokens (D4, D5). Used for estimates only;
# real cost comes from the provider's usage.cost.
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-5.6-luna": (0.20, 1.20),
    "openai/gpt-5.6-terra": (2.00, 12.00),
}

APP_HEADERS = {"X-Title": "Sleeper"}  # OpenRouter app attribution (optional)
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 520, 522, 524, 529}
CONNECT_TIMEOUT_S = 10.0
MAX_BACKOFF_S = 8.0

# Injectable for tests (monkeypatch), so retries don't actually sleep.
_sleep: Callable[[float], None] = time.sleep


class WebAccessRefused(ValueError):
    """The model id would give the agent web access (D12)."""


def assert_no_web(model_id: str) -> None:
    """Refuse model ids that search the web: ':online' variants, search and Perplexity/Sonar models."""
    mid = model_id.strip().lower()
    if mid.endswith(":online") or "search" in mid or mid.startswith("perplexity/") or "sonar" in mid:
        raise WebAccessRefused(f"model {model_id!r} has web access; Sleeper agents never browse the web (D12)")


def _usage(raw: dict[str, Any] | None, model_id: str) -> Usage:
    raw = raw or {}
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    total = int(raw.get("total_tokens") or (prompt + completion))
    cached = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cost = raw.get("cost")
    if cost is None:
        p_in, p_out = PRICES.get(model_id, PRICES["openai/gpt-5.6-terra"])
        cost = (prompt * p_in + completion * p_out) / 1e6
        log.warning("usage.cost missing from the reply; estimated $%.6f from PRICES", cost)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        total_tokens=total,
        cost_usd=max(float(cost), 0.0),
    )


def _reasoning(message: dict[str, Any]) -> str | None:
    """Reasoning text: message.reasoning, else the summaries/texts in reasoning_details."""
    text = message.get("reasoning")
    if isinstance(text, str) and text.strip():
        return text
    parts: list[str] = []
    for d in message.get("reasoning_details") or []:
        if not isinstance(d, dict):
            continue
        piece = d.get("summary") if d.get("type") == "reasoning.summary" else d.get("text")
        if isinstance(piece, str) and piece.strip():
            parts.append(piece)
    return "\n\n".join(parts) or None


def parse_reply(data: Any, model_id: str) -> ModelReply:
    """Turn a chat completions response body into a ModelReply. Raises ModelError("bad_response")."""
    if not isinstance(data, dict):
        raise ModelError("bad_response", "response body is not a JSON object")
    if data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ModelError("bad_response", f"provider error: {msg}")
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ModelError("bad_response", "response has no choices")
    choice = choices[0]
    if choice.get("error"):
        err = choice["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ModelError("bad_response", f"provider error: {msg}")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelError("bad_response", "choice has no message")
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        calls.append(
            ToolCallRequest(id=str(tc.get("id") or ""), name=str(fn.get("name") or ""), arguments=fn.get("arguments") or "")
        )
    content = message.get("content")
    return ModelReply(
        content=content if isinstance(content, str) else None,
        tool_calls=calls,
        usage=_usage(data.get("usage"), model_id),
        reasoning=_reasoning(message),
        finish_reason=choice.get("finish_reason"),
        raw_message=message,
    )


class OpenRouterModel:
    """ChatModel over OpenRouter. Raises ModelError("auth") at once if there is no API key."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 2,
    ) -> None:
        assert_no_web(model_id)
        if not api_key:
            raise ModelError("auth", "OPENROUTER_API_KEY is not set")
        self.model_id = model_id
        self.max_retries = max_retries
        self._api_key = api_key
        self._own_client = client is None
        self._http = client if client is not None else httpx.Client()

    def __repr__(self) -> str:  # never show the key
        return f"OpenRouterModel({self.model_id!r})"

    def close(self) -> None:
        if self._own_client:
            self._http.close()

    def request_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
            body["parallel_tool_calls"] = True
        body["usage"] = {"include": True}
        assert "plugins" not in body and "web_search_options" not in body
        return body

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str | dict[str, Any] = "auto",
        timeout_s: float = 120.0,
    ) -> ModelReply:
        body = self.request_body(messages, tools, tool_choice)
        headers = {"Authorization": f"Bearer {self._api_key}", **APP_HEADERS}
        timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        url = f"{OPENROUTER_URL}/chat/completions"
        last = ""
        for attempt in range(self.max_retries + 1):
            if attempt:
                _sleep(min(0.5 * 2 ** (attempt - 1), MAX_BACKOFF_S))
            try:
                resp = self._http.post(url, json=body, headers=headers, timeout=timeout)
            except httpx.ReadTimeout as e:
                raise ModelError("timeout", f"no reply from {self.model_id} within {timeout_s:.0f}s") from e
            except httpx.TransportError as e:  # connect errors, resets, write/pool timeouts
                last = f"network error: {type(e).__name__}"
                log.warning("OpenRouter %s (attempt %d)", last, attempt + 1)
                continue
            if resp.status_code in (401, 403):
                raise ModelError("auth", f"OpenRouter refused the key: HTTP {resp.status_code} {_err_text(resp)}")
            if resp.status_code in RETRY_STATUSES:
                last = f"HTTP {resp.status_code} {_err_text(resp)}"
                log.warning("OpenRouter %s (attempt %d)", last, attempt + 1)
                continue
            if resp.status_code >= 400:
                raise ModelError("http", f"HTTP {resp.status_code} {_err_text(resp)}")
            try:
                data = resp.json()
            except ValueError as e:
                raise ModelError("bad_response", "response is not JSON") from e
            return parse_reply(data, self.model_id)
        raise ModelError("http", f"gave up after {self.max_retries + 1} attempts: {last}")


def _err_text(resp: httpx.Response) -> str:
    """Short error text from an error response (OpenRouter puts it in error.message)."""
    try:
        err = resp.json().get("error")
        msg = err.get("message") if isinstance(err, dict) else err
    except Exception:
        msg = resp.text
    return str(msg or "")[:300]
