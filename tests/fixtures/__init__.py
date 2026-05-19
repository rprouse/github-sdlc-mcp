"""Synthetic GitHub-API fixture loaders. Shared by phases 5, 6, and 8 tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent

PR_PAGES_PATH = FIXTURE_DIR / "github_prs.json"
COMMIT_PAGES_PATH = FIXTURE_DIR / "github_commits.json"

# Window covered by the fixture (30 days ending the day Rob set up this repo).
FIXTURE_UNTIL = "2026-05-15"
FIXTURE_SINCE = "2026-04-15"
FIXTURE_OWNER = "acme"
FIXTURE_REPO = "web"
FIXTURE_DEFAULT_BRANCH = "main"


def load_pr_pages() -> list[dict[str, Any]]:
    """Return the list of GraphQL PR response pages (as raw dicts)."""
    with PR_PAGES_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    return data


def load_commit_pages() -> list[dict[str, Any]]:
    """Return the list of GraphQL commit-history response pages."""
    with COMMIT_PAGES_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    return data


def iter_pr_nodes() -> list[dict[str, Any]]:
    """Flatten all PR nodes across all pages — convenience for normalizer tests."""
    out: list[dict[str, Any]] = []
    for page in load_pr_pages():
        out.extend(page["data"]["repository"]["pullRequests"]["nodes"])
    return out


def iter_commit_nodes() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in load_commit_pages():
        out.extend(
            page["data"]["repository"]["ref"]["target"]["history"]["nodes"]
        )
    return out
