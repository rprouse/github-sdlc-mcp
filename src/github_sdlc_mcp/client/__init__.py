"""GitHub client: PAT auth, rate-limit-aware async HTTP, GraphQL pagination."""

from github_sdlc_mcp.client.auth import MissingTokenError, resolve_token
from github_sdlc_mcp.client.github import GitHubClient, GitHubClientPool
from github_sdlc_mcp.client.rate_limit import (
    GitHubGraphQLError,
    RateLimitError,
    RateLimitState,
    TransientServerError,
)

__all__ = [
    "GitHubClient",
    "GitHubClientPool",
    "GitHubGraphQLError",
    "MissingTokenError",
    "RateLimitError",
    "RateLimitState",
    "TransientServerError",
    "resolve_token",
]
