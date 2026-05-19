# Phase 8 plan: FastMCP server + tool wiring + active-repo walker

## Goal

Wire every layer built so far into a runnable FastMCP server. Implement
the active-repo walker (the spec's priority — every other metric tool
depends on it for efficient scoping). Register all tools from spec v2
§"Tool surface" — fully implemented for cycle_time and review_health,
stubs (returning ``NotImplementedError`` at call time) for the rest.

After this phase, ``uv run github-sdlc-mcp`` starts a working MCP server
and ``claude mcp add github-sdlc -- uvx github-sdlc-mcp`` is a documented
install path.

## Files

- `src/github_sdlc_mcp/active_repos.py` — GraphQL queries and the
  `discover_active_repos()` walker. Lives separately because the
  early-termination logic is intricate and deserves its own tests.
- `src/github_sdlc_mcp/server.py` — `ServerContext`, `build_context`,
  `build_server`, and the tool-implementation functions.
- `src/github_sdlc_mcp/__main__.py` — wire `_cmd_run_server` to actually
  start the FastMCP transport.
- `tests/test_active_repos.py` — walker tests, with the early-
  termination assertion the spec specifically called out.
- `tests/test_server_tools.py` — integration tests covering every
  registered tool (mocked via respx).
- `.github/workflows/ci.yml` — ubuntu × Python 3.12, uv sync, ruff,
  mypy, pytest --cov.
- `README.md` — full rewrite covering install, config, tool reference,
  sibling-servers note, troubleshooting.

## ServerContext

```python
@dataclass
class ServerContext:
    config: AppConfig
    cache: Cache
    pool: GitHubClientPool
    config_path: Path | None
    config_searched_paths: list[Path]
    config_status: Literal["loaded", "not_found", "error"]
    config_error: str | None
    env: Mapping[str, str] | None  # for fast-approval threshold reads

def build_context(
    resolved: ResolvedConfig,
    *,
    cache: Cache | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,  # respx injection point
) -> ServerContext: ...
```

The `transport` arg is the test seam — respx tests pass a mock
transport that the pool plumbs into every per-host client.

## Tool wiring pattern

Every tool is registered like this:

```python
def build_server(ctx: ServerContext) -> FastMCP:
    mcp = FastMCP("github-sdlc-mcp")

    @mcp.tool()
    async def list_configured_repos() -> dict:
        result = await list_configured_repos_impl(ctx)
        return result.model_dump(mode="json")

    # ... etc
    return mcp
```

Tools return JSON-mode dicts because FastMCP serializes those cleanly
across the protocol. Pydantic models support `model_dump(mode="json")`
which converts dates / paths / enums to JSON-safe primitives.

Each `*_impl` async function takes `ctx` as the first arg, plus the
tool's own keyword args. They're top-level (not closures) so tests
import and call them directly with `await fn(ctx, ...)`.

## Caching helper

```python
async def cached_call(
    ctx: ServerContext,
    tool_name: str,
    kwargs: Mapping[str, Any],
    compute: Callable[[], Awaitable[T]],
) -> T:
    hit = await ctx.cache.get(tool_name, kwargs)
    if hit is not None:
        return cast(T, hit)
    value = await compute()
    await ctx.cache.set(tool_name, kwargs, value)
    return value
```

Used by every metric tool. Cache keys are `(tool_name, kwargs)`.
For metric tools, kwargs include `repo`, `since`, `until` — so
`clear_cache(scope="repo")` correctly drops only that repo's entries.

## Active-repo walker (`active_repos.py`)

```python
ORG_REPOS_QUERY = """ ... organization.repositories ordered by PUSHED_AT DESC ... """
REPO_RECENT_MERGED_QUERY = """ ... pullRequests states=[MERGED] ordered by UPDATED_AT DESC ... """

@dataclass(frozen=True)
class DiscoveredRepo:
    owner: str
    repo: str
    pushed_at: datetime
    default_branch: str
    has_merges_in_window: bool

async def discover_active_repos(
    client: GitHubClient,
    *,
    org: str,
    since: date,
    until: date,
) -> tuple[list[DiscoveredRepo], int]:
    """Returns (repos_active_in_window, total_repos_scanned).

    Early-terminates when a page's oldest pushed_at falls before `since`.
    Per spec v2: do NOT iterate all repos. For each surviving repo,
    issues one more query to determine has_merges_in_window.
    """
```

Implementation outline:

```python
since_dt, until_dt = window_bounds(since, until)
scanned = 0
active: list[DiscoveredRepo] = []
async for node in client.paginate_graphql(
    ORG_REPOS_QUERY,
    {"org": org},
    connection_path=["organization", "repositories"],
):
    scanned += 1
    pushed_at = parse_dt(node["pushedAt"])
    if pushed_at < since_dt:
        break  # this and all later pages are older
    default_branch = (node.get("defaultBranchRef") or {}).get("name") or "main"
    has_merges = await _has_merges_in_window(
        client, owner=org, repo=node["name"],
        default_branch=default_branch, since_dt=since_dt, until_dt=until_dt,
    )
    active.append(DiscoveredRepo(
        owner=org, repo=node["name"], pushed_at=pushed_at,
        default_branch=default_branch, has_merges_in_window=has_merges,
    ))
return active, scanned
```

`_has_merges_in_window` paginates merged PRs by UPDATED_AT DESC and
returns True as soon as one matches (merged in window AND base ==
default_branch). Terminates early when a page's PRs all have
updatedAt < since.

## Tool inventory

### Discovery / config

- `list_configured_repos() -> ConfiguredRepoList` — pure config read,
  no HTTP. Not cached (instant). Returns `repos=[]` if config not loaded.
