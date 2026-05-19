"""Tests for config resolution, validation, and the ``config`` subcommands."""

from __future__ import annotations

from pathlib import Path

import pytest

from github_sdlc_mcp.__main__ import main
from github_sdlc_mcp.config import (
    ENV_CONFIG_PATH,
    AppConfig,
    ConfigLoadError,
    resolve_config,
    write_template_config,
)

VALID_YAML = """\
github_hosts:
  cloud:
    base_url: https://api.github.com
    token_env: GITHUB_TOKEN
repos:
  - host: cloud
    owner: acme
    repo: web
    company_label: Acme
"""

UNKNOWN_HOST_YAML = """\
github_hosts:
  cloud:
    base_url: https://api.github.com
    token_env: GITHUB_TOKEN
repos:
  - host: missing
    owner: acme
    repo: web
    company_label: Acme
"""

BROKEN_YAML = "github_hosts:\n  cloud: { base_url: 'oops'  # missing closing brace\n"


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    d = tmp_path / "platformdirs"
    d.mkdir()
    return d


def _write_yaml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_precedence_cli_beats_env_and_platformdirs(tmp_path: Path) -> None:
    cli_file = _write_yaml(tmp_path / "cli.yaml", VALID_YAML)
    env_file = _write_yaml(tmp_path / "env.yaml", VALID_YAML)
    pd_dir = tmp_path / "pd"
    _write_yaml(pd_dir / "repos.yaml", VALID_YAML)

    res = resolve_config(
        cli_path=cli_file,
        env={ENV_CONFIG_PATH: str(env_file)},
        platformdirs_dir=pd_dir,
    )
    assert res.source == "cli"
    assert res.path == cli_file
    assert len(res.searched_paths) == 3


def test_precedence_env_beats_platformdirs(tmp_path: Path) -> None:
    env_file = _write_yaml(tmp_path / "env.yaml", VALID_YAML)
    pd_dir = tmp_path / "pd"
    _write_yaml(pd_dir / "repos.yaml", VALID_YAML)

    res = resolve_config(
        env={ENV_CONFIG_PATH: str(env_file)},
        platformdirs_dir=pd_dir,
    )
    assert res.source == "env"
    assert res.path == env_file


def test_falls_back_to_platformdirs(tmp_path: Path) -> None:
    pd_dir = tmp_path / "pd"
    pd_file = _write_yaml(pd_dir / "repos.yaml", VALID_YAML)

    res = resolve_config(env={}, platformdirs_dir=pd_dir)
    assert res.source == "platformdirs"
    assert res.path == pd_file


def test_not_found_returns_record_not_exception(empty_dir: Path) -> None:
    res = resolve_config(env={}, platformdirs_dir=empty_dir)
    assert res.source == "not_found"
    assert res.config is None
    assert res.path is None
    assert len(res.searched_paths) == 1
    assert res.searched_paths[0] == empty_dir / "repos.yaml"


def test_cli_path_missing_is_an_error(tmp_path: Path, empty_dir: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigLoadError) as exc:
        resolve_config(cli_path=missing, env={}, platformdirs_dir=empty_dir)
    assert "--config" in exc.value.message


def test_env_path_missing_is_an_error(tmp_path: Path, empty_dir: Path) -> None:
    missing = tmp_path / "ghost.yaml"
    with pytest.raises(ConfigLoadError) as exc:
        resolve_config(
            env={ENV_CONFIG_PATH: str(missing)},
            platformdirs_dir=empty_dir,
        )
    assert ENV_CONFIG_PATH in exc.value.message


