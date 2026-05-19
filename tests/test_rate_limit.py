"""Tests for rate-limit header parsing and 403/429 classification."""

from __future__ import annotations

from datetime import UTC, datetime

from github_sdlc_mcp.client.rate_limit import (
    is_secondary_rate_limit,
    parse_rate_limit_headers,
    retry_after_seconds,
)


def test_parse_full_headers() -> None:
    headers = {
        "x-ratelimit-remaining": "4500",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-reset": "1700000000",
    }
    s = parse_rate_limit_headers(headers)
    assert s.remaining == 4500
    assert s.limit == 5000
    assert s.resets_at == datetime.fromtimestamp(1700000000, tz=UTC)


def test_parse_missing_headers() -> None:
    s = parse_rate_limit_headers({})
    assert s.remaining is None
    assert s.limit is None
    assert s.resets_at is None


def test_parse_malformed_headers_falls_back_to_none() -> None:
    s = parse_rate_limit_headers(
        {
            "x-ratelimit-remaining": "not-a-number",
            "x-ratelimit-limit": "",
            "x-ratelimit-reset": "abc",
        }
    )
    assert s.remaining is None
    assert s.limit is None
    assert s.resets_at is None


def test_is_secondary_rate_limit_true_for_known_phrases() -> None:
    assert is_secondary_rate_limit(403, "You have exceeded a secondary rate limit.")
    assert is_secondary_rate_limit(429, "Abuse detection triggered")
    assert is_secondary_rate_limit(403, "primary rate limit reached")


def test_is_secondary_rate_limit_false_for_other_403s() -> None:
    assert not is_secondary_rate_limit(403, "Bad credentials")
    assert not is_secondary_rate_limit(403, "Resource not accessible by integration")
    assert not is_secondary_rate_limit(200, "rate limit")  # wrong status


def test_retry_after_seconds_with_header() -> None:
    assert retry_after_seconds({"retry-after": "5"}) == 5.0
    assert retry_after_seconds({"retry-after": "0.5"}) == 0.5


def test_retry_after_seconds_default_on_missing() -> None:
    assert retry_after_seconds({}, default=42.0) == 42.0


def test_retry_after_seconds_default_on_malformed() -> None:
    assert retry_after_seconds({"retry-after": "soon"}, default=10.0) == 10.0


def test_retry_after_seconds_clamps_negative() -> None:
    assert retry_after_seconds({"retry-after": "-5"}) == 0.0
