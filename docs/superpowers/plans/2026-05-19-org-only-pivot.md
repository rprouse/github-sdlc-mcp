# Org-only Pivot Implementation Plan (v0.2.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape `github-sdlc-mcp` so every tool takes a single `org` argument, the `repos.yaml` configuration layer is deleted, and the active-repo walker becomes the only fetch path. Ships as v0.2.0.

**Architecture:** One PAT (`GITHUB_TOKEN`, falling back to `GH_TOKEN`), cloud GitHub only (`https://api.github.com`). Tool flow: `org → walk_active_repos → fan out PR fetches → metric.compute → per-repo slices + org rollup`. Cross-server response contract (`ProviderResponse`/`AggregateResponse`) is preserved structurally and extended with per-repo slice fields.

**Tech Stack:** Python 3.12, `uv`, FastMCP, `httpx`, `respx` (test transport), `pydantic` v2, `pytest`, `ruff`, `mypy --strict`.

**Spec:** `docs/superpowers/specs/2026-05-19-org-only-pivot-design.md` (committed `ef2062e`).

**Deviation from spec to note:** the spec text uses `datetime` for tool-arg time fields. The existing codebase consistently uses `date` for tool args (converted to UTC-midnight `datetime` internally via `metrics/_stats.py::window_bounds`). This plan **uses `date`** for tool args to stay consistent with the rest of the codebase; spec §4/§6 should be read as "`date`-typed" in implementation. The plan corrects this in the spec in Task 18.

---

## File structure after the pivot

### Source files (`src/github_sdlc_mcp/`)

| File | Status | Responsibility |
|---|---|---|
| `__init__.py` | edit | Version string bump |
| `__main__.py` | edit | Drop `config` subcommand tree; construct `ServerContext` directly |
| `client/auth.py` | edit | `resolve_github_token() -> str` — single env lookup |
| `client/github.py` | edit | Drop `GitHubClientPool`; expose `make_github_client()` factory hardcoded to `api.github.com` |
| `client/__init__.py` | edit | Update re-exports |
| `active_repos.py` | edit | Accept `since: date, until: date` (unchanged); drop unused `total_repos_configured` concept from caller side |
| `cache.py` | edit | `clear(scope: Literal["all", "org"], org: str | None)` |
| `models.py` | edit | Drop `ConfiguredRepo`/`ConfiguredRepoList`; add `*RepoSlice` per metric; restructure each `*Stats` response: `org`, `repos: list[*RepoSlice]`, top-level aggregate; drop config fields from `HealthStatus`; `CacheClearResult` keyed on `org` |
| `metrics/cycle_time.py` | edit | `compute_cycle_time_stats(*, org, since, until, prs) -> PRCycleTimeStats` with per-repo slices + rollup |
| `metrics/review_health.py` | edit | Same shape change |
| `metrics/pr_size.py` | edit | Stub shape change |
| `metrics/ci_health.py` | edit | Stub shape change |
| `metrics/stale.py` | edit | Stub shape change |
| `metrics/merge_activity.py` | edit | Stub shape change |
| `metrics/portfolio.py` | edit | Signature change (still raises) |
| `metrics/baseline.py` | edit | Signature change |
| `metrics/definitions.py` | edit | Definition strings updated to org-level language |
| `server.py` | edit | New `ServerContext`; all `*_impl` take `org`; register `list_active_repos`; remove `list_configured_repos`; `clear_cache` keyed on `org`; `health_check` no config fields |
| `config.py` | **delete** | — |
| `_templates/repos.yaml` | **delete** | — |
| `_templates/__init__.py` | **delete** | — |

### Test files (`tests/`)

| File | Status |
|---|---|
| `conftest.py` | edit — add `multiplexed_prs` helper |
| `fixtures/__init__.py` | edit — expose `multiplexed_pr_nodes`, `iter_active_repo_nodes` |
| `fixtures/github_active_repos.json` | **new** — paginated `organization.repositories` response for `org=acme` |
| `test_auth.py` | edit — assert new `resolve_github_token` |
| `test_github_client.py` | edit — drop pool tests; assert single client factory |
| `test_active_repos.py` | edit — assert tool surface for `list_active_repos` (no `host`/configured filter) |
| `test_cache.py` | edit — `scope="org"` |
| `test_metrics_cycle_time.py` | edit — assert per-repo slices + rollup |
| `test_metrics_review_health.py` | edit — same |
| `test_models.py` | edit — drop `ConfiguredRepo*`; assert new slice models |
| `test_server_tools.py` | edit — new signatures |
| `test_normalize.py` | unchanged |
| `test_rate_limit.py` | unchanged |
| `test_scaffold.py` | likely unchanged |
| `test_config_resolution.py` | **delete** |
| `test_metrics_per_repo_invariant.py` | **new** — cross-cutting "slices sum to rollup" parametrized over all metrics |
| `test_metrics_empty_org.py` | **new** — empty-org + zero-PR-in-window edge cases |

### Docs

| File | Status |
|---|---|
| `docs/spec_v3.md` | **new** — delta over `spec_v2.md` |
| `docs/plans/09-org-only-pivot.md` | **new** — half-page phase plan referencing this implementation plan |
| `docs/plans/02-config-resolution.md` | **move** → `docs/plans/archive/02-config-resolution.md` |
| `README.md` | edit — replace "Configuration" with "Authentication" + org-only examples |
| `CLAUDE.md` | edit — update "Authoritative spec", "Architecture", "Conventions" sections; remove all config references |

### Top-level

| File | Status |
|---|---|
| `pyproject.toml` | edit — `version = "0.2.0"`, drop `pyyaml` from `[project.dependencies]` if present |
| `uv.lock` | edit — regenerated by `uv lock` |

---

## Phase 1 — Docs and pre-work

### Task 1: Add spec v3, phase-09 plan, archive phase-02

**Files:**
- Create: `docs/spec_v3.md`
- Create: `docs/plans/09-org-only-pivot.md`
- Move: `docs/plans/02-config-resolution.md` → `docs/plans/archive/02-config-resolution.md`
- Modify: `CLAUDE.md` (Authoritative-spec pointer)

- [ ] **Step 1: Create `docs/spec_v3.md`**

