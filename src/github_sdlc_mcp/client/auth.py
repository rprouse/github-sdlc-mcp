"""PAT resolution from environment variables.

v0.2.0 collapses the per-host token lookup to a single env var read.
Cloud GitHub only — no GHES, no per-host overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


class MissingTokenError(Exception):
    """Raised when neither GITHUB_TOKEN nor GH_TOKEN is set in the env."""

    def __init__(self) -> None:
        super().__init__(
            "GitHub token not found: set GITHUB_TOKEN (or GH_TOKEN) "
            "in the environment and restart the MCP host."
        )


def resolve_github_token(env: Mapping[str, str] | None = None) -> str:
    """Return the GitHub PAT, preferring GITHUB_TOKEN over GH_TOKEN."""
    source = os.environ if env is None else env
    raw = source.get("GITHUB_TOKEN") or source.get("GH_TOKEN")
    if not raw:
        raise MissingTokenError()
    return raw
