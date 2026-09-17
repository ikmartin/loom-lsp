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
    # `vscode-languageclient` appends the transport as a flag, so a server it launches over stdio is invoked as `loom-lsp --stdio`. Rejecting it exits 2 before a byte is written, and the editor reports only "server process exited with code 2".
    parser.add_argument(
        "--stdio", action="store_true", help="Speak LSP over stdio (the default; accepted for clients that pass it)."
    )
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
