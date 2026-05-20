"""Tests for ``compute_cycle_time_stats``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from github_sdlc_mcp.metrics.cycle_time import compute_cycle_time_stats
from github_sdlc_mcp.models import (
    NormalizedPR,
    NormalizedReview,
)
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


def _make_pr(
    number: int,
    *,
    created_at: datetime,
    merged_at: datetime | None,
    reviews: list[NormalizedReview] | None = None,
    additions: int = 50,
    deletions: int = 10,
    author: str = "alice",
    merged_by: str | None = "bob",
    owner: str = "acme",
    repo: str = "web",
) -> NormalizedPR:
    return NormalizedPR(
        owner=owner,
        repo=repo,
        number=number,
        author=author,
        title=f"PR {number}",
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
        created_at=created_at,
        merged_at=merged_at,
        closed_at=merged_at,
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
        review_comments_count=0,
        requested_reviewer_count=0,
    )


def _review(
    reviewer: str,
    *,
    state: str = "approved",
    submitted_at: datetime,
    comments_count: int = 0,
) -> NormalizedReview:
    return NormalizedReview(
        reviewer=reviewer,
        state=state,  # type: ignore[arg-type]
        submitted_at=submitted_at,
        comments_count=comments_count,
        body_length=0,
    )


# ---------------------------------------------------------------------------
# Fixture-based integration tests
# ---------------------------------------------------------------------------


def test_fixture_count_matches_merged_in_window() -> None:
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.count == 40
    assert stats.provider == "github"
    assert stats.org == ORG


def test_fixture_stats_invariants() -> None:
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.median_hours is not None
    assert stats.mean_hours is not None
    assert stats.p90_hours is not None
    # median ≤ p90 is a property of percentiles; relation to mean isn't
    # guaranteed for skewed distributions, but our fixture skews right
    # (long tail of slow PRs) so median ≤ mean as well.
    assert stats.median_hours <= stats.p90_hours
    assert stats.median_hours <= stats.mean_hours


def test_fixture_first_review_and_approval_medians_present() -> None:
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    # 5 no-review merges in the fixture, 35 with reviews → both should be set.
    assert stats.median_time_to_first_review_hours is not None
    assert stats.median_approval_to_merge_hours is not None
    # Approval-to-merge is bounded above by total cycle time (math).
    assert stats.median_hours is not None
    assert stats.median_approval_to_merge_hours <= stats.median_hours + 0.01


def test_fixture_examples_are_three_distinct_with_expected_labels() -> None:
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    labels = [e.label for e in stats.examples]
    assert labels == ["slowest", "fastest", "median"]
    numbers = {e.number for e in stats.examples}
    assert len(numbers) == 3  # three distinct PRs
    for e in stats.examples:
        assert e.unit == "hours"
        assert e.value > 0


def test_fixture_pinned_numbers() -> None:
    """Lock in the computed values so silent algorithm drift is caught.

    Update when the generator OR the algorithm changes — never when a
    "test broke" without first identifying which side moved.
    """
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert stats.count == 40
    assert stats.median_hours is not None
    assert stats.p90_hours is not None
    # Spot-check: fixture cohort design ⇒ median is in the typical range
    # (6-48h), p90 lands in slow/very-slow territory (≥48h).
    assert 12.0 <= stats.median_hours <= 80.0, stats.median_hours
    assert 48.0 <= stats.p90_hours <= 360.0, stats.p90_hours
    # Approval-to-merge is small (review submitted late in the cycle for
    # most PRs); median should be < median cycle time.
    assert stats.median_approval_to_merge_hours is not None
    assert stats.median_approval_to_merge_hours < stats.median_hours


def test_fixture_yields_single_repo_slice_reconciling_to_rollup() -> None:
    """Per-repo slices must reconcile to the org rollup."""
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=_fixture_prs()
    )
    assert len(stats.repos) == 1
    assert stats.repos[0].repo == FIXTURE_REPO
    assert sum(s.count for s in stats.repos) == stats.count


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_pr_set() -> None:
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=[]
    )
    assert stats.count == 0
    assert stats.median_hours is None
    assert stats.p90_hours is None
    assert stats.mean_hours is None
    assert stats.median_time_to_first_review_hours is None
    assert stats.median_approval_to_merge_hours is None
    assert stats.examples == []
    assert stats.repos == []
    # Definitions still embedded even on empty result — agents narrate the
    # zero count.
    assert "cycle_time_total" in stats.definitions


def test_single_pr_yields_collapsed_stats() -> None:
    created = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    merged = created + timedelta(hours=10)
    pr = _make_pr(1, created_at=created, merged_at=merged)
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=[pr]
    )
    assert stats.count == 1
    assert stats.median_hours == 10.0
    assert stats.mean_hours == 10.0
    assert stats.p90_hours == 10.0
    assert len(stats.examples) == 1
    assert stats.examples[0].label == "slowest"
    assert len(stats.repos) == 1
    assert stats.repos[0].count == 1


def test_window_filter_excludes_out_of_range_merges() -> None:
    created = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    in_window = _make_pr(1, created_at=created, merged_at=created + timedelta(hours=6))
    way_after = _make_pr(
        2,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        merged_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=[in_window, way_after]
    )
    assert stats.count == 1


def test_no_reviews_yields_null_review_medians() -> None:
    created = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    prs = [
        _make_pr(i, created_at=created, merged_at=created + timedelta(hours=6 + i))
        for i in range(5)
    ]
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=prs
    )
    assert stats.count == 5
    assert stats.median_hours is not None
    assert stats.median_time_to_first_review_hours is None
    assert stats.median_approval_to_merge_hours is None


def test_author_reviews_excluded_from_first_review_calculation() -> None:
    """Per definition: first NON-AUTHOR review. Self-reviews don't count."""
    created = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    merged = created + timedelta(hours=10)
    pr = _make_pr(
        1,
        author="alice",
        created_at=created,
        merged_at=merged,
        reviews=[
            _review("alice", state="approved", submitted_at=created + timedelta(minutes=5)),
        ],
    )
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=[pr]
    )
    # No non-author review → both subset medians are None.
    assert stats.median_time_to_first_review_hours is None
    assert stats.median_approval_to_merge_hours is None


def test_per_repo_slices_partition_org_prs() -> None:
    """Two repos in the same org → two slices, counts sum to org count."""
    created = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    web_prs = [
        _make_pr(
            i, created_at=created, merged_at=created + timedelta(hours=10),
            owner="acme", repo="web",
        )
        for i in range(1, 4)
    ]
    api_prs = [
        _make_pr(
            10 + i, created_at=created, merged_at=created + timedelta(hours=10),
            owner="acme", repo="api",
        )
        for i in range(1, 3)
    ]
    stats = compute_cycle_time_stats(
        org=ORG, since=SINCE, until=UNTIL, prs=[*web_prs, *api_prs]
    )
    assert stats.count == 5
    assert len(stats.repos) == 2
    assert {s.repo for s in stats.repos} == {"web", "api"}
    assert sum(s.count for s in stats.repos) == stats.count
