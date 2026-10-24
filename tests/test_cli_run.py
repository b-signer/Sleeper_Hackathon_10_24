"""`sleeper run` (T10, W2). Offline."""

from __future__ import annotations

import pytest

from sleeper.agent import spend
from sleeper.cli import main
from sleeper.contracts import RunResult, RunSettings, parse_event


@pytest.fixture
def runs(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    monkeypatch.setenv("SLEEPER_RUNS_DIR", str(d))
    return d


def test_cli_run_help(capsys):
    with pytest.raises(SystemExit) as e:
        main(["run", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for opt in (
        "--topic",
        "--universe",
        "--model",
        "--max-picks",
        "--min-ranked",
        "--token-budget",
        "--max-turns",
        "--max-wall-clock",
        "--fake",
        "--quiet",
    ):
        assert opt in out


def test_cli_run_refused_by_spend_guard(runs, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(spend, "fetch_key_usage", lambda key, **kw: 44.99)
    assert main(["run", "--topic", "fake", "--model", "openai/gpt-5.6-terra"]) == 2
    assert "spend guard" in capsys.readouterr().err
    assert not runs.exists() or not any(runs.iterdir())


def test_cli_run_refused_by_web_guard_and_missing_universe(runs, capsys):
    assert main(["run", "--topic", "fake", "--model", "openai/gpt-5.6-luna:online"]) == 2
    assert "web" in capsys.readouterr().err
    assert main(["run", "--topic", "no-such-topic"]) == 2
    assert "no-such-topic" in capsys.readouterr().err
    assert main(["run", "--topic", "fake", "--max-picks", "9"]) == 2
    assert not runs.exists() or not any(runs.iterdir())


def test_cli_run_fake(runs, capsys):
    pytest.importorskip("sleeper.agent.loop")
    assert main(["run", "--topic", "fake", "--fake", "--quiet"]) == 0
    (folder,) = list(runs.iterdir())
    assert RunSettings.model_validate_json((folder / "settings.json").read_text()).model == "scripted/fake"
    events = [parse_event(line) for line in (folder / "events.jsonl").read_text().splitlines()]
    assert events[0].type == "run_started"
    assert events[-1].type == "run_finished" and events[-1].status == "ok"
    result = RunResult.model_validate_json((folder / "result.json").read_text())
    assert result.status == "ok" and len(result.cards) >= 1
    assert str(folder) in capsys.readouterr().out
