"""Deterministic generator for ``github_prs.json`` and ``github_commits.json``.

Run this script when fixture composition needs to change. Output is checked in
so test runs do not regenerate; a drift test verifies the file matches the
generator output.

    python -m tests.fixtures._generate

The synthetic dataset (per docs/plans/05-normalize.md):

* 40 merged PRs distributed across fast/typical/slow/very-slow cycle cohorts
* 3 stale open PRs (age > 14 days, no recent activity)
* 5 no-review merges, 3 fast approvals, 3 with failing checks, 2 with flaky
  checks, 2 self-merges (overlays onto the merged cohorts)
* 30 commits on main, 2 of which are reverts (one prefix, one trailer style)
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SEED = 20260518
WINDOW_END = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(days=30)
OWNER = "acme"
REPO = "web"
DEFAULT_BRANCH = "main"

AUTHORS = ["alice", "bob", "carol", "dan", "eve", "frank", "grace"]
REVIEWERS = ["pat", "quinn", "robin", "sam", "taylor"]
CHECK_NAMES = ["lint", "unit-tests", "integration-tests", "docker-build", "security-scan"]


# ---------------------------------------------------------------------------
# PR specifications — high-level descriptions that get materialized to GraphQL
# nodes below. Keeping intent and shape separate makes the generator readable.
# ---------------------------------------------------------------------------


@dataclass
class PRSpec:
    number: int
    cohort: str                       # "fast" | "typical" | "slow" | "very_slow" | "stale_open"
    author: str
    created_at: datetime
    merged_at: datetime | None
    additions: int
    deletions: int
    changed_files: int
    reviewers: list[str] = field(default_factory=list)
    review_states: list[str] = field(default_factory=list)
    review_delays_min: list[float] = field(default_factory=list)
    review_comments_per_reviewer: list[int] = field(default_factory=list)
    self_merge: bool = False
    failing_check: bool = False
    flaky_check: bool = False
    is_draft: bool = False
    labels: list[str] = field(default_factory=list)
    # For the "fast approval" overlay
    fast_approval: bool = False

    def to_node(self, rng: random.Random) -> dict[str, Any]:
        head_sha = f"sha-{self.number:04d}"
        # Authors who get reviews assigned at PR open time → first review timing
        # measured from created_at. We don't emit review_requested events in
        # this fixture; phase 6 metrics use created_at as the proxy for
        # "reviewer asked at open time", per spec v2 §4.

        reviews = []
        for i, reviewer in enumerate(self.reviewers):
            delay = self.review_delays_min[i] if i < len(self.review_delays_min) else 60.0
            submitted_at = self.created_at + timedelta(minutes=delay)
            if self.merged_at and submitted_at > self.merged_at:
                submitted_at = self.merged_at - timedelta(minutes=1)
            state = self.review_states[i] if i < len(self.review_states) else "APPROVED"
            comments = (
                self.review_comments_per_reviewer[i]
                if i < len(self.review_comments_per_reviewer)
                else rng.randint(0, 4)
            )
            reviews.append(
                {
                    "author": {"login": reviewer},
                    "state": state,
                    "submittedAt": _iso(submitted_at),
                    "bodyText": "looks good" if state == "APPROVED" else "see comments",
                    "comments": {"totalCount": comments},
                }
            )

        check_runs = _build_check_runs(self, rng, head_sha)

        merged_by = None
        if self.merged_at is not None:
            merged_by = {"login": self.author if self.self_merge else rng.choice(REVIEWERS)}

        first_commit_at = self.created_at - timedelta(hours=rng.uniform(0.5, 8.0))

        return {
            "number": self.number,
            "title": _title_for(self),
            "url": f"https://github.com/{OWNER}/{REPO}/pull/{self.number}",
            "isDraft": self.is_draft,
            "createdAt": _iso(self.created_at),
            "mergedAt": _iso(self.merged_at) if self.merged_at else None,
            "closedAt": _iso(self.merged_at) if self.merged_at else None,
            "additions": self.additions,
            "deletions": self.deletions,
            "changedFiles": self.changed_files,
            "baseRefName": DEFAULT_BRANCH,
            "author": {"login": self.author},
            "mergedBy": merged_by,
            "labels": {"nodes": [{"name": lab} for lab in self.labels]},
            "comments": {"totalCount": rng.randint(0, 3)},
            "reviewRequests": {"totalCount": max(0, len(self.reviewers) - 1)},
            "firstCommit": {
                "nodes": [{"commit": {"committedDate": _iso(first_commit_at)}}]
            },
            "lastCommit": {
                "nodes": [
                    {
                        "commit": {
                            "oid": head_sha,
                            "checkSuites": {
                                "nodes": [{"checkRuns": {"nodes": check_runs}}]
                            },
                        }
                    }
                ]
            },
            "reviews": {"nodes": reviews},
        }


def _build_check_runs(spec: PRSpec, rng: random.Random, head_sha: str) -> list[dict[str, Any]]:
    """Three to five check runs per PR. Optionally injects failures and flakes."""
    selected = rng.sample(CHECK_NAMES, k=rng.randint(3, 5))
    runs: list[dict[str, Any]] = []
    started = (spec.merged_at or spec.created_at) - timedelta(minutes=20)
    completed = started + timedelta(minutes=rng.randint(5, 15))
    for name in selected:
        runs.append(
            {
                "name": name,
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": _iso(started),
                "completedAt": _iso(completed),
            }
        )
    if spec.failing_check:
        # Make one of the runs failed instead of success.
        runs[0]["conclusion"] = "FAILURE"
    if spec.flaky_check:
        # Add two runs of the same name with opposite conclusions on the same
        # head SHA — the classic flake pattern (workflow re-run). The first
        # attempt failed; the rerun passed.
        flaky_name = selected[0] + "-flaky"
        runs.append(
            {
                "name": flaky_name,
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "startedAt": _iso(started),
                "completedAt": _iso(completed),
            }
        )
        runs.append(
            {
                "name": flaky_name,
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "startedAt": _iso(started + timedelta(minutes=20)),
                "completedAt": _iso(completed + timedelta(minutes=20)),
            }
        )
    _ = head_sha  # head_sha is added by the caller in the lastCommit shape
    return runs


def _title_for(spec: PRSpec) -> str:
    if spec.cohort == "stale_open":
        return f"WIP: experimental {spec.author} change #{spec.number}"
    base = {
        "fast": "Hotfix",
        "typical": "Feature",
        "slow": "Refactor",
        "very_slow": "Migration",
    }[spec.cohort]
    return f"{base}: tweak module {spec.number}"


def _iso(dt: datetime) -> str:
    """GitHub format: Z-suffixed UTC ISO-8601 to one-second resolution."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Cohort construction