```markdown
# Spec v3: github-sdlc-mcp — org-only pivot

## Status

Authoritative as of v0.2.0. Delta document over `spec_v2.md`. Where
v3 and v2 conflict, v3 wins.

## Why

v2 assumed each MCP-server install maintained a `repos.yaml` listing
the repos it tracks. v3 removes that layer: the calling agent (a
"combining skill" that maps companies to GitHub orgs) supplies an
`org` at tool-invocation time and the server walks every active repo
in that org.

## Resolved decisions (delta from v2)

1. **Single entry point: `org: str`.** No `repo` argument anywhere.
2. **One org per call.** Skill loops when a company spans multiple orgs.
3. **Per-repo breakdown + org aggregate** on every metric response.
4. **`since` required, `until` defaults to today** (date-typed, inside the impl).
5. **Single token env var.** `GITHUB_TOKEN`, falling back to `GH_TOKEN`.
6. **Cloud-only.** `https://api.github.com` hardcoded. GHES out of scope.
7. **Org-only (not user accounts).** `organization(login:)` is the GraphQL entry.
8. **Activity signal: `pushed_at`** on the repo node (walker default).
9. **`list_active_repos(org, since)`** exposed as a discovery tool.
10. **Hard cut to v0.2.0.** `config.py`, `repos.yaml`, `*Config` models deleted.

## Implementation plan

See `docs/superpowers/plans/2026-05-19-org-only-pivot.md`.
```

- [ ] **Step 2: Create `docs/plans/09-org-only-pivot.md`**

```markdown
# Phase 9: Org-only pivot (v0.2.0)

Drops the `repos.yaml` configuration layer. Every tool takes a
single `org: str` argument; the active-repo walker becomes the only
fetch path; metric responses gain a per-repo breakdown alongside
the org-level rollup.

See `docs/spec_v3.md` for resolved decisions and
`docs/superpowers/plans/2026-05-19-org-only-pivot.md` for the
step-by-step implementation plan.
```

- [ ] **Step 3: Move the v2 config plan into archive**

```powershell
New-Item -ItemType Directory -Force docs/plans/archive
git mv docs/plans/02-config-resolution.md docs/plans/archive/02-config-resolution.md
```

- [ ] **Step 4: Update `CLAUDE.md` "Authoritative spec" pointer**

Find the block under `## Authoritative spec` and replace its first
sentence with:

```
`docs/spec_v3.md` is authoritative for v0.2.0+. It is a delta over
`docs/spec_v2.md`, which in turn was a delta over `docs/initial_spec.md`.
Where they conflict, v3 wins.
```

- [ ] **Step 5: Run tests to confirm nothing broke**

```
uv run pytest -q
```

Expected: PASS — no code touched, just docs.

- [ ] **Step 6: Commit**

```powershell
git add docs/spec_v3.md docs/plans/09-org-only-pivot.md docs/plans/archive/02-config-resolution.md CLAUDE.md
git rm docs/plans/02-config-resolution.md 2>$null
git commit -m @'
docs: add spec v3 and phase-09 plan; archive phase-02

Spec v3 delta document for the v0.2.0 org-only pivot. Phase-09
short plan references the detailed implementation plan under
docs/superpowers/plans/. Phase-02 (config resolution) is moved
to docs/plans/archive/ since the layer it documents is about to
be deleted.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

---

## Phase 2 — Test infrastructure (additive)

### Task 2: Add active-repo fixture file

**Files:**
- Create: `tests/fixtures/github_active_repos.json`

- [ ] **Step 1: Create the fixture**

`tests/fixtures/github_active_repos.json` — paginated
`organization.repositories` response for `org=acme` with 3 in-window
repos and 1 out-of-window repo to exercise early termination. Two
pages so `paginate_graphql` actually paginates.

```json
[
  {
    "data": {
      "organization": {
        "repositories": {
          "nodes": [
            {
              "name": "web",
              "pushedAt": "2026-05-14T12:22:29Z",
              "defaultBranchRef": { "name": "main" }
            },
            {
              "name": "api",
              "pushedAt": "2026-05-10T09:00:00Z",
              "defaultBranchRef": { "name": "main" }
            }
          ],
          "pageInfo": { "hasNextPage": true, "endCursor": "cursor-1" }
        }
      }
    }
  },
  {
    "data": {
      "organization": {
        "repositories": {
          "nodes": [
            {
              "name": "lib",
              "pushedAt": "2026-05-05T03:30:00Z",
              "defaultBranchRef": { "name": "main" }
            },
            {
              "name": "ancient",
              "pushedAt": "2024-11-01T00:00:00Z",
              "defaultBranchRef": { "name": "main" }
            }
          ],
          "pageInfo": { "hasNextPage": false, "endCursor": null }
        }
      }
    }
  }
]
```

- [ ] **Step 2: Run tests to confirm nothing broke**

```
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add tests/fixtures/github_active_repos.json
git commit -m @'
tests: add organization.repositories fixture for org acme

Two-page paginated response containing 3 in-window repos
(web/api/lib) and 1 out-of-window repo (ancient) to exercise
walker early-termination. Used by upcoming list_active_repos
and per-metric org-level tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task 3: Add `multiplexed_prs` test helper

**Files:**
- Modify: `tests/fixtures/__init__.py` (add `iter_active_repo_nodes`)
- Modify: `tests/conftest.py` (add `multiplexed_prs`)
- Create: `tests/test_fixtures.py`

- [ ] **Step 1: Write the failing test**

`tests/test_fixtures.py`:

```python
"""Tests for tests/fixtures and tests/conftest helpers."""

from __future__ import annotations

from tests.conftest import multiplexed_prs
from tests.fixtures import iter_active_repo_nodes, iter_pr_nodes


def test_multiplexed_prs_stamps_owner_and_repo():
    targets = [("acme", "web"), ("acme", "api"), ("acme", "lib")]
    prs = multiplexed_prs(targets)
    base_count = sum(1 for _ in iter_pr_nodes())

    assert len(prs) == base_count * len(targets)

    by_repo: dict[str, int] = {}
    for pr in prs:
        assert pr.owner == "acme"
        by_repo[pr.repo] = by_repo.get(pr.repo, 0) + 1

    assert by_repo == {"web": base_count, "api": base_count, "lib": base_count}


def test_iter_active_repo_nodes_yields_four_repos():
    nodes = list(iter_active_repo_nodes())
    names = [n["name"] for n in nodes]
    assert names == ["web", "api", "lib", "ancient"]
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/test_fixtures.py -q
```

Expected: FAIL — `iter_active_repo_nodes` not defined, `multiplexed_prs` not defined.

- [ ] **Step 3: Add `iter_active_repo_nodes` to `tests/fixtures/__init__.py`**

Append to `tests/fixtures/__init__.py`:

```python
ACTIVE_REPOS_FIXTURE = Path(__file__).parent / "github_active_repos.json"


def iter_active_repo_nodes() -> Iterator[dict[str, Any]]:
    """Yield every repo node from the paginated active-repos fixture."""
    with ACTIVE_REPOS_FIXTURE.open("r", encoding="utf-8") as f:
        pages = json.load(f)
    for page in pages:
        for node in page["data"]["organization"]["repositories"]["nodes"]:
            yield node
```

