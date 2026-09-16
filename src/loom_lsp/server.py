"""The language server: lifecycle, debounced rescans, and the LSP handlers.

Everything the server answers comes from the last good `ScanResult`, which is rescanned on a debounce rather than per keystroke: a full scan is 100 to 170 ms on real papers, comfortable at a quarter-second delay and not comfortable per character.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from loom.records.store import Records
from loom.scan.model import Diagnostic as LoomDiagnostic
from loom.scan.scan import ScanResult
from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from loom_lsp import __version__
from loom_lsp.actions import actions_at, uses_edit
from loom_lsp.analysis import definition, node_at, references, token_at
from loom_lsp.encoding import Mapper
from loom_lsp.info import Symbol, completions, hover, symbols
from loom_lsp.workspace import Workspace, find_quilt, path_of, uri_of

DEBOUNCE_SECONDS = 0.25
LOG = logging.getLogger("loom-lsp")

SEVERITY = {
    "error": lsp.DiagnosticSeverity.Error,
    "warning": lsp.DiagnosticSeverity.Warning,
    "info": lsp.DiagnosticSeverity.Information,
}
DIAGNOSTIC_DOCS = "https://github.com/ikmartin/loom/blob/main/docs/specs/diagnostics.md"

SYMBOL_KIND = {
    "section": lsp.SymbolKind.Namespace,
    "environment": lsp.SymbolKind.Class,
    "proof": lsp.SymbolKind.Method,
}
COMPLETION_KIND = {
    "id": lsp.CompletionItemKind.Reference,
    "citekey": lsp.CompletionItemKind.Constant,
    "taxon": lsp.CompletionItemKind.Class,
    "directive": lsp.CompletionItemKind.Keyword,
    "digest": lsp.CompletionItemKind.Reference,
}


class LoomLanguageServer(LanguageServer):
    """Holds one workspace per quilt root, the open buffers, and the debounce timer."""

    def __init__(self) -> None:
        super().__init__(name="loom-lsp", version=__version__, text_document_sync_kind=lsp.TextDocumentSyncKind.Full)
        self.workspaces: dict[Path, Workspace] = {}
        self.mappers: dict[str, Mapper] = {}
        self.encoding = "utf-16"
        self.loom_bin = "loom"
        self.serve_url = "http://127.0.0.1:8000"
        self._timer: threading.Timer | None = None
        self._pending: set[Path] = set()

    # ---- workspaces ------------------------------------------------------------

    def workspace_for(self, uri: str) -> Workspace | None:
        path = path_of(uri)
        root = find_quilt(path)
        if root is None:
            return None
        ws = self.workspaces.get(root)
        if ws is None:
            ws = Workspace.at(root)
            self.workspaces[root] = ws
        return ws

    def mapper(self, uri: str) -> Mapper:
        return self.mappers.get(uri) or Mapper("", encoding=self.encoding)

    def note(self, uri: str, text: str) -> Workspace | None:
        self.mappers[uri] = Mapper(text, encoding=self.encoding)
        ws = self.workspace_for(uri)
        if ws is not None:
            ws.set_buffer(path_of(uri), text)
        return ws

    # ---- rescanning ------------------------------------------------------------

    def schedule(self, root: Path) -> None:
        self._pending.add(root)
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(DEBOUNCE_SECONDS, self._run_pending)
        self._timer.daemon = True
        self._timer.start()

    def _run_pending(self) -> None:
        roots, self._pending = self._pending, set()
        for root in roots:
            self.rescan_now(root)

    def rescan_now(self, root: Path) -> None:
        ws = self.workspaces.get(root)
        if ws is None:
            return
        snapshot = ws.rescan()
        if snapshot is None:
            if ws.error:
                LOG.warning("scan failed in %s: %s", root, ws.error)
            return
        self.publish(ws, snapshot.result, snapshot.diagnostics)

    def publish(self, ws: Workspace, result: ScanResult, diagnostics: list[LoomDiagnostic]) -> None:
        """One `publishDiagnostics` per file that has any, and an empty list for a file that has stopped having them."""
        by_file: dict[str, list[LoomDiagnostic]] = {}
        for d in diagnostics:
            for loc in d.locations:
                by_file.setdefault(loc.file, []).append(d)
        for rel in set(by_file) | {r for r in result.files}:
            uri = uri_of(ws.root / rel)
            items = [self._to_lsp(ws, rel, d) for d in by_file.get(rel, [])]
            self.text_document_publish_diagnostics(
                lsp.PublishDiagnosticsParams(uri=uri, diagnostics=[i for i in items if i is not None])
            )

    def _to_lsp(self, ws: Workspace, rel: str, d: LoomDiagnostic) -> lsp.Diagnostic | None:
        loc = next((x for x in d.locations if x.file == rel), None)
        if loc is None:
            return None
        mapper = Mapper(ws.text_of(rel), encoding=self.encoding)
        line = max(0, loc.line - 1)
        if loc.column is not None:
            start = mapper.offset(line, 0) + (loc.column - 1)
            s_line, s_char = mapper.position(start)
            e_line, e_char = mapper.position(start + 1)
            rng = lsp.Range(lsp.Position(s_line, s_char), lsp.Position(e_line, max(e_char, s_char + 1)))
        else:
            end_char = len(mapper.text.splitlines()[line]) if line < len(mapper.text.splitlines()) else 0
            _, e_char = mapper.position(mapper.offset(line, 0) + end_char)
            rng = lsp.Range(lsp.Position(line, 0), lsp.Position(line, e_char))
        return lsp.Diagnostic(
            range=rng,
            message=d.message,
            severity=SEVERITY.get(d.severity, lsp.DiagnosticSeverity.Information),
            code=d.code,
            code_description=lsp.CodeDescription(href=DIAGNOSTIC_DOCS),
            source="loom",
        )


server = LoomLanguageServer()


def _context(ls: LoomLanguageServer, uri: str) -> tuple[Workspace, ScanResult, str, Mapper] | None:
    ws = ls.workspace_for(uri)
    if ws is None or ws.snapshot is None:
        return None
    rel = ws.relative(path_of(uri))
    if rel is None:
        return None
    return ws, ws.snapshot.result, rel, Mapper(ws.text_of(rel), encoding=ls.encoding)


@server.feature(lsp.INITIALIZE)
def initialize(ls: LoomLanguageServer, params: lsp.InitializeParams) -> None:
    """Negotiate the position encoding and read the client's settings. UTF-16 is the default and the fallback."""
    caps = params.capabilities.general
    wanted = list(getattr(caps, "position_encodings", None) or []) if caps else []
    ls.encoding = "utf-32" if "utf-32" in wanted else ("utf-8" if "utf-8" in wanted else "utf-16")
    options = params.initialization_options or {}
    if isinstance(options, dict):
        ls.loom_bin = str(options.get("loomPath") or ls.loom_bin)
        ls.serve_url = str(options.get("serveUrl") or ls.serve_url)


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LoomLanguageServer, params: lsp.DidOpenTextDocumentParams) -> None:
    ws = ls.note(params.text_document.uri, params.text_document.text)
    if ws is None:
        return
    ls.rescan_now(ws.root)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LoomLanguageServer, params: lsp.DidChangeTextDocumentParams) -> None:
    ws = ls.note(params.text_document.uri, _whole_text(ls, params))
    if ws is not None:
        ls.schedule(ws.root)


