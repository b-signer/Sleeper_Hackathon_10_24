"""On-disk response cache: one JSON file per request.

Files are named by a SHA-256 of the normalised request (method, URL with sorted
query params, Accept header, JSON body). They hold no secrets and no absolute
paths, so the whole directory can be copied to another machine as is.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx


def request_key(method: str, url: str, accept: str = "", body: Any = None) -> str:
    """Stable hash of a request. Query parameter order does not matter."""
    u = httpx.URL(url)
    canonical = {
        "method": method.upper(),
        "url": str(u.copy_with(query=None, fragment=None)),
        "params": sorted(u.params.multi_items()),
        "accept": accept,
        "body": body,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ResponseCache:
    def __init__(self, directory: str | os.PathLike) -> None:
        self.dir = Path(directory)

    def path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self.path(key)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None  # a corrupt entry is treated as a miss and overwritten

    def put(self, key: str, entry: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        # Write atomically so an interrupted build never leaves half a file.
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path(key))
