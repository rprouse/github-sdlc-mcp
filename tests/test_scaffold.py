"""Package-level smoke tests."""

from __future__ import annotations

import github_sdlc_mcp


def test_version_attribute_present() -> None:
    assert github_sdlc_mcp.__version__ == "0.1.0"
