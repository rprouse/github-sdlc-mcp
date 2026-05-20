"""Merge / revert activity metrics. Stubbed in v0.1.0 — implemented in v0.2 (Phase 9+)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.models import MergeActivityStats, NormalizedCommit, NormalizedPR

_STUB_MSG = "compute_merge_activity is stubbed; see docs/spec_v3.md §10."


def compute_merge_activity(
    *,
    org: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
    default_branch_commits: Iterable[NormalizedCommit],
) -> MergeActivityStats:
    raise NotImplementedError(_STUB_MSG)
