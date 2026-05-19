# Phase 6 plan: cycle_time + review_health (and stubs for the rest)

## Goal

Implement the two metrics named in spec v2 §10 as "fully implemented":
`cycle_time` and `review_health`. Stub every other metric module with
a function signature aligned to its `models.py` response type and a
body that raises `NotImplementedError`. After this phase, the metrics
layer is callable from phase 8's tool wiring; the math for the two
hot metrics is exercised against the synthetic fixture from phase 5.

## Files

- `src/github_sdlc_mcp/metrics/_stats.py` — private helpers (`median`,
  `percentile`, `_example_for_pr`). One source of truth for stats math
  so sibling servers can vendor it later.
- `src/github_sdlc_mcp/metrics/cycle_time.py` — `compute_cycle_time_stats`.
- `src/github_sdlc_mcp/metrics/review_health.py` — `compute_review_health`.
- `src/github_sdlc_mcp/metrics/{ci_health,size,stale,merge_activity,portfolio,baseline}.py`
  — stub function bodies with matching signatures.
- `src/github_sdlc_mcp/metrics/__init__.py` — re-exports.
- `tests/test_metrics_cycle_time.py`, `tests/test_metrics_review_health.py`.

## Algorithms

### cycle_time

Input: `Iterable[NormalizedPR]`, window `(since, until)` (both inclusive
date-only), `repo` string for the response model.

Filter: PRs whose `merged_at` is non-null and falls in `[since 00:00 UTC,
until 23:59:59.999999 UTC]`. Open PRs and PRs merged outside the
window are excluded.

For each surviving PR:

- `cycle_hours` = `(merged_at - created_at).total_seconds() / 3600`
- `first_review_hours` = hours from `created_at` to the earliest
  non-author review (any state). `None` if no non-author review exists.
- `approval_to_merge_hours` = hours from the earliest non-author
  *approving* review to `merged_at`. `None` if no such review.

Aggregates:

- `count` = number of merged PRs in window.
- `median_hours`, `mean_hours` = `statistics.median` / `statistics.mean`
  over `cycle_hours`.
- `p90_hours` = 90th percentile via linear interpolation
  (`statistics.quantiles(values, n=10, method="inclusive")[8]` when
  ≥ 2 values; single value returned as-is).
- `median_time_to_first_review_hours` / `median_approval_to_merge_hours`
  = median over the non-null subset of each. `None` if the subset is
  empty.

Numeric outputs are rounded to 2 decimals for stability in tests and
agent-readable narratives.

Examples (up to 3):

- `"slowest"` — PR with max `cycle_hours`, `value=cycle_hours`, unit `"hours"`.
- `"fastest"` — PR with min `cycle_hours`.
- `"median"` — PR whose `cycle_hours` is closest to the median value.
  Ties broken by PR number (lower first) for determinism.

Empty window → count=0, all numeric fields None, examples=[].

### review_health

Input: same as cycle_time.

For each merged PR:

- `non_author_reviewers` = `set(r.reviewer for r in reviews if r.reviewer != pr.author)`.
- `reviewer_count` = `len(non_author_reviewers)`.
- `has_only_author_reviews` = reviews exist AND every review's reviewer
  equals pr.author.
- `has_no_non_author_review` = no review where `reviewer != pr.author`.
- `is_fast_approval` per spec v2 §4 (see below).
- `is_self_merge` = `pr.merged_by == pr.author`.

Fast approval (per spec v2 §4):

```python
def is_fast_approval(pr, *, min_lines, max_seconds):
    if pr.additions + pr.deletions <= min_lines:
        return False
    for r in pr.reviews:
        if r.state != "approved" or r.reviewer == pr.author or r.submitted_at is None:
            continue
        # Proxy for "review requested at open time": measure from created_at.
        delta = (r.submitted_at - pr.created_at).total_seconds()
        if 0 <= delta <= max_seconds:
            return True
    return False
```

