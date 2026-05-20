"""CLI entry point: run the FastMCP server. No config subcommands in v0.2.0."""

from __future__ import annotations

import argparse
import logging
import sys

from github_sdlc_mcp import __version__

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
    parser.add_argument(
        "-V", "--version", action="version", version=f"github-sdlc-mcp {__version__}"
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
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)

    from github_sdlc_mcp.server import build_context, build_server

    ctx = build_context()
    server = build_server(ctx)
    logger.info(
        "Starting FastMCP server (transport=%s%s)",
        args.transport,
        f", port={args.port}" if args.transport != "stdio" else "",
    )
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(transport="streamable-http", port=args.port, show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
