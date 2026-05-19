# Org-only pivot — v0.2.0 design

**Date:** 2026-05-19
**Status:** Draft. Supersedes `docs/spec_v2.md` §"Authentication" and the
configuration-file model implied by §10 where they conflict. The
v0.2.0 implementation that lands from this design will obsolete
`config.py`, `repos.yaml`, and the host-aware token lookup entirely.

## 1. Motivation

`github-sdlc-mcp` is consumed by AI agents, typically through a
higher-level "combining" skill that already knows which GitHub
organisations belong to which company. Maintaining a parallel
`repos.yaml` inside the MCP server duplicates that knowledge and
forces a second source of truth that drifts over time.

This pivot removes the configuration layer entirely. The combining
skill passes an `org` at call time; the server walks every repo in
that org with activity in the requested window and computes the
metric. One PAT, one input axis, uniform tool signatures.

## 2. Resolved decisions

1. **Single entry point: `org: str`.** No `repo` escape hatch. The
   combining skill is responsible for knowing what org to pass.
2. **One org per call.** Skill loops when a company spans multiple
   orgs. Avoids fan-out concerns inside the server.
3. **Per-repo breakdown + org aggregate** in every metric response.
   Agent can drill in without a second round-trip.
4. **`since` required, `until` defaults to "now" inside the impl.**
   Required `since` makes temporal scope explicit; `until=None` in
   the signature keeps cache keys stable across the TTL window.
5. **Single token env var.** `GITHUB_TOKEN`, falling back to
   `GH_TOKEN` to match the `gh` CLI convention.
6. **Cloud-only.** `https://api.github.com` hardcoded. GitHub
   Enterprise Server is out of scope for v0.2.0.
7. **Org-only, not user accounts.** GraphQL `organization(login:)`
   does not return personal repos; the combining skill exclusively
   deals with org-owned work.
8. **Activity signal: `pushed_at`.** Repo-level filter, same as the
   walker already uses.
9. **Walker exposed as `list_active_repos`** — a discoverability
   tool for agents to orient before drilling into metrics.
10. **Hard cut to v0.2.0.** Delete `config.py` and its tests in one
    PR. Nothing is in production; legacy stashing has no payoff.

## 3. Architecture

The layered pipeline from `CLAUDE.md` is preserved end-to-end:

```
GitHub API → client/ → normalize.py → metrics/ → server.py → FastMCP
             (HTTP)    (pure parse)   (pure math)  (orchestration)
```

The only structural change is that `active_repos.py` is promoted
from "quota optimisation over a configured list" to "the primary
fetch path." Same code; load-bearing role.

### Cross-server contract — structure preserved, fields extended

`ProviderResponse` / `AggregateResponse` base classes,
`definitions_for(...)` keys, and the provider-neutral naming
discipline are unchanged. Each metric response gains a new
`repos: list[*RepoSlice]` field and top-level rollup fields; the
extension is applied identically across all metrics and sibling
servers (`gitlab-sdlc-mcp`, `azdo-sdlc-mcp`) will adopt the same
shape when they land. The contract is not narrowed in any way —
existing consumers reading the previous response fields continue
to work.

## 4. Tool surface (13 tools)

All twelve metric tools have an identical, uniform signature:

```python
async def cycle_time(
    *,
    org: str,
    since: datetime,
    until: datetime | None = None,
) -> CycleTimeResponse: ...
```

Plus the newly-exposed walker tool:

```python
async def list_active_repos(
    *,
    org: str,
    since: datetime,
) -> ActiveReposResponse: ...
```

`until=None` is resolved to "now" inside the impl, not in the
signature, so two back-to-back calls produce identical cache keys.

## 5. Data flow per metric tool

```
tool(org, since, until)
  ↓ cache.get(key) → return on hit
  ↓ list_active_repos(org, since)          ← walker, early-terminating
  ↓ asyncio.gather (Semaphore(8)) over repos:
       fetch_prs(repo, since, until)       ← GraphQL query from normalize.py
  ↓ normalize.parse_*(payload) → Iterable[NormalizedPR]
  ↓ metric.compute(prs, by_repo=True) → per-repo slices + org rollup
  ↓ wrap in response model, populate definitions_for(...)
  ↓ cache.set(key, response)
  ↓ return
```

