# Scaffold: github-sdlc-mcp

Build a new Python project that exposes a FastMCP server for software
delivery lifecycle (SDLC) metrics derived from GitHub. This is the first
of a planned trio of sibling servers — `github-sdlc-mcp`, `gitlab-sdlc-mcp`,
and `azdo-sdlc-mcp` — that will share the same tool surface, response
shapes, and metric definitions so an agent can use them interchangeably.

This server only talks to GitHub. The sibling servers will be separate
PyPI packages and separate processes. The agent (or its orchestrator)
is responsible for calling the right server for each repo.

## Goals

- Give an AI agent **answer-shaped** tools, not raw GitHub API passthroughs.
- Compute consistent, comparable SDLC metrics across many GitHub repos
  and many companies.
- Establish a tool surface and response schema that the GitLab and ADO
  sibling servers will mirror exactly.
- Be runnable today (in-memory cache, direct GitHub calls) and
  warehouse-ready later (swap the data layer without changing tool
  signatures).

## Package and tooling

- Package name (PyPI): `github-sdlc-mcp`. Module: `github_sdlc_mcp`.
- Python 3.11+.
- **Use `uv` for everything.** All instructions in the README, all scripts,
  and CI must use `uv` (not pip directly). Generate `pyproject.toml`
  (PEP 621), no `setup.py`, no `requirements.txt`. Lock with `uv.lock`.
- Runtime dependencies: `fastmcp`, `httpx`, `pydantic`, `pydantic-settings`,
  `python-dateutil`, `tenacity`, `structlog`, `pyyaml`, `platformdirs`.
- Dev dependencies (`[dependency-groups.dev]`): `pytest`, `pytest-asyncio`,
  `respx`, `ruff`, `mypy`, `pytest-cov`.
- Console entry point `github-sdlc-mcp` that runs the FastMCP server over
  stdio by default, with an optional `--transport streamable-http` flag
  and `--port` for HTTP mode.
- Support running via `uvx github-sdlc-mcp` as the primary published
  installation path.

## Project layout

```txt
github-sdlc-mcp/
  pyproject.toml
  uv.lock
  README.md
  CHANGELOG.md
  .python-version              # 3.11
  src/github_sdlc_mcp/
    __init__.py
    __main__.py                # CLI entry, transport selection, subcommands
    server.py                  # FastMCP instance + tool registration
    config.py                  # env + yaml driven settings, path resolution
    models.py                  # Pydantic response models for every tool
    cache.py                   # simple TTL cache, swappable interface
    client/
      __init__.py
      github.py                # GitHub HTTP client (REST + GraphQL)
      rate_limit.py            # tenacity policies, secondary-limit handling
      auth.py                  # PAT and GitHub App auth
    normalize.py               # API payloads → normalized Pydantic models
    metrics/
      __init__.py
      cycle_time.py
      review_health.py
      ci_health.py
      size.py
      stale.py
      merge_activity.py
      portfolio.py
      baseline.py
      definitions.py           # canonical metric definition strings
  tests/
    conftest.py
    fixtures/
      github_prs.json          # 30-day window of ~40 PRs
      github_checks.json
    test_metrics_cycle_time.py
    test_metrics_review_health.py
    test_normalize.py
    test_server_tools.py
    test_rate_limit.py
    test_config_resolution.py
```

## Why mirrored servers (context for design decisions)

The sibling servers are separate processes by design. Practical
implications for this server:

- The tool surface defined below is **shared contract** with the sibling
  servers. Any change here must be portable. Avoid GitHub-specific field
  names in tool signatures or response shapes.
- Response models include a `provider: "github"` field on every aggregate
  response so an orchestrator merging results from multiple servers can
  attribute findings.
- Repo identifiers in tool calls use the platform-native shape
  (`"owner/repo"` for GitHub). The GitLab server will use `"group/project"`
  and ADO will use `"organization/project/repo"`. The agent or orchestrator
  is responsible for routing.

## Normalized internal models

`normalize.py` produces internal Pydantic models that the metrics layer
consumes. These are not exposed as tools — they're the shared shape that
will be reproduced inside each sibling server.

- `NormalizedPR`: provider, owner, repo, number, author, created_at,
  merged_at, closed_at, first_commit_at, additions, deletions,
  changed_files, base_branch, head_sha, is_draft, labels, merged_by,
  reviews: list[NormalizedReview], checks: list[NormalizedCheckRun],
  comments_count, review_comments_count, requested_reviewer_count, url.
- `NormalizedReview`: reviewer, state, submitted_at, comments_count,
  body_length.
- `NormalizedCheckRun`: name, status, conclusion, started_at,
  completed_at, head_sha.
