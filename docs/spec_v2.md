# Spec v2: github-sdlc-mcp

This document supersedes `initial_spec.md` where the two conflict. The
original spec remains in the repo as design rationale and historical
record; **v2 is the source of truth for what gets built**.

v2 is a delta document. Anything not addressed here inherits from
`initial_spec.md` unchanged.

## Status

- Author: Claude (Opus 4.7) in collaboration with Rob Prouse.
- Date: 2026-05-18.
- Applies to: v0.1.0 scaffold pass.

## Resolved decisions

### 1. Tool signatures — keyword-only arguments

All tools use keyword-only arguments after `self`/the first positional
arg. This both fixes the Python signature error in the original spec
(required args after defaulted args) and makes MCP tool invocation
unambiguous.

```python
def list_active_repos(
    *,
    since: date,
    until: date,
    host: str | None = None,
    org: str | None = None,
) -> ActiveRepoList: ...

def get_pr_cycle_time_stats(
    *,
    repo: str,
    since: date,
    until: date,
) -> PRCycleTimeStats: ...
```

### 2. `get_stale_prs` signature

Stale is point-in-time, not windowed. Final signature:

```python
def get_stale_prs(
    *,
    repo: str,
    threshold_days: int = 14,
    as_of: date | None = None,  # defaults to today, UTC
) -> StalePRList: ...
```

The `since`/`until` rule in the original spec ("all per-repo metrics
take repo, since, until") is amended: it applies to all **windowed**
metrics. `get_stale_prs` is an explicit exception.

### 3. `provider` field on every response

Every tool response — aggregate or not, including `RepoList`,
`HealthStatus`, and `clear_cache`'s response — includes
`provider: Literal["github"] = "github"`. Uniform is cheaper than
conditional, and an orchestrator merging cross-server results needs it
unconditionally.

### 4. Fast-approval metric — precise definition

Original spec: "approved < 60s with > 200 lines changed".

v2 definition (used verbatim in `metrics/definitions.py`):

> A review is a **fast approval** when (a) the PR has more than
> `FAST_APPROVAL_MIN_LINES_CHANGED` lines changed (additions+deletions,
> default 200), AND (b) the approving review was submitted less than
> `FAST_APPROVAL_MAX_SECONDS` (default 300, i.e. 5 minutes) after the
> **earlier of** (i) the `review_requested` event targeting that
> reviewer, or (ii) the PR's `created_at` if the reviewer was assigned
> at open time. Reviewers who self-requested a review are excluded.

Both thresholds are configurable via env vars:
- `GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES` (default 200)
- `GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS` (default 300 — 5 minutes)

### 5. Flaky-check metric — narrow definition, made explicit

v2 definition string:

> A check is **flaky** for a PR when the same check name has at least
> one failed conclusion and at least one successful conclusion against
> the **same head SHA**. This catches re-runs of the same workflow on
> identical commits. It does NOT catch flakes that surface only across
> rebases (different SHA) — those are indistinguishable from real fixes
> at this layer.

`pct_with_flaky_check` is the percentage of PRs in the window that had
at least one flaky check by this definition.

### 6. Revert detection heuristic

v2 definition string:

> A commit is a **revert** if its message starts with `Revert "` (the
> GitHub default for the "Revert" button) OR contains a trailer line
> matching `^Reverts #\d+`. This will miss hand-authored reverts that
> don't follow either convention.

### 7. CI matrix

`.github/workflows/ci.yml` runs on `ubuntu-latest` with Python 3.12
only. One job runs `uv sync`, `uv run ruff check`, `uv run mypy`, and
`uv run pytest --cov`. Expanding to additional Python versions or OSes
is deferred until a concrete need arises.

### 8. Logging — stderr only

FastMCP's stdio transport uses stdout for JSON-RPC. structlog is
configured at startup to write to `sys.stderr` exclusively, and the
root logger is reconfigured to match. Any third-party library logging
to stdout would break the protocol — the test suite includes a smoke
test that runs the server with `--transport stdio` and asserts no
non-JSON-RPC bytes hit stdout.

### 9. YAML library

`pyyaml` only. `config init` writes a commented template (write-only,
no round-trip needed). `config validate` parses and validates against
a Pydantic model — comments are dropped on parse, which is fine because
the tool only reports, never rewrites.

### 10. Scope of the v0.1.0 scaffold

The scaffold pass produces:

| Component | State |
|---|---|
| `pyproject.toml`, layout, `uv.lock`, `.python-version` | done |
| `config.py` + `config path|init|validate` subcommands | done, fully tested |
| All Pydantic response models in `models.py` | done — contract frozen |
| `client/` (PAT auth, rate_limit, GraphQL+REST, pagination) | done, respx-tested |
| `normalize.py` + tests | done |
| `metrics/cycle_time.py`, `metrics/review_health.py` | fully implemented + fixture-tested |
| `metrics/{ci_health,size,stale,merge_activity,portfolio,baseline}.py` | signatures present, bodies raise `NotImplementedError("v0.1: stubbed")` |
| `server.py`: `list_active_repos`, `list_configured_repos`, `health_check`, `clear_cache`, two implemented metric tools, stubs for the rest | done |
| Fixture: ~40 synthetic PRs over 30 days | hand-crafted JSON, deterministic |
| README, CHANGELOG, CI workflow | done |

Anything labelled stubbed is **not** considered missing for scaffold
acceptance.

### 11. Fixture design for `list_active_repos` early-termination

The repo-list fixture has 7 entries ordered by `PUSHED_AT DESC`:
- entries 1–4: `pushed_at` in window → traversed
- entry 5: `pushed_at` in window (boundary case, last day) → traversed
- entry 6: `pushed_at` before `since` → loop terminates here
- entry 7: `pushed_at` well before `since` → must NOT be fetched

The respx mock is set up to **fail the test** if page 2 (containing
entry 7) is requested, proving early termination. Of the 5 traversed
repos, 3 are in the configured list (intersection step); of those, 2
have merges to default in the window and 1 has only feature-branch
activity.

## Authentication

PAT only. Tokens are referenced from environment variables named by
each host's `token_env` setting in `repos.yaml` (see original spec
§"Config file format"). The GitHub App auth path from the original
spec is **dropped** — for this server, App-based access is achieved by
issuing a PAT (or a GitHub App installation token generated externally)
and exporting it under the configured env var. There is no in-process
JWT signing or installation-token exchange.

`client/auth.py` exposes a single `resolve_token(host_config) -> str`
function that reads the env var and raises a clear error if unset.

## Open questions deferred to v0.2

- Postgres-backed cache.
- Cross-rebase flake detection.
- Per-team / per-author filtering inside metrics (today: per-repo only).
- Multi-default-branch repos (some orgs use `main` + release branches —
  v0.1 treats the API-reported `default_branch` as authoritative).

## Process notes

- Each numbered phase in the build order (see `initial_spec.md` final
  paragraph, refined in the agent's plan response) lands as one
  reviewable commit.
- Phase plans for non-trivial phases get their own doc in `docs/plans/`
  (e.g. `docs/plans/02-config-resolution.md`) before code lands. These
  are short — half a page each — and exist so review-time questions
  have a written answer.
