"""Tests for ``compute_review_health``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from github_sdlc_mcp.metrics import compute_ci_health
from github_sdlc_mcp.metrics.review_health import compute_review_health
from github_sdlc_mcp.models import NormalizedPR, NormalizedReview
from github_sdlc_mcp.normalize import normalize_pr
from tests.fixtures import (
    FIXTURE_OWNER,
    FIXTURE_REPO,
    FIXTURE_SINCE,
    FIXTURE_UNTIL,
    iter_pr_nodes,
)

SINCE = date.fromisoformat(FIXTURE_SINCE)
UNTIL = date.fromisoformat(FIXTURE_UNTIL)
ORG = FIXTURE_OWNER


def _fixture_prs() -> list[NormalizedPR]:
    return [
        normalize_pr(n, owner=FIXTURE_OWNER, repo=FIXTURE_REPO)
        for n in iter_pr_nodes()
    ]


def _pr(
    number: int,
    *,
    author: str = "alice",
    merged_by: str | None = "bob",
    additions: int = 50,
    deletions: int = 10,
    review_comments_count: int = 0,
    reviews: list[NormalizedReview] | None = None,
    created_at: datetime | None = None,
    merged_at: datetime | None = None,
    owner: str = "acme",
    repo: str = "web",
) -> NormalizedPR:
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    return NormalizedPR(
        owner=owner,
        repo=repo,
        number=number,
        author=author,
        title=f"PR {number}",
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
        created_at=created_at or base,
        merged_at=merged_at or base + timedelta(hours=10),
        closed_at=merged_at or base + timedelta(hours=10),
        first_commit_at=None,
        additions=additions,
        deletions=deletions,
        changed_files=3,
        base_branch="main",
        head_sha="sha",
        is_draft=False,
        labels=[],
        merged_by=merged_by,
        reviews=reviews or [],
        checks=[],
        comments_count=0,
        review_comments_count=review_comments_count,
        requested_reviewer_count=0,
    )


def _rev(
    reviewer: str,
    *,
    state: str = "approved",
    submitted_at: datetime,
    comments: int = 0,
) -> NormalizedReview:
    return NormalizedReview(
        reviewer=reviewer,
        state=state,  # type: ignore[arg-type]
        submitted_at=submitted_at,
        comments_count=comments,
        body_length=0,
    )


# ---------------------------------------------------------------------------
# Fixture-based integration
# ---------------------------------------------------------------------------


def test_fixture_count_matches_merged() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.count == 40
    assert stats.provider == "github"
    assert stats.org == ORG


def test_fixture_no_review_floor_matches_overlay() -> None:
    """Fixture has 5 no-review merges; pct = 5/40 = 12.5%."""
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.pct_merged_without_review is not None
    assert stats.pct_merged_without_review >= 12.5 - 0.01


def test_fixture_fast_approval_count_meets_overlay() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.fast_approval_count >= 3


def test_fixture_self_merge_count_meets_overlay() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.self_merge_count >= 2


def test_fixture_examples_present() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    labels = {e.label for e in stats.examples}
    # All three overlays are exercised by the fixture.
    assert "no_review" in labels
    assert "fast_approval" in labels
    assert "deep_review" in labels


def test_fixture_per_repo_slices_reconcile_to_rollup() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert len(stats.repos) == 1
    assert stats.repos[0].repo == FIXTURE_REPO
    assert sum(s.count for s in stats.repos) == stats.count
    # Per-repo counts of fast_approval / self_merge sum to org-level counts.
    assert sum(s.fast_approval_count for s in stats.repos) == stats.fast_approval_count
    assert sum(s.self_merge_count for s in stats.repos) == stats.self_merge_count


# ---------------------------------------------------------------------------
# Threshold-knob tests (spec v2 §4)
# ---------------------------------------------------------------------------


def test_fast_approval_max_seconds_knob_can_zero_out_count() -> None:
    """Tightening the time threshold to 1 second must remove all detections."""
    stats = compute_review_health(
        org=ORG,
        since=SINCE,
        until=UNTIL,
        prs=_fixture_prs(),
        fast_approval_max_seconds=1,
    )
    assert stats.fast_approval_count == 0


def test_fast_approval_min_lines_knob_excludes_small_prs() -> None:
    """Raising the lines threshold above the largest PR yields zero matches."""
    stats = compute_review_health(
        org=ORG,
        since=SINCE,
        until=UNTIL,
        prs=_fixture_prs(),
        fast_approval_min_lines=100_000,
    )
    assert stats.fast_approval_count == 0


# ---------------------------------------------------------------------------
# Definition correctness
# ---------------------------------------------------------------------------


def test_self_only_review_counts_in_both_no_review_and_only_author() -> None:
    """A PR whose only review is from the author counts toward both metrics
    by design — definition strings are explicit about this."""
    created = datetime(2026, 5, 1, tzinfo=UTC)
    pr = _pr(
        1,
        author="alice",
        reviews=[
            _rev("alice", state="approved", submitted_at=created + timedelta(hours=1)),
        ],
    )
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=[pr]
    )
    assert stats.pct_merged_without_review == 100.0  # zero non-author reviews
    assert stats.pct_merged_with_only_author_review == 100.0


def test_non_author_review_excludes_pr_from_no_review_bucket() -> None:
    created = datetime(2026, 5, 1, tzinfo=UTC)
    pr = _pr(
        1,
        author="alice",
        reviews=[
            _rev("bob", state="approved", submitted_at=created + timedelta(hours=1)),
        ],
    )
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=[pr]
    )
    assert stats.pct_merged_without_review == 0.0
    assert stats.pct_merged_with_only_author_review == 0.0


def test_empty_pr_set() -> None:
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=[]
    )
    assert stats.count == 0
    assert stats.median_reviewers_per_pr is None
    assert stats.pct_merged_without_review is None
    assert stats.pct_merged_with_only_author_review is None
    assert stats.median_comments_per_pr is None
    assert stats.fast_approval_count == 0
    assert stats.self_merge_count == 0
    assert stats.examples == []
    assert stats.repos == []
    # Definitions still embedded.
    assert "review_no_review" in stats.definitions


def test_median_comments_uses_review_comments_count() -> None:
    """Should use line-level review_comments_count, not PR-level comments_count."""
    created = datetime(2026, 5, 1, tzinfo=UTC)
    prs = [
        _pr(1, review_comments_count=3, created_at=created),
        _pr(2, review_comments_count=10, created_at=created),
        _pr(3, review_comments_count=5, created_at=created),
    ]
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=prs
    )
    assert stats.median_comments_per_pr == 5.0


def test_self_merge_detection_requires_merged_by_set() -> None:
    created = datetime(2026, 5, 1, tzinfo=UTC)
    self_pr = _pr(1, author="alice", merged_by="alice", created_at=created)
    other_pr = _pr(2, author="alice", merged_by="bob", created_at=created)
    no_info = _pr(3, author="alice", merged_by=None, created_at=created)
    stats = compute_review_health(
        org=ORG, since=SINCE, until=UNTIL, prs=[self_pr, other_pr, no_info]
    )
    assert stats.self_merge_count == 1


# ---------------------------------------------------------------------------
# Stub modules still raise — confirms the v0.2 scope is what spec v3 §10 says
# ---------------------------------------------------------------------------


def test_stubbed_metrics_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        compute_ci_health(
            org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
        )
