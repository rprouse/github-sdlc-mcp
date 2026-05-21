# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-18

### Added

- Initial v0.1.0 scaffold. Project layout, `pyproject.toml` (PEP 621
  + PEP 735 dependency groups), `uv.lock`, GitHub Actions CI workflow
  (ubuntu × Python 3.12).
- Config resolution with three-tier precedence (`--config` flag,
  `GITHUB_SDLC_MCP_CONFIG` env, `platformdirs`) and the
  `config path|init|validate` subcommands.
- Pydantic response models for every tool — the cross-server contract
  that planned sibling servers (`gitlab-sdlc-mcp`, `azdo-sdlc-mcp`)
  will mirror. 24 canonical metric-definition strings in
  `metrics/definitions.py`.
- Async GitHub HTTP client with PAT auth, proactive primary-rate-limit
  pausing, reactive secondary-rate-limit and 5xx retry with jitter,
  and cursor-based GraphQL pagination with caller-driven early
  termination.
- GraphQL → `NormalizedPR` / `NormalizedCommit` transform, with a
  deterministic synthetic fixture covering 43 PRs (40 merged, 3 stale
  open) and 30 commits including 2 reverts. A drift test guards the
  fixture against hand edits.
- Two fully-implemented metric computations: `compute_cycle_time_stats`
  and `compute_review_health`. Stubs for the other six metrics
  (`ci_health`, `pr_size`, `stale`, `merge_activity`, `portfolio`,
  `baseline`) raising `NotImplementedError` with consistent pointers
  to `docs/spec_v2.md` §10.
- TTL cache behind an async `Cache` protocol (15-min default,
  scope-based `clear`, swappable backend).
- FastMCP server (`server.py`) registering all 12 tools — fully wired
  for the implemented metrics, stubbed for the rest. CLI runs over
  stdio by default; `--transport streamable-http --port N` available.
- Active-repo discovery via PUSHED_AT-ordered GraphQL with early
  termination — the priority deliverable per spec v2 §10.
- 187 tests passing, ruff and mypy --strict clean.

### Documentation

- `docs/spec_v2.md` records pushback resolutions on top of the
  original spec.
- `docs/plans/0[1-8]-*.md` document the phased build for review.

## [0.2.0] - 2026-05-21

The "org-only pivot." Every tool now takes a single `org: str`
argument; the configuration-file layer is gone; the active-repo
walker is promoted from quota optimization to the primary fetch
primitive. Metric responses gain a per-repo breakdown alongside
the org-level rollup. Cloud GitHub only.

### Changed

- Every tool's input surface collapses to `org: str` (plus a time
  window). No per-repo argument, no configured-repo concept. The
  combining skill above this server is responsible for mapping
  companies to orgs.
- Metric responses carry both an **org-level rollup** (top-level
  fields) and a **per-repo breakdown** (`repos: list[*RepoSlice]`).
  The cross-server contract is structurally preserved; new fields
  are additive.
- Authentication collapses to a single `GITHUB_TOKEN` env var
  (with `GH_TOKEN` accepted as fallback to match the `gh` CLI).
  No per-host `token_env` indirection.
- `clear_cache` scope changes from `repo` to `org`; entries are
  filtered by their stored `org` kwarg.
- Active-repo walker is now the only fetch path. Every metric
  tool starts with `discover_active_repos(org, since, until)` and
  fans the per-repo PR fetches out with `asyncio.Semaphore`.
- Concurrent per-repo PR fetch default raised from 8 to 16.

### Added

- `list_active_repos(org, since, until=None)` exposed as a
  discoverability tool. v0.1 had the walker as internal-only.
- `GH_TOKEN` env var accepted as a fallback to `GITHUB_TOKEN`.
- `GITHUB_SDLC_MCP_MAX_CONCURRENT_REPO_FETCHES` env var (default
  16) tunes the org-wide PR fan-out concurrency per launch.
- Per-metric `*RepoSlice` models in `models.py` —
  `CycleTimeRepoSlice`, `ReviewHealthRepoSlice`, `PRSizeRepoSlice`,
  `CIHealthRepoSlice`, `StaleRepoSlice`, `MergeActivityRepoSlice`
  — carry the per-repo numbers within each metric response.
- Cross-cutting per-repo / org rollup invariant test in
  `tests/test_metrics_per_repo_invariant.py`. Parametrized so new
  metric implementations join the invariant by appending to a list.

### Removed

- `config.py`, `repos.yaml`, the `_templates/` package, and the
  `RepoConfig` / `HostConfig` / `ConfiguredRepo` /
  `ConfiguredRepoList` models. The whole configuration-file layer
  is gone.
- `config path|init|validate` CLI subcommands.
- `list_configured_repos` tool (no longer meaningful without a
  configured set).
- GitHub Enterprise Server support. `https://api.github.com` is
  hardcoded.
- `GITHUB_SDLC_MCP_CONFIG` env var.

### Fixed

- The six stub `*_impl` functions (`ci_health`, `pr_size`, `stale`,
  `merge_activity`, `portfolio_summary`, `compare_to_baseline`)
  previously walked the org and fetched every active repo's PRs
  before letting `compute_*()` raise `NotImplementedError` —
  causing multi-minute timeouts for portfolio orgs. They now raise
  immediately at the impl entry, before any HTTP call. The metric
  module `compute_*()` functions are unchanged.

### Documentation

- `docs/spec_v3.md` — delta over `spec_v2.md` capturing the
  resolved decisions for v0.2.0.
- `docs/plans/09-org-only-pivot.md` — phase plan referencing the
  detailed implementation plan.
- `docs/plans/02-config-resolution.md` moved to
  `docs/plans/archive/` since the layer it documented was deleted.
- `docs/superpowers/specs/2026-05-19-org-only-pivot-design.md` and
  `docs/superpowers/plans/2026-05-19-org-only-pivot.md` capture
  the brainstorm/spec/plan/implementation history.
- README rewritten for the new auth + org-only surface.
- CLAUDE.md rewritten with the v0.2.0 architecture; adds Windows
  venv-shim workaround and CI-gates-vs-IDE-diagnostic notes for
  future sessions.

### Test suite

- 171 tests passing (was 187 — net -16 after dropping config-
  resolution tests, adding per-repo / rollup reconciliation tests
  in every metric file plus the new cross-cutting invariant file).
- `ruff check` and `mypy --strict` remain clean across all
  source and tests.

## [Unreleased]

Nothing yet.
