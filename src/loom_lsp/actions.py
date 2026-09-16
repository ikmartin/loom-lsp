"""Code actions. Each one is a `loom` command line the editor runs; the server never writes to the quilt itself.

The server builds the argument vector and hands it back as a command the client executes, so the client can show it, confirm it, and put its output where the user expects. Every action here changes something a user would want to see first, which is why none of them is a workspace edit applied silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loom.scan.model import Diagnostic
from loom.scan.scan import ScanResult


@dataclass
class Action:
    title: str
    command: str  # the loom subcommand, for the client's own logic
    argv: list[str]  # the whole argument vector, loom first
    confirm: str  # what the client shows before running it; these write to the quilt
    kind: str = "quickfix"
    data: dict[str, str] = field(default_factory=dict)


def _loom(binary: str, root: str, *args: str) -> list[str]:
    return [binary, *args, "--quilt", root]


def actions_at(
    result: ScanResult,
    diagnostics: list[Diagnostic],
    rel: str,
    line: int,
    key: str | None,
    *,
    loom_bin: str = "loom",
) -> list[Action]:
    """Every action offered at a position: the fixes for the diagnostics on this line, then the things one can do to the node under the cursor."""
    root = str(result.quilt.root)
    out: list[Action] = []

    for d in diagnostics:
        if not any(loc.file == rel and loc.line == line for loc in d.locations):
            continue
        if d.code == "loom:uses-missing":
            missing = _missing_uses(d.message)
            target = d.keys[0] if d.keys else key
            if missing and target:
                out.append(
                    Action(
                        title=f"Add {missing} to the \\uses of {target}",
                        command="uses",
                        argv=[],  # an edit the client applies, not a command; the server supplies the text
                        confirm=f"Add \\uses{{{missing}}} to {target}?",
                        data={"key": target, "label": missing, "file": rel},
                    )
                )

    if key:
        node = result.assembly.nodes.get(key)
        statement = node.of if node is not None and node.kind == "proof" and node.of else key
        out.append(
            Action(
                title=f"Accept {key}",
                command="accept",
                argv=_loom(loom_bin, root, "accept", key),
                confirm=f"Record an acceptance for {key}? This writes to the review ledger.",
                kind="refactor",
            )
        )
        out.append(
            Action(
                title=f"Atomize {rel} into nodes/",
                command="atomize",
                argv=_loom(loom_bin, root, "atomize", rel, "--ignore-src"),
                confirm=f"Atomize {rel}? This writes new files and marks the source ignored.",
                kind="refactor",
            )
        )
        out.append(
            Action(
                title=f"Open {statement} in arras",
                command="open",
                argv=[],  # the editor opens it on the server it owns, so no url is known here
                confirm="",
                kind="source",
                data={"key": statement},
            )
        )
    out.append(
        Action(
            title="Insert a node skeleton",
            command="new",
            argv=_loom(loom_bin, root, "new", "lemma", "Title", "--print"),
            confirm="",
            kind="source",
        )
    )
    return out


def _missing_uses(message: str) -> str | None:
    """`loom:uses-missing` reads "KEY references LABEL but its \\uses does not list it"."""
    parts = message.split(" references ", 1)
    if len(parts) != 2:
        return None
    return parts[1].split(" but ", 1)[0].strip() or None


def uses_edit(result: ScanResult, key: str, label: str) -> tuple[str, int, str] | None:
    """(file, offset, text) inserting `label` into the proof's `\\uses`, or adding a `\\uses` line when it has none."""
    import re

    n = result.assembly.nodes.get(key)
    if n is None or n.file not in result.files:
        return None
    text = result.files[n.file].clean
    for a, b in n.own:
        m = re.search(r"\\uses\s*\{([^}]*)\}", text[a:b])
        if m:
            inner = m.group(1).strip()
            at = a + m.end(1)
            return (n.file, at, (", " if inner else "") + label)
    start = n.own[0][0] if n.own else n.start
    return (n.file, start, f"\\uses{{{label}}}\n")
