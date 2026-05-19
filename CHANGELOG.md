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

## [Unreleased]

Nothing yet.
