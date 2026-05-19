# Phase 3 plan: response models + metric definitions

## Goal

Define every Pydantic response model the server will return, and the
canonical metric-definition strings each aggregate response embeds.
After this phase, the tool contract is **frozen** — sibling servers
(GitLab, ADO) can be built against these shapes, and metric code
written in later phases only has to fill the models, not invent them.

## Files

- `src/github_sdlc_mcp/models.py` — all Pydantic models.
- `src/github_sdlc_mcp/metrics/definitions.py` — canonical metric
  definition strings, keyed by metric name.
- `tests/test_models.py` — round-trip JSON + invariant tests.

## Design principles

1. **Provider-neutral field names.** No `pull_request_id`, no `head_sha`
   in tool responses. Sibling servers must be able to fill the same
   models from GitLab MR / ADO PR data without renaming. Internal
   normalized models *may* keep platform terms.
2. **One base class for every tool response.** `ProviderResponse` adds
   `provider: Literal["github"] = "github"`. Every tool response model
   inherits from it. (Per spec v2 §3.)
3. **Aggregate responses also embed `definitions`.** Implemented via a
   second base class `AggregateResponse(ProviderResponse)` with
   `definitions: dict[str, str]`. Non-aggregate responses
   (`HealthStatus`, `ConfiguredRepoList`, `CacheClearResult`) skip it.
4. **All datetimes are tz-aware UTC.** Pydantic 2 enforces this via
   field annotation `datetime` + a validator that rejects naive values.
   JSON output uses ISO 8601 with `Z`.
5. **Examples have a consistent shape.** One `ExamplePR` class:
   `number`, `title`, `author`, `url`, `label` (string like "slowest"),
   `value` (float), `unit` (string like "hours" or "lines"). Agents
   render examples uniformly across metrics.
6. **No optional fields without a reason.** Every required field is
   non-Optional. Optionals are reserved for genuinely-missing values
   (e.g. `merged_at: datetime | None` on a closed-not-merged PR).

## Models

### Common

```python
class ProviderResponse(BaseModel):
    provider: Literal["github"] = "github"

class AggregateResponse(ProviderResponse):
    definitions: dict[str, str]

class ExamplePR(BaseModel):
    number: int
    title: str
    author: str
    url: str
    label: str        # "slowest", "largest", "no_review", ...
    value: float      # the metric value that made it representative
    unit: str | None  # "hours", "lines", "files", "comments", "count"
```

### Normalized (internal)

These are not tool responses but the metrics layer consumes them. They
live in `models.py` alongside the contract models because they're
contract too — phase 5's normalizer fills them, phase 6's metrics
read them, and sibling servers must produce identical shapes.

```python
class NormalizedReview(BaseModel):
    reviewer: str
    state: Literal["approved", "changes_requested", "commented", "dismissed", "pending"]
    submitted_at: datetime | None
    comments_count: int
    body_length: int

class NormalizedCheckRun(BaseModel):
    name: str
    status: Literal["queued", "in_progress", "completed"]
    conclusion: Literal["success", "failure", "neutral", "cancelled", "timed_out", "action_required", "skipped", "stale"] | None
    started_at: datetime | None
    completed_at: datetime | None
    head_sha: str

class NormalizedCommit(BaseModel):
    sha: str
    author: str
    committed_at: datetime
    message: str
    is_revert: bool
    parent_shas: list[str]

class NormalizedPR(BaseModel):
    provider: Literal["github"] = "github"
    owner: str
    repo: str
    number: int
    author: str
    title: str
    url: str
    created_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    first_commit_at: datetime | None
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
```

### Discovery / config

```python
class ConfiguredRepo(ProviderResponse):
    owner: str
    repo: str
    company_label: str
    github_host: str   # configured host key, e.g. "cloud" or "acme_enterprise"

class ConfiguredRepoList(ProviderResponse):
    repos: list[ConfiguredRepo]

class ActiveRepo(BaseModel):
    owner: str
    repo: str
    company_label: str
    pushed_at: datetime
    default_branch: str
    has_merges_in_window: bool

class ActiveRepoList(AggregateResponse):
    repos: list[ActiveRepo]
    total_repos_configured: int
    total_repos_scanned: int
    total_repos_active: int
    since: date
    until: date

class HealthStatus(ProviderResponse):
    ok: bool
    rate_limit_remaining: int | None
    rate_limit_resets_at: datetime | None
    last_successful_call_at: datetime | None
    cache_entries: int
    config_status: Literal["loaded", "not_found", "error"]
    config_path: Path | None
    config_searched_paths: list[Path]
    config_error: str | None
```

### Per-repo metrics

