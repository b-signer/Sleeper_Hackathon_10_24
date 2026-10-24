"""Offline smoke tests for the project skeleton (T01)."""

from pathlib import Path

import pytest

from sleeper.cli import main
from sleeper.config import load_settings

ENV_VARS = [
    "GITHUB_TOKEN",
    "OPENROUTER_API_KEY",
    "SLEEPER_DEV_MODEL",
    "SLEEPER_DEMO_MODEL",
    "SLEEPER_OFFLINE",
    "SLEEPER_CACHE_DIR",
    "SLEEPER_RUNS_DIR",
    "SLEEPER_UNIVERSES_DIR",
]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env picked up
    return monkeypatch


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage: sleeper" in capsys.readouterr().out


def test_cli_no_args_prints_help(capsys):
    assert main([]) == 0
    assert "usage: sleeper" in capsys.readouterr().out


def test_settings_defaults(clean_env):
    s = load_settings(env_file="does-not-exist.env")
    assert s.github_token is None
    assert s.dev_model == "openai/gpt-5.6-luna"
    assert s.demo_model == "openai/gpt-5.6-terra"
    assert s.offline is False
    assert s.cache_dir == Path(".cache")
    assert s.runs_dir == Path("runs")


def test_settings_from_env_file(clean_env, tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("SLEEPER_OFFLINE=1\nGITHUB_TOKEN=abc\nSLEEPER_CACHE_DIR=/tmp/c\n")
    s = load_settings(env_file=env_file)
    assert s.offline is True
    assert s.github_token == "abc"
    assert s.cache_dir == Path("/tmp/c")


def test_env_example_lists_all_vars():
    text = (Path(__file__).parent.parent / ".env.example").read_text()
    for name in ENV_VARS:
        assert f"{name}=" in text
