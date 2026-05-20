# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

This project uses **uv** for everything. Do not use pip directly.

```bash
uv sync                                  # install dependencies (writes .venv/, uses uv.lock)
uv run github-sdlc-mcp --help            # CLI
uv run github-sdlc-mcp config path       # show resolved config path + search order
uv run github-sdlc-mcp config init       # write template repos.yaml to platformdirs dir
uv run github-sdlc-mcp config validate   # parse + Pydantic-validate the resolved config

uv run pytest -q                         # all tests (~187, ~2s)
uv run pytest tests/test_metrics_cycle_time.py -q              # one file
uv run pytest tests/test_metrics_cycle_time.py::test_fixture_count_matches_merged_in_window
uv run pytest -k "rate_limit"            # match by name

uv run ruff check                        # lint
uv run ruff check --fix                  # autofix where safe
uv run mypy                              # mypy --strict on src/ and tests/

uv run python -m tests.fixtures._generate    # regenerate the synthetic PR/commit fixture
```

CI (`.github/workflows/ci.yml`) runs ubuntu × Python 3.12 only: `uv sync --frozen`, `ruff check`, `mypy`, `pytest --cov`. `ruff format` is not enforced — leave it out of any "make CI green" instinct unless asked.

## Authoritative spec

`docs/spec_v3.md` is authoritative for v0.2.0+. It is a delta over
`docs/spec_v2.md`, which in turn was a delta over `docs/initial_spec.md`.
Where they conflict, v3 wins. v2 is a **delta document** — it records 11 resolved decisions (keyword-only tool args, PAT-only auth, stale-PR signature exception, the 300-second fast-approval threshold, the revert heuristic, etc.) and the v0.1.0 scope table in §10. Treat the scope table as the acceptance contract.

Each phase of the build has a plan doc in `docs/plans/0[1-8]-*.md` explaining design decisions for that layer. Read the relevant plan before changing code in that area.

## Architecture

### The cross-server contract (most important thing to know)

This server is the first of a planned trio (`github-sdlc-mcp`, `gitlab-sdlc-mcp`, `azdo-sdlc-mcp`) that share **identical tool surfaces and response shapes** so an orchestrator can merge results from multiple platforms. That has hard consequences:

- Field names in `src/github_sdlc_mcp/models.py` are deliberately provider-neutral. Never let GitHub vocabulary (`head_sha`, `pull_request_node_id`) leak into a tool response model.
- Every tool response inherits from `ProviderResponse` (stamps `provider: "github"`). Aggregate responses inherit from `AggregateResponse` and also carry a `definitions: dict[str, str]` populated via `definitions_for(*keys)` from `metrics/definitions.py`. Agents quote these strings verbatim — they're user-visible.
- `definitions_for()` raises `KeyError` on unknown keys. Two tests close the loop: every key in `DEFINITIONS` must be referenced by some response (no orphans), and every response must reference only known keys (no typos).

### Layer responsibilities

```
GitHub API  →  client/  →  normalize.py  →  metrics/  →  server.py  →  FastMCP
              (HTTP)      (pure transform)  (pure math)  (orchestration)
```

- **`client/`** is schema-agnostic transport: PAT auth, primary rate-limit proactive pause (sleep until reset when remaining < floor), secondary rate-limit reactive retry (`Retry-After` + jitter), 5xx exponential backoff. `GitHubClient.sleep` is injectable so respx tests assert exact pause durations without real time.
- **`normalize.py`** owns the canonical GraphQL query strings (`PR_PAGE_QUERY`, `COMMIT_PAGE_QUERY`) AND the parser that interprets them. They must change together — that's the worst class of bug in this codebase.
- **`metrics/`** are pure functions over `Iterable[NormalizedPR]`. No I/O. `metrics/_stats.py` centralizes percentile + median + window helpers so sibling servers can vendor one consistent implementation.
- **`server.py`** owns `ServerContext`, the `*_impl` tool functions, and `build_server(ctx)`. Tool impls are top-level async functions taking `ctx` as the first arg — tests call them directly without FastMCP transport ceremony. The FastMCP layer in `build_server` is thin (just decorator registration + `model_dump(mode="json")`).

### Active-repo walker (`active_repos.py`)

