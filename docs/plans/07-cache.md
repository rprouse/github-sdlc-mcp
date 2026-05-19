# Phase 7 plan: TTL cache behind a swappable protocol

## Goal

Implement the in-memory TTL cache the spec requires, behind a small
async `Cache` protocol so a future Postgres-backed implementation can
drop in without changing tool code. After this phase, phase 8 can
wrap every metric tool with `await cache.get_or_compute(...)`.

## Files

- `src/github_sdlc_mcp/cache.py` — `Cache` protocol, `TTLCache`
  implementation, `make_cache_key` helper.
- `tests/test_cache.py` — protocol conformance, TTL, key
  canonicalization, scoped clear.

## Public surface

```python
class Cache(Protocol):
    async def get(self, tool_name: str, kwargs: Mapping[str, Any]) -> Any | None: ...
    async def set(self, tool_name: str, kwargs: Mapping[str, Any], value: Any) -> None: ...
    async def clear(
        self,
        *,
        scope: Literal["all", "repo"] = "all",
        repo: str | None = None,
    ) -> int: ...
    async def size(self) -> int: ...

class TTLCache:
    """In-memory cache. v0.1 implementation of Cache."""
    def __init__(
        self,
        *,
        ttl_seconds: float = 900.0,    # 15 minutes — spec default
        clock: Callable[[], float] | None = None,
    ) -> None: ...

def make_cache_key(tool_name: str, kwargs: Mapping[str, Any]) -> tuple[str, str]:
    """Stable (tool_name, canonical_json) key. Sorted keys, date-aware."""
```

The protocol is async because phase 8 will `await` every call site
and we don't want to refactor when a remote backend lands. The v0.1
implementation has no real async work and runs synchronously inside
the awaited methods.

## Semantics

### Keying

`make_cache_key(tool_name, kwargs)` returns `(tool_name, json_str)`
where `json_str` is `json.dumps(kwargs, sort_keys=True, default=...)`.
The default serializer handles:

- `date` → ISO string `"YYYY-MM-DD"`
- `datetime` → ISO string with `Z` suffix
- `Path` → string

Anything else not JSON-native raises `TypeError` — explicit failure
beats silent collisions.

### TTL

`get` returns `None` if no entry exists or the entry is older than
`ttl_seconds`. Expired entries are evicted lazily on access so the
hot path is cheap.

### `size()` semantics

Returns the count of **non-expired** entries. The walk is O(n) but
this is only called by `health_check`, which is infrequent. Document
that contract in the docstring.

### `clear` semantics

- `scope="all"` (default): drop every entry. Returns count cleared.
- `scope="repo"`: drop entries whose kwargs contain
  `repo == <repo>`. `repo` parameter is required; raises
  `ValueError` if missing.

Unknown scope (impossible via the `Literal`, but defensive) raises
`ValueError`.

## Storage layout

```python
@dataclass(frozen=True)
class _Entry:
    value: Any
    cached_at: float
    kwargs: dict[str, Any]   # original kwargs, for scope="repo" filtering
```

`TTLCache._store: dict[tuple[str, str], _Entry]` — keyed by
`make_cache_key()` output. Insertion-ordered (Python dict
guarantee), so a future LRU eviction policy can sort by recency
without restructuring.

## Concurrency

In-memory ops are synchronous and have no `await` points internally,
so single-threaded asyncio doesn't need a lock. Document this. If we
ever add I/O inside `set`, revisit.

## Tests

1. **Round-trip**: set then get returns the value.
2. **Different kwargs → different keys**: `{since: A}` vs `{since: B}`.
3. **Kwarg order independence**: `{since, until}` and `{until, since}`
   produce identical keys.
4. **Date in kwargs serializes stably**: same `date(2026, 5, 1)`
   from two construction paths produces the same key.
5. **Unsupported type raises**: `kwargs={"x": object()}` → TypeError.
6. **Get returns None for unknown key**.
7. **Get returns None after TTL with fake clock**.
8. **Expired entries don't poison subsequent set** (re-set works
   immediately).
9. **`size()` excludes expired entries**.
10. **`clear(scope="all")` returns count and empties**.
11. **`clear(scope="repo", repo="o/r")`** clears only matching entries
    (other-repo and no-repo entries untouched).
12. **`clear(scope="repo")` without `repo`** raises `ValueError`.
13. **`Cache` protocol** — `TTLCache` satisfies it
    (`isinstance(c, Cache)` with `@runtime_checkable`).
14. **Path in kwargs serializes as string**.

## What's NOT in this phase

- Decorator-style usage (`@cached(tool_name=...)`). Phase 8 may want
  explicit `get_or_compute(...)` semantics, so we'll wait and see.
- LRU eviction on capacity. v0.1 unbounded; documented limitation.
- Persistent / Postgres backend. Future v0.2.

## Acceptance

- `tests/test_cache.py` passes.
- All existing tests still pass.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
