"""Spend guard (T10, W2, D10). Offline: respx mocks OpenRouter."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from sleeper.agent.openrouter import OPENROUTER_URL
from sleeper.agent.spend import (
    SPEND_GUARD_USD,
    SpendGuardError,
    check_spend,
    estimate_max_cost,
    fetch_key_usage,
    local_spend,
)
from sleeper.config import load_settings
from sleeper.contracts import RunSettings
from sleeper.fakes import fake_run

LUNA = "openai/gpt-5.6-luna"
TERRA = "openai/gpt-5.6-terra"


def cfg_for(tmp_path, key="test-key"):
    return dataclasses.replace(load_settings(), openrouter_api_key=key, runs_dir=tmp_path)


def test_spend_guard_refuses_past_45(tmp_path):
    s = RunSettings(topic="fake", model=TERRA)
    with pytest.raises(SpendGuardError, match="spend guard"):
        check_spend(s, cfg_for(tmp_path), spent_usd=44.9)
    assert check_spend(s, cfg_for(tmp_path), spent_usd=10) == pytest.approx(35.0)
    assert SPEND_GUARD_USD == 45.0


def test_estimate_max_cost():
    luna = estimate_max_cost(RunSettings(topic="t", model=LUNA))
    terra = estimate_max_cost(RunSettings(topic="t", model=TERRA))
    other = estimate_max_cost(RunSettings(topic="t", model="someone/new-model"))
    assert terra > luna > 0
    assert other == terra
    assert luna == pytest.approx(300_000 * (0.20 + 0.1 * 1.20) / 1e6)


@respx.mock
def test_fetch_key_usage():
    route = respx.get(f"{OPENROUTER_URL}/key").mock(
        return_value=httpx.Response(200, json={"data": {"label": "sk-or-v1-abc...", "usage": 3.25, "limit": 50}})
    )
    assert fetch_key_usage("test-key") == 3.25
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"


def _write_result(runs_dir, run_id, cost):
    r = fake_run().result.model_copy(deep=True)
    r.usage.cost_usd = cost
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "result.json").write_text(r.model_dump_json())


@respx.mock
def test_spend_falls_back_to_local_ledger(tmp_path, caplog):
    respx.get(f"{OPENROUTER_URL}/key").mock(return_value=httpx.Response(500))
    _write_result(tmp_path, "20261024-100000_a", 1.5)
    _write_result(tmp_path, "20261024-110000_b", 2.25)
    (tmp_path / "20261024-120000_c").mkdir()  # in progress: no result.json yet
    assert local_spend(tmp_path) == pytest.approx(3.75)
    headroom = check_spend(RunSettings(topic="fake", model=LUNA), cfg_for(tmp_path))
    assert headroom == pytest.approx(SPEND_GUARD_USD - 3.75)
    assert "local run ledger" in caplog.text


def test_spend_without_key_uses_ledger(tmp_path):
    _write_result(tmp_path, "20261024-100000_a", 44.95)
    with pytest.raises(SpendGuardError):
        check_spend(RunSettings(topic="fake", model=LUNA), cfg_for(tmp_path, key=None))