- `NormalizedCommit`: sha, author, committed_at, message, is_revert,
  parent_shas.

## GitHub client implementation notes

- Use GraphQL for the heavy pull (PR + reviews + comments + checks in one
  query per page). Use REST only where GraphQL is awkward (check run
  details, search-commits for revert detection).
- Respect both primary rate limits (`X-RateLimit-Remaining`) and secondary
  (abuse) rate limits. Use `tenacity` with exponential backoff and jitter,
  honor `Retry-After`. Pause proactively when remaining quota drops below
  a configurable threshold (default 100).
- Auth: support both PAT (`GITHUB_TOKEN` env var) and GitHub App (private
  key + app id + installation id). PAT is the minimum required for v0.1.0;
  GitHub App path can be stubbed but must have a clean interface.
- Pagination: handle cursor-based pagination end-to-end internally; never
  return partial pages to the metrics layer.
- Support GitHub Enterprise Server via configurable `base_url` (default
  `https://api.github.com`).
- For active-repo discovery, use GraphQL `organization.repositories`
  ordered by `PUSHED_AT DESC` and early-terminate pagination when the
  page's oldest `pushed_at` falls before the requested `since`. Do not
  iterate all repos. For the `has_merges_in_window` check, query each
  surviving repo's PRs with `states: MERGED` ordered by `mergedAt DESC`
  and early-terminate when `mergedAt` falls before `since` — typically
  one page suffices.

## Tool surface

Every tool returns a Pydantic model serialized to JSON. Every aggregate
tool MUST include:

1. A `provider: "github"` literal field.
2. A `definitions: dict[str, str]` field documenting how each returned
   metric was computed. Use the strings in `metrics/definitions.py` so
   they stay consistent.
3. Up to 3 **representative example PRs** with url, title, author, and
   the metric value that made them representative (e.g. slowest, largest,
   most-commented). Agents need this context to write good narratives.

### Configuration / discovery

- `list_configured_repos() -> RepoList`
  Returns repos this server is configured to monitor. Each entry:
  provider, owner, repo, company_label, github_host (cloud or enterprise URL).
- `list_active_repos(host: str | None = None, org: str | None = None, since: date, until: date) -> ActiveRepoList`
  Returns the subset of configured repos that had any push activity in
  the window. This is the entry point for nearly every other tool — it
  lets the agent answer "which teams shipped this month" cheaply and
  lets metric tools skip repos with no data.

  Filtering:
  - If `host` is provided, restrict to that configured github_host.
  - If `org` is provided, restrict to repos under that owner.
  - If neither is provided, scan all configured repos.

  Implementation: use GraphQL to query each relevant host's
  `organization { repositories(orderBy: {field: PUSHED_AT,
  direction: DESC}) }`, walking pages and early-terminating when
  `pushed_at` falls before `since`. Intersect the result with the
  configured `repos` list (the server should only return repos it is
  configured to monitor, even if other repos in the org also had
  activity).

  Response per repo: provider, owner, repo, company_label, pushed_at,
  default_branch, has_merges_in_window (bool — true if at least one
  PR was merged to the default branch in the window, false if
  `pushed_at` is in window but no merges to default; this distinguishes
  feature-branch work from shipped work). Also include
  total_repos_scanned and total_repos_active at the top level so the
  agent can report "12 of 47 repos active this month".

  Cache TTL: 15 minutes (same default as other tools), keyed by
  (host, org, since, until).

  Note on `pushed_at` caveats: this field updates on any ref push,
  including force-pushes to feature branches, tag pushes, and branch
  deletes. The `has_merges_in_window` boolean is the cleaner signal
  for "did this team actually ship". Document this in the
  `definitions` field of the response.
- `health_check() -> HealthStatus`
  Returns provider connectivity, rate limit remaining, cache status,
  last successful API call timestamp, `config_status` (`"loaded"` or
  `"not_found"`), and the resolved config path (or list of searched paths
  if not found).

### Per-repo metrics

All take `repo: str` (`"owner/repo"`), `since: date`, `until: date`.

- `get_pr_cycle_time_stats(repo, since, until)` → count, median_hours,
  p90_hours, mean_hours, median_time_to_first_review_hours,
  median_approval_to_merge_hours, examples (slowest, fastest, median).
- `get_pr_size_stats(repo, since, until)` → median_lines_changed,
  p90_lines_changed, median_files_changed, distribution_buckets
  (xs <10, s 10-99, m 100-499, l 500-1999, xl 2000+), examples
  (largest, smallest).
