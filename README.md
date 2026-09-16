# loom-lsp

A language server for [loom](https://github.com/ikmartin/loom) quilts. It speaks LSP over stdio and is a *client* of loom: it runs no command loom does not already offer, and no loom command requires it.

## What it gives an editor

- **Diagnostics** exactly as `loom lint` reports them, including the ones derived from the review ledger, republished as the buffer changes rather than as it is saved.
- **Go to definition** for `\ref`, `\eqref`, `\cref`, `\uses`, `\cite`, `\input` and `\nest`.
- **Find references**: every `\ref` and `\uses` site that names a key.
- **Hover**: taxon, title, number in the default master, state and why it is stale, open comment counts, and the first lines of the statement.
- **Document symbols**: the file's nodes as a tree, sections containing environments containing proofs.
- **Completion**: ids and aliases with their titles, citekeys from the bibliography, taxa from the preamble closure, `% !LOOM` directive keys, and digest node ids after `\cite[`.
- **Code actions**, each of which shells out to `loom` and confirms first, since these write: add a missing `\uses` entry, insert a node skeleton, atomize the node under the cursor, accept the key under the cursor, open the node in arras.

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
