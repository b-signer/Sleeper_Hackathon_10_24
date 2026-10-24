"""Errors raised by the GitHub access layer. All subclass GitHubError."""

from __future__ import annotations


class GitHubError(Exception):
    """Base class for every error raised by sleeper.github."""


class MissingTokenError(GitHubError):
    """A network request was needed but no GitHub token is configured."""

    def __init__(self) -> None:
        super().__init__(
            "GITHUB_TOKEN is not set. Create a token at "
            "https://github.com/settings/tokens (no scopes needed for public repos) "
            "and put it in your environment or .env, or set SLEEPER_OFFLINE=1 to "
            "use cached responses only."
        )


class OfflineCacheMiss(GitHubError):
    """Offline mode is on and the request is not in the response cache."""

    def __init__(self, method: str, url: str, cache_dir: str) -> None:
        self.method = method
        self.url = url
        super().__init__(
            f"Offline mode (SLEEPER_OFFLINE) is on and {method} {url} is not in the "
            f"response cache at {cache_dir}. Run once online to fill the cache, or "
            "copy the cache directory from a machine that has it."
        )


class RateLimitExceeded(GitHubError):
    """Rate-limit waiting would exceed the configured maximum total wait."""


class GitHubHTTPError(GitHubError):
    """GitHub answered with an error status that retrying will not fix."""

    def __init__(self, status: int, method: str, url: str, message: str = "") -> None:
        self.status = status
        self.method = method
        self.url = url
        super().__init__(f"GitHub returned {status} for {method} {url}: {message}".rstrip(": "))


class NotFound(GitHubHTTPError):
    """404: the repo or resource does not exist (or is not visible)."""


class GraphQLError(GitHubError):
    """A GraphQL response carried errors."""

    def __init__(self, errors: list) -> None:
        self.errors = errors
        msgs = "; ".join(str(e.get("message", e)) for e in errors)
        super().__init__(f"GitHub GraphQL errors: {msgs}")
