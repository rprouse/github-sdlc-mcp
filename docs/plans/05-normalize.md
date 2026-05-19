# Phase 5 plan: GitHub API → normalized models + the shared fixture

## Goal

Implement the transform from GitHub GraphQL response payloads to the
`Normalized*` Pydantic models from phase 3, and build the synthetic
fixture that phases 5, 6, and 8 will all consume.

## Why one fixture for three phases

Phases 5, 6, and 8 each need data with the same statistical
properties: ~40 PRs spread across a 30-day window with realistic mixes
of cycle time, size, review depth, CI status, plus 3 stale PRs and
2 revert commits. Building this dataset three times is wasted work and
introduces drift between layers. One generator → one JSON file → three
consumers means a single source of truth.

## Files

- `src/github_sdlc_mcp/normalize.py` — canonical GraphQL query
  strings and `normalize_pr()` / `normalize_commit_payload()`.
- `tests/fixtures/_generate.py` — deterministic synthetic-data
  generator (seeded). Re-runnable; emits the JSON files below.
- `tests/fixtures/github_prs.json` — generated, checked-in. List of
  page-shaped GraphQL responses for the PR query.
- `tests/fixtures/github_commits.json` — generated, checked-in.
  Default-branch commit listing for revert detection.
- `tests/fixtures/__init__.py` — loader helpers
  (`load_pr_pages()`, `load_commit_payloads()`).
- `tests/test_normalize.py` — parser unit tests using small inline
  payloads + a fixture round-trip test.

## Normalizer public surface

```python
# normalize.py

# GraphQL query strings (module constants), tied to the normalizer shape.
PR_PAGE_QUERY: str       # one PR-list page; takes $owner $name $cursor
COMMIT_PAGE_QUERY: str   # default-branch commit history page

def normalize_pr(node: dict, *, owner: str, repo: str) -> NormalizedPR: ...
def normalize_commit_payload(node: dict) -> NormalizedCommit: ...

def is_revert_message(message: str) -> bool:
    """Per spec v2 §6: starts with 'Revert "' OR has 'Reverts #N' trailer."""
```

The normalizer is **pure** — no I/O, no exceptions for missing
optionals (returns `None` instead). Network calls live in the client;
metric math lives in `metrics/`. This is the lens that turns one
shape into another, nothing more.

### Field mapping (PR)

| NormalizedPR field | GraphQL source |
|---|---|
| `provider` | literal `"github"` |
| `owner`, `repo` | function args (not in node) |
| `number` | `node.number` |
| `author` | `node.author.login` (or `"ghost"` if null — deleted user) |
| `title`, `url`, `is_draft` | direct |
| `created_at` | `node.createdAt` |
| `merged_at`, `closed_at` | direct, may be null |
| `first_commit_at` | `node.firstCommit.nodes[0].commit.committedDate` (may be null on empty PRs) |
| `additions`, `deletions`, `changed_files` | direct |
| `base_branch` | `node.baseRefName` |
| `head_sha` | `node.lastCommit.nodes[0].commit.oid` (or `""` if absent) |
| `labels` | `[n.name for n in node.labels.nodes]` |
| `merged_by` | `node.mergedBy.login` if present |
| `reviews` | mapped from `node.reviews.nodes` |
| `checks` | flattened from `node.lastCommit.nodes[0].commit.checkSuites.nodes[].checkRuns.nodes` |
| `comments_count` | `node.comments.totalCount` (PR-level) |
| `review_comments_count` | `sum(r.comments.totalCount for r in node.reviews.nodes)` — line-level review comments |
| `requested_reviewer_count` | `node.reviewRequests.totalCount` |

State string lowering: GitHub returns `APPROVED`, `CHANGES_REQUESTED`,
etc. Map to our lowercase `Literal`s. Check `status` is the same.

### Field mapping (commit)

| NormalizedCommit field | GraphQL source |
|---|---|
| `sha` | `node.oid` |
| `author` | `node.author.user.login` if present else `node.author.name` else `"unknown"` |
| `committed_at` | `node.committedDate` |
| `message` | `node.message` |
| `is_revert` | `is_revert_message(message)` |
| `parent_shas` | `[p.oid for p in node.parents.nodes]` |

