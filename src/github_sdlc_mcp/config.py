"""Config resolution: CLI flag, env var, then platformdirs user config dir.

The server is typically launched by an MCP host (Claude Code, Claude Desktop,
Cursor, etc.) via ``uvx github-sdlc-mcp``. The working directory at launch is
unpredictable, so config resolution never falls back to ``./repos.yaml``.

Resolution order (first hit wins):

1. The ``cli_path`` argument (from ``--config <path>``).
2. The ``GITHUB_SDLC_MCP_CONFIG`` environment variable.
3. ``platformdirs.user_config_dir("github-sdlc-mcp") / "repos.yaml"``.

If none hit, :class:`ResolvedConfig` is returned with ``source="not_found"`` and
``searched_paths`` populated for diagnostics. If a path resolves but fails to
parse or validate, :class:`ConfigLoadError` is raised.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Literal

import platformdirs
import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

ENV_CONFIG_PATH = "GITHUB_SDLC_MCP_CONFIG"
APP_NAME = "github-sdlc-mcp"
CONFIG_FILENAME = "repos.yaml"

ConfigSource = Literal["cli", "env", "platformdirs", "not_found"]


class ConfigLoadError(Exception):
    """Raised when a config file is found but cannot be parsed or validated."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


class GitHubHost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    token_env: str = Field(min_length=1)


class RepoEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    company_label: str = Field(min_length=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github_hosts: dict[str, GitHubHost] = Field(default_factory=dict)
    repos: list[RepoEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_repo_hosts_resolve(self) -> AppConfig:
        unknown: list[str] = []
        for r in self.repos:
            if r.host not in self.github_hosts:
                unknown.append(f"{r.owner}/{r.repo}: host '{r.host}'")
        if unknown:
            known = ", ".join(sorted(self.github_hosts)) or "(none defined)"
            raise ValueError(
                "repo(s) reference unknown github_hosts entries: "
                + "; ".join(unknown)
                + f". Known hosts: {known}"
            )
        return self


class ResolvedConfig(BaseModel):
    """The outcome of config resolution. ``config`` is ``None`` iff not found."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: AppConfig | None
    source: ConfigSource
    path: Path | None
    searched_paths: list[Path]


def _candidate_paths(
    *,
    cli_path: Path | None,
    env: Mapping[str, str],
    platformdirs_dir: Path,
) -> list[tuple[ConfigSource, Path]]:
    """Build the ordered (source, path) list of where we would look."""
    candidates: list[tuple[ConfigSource, Path]] = []
    if cli_path is not None:
        candidates.append(("cli", cli_path))
    env_val = env.get(ENV_CONFIG_PATH)
    if env_val:
        p = Path(env_val)
        if not p.is_absolute():
            logger.warning(
                "%s is a relative path (%s); resolving against CWD (%s)",
                ENV_CONFIG_PATH,
                env_val,
                Path.cwd(),
            )
            p = (Path.cwd() / p).resolve()
        candidates.append(("env", p))
    candidates.append(("platformdirs", platformdirs_dir / CONFIG_FILENAME))
    return candidates


def default_platformdirs_dir() -> Path:
    """Return the user config directory for this app (platform-specific)."""
    return Path(platformdirs.user_config_dir(APP_NAME))


def resolve_config(
    *,
    cli_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    platformdirs_dir: Path | None = None,
) -> ResolvedConfig:
    """Resolve and load config from the first source that exists.

    ``cli_path`` and ``env[GITHUB_SDLC_MCP_CONFIG]`` are *explicit* — if they
    point at a missing file, that's an error rather than a silent fall-through.
    Only the platformdirs path falls through when missing.
    """
    if env is None:
        env = os.environ
    if platformdirs_dir is None:
        platformdirs_dir = default_platformdirs_dir()

    candidates = _candidate_paths(
        cli_path=cli_path, env=env, platformdirs_dir=platformdirs_dir
    )
    searched = [p for _, p in candidates]

    for source, path in candidates:
        if path.exists():
            return ResolvedConfig(
                config=_load_and_validate(path),
                source=source,
                path=path,
                searched_paths=searched,
            )
        if source in {"cli", "env"}:
            raise ConfigLoadError(
                path,
                f"config path was set via {'--config' if source == 'cli' else ENV_CONFIG_PATH} "
                "but the file does not exist",
            )

    return ResolvedConfig(
        config=None, source="not_found", path=None, searched_paths=searched
    )


def _load_and_validate(path: Path) -> AppConfig:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigLoadError(path, f"could not read file: {e}") from e
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise ConfigLoadError(path, f"YAML parse error: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigLoadError(path, "top-level YAML value must be a mapping")
    try:
        return AppConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigLoadError(path, str(e)) from e


def write_template_config(target_dir: Path, *, force: bool = False) -> Path:
    """Write the commented example ``repos.yaml`` to ``target_dir``.

    Returns the written path. Refuses to overwrite an existing file unless
    ``force=True``.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / CONFIG_FILENAME
    if target.exists() and not force:
        raise FileExistsError(
            f"{target} already exists. Re-run with --force to overwrite."
        )
    template = resources.files("github_sdlc_mcp._templates").joinpath(
        "repos.yaml"
    ).read_text(encoding="utf-8")
    target.write_text(template, encoding="utf-8")
    return target