- `get_review_health(repo, since, until)` → median_reviewers_per_pr,
  pct_merged_without_review, pct_merged_with_only_author_review,
  median_comments_per_pr, fast_approval_count (approved < 60s with > 200
  lines changed), self_merge_count, examples (no_review, fast_approval,
  deep_review).
- `get_ci_health(repo, since, until)` → pr_count, pct_with_failing_check,
  pct_with_flaky_check (same check failing then passing on same SHA),
  top_failing_checks: list[{name, count}], median_failed_runs_per_pr,
  examples (most_failures, flake_suspect).
- `get_stale_prs(repo, threshold_days=14)` → count, list of
  {number, title, author, age_days, last_activity, url}.
- `get_merge_activity(repo, since, until)` → merges_to_default_branch
  (weekly buckets), revert_commit_count, merge_frequency_per_week.

### Portfolio / cross-repo (within this server's configured GitHub repos)

- `get_portfolio_summary(since, until, company_filter: str | None = None,
  include_inactive: bool = False)`
  → one call returning the scorecard across configured GitHub repos.
  Internally calls `list_active_repos` first and computes metrics only
  for repos with activity in the window, unless `include_inactive=True`
  (in which case inactive repos appear with null metric values and an
  `inactive_in_window: true` flag).
  Per repo: cycle_time_median, review_no_review_pct, ci_failure_pct,
  stale_count, merge_frequency, has_merges_in_window. Plus
  portfolio-level percentile ranks per metric so the agent can say
  "Company X is in the bottom quartile for review depth this month".
  Top-level: total_repos_configured, total_repos_active,
  total_repos_with_merges.
  **Note**: this server only sees GitHub repos. Cross-platform portfolio
  rollups are the orchestrator's job.
- `compare_to_baseline(repo, metric, current_window_days=30,
  baseline_window_days=90)` → current value, baseline value, pct_change,
  direction (better/worse/flat), significance hint (above noise floor
  or not).

### Cache management

- `clear_cache(scope: Literal["all", "repo"] = "all", repo: str | None = None)`
  → number of entries cleared.

## Configuration file location

The server is typically launched by an MCP host (Claude Code, Claude
Desktop, Cursor, etc.) via `uvx github-sdlc-mcp`. The working directory
at launch is unpredictable, so the server MUST NOT rely on a relative
path like `./repos.yaml`. Resolve the config path in this order, first
hit wins:

1. `--config <path>` CLI flag.
2. `GITHUB_SDLC_MCP_CONFIG` env var (absolute or relative path; if
   relative, resolved against CWD with a logged warning).
3. Platform user config directory via `platformdirs`:
   `platformdirs.user_config_dir("github-sdlc-mcp") / "repos.yaml"`.
   This resolves to:
   - Linux: `$XDG_CONFIG_HOME/github-sdlc-mcp/repos.yaml`, falling back
     to `~/.config/github-sdlc-mcp/repos.yaml`.
   - macOS: `~/Library/Application Support/github-sdlc-mcp/repos.yaml`.
   - Windows: `%APPDATA%\github-sdlc-mcp\repos.yaml`.
4. If none found, log the searched paths at INFO and start with zero
   configured repos. `list_configured_repos` returns an empty list and
   `health_check` reports `config_status: "not_found"` with the
   searched paths. Do not crash — the server should still be inspectable
   so the user can see what went wrong via the MCP host.

### Config-related subcommands

Provide these in addition to the default server-running mode:

- `github-sdlc-mcp config path` — prints the resolved config path and
  which resolution step found it (or all searched paths if not found).
  Makes setup debuggable without reading source.
- `github-sdlc-mcp config init` — writes a commented example
  `repos.yaml` to the platformdirs user config directory, creating
  the directory if needed. Refuses to overwrite an existing file
  unless `--force` is passed.
- `github-sdlc-mcp config validate` — loads and validates the resolved
  config, reporting parse errors, unknown fields, and unreachable hosts.

### Config file format

```yaml
# repos.yaml
github_hosts:
  cloud:
    base_url: https://api.github.com
    token_env: GITHUB_TOKEN
  acme_enterprise:
    base_url: https://github.acme.internal/api/v3
    token_env: GITHUB_TOKEN_ACME

repos:
  - host: cloud
    owner: acme-corp
    repo: web-app
    company_label: AcmeCo
  - host: cloud
    owner: acme-corp
    repo: api
    company_label: AcmeCo
  - host: acme_enterprise
    owner: internal-tools
    repo: deploy-bot
    company_label: AcmeCo
```

Tokens live in env vars referenced by `token_env`, never in the YAML.
This keeps secrets out of the user config directory and lets the MCP
host inject them per-launch.

### Other env vars

