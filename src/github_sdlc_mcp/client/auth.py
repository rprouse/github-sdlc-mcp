"""PAT resolution from environment variables.

Per spec v2 §"Authentication", GitHub App auth is not implemented in-process.
Tokens (whether PATs or externally-minted App installation tokens) are read
from the env var named by each host's ``token_env`` setting.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from github_sdlc_mcp.config import GitHubHost


class MissingTokenError(Exception):
    """Raised when the env var named by ``token_env`` is unset or empty."""

    def __init__(self, env_var: str, host_label: str) -> None:
        super().__init__(
            f"GitHub token for host '{host_label}' is not set: "
            f"environment variable {env_var!r} is missing or empty. "
            f"Export the variable and restart the MCP host."
        )
        self.env_var = env_var
        self.host_label = host_label


def resolve_token(
    host_cfg: GitHubHost,
    *,
    host_label: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the PAT for ``host_cfg`` from the configured environment variable.

    ``host_label`` is the YAML key (e.g. ``"cloud"`` or ``"acme_enterprise"``) and
    is used only for error messages — the underlying secret is never logged.
    """
    source = os.environ if env is None else env
    raw = source.get(host_cfg.token_env, "")
    if not raw:
        raise MissingTokenError(host_cfg.token_env, host_label)
    return raw
