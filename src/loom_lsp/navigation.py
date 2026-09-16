"""Moving between nodes: workspace symbols, dependencies as a call hierarchy, and the titles shown beside references.

Like `analysis`, everything here reads a `ScanResult` and returns plain data with code-point offsets; the protocol layer turns it into LSP types. The search is loom's own `search_entries` and the edges are loom's graph, so the editor finds and follows exactly what `loom search` and `loom deps` report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loom.cli.nodes import search_entries
from loom.scan.scan import ScanResult

from loom_lsp.analysis import _COMMAND


@dataclass(frozen=True)
class Place:
    """A key and where it is defined: its file, its span, and the span of its label."""

    key: str
    kind: str  # node | section | proof | digest | master
    name: str  # "sy-0003  Theorem: Fixed points"
    detail: str  # "Theorem: Fixed points"
    file: str
    start: int
    end: int
    label_start: int
    label_end: int
    container: str = ""  # aliases and tags, for a picker to show beside the name


@dataclass(frozen=True)
class Call:
    """One neighbour in the dependency graph, with the reference sites that connect it, all in `file`."""

    place: Place
    file: str
    sites: list[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class Hint:
    """Text to show after a reference or inclusion site ends."""

    offset: int
    label: str


def _describe(result: ScanResult, key: str) -> str:
    n = result.assembly.nodes[key]
    taxon = n.taxon or n.kind
    return f"{taxon}: {n.title}" if n.title else taxon


def place(result: ScanResult, key: str) -> Place | None:
    """Where `key` lives, or None for an unknown key. A proof is reported as its statement."""
    graph = result.graph
    stmt = graph.statement_key(key) if graph is not None else key
    n = result.assembly.nodes.get(stmt)
    if n is None:
        return None
    label_start = n.label_offsets.get(n.id or "", n.start) if n.label_offsets else n.start
    label_end = label_start
    src = result.files.get(n.file)
    written = f"\\label{{{n.id}}}"
    if n.id and src is not None and src.text.startswith(written, label_start):
        label_end = label_start + len(written)  # the offset is the `\label` command's backslash
    detail = _describe(result, stmt)
    kind = "master" if n.kind == "master" else ("digest" if n.digest else n.kind)
    tags = [t.strip() for t in n.directives.get("tags", "").split(",") if t.strip()]
    container = ", ".join([*n.aliases, *(f"#{t}" for t in tags)])
    return Place(stmt, kind, f"{stmt}  {detail}", detail, n.file, n.start, n.end, label_start, label_end, container)


def workspace_symbols(result: ScanResult, query: str) -> list[Place]:
    """Every key `loom search QUERY` finds, in its order; an empty query finds every key."""
    out: list[Place] = []
    for entry in search_entries(result, query, None):
        p = place(result, str(entry["key"]))
        if p is not None and p.key == entry["key"]:
            out.append(p)
    return out


def _site(result: ScanResult, file: str, offset: int) -> tuple[int, int]:
    """The span of the command at `offset`: from its backslash to its closing brace."""
    src = result.files.get(file)
    m = _COMMAND.match(src.text, offset) if src is not None else None
    return (offset, m.end()) if m else (offset, offset + 1)


def _group(result: ScanResult, pairs: list[tuple[str, str, int]], caller_file_of: dict[str, str]) -> list[Call]:
    """(neighbour, file, offset) triples grouped by neighbour, in first-seen order, keeping only sites in the caller's file."""
    order: list[str] = []
    sites: dict[str, list[tuple[int, int]]] = {}
    for key, file, offset in pairs:
        if key not in sites:
            order.append(key)
            sites[key] = []
        if file == caller_file_of.get(key) and offset >= 0:
            span = _site(result, file, offset)
            if span not in sites[key]:
                sites[key].append(span)
    out: list[Call] = []
    for key in order:
        p = place(result, key)
        if p is not None:
            out.append(Call(p, caller_file_of.get(key, p.file), sites[key]))
    return out


