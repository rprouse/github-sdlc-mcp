"""Pydantic response models for every tool, plus internal normalized models.

These shapes are the **cross-server contract**. The planned sibling
servers ``gitlab-sdlc-mcp`` and ``azdo-sdlc-mcp`` must reproduce them
exactly so an orchestrator can merge results from multiple servers
without per-provider branching. Field names here are deliberately
provider-neutral.

Datetime fields are required to be timezone-aware (UTC).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

ReviewState = Literal[
    "approved", "changes_requested", "commented", "dismissed", "pending"
]
CheckStatus = Literal["queued", "in_progress", "completed"]
CheckConclusion = Literal[
    "success",
    "failure",
    "neutral",
    "cancelled",
    "timed_out",
    "action_required",
    "skipped",
    "stale",
]


class _Strict(BaseModel):
    """Base model with extra-field rejection. Catches typos in API consumers."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Provider envelope
# ---------------------------------------------------------------------------


class ProviderResponse(_Strict):
    """Base class for every tool response. Stamps the source provider."""

    provider: Literal["github"] = "github"


class AggregateResponse(ProviderResponse):
    """Every aggregate (metric) response carries its own definition strings.

    Keys are metric names (e.g. ``"cycle_time_total"``); values are the
    canonical definition sentences from ``metrics/definitions.py``. Agents
    quote these verbatim when narrating results to humans.
    """

    definitions: dict[str, str]


# ---------------------------------------------------------------------------
# Representative examples
# ---------------------------------------------------------------------------


class ExamplePR(_Strict):
    """A single PR illustrating why a metric came out the way it did."""

    number: int
    title: str
    author: str
    url: str
    label: str
    value: float
    unit: str


# ---------------------------------------------------------------------------
# Internal normalized models (consumed by the metrics layer)
# ---------------------------------------------------------------------------


class NormalizedReview(_Strict):
    reviewer: str
    state: ReviewState
    submitted_at: AwareDatetime | None
    comments_count: int
    body_length: int


class NormalizedCheckRun(_Strict):
    name: str
    status: CheckStatus
    conclusion: CheckConclusion | None
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    head_sha: str


class NormalizedCommit(_Strict):
    sha: str
    author: str
    committed_at: AwareDatetime
    message: str
    is_revert: bool
    parent_shas: list[str]


class NormalizedPR(_Strict):
    provider: Literal["github"] = "github"
    owner: str
    repo: str
    number: int
    author: str
    title: str
    url: str
    created_at: AwareDatetime
    merged_at: AwareDatetime | None
    closed_at: AwareDatetime | None
    first_commit_at: AwareDatetime | None
    additions: int
    deletions: int
    changed_files: int
    base_branch: str
    head_sha: str
    is_draft: bool
    labels: list[str]
    merged_by: str | None
    reviews: list[NormalizedReview]
    checks: list[NormalizedCheckRun]
    comments_count: int
    review_comments_count: int
    requested_reviewer_count: int


# ---------------------------------------------------------------------------
# Discovery tool responses
# ---------------------------------------------------------------------------


class ActiveRepo(_Strict):
    owner: str
    repo: str
    pushed_at: AwareDatetime
    default_branch: str
    has_merges_in_window: bool


class ActiveRepoList(AggregateResponse):
    org: str
    since: date
    until: date
    total_repos_scanned: int
    total_repos_active: int
    repos: list[ActiveRepo]


class HealthStatus(ProviderResponse):
    ok: bool
    rate_limit_remaining: int | None
    rate_limit_resets_at: AwareDatetime | None
    last_successful_call_at: AwareDatetime | None
    cache_entries: int


# ---------------------------------------------------------------------------
# Building-block types referenced by per-repo slices and aggregate responses
# ---------------------------------------------------------------------------


class PRSizeBuckets(_Strict):
    """PR count by lines-changed bucket. xs<10, s 10-99, m 100-499, l 500-1999, xl 2000+."""

    xs: int = Field(ge=0)
    s: int = Field(ge=0)
    m: int = Field(ge=0)
    l: int = Field(ge=0)  # noqa: E741 — domain term, see docstring
    xl: int = Field(ge=0)


class StalePR(_Strict):
    number: int
    title: str
    author: str
    age_days: int
    last_activity: AwareDatetime
    url: str


class WeeklyBucket(_Strict):
    """A weekly count bucket. ``week_starting`` is the Monday of the ISO week."""

    week_starting: date
    count: int = Field(ge=0)

    @field_validator("week_starting")
    @classmethod
    def _must_be_monday(cls, v: date) -> date:
        if v.isoweekday() != 1:
            raise ValueError(
                f"week_starting must be a Monday (ISO weekday 1); got {v} "
                f"(weekday {v.isoweekday()})"
            )
        return v


class FailingCheck(_Strict):
    name: str
    count: int


# ---------------------------------------------------------------------------
# Per-repo metric slices
# ---------------------------------------------------------------------------


class CycleTimeRepoSlice(_Strict):
    repo: str
    count: int
    median_hours: float | None
    p90_hours: float | None
    mean_hours: float | None
    median_time_to_first_review_hours: float | None
    median_approval_to_merge_hours: float | None


class PRSizeRepoSlice(_Strict):
    repo: str
    count: int
    median_lines_changed: float | None
    p90_lines_changed: float | None
    median_files_changed: float | None
    distribution_buckets: PRSizeBuckets


