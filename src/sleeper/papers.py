"""Linked papers (S2): arXiv papers a repo's README points to, with title, date and citation count.

- ``find_arxiv_ids(text)`` pulls arXiv IDs out of README text: arxiv.org links (abs, pdf, html),
  ``arXiv:2106.09685`` mentions and arXiv DOIs (10.48550/arXiv.2106.09685). Versions are dropped
  and the order of first mention is kept.
- ``PapersClient.lookup(ids)`` makes at most two batch requests:
  - **arXiv's API** for title and first-version date. It is the source of truth, and an ID it
    doesn't know is reported as ``found=False``. Failing here raises PapersUnavailable.
  - **Semantic Scholar** for the citation count, best effort. Its free pool often answers 429
    (set SEMANTIC_SCHOLAR_API_KEY to avoid that); then ``citation_count`` is None and nothing is
    cached, so a later call can fill it in. OpenAlex was tried and rejected: it merges unrelated
    records (wrong titles and dates for well-known papers).
- Each paper's answers are cached on their own (``<cache>/papers``), so repos citing the same
  paper share them. Offline mode (SLEEPER_OFFLINE=1) serves only from the cache, like the GitHub
  layer: an uncached paper raises PapersUnavailable, an uncached citation count is just None.

Typical use::

    from sleeper.papers import PapersClient, linked_papers

    papers = linked_papers(readme_text, PapersClient())
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from sleeper.contracts import LinkedPaper
from sleeper.github import ResponseCache, request_key

log = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "citationCount"
MAX_PAPERS = 10  # per README; a survey list of 200 papers is not evidence about the repo
RETRY_STATUSES = {429, 500, 502, 503, 504}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}

# New-style IDs (0704.0001 onwards): YYMM.NNNN or YYMM.NNNNN. Old style: archive[.SUBJ]/YYMMNNN.
_ID = r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
_PATTERNS = [
    re.compile(r"arxiv\.org/(?:abs|pdf|html)/" + _ID, re.IGNORECASE),
    re.compile(r"\barxiv:\s?" + _ID, re.IGNORECASE),
    re.compile(r"10\.48550/arxiv\." + _ID, re.IGNORECASE),
]
_VERSION = re.compile(r"v\d+$")


class PapersUnavailable(Exception):
    """Papers could not be looked up: offline cache miss, or arXiv unreachable or erroring."""


def find_arxiv_ids(text: str) -> list[str]:
    """arXiv IDs mentioned in ``text``, without versions, in order of first mention."""
    hits: list[tuple[int, str]] = []
    for pattern in _PATTERNS:
        hits += [(m.start(), m.group("id")) for m in pattern.finditer(text)]
    seen: dict[str, None] = {}
    for _, arxiv_id in sorted(hits):
        seen.setdefault(arxiv_id, None)
    return list(seen)


def parse_arxiv_feed(xml: str) -> dict[str, dict]:
    """arXiv API Atom feed -> {arxiv_id: {"title", "published"}}. IDs arXiv doesn't know are absent."""
    out = {}
    for entry in ET.fromstring(xml).findall("a:entry", _ATOM):
        abs_url = entry.findtext("a:id", "", _ATOM)
        if "/abs/" not in abs_url:
            continue  # an error entry (malformed ID) links to /api/errors
        arxiv_id = _VERSION.sub("", abs_url.split("/abs/", 1)[1])
        title = " ".join(entry.findtext("a:title", "", _ATOM).split())  # titles wrap across lines
        out[arxiv_id] = {"title": title, "published": entry.findtext("a:published", "", _ATOM)[:10] or None}
    return out


