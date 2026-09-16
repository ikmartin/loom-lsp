"""The quilt a buffer belongs to, and the last good scan of it.

Loom's `scan` is the whole analysis and takes 100 to 170 ms on real papers, so the server rescans on a debounce and answers every request from the last result. Per-keystroke scanning is not attempted, and nothing here writes to the quilt.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from loom.cli.lint_cmd import all_diagnostics
from loom.scan.model import Diagnostic
from loom.scan.quilt import Quilt, load_quilt
from loom.scan.scan import ScanResult, scan


def path_of(uri: str) -> Path:
    """The filesystem path of a `file:` URI, percent-decoded."""
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


def uri_of(path: Path) -> str:
    return path.resolve().as_uri()


def find_quilt(start: Path) -> Path | None:
    """The quilt root at or above `start`: the nearest directory with a `config.toml` holding a `[quilt]` table.

    An ordinary `.tex` file outside a quilt has none, and the server refuses politely rather than guessing, so a LaTeX workspace that has nothing to do with loom is untouched.
    """
    here = start if start.is_dir() else start.parent
    for d in [here, *here.parents]:
        cfg = d / "config.toml"
        if cfg.is_file():
            try:
                if "[quilt]" in cfg.read_text(encoding="utf-8", errors="replace"):
                    return d
            except OSError:
                continue
    return None


@dataclass
class Snapshot:
    """A scan and the diagnostics derived from it, including the ones the review ledger contributes."""

    result: ScanResult
    diagnostics: list[Diagnostic]

    @property
    def root(self) -> Path:
        return self.result.quilt.root


@dataclass
class Workspace:
    """One quilt, its open buffers, and the last scan that succeeded."""

    root: Path
    quilt: Quilt
    buffers: dict[str, str] = field(default_factory=dict)  # quilt-relative path -> unsaved text
    snapshot: Snapshot | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def at(cls, root: Path) -> Workspace:
        return cls(root=root, quilt=load_quilt(root))

    def relative(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None

    def set_buffer(self, path: Path, text: str | None) -> None:
        rel = self.relative(path)
        if rel is None:
            return
        if text is None:
            self.buffers.pop(rel, None)
        else:
            self.buffers[rel] = text

    def text_of(self, rel: str) -> str:
        """The buffer if one is open, else the file on disk, normalised as loom normalises it."""
        if rel in self.buffers:
            return self.buffers[rel].replace("\r\n", "\n").replace("\r", "\n")
        try:
            raw = (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return raw.replace("\r\n", "\n").replace("\r", "\n")

    def rescan(self) -> Snapshot | None:
        """Rescan with the open buffers standing in for what is on disk. A scan that raises leaves the last good snapshot in place and records why."""
        with self.lock:
            try:
                result = scan(self.quilt, dict(self.buffers))
                self.snapshot = Snapshot(result, all_diagnostics(result))
                self.error = None
            except Exception as exc:  # a half-written buffer must never take the server down
                self.error = f"{type(exc).__name__}: {exc}"
            return self.snapshot
