"""Canonical metric-definition strings embedded in every aggregate response.

Agents quote these verbatim to humans, so they are full sentences, name
thresholds inline, and stay short. Tool responses pick subsets via
:func:`definitions_for`, which raises ``KeyError`` on typos so unknown
metric references surface in tests rather than as silently-missing keys.

Numeric thresholds called out in these strings live alongside as module
constants and as environment-variable overrides where applicable. Keep the
two in sync: the strings are the user-visible source of truth.
"""

from __future__ import annotations

# Default thresholds. Where applicable these are overridable via env vars
# documented in docs/spec_v3.md.
FAST_APPROVAL_MIN_LINES_DEFAULT = 200
FAST_APPROVAL_MAX_SECONDS_DEFAULT = 300  # 5 minutes; see spec v2 §4
STALE_THRESHOLD_DAYS_DEFAULT = 14
RATE_LIMIT_FLOOR_DEFAULT = 100


DEFINITIONS: dict[str, str] = {
    # --- Active-repo discovery -------------------------------------------------
    "active_repo_pushed_at": (
        "A repo is reported as active if its GitHub pushed_at timestamp falls "
        "within the requested window. Note: pushed_at updates on any ref push, "
        "including force-pushes to feature branches, tag pushes, and branch "
        "deletes — so this counts feature-branch work, not just merges. Use "
        "has_merges_in_window for the cleaner 'did the team actually ship' signal."
    ),
    "active_repo_has_merges": (
        "True when at least one pull request was merged to the repo's default "
        "branch during the window. This excludes feature-branch pushes and "
        "branch-only activity."
    ),
    # --- Cycle time ------------------------------------------------------------
    "cycle_time_total": (
        "Cycle time per PR (hours): merged_at - created_at. Reported as median, "
        "p90, and mean across all PRs merged in the org within the window. "
        "Draft PRs are included; reopened PRs use the latest open-to-merge span. "
        "The per-repo breakdown applies the same math to each repo's PR subset."
    ),
    "cycle_time_first_review": (
        "Median number of hours from PR open to the first non-author review of "
        "any state (approved, changes_requested, or commented), across all PRs "
        "merged in the org within the window. PRs merged with no human review "
        "contribute null and are excluded from the median. The per-repo "
        "breakdown applies the same math per repo."
    ),
    "cycle_time_approval_to_merge": (
        "Median number of hours from the PR's first approving review to merge, "
        "across all PRs merged in the org within the window. PRs merged without "
        "an approving review contribute null and are excluded from the median. "
        "The per-repo breakdown applies the same math per repo."
    ),
    # --- PR size ---------------------------------------------------------------
    "pr_size_lines": (
        "Median and p90 of total lines changed (additions + deletions) per PR "
        "merged in the org within the window. The per-repo breakdown applies "
        "the same math per repo."
    ),
    "pr_size_files": (
        "Median number of files changed per PR merged in the org within the "
        "window. The per-repo breakdown applies the same math per repo."
    ),
    "pr_size_bucket": (
        "PR counts bucketed by total lines changed: xs (<10), s (10-99), "
        "m (100-499), l (500-1999), xl (2000+). Each PR belongs to exactly "
        "one bucket. Org buckets are element-wise sums of the per-repo buckets."
    ),
    # --- Review health ---------------------------------------------------------
    "review_reviewers_per_pr": (
        "Median count of distinct non-author reviewers that left any review "
        "(approve, request-changes, or comment) on each PR merged in the org "
        "within the window. The per-repo breakdown applies the same math per repo."
    ),
    "review_no_review": (
        "Percentage of PRs merged in the org within the window that had zero "
        "non-author reviews of any state. Bot-authored reviews are counted as "
        "non-author reviews if and only if the bot account is different from "
        "the PR author. The per-repo breakdown applies the same math per repo."
    ),
    "review_only_author": (
        "Percentage of PRs merged in the org within the window whose only "
        "reviews came from the PR author themself (self-review, no external "
        "eyes). The per-repo breakdown applies the same math per repo."
    ),
    "review_comments_per_pr": (
        "Median total number of review comments (line comments) per PR merged "
        "in the org within the window. PR-level comments and issue comments are "
        "excluded. The per-repo breakdown applies the same math per repo."
    ),
    "review_fast_approval": (
        "Count of approving reviews submitted less than 300 seconds (5 minutes) "
        "after the review was requested from that reviewer (or after PR creation "
        "if the reviewer was assigned at open time), on PRs with more than 200 "
        "total lines changed. Aggregated across the org for the window; the "
        "per-repo breakdown reports each repo's count. Reviewers who "
        "self-requested are excluded. Thresholds are configurable via "
        "GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS and "
        "GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES."
    ),
    "review_self_merge": (
        "Count of PRs merged by their own author across the org within the "
        "window. This includes both 'no reviews and self-merge' and 'reviewed "
        "by others but merged by author' cases. The per-repo breakdown reports "
        "each repo's count."
    ),
    # --- CI health -------------------------------------------------------------
    "ci_failing_check": (
        "Percentage of PRs merged in the org within the window where at least "
        "one required check run had a conclusion of 'failure', 'cancelled', or "
        "'timed_out' on any commit in the PR's history before merge. The "
        "per-repo breakdown applies the same math per repo."
    ),
    "ci_flaky_check": (
        "Percentage of PRs in the org within the window with at least one "
        "flaky check. A check is flaky for a PR when the same check name has "
        "at least one failed conclusion AND at least one successful conclusion "
        "against the SAME head SHA. This catches workflow re-runs on identical "
        "commits; it does NOT detect flakes that surface only across rebases. "
        "The per-repo breakdown applies the same math per repo."
    ),
    "ci_top_failing_checks": (
        "The check names that failed most often across the org's PR set in the "
        "window, ordered by failure count descending. Each entry includes the "
        "check name and the number of PRs in which it failed at least once. "
        "Computed from the full org PR set, not aggregated from per-repo slices."
    ),
    "ci_median_failed_runs_per_pr": (
        "Median number of failed check-run conclusions per PR merged in the "
        "org within the window, counting each failed run separately even if "
        "the same check name failed multiple times. The per-repo breakdown "
        "applies the same math per repo."
    ),
    # --- Stale ------------------------------------------------------------------
    "stale_pr": (
        "A PR is stale if it is still open and has had no commit, comment, "
        "review, or label activity for more than threshold_days days "
        "(default 14). Draft PRs are included. age_days is computed from "
        "PR open to as_of (defaults to today, UTC). The response reports the "
        "flat org-wide list of stale PRs alongside a per-repo grouping."
    ),
    # --- Merge activity ---------------------------------------------------------
    "merge_to_default_branch": (
        "Count of PRs merged to each repo's API-reported default_branch, "
        "bucketed by ISO week (week_starting is the Monday). The org weekly "
        "buckets are element-wise sums of the per-repo weekly buckets."
    ),
    "revert_commit": (
        "A commit on the default branch is a revert if its message starts "
        "with 'Revert \"' (GitHub's default for the Revert button) OR contains "
        "a trailer line matching '^Reverts #\\d+'. Hand-authored reverts that "
        "follow neither convention are not detected. The org revert count is "
        "the sum of per-repo revert counts."
    ),
    "merge_frequency_per_week": (
        "Average number of PRs merged to default branches per week across the "
        "org window. Computed as total merges divided by the number of full "
        "and partial weeks the window spans. The per-repo breakdown applies "
        "the same math per repo."
    ),
    # --- Portfolio --------------------------------------------------------------
    "portfolio_percentile_rank": (
        "For each metric, the repo's percentile rank within the active set "
        "of the org (0 = worst, 100 = best). 'Better' direction is "
        "metric-dependent: lower cycle time and lower CI failure rate are "
        "better; higher merge frequency is better. Inactive repos are excluded "
        "from the ranking even when include_inactive=true."
    ),
    # --- Baseline ---------------------------------------------------------------
    "baseline_pct_change": (
        "Percent change of the metric's org-level current value (over "
        "current_window_days) versus baseline value (over baseline_window_days, "
        "ending at the start of the current window). Positive pct_change means "
        "the value rose."
    ),
    "baseline_significance": (
        "Above the noise floor when the absolute pct_change exceeds 10% AND "
        "both the current and baseline windows had at least 5 data points "
        "across the org. Otherwise below the noise floor."
    ),
}


def definitions_for(*keys: str) -> dict[str, str]:
    """Return a subset of :data:`DEFINITIONS` for embedding in a tool response.

    Raises ``KeyError`` (with the offending key) if any requested key is not
    a known definition. This makes typos in tool response wiring fail loudly
    instead of returning silently-missing keys.
    """
    out: dict[str, str] = {}
    for k in keys:
        if k not in DEFINITIONS:
            raise KeyError(
                f"Unknown metric definition key: {k!r}. "
                f"Known keys: {sorted(DEFINITIONS)}"
            )
        out[k] = DEFINITIONS[k]
    return out


__all__ = [
    "DEFINITIONS",
    "FAST_APPROVAL_MAX_SECONDS_DEFAULT",
    "FAST_APPROVAL_MIN_LINES_DEFAULT",
    "RATE_LIMIT_FLOOR_DEFAULT",
    "STALE_THRESHOLD_DAYS_DEFAULT",
    "definitions_for",
]
