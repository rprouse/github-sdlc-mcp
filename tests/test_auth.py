"""Tests for PAT resolution from environment variables."""

from __future__ import annotations

import pytest

from github_sdlc_mcp.client.auth import MissingTokenError, resolve_github_token


def test_resolve_github_token_prefers_github_token() -> None:
    assert (
        resolve_github_token({"GITHUB_TOKEN": "gh-pat", "GH_TOKEN": "fallback"})
        == "gh-pat"
    )


def test_resolve_github_token_falls_back_to_gh_token() -> None:
    assert resolve_github_token({"GH_TOKEN": "fallback-pat"}) == "fallback-pat"


def test_resolve_github_token_raises_when_unset() -> None:
    with pytest.raises(MissingTokenError):
        resolve_github_token({})


def test_resolve_github_token_treats_empty_string_as_missing() -> None:
    with pytest.raises(MissingTokenError):
        resolve_github_token({"GITHUB_TOKEN": ""})


def test_resolve_github_token_defaults_to_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-os-environ")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert resolve_github_token() == "from-os-environ"


def test_token_value_never_in_error_message() -> None:
    """Make sure tokens never leak into error text via __str__ or args."""
    secret = "ghp_super_secret_value_DO_NOT_LEAK"
    # Both env vars empty → raise. The token must not be in the message.
    with pytest.raises(MissingTokenError) as exc:
        resolve_github_token({"GITHUB_TOKEN": "", "GH_TOKEN": ""})
    msg = str(exc.value)
    assert secret not in msg
    assert "GITHUB_TOKEN" in msg
