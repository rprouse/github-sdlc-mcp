"""FastMCP server assembly, tool implementations, and the public ``build_server``.

Tool implementations are top-level async functions taking ``ServerContext`` as
the first positional arg. This makes them directly callable from tests
(``await get_pr_cycle_time_stats_impl(ctx, repo=..., since=..., until=...)``)
without the FastMCP protocol layer in the way. ``build_server`` is a thin
factory that wraps each impl in a registered tool.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import httpx
from fastmcp import FastMCP

from github_sdlc_mcp.active_repos import discover_active_repos
from github_sdlc_mcp.cache import Cache, TTLCache
from github_sdlc_mcp.client.github import GitHubClientPool
from github_sdlc_mcp.config import AppConfig, RepoEntry, ResolvedConfig
from github_sdlc_mcp.metrics import (
    compare_to_baseline,
    compute_ci_health,
    compute_cycle_time_stats,
    compute_merge_activity,
    compute_pr_size_stats,
    compute_review_health,
    compute_stale_prs,
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
    ConfiguredRepo,
    ConfiguredRepoList,
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


class UnknownRepoError(Exception):
    """Raised when a tool is called with a repo not in the configured set."""

    def __init__(self, repo: str) -> None:
        super().__init__(
            f"Repo {repo!r} is not in this server's configured repos. "
            "Add it to repos.yaml under 'repos:' and restart the MCP host."
        )
        self.repo = repo


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ServerContext:
    config: AppConfig
    cache: Cache
    pool: GitHubClientPool
    config_path: Path | None
    config_searched_paths: list[Path]
    config_status: Literal["loaded", "not_found", "error"]
    config_error: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)


def build_context(
    resolved: ResolvedConfig,
    *,
    cache: Cache | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServerContext:
    """Assemble a ServerContext from a ResolvedConfig. ``transport`` is the
    respx injection point for tests."""
    cfg = resolved.config or AppConfig()
    cache_obj: Cache = cache or TTLCache()
    pool = GitHubClientPool(cfg.github_hosts, env=env, transport=transport)
    status: Literal["loaded", "not_found", "error"] = (
        "loaded" if resolved.config is not None else "not_found"
    )
    return ServerContext(
        config=cfg,
        cache=cache_obj,
        pool=pool,
        config_path=resolved.path,
        config_searched_paths=list(resolved.searched_paths),
        config_status=status,
        config_error=None,
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
# Helpers
# ---------------------------------------------------------------------------


def _find_repo_entry(ctx: ServerContext, repo: str) -> RepoEntry:
    try:
        owner, name = repo.split("/", 1)
    except ValueError as e:
        raise UnknownRepoError(repo) from e
    for r in ctx.config.repos:
        if r.owner == owner and r.repo == name:
            return r
    raise UnknownRepoError(repo)


async def _load_prs_for_repo(
    ctx: ServerContext, repo: str, since: date, _until: date
) -> list[NormalizedPR]:
    """Paginate PRs, early-terminate when a node's updatedAt falls before ``since``.

    The PR query orders by UPDATED_AT DESC, so once we see a node whose
    ``updatedAt`` is older than ``since``, every subsequent node is older too
    and cannot fall in the window.
    """
    entry = _find_repo_entry(ctx, repo)
    client = ctx.pool.get(entry.host)
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=UTC)

    prs: list[NormalizedPR] = []
    async for node in client.paginate_graphql(
        PR_PAGE_QUERY,
        {"owner": entry.owner, "name": entry.repo},
        connection_path=["repository", "pullRequests"],
    ):
        updated_at = _opt_dt(node.get("updatedAt"))
        if updated_at is not None and updated_at < since_dt:
            break
        prs.append(normalize_pr(node, owner=entry.owner, repo=entry.repo))

    return prs


def _opt_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def list_configured_repos_impl(ctx: ServerContext) -> ConfiguredRepoList:
    return ConfiguredRepoList(
        repos=[
            ConfiguredRepo(
                owner=r.owner,
                repo=r.repo,
                company_label=r.company_label,
                github_host=r.host,
            )
            for r in ctx.config.repos
        ]
    )


async def list_active_repos_impl(
    ctx: ServerContext,
    *,
    since: date,
    until: date,
    host: str | None = None,
    org: str | None = None,
) -> ActiveRepoList:
    kwargs: dict[str, Any] = {"since": since, "until": until, "host": host, "org": org}

    async def _compute() -> ActiveRepoList:
        configured = ctx.config.repos
        if host is not None:
            configured = [r for r in configured if r.host == host]
        if org is not None:
            configured = [r for r in configured if r.owner == org]

        # Group by (host, owner) — one org-walk per unique pair.
        groups: dict[tuple[str, str], list[RepoEntry]] = {}
        for r in configured:
            groups.setdefault((r.host, r.owner), []).append(r)

        active_rows: list[ActiveRepo] = []
        total_scanned = 0
        for (host_key, owner), entries in groups.items():
            client = ctx.pool.get(host_key)
            discovered, scanned = await discover_active_repos(
                client, org=owner, since=since, until=until
            )
            total_scanned += scanned
            configured_names = {e.repo for e in entries}
            label_by_name = {e.repo: e.company_label for e in entries}
            for d in discovered:
                if d.repo not in configured_names:
                    continue
                active_rows.append(
                    ActiveRepo(
                        owner=d.owner,
                        repo=d.repo,
                        company_label=label_by_name[d.repo],
                        pushed_at=d.pushed_at,
                        default_branch=d.default_branch,
                        has_merges_in_window=d.has_merges_in_window,
                    )
                )

        return ActiveRepoList(
            since=since,
            until=until,
            total_repos_configured=len(configured),
            total_repos_scanned=total_scanned,
            total_repos_active=len(active_rows),
            repos=active_rows,
            definitions=definitions_for(
                "active_repo_pushed_at", "active_repo_has_merges"
            ),
        )

    return await cached_call(ctx, "list_active_repos", kwargs, _compute)


async def health_check_impl(ctx: ServerContext) -> HealthStatus:
    # Aggregate rate limits across the active pool clients. Take the lowest
    # remaining (most conservative) and the latest reset.
    remaining: int | None = None
    resets_at: datetime | None = None
    last_success: datetime | None = None
    for key in ctx.pool.active_host_keys:
        client = ctx.pool.get(key)
        state = client.rate_limit
        if state.remaining is not None:
            remaining = state.remaining if remaining is None else min(remaining, state.remaining)
        if state.resets_at is not None:
            resets_at = (
                state.resets_at if resets_at is None else max(resets_at, state.resets_at)
            )
        if client.last_successful_call_at is not None:
            last_success = (
                client.last_successful_call_at
                if last_success is None
                else max(last_success, client.last_successful_call_at)
            )

    cache_size = await ctx.cache.size()
    return HealthStatus(
        ok=ctx.config_status == "loaded",
        rate_limit_remaining=remaining,
        rate_limit_resets_at=resets_at,
        last_successful_call_at=last_success,
        cache_entries=cache_size,
        config_status=ctx.config_status,
        config_path=ctx.config_path,
        config_searched_paths=ctx.config_searched_paths,
        config_error=ctx.config_error,
    )


async def clear_cache_impl(
    ctx: ServerContext,
    *,
    scope: Literal["all", "repo"] = "all",
    repo: str | None = None,
) -> CacheClearResult:
    cleared = await ctx.cache.clear(scope=scope, repo=repo)
    return CacheClearResult(scope=scope, repo=repo, entries_cleared=cleared)


# ---------- Implemented metrics ----------


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


async def get_pr_cycle_time_stats_impl(
    ctx: ServerContext, *, repo: str, since: date, until: date
) -> PRCycleTimeStats:
    async def _compute() -> PRCycleTimeStats:
        prs = await _load_prs_for_repo(ctx, repo, since, until)
        return compute_cycle_time_stats(repo=repo, since=since, until=until, prs=prs)

    return await cached_call(
        ctx,
        "get_pr_cycle_time_stats",
        {"repo": repo, "since": since, "until": until},
        _compute,
    )


async def get_review_health_impl(
    ctx: ServerContext, *, repo: str, since: date, until: date
) -> ReviewHealthStats:
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
        prs = await _load_prs_for_repo(ctx, repo, since, until)
        return compute_review_health(
            repo=repo,
            since=since,
            until=until,
            prs=prs,
            fast_approval_min_lines=min_lines,
            fast_approval_max_seconds=max_seconds,
        )

    return await cached_call(
        ctx,
        "get_review_health",
        {"repo": repo, "since": since, "until": until, "min_lines": min_lines, "max_seconds": max_seconds},
        _compute,
    )


# ---------- Stubs (raise on call, but tool surface is real) ----------


async def get_pr_size_stats_impl(
    ctx: ServerContext, *, repo: str, since: date, until: date
) -> PRSizeStats:
    prs = await _load_prs_for_repo(ctx, repo, since, until)
    return compute_pr_size_stats(repo=repo, since=since, until=until, prs=prs)


async def get_ci_health_impl(
    ctx: ServerContext, *, repo: str, since: date, until: date
) -> CIHealthStats:
    prs = await _load_prs_for_repo(ctx, repo, since, until)
    return compute_ci_health(repo=repo, since=since, until=until, prs=prs)


async def get_stale_prs_impl(
    ctx: ServerContext, *, repo: str, threshold_days: int = 14, as_of: date | None = None
) -> StalePRList:
    # Stale uses point-in-time data; we still need a PR list to inspect.
    today = as_of or datetime.now(UTC).date()
    prs = await _load_prs_for_repo(ctx, repo, since=today, _until=today)
    return compute_stale_prs(
        repo=repo, prs=prs, threshold_days=threshold_days, as_of=as_of
    )


async def get_merge_activity_impl(
    ctx: ServerContext, *, repo: str, since: date, until: date
) -> MergeActivityStats:
    prs = await _load_prs_for_repo(ctx, repo, since, until)
    return compute_merge_activity(
        repo=repo, since=since, until=until, prs=prs, default_branch_commits=[]
    )


async def get_portfolio_summary_impl(
    ctx: ServerContext,
    *,
    since: date,
    until: date,
    company_filter: str | None = None,
    include_inactive: bool = False,
) -> PortfolioSummary:
    raise NotImplementedError(
        "compute_portfolio_summary is stubbed in v0.1.0; see docs/spec_v2.md §10."
    )


async def compare_to_baseline_impl(
    ctx: ServerContext,
    *,
    repo: str,
    metric: str,
    current_window_days: int = 30,
    baseline_window_days: int = 90,
) -> BaselineComparison:
    today = datetime.now(UTC).date()
    return compare_to_baseline(
        repo=repo,
        metric=metric,
        as_of=today,
        current_window_days=current_window_days,
        baseline_window_days=baseline_window_days,
    )


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


TOOL_NAMES = (
    "list_configured_repos",
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
    async def list_configured_repos() -> dict[str, Any]:
        result = await list_configured_repos_impl(ctx)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def list_active_repos(
        since: date,
        until: date,
        host: str | None = None,
        org: str | None = None,
    ) -> dict[str, Any]:
        result = await list_active_repos_impl(
            ctx, since=since, until=until, host=host, org=org
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def health_check() -> dict[str, Any]:
        result = await health_check_impl(ctx)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def clear_cache(
        scope: Literal["all", "repo"] = "all", repo: str | None = None
    ) -> dict[str, Any]:
        result = await clear_cache_impl(ctx, scope=scope, repo=repo)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_cycle_time_stats(
        repo: str, since: date, until: date
    ) -> dict[str, Any]:
        result = await get_pr_cycle_time_stats_impl(
            ctx, repo=repo, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_review_health(
        repo: str, since: date, until: date
    ) -> dict[str, Any]:
        result = await get_review_health_impl(ctx, repo=repo, since=since, until=until)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_size_stats(
        repo: str, since: date, until: date
    ) -> dict[str, Any]:
        result = await get_pr_size_stats_impl(ctx, repo=repo, since=since, until=until)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_ci_health(repo: str, since: date, until: date) -> dict[str, Any]:
        result = await get_ci_health_impl(ctx, repo=repo, since=since, until=until)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_stale_prs(
        repo: str, threshold_days: int = 14, as_of: date | None = None
    ) -> dict[str, Any]:
        result = await get_stale_prs_impl(
            ctx, repo=repo, threshold_days=threshold_days, as_of=as_of
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_merge_activity(
        repo: str, since: date, until: date
    ) -> dict[str, Any]:
        result = await get_merge_activity_impl(ctx, repo=repo, since=since, until=until)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_portfolio_summary(
        since: date,
        until: date,
        company_filter: str | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        result = await get_portfolio_summary_impl(
            ctx,
            since=since,
            until=until,
            company_filter=company_filter,
            include_inactive=include_inactive,
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def compare_to_baseline_tool(
        repo: str,
        metric: str,
        current_window_days: int = 30,
        baseline_window_days: int = 90,
    ) -> dict[str, Any]:
        result = await compare_to_baseline_impl(
            ctx,
            repo=repo,
            metric=metric,
            current_window_days=current_window_days,
            baseline_window_days=baseline_window_days,
        )
        return result.model_dump(mode="json")

    return mcp