```python
class PRCycleTimeStats(AggregateResponse):
    repo: str
    since: date
    until: date
    count: int
    median_hours: float | None
    p90_hours: float | None
    mean_hours: float | None
    median_time_to_first_review_hours: float | None
    median_approval_to_merge_hours: float | None
    examples: list[ExamplePR]

class PRSizeBuckets(BaseModel):
    xs: int   # <10 lines
    s: int    # 10-99
    m: int    # 100-499
    l: int    # 500-1999
    xl: int   # 2000+

class PRSizeStats(AggregateResponse):
    repo: str
    since: date
    until: date
    count: int
    median_lines_changed: float | None
    p90_lines_changed: float | None
    median_files_changed: float | None
    distribution_buckets: PRSizeBuckets
    examples: list[ExamplePR]

class ReviewHealthStats(AggregateResponse):
    repo: str
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

class FailingCheck(BaseModel):
    name: str
    count: int

class CIHealthStats(AggregateResponse):
    repo: str
    since: date
    until: date
    pr_count: int
    pct_with_failing_check: float | None
    pct_with_flaky_check: float | None
    top_failing_checks: list[FailingCheck]
    median_failed_runs_per_pr: float | None
    examples: list[ExamplePR]

class StalePR(BaseModel):
    number: int
    title: str
    author: str
    age_days: int
    last_activity: datetime
    url: str

class StalePRList(AggregateResponse):
    repo: str
    as_of: date
    threshold_days: int
    count: int
    prs: list[StalePR]

class WeeklyBucket(BaseModel):
    week_starting: date     # Monday of the ISO week
    count: int

class MergeActivityStats(AggregateResponse):
    repo: str
    since: date
    until: date
    merges_to_default_branch: list[WeeklyBucket]
    revert_commit_count: int
    merge_frequency_per_week: float
```

### Portfolio / cross-repo

```python
class PortfolioRepoEntry(BaseModel):
    owner: str
    repo: str
    company_label: str
    has_merges_in_window: bool
    inactive_in_window: bool
    cycle_time_median: float | None
    review_no_review_pct: float | None
    ci_failure_pct: float | None
    stale_count: int | None
    merge_frequency: float | None
    # percentile rank within the portfolio for each metric, 0-100; None if undefined
    cycle_time_percentile: float | None
    review_no_review_percentile: float | None
    ci_failure_percentile: float | None
    merge_frequency_percentile: float | None

class PortfolioSummary(AggregateResponse):
    since: date
    until: date
    company_filter: str | None
    include_inactive: bool
    total_repos_configured: int
    total_repos_active: int
    total_repos_with_merges: int
    repos: list[PortfolioRepoEntry]

class BaselineComparison(AggregateResponse):
    repo: str
    metric: str
    current_window_days: int
    baseline_window_days: int
    current_value: float | None
    baseline_value: float | None
    pct_change: float | None
    direction: Literal["better", "worse", "flat", "unknown"]
    significance_hint: Literal["above_noise_floor", "below_noise_floor", "unknown"]
```

### Cache

```python
class CacheClearResult(ProviderResponse):
    scope: Literal["all", "repo"]
    repo: str | None
    entries_cleared: int
```

## Definition strings

`metrics/definitions.py` exposes a single dict keyed by metric name.
Definitions are full sentences, name thresholds inline, and stay
short enough to fit in an agent's narrative. Each tool response picks
a subset of these into its `definitions` field.

Keys (final names land in code):

- `cycle_time_total`
- `cycle_time_first_review`
- `cycle_time_approval_to_merge`
- `pr_size_lines`
- `pr_size_files`
- `pr_size_bucket`
- `review_reviewers_per_pr`
- `review_no_review`
- `review_only_author`
- `review_comments_per_pr`
- `review_fast_approval`
- `review_self_merge`
- `ci_failing_check`
- `ci_flaky_check`
- `ci_top_failing_checks`
- `stale_pr`
- `merge_to_default_branch`
- `revert_commit`
- `merge_frequency_per_week`
- `portfolio_percentile_rank`
- `baseline_pct_change`
- `baseline_significance`
- `active_repo_pushed_at`
- `active_repo_has_merges`

A helper `definitions_for(*keys: str) -> dict[str, str]` returns a
subset for tool responses; it raises `KeyError` on unknown keys so
typos surface in tests.

## Tests (`tests/test_models.py`)

| # | Test |
|---|---|
| 1 | Every tool response model serializes to JSON via `model_dump(mode='json')` without errors |
| 2 | `provider == "github"` on every response model when constructed without args |
| 3 | `definitions` keys on each aggregate response are all valid `definitions_for` keys |
| 4 | `ExamplePR` requires `unit` to be set when `value` is non-zero (sanity) |
| 5 | Naive datetime in any datetime field is rejected |
| 6 | `definitions_for` raises `KeyError` on unknown name |
| 7 | `definitions` dict is non-empty for every aggregate response model |
| 8 | `PortfolioRepoEntry` percentile fields are in `[0, 100]` when set |
| 9 | `WeeklyBucket.week_starting` snaps to a Monday (validator) |
| 10 | The full set of definitions covers every key referenced by any tool response (no orphans, no missing) |

## What's NOT in this phase

- Metric *computation* — phase 6.
- Normalizer (GitHub API → `NormalizedPR`) — phase 5.
- Wiring models to FastMCP tool functions — phase 8.

## Acceptance

- `tests/test_models.py` passes.
- All existing tests still pass.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
