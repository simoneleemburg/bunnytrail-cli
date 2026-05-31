# bunnytrail-cli

`bt` — authoring CLI for [bunnytrail](https://github.com/simoneleemburg/bunnytrail) sites.

Bunnytrail is a frontmatter-native knowledge-graph engine. This CLI is the
companion tool for authoring and refactoring the content tree: creating
entities, collections, and kinds with the right frontmatter shape; moving and
renaming things while keeping wikilinks intact; and bulk-crosslinking prose
against a target namespace.

## Install

```bash
pipx install git+https://github.com/simoneleemburg/bunnytrail-cli
```

## Usage

Run from anywhere inside a bunnytrail site (a directory containing
`content/` and `STRUCTURE.md`):

```
bt shell                          interactive REPL (recommended)
bt tree [--kinds] [--depth N] [PATH]
bt add entity PATH NAME KIND
bt add collection PATH
bt add kind PATH SINGULAR [PLURAL]
bt move <entity-path> <new-parent>
bt rename <old-path> <new-slug>
bt crosslink <path> <namespace-path>
```

The REPL is the recommended interface: context-aware tab completion over
paths and kinds, persistent history at `~/.bt_history`, a virtual cwd inside
`content/` you can `cd` around, and interactive prompts when arguments are
omitted.

All operations that mutate files print a colorized diff and ask for
confirmation before writing.

## Wikilinks

`bt rename`, `bt move`, and `bt crosslink` all implement the wikilink
contract defined in the bunnytrail engine. See
[bunnytrail/WIKILINKS.md](https://github.com/simoneleemburg/bunnytrail/blob/main/WIKILINKS.md)
for the resolution rules (bare slugs, anchors, language tags, kind links,
collection fold-out, ambiguity, suffix-match resolution).

## Development

```bash
pip install -e .
pytest
```
