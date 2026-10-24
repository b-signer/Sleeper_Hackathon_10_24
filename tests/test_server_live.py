"""T19: live runs from the server (offline tests). Refusals come back as {reason, message}, a live
run streams to `end` and has cards, and the config tells the launcher what a run can cost."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sleeper.agent.runner as agent_runner_mod
import sleeper.agent.spend as spend
from sleeper.config import Settings
from sleeper.contracts import RunResult, RunSettings
from sleeper.server import FakeRunner, app_from_settings, refusal_for
from sleeper.server.runner import RunRefused

ROOT = Path(__file__).resolve().parents[1]
FAKE_RUN_DIR = ROOT / "fixtures" / "fake" / "runs" / "20261024-140000_fake"
LUNA = "openai/gpt-5.6-luna"
LIVE = {"topic": "fake", "model": LUNA, "universe_file": "fixtures/fake/universe.json"}


def parse_sse(text: str) -> list[dict[str, str]]:
    return [dict(line.split(": ", 1) for line in b.splitlines()) for b in text.replace("\r\n", "\n").strip().split("\n\n")]


def online_settings(tmp_path: Path, key: str | None = "sk-test") -> Settings:
    return Settings(
        github_token=None,
        openrouter_api_key=key,
        dev_model=LUNA,
        demo_model="openai/gpt-5.6-terra",
        offline=False,
        cache_dir=tmp_path / "cache",
        runs_dir=tmp_path / "runs",
        universes_dir=tmp_path / "universes",
    )


@pytest.fixture
def online(tmp_path, monkeypatch):
    """An online server with a (fake) key. Nothing here may reach the network."""
    monkeypatch.chdir(ROOT)

    def no_network(*a, **k):
        raise AssertionError("tests must not call OpenRouter")

    monkeypatch.setattr(spend, "fetch_key_usage", no_network)
    cfg = online_settings(tmp_path)
    return cfg, TestClient(app_from_settings(cfg))


def assert_refused(res, reason: str, tmp_path: Path) -> str:
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail["reason"] == reason and detail["message"]
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir()), "no run folder"
    return detail["message"]


# ---- Refusals ------------------------------------------------------------------------


def test_spend_guard_refusal_stubbed(online, tmp_path, monkeypatch):
    def refuse(settings, **kw):
        raise spend.SpendGuardError("spend guard: $44.90 spent + up to $0.96 for this run would exceed $45 (D10)")

    monkeypatch.setattr(agent_runner_mod, "execute_run", refuse)
    _, client = online
    msg = assert_refused(client.post("/api/runs", json=LIVE), "spend_guard", tmp_path)
    assert "$44.90" in msg and "$45" in msg


def test_spend_guard_refusal_through_the_real_execute_run(online, tmp_path, monkeypatch):
    monkeypatch.setattr(spend, "current_spend", lambda cfg: 10.0)
    _, client = online
    body = {**LIVE, "model": "openai/gpt-5.6-terra", "token_budget": 20_000_000}  # worst case $64
    msg = assert_refused(client.post("/api/runs", json=body), "spend_guard", tmp_path)
    assert "$64.00" in msg


def test_offline_refusal(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    cfg = online_settings(tmp_path)
    client = TestClient(app_from_settings(Settings(**{**cfg.__dict__, "offline": True})))
    assert_refused(client.post("/api/runs", json=LIVE), "offline", tmp_path)


def test_missing_universe_refusal(online, tmp_path):
    _, client = online
    msg = assert_refused(client.post("/api/runs", json={"topic": "no-such-topic", "model": LUNA}), "no_universe", tmp_path)
    assert "no-such-topic" in msg


def test_no_api_key_refusal(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    client = TestClient(app_from_settings(online_settings(tmp_path, key=None)))
    assert client.get("/api/config").json()["live_disabled_reason"]
    assert_refused(client.post("/api/runs", json=LIVE), "no_api_key", tmp_path)


def test_web_model_refusal(online, tmp_path):
    _, client = online
    assert_refused(client.post("/api/runs", json={**LIVE, "model": "openai/gpt-5.6-luna:online"}), "web_model", tmp_path)


@pytest.mark.parametrize(
    "error, reason",
    [
        (spend.SpendGuardError("x"), "spend_guard"),
        (RunRefused("x", "offline"), "offline"),
        (RunRefused("x", "made-up"), "other"),
        (FileNotFoundError("x"), "no_universe"),
        (RuntimeError("x"), "other"),
    ],
)
def test_refusal_mapping(error, reason):
    assert refusal_for(error).reason == reason


# ---- A live run ----------------------------------------------------------------------


def test_live_run_streams_to_end_with_cards(online, tmp_path, monkeypatch):
    calls = []

    def stub_execute_run(settings: RunSettings, *, fake: bool, cfg: Settings, on_start, **kw):
        calls.append({"fake": fake, "model": settings.model, "token_budget": settings.token_budget})
        FakeRunner(runs_dir=cfg.runs_dir, source=FAKE_RUN_DIR, delay_s=0.005)(settings, on_start)

    monkeypatch.setattr(agent_runner_mod, "execute_run", stub_execute_run)
    _, client = online
    res = client.post("/api/runs", json={**LIVE, "token_budget": 60_000})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    assert calls == [{"fake": False, "model": LUNA, "token_budget": 60_000}]
    messages = parse_sse(client.get(f"/api/runs/{run_id}/events").text)
    assert messages[0]["event"] == "run_started"
    assert messages[-2]["event"] == "run_finished" and messages[-1]["event"] == "end"
    result = RunResult.model_validate(client.get(f"/api/runs/{run_id}/result").json())
    assert result.status == "ok" and result.cards and result.settings.model == LUNA


# ---- Config and spend ----------------------------------------------------------------


def test_config_online(online):
    _, client = online
    config = client.get("/api/config").json()
    assert config["offline"] is False and config["live_disabled_reason"] is None
    assert config["default_model"] == LUNA and config["spend_guard_usd"] == spend.SPEND_GUARD_USD
    assert config["output_share"] == spend.OUTPUT_SHARE
    priced = [m for m in config["models"] if m["input_per_m"] is not None]
    assert priced and all(m["max_run_usd"] > 0 for m in priced)
    # The launcher's formula gives the same number as the server's guard.
    for m in priced:
        ui = config["default_token_budget"] * (m["input_per_m"] + config["output_share"] * m["output_per_m"]) / 1e6
        assert ui == pytest.approx(spend.estimate_max_cost(RunSettings(topic="x", model=m["id"])))


def test_spend_falls_back_to_the_local_ledger(online):
    _, client = online
    body = client.get("/api/spend").json()
    assert body == {"spent_usd": 0.0, "guard_usd": spend.SPEND_GUARD_USD, "source": "local"}


def test_spend_from_openrouter(online, monkeypatch):
    monkeypatch.setattr(spend, "fetch_key_usage", lambda key: 12.5)
    _, client = online
    assert client.get("/api/spend").json()["source"] == "openrouter"
    assert client.get("/api/spend").json()["spent_usd"] == 12.5
