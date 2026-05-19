"""PR size metrics. Stubbed in v0.1.0 — implemented in v0.2."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.models import NormalizedPR, PRSizeStats

_STUB_MSG = "compute_pr_size_stats is stubbed in v0.1.0; see docs/spec_v2.md §10."


def compute_pr_size_stats(
    *,
    repo: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
) -> PRSizeStats:
    raise NotImplementedError(_STUB_MSG)
