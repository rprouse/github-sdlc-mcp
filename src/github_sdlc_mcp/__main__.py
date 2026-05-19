"""CLI entry point. Transport selection and subcommands land in later phases."""

from __future__ import annotations

import sys

from github_sdlc_mcp import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-V", "--version"}:
        print(f"github-sdlc-mcp {__version__}")
        return 0
    print(
        f"github-sdlc-mcp {__version__} — scaffold phase 1.\n"
        "Server and subcommands are not wired up yet.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
