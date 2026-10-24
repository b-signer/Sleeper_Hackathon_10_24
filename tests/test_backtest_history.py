"""Tests for the S1 star histories. Offline."""

from __future__ import annotations

from datetime import date

import pytest

from sleeper import fakes
from sleeper.backtest.history import (
    CsvStarHistory,
    EventCountHistory,
    FixtureHistory,
    gharchive_bigquery_sql,
)
from sleeper.backtest.types import StarHistory


def write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_csv_history_latest_point_on_or_before(tmp_path):
    p = write(
        tmp_path,
        "stars.csv",
        "repo,date,stars\nAcme/Quiver,2026-03-01,100\nacme/quiver,2026-05-01,180\nlumen-dev/tidepool,2026-04-15,40\n",
    )
    h = CsvStarHistory(p)
    _: StarHistory = h  # satisfies the protocol (checked by type checkers)
    assert h.name == f"csv:{p.as_posix()}"
    assert h.stars_at("acme/quiver", date(2026, 2, 28)) is None  # before the first point
    assert h.stars_at("ACME/QUIVER", date(2026, 3, 1)) == 100
    assert h.stars_at("acme/quiver", date(2026, 4, 30)) == 100
    assert h.stars_at("acme/quiver", date(2026, 5, 1)) == 180
    assert h.stars_at("acme/quiver", date(2027, 1, 1)) == 180
    assert h.stars_at("lumen-dev/tidepool", date(2026, 4, 15)) == 40
    assert h.stars_at("nobody/nothing", date(2026, 5, 1)) is None


def test_csv_history_rejects_bad_files(tmp_path):
    with pytest.raises(ValueError, match="missing column"):
        CsvStarHistory(write(tmp_path, "a.csv", "repo,stars\nacme/quiver,1\n"))
    with pytest.raises(ValueError, match="bad row"):
        CsvStarHistory(write(tmp_path, "b.csv", "repo,date,stars\nacme/quiver,May 1,1\n"))
    with pytest.raises(ValueError, match="negative"):
        CsvStarHistory(write(tmp_path, "c.csv", "repo,date,stars\nacme/quiver,2026-05-01,-1\n"))


def test_event_count_history(tmp_path):
    p = write(tmp_path, "ev.csv", "repo,watch_events_since\nacme/quiver,140\nbigco/turbo-lint,5000\n")
    as_of = date(2026, 8, 1)
    h = EventCountHistory(p, as_of, {"Acme/Quiver": 340, "bigco/turbo-lint": 1950, "oldmill/fmt-lite": 910})
    _: StarHistory = h  # satisfies the protocol (checked by type checkers)
    assert h.name.startswith("gharchive-watch-events:") and h.name.endswith("@2026-08-01")
    assert h.stars_at("ACME/quiver", as_of) == 200
    assert h.stars_at("bigco/turbo-lint", as_of) == 0  # more events than stars now (unstars): floored at 0
    assert h.stars_at("oldmill/fmt-lite", as_of) == 910  # no events since: unchanged
    assert h.stars_at("acme/quiver", date(2026, 8, 2)) is None  # only the exported date is known
    assert h.stars_at("nobody/nothing", as_of) is None
    assert "unstar" in EventCountHistory.CAVEAT


def test_event_count_history_from_universe(tmp_path):
    p = write(tmp_path, "ev.csv", "repo,watch_events_since\nacme/quiver,40\n")
    h = EventCountHistory.from_universe(p, date(2026, 8, 1), fakes.fake_universe())
    assert h.stars_at("acme/quiver", date(2026, 8, 1)) == 300


def test_fixture_history():
    h = FixtureHistory({"Acme/Quiver": 10, "x/y": {date(2026, 1, 1): 5, date(2026, 3, 1): 9}})
    _: StarHistory = h
    assert h.name == "fixture"
    assert h.stars_at("acme/quiver", date(2020, 1, 1)) == 10
    assert h.stars_at("x/y", date(2025, 12, 31)) is None
    assert h.stars_at("x/y", date(2026, 2, 1)) == 5
    assert h.stars_at("X/Y", date(2026, 3, 1)) == 9
    assert h.stars_at("a/b", date(2026, 3, 1)) is None


def test_bigquery_sql_shape():
    sql = gharchive_bigquery_sql(["Acme/Quiver", "lumen-dev/tidepool", "acme/quiver"], date(2026, 8, 1), date(2026, 10, 24))
    assert "FROM `githubarchive.day.20*`" in sql
    assert "_TABLE_SUFFIX > '260801'" in sql
    assert "_TABLE_SUFFIX <= '261024'" in sql
    assert "type = 'WatchEvent'" in sql
    assert "AS watch_events_since" in sql and "AS repo" in sql
    assert sql.count("'acme/quiver'") == 1 and "'lumen-dev/tidepool'" in sql  # deduped, lower-cased
    assert "GROUP BY repo" in sql


def test_bigquery_sql_open_ended_and_validation():
    sql = gharchive_bigquery_sql(["acme/quiver"], date(2026, 8, 1))
    assert "CURRENT_DATE()" in sql
    with pytest.raises(ValueError):
        gharchive_bigquery_sql([], date(2026, 8, 1))
    with pytest.raises(ValueError):
        gharchive_bigquery_sql(["acme/quiver'); DROP TABLE x; --"], date(2026, 8, 1))
    with pytest.raises(ValueError):
        gharchive_bigquery_sql(["acme/quiver"], date(2026, 8, 1), date(2026, 8, 1))
