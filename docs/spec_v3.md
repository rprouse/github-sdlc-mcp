# Spec v3: github-sdlc-mcp — org-only pivot

## Status

Authoritative as of v0.2.0. Delta document over `spec_v2.md`. Where
v3 and v2 conflict, v3 wins.

## Why

v2 assumed each MCP-server install maintained a `repos.yaml` listing
the repos it tracks. v3 removes that layer: the calling agent (a
"combining skill" that maps companies to GitHub orgs) supplies an
`org` at tool-invocation time and the server walks every active repo
in that org.

## Resolved decisions (delta from v2)

1. **Single entry point: `org: str`.** No `repo` argument anywhere.
2. **One org per call.** Skill loops when a company spans multiple orgs.
3. **Per-repo breakdown + org aggregate** on every metric response.
4. **`since` required, `until` defaults to today** (date-typed, inside the impl).
5. **Single token env var.** `GITHUB_TOKEN`, falling back to `GH_TOKEN`.
6. **Cloud-only.** `https://api.github.com` hardcoded. GHES out of scope.
7. **Org-only (not user accounts).** `organization(login:)` is the GraphQL entry.
8. **Activity signal: `pushed_at`** on the repo node (walker default).
9. **`list_active_repos(org, since)`** exposed as a discovery tool.
10. **Hard cut to v0.2.0.** `config.py`, `repos.yaml`, `*Config` models deleted.

## Implementation plan

See `docs/plans/09-org-only-pivot.md`.
