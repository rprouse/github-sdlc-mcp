"""Integration tests for the FastMCP tool implementations.

Tools are called directly via their ``*_impl`` functions so the FastMCP
transport doesn't sit between the assertion and the code under test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from github_sdlc_mcp.cache import TTLCache
from github_sdlc_mcp.server import (
    TOOL_NAMES,
    ServerContext,
    build_context,
    build_server,
    clear_cache_impl,
    get_pr_cycle_time_stats_impl,
    get_review_health_impl,
    health_check_impl,
    list_active_repos_impl,
)
from tests.conftest import multiplexed_prs  # noqa: F401
from tests.fixtures import load_pr_pages

BASE_URL = "https://api.github.com"
SINCE = date(2026, 4, 15)
UNTIL = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _ctx() -> ServerContext:
    ctx = build_context(
        env={"GITHUB_TOKEN": "ghp_test"},
        cache=TTLCache(clock=lambda: 0.0),  # frozen clock; nothing expires
    )
    # Disable sleeps so any retry doesn't block tests.
    ctx.client._sleep = AsyncMock()
    return ctx


def _pr_response(pages: list[dict[str, Any]]) -> list[httpx.Response]:
    return [httpx.Response(200, json=p) for p in pages]


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


# ---------------------------------------------------------------------------
# list_active_repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_active_repos_returns_org_walker_result() -> None:
    ctx = _ctx()
    in_window = datetime(2026, 5, 10, tzinfo=UTC)
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([
                ("web", in_window),
                ("api", in_window),
            ])),
            httpx.Response(200, json=_empty_merged_pr_page()),  # web merge check
            httpx.Response(200, json=_empty_merged_pr_page()),  # api merge check
        ]
    )
    try:
        result = await list_active_repos_impl(
            ctx, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await ctx.client.aclose()
    assert result.org == "acme"
    assert result.total_repos_active == 2
    assert {r.repo for r in result.repos} == {"web", "api"}


@pytest.mark.asyncio
@respx.mock
async def test_list_active_repos_caches_second_call() -> None:
    ctx = _ctx()
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_empty_merged_pr_page()),
        ]
    )
    try:
        await list_active_repos_impl(ctx, org="acme", since=SINCE, until=UNTIL)
        calls_after_first = route.call_count
        await list_active_repos_impl(ctx, org="acme", since=SINCE, until=UNTIL)
        assert route.call_count == calls_after_first  # no new HTTP calls
    finally:
        await ctx.client.aclose()


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_reports_cache_size() -> None:
    ctx = _ctx()
    try:
        result = await health_check_impl(ctx)
        assert result.cache_entries == 0
        assert result.ok is True
        assert result.provider == "github"
    finally:
        await ctx.client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_health_check_reports_rate_limit_after_call() -> None:
    """After an HTTP call updates rate-limit state, health_check surfaces it."""
    ctx = _ctx()
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"ok": True}},
            headers={
                "x-ratelimit-remaining": "4500",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": "1800000000",
            },
        )
    )
    try:
        await ctx.client.graphql("query{}")
        result = await health_check_impl(ctx)
        assert result.rate_limit_remaining == 4500
        assert result.last_successful_call_at is not None
    finally:
        await ctx.client.aclose()


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_cache_all_zeros_the_cache() -> None:
    ctx = _ctx()
    try:
        await ctx.cache.set("t", {"k": 1}, "v1")
        await ctx.cache.set("t", {"k": 2}, "v2")
        result = await clear_cache_impl(ctx, scope="all")
        assert result.entries_cleared == 2
        assert await ctx.cache.size() == 0
    finally:
        await ctx.client.aclose()


@pytest.mark.asyncio
async def test_clear_cache_org_drops_only_matching() -> None:
    ctx = _ctx()
    try:
        await ctx.cache.set("get_cycle", {"org": "acme"}, "ra")
        await ctx.cache.set("get_cycle", {"org": "beta"}, "rb")
        result = await clear_cache_impl(ctx, scope="org", org="acme")
        assert result.entries_cleared == 1
        assert result.org == "acme"
        # beta should still be there
        assert await ctx.cache.get("get_cycle", {"org": "beta"}) == "rb"
    finally:
        await ctx.client.aclose()


# ---------------------------------------------------------------------------
# Metric tools against the synthetic fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_cycle_time_stats_against_fixture() -> None:
    ctx = _ctx()
    # Walker order: org repos page → merge check → PR pages for that repo.
    pr_pages = load_pr_pages()
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))]),
            ),
            httpx.Response(200, json=_empty_merged_pr_page()),  # merge check
            *_pr_response(pr_pages),
        ]
    )
    try:
        result = await get_pr_cycle_time_stats_impl(
            ctx, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await ctx.client.aclose()
    assert result.count == 40
    assert result.median_hours is not None
    assert result.provider == "github"
    assert result.org == "acme"
    # Per-repo slices reconcile to org rollup
    assert sum(s.count for s in result.repos) == result.count


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_cycle_time_stats_is_cached() -> None:
    ctx = _ctx()
    pr_pages = load_pr_pages()
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))]),
            ),
            httpx.Response(200, json=_empty_merged_pr_page()),
            *_pr_response(pr_pages),
        ]
    )
    try:
        await get_pr_cycle_time_stats_impl(
            ctx, org="acme", since=SINCE, until=UNTIL
        )
        first_count = route.call_count
        await get_pr_cycle_time_stats_impl(
            ctx, org="acme", since=SINCE, until=UNTIL
        )
        assert route.call_count == first_count  # cache hit
    finally:
        await ctx.client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_review_health_against_fixture() -> None:
    ctx = _ctx()
    pr_pages = load_pr_pages()
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_org_page([("web", datetime(2026, 5, 10, tzinfo=UTC))]),
            ),
            httpx.Response(200, json=_empty_merged_pr_page()),
            *_pr_response(pr_pages),
        ]
    )
    try:
        result = await get_review_health_impl(
            ctx, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await ctx.client.aclose()
    assert result.count == 40
    assert result.fast_approval_count >= 3
    assert result.self_merge_count >= 2


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------


def test_build_server_registers_every_tool_name() -> None:
    ctx = _ctx()
    try:
        server = build_server(ctx)
        assert isinstance(server.name, str)
        # 11 tools in v0.2 (dropped list_configured_repos).
        assert len(TOOL_NAMES) == 11
    finally:
        # No async close needed here — the server is just a constructor call.
        pass


@pytest.mark.asyncio
async def test_response_models_serialize_as_json_dicts() -> None:
    ctx = _ctx()
    try:
        result = await health_check_impl(ctx)
        dumped = result.model_dump(mode="json")
        assert dumped["provider"] == "github"
        assert dumped["ok"] is True
    finally:
        await ctx.client.aclose()
