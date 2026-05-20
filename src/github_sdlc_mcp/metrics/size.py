"""PR size metrics. Stubbed in v0.1.0 — implemented in v0.2 (Phase 9+)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.models import NormalizedPR, PRSizeStats

_STUB_MSG = "compute_pr_size_stats is stubbed; see docs/spec_v3.md §10."


def compute_pr_size_stats(
    *,
    org: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
) -> PRSizeStats:
    raise NotImplementedError(_STUB_MSG)
