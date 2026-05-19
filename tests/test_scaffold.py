"""Phase 1 smoke tests: package imports and CLI runs."""

from __future__ import annotations

import github_sdlc_mcp
from github_sdlc_mcp.__main__ import main


def test_version_attribute_present() -> None:
    assert github_sdlc_mcp.__version__ == "0.1.0"


def test_cli_version_flag(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "github-sdlc-mcp 0.1.0" in captured.out


def test_cli_no_args_prints_scaffold_notice(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "scaffold phase 1" in captured.err