# ---------------------------------------------------------------------------


def build_specs(rng: random.Random) -> list[PRSpec]:
    specs: list[PRSpec] = []
    number = 100

    # ---- Fast cycle (5): merged 2-6h after open, small, 1 reviewer
    for _ in range(5):
        created = _random_within_window(rng, end_buffer_hours=12)
        merged = created + timedelta(hours=rng.uniform(2, 6))
        specs.append(
            PRSpec(
                number=number,
                cohort="fast",
                author=rng.choice(AUTHORS),
                created_at=created,
                merged_at=merged,
                additions=rng.randint(5, 60),
                deletions=rng.randint(0, 20),
                changed_files=rng.randint(1, 4),
                reviewers=[rng.choice(REVIEWERS)],
                review_states=["APPROVED"],
                review_delays_min=[rng.uniform(30, 120)],
                review_comments_per_reviewer=[rng.randint(0, 2)],
            )
        )
        number += 1

    # ---- Typical cycle (15): merged 6-48h, 1-2 reviewers
    for _ in range(15):
        created = _random_within_window(rng, end_buffer_hours=48)
        merged = created + timedelta(hours=rng.uniform(6, 48))
        reviewer_count = rng.choice([1, 1, 2])
        reviewers_picked = rng.sample(REVIEWERS, k=reviewer_count)
        specs.append(
            PRSpec(
                number=number,
                cohort="typical",
                author=rng.choice(AUTHORS),
                created_at=created,
                merged_at=merged,
                additions=rng.randint(30, 250),
                deletions=rng.randint(5, 80),
                changed_files=rng.randint(2, 10),
                reviewers=reviewers_picked,
                review_states=["APPROVED"] * reviewer_count,
                review_delays_min=[rng.uniform(60, 720) for _ in reviewers_picked],
                review_comments_per_reviewer=[rng.randint(1, 6) for _ in reviewers_picked],
            )
        )
        number += 1

    # ---- Slow cycle (15): merged 48h-7d, mixed size, 2-3 reviewers
    for _ in range(15):
        created = _random_within_window(rng, end_buffer_hours=168)
        merged = created + timedelta(hours=rng.uniform(48, 168))
        reviewer_count = rng.choice([2, 2, 3])
        reviewers_picked = rng.sample(REVIEWERS, k=reviewer_count)
        specs.append(
            PRSpec(
                number=number,
                cohort="slow",
                author=rng.choice(AUTHORS),
                created_at=created,
                merged_at=merged,
                additions=rng.randint(100, 800),
                deletions=rng.randint(20, 300),
                changed_files=rng.randint(5, 25),
                reviewers=reviewers_picked,
                review_states=["APPROVED"] * reviewer_count,
                review_delays_min=[rng.uniform(120, 4320) for _ in reviewers_picked],
                review_comments_per_reviewer=[rng.randint(2, 10) for _ in reviewers_picked],
            )
        )
        number += 1

    # ---- Very slow (5): merged 7-15d, mixed
    for _ in range(5):
        created = _random_within_window(rng, end_buffer_hours=15 * 24)
        merged = created + timedelta(days=rng.uniform(7, 15))
        reviewers_picked = rng.sample(REVIEWERS, k=2)
        specs.append(
            PRSpec(
                number=number,
                cohort="very_slow",
                author=rng.choice(AUTHORS),
                created_at=created,
                merged_at=merged,
                additions=rng.randint(200, 2500),
                deletions=rng.randint(50, 800),
                changed_files=rng.randint(10, 60),
                reviewers=reviewers_picked,
                review_states=["APPROVED", "APPROVED"],
                review_delays_min=[rng.uniform(360, 10080) for _ in reviewers_picked],
                review_comments_per_reviewer=[rng.randint(3, 15) for _ in reviewers_picked],
            )
        )
        number += 1

    # ---- Apply overlays ----
    # 5 no-review merges (clear reviewers + reviewRequests on first five PRs)
    for spec in specs[:5]:
        spec.reviewers = []
        spec.review_states = []
        spec.review_delays_min = []
        spec.review_comments_per_reviewer = []

    # 3 fast approvals (must have >200 lines and reviewer in <5min)
    fast_approval_indices = [10, 11, 12]
    for idx in fast_approval_indices:
        spec = specs[idx]
        spec.fast_approval = True
        spec.additions = max(spec.additions, 220)
        spec.deletions = max(spec.deletions, 30)
        spec.changed_files = max(spec.changed_files, 6)
        # Approval submitted < 5 minutes after PR open
        if not spec.reviewers:
            spec.reviewers = [rng.choice(REVIEWERS)]
            spec.review_states = ["APPROVED"]
            spec.review_comments_per_reviewer = [0]
        spec.review_delays_min = [
            rng.uniform(0.5, 4.0)
            for _ in spec.reviewers
        ]

    # 3 with failing checks (distributed in slow cohort)
    for idx in [25, 26, 27]:
        specs[idx].failing_check = True

    # 2 with flaky checks
    for idx in [28, 29]:
        specs[idx].flaky_check = True

    # 2 self-merges (within the no-review cohort to mean what it sounds like)
    specs[1].self_merge = True
    specs[3].self_merge = True

    # ---- 3 stale open PRs ----
    for _ in range(3):
        created = WINDOW_END - timedelta(days=rng.uniform(20, 45))
        specs.append(
            PRSpec(
                number=number,
                cohort="stale_open",
                author=rng.choice(AUTHORS),
                created_at=created,
                merged_at=None,
                additions=rng.randint(10, 200),
                deletions=rng.randint(0, 50),
                changed_files=rng.randint(1, 8),
                reviewers=[],
                review_states=[],
                is_draft=False,
                labels=["stale-candidate"] if rng.random() < 0.3 else [],
            )
        )
        number += 1

    return specs


