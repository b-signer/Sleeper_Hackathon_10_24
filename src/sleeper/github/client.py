"""GitHubClient: the one way Sleeper talks to GitHub (REST and GraphQL).

- Uses the token from GITHUB_TOKEN (or the ``token`` argument). A local .env is
  loaded first, without overriding variables already set.
- Every cacheable response (200, 404) is stored on disk and reused; a repeated
  request never touches the network. Pass ``refresh=True`` to refetch.
- Offline mode (SLEEPER_OFFLINE=1) serves only from the cache and raises
  OfflineCacheMiss otherwise. No HTTP client is even created.
- Rate limits (primary and secondary) and transient 5xx/network errors are
  waited out and retried, up to ``max_wait`` seconds in total per request.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
from dotenv import load_dotenv

from .cache import ResponseCache, request_key
from .errors import (
    GitHubError,
    GitHubHTTPError,
    GraphQLError,
    MissingTokenError,
    NotFound,
    OfflineCacheMiss,
    RateLimitExceeded,
)

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
DEFAULT_ACCEPT = "application/vnd.github+json"
STAR_ACCEPT = "application/vnd.github.star+json"  # stargazers with starred_at
RAW_ACCEPT = "application/vnd.github.raw+json"  # README etc. as raw text
CACHEABLE_STATUSES = {200, 404}
KEPT_HEADERS = ("link", "content-type", "etag", "last-modified", "x-ratelimit-resource")


def env_offline() -> bool:
    return os.environ.get("SLEEPER_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def env_cache_dir() -> Path:
    return Path(os.environ.get("SLEEPER_CACHE_DIR") or ".cache") / "github"


def _parse_links(header: str) -> dict[str, str]:
    """Parse a Link header into {rel: url}."""
    links = {}
    for part in header.split(","):
        m = re.match(r'\s*<([^>]*)>\s*;\s*rel="?([^";]+)"?', part)
        if m:
            links[m.group(2)] = m.group(1)
    return links


@dataclass
class GitHubResponse:
    status: int
    url: str
    data: Any  # parsed JSON, raw text (for raw media types) or None
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False

    @property
    def links(self) -> dict[str, str]:
        """Pagination links, e.g. {"next": ..., "last": ...}."""
        return _parse_links(self.headers.get("link", ""))


@dataclass
class Stats:
    network_requests: int = 0
    cache_hits: int = 0
    seconds_waited: float = 0.0


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        offline: bool | None = None,
        cache_dir: str | os.PathLike | None = None,
        base_url: str = API_URL,
        transport: httpx.BaseTransport | None = None,
        max_wait: float = 900.0,
        max_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        timeout: float = 30.0,
    ) -> None:
        if token is None or offline is None or cache_dir is None:
            load_dotenv(override=False)  # same rule as load_settings: real env vars win over .env
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "").strip()
        self.offline = env_offline() if offline is None else offline
        self.cache = ResponseCache(cache_dir if cache_dir is not None else env_cache_dir())
        self.base_url = base_url.rstrip("/")
        self.max_wait = max_wait
        self.max_attempts = max_attempts
        self.stats = Stats()
        # Last seen limits per resource ("core", "search", "graphql"): (remaining, reset epoch).
        self.rate_limits: dict[str, tuple[int, float]] = {}
        self._sleep = sleep
        self._clock = clock
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.Client | None = None

    # ---- public API -------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        accept: str = DEFAULT_ACCEPT,
        refresh: bool = False,
    ) -> GitHubResponse:
        """Make one request, from the cache if possible. Raises on 4xx/5xx."""
        url = self._url(path, params)
        key = request_key(method, url, accept, json_body)
        if not refresh and (entry := self.cache.get(key)) is not None:
            self.stats.cache_hits += 1
            resp = GitHubResponse(
                status=entry["status"],
                url=url,
                data=entry["data"],
                headers=entry.get("headers", {}),
                from_cache=True,
            )
            return self._raise_for_status(resp, method)
        if self.offline:
            raise OfflineCacheMiss(method, url, str(self.cache.dir))
        if not self.token:
            raise MissingTokenError()

        resp = self._send(method, url, accept, json_body)
        if resp.status in CACHEABLE_STATUSES:
            self.cache.put(
                key,
                {
                    "request": {"method": method.upper(), "url": url, "accept": accept, "body": json_body},
                    "status": resp.status,
                    "headers": resp.headers,
                    "data": resp.data,
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
        return self._raise_for_status(resp, method)

    def get(self, path: str, params: dict | None = None, *, accept: str = DEFAULT_ACCEPT, refresh: bool = False) -> Any:
        """REST GET, returning the parsed body."""
        return self.request("GET", path, params=params, accept=accept, refresh=refresh).data

    def paginate(
        self,
        path: str,
        params: dict | None = None,
        *,
        per_page: int = 100,
        max_pages: int | None = None,
        accept: str = DEFAULT_ACCEPT,
    ) -> Iterator[Any]:
        """Yield items across pages by following Link rel="next".

        Works for list endpoints and for search endpoints (whose items are under
        "items"; note GitHub search returns at most 1000 results).
        """
        next_url: str | None = path
        page_params: dict | None = {"per_page": per_page, **(params or {})}
        pages = 0
        while next_url and (max_pages is None or pages < max_pages):
            resp = self.request("GET", next_url, params=page_params, accept=accept)
            pages += 1
            data = resp.data
            yield from (data["items"] if isinstance(data, dict) and "items" in data else data)
            next_url, page_params = resp.links.get("next"), None  # next URL carries the params

    def graphql(
        self,
        query: str,
        variables: dict | None = None,
        *,
        allow_partial: bool = False,
        refresh: bool = False,
    ) -> dict:
        """GraphQL POST, returning the "data" object.

        Raises GraphQLError if the response has errors, unless allow_partial is
        set (useful for batched queries where one aliased repo may be missing).
        """
        body = {"query": query, "variables": variables or {}}
        data = self.request("POST", "/graphql", json_body=body, refresh=refresh).data or {}
        if data.get("errors") and not (allow_partial and data.get("data")):
            raise GraphQLError(data["errors"])
        return data.get("data") or {}

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals --------------------------------------------------------

    def _url(self, path: str, params: dict | None) -> str:
        url = httpx.URL(path if path.startswith("http") else self.base_url + "/" + path.lstrip("/"))
        if params:
            url = url.copy_merge_params({k: str(v) for k, v in params.items() if v is not None})
        return str(url)

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "sleeper-agent",
                },
            )
        return self._http

    @staticmethod
    def _resource_for(url: str) -> str:
        path = httpx.URL(url).path
        if path.startswith("/search/"):
            return "search"
        if path.startswith("/graphql"):
            return "graphql"
        return "core"

    def _send(self, method: str, url: str, accept: str, json_body: Any) -> GitHubResponse:
        """Send with bounded waiting/retrying on rate limits and transient errors."""
        resource = self._resource_for(url)
        waited = 0.0
        reason = ""
        rate_limited = False
        for attempt in range(self.max_attempts):
            # Proactive: if the last response said this resource is exhausted, wait for the reset.
            remaining, reset = self.rate_limits.get(resource, (1, 0.0))
            if remaining <= 0 and reset > self._clock():
                waited = self._wait(reset - self._clock() + 1, waited, f"{resource} rate limit exhausted", url, True)

            try:
                r = self._client().request(method, url, json=json_body, headers={"Accept": accept})
                self.stats.network_requests += 1
            except httpx.TransportError as e:
                delay, reason, rate_limited = min(2.0**attempt, 30.0), f"network error: {e!r}", False
            else:
                self._note_limits(r, resource)
                delay, reason, rate_limited = self._retry_delay(r, attempt)
                if delay is None:
                    return self._to_response(r, url)
            waited = self._wait(delay, waited, reason, url, rate_limited)

        err = RateLimitExceeded if rate_limited else GitHubError
        raise err(f"Giving up on {method} {url} after {self.max_attempts} attempts ({reason}).")

    def _note_limits(self, r: httpx.Response, resource: str) -> None:
        remaining, reset = r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset")
        if remaining is not None and reset is not None:
            name = r.headers.get("x-ratelimit-resource", resource)
            self.rate_limits[name] = (int(remaining), float(reset))
            if name != resource:  # keep our guess-by-path in sync too
                self.rate_limits[resource] = (int(remaining), float(reset))

    def _retry_delay(self, r: httpx.Response, attempt: int) -> tuple[float | None, str, bool]:
        """How long to wait before retrying, or None if the response is final."""
        h = r.headers
        if r.status_code in (403, 429):
            if "retry-after" in h:
                return float(h["retry-after"]), f"secondary rate limit (retry-after {h['retry-after']}s)", True
            if h.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in h:
                return max(float(h["x-ratelimit-reset"]) - self._clock(), 0) + 1, "primary rate limit exhausted", True
            if r.status_code == 429 or "rate limit" in r.text.lower():
                # GitHub: without retry-after, wait at least a minute, backing off.
                return 60.0 * 2**attempt, "secondary rate limit", True
            return None, "", False
        if r.status_code == 200 and self._resource_for(str(r.url)) == "graphql":
            try:
                errors = r.json().get("errors") or []
            except ValueError:
                errors = []
            if any(e.get("type") == "RATE_LIMITED" for e in errors if isinstance(e, dict)):
                reset = float(h.get("x-ratelimit-reset", self._clock() + 60))
                return max(reset - self._clock(), 0) + 1, "GraphQL rate limit", True
        if r.status_code in (500, 502, 503, 504):
            return min(2.0**attempt, 30.0), f"server error {r.status_code}", False
        return None, "", False

    def _wait(self, delay: float, waited: float, reason: str, url: str, rate_limited: bool) -> float:
        if waited + delay > self.max_wait:
            err = RateLimitExceeded if rate_limited else GitHubError
            raise err(
                f"GitHub {reason} on {url}: would need to wait {delay:.0f}s more "
                f"(already waited {waited:.0f}s, max_wait is {self.max_wait:.0f}s). "
                "Try again later or use the cache (SLEEPER_OFFLINE=1)."
            )
        log.warning("GitHub %s; waiting %.0fs before retrying %s", reason, delay, url)
        self._sleep(delay)
        self.stats.seconds_waited += delay
        return waited + delay

    @staticmethod
    def _to_response(r: httpx.Response, url: str) -> GitHubResponse:
        ctype = r.headers.get("content-type", "")
        if not r.content:
            data = None
        elif "json" in ctype and "raw" not in ctype:
            data = r.json()
        else:
            data = r.text
        headers = {k: r.headers[k] for k in KEPT_HEADERS if k in r.headers}
        return GitHubResponse(status=r.status_code, url=url, data=data, headers=headers)

    @staticmethod
    def _raise_for_status(resp: GitHubResponse, method: str) -> GitHubResponse:
        if resp.status < 400:
            return resp
        msg = resp.data.get("message", "") if isinstance(resp.data, dict) else str(resp.data or "")[:200]
        cls = NotFound if resp.status == 404 else GitHubHTTPError
        raise cls(resp.status, method, resp.url, msg)
