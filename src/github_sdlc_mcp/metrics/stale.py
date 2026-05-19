"""Stale PR detection. Stubbed in v0.1.0 — implemented in v0.2."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from github_sdlc_mcp.models import NormalizedPR, StalePRList

_STUB_MSG = "compute_stale_prs is stubbed in v0.1.0; see docs/spec_v2.md §10."


def compute_stale_prs(
    *,
    repo: str,
    prs: Iterable[NormalizedPR],
    threshold_days: int = 14,
    as_of: date | None = None,
) -> StalePRList:
    raise NotImplementedError(_STUB_MSG)
