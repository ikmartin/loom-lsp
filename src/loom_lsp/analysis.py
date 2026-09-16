"""What is under the cursor, and the answers to the questions an editor asks about it.

Everything here reads a `ScanResult` and returns plain data; the protocol layer turns it into LSP types. Nothing here writes, and nothing runs a subprocess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loom.scan.scan import ScanResult

REF_CMDS = ("ref", "eqref", "cref", "Cref", "autoref", "pageref", "vref", "Vref", "uses")
CITE_CMDS = ("cite", "parencite", "textcite", "autocite", "citep", "citet", "footcite")
INCLUDE_CMDS = ("input", "include", "nest")

_COMMAND = re.compile(
    r"\\(" + "|".join([*REF_CMDS, *CITE_CMDS, *INCLUDE_CMDS]) + r")\*?(?![A-Za-z@])\s*(\[[^\]]*\])?\s*\{([^}]*)\}"
)
_DIRECTIVE_SEE = re.compile(r"^[ \t]*%[ \t]*!LOOM[ \t]+see[ \t]*:[ \t]*(.*)$", re.M)


@dataclass(frozen=True)
class Token:
    """A citation, reference, inclusion or `see:` item the cursor sits in."""

    kind: str  # ref | cite | include | see
    command: str
    value: str  # the single item under the cursor, not the whole comma list
    start: int  # code-point offset of the item in the file
    end: int
    postnote: str | None = None


def _items(arg: str, base: int) -> list[tuple[str, int, int]]:
    """(item, start, end) for each comma-separated item of a braced argument, with offsets into the file."""
    out: list[tuple[str, int, int]] = []
    pos = 0
    for part in arg.split(","):
        start = base + pos
        stripped = part.strip()
        if stripped:
            lead = len(part) - len(part.lstrip())
            out.append((re.sub(r"\s+", " ", stripped), start + lead, start + lead + len(stripped)))
        pos += len(part) + 1
    return out


def token_at(text: str, offset: int) -> Token | None:
    """The reference, citation, inclusion or `see:` item at `offset`, or None."""
    for m in _COMMAND.finditer(text):
        cmd = m.group(1)
        arg_start = m.start(3)
        if not (m.start() <= offset <= m.end()):
            continue
        kind = "ref" if cmd in REF_CMDS else ("cite" if cmd in CITE_CMDS else "include")
        postnote = (m.group(2) or "")[1:-1] or None
        items = _items(m.group(3), arg_start)
        for value, a, b in items:
            if a <= offset <= b:
                return Token(kind, cmd, value, a, b, postnote)
        if items:  # the cursor is on the command itself: take the first item
            value, a, b = items[0]
            return Token(kind, cmd, value, a, b, postnote)
    for m in _DIRECTIVE_SEE.finditer(text):
        if m.start() <= offset <= m.end():
            for value, a, b in _items(m.group(1), m.start(1)):
                if a <= offset <= b:
                    return Token("see", "see", value, a, b)
    return None


def node_at(result: ScanResult, rel: str, offset: int) -> str | None:
    """The key of the innermost node whose own text covers `offset`, or None."""
    best: tuple[int, str] | None = None
    for key, n in result.assembly.nodes.items():
        if n.file != rel or n.kind in ("master", "file"):
            continue
        for a, b in n.own:
            if a <= offset < b:
                span = b - a
                if best is None or span < best[0]:
                    best = (span, key)
    return best[1] if best else None


@dataclass(frozen=True)
class Target:
    """Where a definition lives: a file, a code-point offset, and how long the name is."""

    file: str
    offset: int
    length: int
    key: str


def definition(result: ScanResult, token: Token) -> Target | None:
    """The definition site of a token: a node's `\\label`, a digest node, a cited file, or an included file."""
    asm = result.assembly
    if token.kind == "include":
        for cand in (token.value, token.value + ".tex"):
            if cand in result.files:
                return Target(cand, 0, 0, cand)
        return None
    if token.kind == "cite":
        digest = {ck: f for f, ck in asm.digest_files.items()}.get(token.value)
        if digest is None:
            return None
        if token.postnote:
            from loom.scan.postnote import locator_of

            wanted = locator_of(token.postnote)
            for key, n in asm.nodes.items():
                if n.digest == token.value and wanted and locator_of(n.title) == wanted:
                    return _label_target(result, key)
        return Target(digest, 0, 0, digest)
    target = asm.labels.get(token.value)
    if target is None:
        return None
    region = asm.regions.get(target)
    if region is not None:
        return Target(region.file, region.offset, len(region.label), region.container)
    return _label_target(result, target)


def _label_target(result: ScanResult, key: str) -> Target | None:
    n = result.assembly.nodes.get(key)
    if n is None:
        return None
    offset = n.label_offsets.get(n.id or "", n.start) if n.label_offsets else n.start
    return Target(n.file, offset, len(n.id or ""), key)


def references(result: ScanResult, key: str) -> list[tuple[str, int, str]]:
    """(file, offset, from_key) for every `\\ref` or `\\uses` site that resolves to `key` or to one of its proofs."""
    node = result.assembly.nodes.get(key)
    targets = {key} | set(node.proofs if node else [])
    out: list[tuple[str, int, str]] = []
    for e in result.edges.edges:
        if e.offset < 0:
            continue
        if e.to in targets or (result.graph is not None and result.graph.statement_key(e.to) in targets):
            out.append((e.file, e.offset, e.src))
    for r in result.relations:
        if r.to_key in targets or r.from_key in targets:
            out.append((r.file, max(0, r.column - 1 if r.column else 0), r.from_key))
    return out
