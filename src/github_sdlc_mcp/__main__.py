"""CLI entry point: server runner + ``config path|init|validate`` subcommands."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from github_sdlc_mcp import __version__
from github_sdlc_mcp.config import (
    ConfigLoadError,
    default_platformdirs_dir,
    resolve_config,
    write_template_config,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Send all logs to stderr. stdout belongs to the MCP JSON-RPC channel."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-sdlc-mcp",
        description="FastMCP server exposing SDLC metrics derived from GitHub.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"github-sdlc-mcp {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override config file location (skips env var and platformdirs lookup).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport. Defaults to stdio (the MCP host's default).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for --transport streamable-http (ignored otherwise).",
    )

    sub = parser.add_subparsers(dest="subcommand")
    config_parser = sub.add_parser("config", help="Configuration tools.")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)

    config_sub.add_parser(
        "path",
        help="Print the resolved config path and how it was found.",
    )
    init_p = config_sub.add_parser(
        "init",
        help="Write a commented example repos.yaml to the user config dir.",
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing repos.yaml.",
    )
    config_sub.add_parser(
        "validate",
        help="Load the resolved config and report any errors.",
    )
    return parser


def _cmd_config_path(cli_config: Path | None) -> int:
    try:
        resolved = resolve_config(cli_path=cli_config)
    except ConfigLoadError as e:
        print(f"Config error at {e.path}:", file=sys.stderr)
        print(f"  {e.message}", file=sys.stderr)
        return 2
    if resolved.config is None:
        print("Config not found. Searched (in order):", file=sys.stderr)
        for p in resolved.searched_paths:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nRun 'github-sdlc-mcp config init' to write a template.",
            file=sys.stderr,
        )
        return 1
    print(f"Resolved config: {resolved.path}")
    print(f"Source: {resolved.source}")
    print("Search order:")
    for p in resolved.searched_paths:
        marker = " (chosen)" if p == resolved.path else ""
        print(f"  - {p}{marker}")
    return 0


def _cmd_config_init(force: bool) -> int:
    target_dir = default_platformdirs_dir()
    try:
        written = write_template_config(target_dir, force=force)
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Wrote template to {written}")
    print("Edit it to list the repos this server should monitor.")
    return 0


def _cmd_config_validate(cli_config: Path | None) -> int:
    try:
        resolved = resolve_config(cli_path=cli_config)
    except ConfigLoadError as e:
        print(f"Config error at {e.path}:", file=sys.stderr)
        print(f"  {e.message}", file=sys.stderr)
        return 2
    if resolved.config is None:
        print("Config not found. Nothing to validate.", file=sys.stderr)
        for p in resolved.searched_paths:
            print(f"  - {p}", file=sys.stderr)
        return 1
    cfg = resolved.config
    print(f"Config OK: {resolved.path}")
    print(f"  github_hosts: {len(cfg.github_hosts)}")
    print(f"  repos: {len(cfg.repos)}")
    # Reachability checks deferred to phase 4 when the HTTP client lands.
    return 0


def _cmd_run_server(cli_config: Path | None, transport: str, port: int) -> int:
    try:
        resolved = resolve_config(cli_path=cli_config)
    except ConfigLoadError as e:
        logger.error("Config load failed at %s: %s", e.path, e.message)
        return 2
    if resolved.config is None:
        logger.info(
            "No config found yet (searched %s). The server will start with "
            "zero configured repos. Run 'github-sdlc-mcp config init' to "
            "write a template.",
            ", ".join(str(p) for p in resolved.searched_paths),
        )
    else:
        logger.info(
            "Loaded config from %s (%d hosts, %d repos)",
            resolved.path,
            len(resolved.config.github_hosts),
            len(resolved.config.repos),
        )
    # Import lazily so `config` subcommands don't pay the FastMCP import cost.
    from github_sdlc_mcp.server import build_context, build_server

    ctx = build_context(resolved)
    server = build_server(ctx)
    logger.info(
        "Starting FastMCP server (transport=%s%s)",
        transport,
        f", port={port}" if transport != "stdio" else "",
    )
    if transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(transport="streamable-http", port=port, show_banner=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "config":
        if args.config_action == "path":
            return _cmd_config_path(args.config)
        if args.config_action == "init":
            return _cmd_config_init(args.force)
        if args.config_action == "validate":
            return _cmd_config_validate(args.config)
        parser.error("unknown config action")
        return 2  # unreachable; parser.error exits

    return _cmd_run_server(args.config, args.transport, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
