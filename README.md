# github-sdlc-mcp

A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes
**software-delivery-lifecycle (SDLC) metrics** derived from GitHub —
answer-shaped tools for AI agents, not raw GitHub API passthroughs.

This is the first of a planned trio of sibling servers
(`github-sdlc-mcp`, `gitlab-sdlc-mcp`, `azdo-sdlc-mcp`) that share
identical tool surfaces and response shapes so an orchestrator can
mix repos from multiple platforms without per-provider branching.

> **Status:** v0.2.0. Every tool takes a single `org: str` argument;
> the active-repo walker is the only fetch path. `list_active_repos`,
> `get_pr_cycle_time_stats`, `get_review_health`, `health_check`, and
> `clear_cache` are fully implemented. Six additional metric tools
> are registered but stubbed; they land in v0.3. See
> [`docs/spec_v3.md`](docs/spec_v3.md) for resolved decisions and
> [`docs/superpowers/plans/2026-05-19-org-only-pivot.md`](docs/superpowers/plans/2026-05-19-org-only-pivot.md)
> for the v0.2 implementation plan.

## Why this exists

GitHub has an official MCP server that exposes the API directly. This
server does something different: it **computes metrics**. An agent
asking "how is the AcmeCo team shipping this month?" wants a single
tool call that returns median cycle time, review depth, CI failure
rate, stale-PR count, and a few representative PR URLs — not a raw
PR list it has to do percentile math on.

Every aggregate response embeds the **canonical definition strings**
for each metric, so an agent narrating results to a human quotes the
exact same explanation regardless of which sibling server (GitHub,
GitLab, ADO) produced the number. Responses also carry a per-repo
breakdown alongside the org-level rollup, so agents can drill in
without a second round-trip.

## Install

The published entry point is `uvx`:

```bash
export GITHUB_TOKEN=ghp_...
uvx github-sdlc-mcp                # runs the MCP server over stdio
```

`--transport streamable-http --port 8000` runs over HTTP instead of
stdio.

### As an MCP host plugin

Claude Code:

```bash
claude mcp add github-sdlc -- uvx github-sdlc-mcp
```

Claude Desktop / Cursor / generic MCP host (`mcp.json`):

```json
{
  "mcpServers": {
    "github-sdlc": {
      "command": "uvx",
      "args": ["github-sdlc-mcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_..."
      }
    }
  }
}
```

## Authentication

v0.2.0 reads a single GitHub Personal Access Token from the
environment, with no configuration file:

- `GITHUB_TOKEN` (preferred)
- `GH_TOKEN` (fallback, matches the `gh` CLI convention)

Required scopes: `repo` (full read) and `read:org` for the orgs you
intend to query. Cloud GitHub only — `https://api.github.com` is
hardcoded; GitHub Enterprise Server is out of scope. The PAT is
read once at process start; missing-token errors fail loudly before
the MCP host establishes its connection.

### Other environment variables