### Revert heuristic (per spec v2 §6)

```python
_REVERT_PREFIX = "Revert \""
_REVERTS_TRAILER = re.compile(r"^Reverts #\d+", re.MULTILINE)

def is_revert_message(msg: str) -> bool:
    if msg.startswith(_REVERT_PREFIX):
        return True
    return bool(_REVERTS_TRAILER.search(msg))
```

## Fixture design

### Shape

`github_prs.json` is a list of 2 page objects, each shaped like:

```json
{
  "data": {
    "repository": {
      "pullRequests": {
        "nodes": [ <PR node>, ... ],
        "pageInfo": {"hasNextPage": true|false, "endCursor": "..."}
      }
    }
  }
}
```

This is exactly what `paginate_graphql(connection_path=["repository",
"pullRequests"])` walks. Two pages of ~20 PRs each = 40 total.

`github_commits.json` is a list of 1 page in the same connection
shape with ~30 commits on `main`, including 2 reverts.

### Composition

The generator builds:

| Cohort | Count | Properties |
|---|---|---|
| Fast cycle | 5 | merged 2-6h after open, small (<100 lines), 1 reviewer |
| Typical cycle | 15 | merged 6-48h, mixed size, 1-2 reviewers, mostly clean CI |
| Slow cycle | 15 | merged 48h-7d, mixed size, 2-3 reviewers, some CI failures |
| Very slow | 5 | merged 7-15d, mixed |
| **Plus overlays:** | | |
| No-review merges | 5 | distributed across cohorts; merged with zero reviews |
| Fast approvals | 3 | >200 lines, approval submitted <5 min after request |
| Failing checks | 3 | at least one failed check run |
| Flaky checks | 2 | same check name has failure + success against same SHA |
| Self-merges | 2 | merged_by == author |
| Stale open PRs | 3 | OPEN state, age > 14d, no activity in 14d |

Open PRs are mixed in but not in the merged-window analysis. Cohort
totals: 40 merged + 3 open = 43 PR nodes.

Commits: 30 nodes on `main`, 2 with revert-style messages (one
`Revert "..."`, one with `Reverts #N` trailer).

### Determinism

`random.Random(seed=20260518)` drives all randomness. The script
prints a summary on regeneration so reviewers can see what changed.
A test asserts the file is in sync with the generator output to catch
drift if someone hand-edits the JSON.

## Tests (`tests/test_normalize.py`)

1. **Empty/minimal PR node** — author null, no reviews, no checks,
   no labels. Round-trips through `normalize_pr` to a valid
   `NormalizedPR` with sensible defaults.
2. **Author null → "ghost"** — deleted users yield `"ghost"`.
3. **Review state lowering** — `APPROVED` → `"approved"`, etc., for
   every value in our `ReviewState` literal.
4. **Check run flattening** — multi-suite, multi-check payload
   produces a flat `list[NormalizedCheckRun]` with all entries.
5. **head_sha from lastCommit** — present case + missing case
   (empty PR) → empty string fallback.
6. **review_comments_count is summed across reviews**, not pulled
   from the PR-level `comments.totalCount`.
7. **`is_revert_message`** for: standard "Revert \"…\"", "Reverts #42"
   trailer, hand-authored revert message (not detected), unrelated
   message.
8. **Fixture round-trip** — load `github_prs.json`, normalize every
   page, assert: 43 PRs total, 40 merged, 3 open, every PR validates,
   no datetime is naive.
9. **Fixture generator drift** — regenerate to a temp file, byte-
   compare to the checked-in JSON, fail if different. Keeps a future
   editor from accidentally desyncing.

## What's NOT in this phase

- The active-repo query (different shape — `organization.repositories`
  ordered by PUSHED_AT). Lands in phase 6 alongside
  `list_active_repos` implementation.
- Per-metric statistical computation — phase 6.
- Caching of normalized results — phase 8.

## Acceptance

- `tests/test_normalize.py` passes.
- All existing tests still pass.
- Fixture files exist and the drift test passes.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
