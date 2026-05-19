"""Tests for PAT resolution from environment variables."""

from __future__ import annotations

import pytest
from pydantic import AnyHttpUrl

from github_sdlc_mcp.client.auth import MissingTokenError, resolve_token
from github_sdlc_mcp.config import GitHubHost


def _host(token_env: str = "GITHUB_TOKEN") -> GitHubHost:
    return GitHubHost(
        base_url=AnyHttpUrl("https://api.github.com"),
        token_env=token_env,
    )


def test_token_present_returned() -> None:
    token = resolve_token(_host(), host_label="cloud", env={"GITHUB_TOKEN": "ghp_abc"})
    assert token == "ghp_abc"


def test_missing_env_var_raises_with_name() -> None:
    with pytest.raises(MissingTokenError) as exc:
        resolve_token(_host("GITHUB_TOKEN_ACME"), host_label="acme", env={})
    assert "GITHUB_TOKEN_ACME" in str(exc.value)
    assert "acme" in str(exc.value)


def test_empty_string_treated_as_missing() -> None:
    with pytest.raises(MissingTokenError):
        resolve_token(_host(), host_label="cloud", env={"GITHUB_TOKEN": ""})


def test_token_value_never_in_error_message() -> None:
    """Make sure tokens never leak into error text via __str__ or args."""
    secret = "ghp_super_secret_value_DO_NOT_LEAK"
    with pytest.raises(MissingTokenError) as exc:
        resolve_token(
            _host("OTHER"),
            host_label="cloud",
            env={"GITHUB_TOKEN": secret},  # not the var we look up
        )
    msg = str(exc.value)
    assert secret not in msg
    assert "OTHER" in msg


def test_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-os-environ")
    assert resolve_token(_host(), host_label="cloud") == "from-os-environ"