If the file already imports `json`, `Path`, `Iterator`, `Any`, reuse;
otherwise add the imports at the top.

- [ ] **Step 4: Add `multiplexed_prs` to `tests/conftest.py`**

Replace `tests/conftest.py` with:

```python
"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from github_sdlc_mcp.models import NormalizedPR
from github_sdlc_mcp.normalize import normalize_pr
from tests.fixtures import iter_pr_nodes


def multiplexed_prs(targets: list[tuple[str, str]]) -> list[NormalizedPR]:
    """Stamp the canonical per-repo PR fixture across multiple (owner, repo) pairs.

    The fixture in tests/fixtures/github_prs.json is calibrated for one
    repo (see CLAUDE.md "Synthetic fixture"). This helper replays it
    across N (owner, repo) pairs so org-level tests get a deterministic
    multi-repo input without regenerating the fixture.

    Output is len(targets) * <fixture-PR-count> NormalizedPR instances.
    """
    return [
        normalize_pr(node, owner=owner, repo=repo)
        for owner, repo in targets
        for node in iter_pr_nodes()
    ]
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/test_fixtures.py -q
```

Expected: PASS.

- [ ] **Step 6: Full test suite still green**

```
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add tests/conftest.py tests/fixtures/__init__.py tests/test_fixtures.py
git commit -m @'
tests: add multiplexed_prs and iter_active_repo_nodes helpers

multiplexed_prs replays the calibrated per-repo PR fixture across
multiple (owner, repo) pairs so upcoming org-level metric tests
get deterministic multi-repo input without regenerating fixtures.
iter_active_repo_nodes flattens the new paginated active-repos
fixture.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

---

## Phase 3 — Big pivot (one atomic commit)

This is the load-bearing change. It must land in a single commit so
the tree stays internally consistent. The task is broken into ordered
sub-steps; do not commit until **all** sub-steps complete and the
full suite passes.

### Task 4: Atomic pivot — models, auth, client, metrics, server

**Files (all in one commit):**
- Modify: `src/github_sdlc_mcp/models.py`
- Modify: `src/github_sdlc_mcp/client/auth.py`
- Modify: `src/github_sdlc_mcp/client/github.py`
- Modify: `src/github_sdlc_mcp/client/__init__.py`
- Modify: `src/github_sdlc_mcp/cache.py`
- Modify: `src/github_sdlc_mcp/metrics/cycle_time.py`
- Modify: `src/github_sdlc_mcp/metrics/review_health.py`
- Modify: `src/github_sdlc_mcp/metrics/pr_size.py`
- Modify: `src/github_sdlc_mcp/metrics/ci_health.py`
- Modify: `src/github_sdlc_mcp/metrics/stale.py`
- Modify: `src/github_sdlc_mcp/metrics/merge_activity.py`
- Modify: `src/github_sdlc_mcp/metrics/portfolio.py`
- Modify: `src/github_sdlc_mcp/metrics/baseline.py`
- Modify: `src/github_sdlc_mcp/metrics/definitions.py`
- Modify: `src/github_sdlc_mcp/server.py`
- Modify: `src/github_sdlc_mcp/__main__.py`
- Delete: `src/github_sdlc_mcp/config.py`
- Delete: `src/github_sdlc_mcp/_templates/repos.yaml`
- Delete: `src/github_sdlc_mcp/_templates/__init__.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_github_client.py`
- Modify: `tests/test_cache.py`
- Modify: `tests/test_active_repos.py`
- Modify: `tests/test_metrics_cycle_time.py`
- Modify: `tests/test_metrics_review_health.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_server_tools.py`
- Delete: `tests/test_config_resolution.py`
- Modify: `pyproject.toml` (drop `pyyaml`; bump version to `0.2.0`)
- Regenerate: `uv.lock`

#### Sub-step 4.1 — Update `models.py`

- [ ] Replace `src/github_sdlc_mcp/models.py` with the new shape.

Key changes:
- Drop `ConfiguredRepo`, `ConfiguredRepoList`.
- Drop `company_label` from `ActiveRepo`. Drop `total_repos_configured` from `ActiveRepoList`.
- Drop `config_status`, `config_path`, `config_searched_paths`, `config_error` from `HealthStatus`.
- Add per-metric `*RepoSlice` models. Each existing `*Stats` response gains `org: str` and `repos: list[*RepoSlice]` and keeps its previous per-repo fields renamed semantically as the **org aggregate**.
- `CacheClearResult` keyed on `org` instead of `repo`.

```python
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
    "success", "failure", "neutral", "cancelled", "timed_out",
    "action_required", "skipped", "stale",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Provider envelope ---------------------------------------------------------


class ProviderResponse(_Strict):
    provider: Literal["github"] = "github"


class AggregateResponse(ProviderResponse):
    definitions: dict[str, str]


# Representative examples ---------------------------------------------------


class ExamplePR(_Strict):
    number: int
    title: str
    author: str
    url: str
    label: str
    value: float
    unit: str


# Internal normalized models ------------------------------------------------


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


# Discovery tool responses --------------------------------------------------


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


# Per-repo metric slices ----------------------------------------------------


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


# Org-aggregate metric responses --------------------------------------------


class PRSizeBuckets(_Strict):
    xs: int = Field(ge=0)
    s: int = Field(ge=0)
    m: int = Field(ge=0)
    l: int = Field(ge=0)  # noqa: E741
    xl: int = Field(ge=0)


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


class FailingCheck(_Strict):
    name: str
    count: int


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


class StalePR(_Strict):
    number: int
    title: str
    author: str
    age_days: int
    last_activity: AwareDatetime
    url: str


class StalePRList(AggregateResponse):
    org: str
    as_of: date
    threshold_days: int
    count: int
    prs: list[StalePR]
    repos: list[StaleRepoSlice]


class WeeklyBucket(_Strict):
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


class MergeActivityStats(AggregateResponse):
    org: str
    since: date
    until: date
    merges_to_default_branch: list[WeeklyBucket]
    revert_commit_count: int
    merge_frequency_per_week: float
    repos: list[MergeActivityRepoSlice]