def test_relative_env_path_resolves_against_cwd(
    tmp_path: Path, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_yaml(tmp_path / "local.yaml", VALID_YAML)
    res = resolve_config(
        env={ENV_CONFIG_PATH: "local.yaml"},
        platformdirs_dir=empty_dir,
    )
    assert res.source == "env"
    assert res.path is not None
    assert res.path.name == "local.yaml"


def test_yaml_parse_error_raises(tmp_path: Path, empty_dir: Path) -> None:
    bad = _write_yaml(tmp_path / "broken.yaml", BROKEN_YAML)
    with pytest.raises(ConfigLoadError) as exc:
        resolve_config(cli_path=bad, env={}, platformdirs_dir=empty_dir)
    assert "YAML parse error" in exc.value.message


def test_unknown_host_reference_raises(tmp_path: Path, empty_dir: Path) -> None:
    bad = _write_yaml(tmp_path / "bad.yaml", UNKNOWN_HOST_YAML)
    with pytest.raises(ConfigLoadError) as exc:
        resolve_config(cli_path=bad, env={}, platformdirs_dir=empty_dir)
    assert "unknown github_hosts" in exc.value.message


def test_extra_fields_are_rejected(tmp_path: Path, empty_dir: Path) -> None:
    extra = VALID_YAML + "  extra_field: nope\n"
    bad = _write_yaml(tmp_path / "extra.yaml", extra)
    with pytest.raises(ConfigLoadError):
        resolve_config(cli_path=bad, env={}, platformdirs_dir=empty_dir)


def test_empty_yaml_yields_empty_config(tmp_path: Path, empty_dir: Path) -> None:
    empty = _write_yaml(tmp_path / "empty.yaml", "")
    res = resolve_config(cli_path=empty, env={}, platformdirs_dir=empty_dir)
    assert isinstance(res.config, AppConfig)
    assert res.config.github_hosts == {}
    assert res.config.repos == []


def test_write_template_creates_dir_and_file(tmp_path: Path) -> None:
    target_dir = tmp_path / "nested" / "user-config"
    written = write_template_config(target_dir)
    assert written.exists()
    assert written.name == "repos.yaml"
    body = written.read_text(encoding="utf-8")
    assert "github_hosts:" in body
    assert "token_env: GITHUB_TOKEN" in body


def test_write_template_refuses_overwrite_without_force(tmp_path: Path) -> None:
    write_template_config(tmp_path)
    with pytest.raises(FileExistsError):
        write_template_config(tmp_path)


def test_write_template_force_overwrites(tmp_path: Path) -> None:
    target = write_template_config(tmp_path)
    target.write_text("changed", encoding="utf-8")
    write_template_config(tmp_path, force=True)
    assert "github_hosts:" in target.read_text(encoding="utf-8")


def test_template_yaml_round_trips_through_validator(tmp_path: Path) -> None:
    """The shipped template must parse cleanly itself."""
    target = write_template_config(tmp_path)
    res = resolve_config(cli_path=target, env={}, platformdirs_dir=tmp_path)
    assert res.config is not None
    assert "cloud" in res.config.github_hosts


# CLI integration tests


def test_cli_config_path_reports_not_found(
    empty_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(ENV_CONFIG_PATH, "")  # ignored — empty string
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.setattr(
        "github_sdlc_mcp.__main__.default_platformdirs_dir", lambda: empty_dir
    )
    monkeypatch.setattr(
        "github_sdlc_mcp.config.default_platformdirs_dir", lambda: empty_dir
    )
    rc = main(["config", "path"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Config not found" in captured.err


def test_cli_config_path_reports_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    f = _write_yaml(tmp_path / "repos.yaml", VALID_YAML)
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    rc = main(["--config", str(f), "config", "path"])
    captured = capsys.readouterr()
    assert rc == 0
    assert str(f) in captured.out
    assert "Source: cli" in captured.out


def test_cli_config_validate_reports_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = _write_yaml(tmp_path / "bad.yaml", UNKNOWN_HOST_YAML)
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    rc = main(["--config", str(bad), "config", "validate"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Config error" in captured.err
    assert "unknown github_hosts" in captured.err


def test_cli_config_validate_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    f = _write_yaml(tmp_path / "ok.yaml", VALID_YAML)
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    rc = main(["--config", str(f), "config", "validate"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Config OK" in captured.out
    assert "github_hosts: 1" in captured.out
    assert "repos: 1" in captured.out


def test_cli_config_init_writes_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "github_sdlc_mcp.__main__.default_platformdirs_dir", lambda: tmp_path
    )
    rc = main(["config", "init"])
    captured = capsys.readouterr()
    assert rc == 0
    assert (tmp_path / "repos.yaml").exists()
    assert "Wrote template" in captured.out

    # Second run should refuse without --force
    rc = main(["config", "init"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "already exists" in captured.err

    # --force overwrites
    rc = main(["config", "init", "--force"])
    assert rc == 0


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "github-sdlc-mcp 0.1.0" in captured.out


def test_cli_server_run_with_no_config_succeeds(
    empty_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Server entry point must not crash when no config exists yet."""
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.setattr(
        "github_sdlc_mcp.config.default_platformdirs_dir", lambda: empty_dir
    )
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "phase 2" in captured.err