Thresholds are read from `metrics.definitions.FAST_APPROVAL_MIN_LINES_DEFAULT`
and `FAST_APPROVAL_MAX_SECONDS_DEFAULT` unless the caller overrides.

Aggregates:

- `count` = merged-in-window count.
- `median_reviewers_per_pr` = median of `reviewer_count` across all
  merged PRs (including zeros).
- `pct_merged_without_review` = % of PRs with `has_no_non_author_review`.
- `pct_merged_with_only_author_review` = % of PRs with `has_only_author_reviews`.
- `median_comments_per_pr` = median of `pr.review_comments_count`.
- `fast_approval_count`, `self_merge_count` = simple counts.

Examples (up to 3):

- `"no_review"` — first PR with `has_no_non_author_review`,
  value=`reviewer_count` (always 0), unit `"count"`.
- `"fast_approval"` — first PR with `is_fast_approval`, value=hours from
  `created_at` to the fast review, unit `"hours"`.
- `"deep_review"` — PR with maximum `pr.review_comments_count`,
  value=`review_comments_count`, unit `"comments"`. Omitted if max is 0.

Determinism: when picking "first" or breaking ties, sort by PR number
ascending.

## Stubs (raise `NotImplementedError`)

Each stub raises with a consistent message so consumers see the same
behaviour and the tests can assert on it:

```python
raise NotImplementedError(
    f"{func.__name__} is stubbed in v0.1.0; see docs/spec_v2.md §10."
)
```

Stub signatures match their response model exactly so server.py can
wire them in phase 8 even before the body lands. They live as
ordinary functions, not `@abstractmethod` — easier to monkeypatch in
tests.

## Tests

`tests/test_metrics_cycle_time.py`:

1. **Counts**: fixture yields exactly 40 merged PRs and `count == 40`.
2. **Window filter**: shifting `until` 1 day earlier excludes ≥1 PR;
   widening it adds back the same PRs.
3. **median ≤ mean ≤ p90**: stats invariant on the fixture.
4. **Approval-to-merge ≤ cycle time** (mathematically required): the
   median approval-to-merge is less than or equal to median cycle time.
5. **Empty PR set**: count=0, all numerics None, examples=[].
6. **Single PR**: count=1, median=mean=p90=that PR's hours, one example.
7. **No reviews → null first-review/approval-to-merge medians**: pass
   five PRs all merged with zero reviews; both medians None.
8. **Examples include slowest, fastest, median labels**.
9. **Numeric exact-value pin** for fixture: lock in the median,
   p90, and count to catch silent algorithm drift. These values come
   from one initial test run; updating the generator updates these.

`tests/test_metrics_review_health.py`:

1. **Counts**: matches the fixture (40 merged).
2. **`pct_merged_without_review` ≥ (5/40)*100** — fixture has 5 no-review
   merges; this is the floor.
3. **`fast_approval_count >= 3`** — fixture has 3 fast approvals; matches.
4. **`self_merge_count >= 2`**.
5. **`is_fast_approval` threshold knob**: same PR set evaluated with
   `max_seconds=1` produces zero fast approvals (proves the parameter
   wires through).
6. **Self-only review counts in BOTH no-review and only-author**: a
   hand-crafted PR list where one PR has only an author review. Both
   percentages reflect it.
7. **Empty PR set** → count=0, all percentages None, all counts 0.
8. **Examples present** for no_review, fast_approval, deep_review on
   the fixture.
9. **Provider, definitions, repo, since, until fields populated correctly**.

Pinned exact-value tests for fixture metrics are tagged with a clear
comment so reviewers know they're brittle by design.

## What's NOT in this phase

- Anything that requires the GitHub client (live API calls). Metric
  functions are pure transforms over `NormalizedPR`.
- Caching of metric results — phase 8.
- Wiring metrics to FastMCP tools — phase 8.

## Acceptance

- New tests pass; existing tests still pass.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
- Importing every stub module does not raise (only calling raises).
