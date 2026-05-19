# github-sdlc-mcp

A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes
**software-delivery-lifecycle (SDLC) metrics** derived from GitHub —
answer-shaped tools for AI agents, not raw GitHub API passthroughs.

This is the first of a planned trio of sibling servers
(`github-sdlc-mcp`, `gitlab-sdlc-mcp`, `azdo-sdlc-mcp`) that share
identical tool surfaces and response shapes so an orchestrator can
mix repos from multiple platforms without per-provider branching.

> **Status:** v0.1.0. `list_active_repos`, `get_pr_cycle_time_stats`,
> `get_review_health`, `list_configured_repos`, `health_check`, and
> `clear_cache` are fully implemented. Six additional metric tools are
> registered but stubbed; they land in v0.2. See
> [`docs/spec_v2.md`](docs/spec_v2.md) §10 for the full scope.

## Why this exists

GitHub has an official MCP server that exposes the API directly. This
server does something different: it **computes metrics**. An agent
asking "how is the AcmeCo team shipping this month?" wants a single
tool call that returns median cycle time, review depth, CI failure
rate, stale-PR count, and a few representative PR URLs — not a raw
PR list it has to do percentile math on.

Every aggregate response also embeds the **canonical definition
strings** for each metric, so an agent narrating results to a human
quotes the exact same explanation regardless of which sibling server
(GitHub, GitLab, ADO) produced the number.

## Install

The published entry point is `uvx`:

```bash
uvx github-sdlc-mcp config init    # writes a template repos.yaml
# edit the printed path to list your repos
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

## Configuration

### File location

The server is launched from an MCP host with an unpredictable working
directory, so config is resolved by this precedence (first hit wins):

1. `--config <path>` CLI flag.
2. `GITHUB_SDLC_MCP_CONFIG` environment variable.
3. Platform user config directory:
   - Linux: `~/.config/github-sdlc-mcp/repos.yaml`
   - macOS: `~/Library/Application Support/github-sdlc-mcp/repos.yaml`
   - Windows: `%APPDATA%\github-sdlc-mcp\repos.yaml`

If a CLI flag or env var points at a missing file, that's an error.
Only the platformdirs path falls through silently.

`github-sdlc-mcp config path` prints the resolved location and which
step found it (or all searched paths if no config exists).

### Format

```yaml
# repos.yaml
github_hosts:
  cloud:
    base_url: https://api.github.com
    token_env: GITHUB_TOKEN

  # GitHub Enterprise Server — optional
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

**Tokens never live in this file** — they're referenced by
`token_env`. The MCP host injects them per launch.

### Environment variables

| Name | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | — | Default PAT (referenced by the default `token_env`) |
| `GITHUB_SDLC_MCP_CONFIG` | — | Override config-file path |
| `GITHUB_SDLC_MCP_CACHE_TTL_SECONDS` | 900 | TTL for cached tool responses |
| `GITHUB_SDLC_MCP_LOG_LEVEL` | INFO | Log level for the structured logger |
| `GITHUB_SDLC_MCP_RATE_LIMIT_FLOOR` | 100 | Pause requests when primary quota dips below |
| `GITHUB_SDLC_MCP_FAST_APPROVAL_MIN_LINES` | 200 | Fast-approval line-count threshold |
| `GITHUB_SDLC_MCP_FAST_APPROVAL_MAX_SECONDS` | 300 | Fast-approval time threshold (5 min) |

## Tool reference

All tools return Pydantic-validated JSON objects with
`provider: "github"` stamped on every response. Aggregate tools also
include a `definitions` map of metric-name → human-readable
definition; agents quote these verbatim.

| Tool | Status | Purpose |
|---|---|---|
| `list_configured_repos` | ✅ | Repos this server is configured to monitor |
| `list_active_repos` | ✅ | Configured repos that had push activity in the window |
| `health_check` | ✅ | Connectivity, rate-limit state, cache size, config status |
| `clear_cache` | ✅ | Drop cached entries — all, or filtered by repo |
| `get_pr_cycle_time_stats` | ✅ | median/p90/mean cycle hours, first-review, approval-to-merge |
| `get_review_health` | ✅ | reviewer depth, no-review %, fast approvals, self-merges |
| `get_pr_size_stats` | 🚧 v0.2 | size distribution buckets |
| `get_ci_health` | 🚧 v0.2 | failure %, flake detection, top failing checks |
| `get_stale_prs` | 🚧 v0.2 | open PRs idle > threshold |
| `get_merge_activity` | 🚧 v0.2 | weekly merge counts, reverts, frequency |
| `get_portfolio_summary` | 🚧 v0.2 | one-call cross-repo scorecard |
| `compare_to_baseline` | 🚧 v0.2 | current vs baseline window, with significance hint |

### Example response — `get_pr_cycle_time_stats`

```json
{
  "provider": "github",
  "repo": "acme-corp/web-app",
  "since": "2026-04-15",
  "until": "2026-05-15",
  "count": 40,
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
  "definitions": {
    "cycle_time_total": "Median, p90, and mean number of hours from PR open ...",
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

The orchestrator (Claude / your agent harness) routes each repo to
the right server based on its identifier shape (`owner/repo`,
`group/project`, `org/project/repo`). This server only sees its
configured GitHub repos; cross-platform portfolio rollups are the
orchestrator's job.

## Troubleshooting

| Problem | Diagnostic |
|---|---|
| Server doesn't see my repos | `github-sdlc-mcp config path` — confirm the right file is loaded |
| Token / 401 errors | `github-sdlc-mcp` then call the `health_check` tool — check `ok` field |
| Validation errors on startup | `github-sdlc-mcp config validate` — reports unknown keys + parse errors |
| Slow first call | The org-repo walk is cached for 15 minutes; subsequent calls are fast |
| Stale data | `clear_cache` tool with `scope: "repo"` to refetch one repo |

## Development

```bash
git clone <this-repo>
cd github-sdlc-mcp
uv sync                       # one-shot install
uv run github-sdlc-mcp --help # see the CLI
uv run pytest                 # 187 tests, ~2s
uv run ruff check             # lint
uv run mypy                   # strict type check
```

The test fixture (`tests/fixtures/github_prs.json`) is generated
deterministically; regenerate via
`uv run python -m tests.fixtures._generate`. A drift test guards
against manual edits.

Implementation phases and design decisions are documented in
`docs/spec_v2.md` and the `docs/plans/` directory.

## License

MIT
