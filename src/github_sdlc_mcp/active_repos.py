"""Active-repo discovery via the org-level PUSHED_AT-ordered query.

This is the priority deliverable from spec v2 §10. The naive approach (query
each configured repo for activity) wastes 10-100x the API quota on portfolio
orgs with hundreds of repos. Instead we walk the org's repository connection
ordered by PUSHED_AT DESC and stop as soon as a repo's pushed_at falls before
the window's ``since``. For each surviving repo we issue **one** PR-list query
to determine ``has_merges_in_window``.

The walker terminates early in two places:

1. Org-repo pagination stops when ``pushed_at < since``.
2. Per-repo merge check terminates when a PR page's PRs all have
   ``updatedAt < since`` (mergedAt ≤ updatedAt for closed PRs, so future
   pages are guaranteed to be older).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from github_sdlc_mcp.client.github import GitHubClient
from github_sdlc_mcp.metrics._stats import window_bounds

ORG_REPOS_QUERY = """
query OrgRepositoriesPage($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(
      first: 100
      after: $cursor
      orderBy: {field: PUSHED_AT, direction: DESC}
    ) {
      nodes {
        name
        pushedAt
        defaultBranchRef { name }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

REPO_MERGED_PRS_QUERY = """
query RepoMergedPRsPage($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 50
      after: $cursor
      orderBy: {field: UPDATED_AT, direction: DESC}
      states: [MERGED]
    ) {
      nodes {
        mergedAt
        updatedAt
        baseRefName
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


@dataclass(frozen=True)
class DiscoveredRepo:
    owner: str
    repo: str
    pushed_at: datetime
    default_branch: str
    has_merges_in_window: bool


async def discover_active_repos(
    client: GitHubClient,
    *,
    org: str,
    since: date,
    until: date,
) -> tuple[list[DiscoveredRepo], int]:
    """Walk the org's repos PUSHED_AT-descending, returning the active subset.

    Returns ``(active_repos, total_repos_scanned)``. ``scanned`` counts every
    repo node the walker actually fetched — including the one whose
    ``pushed_at`` triggered termination — so callers can report "X of Y repos
    active this month" honestly.
    """
    since_dt, until_dt = window_bounds(since, until)
    active: list[DiscoveredRepo] = []
    scanned = 0

    async for node in client.paginate_graphql(
        ORG_REPOS_QUERY,
        {"org": org},
        connection_path=["organization", "repositories"],
    ):
        scanned += 1
        pushed_at = _parse_dt(node.get("pushedAt"))
        if pushed_at is None:
            continue
        if pushed_at < since_dt:
            # The connection is ordered PUSHED_AT DESC, so every subsequent
            # node is older. Stop without fetching more pages.
            break

        default_branch = _default_branch(node) or "main"
        has_merges = await _has_merges_in_window(
            client,
            owner=org,
            repo=str(node["name"]),
            default_branch=default_branch,
            since_dt=since_dt,
            until_dt=until_dt,
        )
        active.append(
            DiscoveredRepo(
                owner=org,
                repo=str(node["name"]),
                pushed_at=pushed_at,
                default_branch=default_branch,
                has_merges_in_window=has_merges,
            )
        )

    return active, scanned


async def _has_merges_in_window(
    client: GitHubClient,
    *,
    owner: str,
    repo: str,
    default_branch: str,
    since_dt: datetime,
    until_dt: datetime,
) -> bool:
    """True if any PR merged to ``default_branch`` falls in ``[since, until]``."""
    async for node in client.paginate_graphql(
        REPO_MERGED_PRS_QUERY,
        {"owner": owner, "name": repo},
        connection_path=["repository", "pullRequests"],
    ):
        merged_at = _parse_dt(node.get("mergedAt"))
        updated_at = _parse_dt(node.get("updatedAt"))
        base = str(node.get("baseRefName") or "")

        if merged_at is not None and since_dt <= merged_at <= until_dt and base == default_branch:
            return True

        # Termination: when even the page's latest UPDATED_AT is older than
        # `since`, no later page can contain a window-merge. Closed PRs
        # generally don't update further, so mergedAt ≤ updatedAt.
        if updated_at is not None and updated_at < since_dt:
            break

    return False


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _default_branch(node: dict[str, object]) -> str | None:
    ref = node.get("defaultBranchRef")
    if not isinstance(ref, dict):
        return None
    name = ref.get("name")
    return str(name) if isinstance(name, str) and name else None