def _whole_text(ls: LoomLanguageServer, params: lsp.DidChangeTextDocumentParams) -> str:
    """The buffer after the change. A full-sync client sends the whole document; an incremental one leaves it to pygls's own store."""
    for change in reversed(params.content_changes or []):
        if getattr(change, "range", None) is None and isinstance(getattr(change, "text", None), str):
            return change.text
    doc = ls.workspace.get_text_document(params.text_document.uri)
    return doc.source


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LoomLanguageServer, params: lsp.DidSaveTextDocumentParams) -> None:
    ws = ls.workspace_for(params.text_document.uri)
    if ws is not None:
        ls.rescan_now(ws.root)


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LoomLanguageServer, params: lsp.DidCloseTextDocumentParams) -> None:
    uri = params.text_document.uri
    ls.mappers.pop(uri, None)
    ws = ls.workspace_for(uri)
    if ws is not None:
        ws.set_buffer(path_of(uri), None)
        ls.schedule(ws.root)


@server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def goto_definition(ls: LoomLanguageServer, params: lsp.DefinitionParams) -> lsp.Location | None:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return None
    ws, result, rel, mapper = ctx
    offset = mapper.offset(params.position.line, params.position.character)
    token = token_at(mapper.text, offset)
    if token is None:
        return None
    target = definition(result, token)
    if target is None:
        return None
    target_map = Mapper(ws.text_of(target.file), encoding=ls.encoding)
    s_line, s_char = target_map.position(target.offset)
    e_line, e_char = target_map.position(target.offset + target.length)
    return lsp.Location(
        uri=uri_of(ws.root / target.file),
        range=lsp.Range(lsp.Position(s_line, s_char), lsp.Position(e_line, e_char)),
    )