The concurrency cap of 8 is calibrated against GitHub's 5000
point/hour GraphQL budget; the proactive primary-rate-limit pause
already in `client/` is the safety net for larger orgs.

## 6. Response model shape

Concrete example for `cycle_time`; the same pattern applies to
every metric tool.

```python
class CycleTimeRepoSlice(BaseModel):
    repo: str  # "owner/name"
    count: int
    p50_seconds: float | None
    p90_seconds: float | None

class CycleTimeResponse(AggregateResponse):
    org: str
    since: AwareDatetime
    until: AwareDatetime  # resolved "now" if caller passed None
    count: int            # org-wide
    p50_seconds: float | None
    p90_seconds: float | None
    repos: list[CycleTimeRepoSlice]
    # definitions: dict[str, str] inherited from AggregateResponse
```

The `until` field is non-optional in the response — it records
what the server actually used, even when the caller omitted it.

## 7. Authentication

```python
# src/github_sdlc_mcp/client/auth.py
def resolve_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN (or GH_TOKEN) must be set in the environment."
        )
    return token
```

Called once at process start. No per-host indirection, no
`HostConfig` parameter, no env-var name configurability.

## 8. `ServerContext` after the cut

```python
@dataclass
class ServerContext:
    client: GitHubClient
    cache: Cache
```

No `AppConfig`, no `repos: list[RepoConfig]`. Constructed once
inside `build_server`.

## 9. Cache

| Aspect | Behaviour |
|--------|-----------|
| Key shape | `(tool_name, json.dumps({"org": ..., "since": ..., "until": None}, sort_keys=True))` |
| TTL | 15 minutes (unchanged default) |
| `clear_cache(scope=...)` | `scope="org"` filters entries by stored `org` kwarg. Old `scope="repo"` is removed. |
| Protocol | Async `Protocol` in `cache.py` (unchanged). Postgres swap-in remains the v0.2+ plan. |

The `until=None`-in-signature pattern matters: with a resolved
timestamp in the signature, every call carries a different `until`
and the cache never hits.

## 10. Error handling

| Situation | Behaviour |
|-----------|-----------|
| `GITHUB_TOKEN` and `GH_TOKEN` both unset | Startup `RuntimeError` from `resolve_token()` before FastMCP starts listening. |
| Org does not exist / token lacks access | GraphQL error → surface as `ToolError` with the GitHub message. |
| Org has zero repos with `pushed_at >= since` | `repos: []`, zero counts, status 200. Not an error. |
| Repo had pushes in window but zero PRs | Appears in `repos:` with `count: 0` (consistent with "we checked"). |
| Primary rate limit hit mid-fanout | `client/` sleeps proactively until reset; one slow call, not a failure. |
| Secondary rate limit hit | `Retry-After` + jitter, transparent retry. |

## 11. Migration — file-level changes

### Deleted

```
src/github_sdlc_mcp/config.py
src/github_sdlc_mcp/templates/repos.yaml   (and any other config templates)
tests/test_config*.py
```

### Edited (within one PR, in this order)

| File | Change |
|------|--------|
| `models.py` | Drop `RepoConfig`, `HostConfig`. Add `*RepoSlice` model per metric. Add `repos: list[*RepoSlice]` field on each `*Response`. |
| `client/auth.py` | `resolve_token() -> str` (no args). `GITHUB_TOKEN` then `GH_TOKEN`. Clear error if neither set. |
| `__main__.py` | Drop the `config` subcommand tree. Drop platformdirs/config load from startup. |
| `server.py` | `ServerContext` → `{client, cache}`. Every `*_impl` signature: `(ctx, *, org, since, until=None)`. Register `list_active_repos`. |
| `metrics/*.py` | Each `compute` gains `by_repo=True` semantics. Response wrappers populate per-repo slices + org rollup. |
| `metrics/definitions.py` | Definition strings rewritten in org-level language. Orphan/typo tests guide. |
| `pyproject.toml` | `version = "0.2.0"`. Drop `pyyaml` if it was only for config (verify). |
| `README.md` | "Configuration" section → "Authentication". Document `GITHUB_TOKEN`. Update tool-invocation examples. |
| `CLAUDE.md` | Update "stubs vs implemented", "active-repo walker", "Conventions" sections. Drop all config references. |