# Portfolio / cross-repo ----------------------------------------------------


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
```

**Note:** `PRSizeBuckets` is referenced by `PRSizeRepoSlice` before its
definition in the listing above. Reorder the actual file so
`PRSizeBuckets`, `StalePR`, `WeeklyBucket` appear **before** the slice
classes that reference them.

#### Sub-step 4.2 — Simplify `client/auth.py`

- [ ] Replace `src/github_sdlc_mcp/client/auth.py`:

```python
"""PAT resolution from environment variables.

v0.2.0 collapses the per-host token lookup to a single env var read.
Cloud GitHub only — no GHES, no per-host overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


class MissingTokenError(Exception):
    """Raised when neither GITHUB_TOKEN nor GH_TOKEN is set in the env."""

    def __init__(self) -> None:
        super().__init__(
            "GitHub token not found: set GITHUB_TOKEN (or GH_TOKEN) "
            "in the environment and restart the MCP host."
        )


def resolve_github_token(env: Mapping[str, str] | None = None) -> str:
    """Return the GitHub PAT, preferring GITHUB_TOKEN over GH_TOKEN."""
    source = os.environ if env is None else env
    raw = source.get("GITHUB_TOKEN") or source.get("GH_TOKEN")
    if not raw:
        raise MissingTokenError()
    return raw
```

#### Sub-step 4.3 — Simplify `client/github.py` (drop pool)

- [ ] Open `src/github_sdlc_mcp/client/github.py`. Remove `GitHubClientPool`. Adapt the existing `GitHubClient` constructor to take an explicit `token: str` and `base_url: str = "https://api.github.com"`. Replace the pool-creation public entry point with a `make_github_client()` factory:

```python
def make_github_client(
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> GitHubClient:
    """Construct a single GitHubClient bound to api.github.com.

    ``transport`` is the respx injection point for tests. ``sleep`` is
    likewise injectable so rate-limit tests assert exact pause durations.
    """
    token = resolve_github_token(env)
    return GitHubClient(
        token=token,
        base_url="https://api.github.com",
        transport=transport,
        sleep=sleep,
    )
```

(Keep the inner `GitHubClient` class signature aligned with how it is
constructed in the pool today — just remove the pool layer above it.)

- [ ] Update `src/github_sdlc_mcp/client/__init__.py` to drop the pool re-export and add `make_github_client`.

#### Sub-step 4.4 — Update `cache.py` scope

- [ ] In `src/github_sdlc_mcp/cache.py`:
  - Change `Cache.clear` protocol signature to `scope: Literal["all", "org"] = "all", org: str | None = None`.
  - Change `TTLCache.clear` likewise; filter by `entry.kwargs.get("org") == org` when `scope == "org"`.
  - Update the docstring header — remove the `scope="repo"` reference, replace with `scope="org"`.

#### Sub-step 4.5 — Update each metric's compute function

The pattern is identical for each metric. Walk through `cycle_time` in full;
apply the same shape change to the others.

- [ ] `src/github_sdlc_mcp/metrics/cycle_time.py` — rewrite to:

```python
"""PR cycle-time statistics over a window of merged PRs across an org."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
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
        examples=_examples(merged, rollup.cycle_hours_by_number, rollup.median),
        repos=repos,
        definitions=defs,
        **rollup.as_kwargs(),
    )
```

Add two private helpers in the same file: `_per_repo_slices` (groups
`merged` by `pr.repo` and returns `list[CycleTimeRepoSlice]` — same
math as the rollup but constrained to each group) and `_aggregate` (a
tiny dataclass with the org-wide stats and a small `as_kwargs()` so
the response construction stays tidy). Use `itertools.groupby` after
sorting by `pr.repo` for stable grouping.

```python
from dataclasses import dataclass, field


