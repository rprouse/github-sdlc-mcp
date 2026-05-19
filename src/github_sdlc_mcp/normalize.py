"""GitHub GraphQL payloads → ``Normalized*`` Pydantic models.

This is the pure-transform layer. No I/O, no exceptions for missing optionals,
no business logic. The canonical query strings live here too so the response
shape and the parser stay coupled in one file — changing one without the other
is the source of the worst class of bug in this codebase.

Per spec v2 §6, revert detection uses two heuristics: messages that start
with ``Revert "`` (GitHub's default for the Revert button) and messages with
a ``Reverts #N`` trailer. Hand-authored reverts that follow neither convention
are intentionally not detected.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast, get_args

from github_sdlc_mcp.models import (
    CheckConclusion,
    CheckStatus,
    NormalizedCheckRun,
    NormalizedCommit,
    NormalizedPR,
    NormalizedReview,
    ReviewState,
)

# ---------------------------------------------------------------------------
# Canonical GraphQL queries — coupled 1:1 with the parsers below.
# ---------------------------------------------------------------------------

PR_PAGE_QUERY = """
query PullRequestsPage($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 50
      after: $cursor
      orderBy: {field: UPDATED_AT, direction: DESC}
      states: [OPEN, MERGED, CLOSED]
    ) {
      nodes {
        number
        title
        url
        isDraft
        createdAt
        updatedAt
        mergedAt
        closedAt
        additions
        deletions
        changedFiles
        baseRefName
        author { login }
        mergedBy { login }
        labels(first: 20) { nodes { name } }
        comments { totalCount }
        reviewRequests { totalCount }
        firstCommit: commits(first: 1) {
          nodes { commit { committedDate } }
        }
        lastCommit: commits(last: 1) {
          nodes {
            commit {
              oid
              checkSuites(first: 10) {
                nodes {
                  checkRuns(first: 50) {
                    nodes {
                      name
                      status
                      conclusion
                      startedAt
                      completedAt
                    }
                  }
                }
              }
            }
          }
        }
        reviews(first: 50) {
          nodes {
            author { login }
            state
            submittedAt
            bodyText
            comments { totalCount }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

COMMIT_PAGE_QUERY = """
query CommitHistoryPage($owner: String!, $name: String!, $branch: String!,
                        $since: GitTimestamp!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          history(first: 100, since: $since, after: $cursor) {
            nodes {
              oid
              committedDate
              message
              author {
                name
                user { login }
              }
              parents(first: 10) { nodes { oid } }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Revert detection (spec v2 §6)
# ---------------------------------------------------------------------------

_REVERT_PREFIX = 'Revert "'
_REVERTS_TRAILER = re.compile(r"^Reverts #\d+", re.MULTILINE)


def is_revert_message(message: str) -> bool:
    if message.startswith(_REVERT_PREFIX):
        return True
    return bool(_REVERTS_TRAILER.search(message))


# ---------------------------------------------------------------------------
# Public normalizers
# ---------------------------------------------------------------------------


def normalize_pr(node: dict[str, Any], *, owner: str, repo: str) -> NormalizedPR:
    """Map one GraphQL PR node (matching :data:`PR_PAGE_QUERY`) to a NormalizedPR."""
    reviews = [_normalize_review(r) for r in _nodes_of(node.get("reviews"))]
    checks = list(_iter_check_runs(node.get("lastCommit")))
    return NormalizedPR(
        owner=owner,
        repo=repo,
        number=int(node["number"]),
        author=_login_or_ghost(node.get("author")),
        title=str(node.get("title") or ""),
        url=str(node.get("url") or ""),
        created_at=_parse_dt(node["createdAt"]),
        merged_at=_parse_dt_opt(node.get("mergedAt")),
        closed_at=_parse_dt_opt(node.get("closedAt")),
        first_commit_at=_first_commit_at(node.get("firstCommit")),
        additions=int(node.get("additions") or 0),
        deletions=int(node.get("deletions") or 0),
        changed_files=int(node.get("changedFiles") or 0),
        base_branch=str(node.get("baseRefName") or ""),
        head_sha=_head_sha(node.get("lastCommit")),
        is_draft=bool(node.get("isDraft", False)),
        labels=[
            str(n["name"]) for n in _nodes_of(node.get("labels")) if "name" in n
        ],
        merged_by=_login_opt(node.get("mergedBy")),
        reviews=reviews,
        checks=checks,
        comments_count=_total_count(node.get("comments")),
        # Sum line-level review comments across all reviews. This is what the
        # review-health metric actually wants — "how much did reviewers engage
        # with the code?" — not PR-level discussion comments.
        review_comments_count=sum(
            _total_count(r.get("comments")) for r in _nodes_of(node.get("reviews"))
        ),
        requested_reviewer_count=_total_count(node.get("reviewRequests")),
    )


def normalize_commit_payload(node: dict[str, Any]) -> NormalizedCommit:
    """Map one GraphQL commit node to a :class:`NormalizedCommit`."""
    message = str(node.get("message") or "")
    return NormalizedCommit(
        sha=str(node["oid"]),
        author=_commit_author(node.get("author")),
        committed_at=_parse_dt(node["committedDate"]),
        message=message,
        is_revert=is_revert_message(message),
        parent_shas=[
            str(p["oid"]) for p in _nodes_of(node.get("parents")) if "oid" in p
        ],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REVIEW_STATES: frozenset[str] = frozenset(get_args(ReviewState))
_CHECK_STATUSES: frozenset[str] = frozenset(get_args(CheckStatus))
_CHECK_CONCLUSIONS: frozenset[str] = frozenset(get_args(CheckConclusion))


def _normalize_review(node: dict[str, Any]) -> NormalizedReview:
    return NormalizedReview(
        reviewer=_login_or_ghost(node.get("author")),
        state=_review_state(node.get("state")),
        submitted_at=_parse_dt_opt(node.get("submittedAt")),
        comments_count=_total_count(node.get("comments")),
        body_length=len(str(node.get("bodyText") or "")),
    )


def _iter_check_runs(last_commit: Any) -> list[NormalizedCheckRun]:
    out: list[NormalizedCheckRun] = []
    commit = _first_commit(last_commit)
    if commit is None:
        return out
    head_sha = str(commit.get("oid") or "")
    suites = _nodes_of(commit.get("checkSuites"))
    for suite in suites:
        for run in _nodes_of(suite.get("checkRuns")):
            out.append(
                NormalizedCheckRun(
                    name=str(run.get("name") or ""),
                    status=_check_status(run.get("status")),
                    conclusion=_check_conclusion(run.get("conclusion")),
                    started_at=_parse_dt_opt(run.get("startedAt")),
                    completed_at=_parse_dt_opt(run.get("completedAt")),
                    head_sha=head_sha,
                )
            )
    return out


def _first_commit(connection: Any) -> dict[str, Any] | None:
    nodes = _nodes_of(connection)
    if not nodes:
        return None
    commit = nodes[0].get("commit")
    return commit if isinstance(commit, dict) else None


def _first_commit_at(connection: Any) -> datetime | None:
    commit = _first_commit(connection)
    if commit is None:
        return None
    return _parse_dt_opt(commit.get("committedDate"))


def _head_sha(last_commit: Any) -> str:
    commit = _first_commit(last_commit)
    if commit is None:
        return ""
    return str(commit.get("oid") or "")


def _nodes_of(connection: Any) -> list[dict[str, Any]]:
    """Return ``connection.nodes`` as a list, tolerating None at every level."""
    if not isinstance(connection, dict):
        return []
    nodes = connection.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [n for n in nodes if isinstance(n, dict)]


def _total_count(connection: Any) -> int:
    if not isinstance(connection, dict):
        return 0
    raw = connection.get("totalCount", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _login_or_ghost(user: Any) -> str:
    """A deleted GitHub user comes back as ``null`` for author objects."""
    if not isinstance(user, dict):
        return "ghost"
    login = user.get("login")
    return str(login) if login else "ghost"


def _login_opt(user: Any) -> str | None:
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    return str(login) if login else None


def _commit_author(author: Any) -> str:
    if not isinstance(author, dict):
        return "unknown"
    user = author.get("user")
    if isinstance(user, dict) and user.get("login"):
        return str(user["login"])
    if author.get("name"):
        return str(author["name"])
    return "unknown"


def _parse_dt(raw: Any) -> datetime:
    """Parse an ISO-8601 timestamp. GitHub uses ``Z`` for UTC."""
    if isinstance(raw, datetime):
        return raw
    s = str(raw)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _parse_dt_opt(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    return _parse_dt(raw)


def _review_state(raw: Any) -> ReviewState:
    lowered = str(raw or "").lower()
    if lowered in _REVIEW_STATES:
        return cast(ReviewState, lowered)
    # GitHub occasionally returns null for in-flight reviews; treat as pending.
    return "pending"


def _check_status(raw: Any) -> CheckStatus:
    lowered = str(raw or "").lower()
    if lowered in _CHECK_STATUSES:
        return cast(CheckStatus, lowered)
    return "completed"  # treat unknowns as completed; conclusion still carries signal


def _check_conclusion(raw: Any) -> CheckConclusion | None:
    if raw is None:
        return None
    lowered = str(raw).lower()
    if lowered in _CHECK_CONCLUSIONS:
        return cast(CheckConclusion, lowered)
    return None


__all__ = [
    "COMMIT_PAGE_QUERY",
    "PR_PAGE_QUERY",
    "is_revert_message",
    "normalize_commit_payload",
    "normalize_pr",
]
