"""Portfolio / cross-repo summary. Stubbed in v0.1.0 — implemented in v0.2 (Phase 9+)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.models import NormalizedPR, PortfolioSummary

_STUB_MSG = "compute_portfolio_summary is stubbed; see docs/spec_v3.md §10."


def compute_portfolio_summary(
    *,
    org: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
    include_inactive: bool = False,
) -> PortfolioSummary:
    raise NotImplementedError(_STUB_MSG)
