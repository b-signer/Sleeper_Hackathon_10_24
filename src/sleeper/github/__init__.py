"""GitHub access layer (T05): token, rate limits, disk cache, offline mode.

Typical use::

    from sleeper.github import GitHubClient

    gh = GitHubClient()  # reads GITHUB_TOKEN, SLEEPER_OFFLINE, SLEEPER_CACHE_DIR
    repo = gh.get("/repos/astral-sh/uv")
    for fork in gh.paginate("/repos/o/r/forks", params={"sort": "newest"}, max_pages=2):
        print(fork["created_at"])
    data = gh.graphql("query($o:String!,$n:String!){repository(owner:$o,name:$n){stargazerCount}}",
                      {"o": "astral-sh", "n": "uv"})
"""

from .cache import ResponseCache, request_key
from .client import (
    API_URL,
    DEFAULT_ACCEPT,
    RAW_ACCEPT,
    STAR_ACCEPT,
    GitHubClient,
    GitHubResponse,
    Stats,
)
from .errors import (
    GitHubError,
    GitHubHTTPError,
    GraphQLError,
    MissingTokenError,
    NotFound,
    OfflineCacheMiss,
    RateLimitExceeded,
)

__all__ = [
    "API_URL",
    "DEFAULT_ACCEPT",
    "RAW_ACCEPT",
    "STAR_ACCEPT",
    "GitHubClient",
    "GitHubResponse",
    "Stats",
    "ResponseCache",
    "request_key",
    "GitHubError",
    "GitHubHTTPError",
    "GraphQLError",
    "MissingTokenError",
    "NotFound",
    "OfflineCacheMiss",
    "RateLimitExceeded",
]
