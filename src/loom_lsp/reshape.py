"""Reshaping the node under the cursor: moving it into `nodes/<id>.tex`, and giving it an id when it has none.

Both are plans loom computes and the editor applies as one edit, so the author's file is changed by the author's editor, with undo intact and an unsaved buffer included. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from loom.reshape.atomize import plan_atomize, verify_plan
from loom.reshape.ids import plan_insertions
from loom.scan.alloc import visible_locals
from loom.scan.labels import next_local
from loom.scan.scan import ScanResult


@dataclass(frozen=True)
class Move:
    """One node's move out of `file`: the region to replace, what stands in its place, and the file to create."""

    key: str
    file: str
    start: int
    end: int
    replacement: str
    target: str  # quilt-relative path of the new node file
    text: str  # its whole content


@dataclass(frozen=True)
class Label:
    """An id for a node that has none: where the `\\label` goes and what it says."""

    key: str
    file: str
    offset: int
    text: str
    node_id: str


def move_for(result: ScanResult, rel: str, key: str) -> tuple[Move | None, str | None]:
    """The move that atomizes `key` out of `rel`, or None and why it is refused. A proof is moved by the statement that carries it."""
    plan = plan_atomize(result, rel, rel, keys=[key])
    if plan.refusals:
        return None, plan.refusals[0]
    if not plan.moves:
        return None, f"nothing to move for {key}"
    problem = verify_plan(result, plan)
    if problem is not None:
        return None, problem
    m = plan.moves[0]
    stem = m.target[: -len(".tex")] if m.target.endswith(".tex") else m.target
    return Move(m.key, rel, m.start, m.end, f"\\input{{{stem}}}", m.target, m.text), None


def label_for(result: ScanResult, rel: str, key: str) -> Label | None:
    """The `\\label{<next id>}` for the node `key` of `rel`, or None when it has an id already or takes no label."""
    node = result.assembly.nodes.get(key)
    if node is None or node.id:
        return None
    prefix = result.quilt.config.prefix
    first = next_local(visible_locals(result, prefix))
    for ins in plan_insertions(result, [rel], prefix, first):
        if ins.key == key:
            return Label(key, rel, ins.offset, ins.text, ins.text[len("\\label{") : -1])
    return None
