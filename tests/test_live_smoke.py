"""Live smoke test: real network. Skipped by default; run with `uv run pytest -m live`."""

import httpx
import pytest

from sleeper.config import load_settings


@pytest.mark.live
def test_github_api_reachable():
    s = load_settings()
    if s.offline:
        pytest.skip("SLEEPER_OFFLINE=1")
    headers = {"Authorization": f"Bearer {s.github_token}"} if s.github_token else {}
    r = httpx.get("https://api.github.com/rate_limit", headers=headers, timeout=10)
    assert r.status_code == 200
