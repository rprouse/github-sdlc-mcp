"""PR cycle-time statistics over a window of merged PRs across an org."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import groupby

from github_sdlc_mcp.metrics._stats import (
    example_for_pr,
    mean_or_none,
    median_or_none,
    merged_in_window,
    percentile,
    round2,
)
from github_sdlc_mcp.metrics.definitions import definitions_for
from github_sdlc_mcp.models import (
    CycleTimeRepoSlice,
    ExamplePR,
    NormalizedPR,
    PRCycleTimeStats,
)

_DEFINITION_KEYS = (
    "cycle_time_total",
    "cycle_time_first_review",
    "cycle_time_approval_to_merge",
)


@dataclass
class _RollupValues:
    count: int
    median: float | None
    p90: float | None
    mean: float | None
    median_first_review: float | None
    median_approval_to_merge: float | None
    cycle_hours_by_number: dict[int, float] = field(default_factory=dict)

def compute_cycle_time_stats(
    *,
    org: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
) -> PRCycleTimeStats:
    """Cycle-time statistics over PRs merged within ``[since, until]`` (UTC).

    Computes both the org-level rollup and a per-repo breakdown.
    """
    merged = [pr for pr in prs if merged_in_window(pr, since, until)]
    defs = definitions_for(*_DEFINITION_KEYS)

    if not merged:
        return PRCycleTimeStats(
            org=org,
            since=since,
            until=until,
            count=0,
            median_hours=None,
            p90_hours=None,
            mean_hours=None,
            median_time_to_first_review_hours=None,
            median_approval_to_merge_hours=None,
            examples=[],
            repos=[],
            definitions=defs,
        )

    repos = _per_repo_slices(merged)
    rollup = _aggregate(merged)

    return PRCycleTimeStats(
        org=org,
        since=since,
        until=until,
        count=rollup.count,
        median_hours=round2(rollup.median),
        p90_hours=round2(rollup.p90),
        mean_hours=round2(rollup.mean),
        median_time_to_first_review_hours=round2(rollup.median_first_review),
        median_approval_to_merge_hours=round2(rollup.median_approval_to_merge),
        examples=_examples(merged, rollup.cycle_hours_by_number, rollup.median),
        repos=repos,
        definitions=defs,
    )


def _aggregate(merged: list[NormalizedPR]) -> _RollupValues:
    cycle_hours = {pr.number: _cycle_hours(pr) for pr in merged}
    first_review = [
        h for pr in merged if (h := _first_non_author_review_hours(pr)) is not None
    ]
    approval_to_merge = [
        h for pr in merged if (h := _approval_to_merge_hours(pr)) is not None
    ]
    values = list(cycle_hours.values())
    p90 = percentile(values, 0.9) if len(values) > 1 else values[0]
    return _RollupValues(
        count=len(merged),
        median=median_or_none(values),
        p90=p90,
        mean=mean_or_none(values),
        median_first_review=median_or_none(first_review),
        median_approval_to_merge=median_or_none(approval_to_merge),
        cycle_hours_by_number=cycle_hours,
    )


def _per_repo_slices(merged: list[NormalizedPR]) -> list[CycleTimeRepoSlice]:
    slices: list[CycleTimeRepoSlice] = []
    by_repo = sorted(merged, key=lambda p: p.repo)
    for repo, group_iter in groupby(by_repo, key=lambda p: p.repo):
        group = list(group_iter)
        rollup = _aggregate(group)
        slices.append(
            CycleTimeRepoSlice(
                repo=repo,
                count=rollup.count,
                median_hours=round2(rollup.median),
                p90_hours=round2(rollup.p90),
                mean_hours=round2(rollup.mean),
                median_time_to_first_review_hours=round2(rollup.median_first_review),
                median_approval_to_merge_hours=round2(rollup.median_approval_to_merge),
            )
        )
    return slices


# ---------------------------------------------------------------------------
# Per-PR helpers
# ---------------------------------------------------------------------------


def _cycle_hours(pr: NormalizedPR) -> float:
    assert pr.merged_at is not None  # merged_in_window filtered for this
    return (pr.merged_at - pr.created_at).total_seconds() / 3600.0


def _first_non_author_review_at(pr: NormalizedPR) -> datetime | None:
    candidates = [
        r.submitted_at
        for r in pr.reviews
        if r.reviewer != pr.author and r.submitted_at is not None
    ]
    return min(candidates) if candidates else None


def _first_non_author_review_hours(pr: NormalizedPR) -> float | None:
    at = _first_non_author_review_at(pr)
    if at is None:
        return None
    return (at - pr.created_at).total_seconds() / 3600.0


def _first_approving_review_at(pr: NormalizedPR) -> datetime | None:
    candidates = [
        r.submitted_at
        for r in pr.reviews
        if (
            r.state == "approved"
            and r.reviewer != pr.author
            and r.submitted_at is not None
        )
    ]
    return min(candidates) if candidates else None


def _approval_to_merge_hours(pr: NormalizedPR) -> float | None:
    approved_at = _first_approving_review_at(pr)
    if approved_at is None or pr.merged_at is None:
        return None
    delta = (pr.merged_at - approved_at).total_seconds() / 3600.0
    return max(delta, 0.0)


def _examples(
    merged: list[NormalizedPR],
    cycle_hours: dict[int, float],
    median: float | None,
) -> list[ExamplePR]:
    """Up to 3 examples: slowest, fastest, and closest-to-median."""
    if not merged:
        return []
    examples: list[ExamplePR] = []
    by_pr = {pr.number: pr for pr in merged}

    # slowest = max cycle_hours (ties broken by lowest PR number)
    slowest_num = max(cycle_hours, key=lambda n: (cycle_hours[n], -n))
    examples.append(
        example_for_pr(
            by_pr[slowest_num],
            label="slowest",
            value=cycle_hours[slowest_num],
            unit="hours",
        )
    )

    # fastest = min cycle_hours
    fastest_num = min(cycle_hours, key=lambda n: (cycle_hours[n], n))
    if fastest_num != slowest_num:
        examples.append(
            example_for_pr(
                by_pr[fastest_num],
                label="fastest",
                value=cycle_hours[fastest_num],
                unit="hours",
            )
        )

    # closest to median, excluding already-picked PRs
    if median is not None:
        used = {slowest_num, fastest_num}
        remaining = [n for n in cycle_hours if n not in used]
        if remaining:
            median_num = min(
                remaining,
                key=lambda n: (abs(cycle_hours[n] - median), n),
            )
            examples.append(
                example_for_pr(
                    by_pr[median_num],
                    label="median",
                    value=cycle_hours[median_num],
                    unit="hours",
                )
            )

    return examples
