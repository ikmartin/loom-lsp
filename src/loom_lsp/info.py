"""Hover text, document symbols, and completion items. All of it is read from a `ScanResult` and the review records; none of it writes."""

from __future__ import annotations

from dataclasses import dataclass

from loom.records.store import Records
from loom.scan.scan import ScanResult

_EXCERPT_LINES = 6


def _number(result: ScanResult, key: str, master: str) -> str | None:
    """The node's number in `master`, when the master has been compiled and the `.aux` read."""
    from loom.tex.aux import read_numbers

    try:
        table = read_numbers(result.quilt.root, master)
    except Exception:
        return None
    n = result.assembly.nodes.get(key)
    if n is None:
        return None
    for lab in [n.id, *n.labels]:
        if lab and lab in table:
            return table[lab].number
    return None


def _statement_excerpt(result: ScanResult, key: str) -> str:
    n = result.assembly.nodes.get(key)
    if n is None or n.file not in result.files:
        return ""
    text = result.files[n.file].text
    start = n.own[0][0] if n.own else n.start
    end = n.own[0][1] if n.own else min(n.end, start + 600)
    body = text[start:end].strip()
    lines = [ln for ln in body.splitlines() if ln.strip()][:_EXCERPT_LINES]
    return "\n".join(lines)


def hover(result: ScanResult, key: str, records: Records | None = None) -> str:
    """Markdown for a key: what it is, where it is numbered, what state it is in, and the first lines of its text."""
    n = result.assembly.nodes.get(key)
    if n is None:
        return ""
    head = f"**{n.taxon or n.kind}**"
    if n.title:
        head += f" — {n.title}"
    parts = [f"{head}  \n`{key}`"]
    default = result.default_master
    if default:
        number = _number(result, key, default)
        if number:
            parts[0] += f" · {n.taxon or ''} {number} in `{default}`".replace("  ", " ")
        elif default in n.exp_ranges:
            parts[0] += f" · reached by `{default}`"
    if n.aliases:
        parts[0] += "  \naliases: " + ", ".join(f"`{a}`" for a in n.aliases)
    if records is not None:
        states = records.key_states(result)
        st = states.get(key)
        if st is not None:
            line = f"state: **{st.state}**"
            if getattr(st, "fresh", True) is False:
                causes = ", ".join(c.kind for c in getattr(st, "causes", []) or [])
                line += f" · stale{f' ({causes})' if causes else ''}"
            open_counts = getattr(st, "open", None) or {}
            if open_counts:
                line += " · " + ", ".join(f"{v} open {k}" for k, v in open_counts.items() if v)
            parts.append(line)
    if n.incomplete:
        parts.append("incomplete: " + "; ".join(n.incomplete))
    excerpt = _statement_excerpt(result, key)
    if excerpt:
        parts.append("```latex\n" + excerpt + "\n```")
    return "\n\n".join(parts)


@dataclass
class Symbol:
    key: str
    name: str
    detail: str
    kind: str  # section | environment | proof
    start: int
    end: int
    children: list[Symbol]


def symbols(result: ScanResult, rel: str) -> list[Symbol]:
    """The file's nodes as a tree: sections contain environments, environments contain their proofs."""
    nodes = [
        n for n in result.assembly.nodes.values() if n.file == rel and n.kind in ("section", "environment", "proof")
    ]
    nodes.sort(key=lambda n: (n.start, -(n.end - n.start)))
    made: dict[str, Symbol] = {}
    roots: list[Symbol] = []
    stack: list[Symbol] = []
    for n in nodes:
        sym = Symbol(
            key=n.key,
            name=n.title or n.id or n.key,
            detail=n.taxon or n.kind,
            kind=n.kind,
            start=n.start,
            end=n.end,
            children=[],
        )
        made[n.key] = sym
        if n.kind == "proof" and n.of and n.of in made:
            made[n.of].children.append(sym)  # a proof is a sibling in the file and a child in the outline
            continue
        while stack and not (stack[-1].start <= n.start and n.end <= stack[-1].end):
            stack.pop()
        (stack[-1].children if stack else roots).append(sym)
        stack.append(sym)
    return roots


DIRECTIVE_KEYS = [
    ("author", "author(s) of the node"),
    ("created", "creation date, YYYY-MM-DD"),
    ("tags", "thematic labels, comma separated"),
    ("see", "related nodes for the viewer; never a dependency"),
    ("environment", "declare a taxon the preamble does not"),
    ("digest", "this file is the digest of a citekey"),
    ("source", "provenance of a digest"),
    ("method", "how a digest was produced"),
    ("requires", "packages a digest's statements need"),
    ("numbering", "emulated, when the reference produced no .aux"),
    ("ignore", "do not scan this file"),
]


@dataclass
class Completion:
    label: str
    detail: str
    kind: str  # id | citekey | taxon | directive | digest


def completions(result: ScanResult, text: str, offset: int) -> list[Completion]:
    """What can be written here: directive keys after `% !LOOM`, taxa after `\\begin{`, citekeys in a citation, digest node ids after `\\cite[`, and ids and aliases everywhere else."""
    line_start = text.rfind("\n", 0, offset) + 1
    prefix = text[line_start:offset]

    if prefix.lstrip().startswith("%") and "!LOOM" in prefix:
        return [Completion(k, d, "directive") for k, d in DIRECTIVE_KEYS]
    if prefix.rstrip().endswith("\\begin{") or prefix.rstrip().endswith("\\end{"):
        return [Completion(env, taxon.name, "taxon") for env, taxon in sorted(result.taxa.items())]

    open_brace = prefix.rfind("{")
    open_cmd = prefix.rfind("\\")
    if open_brace > open_cmd >= 0:
        command = prefix[open_cmd + 1 : open_brace].split("[")[0].rstrip("*")
        if command in ("cite", "parencite", "textcite", "autocite", "citep", "citet", "footcite"):
            digests = {ck for f, ck in result.assembly.digest_files.items()}
            return [
                Completion(ck, "digested" if ck in digests else "no digest yet", "citekey") for ck in sorted(result.bib)
            ]
    if "\\cite[" in prefix:
        return [
            Completion(key, f"{n.taxon or ''} {n.title or ''}".strip(), "digest")
            for key, n in sorted(result.assembly.nodes.items())
            if n.digest
        ]

    out: list[Completion] = []
    for key, n in sorted(result.assembly.nodes.items()):
        if n.kind in ("master", "file"):
            continue
        detail = f"{n.taxon or n.kind}" + (f" · {n.title}" if n.title else "")
        out.append(Completion(key, detail, "id"))
        for alias in n.aliases:
            out.append(Completion(alias, f"alias of {key} · {detail}", "id"))
    return out
