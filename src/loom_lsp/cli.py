"""The `loom-lsp` entry point: an LSP server over stdio, or `--version`."""

from __future__ import annotations

import argparse
import logging
import sys

from loom_lsp import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loom-lsp", description="A language server for loom quilts.")
    parser.add_argument("--version", action="version", version=f"loom-lsp {__version__}")
    parser.add_argument("--log", default="warning", help="Logging level on stderr (default: warning).")
    parser.add_argument("--tcp", metavar="PORT", type=int, default=None, help="Serve over TCP instead of stdio.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.WARNING), stream=sys.stderr)

    from loom_lsp.server import server

    if args.tcp is not None:
        server.start_tcp("127.0.0.1", args.tcp)
    else:
        server.start_io()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
