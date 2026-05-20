# Phase 9: Org-only pivot (v0.2.0)

Drops the `repos.yaml` configuration layer. Every tool takes a
single `org: str` argument; the active-repo walker becomes the only
fetch path; metric responses gain a per-repo breakdown alongside
the org-level rollup.

See `docs/spec_v3.md` for resolved decisions and
`docs/superpowers/plans/2026-05-19-org-only-pivot.md` for the
step-by-step implementation plan.
