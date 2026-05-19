"""Tests for the in-memory TTL cache and the cache-key serializer."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from github_sdlc_mcp.cache import (
    DEFAULT_TTL_SECONDS,
    Cache,
    TTLCache,
    make_cache_key,
)


class _Clock:
    """Test clock — t advances explicitly via .advance()."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Key canonicalization
# ---------------------------------------------------------------------------


def test_key_is_stable_across_kwarg_order() -> None:
    k1 = make_cache_key("get_x", {"since": date(2026, 5, 1), "until": date(2026, 5, 15)})
    k2 = make_cache_key("get_x", {"until": date(2026, 5, 15), "since": date(2026, 5, 1)})
    assert k1 == k2


def test_different_kwargs_produce_different_keys() -> None:
    k1 = make_cache_key("get_x", {"repo": "a/b"})
    k2 = make_cache_key("get_x", {"repo": "a/c"})
    assert k1 != k2


def test_different_tool_names_produce_different_keys() -> None:
    k1 = make_cache_key("get_cycle", {"repo": "a/b"})
    k2 = make_cache_key("get_review", {"repo": "a/b"})
    assert k1 != k2


def test_date_serializes_stably() -> None:
    # Same date constructed two ways → identical key string.
    k1 = make_cache_key("t", {"d": date(2026, 5, 1)})
    k2 = make_cache_key("t", {"d": date.fromisoformat("2026-05-01")})
    assert k1 == k2
    assert "2026-05-01" in k1[1]


def test_datetime_serializes_stably() -> None:
    dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    k = make_cache_key("t", {"at": dt})
    assert "2026-05-01" in k[1]


def test_path_serializes_as_string() -> None:
    """Same Path constructed two ways must yield the same key string."""
    k1 = make_cache_key("t", {"p": Path("/tmp/x")})
    k2 = make_cache_key("t", {"p": Path("/tmp/x")})
    assert k1 == k2
    # The serialized form contains the platform-appropriate string repr.
    assert str(Path("/tmp/x")) in k1[1].replace("\\\\", "\\")


def test_unsupported_type_raises() -> None:
    with pytest.raises(TypeError):
        make_cache_key("t", {"o": object()})


# ---------------------------------------------------------------------------
# Basic set/get round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_then_get_returns_value() -> None:
    cache = TTLCache()
    await cache.set("get_x", {"repo": "a/b"}, "the answer")
    assert await cache.get("get_x", {"repo": "a/b"}) == "the answer"


@pytest.mark.asyncio
async def test_get_unknown_key_returns_none() -> None:
    cache = TTLCache()
    assert await cache.get("get_x", {"repo": "missing"}) is None


@pytest.mark.asyncio
async def test_set_overwrites_existing_entry() -> None:
    cache = TTLCache()
    await cache.set("get_x", {"repo": "a/b"}, "v1")
    await cache.set("get_x", {"repo": "a/b"}, "v2")
    assert await cache.get("get_x", {"repo": "a/b"}) == "v2"


@pytest.mark.asyncio
async def test_kwarg_order_does_not_affect_lookup() -> None:
    cache = TTLCache()
    await cache.set("get_x", {"since": date(2026, 5, 1), "until": date(2026, 5, 15)}, "v")
    assert (
        await cache.get(
            "get_x",
            {"until": date(2026, 5, 15), "since": date(2026, 5, 1)},
        )
        == "v"
    )


# ---------------------------------------------------------------------------
# TTL behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_none_after_ttl_expires() -> None:
    clock = _Clock()
    cache = TTLCache(ttl_seconds=60.0, clock=clock)
    await cache.set("t", {"k": 1}, "v")
    clock.advance(61.0)
    assert await cache.get("t", {"k": 1}) is None


@pytest.mark.asyncio
async def test_get_returns_value_within_ttl() -> None:
    clock = _Clock()
    cache = TTLCache(ttl_seconds=60.0, clock=clock)
    await cache.set("t", {"k": 1}, "v")
    clock.advance(59.0)
    assert await cache.get("t", {"k": 1}) == "v"


@pytest.mark.asyncio
async def test_expired_entry_can_be_reset() -> None:
    """An expired entry shouldn't poison subsequent set() of the same key."""
    clock = _Clock()
    cache = TTLCache(ttl_seconds=60.0, clock=clock)
    await cache.set("t", {"k": 1}, "old")
    clock.advance(120.0)
    # Trigger eviction.
    assert await cache.get("t", {"k": 1}) is None
    # New write must land.
    await cache.set("t", {"k": 1}, "new")
    assert await cache.get("t", {"k": 1}) == "new"


# ---------------------------------------------------------------------------
# size()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_size_counts_only_non_expired_entries() -> None:
    clock = _Clock()
    cache = TTLCache(ttl_seconds=60.0, clock=clock)
    await cache.set("t", {"k": 1}, "v1")
    await cache.set("t", {"k": 2}, "v2")
    assert await cache.size() == 2
    clock.advance(61.0)
    assert await cache.size() == 0


@pytest.mark.asyncio
async def test_size_empty_cache_is_zero() -> None:
    assert await TTLCache().size() == 0


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_all_empties_and_returns_count() -> None:
    cache = TTLCache()
    await cache.set("t", {"k": 1}, "v1")
    await cache.set("t", {"k": 2}, "v2")
    cleared = await cache.clear(scope="all")
    assert cleared == 2
    assert await cache.size() == 0


@pytest.mark.asyncio
async def test_clear_repo_scoped_only_drops_matching_entries() -> None:
    cache = TTLCache()
    await cache.set("get_cycle", {"repo": "o/a"}, "ra")
    await cache.set("get_cycle", {"repo": "o/b"}, "rb")
    await cache.set("get_active", {"since": date(2026, 5, 1)}, "no-repo")

    cleared = await cache.clear(scope="repo", repo="o/a")

    assert cleared == 1
    assert await cache.get("get_cycle", {"repo": "o/a"}) is None
    assert await cache.get("get_cycle", {"repo": "o/b"}) == "rb"
    assert await cache.get("get_active", {"since": date(2026, 5, 1)}) == "no-repo"


@pytest.mark.asyncio
async def test_clear_repo_requires_repo_arg() -> None:
    cache = TTLCache()
    with pytest.raises(ValueError):
        await cache.clear(scope="repo")


@pytest.mark.asyncio
async def test_clear_repo_with_unmatched_value_returns_zero() -> None:
    cache = TTLCache()
    await cache.set("get_cycle", {"repo": "o/a"}, "ra")
    cleared = await cache.clear(scope="repo", repo="o/does-not-exist")
    assert cleared == 0
    assert await cache.size() == 1


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_ttl_cache_satisfies_cache_protocol() -> None:
    cache = TTLCache()
    assert isinstance(cache, Cache)


def test_default_ttl_is_15_minutes() -> None:
    assert DEFAULT_TTL_SECONDS == 900.0
    assert TTLCache().ttl_seconds == 900.0