def outgoing(result: ScanResult, key: str) -> list[Call]:
    """What `key` depends on: the targets of its statement's and its proofs' edges, with the sites in `key`'s own file."""
    graph = result.graph
    n = result.assembly.nodes.get(key)
    if graph is None or n is None:
        return []
    stmt = graph.statement_key(key)
    own = result.assembly.nodes[stmt]
    pairs: list[tuple[str, str, int]] = []
    for src in [stmt, *own.proofs]:
        for e in graph.out.get(src, []):
            target = graph.statement_key(e.to)
            if target != stmt:
                pairs.append((target, e.file, e.offset))
    return _group(result, pairs, {k: own.file for k, _f, _o in pairs})


def incoming(result: ScanResult, key: str) -> list[Call]:
    """What depends on `key`: the statements whose own or proofs' edges reach it, with the sites in each dependent's file."""
    graph = result.graph
    n = result.assembly.nodes.get(key)
    if graph is None or n is None:
        return []
    stmt = graph.statement_key(key)
    targets = {stmt, *result.assembly.nodes[stmt].proofs}
    pairs: list[tuple[str, str, int]] = []
    files: dict[str, str] = {}
    for t in targets:
        for e in graph.inc.get(t, []):
            source = graph.statement_key(e.src)
            if source == stmt or source not in result.assembly.nodes:
                continue
            pairs.append((source, e.file, e.offset))
            files.setdefault(source, result.assembly.nodes[source].file)
    return _group(result, pairs, files)


def _numbers(result: ScanResult) -> dict[str, str]:
    """Label to number in the default master, read once per request; empty before the master is compiled."""
    from loom.tex.aux import read_numbers

    if not result.default_master:
        return {}
    try:
        return {label: aux.number for label, aux in read_numbers(result.quilt.root, result.default_master).items()}
    except Exception:
        return {}


def _hint_label(result: ScanResult, key: str, numbers: dict[str, str]) -> str:
    n = result.assembly.nodes[key]
    number = next((numbers[lab] for lab in [n.id, *n.labels] if lab and lab in numbers), None)
    head = " ".join(x for x in (n.taxon or n.kind, number) if x)
    return f"{head} {n.title}" if n.title else head


def _file_node(result: ScanResult, file: str) -> str | None:
    """The first theorem-like or section node in `file`, which is what an `\\input` of it brings in."""
    best: tuple[int, str] | None = None
    for key, n in result.assembly.nodes.items():
        if n.file == file and n.kind in ("environment", "section"):
            if best is None or n.start < best[0]:
                best = (n.start, key)
    return best[1] if best else None


def inlay_hints(result: ScanResult, rel: str, start: int, end: int) -> list[Hint]:
    """One hint per reference or inclusion site in `rel` between `start` and `end`, naming what it points to."""
    graph = result.graph
    numbers = _numbers(result)
    by_site: dict[int, tuple[int, list[str]]] = {}
    for e in result.edges.edges:
        if e.file != rel or e.offset < 0 or not (start <= e.offset < end):
            continue
        target = graph.statement_key(e.to) if graph is not None else e.to
        if target not in result.assembly.nodes:
            continue
        site_end = _site(result, rel, e.offset)[1]
        _, keys = by_site.setdefault(e.offset, (site_end, []))
        if target not in keys:
            keys.append(target)
    out = [
        Hint(site_end, "; ".join(_hint_label(result, k, numbers) for k in keys)) for site_end, keys in by_site.values()
    ]

    seen: set[int] = set()
    for expansion in result.expansions.values():
        for inc in expansion.inclusions:
            if inc.parent != rel or inc.child is None or inc.site_start in seen or not (start <= inc.site_start < end):
                continue
            seen.add(inc.site_start)
            key = _file_node(result, inc.child)
            if key is not None:
                out.append(Hint(inc.site_end, _hint_label(result, key, numbers)))
    return sorted(out, key=lambda h: h.offset)
