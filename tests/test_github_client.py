"""Tests for the async GitHub client (respx mocks the HTTP layer)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from github_sdlc_mcp.client.auth import MissingTokenError
from github_sdlc_mcp.client.github import (
    DEFAULT_BASE_URL,
    GitHubClient,
    make_github_client,
)
from github_sdlc_mcp.client.rate_limit import (
    GitHubGraphQLError,
    TransientServerError,
)

BASE_URL = DEFAULT_BASE_URL


def _client(**kwargs: Any) -> GitHubClient:
    defaults: dict[str, Any] = {
        "token": "ghp_test",
        "sleep": AsyncMock(),
        "backoff_base": 0.0,  # zero so even un-mocked sleeps would be instant
    }
    defaults.update(kwargs)
    return GitHubClient(**defaults)


# ---------------------------------------------------------------------------
# make_github_client factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_make_github_client_wires_token_into_authorization_header() -> None:
    """The factory must thread the env token through to the Bearer header."""
    route = respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"ok": True}})
    )
    client = make_github_client(env={"GITHUB_TOKEN": "ghp_from_env"})
    try:
        await client.graphql("query {}")
    finally:
        await client.aclose()
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_from_env"


def test_make_github_client_raises_when_token_missing() -> None:
    with pytest.raises(MissingTokenError):
        make_github_client(env={})


# ---------------------------------------------------------------------------
# Basic GraphQL transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_graphql_sends_correct_request_shape() -> None:
    route = respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"login": "octocat"}}})
    )
    client = _client()
    try:
        data = await client.graphql("query { viewer { login } }", {"x": 1})
    finally:
        await client.aclose()

    assert data == {"viewer": {"login": "octocat"}}
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer ghp_test"
    assert req.headers["User-Agent"].startswith("github-sdlc-mcp/")
    assert req.headers["Accept"] == "application/vnd.github+json"
    body = req.content.decode("utf-8")
    assert "viewer" in body
    assert "\"variables\"" in body


@pytest.mark.asyncio
@respx.mock
async def test_graphql_errors_raise_typed_exception() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": None, "errors": [{"message": "boom"}]}
        )
    )
    client = _client()
    try:
        with pytest.raises(GitHubGraphQLError) as exc:
            await client.graphql("query {}")
    finally:
        await client.aclose()
    assert exc.value.errors[0]["message"] == "boom"


@pytest.mark.asyncio
@respx.mock
async def test_rest_get_sends_correct_request() -> None:
    route = respx.get(f"{BASE_URL}/repos/o/r").mock(
        return_value=httpx.Response(200, json={"name": "r"})
    )
    client = _client()
    try:
        data = await client.rest_get("/repos/o/r", params={"q": "abc"})
    finally:
        await client.aclose()
    assert data == {"name": "r"}
    assert route.calls.last.request.url.params["q"] == "abc"


# ---------------------------------------------------------------------------
# Rate-limit accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_state_updated_from_headers() -> None:
    reset_epoch = int(time.time()) + 3600
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"ok": True}},
            headers={
                "x-ratelimit-remaining": "4500",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": str(reset_epoch),
            },
        )
    )
    client = _client()
    try:
        await client.graphql("query {}")
    finally:
        await client.aclose()

    assert client.rate_limit.remaining == 4500
    assert client.rate_limit.limit == 5000
    assert client.last_successful_call_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_proactive_pause_when_remaining_below_floor() -> None:
    """When cached remaining < floor, client sleeps until reset before next call."""
    reset_epoch = int(time.time()) + 60  # 60s from now
    # First response drops us below floor; second is the call we care about.
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"data": {"ok": True}},
                headers={
                    "x-ratelimit-remaining": "5",  # below floor of 100
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-reset": str(reset_epoch),
                },
            ),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    sleep = AsyncMock()
    client = _client(sleep=sleep, rate_limit_floor=100)
    try:
        await client.graphql("query {}")  # populates rate-limit state
        sleep.reset_mock()
        await client.graphql("query {}")  # should pause before sending
    finally:
        await client.aclose()

    assert sleep.await_count == 1
    awaited_delay = sleep.await_args_list[0].args[0]
    # Should be roughly 60s (with +1s buffer); allow generous slack for skew.
    assert 50.0 <= awaited_delay <= 70.0


@pytest.mark.asyncio
@respx.mock
async def test_no_proactive_pause_when_remaining_above_floor() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"ok": True}},
            headers={
                "x-ratelimit-remaining": "500",
                "x-ratelimit-limit": "5000",
                "x-ratelimit-reset": str(int(time.time()) + 3600),
            },
        )
    )
    sleep = AsyncMock()
    client = _client(sleep=sleep, rate_limit_floor=100)
    try:
        await client.graphql("query {}")
        await client.graphql("query {}")
    finally:
        await client.aclose()
    assert sleep.await_count == 0


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_secondary_rate_limit_retried_with_retry_after() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(
                403,
                text="You have exceeded a secondary rate limit. Please wait.",
                headers={"retry-after": "0.01"},
            ),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    sleep = AsyncMock()
    client = _client(sleep=sleep)
    try:
        data = await client.graphql("query {}")
    finally:
        await client.aclose()
    assert data == {"ok": True}
    # At least one sleep should be the ~0.01s retry-after (plus jitter < 0.5)
    delays = [call.args[0] for call in sleep.await_args_list]
    assert any(0.01 <= d < 0.6 for d in delays), delays


@pytest.mark.asyncio
@respx.mock
async def test_5xx_retried_then_succeeds() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(200, json={"data": {"ok": True}}),
        ]
    )
    client = _client()
    try:
        data = await client.graphql("query {}")
    finally:
        await client.aclose()
    assert data == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_5xx_exhausts_retries_and_raises() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    client = _client(max_retries=2)
    try:
        with pytest.raises(TransientServerError):
            await client.graphql("query {}")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_403_bad_credentials_not_retried() -> None:
    """A 403 that isn't a secondary rate limit is fatal — don't retry."""
    route = respx.post(f"{BASE_URL}/graphql").mock(
        return_value=httpx.Response(403, text="Bad credentials")
    )
    client = _client()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.graphql("query {}")
    finally:
        await client.aclose()
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

PAGINATED_QUERY = """
query($cursor: String) {
  organization(login: "acme") {
    repositories(first: 3, orderBy: {field: PUSHED_AT, direction: DESC}, after: $cursor) {
      nodes { name pushedAt }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _page(nodes: list[dict[str, Any]], has_next: bool, cursor: str | None) -> dict[str, Any]:
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


async def _collect(it: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for node in it:
        out.append(node)
    return out


@pytest.mark.asyncio
@respx.mock
async def test_pagination_stitches_pages() -> None:
    respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_page(
                [{"name": "a"}, {"name": "b"}, {"name": "c"}], True, "c1"
            )),
            httpx.Response(200, json=_page(
                [{"name": "d"}, {"name": "e"}, {"name": "f"}], True, "c2"
            )),
            httpx.Response(200, json=_page([{"name": "g"}], False, None)),
        ]
    )
    client = _client()
    try:
        nodes = await _collect(
            client.paginate_graphql(
                PAGINATED_QUERY,
                {},
                connection_path=["organization", "repositories"],
            )
        )
    finally:
        await client.aclose()
    assert [n["name"] for n in nodes] == ["a", "b", "c", "d", "e", "f", "g"]


@pytest.mark.asyncio
@respx.mock
async def test_pagination_early_terminate_stops_requests() -> None:
    """Caller break must stop further page fetches — critical for active-repo."""
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_page(
                [{"name": "a"}, {"name": "b"}, {"name": "c"}], True, "c1"
            )),
            # Second response would be returned if we paged again — but the
            # test breaks after node 2, so we should never see this call.
            httpx.Response(200, json=_page([{"name": "d"}], False, None)),
        ]
    )
    client = _client()
    seen: list[str] = []
    try:
        async for node in client.paginate_graphql(
            PAGINATED_QUERY,
            {},
            connection_path=["organization", "repositories"],
        ):
            seen.append(node["name"])
            if len(seen) >= 2:
                break
    finally:
        await client.aclose()
    assert seen == ["a", "b"]
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_pagination_passes_cursor_in_subsequent_requests() -> None:
    route = respx.post(f"{BASE_URL}/graphql").mock(
        side_effect=[
            httpx.Response(200, json=_page([{"name": "a"}], True, "CURSOR1")),
            httpx.Response(200, json=_page([{"name": "b"}], False, None)),
        ]
    )
    client = _client()
    try:
        await _collect(
            client.paginate_graphql(
                PAGINATED_QUERY,
                {},
                connection_path=["organization", "repositories"],
            )
        )
    finally:
        await client.aclose()
    assert route.call_count == 2
    first_body = route.calls[0].request.content.decode()
    second_body = route.calls[1].request.content.decode()
    assert "CURSOR1" not in first_body
    assert "CURSOR1" in second_body