- `GITHUB_TOKEN` — default PAT, referenced by `token_env: GITHUB_TOKEN`.
- `GITHUB_SDLC_MCP_CONFIG` — override config file path.
- `GITHUB_SDLC_MCP_CACHE_TTL_SECONDS` — default 900.
- `GITHUB_SDLC_MCP_LOG_LEVEL` — default INFO.
- `GITHUB_SDLC_MCP_RATE_LIMIT_FLOOR` — pause when remaining drops below
  this (default 100).

## Caching

In-memory TTL cache keyed by `(tool_name, sorted_kwargs)`. Default TTL
15 minutes. The cache implementation must live behind an interface
(`Cache` protocol) so it can later be swapped for Postgres-backed
materialized views without changing tool code.

## Testing

- Mock GitHub with `respx`. Fixture JSON for one repo across a 30-day
  window: ~40 PRs mixed in cycle time, size, review depth, CI status;
  3 stale PRs; 2 revert commits.
- Test each metric function against the fixture with known expected
  values. Snapshot tests for the tool output JSON shape so the
  cross-server contract is enforced.
- Test rate-limit retry behavior with `respx` returning 403 + `Retry-After`.
- Test pagination by returning multi-page responses from the mock.
- Test config path resolution: monkeypatch platformdirs, `GITHUB_SDLC_MCP_CONFIG`,
  and the CLI flag; assert correct precedence and "not found" behavior.
- Run via `uv run pytest`. Add `uv run pytest --cov` to CI.
- Test `list_active_repos` against a fixture with: 5 configured repos
  where 3 had pushes in window (2 with merges to default, 1 with only
  feature-branch activity) and 2 had no activity. Assert correct
  filtering, correct `has_merges_in_window` values, and correct totals.
- Test that `get_portfolio_summary` skips inactive repos by default and
  includes them with nulls when `include_inactive=True`.

## What NOT to do

- Do not implement generic `search_prs` or `get_repo` tools. Agents
  have the official GitHub MCP for that. Every tool here computes a
  metric.
- Do not return raw GitHub API payloads. Always go through normalized
  models.
- Do not put GitHub-specific field names in tool signatures or response
  shapes — those are the cross-server contract.
- Do not couple cache keys or response models to API URL structure.
- Do not assume the agent will do date math, percentile calculations,
  or pagination. The server does all of that.
- Do not default to `./repos.yaml` or any other CWD-relative path. CWD
  is unpredictable under an MCP host launch.
- Do not store tokens in the YAML config — env vars only.
- Do not query every configured repo's PRs/commits to determine
  activity. Use the org-level GraphQL repo list with PUSHED_AT ordering
  and early-terminate. On portfolio companies with hundreds of repos,
  the naive approach wastes 10-100x the API quota.

## Deliverables for this scaffold pass

1. Full project skeleton above, runnable end-to-end against GitHub with
   a PAT. `uv sync && uv run github-sdlc-mcp` must start the server.
2. `list_active_repos`, `get_pr_cycle_time_stats`, and
   `get_review_health` fully implemented with passing tests against
   fixture data. `list_active_repos` is the priority — every other
   metric tool depends on it for efficient operation.
3. Other tools stubbed with correct signatures, Pydantic models, and
   `NotImplementedError` bodies — so the tool surface is real even where
   the math isn't yet.
4. Config resolution fully implemented with all three subcommands
   (`path`, `init`, `validate`) and tests covering precedence.
5. `README.md` covering:
   - Install via `uvx github-sdlc-mcp` and via `uv sync` for development.
   - First-run setup: `uvx github-sdlc-mcp config init`, edit the YAML,****
     then add to your MCP host.
   - Config file format, location per OS, and env vars.
   - Running under Claude Code:
```
     claude mcp add github-sdlc -- uvx github-sdlc-mcp
```
     plus a JSON example showing the env block for `GITHUB_TOKEN`.
   - Each tool with example invocation and example response.
   - A "Sibling servers" section noting the planned `gitlab-sdlc-mcp`
     and `azdo-sdlc-mcp` and the shared contract.
   - Troubleshooting: `github-sdlc-mcp config path` to debug config
     discovery, `health_check` tool to verify connectivity.
6. `CHANGELOG.md` seeded with 0.1.0.
7. GitHub Actions workflow at `.github/workflows/ci.yml` running
   `uv sync`, `uv run ruff check`, `uv run mypy`, and `uv run pytest`
   on push and PR.

Start by laying out the file tree and `pyproject.toml`, then config
resolution and subcommands (this is foundational — every other piece
depends on it), then the GitHub client, then `normalize.py`, then the
two fully-implemented metrics, then stub the rest. Get tests green
before considering this scaffold complete.
