"""GitHub client: PAT auth, rate-limit-aware async HTTP, GraphQL pagination."""

from github_sdlc_mcp.client.auth import MissingTokenError, resolve_github_token
from github_sdlc_mcp.client.github import GitHubClient, make_github_client
from github_sdlc_mcp.client.rate_limit import (
    GitHubGraphQLError,
    RateLimitError,
    RateLimitState,
    TransientServerError,
)

__all__ = [
    "GitHubClient",
    "GitHubGraphQLError",
    "MissingTokenError",
    "RateLimitError",
    "RateLimitState",
    "TransientServerError",
    "make_github_client",
    "resolve_github_token",
]
