"""FastMCP server assembly, tool implementations, and the public ``build_server``.

Tool implementations are top-level async functions taking ``ServerContext`` as
the first positional arg. This makes them directly callable from tests
(``await get_pr_cycle_time_stats_impl(ctx, org=..., since=..., until=...)``)
without the FastMCP protocol layer in the way. ``build_server`` is a thin
factory that wraps each impl in a registered tool.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, TypeVar, cast

import httpx
from fastmcp import FastMCP

from github_sdlc_mcp.active_repos import DiscoveredRepo, discover_active_repos
from github_sdlc_mcp.cache import Cache, TTLCache
from github_sdlc_mcp.client.github import GitHubClient, make_github_client
from github_sdlc_mcp.metrics import (
    compute_cycle_time_stats,
    compute_review_health,
)
from github_sdlc_mcp.metrics.definitions import (
    FAST_APPROVAL_MAX_SECONDS_DEFAULT,
    FAST_APPROVAL_MIN_LINES_DEFAULT,
    definitions_for,
)
from github_sdlc_mcp.models import (
    ActiveRepo,
    ActiveRepoList,
    BaselineComparison,
    CacheClearResult,
    CIHealthStats,
    HealthStatus,
    MergeActivityStats,
    NormalizedPR,
    PortfolioSummary,
    PRCycleTimeStats,
    PRSizeStats,
    ReviewHealthStats,
    StalePRList,
)
from github_sdlc_mcp.normalize import PR_PAGE_QUERY, normalize_pr

logger = logging.getLogger(__name__)
T = TypeVar("T")

_MAX_CONCURRENT_REPO_FETCHES_DEFAULT = 16


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ServerContext:
    client: GitHubClient
    cache: Cache
    env: Mapping[str, str] = field(default_factory=dict)


def build_context(
    *,
    cache: Cache | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServerContext:
    """Assemble a ServerContext bound to cloud GitHub.

    ``transport`` is the respx injection point for tests.
    """
    return ServerContext(
        client=make_github_client(env=env, transport=transport),
        cache=cache or TTLCache(),
        env=env or os.environ,
    )


# ---------------------------------------------------------------------------
# Cache wrapper
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Active-repo + PR fetch helpers
# ---------------------------------------------------------------------------


async def _active_repos(
    ctx: ServerContext, *, org: str, since: date, until: date
) -> tuple[list[DiscoveredRepo], int]:
    return await discover_active_repos(
        ctx.client, org=org, since=since, until=until
    )


async def _load_prs_for_org(
    ctx: ServerContext, *, org: str, since: date, until: date
) -> list[NormalizedPR]:
    """Walk active repos and fan-out PR fetches with a concurrency cap.

    Fail-fast on purpose: if any single repo's fetch raises (permissions,
    GraphQL error, transient network failure that exhausts the client's
    retry budget), the whole tool call fails. v0.2 prefers loud failure
    over silently returning a partial dataset that an agent could mistake
    for a complete answer. Per-repo error swallowing with a warning channel
    is on the v0.3+ list.
    """
    repos, _scanned = await _active_repos(ctx, org=org, since=since, until=until)
    max_concurrent = _read_threshold_int(
        ctx,
        "GITHUB_SDLC_MCP_MAX_CONCURRENT_REPO_FETCHES",
        _MAX_CONCURRENT_REPO_FETCHES_DEFAULT,
    )
    sem = asyncio.Semaphore(max_concurrent)

    async def _fetch(d: DiscoveredRepo) -> list[NormalizedPR]:
        async with sem:
            return await _load_prs_for_repo(ctx, d.owner, d.repo, since)

    chunks = await asyncio.gather(*[_fetch(d) for d in repos])
    return [pr for chunk in chunks for pr in chunk]


async def _load_prs_for_repo(
    ctx: ServerContext, owner: str, repo: str, since: date
) -> list[NormalizedPR]:
    """Paginate PRs, early-terminate when a node's updatedAt falls before ``since``.

    The PR query orders by UPDATED_AT DESC, so once we see a node whose
    ``updatedAt`` is older than ``since``, every subsequent node is older too
    and cannot fall in the window.
    """
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=UTC)
    prs: list[NormalizedPR] = []
    async for node in ctx.client.paginate_graphql(
        PR_PAGE_QUERY,
        {"owner": owner, "name": repo},
        connection_path=["repository", "pullRequests"],
    ):
        updated_at = _opt_dt(node.get("updatedAt"))
        if updated_at is not None and updated_at < since_dt:
            break
        prs.append(normalize_pr(node, owner=owner, repo=repo))
    return prs


def _opt_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _resolve_until(until: date | None) -> date:
    return until if until is not None else datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------


def _read_threshold_int(ctx: ServerContext, var: str, default: int) -> int:
    raw = ctx.env.get(var)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid value for %s=%r; falling back to %d", var, raw, default)
        return default


def _read_threshold_float(ctx: ServerContext, var: str, default: float) -> float:
    raw = ctx.env.get(var)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid value for %s=%r; falling back to %g", var, raw, default)
        return default


# ---------------------------------------------------------------------------
# Tool impls — discovery / admin
# ---------------------------------------------------------------------------


async def list_active_repos_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> ActiveRepoList:
    resolved_until = _resolve_until(until)
    kwargs: dict[str, Any] = {"org": org, "since": since, "until": until}

    async def _compute() -> ActiveRepoList:
        discovered, scanned = await _active_repos(
            ctx, org=org, since=since, until=resolved_until
        )
        return ActiveRepoList(
            org=org,
            since=since,
            until=resolved_until,
            total_repos_scanned=scanned,
            total_repos_active=len(discovered),
            repos=[
                ActiveRepo(
                    owner=d.owner,
                    repo=d.repo,
                    pushed_at=d.pushed_at,
                    default_branch=d.default_branch,
                    has_merges_in_window=d.has_merges_in_window,
                )
                for d in discovered
            ],
            definitions=definitions_for(
                "active_repo_pushed_at", "active_repo_has_merges"
            ),
        )

    return await cached_call(ctx, "list_active_repos", kwargs, _compute)


async def health_check_impl(ctx: ServerContext) -> HealthStatus:
    state = ctx.client.rate_limit
    return HealthStatus(
        ok=True,
        rate_limit_remaining=state.remaining,
        rate_limit_resets_at=state.resets_at,
        last_successful_call_at=ctx.client.last_successful_call_at,
        cache_entries=await ctx.cache.size(),
    )


async def clear_cache_impl(
    ctx: ServerContext,
    *,
    scope: Literal["all", "org"] = "all",
    org: str | None = None,
) -> CacheClearResult:
    cleared = await ctx.cache.clear(scope=scope, org=org)
    return CacheClearResult(scope=scope, org=org, entries_cleared=cleared)


# ---------------------------------------------------------------------------
# Tool impls — metrics
# ---------------------------------------------------------------------------


async def get_pr_cycle_time_stats_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> PRCycleTimeStats:
    resolved_until = _resolve_until(until)

    async def _compute() -> PRCycleTimeStats:
        prs = await _load_prs_for_org(
            ctx, org=org, since=since, until=resolved_until
        )
        return compute_cycle_time_stats(
            org=org, since=since, until=resolved_until, prs=prs
        )

    return await cached_call(
        ctx,
        "get_pr_cycle_time_stats",
        {"org": org, "since": since, "until": until},
        _compute,
    )


async def get_review_health_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> ReviewHealthStats:
    resolved_until = _resolve_until(until)
    min_lines = _read_threshold_int(
        ctx,
        "GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES",
        FAST_APPROVAL_MIN_LINES_DEFAULT,
    )
    max_seconds = _read_threshold_float(
        ctx,
        "GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS",
        FAST_APPROVAL_MAX_SECONDS_DEFAULT,
    )

    async def _compute() -> ReviewHealthStats:
        prs = await _load_prs_for_org(
            ctx, org=org, since=since, until=resolved_until
        )
        return compute_review_health(
            org=org,
            since=since,
            until=resolved_until,
            prs=prs,
            fast_approval_min_lines=min_lines,
            fast_approval_max_seconds=max_seconds,
        )

    return await cached_call(
        ctx,
        "get_review_health",
        {
            "org": org,
            "since": since,
            "until": until,
            "min_lines": min_lines,
            "max_seconds": max_seconds,
        },
        _compute,
    )


async def get_pr_size_stats_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> PRSizeStats:
    raise NotImplementedError(
        "get_pr_size_stats is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


async def get_ci_health_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> CIHealthStats:
    raise NotImplementedError(
        "get_ci_health is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


async def get_stale_prs_impl(
    ctx: ServerContext,
    *,
    org: str,
    threshold_days: int = 14,
    as_of: date | None = None,
) -> StalePRList:
    raise NotImplementedError(
        "get_stale_prs is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


async def get_merge_activity_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> MergeActivityStats:
    raise NotImplementedError(
        "get_merge_activity is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


async def get_portfolio_summary_impl(
    ctx: ServerContext,
    *,
    org: str,
    since: date,
    until: date | None = None,
    include_inactive: bool = False,
) -> PortfolioSummary:
    raise NotImplementedError(
        "get_portfolio_summary is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


async def compare_to_baseline_impl(
    ctx: ServerContext,
    *,
    org: str,
    metric: str,
    current_window_days: int = 30,
    baseline_window_days: int = 90,
) -> BaselineComparison:
    raise NotImplementedError(
        "compare_to_baseline is stubbed in v0.2; see docs/spec_v3.md §10. "
        "Tool is registered for surface discovery only."
    )


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


TOOL_NAMES = (
    "list_active_repos",
    "health_check",
    "clear_cache",
    "get_pr_cycle_time_stats",
    "get_review_health",
    "get_pr_size_stats",
    "get_ci_health",
    "get_stale_prs",
    "get_merge_activity",
    "get_portfolio_summary",
    "compare_to_baseline",
)


def build_server(ctx: ServerContext) -> FastMCP:
    """Construct a FastMCP server with every tool registered against ``ctx``."""
    mcp: FastMCP = FastMCP("github-sdlc-mcp")

    @mcp.tool()
    async def list_active_repos(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await list_active_repos_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def health_check() -> dict[str, Any]:
        result = await health_check_impl(ctx)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def clear_cache(
        scope: Literal["all", "org"] = "all", org: str | None = None
    ) -> dict[str, Any]:
        result = await clear_cache_impl(ctx, scope=scope, org=org)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_cycle_time_stats(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_pr_cycle_time_stats_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_review_health(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_review_health_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_size_stats(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_pr_size_stats_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_ci_health(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_ci_health_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_stale_prs(
        org: str, threshold_days: int = 14, as_of: date | None = None
    ) -> dict[str, Any]:
        result = await get_stale_prs_impl(
            ctx, org=org, threshold_days=threshold_days, as_of=as_of
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_merge_activity(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_merge_activity_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_portfolio_summary(
        org: str,
        since: date,
        until: date | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        result = await get_portfolio_summary_impl(
            ctx,
            org=org,
            since=since,
            until=until,
            include_inactive=include_inactive,
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def compare_to_baseline_tool(
        org: str,
        metric: str,
        current_window_days: int = 30,
        baseline_window_days: int = 90,
    ) -> dict[str, Any]:
        result = await compare_to_baseline_impl(
            ctx,
            org=org,
            metric=metric,
            current_window_days=current_window_days,
            baseline_window_days=baseline_window_days,
        )
        return result.model_dump(mode="json")

    return mcp
