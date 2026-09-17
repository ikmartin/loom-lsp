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
    incoming_calls,
    inlay_hint,
    outgoing_calls,
    prepare_call_hierarchy,
    workspace_symbol,
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

    # every action does something when chosen: an edit, or a command the editor carries out
    assert all(a.edit is not None or a.command is not None for a in got)
    assert {a.command.command for a in got if a.command} <= {"loom.run", "loom.open"}


def _actions_at(harness, path: Path, needle: str) -> list[lsp.CodeAction]:  # type: ignore[no-untyped-def]
    uri = harness.open(path)
    pos = harness.position_of(path, needle)
    return code_actions(
        harness.server,
        lsp.CodeActionParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            range=lsp.Range(pos, pos),
            context=lsp.CodeActionContext(diagnostics=[]),
        ),
    )


def test_open_in_arras_is_a_command_naming_the_statement_even_from_its_proof(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    node = quilt / "nodes" / "sy-000B.tex"
    for needle in ("Every finite widget", "Combine Theorem"):
        opened = [a for a in _actions_at(harness, node, needle) if a.title.startswith("Open ")]
        assert len(opened) == 1, needle
        assert opened[0].command is not None
        assert opened[0].command.command == "loom.open"
        assert opened[0].command.arguments == ["sy-000B"]
        assert opened[0].data is None


def _reshape_actions_at(harness, path: Path, needle: str, kind: str):  # type: ignore[no-untyped-def]
    return [a for a in _actions_at(harness, path, needle) if a.kind == kind]


def test_atomizing_the_node_under_the_cursor_is_one_edit_that_creates_its_file(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main = quilt / "drafts" / "main.tex"
    (action,) = _reshape_actions_at(harness, main, "\\begin{definition}[Widget]", lsp.CodeActionKind.RefactorExtract)
    assert action.title == "Atomize sy-0001 into nodes/sy-0001.tex"
    create, write, replace = action.edit.document_changes
    assert isinstance(create, lsp.CreateFile) and create.uri.endswith("/nodes/sy-0001.tex")
    assert write.text_document.uri == create.uri
    assert write.edits[0].new_text.startswith("\\begin{definition}[Widget]\\label{sy-0001}")
    assert write.edits[0].new_text.endswith("\n")

    text = main.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    rng = replace.edits[0].range
    start = sum(len(x) for x in lines[: rng.start.line]) + rng.start.character
    end = sum(len(x) for x in lines[: rng.end.line]) + rng.end.character
    assert replace.text_document.uri.endswith("/drafts/main.tex")
    assert replace.edits[0].new_text == "\\input{nodes/sy-0001}"
    assert text[start:end] == write.edits[0].new_text.rstrip("\n"), "the region that moves is what the file receives"
    assert text[:start] + "\\input{nodes/sy-0001}" + text[end:] != text


def test_the_client_gets_only_the_kinds_it_asked_for(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main = quilt / "drafts" / "main.tex"
    uri = harness.open(main)
    pos = harness.position_of(main, "\\begin{definition}[Widget]")

    def ask(only: list[str] | None) -> list[str]:
        got = code_actions(
            harness.server,
            lsp.CodeActionParams(
                text_document=lsp.TextDocumentIdentifier(uri=uri),
                range=lsp.Range(pos, pos),
                context=lsp.CodeActionContext(
                    diagnostics=[], only=[lsp.CodeActionKind(k) for k in only] if only else None
                ),
            ),
        )
        return [str(a.kind.value) for a in got]

    everything = ask(None)
    assert len(everything) > 1 and "refactor.extract" in everything
    assert ask(["refactor.extract"]) == ["refactor.extract"]
    assert set(ask(["source"])) == {"source"}
    assert ask(["quickfix"]) == []
    assert "refactor.extract" in ask(["refactor"]), "a kind covers everything under it"


def test_a_node_already_in_its_own_file_or_a_section_is_not_offered(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    assert (
        _reshape_actions_at(
            harness, quilt / "nodes" / "sy-0003.tex", "\\begin{theorem}", lsp.CodeActionKind.RefactorExtract
        )
        == []
    )
    assert (
        _reshape_actions_at(
            harness, quilt / "drafts" / "main.tex", "\\section{Introduction}", lsp.CodeActionKind.RefactorExtract
        )
        == []
    )


def test_a_node_with_no_id_is_offered_one_instead_of_an_atomize(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main = quilt / "drafts" / "main.tex"
    text = main.read_text(encoding="utf-8")
    edited = text.replace("\\end{document}", "\\begin{lemma}\nNo id yet.\n\\end{lemma}\n\\end{document}")
    harness.open(main)
    harness.change(main, edited)
    uri = harness.uri(main)
    line = edited[: edited.index("No id yet.")].count("\n") - 1
    got = code_actions(
        harness.server,
        lsp.CodeActionParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            range=lsp.Range(lsp.Position(line, 0), lsp.Position(line, 1)),
            context=lsp.CodeActionContext(diagnostics=[]),
        ),
    )
    assert [a.title for a in got if a.kind == lsp.CodeActionKind.RefactorExtract] == []
    (label,) = [a for a in got if a.kind == lsp.CodeActionKind.RefactorRewrite]
    assert label.title.startswith("Give this node the id sy-")
    (edit,) = next(iter(label.edit.changes.values()))
    assert edit.new_text.startswith("\\label{sy-") and edit.new_text.endswith("}")
    assert edited.splitlines()[edit.range.start.line].startswith("\\begin{lemma}")


def test_the_plan_reads_the_unsaved_buffer(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main = quilt / "drafts" / "main.tex"
    harness.open(main)
    edited = main.read_text(encoding="utf-8").replace("A \\emph{widget} is", "A \\emph{gadget} is")
    uri = harness.change(main, edited)
    line = edited[: edited.index("\\begin{definition}[Widget]")].count("\n")
    got = code_actions(
        harness.server,
        lsp.CodeActionParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            range=lsp.Range(lsp.Position(line, 0), lsp.Position(line, 1)),
            context=lsp.CodeActionContext(diagnostics=[]),
        ),
    )
    (action,) = [a for a in got if a.kind == lsp.CodeActionKind.RefactorExtract]
    _create, write, _replace = action.edit.document_changes
    assert "gadget" in write.edits[0].new_text, "the node file takes the buffer's text, not the disk's"


def test_workspace_symbols_find_a_node_by_title_at_its_label(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    harness.open(quilt / "drafts" / "main.tex")
    got = workspace_symbol(harness.server, lsp.WorkspaceSymbolParams(query="widget"))
    widget = next(s for s in got if s.name.startswith("sy-0001 "))
    assert "Definition: Widget" in widget.name
    assert isinstance(widget.location, lsp.Location)
    text = (quilt / "drafts" / "main.tex").read_text(encoding="utf-8").splitlines()
    line = widget.location.range.start.line
    rng = widget.location.range
    assert text[line][rng.start.character : rng.end.character] == "\\label{sy-0001}"

    everything = workspace_symbol(harness.server, lsp.WorkspaceSymbolParams(query=""))
    names = {s.name.split()[0] for s in everything}
    assert {"sy-0001", "sy-0003", "sy-000B"} <= names


def _prepare(harness, path: Path, needle: str, extra: int = 0) -> lsp.CallHierarchyItem:  # type: ignore[no-untyped-def]
    uri = harness.open(path)
    items = prepare_call_hierarchy(
        harness.server,
        lsp.CallHierarchyPrepareParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri), position=harness.position_of(path, needle, extra)
        ),
    )
    assert len(items) == 1
    return items[0]


def test_dependencies_are_a_call_hierarchy_in_both_directions(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    main_thm = quilt / "nodes" / "sy-000B.tex"
    item = _prepare(harness, main_thm, "Every finite widget")
    assert item.name == "sy-000B"

    out = outgoing_calls(harness.server, lsp.CallHierarchyOutgoingCallsParams(item=item))
    targets = {c.to.name: c for c in out}
    assert {"sy-0003", "sy-0006"} <= set(targets)
    lines = main_thm.read_text(encoding="utf-8").splitlines()
    sites = [lines[r.start.line][r.start.character : r.end.character] for r in targets["sy-0003"].from_ranges]
    assert "\\ref{sy-0003}" in sites and "\\uses{sy-0003, sy-0006}" in sites

    # from a reference, the hierarchy is prepared on the node it names
    theorem = _prepare(harness, main_thm, "\\ref{sy-0003}", 6)
    assert theorem.name == "sy-0003"
    inc = incoming_calls(harness.server, lsp.CallHierarchyIncomingCallsParams(item=theorem))
    assert "sy-000B" in {c.from_.name for c in inc}
    assert all(c.from_.name != "sy-0003" for c in inc)


def _hints(harness, path: Path) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    uri = harness.open(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    got = inlay_hint(
        harness.server,
        lsp.InlayHintParams(
            text_document=lsp.TextDocumentIdentifier(uri=uri),
            range=lsp.Range(lsp.Position(0, 0), lsp.Position(len(lines), 0)),
        ),
    )
    return [(lines[h.position.line][: h.position.character], str(h.label)) for h in got]


def test_inlay_hints_name_what_references_and_inclusions_point_to(quilt: Path, harness) -> None:  # type: ignore[no-untyped-def]
    hints = _hints(harness, quilt / "nodes" / "sy-000B.tex")
    before_ref = [label for before, label in hints if before.endswith("\\ref{sy-0003}")]
    assert len(before_ref) == 1 and before_ref[0].startswith("Theorem")
    uses = [label for before, label in hints if before.endswith("\\uses{sy-0003, sy-0006}")]
    assert len(uses) == 1 and "; " in uses[0]  # one hint for a command naming two keys

    main = _hints(harness, quilt / "drafts" / "main.tex")
    assert any(before.endswith("\\input{nodes/sy-0002}") for before, _ in main)
    assert not any(before.endswith("\\input{nodes/missing}") for before, _ in main)


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