| Name | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` / `GH_TOKEN` | — | GitHub PAT |
| `GITHUB_SDLC_MCP_CACHE_TTL_SECONDS` | 900 | TTL for cached tool responses (15 min) |
| `GITHUB_SDLC_MCP_LOG_LEVEL` | INFO | Log level for the structured logger |
| `GITHUB_SDLC_MCP_RATE_LIMIT_FLOOR` | 100 | Pause requests when primary quota dips below |
| `GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES` | 200 | Fast-approval line-count threshold |
| `GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS` | 300 | Fast-approval time threshold (5 min) |
| `GITHUB_SDLC_MCP_MAX_CONCURRENT_REPO_FETCHES` | 16 | Per-org PR fan-out concurrency cap |

## Tool reference

All tools return Pydantic-validated JSON objects with
`provider: "github"` stamped on every response. Aggregate tools
include a `definitions` map of metric-name → human-readable
definition; agents quote these verbatim. Every metric response
carries both an **org-level rollup** and a **per-repo breakdown**
under `repos: list[*RepoSlice]`.

| Tool | Status | Purpose |
|---|---|---|
| `list_active_repos` | ✅ | Repos in the org with `pushed_at` in the window |
| `health_check` | ✅ | Connectivity, rate-limit state, cache size |
| `clear_cache` | ✅ | Drop cached entries — all, or filtered by `org` |
| `get_pr_cycle_time_stats` | ✅ | median/p90/mean cycle hours, first-review, approval-to-merge |
| `get_review_health` | ✅ | reviewer depth, no-review %, fast approvals, self-merges |
| `get_pr_size_stats` | 🚧 v0.3 | size distribution buckets |
| `get_ci_health` | 🚧 v0.3 | failure %, flake detection, top failing checks |
| `get_stale_prs` | 🚧 v0.3 | open PRs idle > threshold |
| `get_merge_activity` | 🚧 v0.3 | weekly merge counts, reverts, frequency |
| `get_portfolio_summary` | 🚧 v0.3 | one-call cross-repo scorecard |
| `compare_to_baseline` | 🚧 v0.3 | current vs baseline window, with significance hint |

### Invoking a tool

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

`since` is required. `until` defaults to today if omitted.

### Example response — `get_pr_cycle_time_stats`

```jsonc
{
  "provider": "github",
  "org": "valsoftcorp",
  "since": "2026-04-15",
  "until": "2026-05-15",
  "count": 120,
  "median_hours": 18.5,
  "p90_hours": 96.2,
  "mean_hours": 31.4,
  "median_time_to_first_review_hours": 4.1,
  "median_approval_to_merge_hours": 2.8,
  "examples": [
    {"number": 142, "label": "slowest", "value": 312.5, "unit": "hours", ...},
    {"number": 156, "label": "fastest", "value": 0.4,   "unit": "hours", ...},
    {"number": 149, "label": "median",  "value": 19.0,  "unit": "hours", ...}
  ],
  "repos": [
    {
      "repo": "web",
      "count": 40,
      "median_hours": 18.5,
      "p90_hours": 96.2,
      "mean_hours": 31.4,
      "median_time_to_first_review_hours": 4.1,
      "median_approval_to_merge_hours": 2.8
    },
    {"repo": "api", "count": 40, "median_hours": 21.0, ...},
    {"repo": "lib", "count": 40, "median_hours": 12.3, ...}
  ],
  "definitions": {
    "cycle_time_total": "Cycle time per PR (hours): merged_at - created_at ...",
    "cycle_time_first_review": "...",
    "cycle_time_approval_to_merge": "..."
  }
}
```

## Sibling servers

The cross-platform contract is the design centre of this project.
Sibling servers are separate PyPI packages and separate processes:

- `gitlab-sdlc-mcp` — same tools, GitLab MRs and CI pipelines.
- `azdo-sdlc-mcp` — same tools, Azure DevOps PRs and builds.

The orchestrator (Claude / your agent harness) routes each `org` to
the right server based on which platform owns it. This server only
sees GitHub orgs; cross-platform portfolio rollups are the
orchestrator's job.

## Troubleshooting

| Problem | Diagnostic |
|---|---|
| Token / 401 errors | Call the `health_check` tool — check `ok` field and `rate_limit_remaining` |
| Empty repos list | Org has zero repos with `pushed_at >= since`. Widen the window. |
| Slow first call | The org-walk and PR fetches are cached for 15 minutes; subsequent calls are fast |
| Stale data | `clear_cache` tool with `scope: "org", org: "..."` to refetch one org |
| Server can't see one repo | Verify your PAT has `read:org` for the org and `repo` for the repo |

## Development

```bash
git clone <this-repo>
cd github-sdlc-mcp
uv sync                       # one-shot install
uv run github-sdlc-mcp --help # see the CLI
uv run pytest                 # ~171 tests
uv run ruff check             # lint
uv run mypy                   # strict type check
```

The test fixture (`tests/fixtures/github_prs.json`) is generated
deterministically; regenerate via
`uv run python -m tests.fixtures._generate`. A drift test guards
against manual edits.

Implementation phases and design decisions are documented in
`docs/spec_v3.md`, `docs/spec_v2.md`, and the `docs/plans/`
directory.

### Pointing Claude Desktop / Claude Code at the dev checkout

Use this MCP client config block to run the server from source instead of an
installed copy:

```json
{
  "mcpServers": {
    "github-sdlc": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "D:\\src\\AI\\github-sdlc-mcp",
        "github-sdlc-mcp"
      ],
      "env": {
        "GITHUB_TOKEN": "ghp_..."
      }
    }
  }
}
```

After editing a source file, restart the MCP client (or use its "reload MCP
servers" action) to pick up the change.

## License

MIT
