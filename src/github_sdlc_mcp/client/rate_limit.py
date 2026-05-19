"""GitHub rate-limit accounting and retry classification.

GitHub publishes two distinct kinds of rate limit:

* **Primary** (per-hour quota). Visible on every response via
  ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``. The client uses these to
  *pause proactively* when remaining drops below a configured floor — that's
  cheaper and friendlier than waiting until we get a 403.
* **Secondary** (abuse / concurrency throttling). Not announced ahead of time;
  surfaces as HTTP 403 with a body that mentions "secondary rate limit" and
  usually a ``Retry-After`` header. The client retries these reactively with
  the server-supplied delay.

The helpers in this module are pure functions; the actual sleep loop lives in
``github.py`` so it can be unit-tested with an injected ``sleep`` callable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RateLimitState:
    """Snapshot of GitHub's primary-rate-limit headers from a response.

    Any field may be ``None`` if the response lacked the corresponding header
    (e.g. requests to the GraphQL endpoint that error out before headers are
    populated, or non-API endpoints).
    """

    remaining: int | None
    limit: int | None
    resets_at: datetime | None


EMPTY_RATE_LIMIT_STATE = RateLimitState(remaining=None, limit=None, resets_at=None)


class RateLimitError(Exception):
    """Raised on a 403/429 we should retry.

    Carries the server-supplied retry delay in seconds (or a sane default of
    60s when the header is absent).
    """

    def __init__(self, retry_after: float, message: str = "rate limited") -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientServerError(Exception):
    """5xx response that the client should retry with backoff."""


class GitHubGraphQLError(Exception):
    """GraphQL response returned an ``errors`` array. Not retried."""

    def __init__(self, errors: list[dict[str, object]]) -> None:
        super().__init__(f"GraphQL errors: {errors!r}")
        self.errors = errors


def parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitState:
    """Extract a :class:`RateLimitState` from response headers.

    Tolerant of missing or malformed headers — each field falls back to
    ``None`` rather than raising. That's the right behaviour for a
    cross-cutting accounting concern that shouldn't make request paths fail.
    """
    remaining = _parse_int(headers.get("x-ratelimit-remaining"))
    limit = _parse_int(headers.get("x-ratelimit-limit"))
    reset_epoch = _parse_int(headers.get("x-ratelimit-reset"))
    resets_at = (
        datetime.fromtimestamp(reset_epoch, tz=UTC) if reset_epoch is not None else None
    )
    return RateLimitState(remaining=remaining, limit=limit, resets_at=resets_at)


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_secondary_rate_limit(status: int, body: str) -> bool:
    """True if a 403/429 looks like a secondary rate limit (vs. auth/scope)."""
    if status not in (403, 429):
        return False
    lowered = body.lower()
    return (
        "secondary rate limit" in lowered
        or "abuse" in lowered
        or "rate limit" in lowered
    )


def retry_after_seconds(headers: Mapping[str, str], default: float = 60.0) -> float:
    """Read the ``Retry-After`` header. GitHub sends seconds-as-integer."""
    raw = headers.get("retry-after")
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default
