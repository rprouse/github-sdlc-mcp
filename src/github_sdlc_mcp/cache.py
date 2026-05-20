"""TTL cache behind a swappable ``Cache`` protocol.

Phase 7 ships an in-memory implementation; the protocol exists so phase 8's
tool wiring can ``await cache.get(...)`` everywhere, and a future
Postgres-backed cache can drop in without changing any call sites. That's why
this is async even though the in-memory version has no real I/O.

Spec: 15-minute default TTL, keyed by ``(tool_name, sorted_kwargs)``,
scope="org" support for ``clear_cache``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

DEFAULT_TTL_SECONDS = 900.0  # 15 minutes per spec


@runtime_checkable
class Cache(Protocol):
    """Async cache contract. The TTL is an implementation detail of the backend."""

    async def get(self, tool_name: str, kwargs: Mapping[str, Any]) -> Any | None: ...
    async def set(self, tool_name: str, kwargs: Mapping[str, Any], value: Any) -> None: ...
    async def clear(
        self,
        *,
        scope: Literal["all", "org"] = "all",
        org: str | None = None,
    ) -> int: ...
    async def size(self) -> int: ...


# ---------------------------------------------------------------------------
# Key serialization
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> str:
    """Make dates, datetimes, and Paths JSON-serializable for stable keys.

    Anything else raises so silent collisions don't slip in via repr fallback.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(
        f"Cannot include value of type {type(obj).__name__} in a cache key"
    )


def make_cache_key(tool_name: str, kwargs: Mapping[str, Any]) -> tuple[str, str]:
    """``(tool_name, canonical_json)`` — stable across kwarg ordering."""
    canonical = json.dumps(dict(kwargs), sort_keys=True, default=_json_default)
    return tool_name, canonical


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    value: Any
    cached_at: float
    kwargs: dict[str, Any]


class TTLCache:
    """In-memory TTL cache. Single-threaded — relies on asyncio cooperative scheduling.

    No internal ``await`` points, so concurrent coroutines can't interleave
    mutations. If a remote backend is added later (Postgres, Redis), add
    locking around ``set``/``clear`` then.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or time.monotonic
        self._store: dict[tuple[str, str], _Entry] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    async def get(self, tool_name: str, kwargs: Mapping[str, Any]) -> Any | None:
        key = make_cache_key(tool_name, kwargs)
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._clock() - entry.cached_at > self._ttl:
            # Lazy eviction keeps the hot path simple.
            del self._store[key]
            return None
        return entry.value

    async def set(
        self, tool_name: str, kwargs: Mapping[str, Any], value: Any
    ) -> None:
        key = make_cache_key(tool_name, kwargs)
        self._store[key] = _Entry(
            value=value,
            cached_at=self._clock(),
            kwargs=dict(kwargs),
        )

    async def clear(
        self,
        *,
        scope: Literal["all", "org"] = "all",
        org: str | None = None,
    ) -> int:
        if scope == "all":
            count = len(self._store)
            self._store.clear()
            return count
        if scope == "org":
            if not org:
                raise ValueError("scope='org' requires the org parameter")
            to_remove = [
                k for k, entry in self._store.items() if entry.kwargs.get("org") == org
            ]
            for k in to_remove:
                del self._store[k]
            return len(to_remove)
        raise ValueError(f"Unknown clear scope: {scope!r}")

    async def size(self) -> int:
        """Number of non-expired entries.

        O(n) — only called by ``health_check``, which is infrequent. Tightening
        this (e.g. via a heap of (expiry, key)) is a v0.2 concern.
        """
        now = self._clock()
        # Walk and evict expired entries on the way — keeps size() consistent
        # with what subsequent get() calls would see.
        expired = [
            k for k, entry in self._store.items() if now - entry.cached_at > self._ttl
        ]
        for k in expired:
            del self._store[k]
        return len(self._store)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "Cache",
    "TTLCache",
    "make_cache_key",
]