@server.feature(lsp.TEXT_DOCUMENT_REFERENCES)
def find_references(ls: LoomLanguageServer, params: lsp.ReferenceParams) -> list[lsp.Location]:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return []
    ws, result, rel, mapper = ctx
    offset = mapper.offset(params.position.line, params.position.character)
    token = token_at(mapper.text, offset)
    key = result.assembly.labels.get(token.value) if token else node_at(result, rel, offset)
    if key is None:
        return []
    out: list[lsp.Location] = []
    for file, at, _from in references(result, key):
        m = Mapper(ws.text_of(file), encoding=ls.encoding)
        line, char = m.position(at)
        out.append(
            lsp.Location(
                uri=uri_of(ws.root / file),
                range=lsp.Range(lsp.Position(line, char), lsp.Position(line, char + 1)),
            )
        )
    return out


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def do_hover(ls: LoomLanguageServer, params: lsp.HoverParams) -> lsp.Hover | None:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return None
    ws, result, rel, mapper = ctx
    offset = mapper.offset(params.position.line, params.position.character)
    token = token_at(mapper.text, offset)
    key = None
    if token is not None and token.kind in ("ref", "see"):
        key = result.assembly.labels.get(token.value)
    if key is None:
        key = node_at(result, rel, offset)
    if key is None:
        return None
    text = hover(result, key, Records(ws.root))
    if not text:
        return None
    return lsp.Hover(contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=text))


@server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbols(ls: LoomLanguageServer, params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol]:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return []
    _ws, result, rel, mapper = ctx

    def convert(sym: Symbol) -> lsp.DocumentSymbol:
        s_line, s_char = mapper.position(sym.start)
        e_line, e_char = mapper.position(sym.end)
        rng = lsp.Range(lsp.Position(s_line, s_char), lsp.Position(e_line, e_char))
        return lsp.DocumentSymbol(
            name=sym.name,
            detail=sym.detail,
            kind=SYMBOL_KIND.get(sym.kind, lsp.SymbolKind.Object),
            range=rng,
            selection_range=rng,
            children=[convert(c) for c in sym.children],
        )

    return [convert(s) for s in symbols(result, rel)]


@server.feature(lsp.TEXT_DOCUMENT_COMPLETION, lsp.CompletionOptions(trigger_characters=["{", ",", "\\", ":"]))
def complete(ls: LoomLanguageServer, params: lsp.CompletionParams) -> lsp.CompletionList:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return lsp.CompletionList(is_incomplete=False, items=[])
    _ws, result, _rel, mapper = ctx
    offset = mapper.offset(params.position.line, params.position.character)
    items = [
        lsp.CompletionItem(
            label=c.label,
            detail=c.detail,
            kind=COMPLETION_KIND.get(c.kind, lsp.CompletionItemKind.Text),
        )
        for c in completions(result, mapper.text, offset)
    ]
    return lsp.CompletionList(is_incomplete=False, items=items)


@server.feature(lsp.TEXT_DOCUMENT_CODE_ACTION)
def code_actions(ls: LoomLanguageServer, params: lsp.CodeActionParams) -> list[lsp.CodeAction]:
    ctx = _context(ls, params.text_document.uri)
    if ctx is None:
        return []
    ws, result, rel, mapper = ctx
    assert ws.snapshot is not None
    offset = mapper.offset(params.range.start.line, params.range.start.character)
    key = node_at(result, rel, offset)
    line = params.range.start.line + 1
    out: list[lsp.CodeAction] = []
    for a in actions_at(result, ws.snapshot.diagnostics, rel, line, key, loom_bin=ls.loom_bin, serve_url=ls.serve_url):
        edit = None
        if a.command == "uses" and a.data.get("key") and a.data.get("label"):
            made = uses_edit(result, a.data["key"], a.data["label"])
            if made is not None:
                file, at, text = made
                m = Mapper(ws.text_of(file), encoding=ls.encoding)
                pos_line, pos_char = m.position(at)
                edit = lsp.WorkspaceEdit(
                    changes={
                        uri_of(ws.root / file): [
                            lsp.TextEdit(
                                range=lsp.Range(lsp.Position(pos_line, pos_char), lsp.Position(pos_line, pos_char)),
                                new_text=text,
                            )
                        ]
                    }
                )
        out.append(
            lsp.CodeAction(
                title=a.title,
                kind=lsp.CodeActionKind(a.kind.replace("quickfix", "quickfix")),
                edit=edit,
                command=None
                if edit is not None or not a.argv
                else lsp.Command(title=a.title, command="loom.run", arguments=[a.argv, a.confirm]),
                data={"url": a.data["url"]} if a.data.get("url") else None,
            )
        )
    return out
