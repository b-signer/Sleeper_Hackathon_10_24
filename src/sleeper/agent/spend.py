"""Spend guard (D10): refuse a run that could push total OpenRouter spend past SPEND_GUARD_USD.

Spent so far comes from OpenRouter (GET /key -> data.usage, USD, lifetime for this key).
If that call fails, the local ledger (sum of runs/*/result.json usage.cost_usd) is used
instead, with a warning. The run's worst case is estimated from its token budget.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from sleeper.agent.openrouter import OPENROUTER_URL, PRICES
from sleeper.config import Settings
from sleeper.contracts import RunSettings

log = logging.getLogger(__name__)

SPEND_GUARD_USD = 45.0
OUTPUT_SHARE = 0.10  # estimate: ~10% of the token budget is output tokens
FALLBACK_MODEL = "openai/gpt-5.6-terra"  # unknown models are priced like the dearest known one


class SpendGuardError(RuntimeError):
    """The run would push total spend past the guard."""


def fetch_key_usage(api_key: str, *, client: httpx.Client | None = None) -> float:
    """USD spent so far on this OpenRouter key. Raises on any failure."""
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{OPENROUTER_URL}/key"
    if client is None:
        with httpx.Client() as c:
            resp = c.get(url, headers=headers, timeout=10.0)
    else:
        resp = client.get(url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    return float(resp.json()["data"]["usage"])


def local_spend(runs_dir: Path) -> float:
    """Sum of usage.cost_usd over runs_dir/*/result.json (unreadable files are skipped)."""
    total = 0.0
    for path in Path(runs_dir).glob("*/result.json"):
        try:
            total += float(json.loads(path.read_text())["usage"]["cost_usd"])
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("spend ledger: skipping unreadable %s", path)
    return total


def estimate_max_cost(settings: RunSettings) -> float:
    """Worst case for one run: the whole token budget at the input price, plus ~10% at the output price."""
    p_in, p_out = PRICES.get(settings.model, PRICES[FALLBACK_MODEL])
    return settings.token_budget * (p_in + OUTPUT_SHARE * p_out) / 1e6


def current_spend(cfg: Settings) -> float:
    """OpenRouter's figure if reachable, else the local ledger."""
    if cfg.openrouter_api_key:
        try:
            return fetch_key_usage(cfg.openrouter_api_key)
        except Exception as e:  # network, HTTP, shape: fall back, never block on the guard itself
            log.warning("could not read spend from OpenRouter (%s); using the local run ledger", type(e).__name__)
    else:
        log.warning("OPENROUTER_API_KEY not set; using the local run ledger for spend")
    return local_spend(cfg.runs_dir)


def check_spend(settings: RunSettings, cfg: Settings, *, spent_usd: float | None = None) -> float:
    """Returns headroom_usd = SPEND_GUARD_USD - spent. Raises SpendGuardError if spent + estimate > guard."""
    spent = current_spend(cfg) if spent_usd is None else spent_usd
    estimate = estimate_max_cost(settings)
    if spent + estimate > SPEND_GUARD_USD:
        raise SpendGuardError(
            f"spend guard: ${spent:.2f} spent + up to ${estimate:.2f} for this run "
            f"would exceed ${SPEND_GUARD_USD:.0f} (D10)"
        )
    return SPEND_GUARD_USD - spent
