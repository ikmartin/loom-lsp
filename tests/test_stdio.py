"""One test that drives the real binary over stdio, so the entry point, the framing, and the handshake are exercised rather than assumed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

QUILTS = Path(__file__).resolve().parents[2] / "loom" / "tests" / "quilts"


def _frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload).encode()
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


def _messages(stream, deadline: float):  # type: ignore[no-untyped-def]
    """Yield every JSON-RPC message the server sends until the deadline."""
    buf = b""
    while time.monotonic() < deadline:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
        if not chunk:
            break
        buf += chunk
        while b"\r\n\r\n" in buf:
            head, rest = buf.split(b"\r\n\r\n", 1)
            length = next(
                (int(x.split(b":")[1]) for x in head.split(b"\r\n") if x.lower().startswith(b"content-length")), None
            )
            if length is None or len(rest) < length:
                break
            yield json.loads(rest[:length])
            buf = rest[length:]


def test_the_binary_answers_over_stdio(tmp_path: Path) -> None:
    quilt = tmp_path / "synthetic"
    shutil.copytree(QUILTS / "synthetic", quilt)
    node = quilt / "nodes" / "sy-0003.tex"

    exe = shutil.which("loom-lsp")
    cmd = [exe] if exe else [sys.executable, "-m", "loom_lsp.cli"]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"processId": None, "rootUri": quilt.as_uri(), "capabilities": {}},
                }
            )
        )
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": node.as_uri(),
                            "languageId": "tex",
                            "version": 1,
                            "text": node.read_text(encoding="utf-8"),
                        }
                    },
                }
            )
        )
        proc.stdin.flush()

        got_initialize = False
        got_diagnostics = False
        for msg in _messages(proc.stdout, time.monotonic() + 45):
            if msg.get("id") == 1 and "result" in msg:
                got_initialize = True
                caps = msg["result"]["capabilities"]
                assert "hoverProvider" in caps and "definitionProvider" in caps
            if msg.get("method") == "textDocument/publishDiagnostics":
                got_diagnostics = True
                break
        assert got_initialize, "the server did not answer initialize"
        assert got_diagnostics, "the server published no diagnostics"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_transport_flag_clients_append_is_accepted() -> None:
    """`vscode-languageclient` invokes a stdio server as `loom-lsp --stdio`. Rejecting the flag exits 2 before a byte is written, and the editor reports only "server process exited with code 2"."""
    exe = shutil.which("loom-lsp")
    cmd = [exe, "--stdio", "--version"] if exe else [sys.executable, "-m", "loom_lsp.cli", "--stdio", "--version"]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    assert done.returncode == 0, done.stderr
    assert "loom-lsp" in done.stdout
