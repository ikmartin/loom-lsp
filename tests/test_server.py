"""The server against a copy of loom's synthetic quilt: what it publishes, and what it answers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lsprotocol import types as lsp

from loom_lsp.server import (
    code_actions,
    complete,
    did_close,
    do_hover,
    document_symbols,
    find_references,
    goto_definition,
)
from loom_lsp.workspace import find_quilt


def test_the_quilt_is_found_by_walking_up_and_only_inside_one(tmp_path: Path, quilt: Path) -> None:
    assert find_quilt(quilt / "nodes" / "sy-0003.tex") == quilt
    assert find_quilt(quilt / "nodes") == quilt
    loose = tmp_path / "elsewhere"
    loose.mkdir()
    (loose / "paper.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    assert find_quilt(loose / "paper.tex") is None


def test_diagnostics_match_what_lint_reports(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    harness.open(quilt / "drafts" / "main.tex")
    out = subprocess.run(["loom", "lint", "--json", "--quilt", str(quilt)], capture_output=True, text=True, check=False)
    expected = json.loads(out.stdout)
    by_file: dict[str, set[tuple[str, int]]] = {}
    for d in expected:
        for loc in d["locations"]:
            by_file.setdefault(loc["file"], set()).add((d["code"], loc["line"]))
    for rel, wanted in by_file.items():
        got = {(d.code, d.range.start.line + 1) for d in harness.diagnostics(quilt / rel)}
        assert wanted <= got, (rel, wanted - got)


def test_a_dangling_reference_is_an_error_at_the_command_not_the_line(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main = quilt / "drafts" / "main.tex"
    harness.open(main)
    dangling = [d for d in harness.diagnostics(main) if d.code == "dangling-link"]
    assert dangling and dangling[0].severity == lsp.DiagnosticSeverity.Error
    assert dangling[0].code_description is not None
    line = main.read_text(encoding="utf-8").splitlines()[dangling[0].range.start.line]
    assert line[dangling[0].range.start.character :].startswith("\\ref{sy-9999}")


def test_an_unsaved_buffer_produces_diagnostics_the_disk_does_not_justify(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0003.tex"
    harness.open(node)
    assert not [d for d in harness.diagnostics(node) if d.code == "dangling-link"]

    edited = node.read_text(encoding="utf-8").replace("\\begin{proof}", "\\begin{proof}\nBy Lemma~\\ref{sy-7777}.", 1)
    harness.change(node, edited)
    assert [d for d in harness.diagnostics(node) if d.code == "dangling-link"]
    assert "sy-7777" not in node.read_text(encoding="utf-8")  # nothing was written to disk

    harness.change(node, node.read_text(encoding="utf-8"))
    assert not [d for d in harness.diagnostics(node) if d.code == "dangling-link"]


def test_definition_of_a_reference_a_citation_and_an_inclusion(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-000B.tex"
    uri = harness.open(node)
    pos = harness.position_of(node, "Theorem~\\ref{sy-0003}", len("Theorem~\\ref{") + 2)
    loc = goto_definition(
        harness.server,
        lsp.DefinitionParams(text_document=lsp.TextDocumentIdentifier(uri=uri), position=pos),
    )
    assert loc is not None and loc.uri.endswith("nodes/sy-0003.tex")
    target = (quilt / "nodes" / "sy-0003.tex").read_text(encoding="utf-8").splitlines()
    assert "sy-0003" in target[loc.range.start.line]

    main = quilt / "drafts" / "main.tex"
    uri = harness.open(main)
    pos = harness.position_of(main, "\\input{nodes/sy-0002}", 8)
    loc = goto_definition(
        harness.server,
        lsp.DefinitionParams(text_document=lsp.TextDocumentIdentifier(uri=uri), position=pos),
    )
    assert loc is not None and loc.uri.endswith("nodes/sy-0002.tex")


def test_references_finds_every_site_that_names_a_key(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0003.tex"
    uri = harness.open(node)
    pos = harness.position_of(node, "\\label{sy-0003}", 8)
    found = find_references(
        harness.server,
        lsp.ReferenceParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            position=pos,
            context=lsp.ReferenceContext(include_declaration=False),
        ),
    )
    assert found
    assert any(loc.uri.endswith("nodes/sy-000B.tex") for loc in found)


def test_hover_says_what_the_node_is_and_what_state_it_is_in(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0003.tex"
    uri = harness.open(node)
    pos = harness.position_of(node, "\\label{sy-0003}", 8)
    got = do_hover(harness.server, lsp.HoverParams(text_document=lsp.TextDocumentIdentifier(uri=uri), position=pos))
    assert got is not None
    text = got.contents.value
    assert "sy-0003" in text and "state:" in text and "```latex" in text


def test_document_symbols_are_a_tree_with_proofs_under_their_statements(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0003.tex"
    uri = harness.open(node)
    syms = document_symbols(harness.server, lsp.DocumentSymbolParams(text_document=lsp.TextDocumentIdentifier(uri=uri)))
    assert syms and syms[0].kind == lsp.SymbolKind.Class
    assert [c.kind for c in syms[0].children] == [lsp.SymbolKind.Method, lsp.SymbolKind.Method]


def test_completion_offers_ids_directives_and_taxa_in_the_right_places(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    from loom_lsp.encoding import Mapper

    node = quilt / "nodes" / "sy-0003.tex"
    text = node.read_text(encoding="utf-8")

    def labels_after(suffix: str) -> set[str]:
        uri = harness.change(node, text + suffix)
        m = Mapper(text + suffix)
        end = lsp.Position(*m.position(len(m.text)))
        return {
            i.label
            for i in complete(
                harness.server,
                lsp.CompletionParams(text_document=lsp.TextDocumentIdentifier(uri=uri), position=end),
            ).items
        }

    ids = labels_after("\n\\ref{")
    assert "sy-0001" in ids and "def:gadget" in ids  # an id and an alias

    assert {"tags", "see", "author"} <= labels_after("\n% !LOOM ")
    assert "theorem" in labels_after("\n\\begin{")
    assert "Kre99" in labels_after("\n\\cite{")


def test_code_actions_offer_the_missing_uses_as_an_edit_and_the_rest_as_commands(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0002.tex"
    uri = harness.open(node)
    diags = [d for d in harness.diagnostics(node) if d.code == "loom:uses-missing"]
    assert diags
    line = diags[0].range.start.line
    got = code_actions(
        harness.server,
        lsp.CodeActionParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            range=lsp.Range(lsp.Position(line, 0), lsp.Position(line, 1)),
            context=lsp.CodeActionContext(diagnostics=diags),
        ),
    )
    titles = [a.title for a in got]
    assert any(t.startswith("Add ") for t in titles)
    add = next(a for a in got if a.title.startswith("Add "))
    assert add.edit is not None
    wanted = diags[0].message.split(" references ", 1)[1].split(" but ", 1)[0]
    edits = next(iter(add.edit.changes.values()))
    assert wanted in edits[0].new_text

    assert any(t.startswith("Accept ") for t in titles)
    accept = next(a for a in got if a.title.startswith("Accept "))
    assert accept.command is not None and accept.command.arguments[0][:2] == ["loom", "accept"]
    assert "--quilt" in accept.command.arguments[0]
    assert accept.command.arguments[1]  # a confirmation string, because this writes


def test_closing_a_buffer_drops_its_overlay(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-0003.tex"
    uri = harness.open(node)
    harness.change(
        node, node.read_text(encoding="utf-8").replace("\\begin{proof}", "\\begin{proof}\n\\ref{sy-7777}", 1)
    )
    assert [d for d in harness.diagnostics(node) if d.code == "dangling-link"]
    did_close(harness.server, lsp.DidCloseTextDocumentParams(text_document=lsp.TextDocumentIdentifier(uri=uri)))
    ws = harness.server.workspace_for(uri)
    assert ws is not None
    harness.server.rescan_now(ws.root)
    assert not [d for d in harness.diagnostics(node) if d.code == "dangling-link"]
