"""Async GitHub HTTP client: GraphQL + REST + pagination + rate-limit aware.

One :class:`GitHubClient` owns one ``httpx.AsyncClient`` and serves the cloud
``api.github.com`` host. Construct via :func:`make_github_client`, which reads
``GITHUB_TOKEN`` (falling back to ``GH_TOKEN``) from the environment.

The client deliberately exposes a tiny surface: ``graphql``, ``paginate_graphql``,
and ``rest_get``. Query strings and response interpretation belong with the
normalizer and metric modules — keeping this layer schema-agnostic makes it
easy to swap GitHub for GitLab/ADO transports in the sibling servers.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from github_sdlc_mcp import __version__
from github_sdlc_mcp.client.auth import resolve_github_token
from github_sdlc_mcp.client.rate_limit import (
    EMPTY_RATE_LIMIT_STATE,
    GitHubGraphQLError,
    RateLimitError,
    RateLimitState,
    TransientServerError,
    is_secondary_rate_limit,
    parse_rate_limit_headers,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RATE_LIMIT_FLOOR = 100
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 0.5  # seconds; exponential
DEFAULT_BASE_URL = "https://api.github.com"


class GitHubClient:
    """Async client for cloud GitHub (``api.github.com``)."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        rate_limit_floor: int = DEFAULT_RATE_LIMIT_FLOOR,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = f"github-sdlc-mcp/{__version__}",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._rate_limit_floor = rate_limit_floor
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep or asyncio.sleep
        self._rate_limit: RateLimitState = EMPTY_RATE_LIMIT_STATE
        self._last_success_at: datetime | None = None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": user_agent,
                "Authorization": f"Bearer {token}",
            },
        )

    # ---- public properties ------------------------------------------------

    @property
    def rate_limit(self) -> RateLimitState:
        return self._rate_limit

    @property
    def last_successful_call_at(self) -> datetime | None:
        return self._last_success_at

    # ---- public methods ---------------------------------------------------

    async def graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """POST a GraphQL query. Returns the ``data`` field on success."""
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        body = await self._request_with_retry("POST", "/graphql", json=payload)
        if not isinstance(body, dict):
            raise GitHubGraphQLError(
                [{"message": f"unexpected non-dict GraphQL response: {body!r}"}]
            )
        if body.get("errors"):
            raise GitHubGraphQLError(list(body["errors"]))
        data = body.get("data")
        if not isinstance(data, dict):
            raise GitHubGraphQLError(
                [{"message": "GraphQL response missing 'data' field"}]
            )
        return data

    async def paginate_graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        connection_path: Sequence[str],
        cursor_var: str = "cursor",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield nodes from a GraphQL connection, walking ``pageInfo``.

        The query must accept ``$cursor: String`` and the connection at
        ``connection_path`` must expose ``nodes { ... }`` and
        ``pageInfo { hasNextPage endCursor }``.

        Callers can ``break`` out of the iteration to stop early — no
        further requests are issued (e.g. for active-repo PUSHED_AT
        traversal).
        """
        cursor: str | None = None
        while True:
            vars_with_cursor = {**variables, cursor_var: cursor}
            data = await self.graphql(query, vars_with_cursor)
            conn: Any = data
            for key in connection_path:
                if not isinstance(conn, dict) or key not in conn:
                    raise GitHubGraphQLError(
                        [
                            {
                                "message": f"connection_path {list(connection_path)} not found in response"
                            }
                        ]
                    )
                conn = conn[key]
            if not isinstance(conn, dict):
                raise GitHubGraphQLError(
                    [{"message": f"connection at {list(connection_path)} is not an object"}]
                )
            for node in conn.get("nodes") or []:
                yield node
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")

    async def rest_get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET a REST endpoint and return the parsed JSON body."""
        return await self._request_with_retry("GET", path, params=params)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- internals --------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            await self._maybe_pause_for_primary_limit()
            try:
                response = await self._client.request(
                    method, url, params=params, json=json
                )
            except httpx.TransportError as e:
                last_exc = TransientServerError(f"transport error: {e}")
                logger.warning(
                    "transport error on %s %s (attempt %d/%d): %s",
                    method,
                    url,
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )
                await self._backoff(attempt)
                continue

            self._update_rate_limit(response.headers)
            outcome = self._classify_response(response)

            if outcome == "ok":
                self._last_success_at = datetime.now(UTC)
                if not response.content:
                    return None
                return response.json()

            if outcome == "secondary_rate_limit":
                delay = retry_after_seconds(response.headers)
                last_exc = RateLimitError(retry_after=delay)
                logger.info(
                    "secondary rate limit on %s %s; sleeping %.2fs",
                    method,
                    url,
                    delay,
                )
                await self._sleep(delay + random.uniform(0, 0.5))
                continue

            if outcome == "transient":
                last_exc = TransientServerError(
                    f"{response.status_code} on {method} {url}: {response.text[:200]}"
                )
                logger.warning(
                    "transient error %d on %s %s (attempt %d/%d)",
                    response.status_code,
                    method,
                    url,
                    attempt + 1,
                    self._max_retries + 1,
                )
                await self._backoff(attempt)
                continue

            # outcome == "fatal" — auth, scope, validation errors, etc.
            raise httpx.HTTPStatusError(
                f"{response.status_code} on {method} {url}: {response.text[:500]}",
                request=response.request,
                response=response,
            )

        assert last_exc is not None
        raise last_exc

    async def _maybe_pause_for_primary_limit(self) -> None:
        state = self._rate_limit
        if state.remaining is None or state.remaining >= self._rate_limit_floor:
            return
        if state.resets_at is None:
            return
        delay = (state.resets_at - datetime.now(UTC)).total_seconds() + 1.0
        if delay <= 0:
            return
        logger.info(
            "primary rate limit low (remaining=%s); sleeping %.1fs until reset",
            state.remaining,
            delay,
        )
        await self._sleep(delay)

    async def _backoff(self, attempt: int) -> None:
        # Exponential backoff with full jitter.
        cap = self._backoff_base * (2**attempt)
        await self._sleep(random.uniform(0, cap))

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        self._rate_limit = parse_rate_limit_headers(
            {k.lower(): v for k, v in headers.items()}
        )

    @staticmethod
    def _classify_response(response: httpx.Response) -> str:
        status = response.status_code
        if 200 <= status < 300:
            return "ok"
        if status in (403, 429):
            if is_secondary_rate_limit(status, response.text):
                return "secondary_rate_limit"
            return "fatal"
        if 500 <= status < 600:
            return "transient"
        return "fatal"


def make_github_client(
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> GitHubClient:
    """Construct a single GitHubClient bound to api.github.com.

    ``transport`` is the respx injection point for tests. ``sleep`` is
    likewise injectable so rate-limit tests assert exact pause durations.
    The token is read from ``GITHUB_TOKEN`` (or ``GH_TOKEN`` as a fallback).
    """
    token = resolve_github_token(env)
    return GitHubClient(
        token=token,
        base_url=DEFAULT_BASE_URL,
        transport=transport,
        sleep=sleep,
    )
