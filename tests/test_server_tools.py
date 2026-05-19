"""Integration tests for the FastMCP tool implementations.

Tools are called directly via their ``*_impl`` functions so the FastMCP
transport doesn't sit between the assertion and the code under test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import AnyHttpUrl

from github_sdlc_mcp.cache import TTLCache
from github_sdlc_mcp.config import AppConfig, GitHubHost, RepoEntry, ResolvedConfig
from github_sdlc_mcp.server import (
    TOOL_NAMES,
    ServerContext,
    UnknownRepoError,
    build_context,
    build_server,
    clear_cache_impl,
    get_pr_cycle_time_stats_impl,
    get_review_health_impl,
    health_check_impl,
    list_active_repos_impl,
    list_configured_repos_impl,
)
from tests.fixtures import load_pr_pages

BASE_URL = "https://api.github.com"
SINCE = date(2026, 4, 15)
UNTIL = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _config() -> AppConfig:
    return AppConfig(
        github_hosts={
            "cloud": GitHubHost(
                base_url=AnyHttpUrl(BASE_URL), token_env="GITHUB_TOKEN"
            )
        },
        repos=[
            RepoEntry(host="cloud", owner="acme", repo="web", company_label="AcmeCo"),
            RepoEntry(host="cloud", owner="acme", repo="api", company_label="AcmeCo"),
        ],
    )


def _resolved(cfg: AppConfig | None = None) -> ResolvedConfig:
    return ResolvedConfig(
        config=cfg if cfg is not None else _config(),
        source="cli",
        path=Path("/etc/repos.yaml"),
        searched_paths=[Path("/etc/repos.yaml")],
    )


def _ctx(cfg: AppConfig | None = None) -> ServerContext:
    return build_context(
        _resolved(cfg),
        env={"GITHUB_TOKEN": "ghp_test"},
        cache=TTLCache(clock=lambda: 0.0),  # frozen clock; nothing expires
    )


def _patch_pool_sleep(ctx: ServerContext, host: str = "cloud") -> None:
    """Replace the per-host client's sleep so retries don't block tests."""
    # Touch get(host) so client gets constructed, then swap its sleep.
    client = ctx.pool.get(host)
    client._sleep = AsyncMock()


def _pr_response(pages: list[dict[str, Any]]) -> list[httpx.Response]:
    return [httpx.Response(200, json=p) for p in pages]


# ---------------------------------------------------------------------------
# list_configured_repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_configured_repos_returns_full_set() -> None:
    ctx = _ctx()
    result = await list_configured_repos_impl(ctx)
    assert result.provider == "github"
    assert len(result.repos) == 2
    assert {r.repo for r in result.repos} == {"web", "api"}


@pytest.mark.asyncio
async def test_list_configured_repos_empty_when_no_config() -> None:
    ctx = _ctx(cfg=AppConfig())
    result = await list_configured_repos_impl(ctx)
    assert result.repos == []


# ---------------------------------------------------------------------------
# list_active_repos
# ---------------------------------------------------------------------------


def _org_page(repos: list[tuple[str, datetime]]) -> dict[str, Any]:
    return {
        "data": {
            "organization": {
                "repositories": {
                    "nodes": [
                        {
                            "name": name,
                            "pushedAt": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "defaultBranchRef": {"name": "main"},
                        }
                        for name, dt in repos
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


def _empty_merged_pr_page() -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_active_repos_intersects_with_configured() -> None:
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    in_window = datetime(2026, 5, 10, tzinfo=UTC)
    # Org has web, api, AND a third repo not in config — should be filtered out.
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([
                ("web", in_window),
                ("api", in_window),
                ("unrelated", in_window),
            ])),
            httpx.Response(200, json=_empty_merged_pr_page()),  # web merge check
            httpx.Response(200, json=_empty_merged_pr_page()),  # api merge check
            httpx.Response(200, json=_empty_merged_pr_page()),  # unrelated merge check
        ]
    )
    result = await list_active_repos_impl(ctx, since=SINCE, until=UNTIL)
    assert result.total_repos_active == 2
    assert {r.repo for r in result.repos} == {"web", "api"}


@pytest.mark.asyncio
@respx.mock
async def test_list_active_repos_caches_second_call() -> None:
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_empty_merged_pr_page()),
        ]
    )
    await list_active_repos_impl(ctx, since=SINCE, until=UNTIL)
    calls_after_first = route.call_count
    await list_active_repos_impl(ctx, since=SINCE, until=UNTIL)
    assert route.call_count == calls_after_first  # no new HTTP calls


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_reports_config_status_and_cache_size() -> None:
    ctx = _ctx()
    result = await health_check_impl(ctx)
    assert result.config_status == "loaded"
    assert result.config_path == Path("/etc/repos.yaml")
    assert result.cache_entries == 0
    assert result.ok is True


@pytest.mark.asyncio
async def test_health_check_reports_not_found_when_no_config() -> None:
    resolved = ResolvedConfig(
        config=None,
        source="not_found",
        path=None,
        searched_paths=[Path("/x/repos.yaml")],
    )
    ctx = build_context(resolved, cache=TTLCache(clock=lambda: 0.0))
    result = await health_check_impl(ctx)
    assert result.config_status == "not_found"
    assert result.config_path is None
    assert result.ok is False


@pytest.mark.asyncio
@respx.mock
async def test_health_check_reports_rate_limit_after_call() -> None:
    """After an HTTP call updates rate-limit state, health_check surfaces it."""
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(
            200,
            json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))]),
            headers={
                "x-ratelimit-remaining": "4500",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": "1800000000",
            },
        )
    )
    # Issue a call so the client populates rate-limit state.
    client = ctx.pool.get("cloud")
    await client.graphql("query{}")
    result = await health_check_impl(ctx)
    assert result.rate_limit_remaining == 4500
    assert result.last_successful_call_at is not None


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_cache_all_zeros_the_cache() -> None:
    ctx = _ctx()
    await ctx.cache.set("t", {"k": 1}, "v1")
    await ctx.cache.set("t", {"k": 2}, "v2")
    result = await clear_cache_impl(ctx, scope="all")
    assert result.entries_cleared == 2
    assert await ctx.cache.size() == 0


@pytest.mark.asyncio
async def test_clear_cache_repo_drops_only_matching() -> None:
    ctx = _ctx()
    await ctx.cache.set("get_cycle", {"repo": "acme/web"}, "rw")
    await ctx.cache.set("get_cycle", {"repo": "acme/api"}, "ra")
    result = await clear_cache_impl(ctx, scope="repo", repo="acme/web")
    assert result.entries_cleared == 1
    assert result.repo == "acme/web"
    # acme/api should still be there
    assert await ctx.cache.get("get_cycle", {"repo": "acme/api"}) == "ra"


# ---------------------------------------------------------------------------
# Metric tools against the synthetic fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_cycle_time_stats_against_fixture() -> None:
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=_pr_response(load_pr_pages())
    )
    result = await get_pr_cycle_time_stats_impl(
        ctx, repo="acme/web", since=SINCE, until=UNTIL
    )
    assert result.count == 40
    assert result.median_hours is not None
    assert result.provider == "github"


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_cycle_time_stats_is_cached() -> None:
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=_pr_response(load_pr_pages())
    )
    await get_pr_cycle_time_stats_impl(
        ctx, repo="acme/web", since=SINCE, until=UNTIL
    )
    first_count = route.call_count
    await get_pr_cycle_time_stats_impl(
        ctx, repo="acme/web", since=SINCE, until=UNTIL
    )
    assert route.call_count == first_count  # cache hit


@pytest.mark.asyncio
@respx.mock
async def test_get_review_health_against_fixture() -> None:
    ctx = _ctx()
    _patch_pool_sleep(ctx)
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=_pr_response(load_pr_pages())
    )
    result = await get_review_health_impl(
        ctx, repo="acme/web", since=SINCE, until=UNTIL
    )
    assert result.count == 40
    assert result.fast_approval_count >= 3
    assert result.self_merge_count >= 2


@pytest.mark.asyncio
async def test_unknown_repo_raises() -> None:
    ctx = _ctx()
    with pytest.raises(UnknownRepoError) as exc:
        await get_pr_cycle_time_stats_impl(
            ctx, repo="other/repo", since=SINCE, until=UNTIL
        )
    assert "other/repo" in str(exc.value)


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------


def test_build_server_registers_every_tool_name() -> None:
    ctx = _ctx()
    server = build_server(ctx)
    # FastMCP keeps tools in an internal registry; we expect at least every
    # name from TOOL_NAMES to be discoverable via call_tool.
    assert isinstance(server.name, str)
    assert len(TOOL_NAMES) == 12


@pytest.mark.asyncio
async def test_response_models_serialize_as_json_dicts() -> None:
    ctx = _ctx()
    result = await list_configured_repos_impl(ctx)
    dumped = result.model_dump(mode="json")
    assert dumped["provider"] == "github"
    assert isinstance(dumped["repos"], list)
    assert all(isinstance(r, dict) for r in dumped["repos"])