- `list_active_repos(*, since, until, host=None, org=None) -> ActiveRepoList`
  — calls `discover_active_repos` per relevant host/org combo,
  intersects with configured repos, assembles totals. Cached (15-min TTL).
- `health_check() -> HealthStatus` — aggregates rate-limit state across
  active pool clients (min remaining, latest reset), reads
  `cache.size()`, reports config status.

### Per-repo metrics (cached by repo+since+until)

- `get_pr_cycle_time_stats(*, repo, since, until) -> PRCycleTimeStats` —
  load PRs via `_load_prs_for_repo(ctx, repo, since, until)`, run
  `compute_cycle_time_stats`. Fully implemented.
- `get_review_health(*, repo, since, until) -> ReviewHealthStats` — same
  pattern. Reads fast-approval thresholds from env (with defaults).

### Stubs (registered, raise on call)

- `get_pr_size_stats(*, repo, since, until)`
- `get_ci_health(*, repo, since, until)`
- `get_stale_prs(*, repo, threshold_days=14, as_of=None)`
- `get_merge_activity(*, repo, since, until)`
- `get_portfolio_summary(*, since, until, company_filter=None, include_inactive=False)`
- `compare_to_baseline(*, repo, metric, current_window_days=30, baseline_window_days=90)`

### Cache management

- `clear_cache(*, scope="all", repo=None) -> CacheClearResult`

## `_load_prs_for_repo`

Shared helper that:

1. Validates `owner/repo` is in the configured list (raises a typed
   error otherwise).
2. Resolves the host from config, gets the client from the pool.
3. Paginates `PR_PAGE_QUERY` for the repo, early-terminating when a
   page's PRs all have `updatedAt < since`.
4. Normalizes each PR via `normalize_pr`.
5. Returns `list[NormalizedPR]`.

The metric tools handle window-filtering inside their math; we don't
filter here so the loaded list is reusable across metrics that might
share a cache key in the future.

## `__main__.py` server start

Replace the phase-2 placeholder with:

```python
def _cmd_run_server(cli_config, transport, port):
    resolved = resolve_config(cli_path=cli_config)
    ctx = build_context(resolved)
    server = build_server(ctx)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", port=port)
```

stdio MCP requires all logs go to stderr; we already configured that
in phase 2's `_configure_logging`.

## Tests

`tests/test_active_repos.py` (8 tests):

1. Walker yields only repos with `pushedAt >= since`, scanned counts
   include all walked entries.
2. **Early termination**: a fixture with 3 repos in-window followed
   by 2 out-of-window. Walker stops at the first out-of-window repo;
   respx assertion confirms exactly 1 page request was made.
3. `has_merges_in_window=True` when first PR's mergedAt in window AND
   baseRefName == default_branch.
4. `has_merges_in_window=False` when latest merged PR is to a feature
   branch only.
5. `has_merges_in_window=False` when most-recent merge is before `since`.
6. Merge-check pagination early-termination on UPDATED_AT.
7. Missing `defaultBranchRef` (rare archived repo) defaults to "main".
8. Empty org (zero repos) returns `([], 0)`.

`tests/test_server_tools.py` (~15 tests):

1. `list_configured_repos` returns configured set.
2. `list_configured_repos` with no config → empty repos list.
3. `list_active_repos` calls discover_active_repos and intersects with
   configured repos.
4. `list_active_repos` cached on second call (no second HTTP call).
5. `health_check` reports config status and cache size.
6. `health_check` reports rate-limit state from active pool clients.
7. `get_pr_cycle_time_stats` against the fixture returns count=40.
8. `get_pr_cycle_time_stats` cached on second call.
9. `get_review_health` against the fixture.
10. `get_pr_cycle_time_stats` for an unconfigured repo raises a typed error.
11. `clear_cache(scope="all")` reports count and zeros cache.
12. `clear_cache(scope="repo", repo=...)` drops only that repo.
13. Every stub tool raises NotImplementedError when invoked.
14. `build_server` registers exactly the expected tool names.
15. Tool responses round-trip as JSON dicts.

## CI workflow

`.github/workflows/ci.yml`:

```yaml
on:
  push:
  pull_request:

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - run: uv sync --frozen
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run mypy
      - run: uv run pytest --cov
```

`--frozen` enforces that `uv.lock` is up-to-date with `pyproject.toml`.

## README

Full rewrite. Sections:

1. **What it is** + sibling-servers note.
2. **Install for users**: `uvx github-sdlc-mcp` + Claude/Cursor/etc.
3. **First-run setup**: `config init`, edit YAML, set env vars.
4. **Config file format** + per-OS location table.
5. **Environment variables**.
6. **Tool reference**: brief one-liner per tool with example invocation.
7. **MCP host examples**: Claude Code + Claude Desktop JSON snippet.
8. **Troubleshooting**: `config path`, `health_check`.
9. **Development**: clone + `uv sync` + tests.
10. **Roadmap**: v0.2 stubs (named).

## What's NOT in this phase

- The metric stubs' bodies — they remain `NotImplementedError`. The
  goal is registered tool surface, not implementation.
- Production-grade observability (Prometheus, OpenTelemetry).
- Caching of intermediate normalized-PR data (we cache final tool
  results only).
- Token refresh / rotation.

## Acceptance

- All ~165+ existing tests pass; ~23 new tests pass.
- `uv run github-sdlc-mcp --help` shows full CLI.
- `uv run github-sdlc-mcp` starts and stays running (manual smoke).
- `uv run ruff check && uv run mypy && uv run pytest` all green.
- `.github/workflows/ci.yml` lints cleanly.
- README renders correctly.
