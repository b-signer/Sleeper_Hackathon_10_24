"""Run folder, live events.jsonl, result.json, load_universe and execute_run refusals (T10, W2)."""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sleeper.agent import spend
from sleeper.agent.openrouter import WebAccessRefused
from sleeper.agent.records import RunRecorder, new_run_id, read_events
from sleeper.agent.runner import execute_run, load_universe
from sleeper.agent.spend import SpendGuardError
from sleeper.config import load_settings
from sleeper.contracts import Narration, RunResult, RunSettings, StatusEvent, Universe, parse_event
from sleeper.fakes import fake_run, fake_universe

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "fixtures/fake/runs/20261024-140000_fake"
LUNA = "openai/gpt-5.6-luna"


def settings(**kw) -> RunSettings:
    return RunSettings(**{"topic": "fake", "model": LUNA, **kw})


def test_new_run_id():
    now = datetime(2026, 10, 24, 14, 0, 5, tzinfo=UTC)
    assert new_run_id("fake", now) == "20261024-140005_fake"
    assert new_run_id("rust/dev tools", now) == "20261024-140005_rust-dev-tools"


def test_records_layout(tmp_path):
    s = settings()
    a = RunRecorder.create(tmp_path, s)
    assert re.match(r"^\d{8}-\d{6}_fake(-\d+)?$", a.run_id)
    assert a.dir == tmp_path / a.run_id
    assert RunSettings.model_validate_json((a.dir / "settings.json").read_text()) == s
    assert (a.dir / "events.jsonl").exists()
    b = RunRecorder.create(tmp_path, s, run_id=a.run_id)
    c = RunRecorder.create(tmp_path, s, run_id=a.run_id)
    assert (b.run_id, c.run_id) == (f"{a.run_id}-2", f"{a.run_id}-3")
    for r in (a, b, c):
        r.close()


READER = """
import sys
from sleeper.contracts import parse_event
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
print(len([parse_event(line) for line in lines]))
"""


def test_records_live_readable(tmp_path):
    rec = RunRecorder.create(tmp_path, settings())
    for i in range(1, 4):
        e = rec.emit(Narration, turn=i, text=f"turn {i}: checking repos")
        assert e.seq == i - 1 and e.run_id == rec.run_id
        out = subprocess.run(
            [sys.executable, "-c", READER, str(rec.events_path)], capture_output=True, text=True, check=True
        )
        assert int(out.stdout) == i  # another process sees every event so far, each one valid
    rec.close()


def test_records_thread_safe(tmp_path):
    rec = RunRecorder.create(tmp_path, settings())

    def work(k):
        for i in range(50):
            rec.emit(StatusEvent, phase="researching", message=f"worker {k} event {i} " + "x" * 200)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rec.close()
    lines = rec.events_path.read_text().splitlines()
    assert len(lines) == 200
    assert [parse_event(line).seq for line in lines] == list(range(200))


def test_echo_called_and_errors_swallowed(tmp_path):
    seen = []
    rec = RunRecorder.create(tmp_path, settings(), echo=seen.append)
    rec.emit(StatusEvent, phase="starting")
    rec2 = RunRecorder.create(tmp_path, settings(), echo=lambda e: 1 / 0)
    rec2.emit(StatusEvent, phase="starting")  # must not raise
    assert [e.type for e in seen] == ["status"]
    rec.close()
    rec2.close()
    with pytest.raises(RuntimeError):
        rec.emit(StatusEvent, phase="finished")


def test_read_events_recorded_fake_run():
    events = read_events(RECORDED / "events.jsonl")
    n = len((RECORDED / "events.jsonl").read_text().splitlines())
    assert len(events) == n > 0
    assert [e.seq for e in events] == list(range(n))
    assert events[0].type == "run_started" and events[-1].type == "run_finished"


def test_read_events_tolerates_partial_line(tmp_path):
    lines = (RECORDED / "events.jsonl").read_text().splitlines()
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines[:5]) + "\n" + lines[5][:40])
    assert [e.seq for e in read_events(path)] == [0, 1, 2, 3, 4]
    path.write_text("\n".join(lines[:5]))  # complete last line without newline: kept
    assert len(read_events(path)) == 5
    path.write_text(lines[0] + "\n{broken\n" + lines[1] + "\n")  # a bad line in the middle is an error
    with pytest.raises(ValueError):
        read_events(path)


def test_write_result_atomic_and_valid(tmp_path):
    result = fake_run().result
    rec = RunRecorder.create(tmp_path, result.settings)
    path = rec.write_result(result)
    assert path == rec.dir / "result.json"
    assert RunResult.model_validate_json(path.read_text()) == result
    path2 = rec.write_result(result.model_copy(update={"turns": 99}))
    assert RunResult.model_validate_json(path2.read_text()).turns == 99
    assert sorted(p.name for p in rec.dir.iterdir()) == ["events.jsonl", "result.json", "settings.json"]  # no tmp left
    rec.close()


def test_load_universe(tmp_path, monkeypatch):
    u = load_universe(settings())
    assert isinstance(u, Universe) and u.header.topic == "fake"
    custom = fake_universe().model_copy(deep=True)
    custom.header.topic_label = "Custom label"
    f = tmp_path / "u.json"
    f.write_text(custom.model_dump_json())
    assert load_universe(settings(topic="other", universe_file=str(f))).header.topic_label == "Custom label"
    monkeypatch.chdir(tmp_path)  # topic 'fake' still resolves (repo root fallback)
    assert load_universe(settings()).header.topic == "fake"
    with pytest.raises(FileNotFoundError, match="no-such-topic"):
        load_universe(settings(topic="no-such-topic"))
    with pytest.raises(FileNotFoundError):
        load_universe(settings(universe_file=str(tmp_path / "missing.json")))


def test_execute_run_refuses_before_folder(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    cfg = dataclasses.replace(load_settings(), openrouter_api_key="test-key", runs_dir=runs)
    monkeypatch.setattr(spend, "fetch_key_usage", lambda key, **kw: 44.99)
    with pytest.raises(SpendGuardError):
        execute_run(settings(), cfg=cfg)
    with pytest.raises(WebAccessRefused):
        execute_run(settings(model=f"{LUNA}:online"), cfg=cfg)
    with pytest.raises(FileNotFoundError):
        execute_run(settings(topic="no-such-topic"), cfg=cfg)
    assert not runs.exists() or not any(runs.iterdir())


def test_recorded_settings_round_trip():
    s = json.loads((RECORDED / "settings.json").read_text())
    assert RunSettings.model_validate(s).topic == "fake"
