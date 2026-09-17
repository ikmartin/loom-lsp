# loom-lsp

A language server for [loom](https://github.com/ikmartin/loom) quilts. It speaks LSP over stdio and is a *client* of loom: it runs no command loom does not already offer, and no loom command requires it.

## What it gives an editor

- **Diagnostics** exactly as `loom lint` reports them, including the ones derived from the review ledger, republished as the buffer changes rather than as it is saved.
- **Go to definition** for `\ref`, `\eqref`, `\cref`, `\uses`, `\cite`, `\input` and `\nest`.
- **Find references**: every `\ref` and `\uses` site that names a key.
- **Hover**: taxon, title, number in the default master, state and why it is stale, open comment counts, and the first lines of the statement.
- **Document symbols**: the file's nodes as a tree, sections containing environments containing proofs.
- **Completion**: ids and aliases with their titles, citekeys from the bibliography, taxa from the preamble closure, `% !LOOM` directive keys, and digest node ids after `\cite[`.
- **Workspace symbols**: every key `loom search` finds for the query, named with its taxon and title, so a node can be found by title, alias, tag or id.
- **Call hierarchy** over dependencies: outgoing calls are what a node uses (its statement's and proofs' edges), incoming calls are what uses it, each with the reference sites.
- **Inlay hints**: the taxon, number and title of what each `\ref`, `\uses`, `\input` or `\nest` points to, shown after the command.
- **Reshaping the node under the cursor**, both as workspace edits the editor applies, since loom never edits source: *atomize* moves the node (with the proof it carries) into `nodes/<id>.tex` and leaves an `\input` line behind, in one undoable step; *give this node an id* inserts `\label{<next free id>}`, and is what is offered when a node has no id.
- **Code actions**: add a missing `\uses` entry as an edit; accept the key under the cursor, atomize the file and insert a node skeleton as a `loom.run` command (argument vector, confirmation text); open the node in arras as a `loom.open` command (the statement's key). The editor carries out both commands, confirming first for the ones that write; the server registers no commands of its own, and it knows no server URL, because the editor owns the `loom serve` it opens.

## Installing

```
uv tool install loom-lsp
# or, from a checkout
uv sync && uv run loom-lsp --help
```

The server starts only inside a quilt: it walks up from the file being edited to a `config.toml` with a `[quilt]` table and refuses politely if there is none, so an ordinary `.tex` file is untouched.

## Editors

- Neovim: [loom-nvim](https://github.com/ikmartin/loom-nvim).
- VS Code: [loom-vscode](https://github.com/ikmartin/loom-vscode).