@dataclass
class _RollupValues:
    count: int
    median: float | None
    p90: float | None
    mean: float | None
    median_first_review: float | None
    median_approval_to_merge: float | None
    cycle_hours_by_number: dict[int, float] = field(default_factory=dict)

    def as_kwargs(self) -> dict[str, object]:
        return {
            "count": self.count,
            "median_hours": round2(self.median),
            "p90_hours": round2(self.p90),
            "mean_hours": round2(self.mean),
            "median_time_to_first_review_hours": round2(self.median_first_review),
            "median_approval_to_merge_hours": round2(self.median_approval_to_merge),
        }


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
```

Keep the existing `_cycle_hours`, `_first_non_author_review_hours`,
`_approval_to_merge_hours`, `_examples` helpers unchanged.

- [ ] **Apply the identical pattern** to the remaining six metric files. The pattern is mechanical — only the slice class name and the per-metric field set differ. For each file:
  1. Change the compute function's first kwarg from `repo: str` to `org: str`.
  2. Pull the existing per-repo math out into a private `_aggregate(group)` helper.
  3. Add `_per_repo_slices(merged)` that groups by `pr.repo` via `groupby` after sorting, calls `_aggregate(group)` per group, and constructs the matching `*RepoSlice` from §4.1.
  4. Construct the response with `org=org, …, repos=_per_repo_slices(merged), definitions=defs`.

  The six files and their slice types:
  - `metrics/review_health.py` → `ReviewHealthRepoSlice`. Carry `fast_approval_min_lines` / `fast_approval_max_seconds` kwargs through unchanged.
  - `metrics/pr_size.py` → `PRSizeRepoSlice`. Per-repo distribution buckets aggregate element-wise to the org buckets.
  - `metrics/ci_health.py` → `CIHealthRepoSlice` (`pct_with_failing_check`, etc.). The org-wide `top_failing_checks` is computed from the full PR list, not aggregated from slices.
  - `metrics/merge_activity.py` → `MergeActivityRepoSlice`. Per-repo weekly buckets; org weekly buckets are element-wise sums.
  - `metrics/stale.py` → `StaleRepoSlice`. Group `prs` by repo into the slice's `prs` field; the response's top-level `prs:` keeps the flat org-wide view.
  - `metrics/portfolio.py` → no slice; signature changes to `compute_portfolio_summary(*, org, since, until, prs, include_inactive=False)`; body remains `raise NotImplementedError("compute_portfolio_summary is stubbed; see docs/spec_v3.md.")`.
  - `metrics/baseline.py` → no slice; `compare_to_baseline(*, org, metric, as_of, current_window_days, baseline_window_days)`; body still raises.

- [ ] **Ensure `src/github_sdlc_mcp/metrics/__init__.py` re-exports `compute_portfolio_summary`.** The current `__init__.py` may not list it because v0.1's `get_portfolio_summary_impl` raised directly. The new `server.py` imports it from the package, so it must be exported.

#### Sub-step 4.6 — Definition strings (`metrics/definitions.py`)

- [ ] Open `src/github_sdlc_mcp/metrics/definitions.py`. Update every definition string from per-repo wording to org-level wording. Example:

```python
DEFINITIONS["cycle_time_total"] = (
    "Cycle time per PR (hours): merged_at − created_at. Reported as median "
    "and p90 across all PRs merged in the org within the window. The "
    "per-repo breakdown applies the same math to each repo's PR subset."
)
```

Apply analogous edits to every other entry. The orphan/typo tests in
`tests/test_models.py` will fail if any key drifts.

#### Sub-step 4.7 — Rewrite `server.py`

- [ ] Replace `src/github_sdlc_mcp/server.py` with the new orchestration layer. Key shape:

```python
"""FastMCP server assembly, tool implementations, and the public ``build_server``."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, TypeVar, cast

import httpx
from fastmcp import FastMCP

from github_sdlc_mcp.active_repos import DiscoveredRepo, discover_active_repos
from github_sdlc_mcp.cache import Cache, TTLCache
from github_sdlc_mcp.client.github import GitHubClient, make_github_client
from github_sdlc_mcp.metrics import (
    compare_to_baseline,
    compute_ci_health,
    compute_cycle_time_stats,
    compute_merge_activity,
    compute_portfolio_summary,
    compute_pr_size_stats,
    compute_review_health,
    compute_stale_prs,
)
from github_sdlc_mcp.metrics.definitions import (
    FAST_APPROVAL_MAX_SECONDS_DEFAULT,
    FAST_APPROVAL_MIN_LINES_DEFAULT,
    definitions_for,
)
from github_sdlc_mcp.models import (
    ActiveRepo,
    ActiveRepoList,
    BaselineComparison,
    CacheClearResult,
    CIHealthStats,
    HealthStatus,
    MergeActivityStats,
    NormalizedPR,
    PortfolioSummary,
    PRCycleTimeStats,
    PRSizeStats,
    ReviewHealthStats,
    StalePRList,
)
from github_sdlc_mcp.normalize import PR_PAGE_QUERY, normalize_pr

logger = logging.getLogger(__name__)
T = TypeVar("T")

_MAX_CONCURRENT_REPO_FETCHES = 8


@dataclass
class ServerContext:
    client: GitHubClient
    cache: Cache
    env: Mapping[str, str] = field(default_factory=dict)


def build_context(
    *,
    cache: Cache | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ServerContext:
    return ServerContext(
        client=make_github_client(env=env, transport=transport),
        cache=cache or TTLCache(),
        env=env or os.environ,
    )


async def cached_call(
    ctx: ServerContext,
    tool_name: str,
    kwargs: Mapping[str, Any],
    compute: Callable[[], Awaitable[T]],
) -> T:
    hit = await ctx.cache.get(tool_name, kwargs)
    if hit is not None:
        return cast(T, hit)
    value = await compute()
    await ctx.cache.set(tool_name, kwargs, value)
    return value


# Active-repo + PR fetch helpers --------------------------------------------


async def _active_repos(
    ctx: ServerContext, *, org: str, since: date, until: date
) -> tuple[list[DiscoveredRepo], int]:
    return await discover_active_repos(
        ctx.client, org=org, since=since, until=until
    )


async def _load_prs_for_org(
    ctx: ServerContext, *, org: str, since: date, until: date
) -> list[NormalizedPR]:
    """Walk active repos and fan-out PR fetches with a concurrency cap."""
    repos, _scanned = await _active_repos(ctx, org=org, since=since, until=until)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_REPO_FETCHES)

    async def _fetch(d: DiscoveredRepo) -> list[NormalizedPR]:
        async with sem:
            return await _load_prs_for_repo(ctx, d.owner, d.repo, since)

    chunks = await asyncio.gather(*[_fetch(d) for d in repos])
    return [pr for chunk in chunks for pr in chunk]


async def _load_prs_for_repo(
    ctx: ServerContext, owner: str, repo: str, since: date
) -> list[NormalizedPR]:
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=UTC)
    prs: list[NormalizedPR] = []
    async for node in ctx.client.paginate_graphql(
        PR_PAGE_QUERY,
        {"owner": owner, "name": repo},
        connection_path=["repository", "pullRequests"],
    ):
        updated_at = _opt_dt(node.get("updatedAt"))
        if updated_at is not None and updated_at < since_dt:
            break
        prs.append(normalize_pr(node, owner=owner, repo=repo))
    return prs


def _opt_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _resolve_until(until: date | None) -> date:
    return until if until is not None else datetime.now(UTC).date()


# Threshold helpers (unchanged from v0.1) -----------------------------------


def _read_threshold_int(ctx: ServerContext, var: str, default: int) -> int:
    raw = ctx.env.get(var)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid value for %s=%r; falling back to %d", var, raw, default)
        return default


def _read_threshold_float(ctx: ServerContext, var: str, default: float) -> float:
    raw = ctx.env.get(var)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid value for %s=%r; falling back to %g", var, raw, default)
        return default


# Tool impls — discovery / admin --------------------------------------------


async def list_active_repos_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> ActiveRepoList:
    resolved_until = _resolve_until(until)
    kwargs: dict[str, Any] = {"org": org, "since": since, "until": until}

    async def _compute() -> ActiveRepoList:
        discovered, scanned = await _active_repos(
            ctx, org=org, since=since, until=resolved_until
        )
        return ActiveRepoList(
            org=org,
            since=since,
            until=resolved_until,
            total_repos_scanned=scanned,
            total_repos_active=len(discovered),
            repos=[
                ActiveRepo(
                    owner=d.owner,
                    repo=d.repo,
                    pushed_at=d.pushed_at,
                    default_branch=d.default_branch,
                    has_merges_in_window=d.has_merges_in_window,
                )
                for d in discovered
            ],
            definitions=definitions_for(
                "active_repo_pushed_at", "active_repo_has_merges"
            ),
        )

    return await cached_call(ctx, "list_active_repos", kwargs, _compute)


async def health_check_impl(ctx: ServerContext) -> HealthStatus:
    state = ctx.client.rate_limit
    return HealthStatus(
        ok=True,
        rate_limit_remaining=state.remaining,
        rate_limit_resets_at=state.resets_at,
        last_successful_call_at=ctx.client.last_successful_call_at,
        cache_entries=await ctx.cache.size(),
    )


async def clear_cache_impl(
    ctx: ServerContext,
    *,
    scope: Literal["all", "org"] = "all",
    org: str | None = None,
) -> CacheClearResult:
    cleared = await ctx.cache.clear(scope=scope, org=org)
    return CacheClearResult(scope=scope, org=org, entries_cleared=cleared)


# Tool impls — metrics ------------------------------------------------------


async def get_pr_cycle_time_stats_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> PRCycleTimeStats:
    resolved_until = _resolve_until(until)

    async def _compute() -> PRCycleTimeStats:
        prs = await _load_prs_for_org(
            ctx, org=org, since=since, until=resolved_until
        )
        return compute_cycle_time_stats(
            org=org, since=since, until=resolved_until, prs=prs
        )

    return await cached_call(
        ctx,
        "get_pr_cycle_time_stats",
        {"org": org, "since": since, "until": until},
        _compute,
    )


async def get_review_health_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> ReviewHealthStats:
    resolved_until = _resolve_until(until)
    min_lines = _read_threshold_int(
        ctx, "GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES", FAST_APPROVAL_MIN_LINES_DEFAULT
    )
    max_seconds = _read_threshold_float(
        ctx,
        "GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS",
        FAST_APPROVAL_MAX_SECONDS_DEFAULT,
    )

    async def _compute() -> ReviewHealthStats:
        prs = await _load_prs_for_org(
            ctx, org=org, since=since, until=resolved_until
        )
        return compute_review_health(
            org=org,
            since=since,
            until=resolved_until,
            prs=prs,
            fast_approval_min_lines=min_lines,
            fast_approval_max_seconds=max_seconds,
        )

    return await cached_call(
        ctx,
        "get_review_health",
        {
            "org": org,
            "since": since,
            "until": until,
            "min_lines": min_lines,
            "max_seconds": max_seconds,
        },
        _compute,
    )


async def get_pr_size_stats_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> PRSizeStats:
    resolved_until = _resolve_until(until)
    prs = await _load_prs_for_org(
        ctx, org=org, since=since, until=resolved_until
    )
    return compute_pr_size_stats(
        org=org, since=since, until=resolved_until, prs=prs
    )


async def get_ci_health_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> CIHealthStats:
    resolved_until = _resolve_until(until)
    prs = await _load_prs_for_org(
        ctx, org=org, since=since, until=resolved_until
    )
    return compute_ci_health(
        org=org, since=since, until=resolved_until, prs=prs
    )


async def get_stale_prs_impl(
    ctx: ServerContext,
    *,
    org: str,
    threshold_days: int = 14,
    as_of: date | None = None,
) -> StalePRList:
    today = as_of or datetime.now(UTC).date()
    prs = await _load_prs_for_org(ctx, org=org, since=today, until=today)
    return compute_stale_prs(
        org=org, prs=prs, threshold_days=threshold_days, as_of=as_of
    )


async def get_merge_activity_impl(
    ctx: ServerContext, *, org: str, since: date, until: date | None = None
) -> MergeActivityStats:
    resolved_until = _resolve_until(until)
    prs = await _load_prs_for_org(
        ctx, org=org, since=since, until=resolved_until
    )
    return compute_merge_activity(
        org=org,
        since=since,
        until=resolved_until,
        prs=prs,
        default_branch_commits=[],
    )


async def get_portfolio_summary_impl(
    ctx: ServerContext,
    *,
    org: str,
    since: date,
    until: date | None = None,
    include_inactive: bool = False,
) -> PortfolioSummary:
    resolved_until = _resolve_until(until)
    return compute_portfolio_summary(
        org=org,
        since=since,
        until=resolved_until,
        prs=[],
        include_inactive=include_inactive,
    )


async def compare_to_baseline_impl(
    ctx: ServerContext,
    *,
    org: str,
    metric: str,
    current_window_days: int = 30,
    baseline_window_days: int = 90,
) -> BaselineComparison:
    today = datetime.now(UTC).date()
    return compare_to_baseline(
        org=org,
        metric=metric,
        as_of=today,
        current_window_days=current_window_days,
        baseline_window_days=baseline_window_days,
    )


# FastMCP wiring ------------------------------------------------------------


TOOL_NAMES = (
    "list_active_repos",
    "health_check",
    "clear_cache",
    "get_pr_cycle_time_stats",
    "get_review_health",
    "get_pr_size_stats",
    "get_ci_health",
    "get_stale_prs",
    "get_merge_activity",
    "get_portfolio_summary",
    "compare_to_baseline",
)


def build_server(ctx: ServerContext) -> FastMCP:
    mcp: FastMCP = FastMCP("github-sdlc-mcp")

    @mcp.tool()
    async def list_active_repos(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await list_active_repos_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def health_check() -> dict[str, Any]:
        result = await health_check_impl(ctx)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def clear_cache(
        scope: Literal["all", "org"] = "all", org: str | None = None
    ) -> dict[str, Any]:
        result = await clear_cache_impl(ctx, scope=scope, org=org)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_cycle_time_stats(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_pr_cycle_time_stats_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_review_health(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_review_health_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_pr_size_stats(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_pr_size_stats_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_ci_health(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_ci_health_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_stale_prs(
        org: str, threshold_days: int = 14, as_of: date | None = None
    ) -> dict[str, Any]:
        result = await get_stale_prs_impl(
            ctx, org=org, threshold_days=threshold_days, as_of=as_of
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_merge_activity(
        org: str, since: date, until: date | None = None
    ) -> dict[str, Any]:
        result = await get_merge_activity_impl(
            ctx, org=org, since=since, until=until
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def get_portfolio_summary(
        org: str,
        since: date,
        until: date | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        result = await get_portfolio_summary_impl(
            ctx,
            org=org,
            since=since,
            until=until,
            include_inactive=include_inactive,
        )
        return result.model_dump(mode="json")

    @mcp.tool()
    async def compare_to_baseline_tool(
        org: str,
        metric: str,
        current_window_days: int = 30,
        baseline_window_days: int = 90,
    ) -> dict[str, Any]:
        result = await compare_to_baseline_impl(
            ctx,
            org=org,
            metric=metric,
            current_window_days=current_window_days,
            baseline_window_days=baseline_window_days,
        )
        return result.model_dump(mode="json")

    return mcp
```

#### Sub-step 4.8 — Strip `config` subcommands from `__main__.py`

- [ ] Replace `src/github_sdlc_mcp/__main__.py` with:

```python
"""CLI entry point: run the FastMCP server. No config subcommands in v0.2.0."""

from __future__ import annotations

import argparse
import logging
import sys

from github_sdlc_mcp import __version__

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Send all logs to stderr. stdout belongs to the MCP JSON-RPC channel."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-sdlc-mcp",
        description="FastMCP server exposing SDLC metrics derived from GitHub.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"github-sdlc-mcp {__version__}"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport. Defaults to stdio (the MCP host's default).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for --transport streamable-http (ignored otherwise).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)

    from github_sdlc_mcp.server import build_context, build_server

    ctx = build_context()
    server = build_server(ctx)
    logger.info(
        "Starting FastMCP server (transport=%s%s)",
        args.transport,
        f", port={args.port}" if args.transport != "stdio" else "",
    )
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(transport="streamable-http", port=args.port, show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

#### Sub-step 4.9 — Delete config files

- [ ] Run:

```powershell
git rm src/github_sdlc_mcp/config.py
git rm src/github_sdlc_mcp/_templates/repos.yaml
git rm src/github_sdlc_mcp/_templates/__init__.py
git rm tests/test_config_resolution.py
```

If `_templates` has no other files, also remove the directory.

#### Sub-step 4.10 — Update `pyproject.toml`

- [ ] Open `pyproject.toml`. In `[project]`:
  - Set `version = "0.2.0"`.
  - In `dependencies`, remove the `"pyyaml"` line (config is gone). Keep `platformdirs` only if other code references it; otherwise remove.
- [ ] In `src/github_sdlc_mcp/__init__.py`, update `__version__ = "0.2.0"`.

#### Sub-step 4.11 — Regenerate `uv.lock`

- [ ] Run:

```
uv lock
```

This rewrites `uv.lock` to reflect the dropped dependency.

#### Sub-step 4.12 — Update existing tests

Each of the following test files has assertions or fixtures that
reference the deleted/renamed surface. Walk through and update:

- [ ] `tests/test_auth.py` — replace `resolve_token(host_cfg, host_label=...)` calls with `resolve_github_token()`. Add three cases:
  - `GITHUB_TOKEN` set → returns its value.
  - `GITHUB_TOKEN` unset, `GH_TOKEN` set → returns `GH_TOKEN`.
  - Both unset → raises `MissingTokenError`.

```python
from github_sdlc_mcp.client.auth import MissingTokenError, resolve_github_token
import pytest

def test_resolve_github_token_prefers_github_token():
    assert resolve_github_token({"GITHUB_TOKEN": "gh-pat", "GH_TOKEN": "fallback"}) == "gh-pat"

def test_resolve_github_token_falls_back_to_gh_token():
    assert resolve_github_token({"GH_TOKEN": "fallback-pat"}) == "fallback-pat"

def test_resolve_github_token_raises_when_unset():
    with pytest.raises(MissingTokenError):
        resolve_github_token({})
```

- [ ] `tests/test_github_client.py` — remove tests that exercise the pool. Replace with one test asserting `make_github_client(env={"GITHUB_TOKEN": "x"})` returns a `GitHubClient` with the token wired into the auth header. Reuse the existing `respx`-injected transport pattern.

- [ ] `tests/test_cache.py` — rename `scope="repo"` cases to `scope="org"`. Update kwargs in test setups from `{"repo": "owner/name", ...}` to `{"org": "owner", ...}`. Assert that `clear(scope="org", org="acme")` removes only entries whose stored kwargs contain `org=="acme"`.

- [ ] `tests/test_active_repos.py` — the walker itself is unchanged. The test was likely already exercising `discover_active_repos` directly; that part stays. Drop any test that exercised the `list_active_repos_impl` configured-repo filter (no longer exists). Add one new test that calls `list_active_repos_impl(ctx, org="acme", since=..., until=...)` against a `respx`-mocked `organization.repositories` response (the new `github_active_repos.json` fixture) and asserts the response shape.

- [ ] `tests/test_metrics_cycle_time.py` — update the fixture-driven test. Change `compute_cycle_time_stats(repo=..., since=..., until=..., prs=...)` to `compute_cycle_time_stats(org=..., since=..., until=..., prs=...)`. Add an assertion that `result.repos` is a non-empty list and `sum(slice.count for slice in result.repos) == result.count`.

- [ ] `tests/test_metrics_review_health.py` — same shape update.

- [ ] `tests/test_models.py` — remove tests for `ConfiguredRepo`/`ConfiguredRepoList`. Add round-trip tests for `CycleTimeRepoSlice`, `ReviewHealthRepoSlice`, etc. Keep the existing orphan/typo invariants for `definitions_for` (no key drift). Re-run them — definition string changes should not break key membership.

- [ ] `tests/test_server_tools.py` — update every test calling an `*_impl` function to pass `org=...` instead of `repo=...`. Update `clear_cache_impl` tests to use `scope="all"` or `scope="org", org="acme"`. Drop any test invoking `list_configured_repos_impl`.

#### Sub-step 4.13 — Run lint, types, tests

- [ ] Run all three to verify the tree is green end-to-end:

```
uv run ruff check
uv run mypy
uv run pytest -q
```

Expected: all PASS. If anything fails, fix before committing — do not commit a red tree.

#### Sub-step 4.14 — Commit (via PowerShell — required for 1Password SSH signing)

- [ ] Stage everything and commit:

```powershell
git add -A
git commit -m @'
Phase 9: org-only pivot — drop config layer (v0.2.0)

Every tool now takes a single `org` argument. The active-repo
walker is promoted to the only fetch path; metric responses gain
per-repo slices alongside the org-level rollup. Authentication
collapses to GITHUB_TOKEN (with GH_TOKEN fallback); cloud GitHub
only. The repos.yaml configuration layer and its CLI subcommands
are deleted.

See docs/spec_v3.md for resolved decisions and
docs/superpowers/plans/2026-05-19-org-only-pivot.md for the
step-by-step implementation plan.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

- [ ] Verify the commit landed:

```
git log -1 --stat
```

Expected: ONE commit, version bump in `pyproject.toml`, all of the
files listed in the file structure are in the diff.

---

## Phase 4 — New behavior tests

### Task 5: Per-repo / rollup invariant (parametrized across metrics)

**Files:**
- Create: `tests/test_metrics_per_repo_invariant.py`

- [ ] **Step 1: Write the test**

```python
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
```

- [ ] **Step 2: Run to verify it passes**

```
uv run pytest tests/test_metrics_per_repo_invariant.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_metrics_per_repo_invariant.py
git commit -m @'
tests: per-repo / org rollup invariant for cycle_time and review_health

Asserts that across the implemented metrics, the per-repo slice
counts sum to the org-wide rollup count and the slice repo set
covers exactly the input repos. Parametrised so the remaining
stub metrics can join later.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task 6: Empty-org edge cases

**Files:**
- Create: `tests/test_metrics_empty_org.py`

- [ ] **Step 1: Write the test**

```python
"""Empty org and no-PRs-in-window: success paths, not errors."""

from __future__ import annotations

from datetime import date

from github_sdlc_mcp.metrics.cycle_time import compute_cycle_time_stats


def test_no_prs_yields_zero_count_empty_slices():
    result = compute_cycle_time_stats(
        org="empty-org",
        since=date(2026, 5, 1),
        until=date(2026, 5, 19),
        prs=[],
    )
    assert result.count == 0
    assert result.repos == []
    assert result.median_hours is None
    assert "cycle_time_total" in result.definitions
```

- [ ] **Step 2: Run + commit**

```
uv run pytest tests/test_metrics_empty_org.py -q
```

```powershell
git add tests/test_metrics_empty_org.py
git commit -m @'
tests: empty org returns zero counts, not an error

Confirms compute_cycle_time_stats(prs=[]) returns a well-formed
zero-count PRCycleTimeStats with no per-repo slices, definitions
populated, and no metric values - the contract for "org has no
PRs in window".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task 7: `list_active_repos` tool surface

**Files:**
- Modify: `tests/test_active_repos.py`

- [ ] **Step 1: Add the tool-surface test**

Append to `tests/test_active_repos.py`:

```python
import respx
from httpx import Response

from github_sdlc_mcp.server import build_context, list_active_repos_impl
from tests.fixtures import iter_active_repo_nodes, ACTIVE_REPOS_FIXTURE


@respx.mock
async def test_list_active_repos_tool_against_fixture():
    # Replay the canned organization.repositories fixture via respx.
    import json
    with ACTIVE_REPOS_FIXTURE.open() as f:
        pages = json.load(f)
    respx.post("https://api.github.com/graphql").mock(
        side_effect=[Response(200, json=p) for p in pages]
    )

    ctx = build_context(env={"GITHUB_TOKEN": "test-pat"})
    result = await list_active_repos_impl(
        ctx, org="acme", since=date(2026, 5, 1), until=date(2026, 5, 19)
    )

    assert result.org == "acme"
    active_names = {r.repo for r in result.repos}
    # `ancient` is dropped by the walker's PUSHED_AT-DESC early termination.
    assert "ancient" not in active_names
    assert {"web", "api", "lib"}.issubset(active_names)
```

(If the existing test file already mocks `respx` per-test with a
different pattern, adapt the snippet to fit.)

- [ ] **Step 2: Run + commit**

```
uv run pytest tests/test_active_repos.py -q
```

```powershell
git add tests/test_active_repos.py
git commit -m @'
tests: list_active_repos tool surface against canned fixture

Drives list_active_repos_impl end-to-end against the new paginated
organization.repositories fixture and asserts walker early-termination
keeps out-of-window repos out of the response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

---

## Phase 5 — Polish

### Task 8: Rewrite README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the "Configuration" / "Quick start" sections**

Locate the README's configuration section and replace it with the
authentication + tool-invocation story. Keep CI / installation /
license sections.

```markdown
## Authentication

`github-sdlc-mcp` v0.2.0 reads a single GitHub Personal Access Token
from the environment:

- `GITHUB_TOKEN` (preferred)
- `GH_TOKEN` (fallback, for compatibility with `gh` CLI users)

Token scope: `repo` (full read) and `read:org` for the orgs you
intend to query. Cloud GitHub only (`https://api.github.com`).

```sh
export GITHUB_TOKEN="ghp_..."
uvx github-sdlc-mcp
```

## Tool usage

All metric tools take a single `org` argument plus a time window.
The MCP server walks every repo in `org` with activity in the
window and returns both a per-repo breakdown and an org-level
aggregate.

```jsonc
// Example: cycle time across an org for the last 30 days
{
  "tool": "get_pr_cycle_time_stats",
  "arguments": {
    "org": "valsoftcorp",
    "since": "2026-04-19",
    "until": "2026-05-19"
  }
}
```

`until` defaults to today if omitted.

## Available tools

(unchanged list, with `repo` → `org` substituted)
```

- [ ] **Step 2: Commit**

```powershell
git add README.md
git commit -m @'
docs: rewrite README for v0.2.0 org-only surface

Replaces the configuration-file section with auth-via-GITHUB_TOKEN
and adds an org-based tool-call example.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task 9: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the relevant sections**

In `CLAUDE.md`:

- Under `## Architecture`, replace the per-host language under "client/" and remove the "Active-repo walker" introduction's note about being "the priority deliverable from spec v2 §10" — rewrite to say it is "the load-bearing fetch primitive (every metric tool starts with `walk_active_repos`)".
- Under "Stubs vs implemented", note that response shapes now carry `repos: list[*RepoSlice]`.
- Remove every reference to `config.py`, `repos.yaml`, `--config`, `RepoConfig`, `HostConfig`, `_find_repo_entry`, `ResolvedConfig`, `default_platformdirs_dir`.
- Under "Conventions worth knowing", replace the implication that tool args take `repo: str` with "Every tool takes `org: str` (cloud GitHub only)."
- Update the `Common commands` block: remove the three `uv run github-sdlc-mcp config ...` lines.

- [ ] **Step 2: Commit**

```powershell
git add CLAUDE.md
git commit -m @'
docs: update CLAUDE.md for v0.2.0 org-only architecture

Drops every reference to the deleted config layer. Reframes the
active-repo walker as a load-bearing primitive. Updates the
conventions section to reflect uniform `org`-only tool args.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task 10: Final verification

- [ ] **Step 1: Run the full CI command stack locally**

```
uv sync --frozen
uv run ruff check
uv run mypy
uv run pytest -q --cov
```

Expected: all PASS. If `uv sync --frozen` complains about lock drift,
re-run `uv lock` and commit the result as part of this task.

- [ ] **Step 2: Confirm the server starts**

```
uv run github-sdlc-mcp --help
```

Expected: prints the new help text (no `config` subcommand).

```powershell
$env:GITHUB_TOKEN = "ghp_dummy"; uv run github-sdlc-mcp --version
```

Expected: `github-sdlc-mcp 0.2.0`.

- [ ] **Step 3: If any uv.lock drift was committed, push the tail commit; otherwise this phase needs no commit.**

---

## Self-review checklist

- [ ] Every spec section has at least one task implementing it:
  - §1 Motivation — N/A (no implementation)
  - §2 Resolved decisions — Tasks 1, 4
  - §3 Architecture — Task 4 (sub-steps 4.1–4.7)
  - §4 Tool surface — Task 4.7
  - §5 Data flow — Task 4.7 (`_load_prs_for_org`)
  - §6 Response models — Task 4.1
  - §7 Authentication — Task 4.2
  - §8 ServerContext — Task 4.7
  - §9 Cache — Task 4.4
  - §10 Error handling — Tasks 4.2 (token error), 4.7 (rate-limit path inherited), 6 (empty org)
  - §11 Migration — Task 4 (whole file-by-file list)
  - §12 Testing — Tasks 3, 5, 6, 7
  - §13 CI — Task 10
  - §14 Commit shape — Task 4.14
  - §15 Non-goals — implicit (we don't build them)
  - §16 Open questions — N/A
- [ ] No placeholders: "TBD", "TODO", "later", or "similar to" detected.
- [ ] Symbol consistency: `resolve_github_token` (auth.py + auth test), `make_github_client` (client + server + test), `_load_prs_for_org` (server.py + test_server_tools), `_per_repo_slices` (each metric), `CacheClearResult(scope, org)` (models + cache + server).
- [ ] `org` parameter consistently named — never `organization` or `org_login`.
- [ ] Every commit step uses `git commit -m @'...'@` (PowerShell here-string) per global CLAUDE.md.
