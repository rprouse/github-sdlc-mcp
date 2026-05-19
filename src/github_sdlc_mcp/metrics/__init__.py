"""SDLC metric computations.

cycle_time and review_health are fully implemented in v0.1.0. The rest are
stub functions with matching signatures that raise ``NotImplementedError``;
they land in v0.2.
"""

from github_sdlc_mcp.metrics.baseline import compare_to_baseline
from github_sdlc_mcp.metrics.ci_health import compute_ci_health
from github_sdlc_mcp.metrics.cycle_time import compute_cycle_time_stats
from github_sdlc_mcp.metrics.merge_activity import compute_merge_activity
from github_sdlc_mcp.metrics.portfolio import compute_portfolio_summary
from github_sdlc_mcp.metrics.review_health import compute_review_health
from github_sdlc_mcp.metrics.size import compute_pr_size_stats
from github_sdlc_mcp.metrics.stale import compute_stale_prs

__all__ = [
    "compare_to_baseline",
    "compute_ci_health",
    "compute_cycle_time_stats",
    "compute_merge_activity",
    "compute_portfolio_summary",
    "compute_pr_size_stats",
    "compute_review_health",
    "compute_stale_prs",
]
