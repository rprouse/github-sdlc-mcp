"""Tests for the GitHub-API → NormalizedPR/NormalizedCommit transform."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from github_sdlc_mcp.models import NormalizedCheckRun, NormalizedPR
from github_sdlc_mcp.normalize import (
    is_revert_message,
    normalize_commit_payload,
    normalize_pr,
)
from tests.fixtures import (
    FIXTURE_OWNER,
    FIXTURE_REPO,
    iter_commit_nodes,
    iter_pr_nodes,
)
from tests.fixtures._generate import generate

# ---------------------------------------------------------------------------
# Minimal PR node helpers — used by the unit tests in this file
# ---------------------------------------------------------------------------


def _empty_pr_node(number: int = 1, **overrides: Any) -> dict[str, Any]:
    """A barely-valid PR node — minimal connections everywhere."""
    node: dict[str, Any] = {
        "number": number,
        "title": "Test",
        "url": "https://github.com/o/r/pull/1",
        "isDraft": False,
        "createdAt": "2026-05-01T12:00:00Z",
        "mergedAt": None,
        "closedAt": None,
        "additions": 0,
        "deletions": 0,
        "changedFiles": 0,
        "baseRefName": "main",
        "author": None,           # deleted user
        "mergedBy": None,
        "labels": {"nodes": []},
        "comments": {"totalCount": 0},
        "reviewRequests": {"totalCount": 0},
        "firstCommit": {"nodes": []},
        "lastCommit": {"nodes": []},
        "reviews": {"nodes": []},
    }
    node.update(overrides)
    return node


# ---------------------------------------------------------------------------
# Revert detection (spec v2 §6)
# ---------------------------------------------------------------------------


def test_is_revert_prefix_form() -> None:
    assert is_revert_message('Revert "feat: add login"\n\nReason: broke prod')


def test_is_revert_trailer_form() -> None:
    assert is_revert_message("chore: undo experiment\n\nReverts #42")


def test_is_revert_not_detected_for_handwritten() -> None:
    """Spec v2 §6 is explicit: hand-authored reverts that don't follow the
    GitHub conventions are intentionally not detected."""
    assert not is_revert_message("undo previous commit, it was bad")
    assert not is_revert_message("reverting the auth changes")


def test_is_revert_not_detected_for_unrelated() -> None:
    assert not is_revert_message("feat: add login flow")


# ---------------------------------------------------------------------------
# PR normalizer — minimal / edge cases
# ---------------------------------------------------------------------------


def test_normalize_minimal_pr_node() -> None:
    norm = normalize_pr(_empty_pr_node(), owner="o", repo="r")
    assert isinstance(norm, NormalizedPR)
    assert norm.provider == "github"
    assert norm.author == "ghost"
    assert norm.merged_by is None
    assert norm.first_commit_at is None
    assert norm.head_sha == ""
    assert norm.reviews == []
    assert norm.checks == []
    assert norm.labels == []


def test_normalize_deleted_reviewer_yields_ghost() -> None:
    node = _empty_pr_node(
        reviews={
            "nodes": [
                {
                    "author": None,
                    "state": "APPROVED",
                    "submittedAt": "2026-05-02T08:00:00Z",
                    "bodyText": "lgtm",
                    "comments": {"totalCount": 0},
                }
            ]
        }
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert norm.reviews[0].reviewer == "ghost"


def test_normalize_review_state_lowering() -> None:
    states_in = ["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"]
    states_out = ["approved", "changes_requested", "commented", "dismissed", "pending"]
    node = _empty_pr_node(
        reviews={
            "nodes": [
                {
                    "author": {"login": f"rev{i}"},
                    "state": s,
                    "submittedAt": "2026-05-02T08:00:00Z",
                    "bodyText": "",
                    "comments": {"totalCount": 0},
                }
                for i, s in enumerate(states_in)
            ]
        }
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert [r.state for r in norm.reviews] == states_out


def test_normalize_unknown_review_state_falls_back_to_pending() -> None:
    node = _empty_pr_node(
        reviews={
            "nodes": [
                {
                    "author": {"login": "r"},
                    "state": "WAT",
                    "submittedAt": None,
                    "bodyText": "",
                    "comments": {"totalCount": 0},
                }
            ]
        }
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert norm.reviews[0].state == "pending"


def test_normalize_check_run_flattening() -> None:
    head_sha = "deadbeef"
    node = _empty_pr_node(
        lastCommit={
            "nodes": [
                {
                    "commit": {
                        "oid": head_sha,
                        "checkSuites": {
                            "nodes": [
                                {
                                    "checkRuns": {
                                        "nodes": [
                                            {
                                                "name": "lint",
                                                "status": "COMPLETED",
                                                "conclusion": "SUCCESS",
                                                "startedAt": "2026-05-01T12:05:00Z",
                                                "completedAt": "2026-05-01T12:08:00Z",
                                            },
                                            {
                                                "name": "tests",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "startedAt": "2026-05-01T12:05:00Z",
                                                "completedAt": "2026-05-01T12:10:00Z",
                                            },
                                        ]
                                    }
                                },
                                {
                                    "checkRuns": {
                                        "nodes": [
                                            {
                                                "name": "docker",
                                                "status": "COMPLETED",
                                                "conclusion": "SUCCESS",
                                                "startedAt": "2026-05-01T12:06:00Z",
                                                "completedAt": "2026-05-01T12:09:00Z",
                                            }
                                        ]
                                    }
                                },
                            ]
                        },
                    }
                }
            ]
        }
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert [c.name for c in norm.checks] == ["lint", "tests", "docker"]
    assert all(isinstance(c, NormalizedCheckRun) for c in norm.checks)
    assert all(c.head_sha == head_sha for c in norm.checks)
    assert [c.conclusion for c in norm.checks] == ["success", "failure", "success"]


def test_normalize_head_sha_present() -> None:
    node = _empty_pr_node(
        lastCommit={
            "nodes": [
                {
                    "commit": {
                        "oid": "abcdef",
                        "checkSuites": {"nodes": []},
                    }
                }
            ]
        }
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert norm.head_sha == "abcdef"


def test_review_comments_count_summed_across_reviews() -> None:
    """Spec proxy: review_comments_count = sum across review.comments.totalCount,
    NOT the PR-level comments.totalCount."""
    node = _empty_pr_node(
        comments={"totalCount": 99},  # PR-level — should NOT be picked up
        reviews={
            "nodes": [
                {
                    "author": {"login": "r1"},
                    "state": "APPROVED",
                    "submittedAt": "2026-05-02T08:00:00Z",
                    "bodyText": "",
                    "comments": {"totalCount": 3},
                },
                {
                    "author": {"login": "r2"},
                    "state": "COMMENTED",
                    "submittedAt": "2026-05-02T09:00:00Z",
                    "bodyText": "",
                    "comments": {"totalCount": 5},
                },
            ]
        },
    )
    norm = normalize_pr(node, owner="o", repo="r")
    assert norm.comments_count == 99
    assert norm.review_comments_count == 8  # 3 + 5


def test_normalize_pr_handles_z_suffix_timestamps() -> None:
    norm = normalize_pr(
        _empty_pr_node(createdAt="2026-05-01T12:00:00Z"), owner="o", repo="r"
    )
    assert norm.created_at == datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    assert norm.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Commit normalizer
# ---------------------------------------------------------------------------


def test_normalize_commit_basic() -> None:
    node = {
        "oid": "sha-1",
        "committedDate": "2026-05-01T12:00:00Z",
        "message": "feat: add thing",
        "author": {"name": "Alice User", "user": {"login": "alice"}},
        "parents": {"nodes": [{"oid": "parent-1"}]},
    }
    c = normalize_commit_payload(node)
    assert c.sha == "sha-1"
    assert c.author == "alice"  # prefers user.login over name
    assert c.is_revert is False
    assert c.parent_shas == ["parent-1"]


def test_normalize_commit_revert_detected() -> None:
    node = {
        "oid": "sha-1",
        "committedDate": "2026-05-01T12:00:00Z",
        "message": 'Revert "feat: thing"\n\nReason: broke prod',
        "author": {"name": "Alice User", "user": {"login": "alice"}},
        "parents": {"nodes": [{"oid": "p1"}]},
    }
    c = normalize_commit_payload(node)
    assert c.is_revert is True


def test_normalize_commit_falls_back_to_author_name() -> None:
    node = {
        "oid": "sha-1",
        "committedDate": "2026-05-01T12:00:00Z",
        "message": "x",
        "author": {"name": "External Contributor", "user": None},
        "parents": {"nodes": []},
    }
    c = normalize_commit_payload(node)
    assert c.author == "External Contributor"
    assert c.parent_shas == []


# ---------------------------------------------------------------------------
# Fixture round-trip
# ---------------------------------------------------------------------------


def test_fixture_normalizes_into_43_prs() -> None:
    nodes = iter_pr_nodes()
    assert len(nodes) == 43
    normalized = [
        normalize_pr(n, owner=FIXTURE_OWNER, repo=FIXTURE_REPO) for n in nodes
    ]
    merged = [pr for pr in normalized if pr.merged_at is not None]
    open_prs = [pr for pr in normalized if pr.merged_at is None]
    assert len(merged) == 40
    assert len(open_prs) == 3
    # Every datetime must be tz-aware (model validator enforces, but the
    # fixture is exercising the real path).
    for pr in normalized:
        assert pr.created_at.tzinfo is not None
        if pr.merged_at:
            assert pr.merged_at.tzinfo is not None


def test_fixture_includes_overlay_signals() -> None:
    """The fixture should produce the cohorts/overlays the metric tests rely on."""
    normalized = [
        normalize_pr(n, owner=FIXTURE_OWNER, repo=FIXTURE_REPO)
        for n in iter_pr_nodes()
    ]
    merged = [pr for pr in normalized if pr.merged_at is not None]

    no_review = [pr for pr in merged if not pr.reviews]
    assert len(no_review) >= 5, f"expected ≥5 no-review merges, got {len(no_review)}"

    self_merges = [pr for pr in merged if pr.merged_by == pr.author]
    assert len(self_merges) >= 2

    with_failures = [
        pr for pr in merged
        if any(c.conclusion == "failure" for c in pr.checks)
    ]
    assert len(with_failures) >= 3

    # Flaky = same name, same head_sha, has both failure and success
    flaky_count = 0
    for pr in merged:
        by_name: dict[str, set[str | None]] = {}
        for c in pr.checks:
            by_name.setdefault(c.name, set()).add(c.conclusion)
        if any({"failure", "success"}.issubset(v) for v in by_name.values()):
            flaky_count += 1
    assert flaky_count >= 2, f"expected ≥2 flaky-check PRs, got {flaky_count}"


def test_fixture_commits_include_two_reverts() -> None:
    commits = [normalize_commit_payload(n) for n in iter_commit_nodes()]
    reverts = [c for c in commits if c.is_revert]
    assert len(reverts) == 2
    # One uses the prefix form, one uses the trailer form
    prefix_form = [c for c in reverts if c.message.startswith('Revert "')]
    trailer_form = [c for c in reverts if "Reverts #" in c.message]
    assert len(prefix_form) == 1
    assert len(trailer_form) == 1


# ---------------------------------------------------------------------------
# Generator drift — checked-in JSON must match what the generator produces
# ---------------------------------------------------------------------------


def test_generator_output_matches_checked_in_files() -> None:
    """Catch the case where someone hand-edits the JSON instead of the generator."""
    pr_pages, commit_pages = generate()

    pr_expected = json.dumps(pr_pages, indent=2, sort_keys=False) + "\n"
    commit_expected = json.dumps(commit_pages, indent=2, sort_keys=False) + "\n"

    fixture_dir = Path(__file__).parent / "fixtures"
    pr_actual = (fixture_dir / "github_prs.json").read_text(encoding="utf-8")
    commit_actual = (fixture_dir / "github_commits.json").read_text(encoding="utf-8")

    if pr_actual != pr_expected:
        pytest.fail(
            "github_prs.json is out of sync with the generator. "
            "Re-run: uv run python -m tests.fixtures._generate"
        )
    if commit_actual != commit_expected:
        pytest.fail(
            "github_commits.json is out of sync with the generator. "
            "Re-run: uv run python -m tests.fixtures._generate"
        )
