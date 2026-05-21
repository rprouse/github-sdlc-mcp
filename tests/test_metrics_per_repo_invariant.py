"""Cross-cutting invariant: per-repo slices reconcile to the org rollup."""

from __future__ import annotations

from datetime import date

import pytest

from github_sdlc_mcp.metrics.cycle_time import compute_cycle_time_stats
from github_sdlc_mcp.metrics.review_health import compute_review_health
from tests.conftest import multiplexed_prs
from tests.fixtures import FIXTURE_SINCE, FIXTURE_UNTIL

SINCE = date.fromisoformat(FIXTURE_SINCE)
UNTIL = date.fromisoformat(FIXTURE_UNTIL)
TARGETS = [("acme", "web"), ("acme", "api"), ("acme", "lib")]


@pytest.mark.parametrize(
    "compute",
    [
        lambda prs: compute_cycle_time_stats(
            org="acme", since=SINCE, until=UNTIL, prs=prs
        ),
        lambda prs: compute_review_health(
            org="acme",
            since=SINCE,
            until=UNTIL,
            prs=prs,
            fast_approval_min_lines=200,
            fast_approval_max_seconds=300.0,
        ),
    ],
    ids=["cycle_time", "review_health"],
)
def test_per_repo_counts_sum_to_rollup(compute):
    prs = multiplexed_prs(TARGETS)
    result = compute(prs)

    repo_total = sum(slice_.count for slice_ in result.repos)
    assert repo_total == result.count
    assert {slice_.repo for slice_ in result.repos} == {t[1] for t in TARGETS}