This is the priority deliverable from spec v2 §10. The naive approach (query each configured repo for activity) wastes 10-100x API quota on portfolio orgs. The walker:

1. Paginates `organization.repositories` ordered by `PUSHED_AT DESC`.
2. Breaks as soon as a node's `pushed_at < since` (every subsequent node is older).
3. For each surviving repo, paginates merged PRs ordered by `UPDATED_AT DESC` (GitHub's GraphQL doesn't expose `MERGED_AT` as orderBy — `UPDATED_AT` is the closest proxy). Stops when the page's newest `updatedAt < since`.

Early-termination is **load-bearing** — `tests/test_active_repos.py::test_walker_early_terminates_across_pages` will fail if a refactor breaks it.

### Stubs vs implemented

v0.1.0 ships `cycle_time` and `review_health` fully implemented; the other six metrics (`ci_health`, `pr_size`, `stale`, `merge_activity`, `portfolio`, `baseline`) are stubs that raise `NotImplementedError` with a consistent message pointing at `docs/spec_v2.md §10`. The tool surface is real — `build_server` registers all 12 — so an agent sees the API even where the math isn't yet. Don't "fix" the stubs without checking the v0.2 scope.

### Cache

`Cache` is an async `Protocol` (in `cache.py`). The v0.1 implementation is `TTLCache` (in-memory, 15-min default TTL). Async-from-day-one because phase 8 wraps every metric tool with `await cache.get(...)` — when Postgres lands in v0.2, no call site changes. Cache keys are `(tool_name, json.dumps(kwargs, sort_keys=True))` with a custom default that handles `date`/`datetime`/`Path` and **raises on unsupported types** (silent collisions via repr fallback are worse than loud failures). `clear_cache(scope="repo")` filters by the original kwargs stored alongside each entry — that's why entries carry their kwargs dict.

### Synthetic fixture

`tests/fixtures/github_prs.json` and `github_commits.json` are generated by `tests/fixtures/_generate.py` with a fixed seed. **Never hand-edit them** — `test_generator_output_matches_checked_in_files` will fail. If the dataset needs to change, edit the generator and re-run `uv run python -m tests.fixtures._generate`. The fixture composition (40 merged PRs, 3 stale open, 5 no-review, 3 fast-approvals, 3 failing checks, 2 flaky, 2 self-merges, 2 reverts) is calibrated so the metric tests' "expected count ≥ N" assertions pass.

## Conventions worth knowing

- **All datetimes are tz-aware UTC.** Models use `pydantic.AwareDatetime`; naive datetimes fail validation. The normalizer parses GitHub's `Z`-suffixed ISO timestamps explicitly.
- **All tool args are keyword-only.** Both for Python-signature reasons (defaults + required mixed) and for unambiguous MCP invocation.
- **Stdio MCP means stdout is JSON-RPC.** All logs must go to stderr. `_configure_logging()` in `__main__.py` sets this up. Library output to stdout will corrupt the protocol and kill the connection.
- **Deleted GitHub users surface as `null`** in API responses. The normalizer maps these to `"ghost"` (consistent with GitHub's UI).
- **Review/check state strings are lowercased Literals** (`"approved"`, `"completed"`, etc.). The normalizer handles the conversion; unknown states fall back to safe defaults rather than raising.
- **Revert detection is intentionally narrow** (spec v2 §6): `Revert "...` prefix OR `Reverts #N` trailer. Hand-authored reverts are false-negative by design.
- **Fast-approval default is 300 seconds (5 min), not 60.** Configurable via env vars named in `definitions.py` and quoted in the metric's definition string.

## Working with this codebase

- Prefer reading the relevant `docs/plans/0X-*.md` before changing code — they record *why* each layer is shaped the way it is.
- If you're adding a metric, the contract is: pure function over `Iterable[NormalizedPR]` returning a Pydantic response model, with `definitions=definitions_for(...)` populated. Add the definition string to `metrics/definitions.py` first; the orphan/typo tests will guide you.
- When debugging a Windows-specific bug, run the CLI directly — the test suite injects `platformdirs_dir` and won't surface path-formation issues. Example: the platformdirs `appauthor=False` fix in `config.py` was only catchable by running the CLI.