### Added

```
docs/spec_v3.md                                  ← delta doc for v0.2.0
docs/plans/09-org-only-pivot.md                  ← phase plan (matches existing pattern)
tests/fixtures/github_active_repos.json          ← paginated organization.repositories response
```

### Moved

```
docs/plans/02-config-resolution.md  →  docs/plans/archive/02-config-resolution.md
```

### `docs/spec_v3.md`

Short delta document over `spec_v2.md`, listing the new resolved
decisions (org-only entry, single `GITHUB_TOKEN`, per-repo +
aggregate response, walker as load-bearing path, no escape hatch).
`CLAUDE.md`'s "Authoritative spec" line updated: "v3 supersedes v2
where they conflict."

## 12. Testing strategy

### Fixture posture

The existing `tests/fixtures/github_prs.json` is a single-repo
GraphQL response for `acme/web`. Its calibrated composition
(40 merged, 3 stale open, 5 no-review, 3 fast-approvals, 3 failing
checks, 2 flaky, 2 self-merges, 2 reverts) is preserved as-is —
it remains a valid response for `fetch_prs(repo=acme/web, ...)`.
No regeneration. No assertion re-tuning across eight test files.

### New fixture: `github_active_repos.json`

Paginated `organization.repositories` response for `org=acme`
listing three repos (`acme/web`, `acme/api`, `acme/lib`) with
varying `pushedAt` values, including one that falls outside the
window so walker early-termination is exercised.

### Multi-repo metric tests

A small `tests/conftest.py` helper:

```python
def multiplexed_prs(repos: list[str]) -> list[NormalizedPR]:
    """For each repo in `repos`, yield a copy of the canonical
    per-repo PR fixture with the `repo` field stamped to that
    name. Output is len(repos) × 40 NormalizedPR instances."""
```

Tests then assert "count == 120 across 3 repos" (3×40) and that
per-repo slice counts sum to the rollup.

### Deletions

`tests/test_config*.py` entirely.

### Updates

| Test file | Change |
|-----------|--------|
| `test_metrics_cycle_time.py` | Per-repo slice + rollup consistency assertions. |
| `test_metrics_review_health.py` | Same. |
| `test_server.py` | Tool signatures changed. |
| `test_active_repos.py` | Add `list_active_repos` tool-surface assertion. |
| `test_cache.py` | `clear_cache(scope="org")` semantics. |

### Additions

- **Per-repo / rollup invariant** (parameterised across all eight
  metrics): per-repo slice counts sum to the org-level count;
  weighted aggregates respect the per-repo distribution.
- **Empty org**: zero repos with `pushed_at >= since` →
  `repos: []`, zero counts, no error.
- **Active repos with no PRs in window**: repo appears in `repos:`
  with `count: 0`.
- **`list_active_repos` tool**: response shape + early-termination
  observable through the tool surface.
- **`resolve_token()`**: `GITHUB_TOKEN` wins; `GH_TOKEN` fallback;
  both-unset raises with a clear message.

## 13. CI

`.github/workflows/ci.yml` unchanged: ubuntu × py3.12,
`uv sync --frozen`, `ruff check`, `mypy`, `pytest --cov`. If
`pyyaml` is dropped from `pyproject.toml`, `uv lock` is
regenerated and `uv.lock` is committed in the same PR so
`--frozen` keeps working.

## 14. Commit shape

One PR, title:

```
Phase 9: org-only pivot — drop config, walker as primary fetch path (v0.2.0)
```

Body references this design doc and the new
`docs/plans/09-org-only-pivot.md` phase plan.

## 15. Non-goals

- GitHub Enterprise Server / on-prem instances.
- User-account repos (`https://github.com/<user>/<repo>`).
- Multi-org per call.
- Persistent storage of org → company mappings inside the server.

## 16. Open questions deferred

None for v0.2.0. The v0.2+ items from `spec_v2.md §"Open questions
deferred to v0.2"` (Postgres cache, cross-rebase flake detection,
per-team filtering, multi-default-branch) remain deferred and are
not changed by this pivot.
