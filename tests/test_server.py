"""T14: the server lists, streams and replays runs, and starts new ones in the background."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sleeper.config import Settings
from sleeper.contracts import RunResult, RunSettings
from sleeper.server import FAKE_MODEL, FakeRunner, RunStore, app_from_settings, create_app
from sleeper.server.store import read_events

ROOT = Path(__file__).resolve().parents[1]
FAKE_DIR = ROOT / "fixtures" / "fake"
FAKE_RUN_ID = "20261024-140000_fake"
FAKE_RUN_DIR = FAKE_DIR / "runs" / FAKE_RUN_ID
SETTINGS = {"topic": "fake", "model": "openai/gpt-5.6-luna"}


def fake_runner(tmp_path: Path, delay_s: float = 0.005) -> FakeRunner:
    return FakeRunner(runs_dir=tmp_path / "runs", source=FAKE_RUN_DIR, delay_s=delay_s)


def make_client(tmp_path: Path, runner=None) -> TestClient:
    store = RunStore(tmp_path / "runs", extra_dirs=[FAKE_DIR / "runs"])
    runner = runner or fake_runner(tmp_path)
    return TestClient(create_app(store, universe_dirs=[FAKE_DIR], runner=runner, start_timeout_s=5))


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


def parse_sse(text: str) -> list[dict[str, str]]:
    messages = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        messages.append(dict(line.split(": ", 1) for line in block.splitlines()))
    return messages


def wait_for(predicate, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


# ---- Listing and reading -----------------------------------------------------


def test_lists_universes(client):
    universes = client.get("/api/universes").json()
    assert [u["topic"] for u in universes] == ["fake"]  # tool_outputs.json is skipped, not a universe
    assert universes[0]["rows"] == 10


def test_lists_the_recorded_run(client):
    (run,) = client.get("/api/runs").json()
    assert run["run_id"] == FAKE_RUN_ID and run["status"] == "ok" and run["cards"] == 3
    assert run["writable"] is False
    assert client.get(f"/api/runs/{FAKE_RUN_ID}").json() == run


def test_returns_the_result(client):
    body = client.get(f"/api/runs/{FAKE_RUN_ID}/result").json()
    assert RunResult.model_validate(body) == RunResult.model_validate_json((FAKE_RUN_DIR / "result.json").read_text())


@pytest.mark.parametrize("run_id", ["nope", "20990101-000000_missing", "..%2F..%2Fetc", "20261024-140000_fake%2F.."])
def test_unknown_or_tricky_run_ids_are_404(client, run_id):
    assert client.get(f"/api/runs/{run_id}/result").status_code == 404


# ---- Streaming and replay ----------------------------------------------------


def test_streams_a_finished_run_then_ends(client):
    messages = parse_sse(client.get(f"/api/runs/{FAKE_RUN_ID}/events").text)
    expected = read_events(FAKE_RUN_DIR / "events.jsonl")
    assert [int(m["id"]) for m in messages[:-1]] == [e.seq for e in expected]
    assert messages[-2]["event"] == "run_finished" and messages[-1]["event"] == "end"


def test_reconnect_resumes_after_last_event_id(client):
    messages = parse_sse(client.get(f"/api/runs/{FAKE_RUN_ID}/events", headers={"Last-Event-ID": "10"}).text)
    assert messages[0]["id"] == "11"


def test_replays_at_a_chosen_speed(client):
    start = time.monotonic()
    instant = parse_sse(client.get(f"/api/runs/{FAKE_RUN_ID}/replay", params={"speed": 0}).text)
    assert time.monotonic() - start < 2
    assert [m["event"] for m in instant] == [e.type for e in read_events(FAKE_RUN_DIR / "events.jsonl")] + ["end"]
    paced = parse_sse(client.get(f"/api/runs/{FAKE_RUN_ID}/replay", params={"speed": 100}).text)
    assert [m.get("id") for m in paced] == [m.get("id") for m in instant]


def test_replay_rejects_bad_speed(client):
    assert client.get(f"/api/runs/{FAKE_RUN_ID}/replay", params={"speed": -1}).status_code == 422


# ---- Starting runs ------------------------------------------------------------


def test_starts_a_run_and_streams_it_live(client):
    res = client.post("/api/runs", json=SETTINGS)
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    assert re.match(r"^\d{8}-\d{6}_fake$", run_id)
    messages = parse_sse(client.get(f"/api/runs/{run_id}/events").text)  # follows the file as the run writes it
    assert messages[-2]["event"] == "run_finished" and messages[-1]["event"] == "end"
    assert all(f'"run_id":"{run_id}"' in m["data"] for m in messages[:-1])
    # `end` means the result is ready: no waiting needed.
    result = RunResult.model_validate(client.get(f"/api/runs/{run_id}/result").json())
    assert result.run_id == run_id and result.status == "ok"
    assert client.get("/api/runs").json()[0]["run_id"] == run_id  # newest first


def test_stream_end_waits_for_the_result(tmp_path):
    def slow_result_runner(settings: RunSettings, on_start) -> None:
        run_dir = tmp_path / "runs" / "20261024-150000_fake"
        run_dir.mkdir(parents=True)
        (run_dir / "settings.json").write_text(settings.model_dump_json())
        on_start(run_dir.name)
        (run_dir / "events.jsonl").write_text((FAKE_RUN_DIR / "events.jsonl").read_text())
        time.sleep(0.5)  # result.json lands well after run_finished
        (run_dir / "result.json").write_text((FAKE_RUN_DIR / "result.json").read_text())

    client = make_client(tmp_path, runner=slow_result_runner)
    run_id = client.post("/api/runs", json=SETTINGS).json()["run_id"]
    assert parse_sse(client.get(f"/api/runs/{run_id}/events").text)[-1]["event"] == "end"
    assert client.get(f"/api/runs/{run_id}/result").status_code == 200


def test_rejects_invalid_settings(client):
    assert client.post("/api/runs", json={"topic": "fake"}).status_code == 422  # no model


def test_one_run_at_a_time(tmp_path):
    release = threading.Event()

    def blocking_runner(settings: RunSettings, on_start) -> None:
        def start_then_block(run_id: str) -> None:
            on_start(run_id)
            release.wait(5)

        fake_runner(tmp_path, delay_s=0)(settings, start_then_block)

    client = make_client(tmp_path, runner=blocking_runner)
    first = client.post("/api/runs", json=SETTINGS).json()["run_id"]
    assert client.get(f"/api/runs/{first}").json()["status"] == "running"
    assert client.get(f"/api/runs/{first}/result").status_code == 409
    assert client.get(f"/api/runs/{first}/replay").status_code == 409
    assert client.post("/api/runs", json=SETTINGS).status_code == 409
    release.set()
    wait_for(lambda: client.get(f"/api/runs/{first}/result").status_code == 200)
    wait_for(lambda: client.post("/api/runs", json=SETTINGS).status_code == 202)


def test_a_refused_run_reports_why_and_leaves_no_folder(tmp_path):
    def refusing_runner(settings: RunSettings, on_start) -> None:
        raise RuntimeError("spend guard: $44.10 spent, run could cost $2.40")

    client = make_client(tmp_path, runner=refusing_runner)
    res = client.post("/api/runs", json=SETTINGS)
    assert res.status_code == 400 and "spend guard" in res.json()["detail"]
    assert not (tmp_path / "runs").exists()
    assert client.post("/api/runs", json=SETTINGS).status_code == 400  # not stuck as "in progress"


def test_a_crashed_run_still_ends_with_a_record(tmp_path):
    def crashing_runner(settings: RunSettings, on_start) -> None:
        def start_then_crash(run_id: str) -> None:
            on_start(run_id)
            raise RuntimeError("model provider down")

        fake_runner(tmp_path)(settings, start_then_crash)

    client = make_client(tmp_path, runner=crashing_runner)
    run_id = client.post("/api/runs", json=SETTINGS).json()["run_id"]
    messages = parse_sse(client.get(f"/api/runs/{run_id}/events").text)
    assert messages[-2]["event"] == "run_finished" and '"status":"error"' in messages[-2]["data"]
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["status"] == "error" and "model provider down" in result["error"]


# ---- Real runs through T10 (app_from_settings) ------------------------------------


def settings_for(tmp_path: Path, offline: bool) -> Settings:
    return Settings(
        github_token=None,
        openrouter_api_key=None,
        dev_model="openai/gpt-5.6-luna",
        demo_model="openai/gpt-5.6-terra",
        offline=offline,
        cache_dir=tmp_path / "cache",
        runs_dir=tmp_path / "runs",
        universes_dir=tmp_path / "universes",
    )


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)  # the fake universe and fake run are found relative to the repo
    return TestClient(app_from_settings(settings_for(tmp_path, offline=True)))


def test_config_lists_models_and_offline(agent_client):
    config = agent_client.get("/api/config").json()
    assert config["offline"] is True and config["default_model"] == FAKE_MODEL
    by_id = {m["id"]: m for m in config["models"]}
    assert by_id[FAKE_MODEL]["offline_ok"] is True
    assert by_id["openai/gpt-5.6-terra"]["input_per_m"] == 2.0


def test_scripted_run_through_t10_end_to_end(agent_client):
    res = agent_client.post("/api/runs", json={"topic": "fake", "model": FAKE_MODEL, "universe_file": "fixtures/fake/universe.json"})
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    messages = parse_sse(agent_client.get(f"/api/runs/{run_id}/events").text)
    assert messages[0]["event"] == "run_started" and messages[-2]["event"] == "run_finished"
    result = RunResult.model_validate(agent_client.get(f"/api/runs/{run_id}/result").json())
    assert result.status == "ok" and 1 <= len(result.cards) <= 5


@pytest.mark.parametrize(
    "body, reason",
    [
        ({"topic": "fake", "model": "openai/gpt-5.6-luna"}, "offline mode"),
        ({"topic": "rust-devtools", "model": FAKE_MODEL}, "only works on the 'fake' universe"),
    ],
)
def test_refused_settings(agent_client, body, reason):
    res = agent_client.post("/api/runs", json=body)
    assert res.status_code == 400 and reason in res.json()["detail"]


# ---- The page -------------------------------------------------------------------


def test_page_and_assets_load_with_no_external_assets(client):
    res = client.get("/")
    assert res.status_code == 200 and "<title>Sleeper</title>" in res.text
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", res.text), "page must work offline (C4)"
    assets = re.findall(r"""(?:src|href)="(/static/[^"]+)\"""", res.text)
    assert "/static/app.js" in assets and "/static/app.css" in assets
    modules = ["/static/util.js", "/static/trace.js", "/static/cards.js"]
    for path in assets + modules:
        body = client.get(path)
        assert body.status_code == 200, path
        assert not re.search(r"""(import[^;]*from\s*["']https?:|url\(\s*["']?https?:|@import)""", body.text), path
