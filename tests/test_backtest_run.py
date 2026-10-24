"""S1 end to end on the fake universe through `sleeper backtest`: prepare -> run --fake -> score. Offline."""

from __future__ import annotations

import json
from datetime import date

import pytest

from sleeper.agent import prompt as live
from sleeper.backtest import prompt as bt_prompt
from sleeper.backtest.types import BacktestResult
from sleeper.cli import main
from sleeper.contracts import RunResult, RunSettings
from sleeper.fakes import fake_universe

AS_OF = "2026-08-01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setenv("SLEEPER_RUNS_DIR", str(runs))
    u = fake_universe().model_copy(deep=True)
    current = tmp_path / "current.json"
    current.write_text(u.model_dump_json(), encoding="utf-8")
    history = tmp_path / "stars.csv"  # every repo had half its stars on the date
    history.write_text("repo,date,stars\n" + "".join(f"{r.repo},{AS_OF},{r.metrics.stars // 2}\n" for r in u.rows))
    return tmp_path, runs, current, history


def test_backtest_fake_end_to_end(env, capsys):
    tmp, runs, current, history = env
    out = tmp / "backtest"
    assert main(["backtest", "prepare", "--universe", str(current), "--as-of", AS_OF,
                 "--history", str(history), "--history-kind", "stars", "--out", str(out)]) == 0
    past_path = out / f"fake@{AS_OF}.json"
    assert past_path.is_file() and (out / f"fake@{AS_OF}.report.json").is_file()

    capsys.readouterr()
    assert main(["backtest", "run", "--past", str(past_path), "--fake", "--quiet"]) == 0
    run_id = next(p.name for p in runs.iterdir())
    result = RunResult.model_validate_json((runs / run_id / "result.json").read_text())
    assert result.status == "ok" and result.settings.universe_file == str(past_path)
    past_repos = {r["repo"] for r in json.loads(past_path.read_text())["rows"]}
    assert {c.repo for c in result.cards} <= past_repos  # picks come from the past view only

    assert main(["backtest", "score", run_id, "--current", str(current)]) == 0
    bt = BacktestResult.model_validate_json((runs / run_id / "backtest.json").read_text())
    assert bt.as_of == date.fromisoformat(AS_OF)
    assert {"agent_picks", "universe_average", "momentum_top"} <= {g.name for g in bt.groups}
    assert bt.caveats
    assert (out / f"fake@{AS_OF}.outcomes.json").is_file()  # snapshot reused by later runs

    assert main(["backtest", "score", run_id]) == 0  # second score: no source needed, snapshot reused


def test_score_without_snapshot_or_source_is_refused(env, capsys):
    tmp, runs, current, history = env
    out = tmp / "backtest"
    main(["backtest", "prepare", "--universe", str(current), "--as-of", AS_OF,
          "--history", str(history), "--history-kind", "stars", "--out", str(out)])
    main(["backtest", "run", "--past", str(out / f"fake@{AS_OF}.json"), "--fake", "--quiet"])
    run_id = next(p.name for p in runs.iterdir())
    assert main(["backtest", "score", run_id]) == 2
    assert "no outcome snapshot" in capsys.readouterr().err


def test_backtest_prompt_wraps_whatever_the_live_prompt_is(monkeypatch):
    """T11 owns the live prompt; the backtest must pick up its current text, then add the as-of rules."""
    u = fake_universe()
    s = RunSettings(topic="fake", model="scripted/fake")
    monkeypatch.setattr(live, "system_prompt", lambda settings, header: "TUNED PROMPT")
    text = bt_prompt.backtest_prompt(s, u.header, date(2026, 8, 1))
    assert text.startswith("TUNED PROMPT")
    assert "Today is 2026-08-01" in text


def test_backtest_prompt_custom_base():
    u = fake_universe()
    s = RunSettings(topic="fake", model="scripted/fake")
    text = bt_prompt.backtest_prompt(s, u.header, date(2026, 8, 1), base=lambda st, h: "BASE")
    assert text.startswith("BASE") and "after 2026-08-01" in text
