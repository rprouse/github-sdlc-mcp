"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from github_sdlc_mcp.models import NormalizedPR
from github_sdlc_mcp.normalize import normalize_pr
from tests.fixtures import iter_pr_nodes


def multiplexed_prs(targets: list[tuple[str, str]]) -> list[NormalizedPR]:
    """Stamp the canonical per-repo PR fixture across multiple (owner, repo) pairs.

    The fixture in tests/fixtures/github_prs.json is calibrated for one
    repo (see CLAUDE.md "Synthetic fixture"). This helper replays it
    across N (owner, repo) pairs so org-level tests get a deterministic
    multi-repo input without regenerating the fixture.

    Output is len(targets) * <fixture-PR-count> NormalizedPR instances.
    """
    return [
        normalize_pr(node, owner=owner, repo=repo)
        for owner, repo in targets
        for node in iter_pr_nodes()
    ]
