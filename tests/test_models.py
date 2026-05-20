"""Contract tests for response models and metric definition strings.

These tests freeze the cross-server contract that sibling SDLC servers
(``gitlab-sdlc-mcp``, ``azdo-sdlc-mcp``) will mirror. Failures here mean
the shared shape changed — that should be an explicit, reviewed decision.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TypeVar, cast

import pytest
from pydantic import ValidationError

from github_sdlc_mcp import models
from github_sdlc_mcp.metrics.definitions import (
    DEFINITIONS,
    definitions_for,
)
from github_sdlc_mcp.models import (
    ActiveRepo,
    ActiveRepoList,
    AggregateResponse,
    BaselineComparison,
    CacheClearResult,
    CIHealthRepoSlice,
    CIHealthStats,
    CycleTimeRepoSlice,
    ExamplePR,
    FailingCheck,
    HealthStatus,
    MergeActivityRepoSlice,
    MergeActivityStats,
    NormalizedCheckRun,
    NormalizedCommit,
    NormalizedPR,
    NormalizedReview,
    PortfolioRepoEntry,
    PortfolioSummary,
    PRCycleTimeStats,
    ProviderResponse,
    PRSizeBuckets,
    PRSizeRepoSlice,
    PRSizeStats,
    ReviewHealthRepoSlice,
    ReviewHealthStats,
    StalePR,
    StalePRList,
    StaleRepoSlice,
    WeeklyBucket,
)

NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
SINCE = date(2026, 4, 1)
UNTIL = date(2026, 5, 1)


# ---------------------------------------------------------------------------
# Helpers / inventories
# ---------------------------------------------------------------------------


def _example(label: str = "slowest", value: float = 42.0, unit: str = "hours") -> ExamplePR:
    return ExamplePR(
        number=1,
        title="Test PR",
        author="alice",
        url="https://github.com/o/r/pull/1",
        label=label,
        value=value,
        unit=unit,
    )


def _all_response_models() -> list[type[ProviderResponse]]:
    """Every concrete ProviderResponse subclass we ship."""
    return [
        ActiveRepoList,
        HealthStatus,
        PRCycleTimeStats,
        PRSizeStats,
        ReviewHealthStats,
        CIHealthStats,
        StalePRList,
        MergeActivityStats,
        PortfolioSummary,
        BaselineComparison,
        CacheClearResult,
    ]


def _all_aggregate_models() -> list[type[AggregateResponse]]:
    return [m for m in _all_response_models() if issubclass(m, AggregateResponse)]


T = TypeVar("T", bound=ProviderResponse)


def _sample_instance(cls: type[T]) -> T:
    """Build a minimal valid instance of each model for serialization tests."""
    if cls is ActiveRepoList:
        return cast(T, ActiveRepoList(
            org="acme",
            since=SINCE,
            until=UNTIL,
            total_repos_scanned=5,
            total_repos_active=3,
            repos=[
                ActiveRepo(
                    owner="acme",
                    repo="r",
                    pushed_at=NOW,
                    default_branch="main",
                    has_merges_in_window=True,
                )
            ],
            definitions=definitions_for(
                "active_repo_pushed_at", "active_repo_has_merges"
            ),
        ))
    if cls is HealthStatus:
        return cast(T, HealthStatus(
            ok=True,
            rate_limit_remaining=4500,
            rate_limit_resets_at=NOW,
            last_successful_call_at=NOW,
            cache_entries=0,
        ))
    if cls is PRCycleTimeStats:
        return cast(T, PRCycleTimeStats(
            org="acme",
            since=SINCE,
            until=UNTIL,
            count=10,
            median_hours=12.0,
            p90_hours=48.0,
            mean_hours=18.5,
            median_time_to_first_review_hours=2.0,
            median_approval_to_merge_hours=4.0,
            examples=[_example()],
            repos=[
                CycleTimeRepoSlice(
                    repo="web",
                    count=10,
                    median_hours=12.0,
                    p90_hours=48.0,
                    mean_hours=18.5,
                    median_time_to_first_review_hours=2.0,
                    median_approval_to_merge_hours=4.0,
                )
            ],
            definitions=definitions_for(
                "cycle_time_total",
                "cycle_time_first_review",
                "cycle_time_approval_to_merge",
            ),
        ))
    if cls is PRSizeStats:
        return cast(T, PRSizeStats(
            org="acme",
            since=SINCE,
            until=UNTIL,
            count=10,
            median_lines_changed=80.0,
            p90_lines_changed=500.0,
            median_files_changed=3.0,
            distribution_buckets=PRSizeBuckets(xs=2, s=4, m=3, l=1, xl=0),
            examples=[_example(label="largest", value=1500, unit="lines")],
            repos=[
                PRSizeRepoSlice(
                    repo="web",
                    count=10,
                    median_lines_changed=80.0,
                    p90_lines_changed=500.0,
                    median_files_changed=3.0,
                    distribution_buckets=PRSizeBuckets(xs=2, s=4, m=3, l=1, xl=0),
                )
            ],
            definitions=definitions_for("pr_size_lines", "pr_size_files", "pr_size_bucket"),
        ))
    if cls is ReviewHealthStats:
        return cast(T, ReviewHealthStats(
            org="acme",
            since=SINCE,
            until=UNTIL,
            count=10,
            median_reviewers_per_pr=1.0,
            pct_merged_without_review=10.0,
            pct_merged_with_only_author_review=0.0,
            median_comments_per_pr=3.0,
            fast_approval_count=1,
            self_merge_count=2,
            examples=[_example(label="no_review", value=0, unit="count")],
            repos=[
                ReviewHealthRepoSlice(
                    repo="web",
                    count=10,
                    median_reviewers_per_pr=1.0,
                    pct_merged_without_review=10.0,
                    pct_merged_with_only_author_review=0.0,
                    median_comments_per_pr=3.0,
                    fast_approval_count=1,
                    self_merge_count=2,
                )
            ],
            definitions=definitions_for(
                "review_reviewers_per_pr",
                "review_no_review",
                "review_only_author",
                "review_comments_per_pr",
                "review_fast_approval",
                "review_self_merge",
            ),
        ))
    if cls is CIHealthStats:
        return cast(T, CIHealthStats(
            org="acme",
            since=SINCE,
            until=UNTIL,
            pr_count=10,
            pct_with_failing_check=20.0,
            pct_with_flaky_check=5.0,
            top_failing_checks=[FailingCheck(name="lint", count=3)],
            median_failed_runs_per_pr=0.0,
            examples=[_example(label="most_failures", value=5, unit="count")],
            repos=[
                CIHealthRepoSlice(
                    repo="web",
                    pr_count=10,
                    pct_with_failing_check=20.0,
                    pct_with_flaky_check=5.0,
                    median_failed_runs_per_pr=0.0,
                )
            ],
            definitions=definitions_for(
                "ci_failing_check",
                "ci_flaky_check",
                "ci_top_failing_checks",
                "ci_median_failed_runs_per_pr",
            ),
        ))
    if cls is StalePRList:
        stale_pr = StalePR(
            number=42,
            title="Old work",
            author="alice",
            age_days=30,
            last_activity=NOW,
            url="https://github.com/o/r/pull/42",
        )
        return cast(T, StalePRList(
            org="acme",
            as_of=UNTIL,
            threshold_days=14,
            count=1,
            prs=[stale_pr],
            repos=[
                StaleRepoSlice(repo="web", count=1, prs=[stale_pr]),
            ],
            definitions=definitions_for("stale_pr"),
        ))
    if cls is MergeActivityStats:
        bucket = WeeklyBucket(week_starting=date(2026, 4, 27), count=3)
        return cast(T, MergeActivityStats(
            org="acme",
            since=SINCE,
            until=UNTIL,
            merges_to_default_branch=[bucket],
            revert_commit_count=1,
            merge_frequency_per_week=3.0,
            repos=[
                MergeActivityRepoSlice(
                    repo="web",
                    merges_to_default_branch=[bucket],
                    revert_commit_count=1,
                    merge_frequency_per_week=3.0,
                )
            ],
            definitions=definitions_for(
                "merge_to_default_branch", "revert_commit", "merge_frequency_per_week"
            ),
        ))
    if cls is PortfolioSummary:
        return cast(T, PortfolioSummary(
            org="acme",
            since=SINCE,
            until=UNTIL,
            include_inactive=False,
            total_repos_active=3,
            total_repos_with_merges=2,
            repos=[
                PortfolioRepoEntry(
                    owner="acme",
                    repo="r",
                    has_merges_in_window=True,
                    inactive_in_window=False,
                    cycle_time_median=12.0,
                    review_no_review_pct=10.0,
                    ci_failure_pct=20.0,
                    stale_count=1,
                    merge_frequency=3.0,
                    cycle_time_percentile=50.0,
                    review_no_review_percentile=75.0,
                    ci_failure_percentile=25.0,
                    merge_frequency_percentile=80.0,
                )
            ],
            definitions=definitions_for(
                "cycle_time_total",
                "review_no_review",
                "ci_failing_check",
                "stale_pr",
                "merge_frequency_per_week",
                "portfolio_percentile_rank",
            ),
        ))
    if cls is BaselineComparison:
        return cast(T, BaselineComparison(
            org="acme",
            metric="cycle_time_median_hours",
            current_window_days=30,
            baseline_window_days=90,
            current_value=12.0,
            baseline_value=10.0,
            pct_change=20.0,
            direction="worse",
            significance_hint="above_noise_floor",
            definitions=definitions_for("baseline_pct_change", "baseline_significance"),
        ))
    if cls is CacheClearResult:
        return cast(T, CacheClearResult(scope="all", org=None, entries_cleared=12))
    raise AssertionError(f"no sample factory for {cls!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _all_response_models())
def test_response_model_json_round_trip(cls: type[ProviderResponse]) -> None:
    """Every response model must serialize to JSON and re-parse identically."""
    inst = _sample_instance(cls)
    raw = inst.model_dump_json()
    payload = json.loads(raw)
    again = cls.model_validate(payload)
    assert again == inst


@pytest.mark.parametrize("cls", _all_response_models())
def test_provider_field_is_github(cls: type[ProviderResponse]) -> None:
    inst = _sample_instance(cls)
    assert inst.provider == "github"


@pytest.mark.parametrize("cls", _all_aggregate_models())
def test_aggregate_definitions_are_known_keys(cls: type[AggregateResponse]) -> None:
    inst = _sample_instance(cls)
    assert inst.definitions, f"{cls.__name__} has empty definitions"
    for key in inst.definitions:
        assert key in DEFINITIONS, f"{cls.__name__} references unknown key {key!r}"


def test_definitions_for_rejects_unknown_keys() -> None:
    with pytest.raises(KeyError) as exc:
        definitions_for("cycle_time_total", "not_a_real_metric")
    assert "not_a_real_metric" in str(exc.value)


def test_definitions_for_returns_subset() -> None:
    subset = definitions_for("cycle_time_total", "stale_pr")
    assert set(subset.keys()) == {"cycle_time_total", "stale_pr"}
    for v in subset.values():
        assert isinstance(v, str) and len(v) > 20


def test_all_definitions_referenced_by_some_response() -> None:
    """No orphaned definitions. If a key is in DEFINITIONS, some response uses it."""
    referenced: set[str] = set()
    for cls in _all_aggregate_models():
        referenced.update(_sample_instance(cls).definitions.keys())
    orphans = set(DEFINITIONS) - referenced
    assert not orphans, f"definitions not used by any response: {sorted(orphans)}"


# ---------------------------------------------------------------------------
# Per-repo slice round-trip
# ---------------------------------------------------------------------------


def test_cycle_time_repo_slice_round_trip() -> None:
    s = CycleTimeRepoSlice(
        repo="web",
        count=5,
        median_hours=10.0,
        p90_hours=20.0,
        mean_hours=12.0,
        median_time_to_first_review_hours=2.0,
        median_approval_to_merge_hours=4.0,
    )
    again = CycleTimeRepoSlice.model_validate_json(s.model_dump_json())
    assert again == s


def test_review_health_repo_slice_round_trip() -> None:
    s = ReviewHealthRepoSlice(
        repo="web",
        count=5,
        median_reviewers_per_pr=1.0,
        pct_merged_without_review=20.0,
        pct_merged_with_only_author_review=0.0,
        median_comments_per_pr=3.0,
        fast_approval_count=1,
        self_merge_count=0,
    )
    again = ReviewHealthRepoSlice.model_validate_json(s.model_dump_json())
    assert again == s


def test_stale_repo_slice_round_trip() -> None:
    s = StaleRepoSlice(
        repo="web",
        count=1,
        prs=[
            StalePR(
                number=42,
                title="Old",
                author="a",
                age_days=30,
                last_activity=NOW,
                url="u",
            )
        ],
    )
    again = StaleRepoSlice.model_validate_json(s.model_dump_json())
    assert again == s


def test_merge_activity_repo_slice_round_trip() -> None:
    s = MergeActivityRepoSlice(
        repo="web",
        merges_to_default_branch=[WeeklyBucket(week_starting=date(2026, 4, 27), count=2)],
        revert_commit_count=0,
        merge_frequency_per_week=2.0,
    )
    again = MergeActivityRepoSlice.model_validate_json(s.model_dump_json())
    assert again == s


def test_naive_datetime_rejected_on_normalized_pr() -> None:
    """Datetimes must be timezone-aware. Naive values are a bug we want loud."""
    with pytest.raises(ValidationError):
        NormalizedPR(
            owner="o",
            repo="r",
            number=1,
            author="a",
            title="t",
            url="u",
            created_at=datetime(2026, 5, 1, 12, 0, 0),
            merged_at=None,
            closed_at=None,
            first_commit_at=None,
            additions=0,
            deletions=0,
            changed_files=0,
            base_branch="main",
            head_sha="abc",
            is_draft=False,
            labels=[],
            merged_by=None,
            reviews=[],
            checks=[],
            comments_count=0,
            review_comments_count=0,
            requested_reviewer_count=0,
        )


def test_naive_datetime_rejected_on_example_with_aware_field() -> None:
    """StalePR.last_activity must be aware."""
    with pytest.raises(ValidationError):
        StalePR(
            number=1,
            title="t",
            author="a",
            age_days=30,
            last_activity=datetime(2026, 5, 1, 12, 0, 0),
            url="u",
        )


def test_weekly_bucket_requires_monday() -> None:
    # Tuesday should be rejected
    with pytest.raises(ValidationError):
        WeeklyBucket(week_starting=date(2026, 4, 28), count=1)
    # Monday accepted
    WeeklyBucket(week_starting=date(2026, 4, 27), count=1)


def test_portfolio_percentile_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        PortfolioRepoEntry(
            owner="o",
            repo="r",
            has_merges_in_window=True,
            inactive_in_window=False,
            cycle_time_median=10.0,
            review_no_review_pct=0.0,
            ci_failure_pct=0.0,
            stale_count=0,
            merge_frequency=1.0,
            cycle_time_percentile=150.0,
        )


def test_extra_field_rejected() -> None:
    """Extra fields catch typos in consumers building these models."""
    with pytest.raises(ValidationError):
        ExamplePR(
            number=1,
            title="t",
            author="a",
            url="u",
            label="slowest",
            value=1.0,
            unit="hours",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_normalized_pr_provider_field_defaults_to_github() -> None:
    pr = NormalizedPR(
        owner="o",
        repo="r",
        number=1,
        author="a",
        title="t",
        url="u",
        created_at=NOW,
        merged_at=None,
        closed_at=None,
        first_commit_at=None,
        additions=0,
        deletions=0,
        changed_files=0,
        base_branch="main",
        head_sha="abc",
        is_draft=False,
        labels=[],
        merged_by=None,
        reviews=[],
        checks=[],
        comments_count=0,
        review_comments_count=0,
        requested_reviewer_count=0,
    )
    assert pr.provider == "github"


def test_normalized_review_state_literal_enforced() -> None:
    with pytest.raises(ValidationError):
        NormalizedReview(
            reviewer="r",
            state="approved_unknown",  # type: ignore[arg-type]
            submitted_at=NOW,
            comments_count=0,
            body_length=0,
        )


def test_normalized_check_conclusion_can_be_none() -> None:
    """A check that hasn't completed has conclusion=None — must be accepted."""
    NormalizedCheckRun(
        name="lint",
        status="in_progress",
        conclusion=None,
        started_at=NOW,
        completed_at=None,
        head_sha="abc",
    )


def test_normalized_commit_round_trip() -> None:
    c = NormalizedCommit(
        sha="abc",
        author="a",
        committed_at=NOW,
        message='Revert "Add foo"',
        is_revert=True,
        parent_shas=["def"],
    )
    again = NormalizedCommit.model_validate_json(c.model_dump_json())
    assert again == c


def test_models_module_exports_are_consistent() -> None:
    """``__all__`` should not reference anything that isn't defined."""
    for name in models.__all__:
        assert hasattr(models, name), f"models.__all__ refers to missing {name!r}"
