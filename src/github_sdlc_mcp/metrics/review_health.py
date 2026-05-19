"""PR review-health metrics over a window of merged PRs.

Per spec v2 §4, the fast-approval detector uses configurable thresholds. The
defaults live in :mod:`metrics.definitions`; callers (CLI, tests) can override
to verify the knob wires through.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.metrics._stats import (
    example_for_pr,
    median_or_none,
    merged_in_window,
    round2,
)
from github_sdlc_mcp.metrics.definitions import (
    FAST_APPROVAL_MAX_SECONDS_DEFAULT,
    FAST_APPROVAL_MIN_LINES_DEFAULT,
    definitions_for,
)
from github_sdlc_mcp.models import ExamplePR, NormalizedPR, ReviewHealthStats

_DEFINITION_KEYS = (
    "review_reviewers_per_pr",
    "review_no_review",
    "review_only_author",
    "review_comments_per_pr",
    "review_fast_approval",
    "review_self_merge",
)


def compute_review_health(
    *,
    repo: str,
    since: date,
    until: date,
    prs: Iterable[NormalizedPR],
    fast_approval_min_lines: int = FAST_APPROVAL_MIN_LINES_DEFAULT,
    fast_approval_max_seconds: float = FAST_APPROVAL_MAX_SECONDS_DEFAULT,
) -> ReviewHealthStats:
    """Review-health metrics over PRs merged within ``[since, until]`` (UTC)."""
    merged = [pr for pr in prs if merged_in_window(pr, since, until)]
    defs = definitions_for(*_DEFINITION_KEYS)

    if not merged:
        return ReviewHealthStats(
            repo=repo,
            since=since,
            until=until,
            count=0,
            median_reviewers_per_pr=None,
            pct_merged_without_review=None,
            pct_merged_with_only_author_review=None,
            median_comments_per_pr=None,
            fast_approval_count=0,
            self_merge_count=0,
            examples=[],
            definitions=defs,
        )

    n = len(merged)
    reviewer_counts: list[int] = []
    no_review: list[NormalizedPR] = []
    only_author: list[NormalizedPR] = []
    fast_approvals: list[NormalizedPR] = []
    self_merges: list[NormalizedPR] = []

    for pr in merged:
        non_author = {r.reviewer for r in pr.reviews if r.reviewer != pr.author}
        reviewer_counts.append(len(non_author))
        if not non_author:
            no_review.append(pr)
        if pr.reviews and all(r.reviewer == pr.author for r in pr.reviews):
            only_author.append(pr)
        if _is_fast_approval(
            pr,
            min_lines=fast_approval_min_lines,
            max_seconds=fast_approval_max_seconds,
        ):
            fast_approvals.append(pr)
        if pr.merged_by is not None and pr.merged_by == pr.author:
            self_merges.append(pr)

    def _pct(items: list[NormalizedPR]) -> float | None:
        return round2(100.0 * len(items) / n)

    return ReviewHealthStats(
        repo=repo,
        since=since,
        until=until,
        count=n,
        median_reviewers_per_pr=round2(median_or_none(reviewer_counts)),
        pct_merged_without_review=_pct(no_review),
        pct_merged_with_only_author_review=_pct(only_author),
        median_comments_per_pr=round2(
            median_or_none(pr.review_comments_count for pr in merged)
        ),
        fast_approval_count=len(fast_approvals),
        self_merge_count=len(self_merges),
        examples=_examples(merged, no_review, fast_approvals),
        definitions=defs,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_fast_approval(
    pr: NormalizedPR, *, min_lines: int, max_seconds: float
) -> bool:
    """Spec v2 §4: large PR + non-author approval submitted soon after open."""
    if pr.additions + pr.deletions <= min_lines:
        return False
    for r in pr.reviews:
        if r.state != "approved":
            continue
        if r.reviewer == pr.author:
            continue
        if r.submitted_at is None:
            continue
        delta = (r.submitted_at - pr.created_at).total_seconds()
        if 0 <= delta <= max_seconds:
            return True
    return False


def _fast_approval_seconds(pr: NormalizedPR) -> float | None:
    """Seconds from PR open to first non-author approval. None if not present."""
    for r in pr.reviews:
        if r.state == "approved" and r.reviewer != pr.author and r.submitted_at is not None:
            return max(0.0, (r.submitted_at - pr.created_at).total_seconds())
    return None


def _examples(
    merged: list[NormalizedPR],
    no_review: list[NormalizedPR],
    fast_approvals: list[NormalizedPR],
) -> list[ExamplePR]:
    examples: list[ExamplePR] = []

    if no_review:
        pr = min(no_review, key=lambda p: p.number)
        examples.append(
            example_for_pr(pr, label="no_review", value=0, unit="count")
        )

    if fast_approvals:
        pr = min(fast_approvals, key=lambda p: p.number)
        seconds = _fast_approval_seconds(pr)
        examples.append(
            example_for_pr(
                pr,
                label="fast_approval",
                value=(seconds / 3600.0) if seconds is not None else 0.0,
                unit="hours",
            )
        )

    # deep_review: highest review_comments_count, must be > 0
    with_comments = [pr for pr in merged if pr.review_comments_count > 0]
    if with_comments:
        pr = max(with_comments, key=lambda p: (p.review_comments_count, -p.number))
        examples.append(
            example_for_pr(
                pr,
                label="deep_review",
                value=pr.review_comments_count,
                unit="comments",
            )
        )

    return examples
