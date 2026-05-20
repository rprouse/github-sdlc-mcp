"""Tests for tests/fixtures and tests/conftest helpers."""

from __future__ import annotations

from tests.conftest import multiplexed_prs
from tests.fixtures import iter_active_repo_nodes, iter_pr_nodes


def test_multiplexed_prs_stamps_owner_and_repo():
    targets = [("acme", "web"), ("acme", "api"), ("acme", "lib")]
    prs = multiplexed_prs(targets)
    base_count = sum(1 for _ in iter_pr_nodes())

    assert len(prs) == base_count * len(targets)

    by_repo: dict[str, int] = {}
    for pr in prs:
        assert pr.owner == "acme"
        by_repo[pr.repo] = by_repo.get(pr.repo, 0) + 1

    assert by_repo == {"web": base_count, "api": base_count, "lib": base_count}


def test_iter_active_repo_nodes_yields_four_repos():
    nodes = list(iter_active_repo_nodes())
    names = [n["name"] for n in nodes]
    assert names == ["web", "api", "lib", "ancient"]