def _env_offline() -> bool:
    return os.environ.get("SLEEPER_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_cache_dir() -> Path:
    return Path(os.environ.get("SLEEPER_CACHE_DIR") or ".cache") / "papers"


def _arxiv_key(arxiv_id: str) -> str:
    return request_key("GET", f"{ARXIV_API_URL}?id_list={arxiv_id}")


def _s2_key(arxiv_id: str) -> str:
    return request_key("GET", f"{S2_BATCH_URL}/ARXIV:{arxiv_id}", S2_FIELDS)


class PapersClient:
    def __init__(
        self,
        *,
        s2_api_key: str | None = None,
        offline: bool | None = None,
        cache_dir: str | os.PathLike | None = None,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        if s2_api_key is None or offline is None or cache_dir is None:
            load_dotenv(override=False)
        self.s2_api_key = s2_api_key if s2_api_key is not None else os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        self.offline = _env_offline() if offline is None else offline
        self.cache = ResponseCache(cache_dir if cache_dir is not None else _env_cache_dir())
        self.max_attempts = max_attempts
        self.network_requests = 0
        self._sleep = sleep
        self._transport = transport
        self._timeout = timeout

    def lookup(self, arxiv_ids: list[str]) -> list[LinkedPaper]:
        """Papers for ``arxiv_ids``, in the same order. Cached answers never touch the network."""
        meta = {i: self.cache.get(_arxiv_key(i)) for i in arxiv_ids}
        missing = [i for i, entry in meta.items() if entry is None]
        if missing:
            if self.offline:
                raise PapersUnavailable(f"papers not in the offline cache: {', '.join(missing)}")
            feed = parse_arxiv_feed(self._fetch_arxiv(missing))
            for arxiv_id in missing:
                meta[arxiv_id] = {"arxiv_id": arxiv_id, "data": feed.get(arxiv_id)}
                self.cache.put(_arxiv_key(arxiv_id), meta[arxiv_id])

        found = [i for i in arxiv_ids if meta[i]["data"]]
        cites = {i: self.cache.get(_s2_key(i)) for i in found}
        uncited = [i for i, entry in cites.items() if entry is None]
        if uncited and not self.offline:
            cites.update(self._fetch_citations(uncited))

        papers = []
        for i in arxiv_ids:
            data, cite = meta[i]["data"], cites.get(i)
            papers.append(
                LinkedPaper(
                    arxiv_id=i,
                    url=f"https://arxiv.org/abs/{i}",
                    found=data is not None,
                    title=data and data["title"],
                    published=data and data["published"],
                    citation_count=cite and cite["citation_count"],
                    citations_as_of=cite and cite["fetched_at"],
                )
            )
        return papers

    def _fetch_citations(self, arxiv_ids: list[str]) -> dict[str, dict]:
        """Citation counts from Semantic Scholar; {} if it is unreachable (logged, not cached)."""
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}
        body = {"ids": [f"ARXIV:{i}" for i in arxiv_ids]}
        try:
            items = self._send("POST", S2_BATCH_URL, params={"fields": S2_FIELDS}, json=body, headers=headers).json()
        except PapersUnavailable as e:
            log.warning("citation counts unavailable: %s", e)
            return {}
        if not isinstance(items, list) or len(items) != len(arxiv_ids):
            log.warning("citation counts unavailable: Semantic Scholar returned an unexpected batch shape")
            return {}
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out = {}
        for arxiv_id, item in zip(arxiv_ids, items):
            out[arxiv_id] = {"citation_count": item.get("citationCount") if item else None, "fetched_at": fetched_at}
            self.cache.put(_s2_key(arxiv_id), {"arxiv_id": arxiv_id, **out[arxiv_id]})
        return out

    def _fetch_arxiv(self, arxiv_ids: list[str]) -> str:
        params = {"id_list": ",".join(arxiv_ids), "max_results": str(len(arxiv_ids))}
        return self._send("GET", ARXIV_API_URL, params=params).text

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        """One request with retries on 429/5xx and network errors. Raises PapersUnavailable."""
        with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=True) as http:
            for attempt in range(1, self.max_attempts + 1):
                self.network_requests += 1
                try:
                    r = http.request(method, url, **kw)
                except httpx.TransportError as e:
                    error = f"network error from {url}: {e}"
                else:
                    if r.status_code == 200:
                        return r
                    error = f"{url} returned {r.status_code}"
                    if r.status_code not in RETRY_STATUSES:
                        raise PapersUnavailable(f"{error}: {r.text[:200]}")
                if attempt < self.max_attempts:
                    delay = 3.0 * attempt  # arXiv asks for 3 s between calls
                    log.info("%s; retrying in %.0fs", error, delay)
                    self._sleep(delay)
        raise PapersUnavailable(f"{error} after {self.max_attempts} attempts")


def linked_papers(readme: str, client: PapersClient, *, limit: int = MAX_PAPERS) -> list[LinkedPaper]:
    """The first ``limit`` arXiv papers ``readme`` links to, looked up through ``client``."""
    ids = find_arxiv_ids(readme)[:limit]
    return client.lookup(ids) if ids else []
