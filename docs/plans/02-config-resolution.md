# Phase 2 plan: config resolution + CLI subcommands

## Goal

Implement everything in `initial_spec.md` §"Configuration file location"
and §"Config-related subcommands", with full test coverage. After this
phase, `github-sdlc-mcp config path|init|validate` work end-to-end and
the rest of the server can rely on a loaded, validated config object.

## Public surface

```python
# config.py

class GitHubHost(BaseModel):
    base_url: AnyHttpUrl
    token_env: str

class RepoEntry(BaseModel):
    host: str
    owner: str
    repo: str
    company_label: str

class AppConfig(BaseModel):
    github_hosts: dict[str, GitHubHost]
    repos: list[RepoEntry]

    @model_validator(mode="after")
    def _check_repo_hosts_resolve(self) -> "AppConfig": ...
    # every repo.host must be a key in github_hosts

class ResolvedConfig(BaseModel):
    """The outcome of config resolution — config OR a 'not found' record."""
    config: AppConfig | None
    source: Literal["cli", "env", "platformdirs", "not_found"]
    path: Path | None             # the path we loaded, if any
    searched_paths: list[Path]    # ALL paths checked, in order, always populated

def resolve_config(
    *,
    cli_path: Path | None = None,
    env: Mapping[str, str] | None = None,   # defaults to os.environ; injectable for tests
    platformdirs_dir: Path | None = None,   # injectable for tests
) -> ResolvedConfig: ...
```

Resolution order (first hit wins):
1. `cli_path` if provided.
2. `env["GITHUB_SDLC_MCP_CONFIG"]` if set. Absolute or CWD-relative;
   log a warning if relative.
3. `platformdirs_dir / "repos.yaml"` (defaults to
   `platformdirs.user_config_dir("github-sdlc-mcp")`).

If none hit: `ResolvedConfig(config=None, source="not_found",
path=None, searched_paths=[...all three...])`. **No exception.**
`searched_paths` is always populated so callers can show diagnostics.

If the chosen path exists but fails to parse or validate:
`ConfigLoadError` is raised with the path and the underlying error
attached. `config validate` catches this and reports it cleanly;
`config path` reports the resolved path even if loading failed.

## CLI

`__main__.py` grows real argparse handling:

```
github-sdlc-mcp                            # run server (stdio default)
github-sdlc-mcp --transport streamable-http --port 8000
github-sdlc-mcp --config <path>            # override resolution
github-sdlc-mcp config path                # print resolved path + source
github-sdlc-mcp config init [--force]      # write commented template
github-sdlc-mcp config validate            # load + report errors
github-sdlc-mcp --version
```

Server startup itself is still a stub in this phase — it calls
`resolve_config()`, logs the outcome, and exits with the same scaffold
notice from phase 1. The real FastMCP server is wired up in phase 8.

## Tests (`tests/test_config_resolution.py`)

Precedence: with all three sources potentially set, prove the first
wins. Inject env and platformdirs_dir via the function's optional
kwargs — no monkeypatching of `os.environ` or the `platformdirs`
module.

| # | Setup | Expect |
|---|---|---|
| 1 | cli + env + platformdirs all valid | `source == "cli"` |
| 2 | env + platformdirs both valid | `source == "env"` |
| 3 | only platformdirs valid | `source == "platformdirs"` |
| 4 | none exist | `source == "not_found"`, `searched_paths` has 3 entries |
| 5 | env var set to relative path | resolves against CWD, logs warning |
| 6 | env var set but file missing | falls through to platformdirs (?) **see below** |
| 7 | YAML parse error at resolved path | `ConfigLoadError` with line info |
| 8 | repo references unknown host | `ConfigLoadError` from validator |
| 9 | `config init` writes file, refuses to overwrite without --force | |
| 10 | `config init --force` overwrites existing file | |

**Open design question #6:** if `GITHUB_SDLC_MCP_CONFIG` is set but the
file doesn't exist, should that be (a) an error — "you told us where
to look and it wasn't there", or (b) fall through to platformdirs?

I'll go with **(a) error**. Explicit user direction overrides
discovery; falling through silently is the kind of behaviour that
produces "why isn't my config loading?" tickets. `config path` will
still report the searched paths so the user sees what happened.

## CLI flag layering

`--config` and `config <subcommand>` interact:
- `github-sdlc-mcp --config foo.yaml` → run server, override resolution.
- `github-sdlc-mcp --config foo.yaml config path` → print path for that override.
- `github-sdlc-mcp config init --force` → ignores `--config`; writes to platformdirs.
- `github-sdlc-mcp --config foo.yaml config validate` → validates foo.yaml.

`--config` is a top-level flag; subcommands inherit it except `init`,
which always targets platformdirs (it's the bootstrap command).

## Template (`config init` output)

A commented YAML file, ~25 lines, that mirrors the example in
`initial_spec.md` §"Config file format" with extra `# explanation`
comments next to each field. Lives in
`src/github_sdlc_mcp/_templates/repos.yaml.j2` as a plain text file
(no Jinja yet — just a static asset loaded via `importlib.resources`).

## What's NOT in this phase

- Loading repos into the GitHub client. The config object is produced
  but unused outside the CLI subcommands; phase 4+ consumes it.
- Reachability tests in `config validate` ("unreachable hosts" from
  the spec). Doing that requires the HTTP client, which lands in
  phase 4. `config validate` will report only structural errors in
  this phase, with a TODO comment for the network check.

## Acceptance

- All 10 tests above pass.
- `uv run github-sdlc-mcp config init` works in a fresh user dir.
- `uv run github-sdlc-mcp config path` prints the searched paths when
  no config exists.
- `uv run github-sdlc-mcp config validate` reports a structural error
  on a hand-broken YAML and exits non-zero.
- `uv run ruff check && uv run mypy && uv run pytest` all green.
