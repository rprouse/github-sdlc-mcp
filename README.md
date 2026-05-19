# github-sdlc-mcp

FastMCP server exposing software-delivery-lifecycle (SDLC) metrics
derived from GitHub. First in a planned trio of sibling servers
(`github-sdlc-mcp`, `gitlab-sdlc-mcp`, `azdo-sdlc-mcp`) that share the
same tool surface and response shapes so an agent can use them
interchangeably.

> **Status:** v0.1.0 scaffold. See `docs/spec_v2.md` for the
> authoritative spec.

## Quick start

```bash
uvx github-sdlc-mcp config init
# edit the printed YAML path, then:
export GITHUB_TOKEN=ghp_...
uvx github-sdlc-mcp
```

Full documentation lands in later phases of the scaffold.

## Development

```bash
uv sync
uv run github-sdlc-mcp --help
uv run pytest
uv run ruff check
uv run mypy
```
