# Phase 4 plan: GitHub HTTP client

## Goal

Implement the transport layer the metrics code will build on:
PAT-based auth, primary + secondary rate-limit handling, GraphQL and
REST request execution, and cursor-based pagination — all behind an
async API. No business logic, no GraphQL query strings beyond a tiny
healthcheck — the queries belong with their callers (phase 5+).

## Files

- `src/github_sdlc_mcp/client/auth.py` — token resolution from env.
- `src/github_sdlc_mcp/client/rate_limit.py` — header parsing, retry
  policy, custom exception types.
- `src/github_sdlc_mcp/client/github.py` — `GitHubClient` + pool.
- `tests/test_auth.py`, `tests/test_rate_limit.py`, `tests/test_github_client.py`.

## Public surface

```python
# auth.py
def resolve_token(host_cfg: GitHubHost, env: Mapping[str, str] | None = None) -> str:
    """Read the PAT from the env var named by host_cfg.token_env.
    Raises MissingTokenError if unset or empty."""

# rate_limit.py
@dataclass(frozen=True)
class RateLimitState:
    remaining: int | None
    limit: int | None
    resets_at: datetime | None    # tz-aware UTC

class RateLimitError(Exception):
    """Raised on a 403/429 we should retry. Carries retry_after seconds."""
    retry_after: float

class TransientServerError(Exception):
    """5xx; retryable."""

def parse_rate_limit_headers(headers: Mapping[str, str]) -> RateLimitState: ...
def is_secondary_rate_limit(status: int, body: str) -> bool: ...

# github.py
class GitHubClient:
    """Async client for one host. Owns its httpx.AsyncClient."""

    def __init__(
        self,
        host_cfg: GitHubHost,
        token: str,
        *,
        rate_limit_floor: int = 100,
        max_retries: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,  # for respx
        user_agent: str = "github-sdlc-mcp/0.1.0",
    ) -> None: ...

    @property
    def rate_limit(self) -> RateLimitState: ...
    @property
    def last_successful_call_at(self) -> datetime | None: ...

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def paginate_graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        connection_path: Sequence[str],
        cursor_var: str = "cursor",
    ) -> AsyncIterator[dict[str, Any]]: ...
    async def rest_get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...
    async def aclose(self) -> None: ...

class GitHubClientPool:
    """One client per configured host, constructed on first use."""

    def __init__(self, hosts: dict[str, GitHubHost], env: Mapping[str, str] | None = None) -> None: ...
    def get(self, host_key: str) -> GitHubClient: ...
    async def aclose(self) -> None: ...
```

## Behaviour

### Auth

`resolve_token` reads `env[host_cfg.token_env]`. Empty string is
treated as unset. The error message names the env var so the user
knows which one to set; the token value never appears in the error.

### Rate-limit handling — proactive

On every response (success or not), the client parses
`X-RateLimit-Remaining`, `X-RateLimit-Limit`, and `X-RateLimit-Reset`.
Before each request, if the cached `remaining < floor`, the client
sleeps until `resets_at + 1s` (small buffer for skew). The default
floor of 100 leaves room for concurrent tools to share the budget.

### Rate-limit handling — reactive

On 403/429:
- If the response body contains "secondary rate limit" (case-insensitive)
  OR a `Retry-After` header is present → raise `RateLimitError(retry_after=<header value or 60>)`.
- Otherwise → raise the normal HTTP error (auth, scope, etc. are not retryable).

On 5xx: raise `TransientServerError`.

The retry policy wraps every request in a `tenacity` `AsyncRetrying`
that catches `RateLimitError` (waits `retry_after + jitter`) and
`TransientServerError` (exponential backoff with jitter), up to
`max_retries`. After exhaustion, the last exception propagates.

### GraphQL transport

All GraphQL requests `POST {base_url}/graphql` with
`{"query": ..., "variables": ...}`. The client returns the `data`
field on success; if `errors` is present in the response body, raises
`GitHubGraphQLError` carrying the list of errors. Type errors in the
query itself (which come back as 200 + errors) are not retried.

### Pagination

`paginate_graphql` is an async generator. It calls `graphql` repeatedly,
walks `connection_path` to find the connection object, yields every
`node` in the current page, then follows `pageInfo.endCursor` until
`hasNextPage == false`. The cursor variable defaults to `cursor`; the
caller's query is expected to reference `$cursor`.

The caller can early-terminate by `break`ing out of the iteration; the
generator is closed and no further requests are made — this is exactly
what `list_active_repos` needs for PUSHED_AT-descending early stop.

### Connection pooling

Each `GitHubClient` owns one `httpx.AsyncClient` with HTTP/2 disabled
(GitHub Enterprise sometimes misbehaves) and a 30s default timeout.
`GitHubClientPool` constructs one per host on demand.

## Tests

`tests/test_auth.py`:
- Token present in env → returned.
- Token missing → `MissingTokenError` mentions the env var name.
- Token empty string → treated as missing.

`tests/test_rate_limit.py`:
- `parse_rate_limit_headers` extracts remaining / limit / reset.
- Missing headers → all-None state, no exception.
- `is_secondary_rate_limit(403, "... secondary rate limit ...")` → True.
- `is_secondary_rate_limit(403, "... bad credentials ...")` → False.

`tests/test_github_client.py` (using respx + AsyncMock sleep):
- `graphql` sends correct method, URL, headers (Bearer auth, UA), body.
- `graphql` returns `data` and propagates `errors` as `GitHubGraphQLError`.
- `rest_get` sends correct request shape and parses JSON.
- Proactive pause: with cached remaining < floor, client sleeps before
  next request. Assert the sleep callback was awaited with the right
  duration.
- Reactive retry: 1× 403 with `Retry-After: 0.01` then 200 → succeeds,
  sleep callback awaited once with ~0.01s.
- Reactive retry: 2× 502 then 200 → succeeds, retry count == 2.
- Retry exhaustion: persistent 502 → after `max_retries`, raises.
- Pagination: 3-page response stitched into a list of 7 nodes.
- Pagination early-termination: caller breaks after 2 nodes, only 1
  request issued; respx assertion confirms no second call.
- `aclose` closes the httpx client (subsequent calls raise).

## What's NOT in this phase

- GraphQL query strings for PRs / repos / commits — phase 5 (normalizer)
  and phase 6 (metrics) own those.
- The active-repo / merge-detection algorithms — phase 6/7.
- Caching of API responses — separate from rate-limit caching; lands
  alongside the FastMCP tool wiring in phase 8.
- GitHub App auth — out of scope per spec v2 §"Authentication".

## Acceptance

- All new tests pass; existing tests still pass.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
- `client/__init__.py` re-exports `GitHubClient`, `GitHubClientPool`,
  `resolve_token`, and the relevant exception types.