def _random_within_window(rng: random.Random, *, end_buffer_hours: float) -> datetime:
    """Pick a ``created_at`` such that merged_at can still fall inside the window."""
    latest_open = WINDOW_END - timedelta(hours=end_buffer_hours)
    span = (latest_open - WINDOW_START).total_seconds()
    offset = rng.uniform(0, max(span, 1))
    return WINDOW_START + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# Commits on main
# ---------------------------------------------------------------------------


def build_commits(rng: random.Random) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    # 30 commits spaced across the window
    seconds_span = int((WINDOW_END - WINDOW_START).total_seconds())
    step = seconds_span // 30
    for i in range(30):
        sha = f"commit-{i:04d}"
        parent_sha = f"commit-{i - 1:04d}" if i > 0 else "commit-root"
        when = WINDOW_START + timedelta(seconds=i * step)
        author = rng.choice(AUTHORS)
        message = f"feat: change number {i}"
        # Two reverts placed deterministically.
        if i == 7:
            message = 'Revert "feat: change number 3"\n\nReason: broke build'
        if i == 22:
            message = f"chore: undo change {i}\n\nReverts #{200 + i}"
        nodes.append(
            {
                "oid": sha,
                "committedDate": _iso(when),
                "message": message,
                "author": {
                    "name": f"{author.title()} User",
                    "user": {"login": author},
                },
                "parents": {"nodes": [{"oid": parent_sha}]},
            }
        )
    return nodes


