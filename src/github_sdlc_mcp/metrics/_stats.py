"""Private statistical helpers shared by metric implementations.

Kept tiny on purpose. The metric functions are easier to read when the math
isn't inlined, and centralizing the percentile algorithm means cross-server
(GitLab, ADO) implementations can vendor this one definition rather than each
inventing their own and producing subtly-different numbers.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from github_sdlc_mcp.models import ExamplePR, NormalizedPR


def percentile(values: Iterable[float], p: float) -> float:
    """Linear-interpolation percentile (NumPy "linear", statistics 'inclusive').

    ``p`` is a fraction in [0, 1]. The single-value case returns the value
    unchanged. Two or more values use ``statistics.quantiles`` with
    ``n=10000`` and select the appropriate index so the result matches the
    classic linear-interpolation formula. Keeps numeric agreement with
    consumers (dashboards, sibling servers) without rolling our own loop.
    """
    seq = list(values)
    if not seq:
        raise ValueError("percentile() needs at least one value")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"percentile fraction must be in [0,1]; got {p}")
    if len(seq) == 1:
        return float(seq[0])
    sorted_v = sorted(seq)
    k = (len(sorted_v) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = k - lo
    return float(sorted_v[lo] + frac * (sorted_v[hi] - sorted_v[lo]))


def median_or_none(values: Iterable[float]) -> float | None:
    seq = list(values)
    return statistics.median(seq) if seq else None


def mean_or_none(values: Iterable[float]) -> float | None:
    seq = list(values)
    return statistics.mean(seq) if seq else None


def round2(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def window_bounds(since: date, until: date) -> tuple[datetime, datetime]:
    """Inclusive day-aligned UTC bounds: [since 00:00, until 23:59:59.999999]."""
    start = datetime.combine(since, time.min, tzinfo=UTC)
    end = datetime.combine(until, time.max, tzinfo=UTC)
    return start, end


def merged_in_window(pr: NormalizedPR, since: date, until: date) -> bool:
    if pr.merged_at is None:
        return False
    start, end = window_bounds(since, until)
    return start <= pr.merged_at <= end


def example_for_pr(
    pr: NormalizedPR, *, label: str, value: float, unit: str
) -> ExamplePR:
    return ExamplePR(
        number=pr.number,
        title=pr.title,
        author=pr.author,
        url=pr.url,
        label=label,
        value=round(value, 2),
        unit=unit,
    )
