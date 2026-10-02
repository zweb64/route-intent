"""Command-line entry point for route-intent."""

import argparse
from collections.abc import Sequence

from route_intent import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="route-intent",
        description="Verify that a network's live routing state matches a declared intent.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code.

    argparse exits on its own for ``--version`` (code 0) and for invalid arguments (code 2).
    """
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