# ---------------------------------------------------------------------------
# Page packaging
# ---------------------------------------------------------------------------


def package_pr_pages(nodes: list[dict[str, Any]], page_size: int = 22) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for i in range(0, len(nodes), page_size):
        chunk = nodes[i : i + page_size]
        has_next = i + page_size < len(nodes)
        cursor = f"cursor-page-{i // page_size + 1}" if has_next else None
        pages.append(
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "nodes": chunk,
                            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        }
                    }
                }
            }
        )
    return pages


def package_commit_pages(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "data": {
                "repository": {
                    "ref": {
                        "target": {
                            "history": {
                                "nodes": nodes,
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }
        }
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(SEED)
    specs = build_specs(rng)
    pr_nodes = [spec.to_node(rng) for spec in specs]
    rng_commits = random.Random(SEED + 1)
    commit_nodes = build_commits(rng_commits)
    pr_pages = package_pr_pages(pr_nodes)
    commit_pages = package_commit_pages(commit_nodes)
    return pr_pages, commit_pages


def write_fixtures(out_dir: Path) -> None:
    pr_pages, commit_pages = generate()
    (out_dir / "github_prs.json").write_text(
        json.dumps(pr_pages, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    (out_dir / "github_commits.json").write_text(
        json.dumps(commit_pages, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    merged = sum(
        1
        for page in pr_pages
        for n in page["data"]["repository"]["pullRequests"]["nodes"]
        if n["mergedAt"]
    )
    total = sum(
        len(page["data"]["repository"]["pullRequests"]["nodes"])
        for page in pr_pages
    )
    print(f"github_prs.json: {len(pr_pages)} pages, {total} PRs ({merged} merged, {total - merged} open)")
    print(f"github_commits.json: {len(commit_pages)} pages, "
          f"{len(commit_pages[0]['data']['repository']['ref']['target']['history']['nodes'])} commits")


if __name__ == "__main__":
    write_fixtures(Path(__file__).parent)
