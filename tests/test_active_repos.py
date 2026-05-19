"""Tests for the active-repo walker — including the early-termination assertion."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import AnyHttpUrl

from github_sdlc_mcp.active_repos import (
    DiscoveredRepo,
    discover_active_repos,
)
from github_sdlc_mcp.client.github import GitHubClient
from github_sdlc_mcp.config import GitHubHost

BASE_URL = "https://api.github.com"
SINCE = date(2026, 4, 15)
UNTIL = date(2026, 5, 15)


def _client() -> GitHubClient:
    host = GitHubHost(base_url=AnyHttpUrl(BASE_URL), token_env="GITHUB_TOKEN")
    return GitHubClient(host, token="ghp_test", sleep=AsyncMock(), backoff_base=0.0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_node(name: str, pushed_at: datetime, default_branch: str = "main") -> dict[str, Any]:
    return {
        "name": name,
        "pushedAt": _iso(pushed_at),
        "defaultBranchRef": {"name": default_branch},
    }


def _org_page(nodes: list[dict[str, Any]], has_next: bool = False, cursor: str | None = None) -> dict[str, Any]:
    return {
        "data": {
            "organization": {
                "repositories": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                }
            }
        }
    }


def _merged_pr_page(nodes: list[dict[str, Any]], has_next: bool = False) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": None},
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Org-level walker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_walker_yields_only_in_window_repos() -> None:
    """Org has 3 in-window repos and 2 out-of-window repos in one page.
    Walker should terminate at the first out-of-window repo and not paginate."""
    in_window_dt = datetime(2026, 5, 10, tzinfo=UTC)
    out_of_window_dt = datetime(2026, 3, 1, tzinfo=UTC)  # before SINCE

    org_route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_org_page(
                    [
                        _repo_node("alpha", in_window_dt),
                        _repo_node("beta", in_window_dt - timedelta(days=1)),
                        _repo_node("gamma", in_window_dt - timedelta(days=2)),
                        _repo_node("delta", out_of_window_dt),  # walker breaks here
                        _repo_node("epsilon", out_of_window_dt - timedelta(days=5)),
                    ]
                ),
            ),
            # Per-repo merge checks for alpha/beta/gamma — return empty so
            # has_merges_in_window = False.
            httpx.Response(200, json=_merged_pr_page([])),
            httpx.Response(200, json=_merged_pr_page([])),
            httpx.Response(200, json=_merged_pr_page([])),
        ]
    )
    client = _client()
    try:
        active, scanned = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()

    assert [r.repo for r in active] == ["alpha", "beta", "gamma"]
    # scanned includes the terminating "delta" node — we did fetch it.
    assert scanned == 4
    # 1 org-list request + 3 merge-check requests = 4. No second page fetch.
    assert org_route.call_count == 4


@pytest.mark.asyncio
@respx.mock
async def test_walker_early_terminates_across_pages() -> None:
    """If a full page is in-window and the next page contains the cutoff,
    we fetch page 2 but not page 3."""
    in_dt = datetime(2026, 5, 10, tzinfo=UTC)
    out_dt = datetime(2026, 3, 1, tzinfo=UTC)

    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            # page 1: all in-window
            httpx.Response(
                200,
                json=_org_page(
                    [_repo_node(f"r{i}", in_dt) for i in range(3)],
                    has_next=True,
                    cursor="cursor1",
                ),
            ),
            # 3 merge checks
            httpx.Response(200, json=_merged_pr_page([])),
            httpx.Response(200, json=_merged_pr_page([])),
            httpx.Response(200, json=_merged_pr_page([])),
            # page 2: starts in-window, transitions out
            httpx.Response(
                200,
                json=_org_page(
                    [
                        _repo_node("r3", in_dt - timedelta(days=2)),
                        _repo_node("r4", out_dt),  # walker breaks here
                    ],
                    has_next=True,
                    cursor="cursor2",
                ),
            ),
            httpx.Response(200, json=_merged_pr_page([])),  # for r3
            # page 3 would arrive next — but we'd better not fetch it.
            httpx.Response(500, text="should not be called"),
        ]
    )
    client = _client()
    try:
        active, scanned = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()

    assert [r.repo for r in active] == ["r0", "r1", "r2", "r3"]
    assert scanned == 5  # includes r4 (the cutoff node)
    # 2 org-list requests + 4 merge checks = 6. No third org-list call.
    assert route.call_count == 6


@pytest.mark.asyncio
@respx.mock
async def test_has_merges_true_when_merged_to_default_in_window() -> None:
    in_window_pr = {
        "mergedAt": _iso(datetime(2026, 5, 5, tzinfo=UTC)),
        "updatedAt": _iso(datetime(2026, 5, 5, tzinfo=UTC)),
        "baseRefName": "main",
    }
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([_repo_node("r1", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_merged_pr_page([in_window_pr])),
        ]
    )
    client = _client()
    try:
        active, _ = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active[0].has_merges_in_window is True


@pytest.mark.asyncio
@respx.mock
async def test_has_merges_false_when_merge_is_to_feature_branch() -> None:
    pr_to_feature = {
        "mergedAt": _iso(datetime(2026, 5, 5, tzinfo=UTC)),
        "updatedAt": _iso(datetime(2026, 5, 5, tzinfo=UTC)),
        "baseRefName": "feature/x",
    }
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([_repo_node("r1", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_merged_pr_page([pr_to_feature])),
        ]
    )
    client = _client()
    try:
        active, _ = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active[0].has_merges_in_window is False


@pytest.mark.asyncio
@respx.mock
async def test_has_merges_false_when_latest_merge_before_window() -> None:
    old_pr = {
        "mergedAt": _iso(datetime(2026, 1, 5, tzinfo=UTC)),  # well before SINCE
        "updatedAt": _iso(datetime(2026, 1, 5, tzinfo=UTC)),
        "baseRefName": "main",
    }
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([_repo_node("r1", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_merged_pr_page([old_pr])),
        ]
    )
    client = _client()
    try:
        active, _ = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active[0].has_merges_in_window is False


@pytest.mark.asyncio
@respx.mock
async def test_merge_check_early_terminates_on_updated_at() -> None:
    """If a PR page's last entry has updatedAt < since, the merge-check loop
    must stop without fetching the next page."""
    old_updated = {
        "mergedAt": _iso(datetime(2026, 1, 5, tzinfo=UTC)),
        "updatedAt": _iso(datetime(2026, 1, 5, tzinfo=UTC)),
        "baseRefName": "main",
    }
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([_repo_node("r1", datetime(2026, 5, 10, tzinfo=UTC))])),
            httpx.Response(200, json=_merged_pr_page([old_updated], has_next=True)),
            httpx.Response(500, text="should not paginate further"),
        ]
    )
    client = _client()
    try:
        active, _ = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active[0].has_merges_in_window is False
    # 1 org-list + 1 merge-check; no second page fetch
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_missing_default_branch_ref_defaults_to_main() -> None:
    node = {
        "name": "archived",
        "pushedAt": _iso(datetime(2026, 5, 10, tzinfo=UTC)),
        "defaultBranchRef": None,  # archived/empty repo
    }
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_org_page([node])),
            httpx.Response(200, json=_merged_pr_page([])),
        ]
    )
    client = _client()
    try:
        active, _ = await discover_active_repos(
            client, org="acme", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active[0].default_branch == "main"


@pytest.mark.asyncio
@respx.mock
async def test_empty_org_yields_empty_result() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(200, json=_org_page([]))
    )
    client = _client()
    try:
        active, scanned = await discover_active_repos(
            client, org="empty", since=SINCE, until=UNTIL
        )
    finally:
        await client.aclose()
    assert active == []
    assert scanned == 0


def test_discovered_repo_is_a_frozen_dataclass() -> None:
    d = DiscoveredRepo(
        owner="o",
        repo="r",
        pushed_at=datetime(2026, 5, 1, tzinfo=UTC),
        default_branch="main",
        has_merges_in_window=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.owner = "x"  # type: ignore[misc]
