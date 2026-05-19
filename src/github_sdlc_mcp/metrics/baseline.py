"""Compare-to-baseline metric. Stubbed in v0.1.0 — implemented in v0.2."""

from __future__ import annotations

from datetime import date

from github_sdlc_mcp.models import BaselineComparison

_STUB_MSG = "compare_to_baseline is stubbed in v0.1.0; see docs/spec_v2.md §10."


def compare_to_baseline(
    *,
    repo: str,
    metric: str,
    as_of: date,
    current_window_days: int = 30,
    baseline_window_days: int = 90,
) -> BaselineComparison:
    raise NotImplementedError(_STUB_MSG)
