"""Portfolio / cross-repo summary. Stubbed in v0.1.0 — implemented in v0.2."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from github_sdlc_mcp.models import (
    CIHealthStats,
    NormalizedPR,
    PortfolioSummary,
    PRCycleTimeStats,
    ReviewHealthStats,
    StalePRList,
)

_STUB_MSG = "compute_portfolio_summary is stubbed in v0.1.0; see docs/spec_v2.md §10."


def compute_portfolio_summary(
    *,
    since: date,
    until: date,
    repo_prs: Mapping[str, list[NormalizedPR]],
    cycle_time_by_repo: Mapping[str, PRCycleTimeStats] | None = None,
    review_health_by_repo: Mapping[str, ReviewHealthStats] | None = None,
    ci_health_by_repo: Mapping[str, CIHealthStats] | None = None,
    stale_by_repo: Mapping[str, StalePRList] | None = None,
    company_filter: str | None = None,
    include_inactive: bool = False,
) -> PortfolioSummary:
    raise NotImplementedError(_STUB_MSG)