class ReviewHealthRepoSlice(_Strict):
    repo: str
    count: int
    median_reviewers_per_pr: float | None
    pct_merged_without_review: float | None
    pct_merged_with_only_author_review: float | None
    median_comments_per_pr: float | None
    fast_approval_count: int
    self_merge_count: int


class CIHealthRepoSlice(_Strict):
    repo: str
    pr_count: int
    pct_with_failing_check: float | None
    pct_with_flaky_check: float | None
    median_failed_runs_per_pr: float | None


class StaleRepoSlice(_Strict):
    repo: str
    count: int
    prs: list[StalePR]


class MergeActivityRepoSlice(_Strict):
    repo: str
    merges_to_default_branch: list[WeeklyBucket]
    revert_commit_count: int
    merge_frequency_per_week: float


# ---------------------------------------------------------------------------
# Org-aggregate metric responses
# ---------------------------------------------------------------------------


class PRCycleTimeStats(AggregateResponse):
    org: str
    since: date
    until: date
    count: int
    median_hours: float | None
    p90_hours: float | None
    mean_hours: float | None
    median_time_to_first_review_hours: float | None
    median_approval_to_merge_hours: float | None
    examples: list[ExamplePR]
    repos: list[CycleTimeRepoSlice]


class PRSizeStats(AggregateResponse):
    org: str
    since: date
    until: date
    count: int
    median_lines_changed: float | None
    p90_lines_changed: float | None
    median_files_changed: float | None
    distribution_buckets: PRSizeBuckets
    examples: list[ExamplePR]
    repos: list[PRSizeRepoSlice]


class ReviewHealthStats(AggregateResponse):
    org: str
    since: date
    until: date
    count: int
    median_reviewers_per_pr: float | None
    pct_merged_without_review: float | None
    pct_merged_with_only_author_review: float | None
    median_comments_per_pr: float | None
    fast_approval_count: int
    self_merge_count: int
    examples: list[ExamplePR]
    repos: list[ReviewHealthRepoSlice]


class CIHealthStats(AggregateResponse):
    org: str
    since: date
    until: date
    pr_count: int
    pct_with_failing_check: float | None
    pct_with_flaky_check: float | None
    top_failing_checks: list[FailingCheck]
    median_failed_runs_per_pr: float | None
    examples: list[ExamplePR]
    repos: list[CIHealthRepoSlice]


class StalePRList(AggregateResponse):
    org: str
    as_of: date
    threshold_days: int
    count: int
    prs: list[StalePR]
    repos: list[StaleRepoSlice]


class MergeActivityStats(AggregateResponse):
    org: str
    since: date
    until: date
    merges_to_default_branch: list[WeeklyBucket]
    revert_commit_count: int
    merge_frequency_per_week: float
    repos: list[MergeActivityRepoSlice]


# ---------------------------------------------------------------------------
# Portfolio / cross-repo
# ---------------------------------------------------------------------------


class PortfolioRepoEntry(_Strict):
    owner: str
    repo: str
    has_merges_in_window: bool
    inactive_in_window: bool
    cycle_time_median: float | None
    review_no_review_pct: float | None
    ci_failure_pct: float | None
    stale_count: int | None
    merge_frequency: float | None
    # Percentile rank within the portfolio for each metric (0-100). None when
    # the metric is undefined for this repo (e.g. inactive).
    cycle_time_percentile: float | None = Field(default=None, ge=0, le=100)
    review_no_review_percentile: float | None = Field(default=None, ge=0, le=100)
    ci_failure_percentile: float | None = Field(default=None, ge=0, le=100)
    merge_frequency_percentile: float | None = Field(default=None, ge=0, le=100)


class PortfolioSummary(AggregateResponse):
    org: str
    since: date
    until: date
    include_inactive: bool
    total_repos_active: int
    total_repos_with_merges: int
    repos: list[PortfolioRepoEntry]


class BaselineComparison(AggregateResponse):
    org: str
    metric: str
    current_window_days: int
    baseline_window_days: int
    current_value: float | None
    baseline_value: float | None
    pct_change: float | None
    direction: Literal["better", "worse", "flat", "unknown"]
    significance_hint: Literal["above_noise_floor", "below_noise_floor", "unknown"]


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


class CacheClearResult(ProviderResponse):
    scope: Literal["all", "org"]
    org: str | None
    entries_cleared: int


__all__ = [
    "ActiveRepo",
    "ActiveRepoList",
    "AggregateResponse",
    "BaselineComparison",
    "CIHealthRepoSlice",
    "CIHealthStats",
    "CacheClearResult",
    "CheckConclusion",
    "CheckStatus",
    "CycleTimeRepoSlice",
    "ExamplePR",
    "FailingCheck",
    "HealthStatus",
    "MergeActivityRepoSlice",
    "MergeActivityStats",
    "NormalizedCheckRun",
    "NormalizedCommit",
    "NormalizedPR",
    "NormalizedReview",
    "PRCycleTimeStats",
    "PRSizeBuckets",
    "PRSizeRepoSlice",
    "PRSizeStats",
    "PortfolioRepoEntry",
    "PortfolioSummary",
    "ProviderResponse",
    "ReviewHealthRepoSlice",
    "ReviewHealthStats",
    "ReviewState",
    "StalePR",
    "StalePRList",
    "StaleRepoSlice",
    "WeeklyBucket",
]
