# bunnytrail-cli

Authoring CLI for bunnytrail world-building sites. Installed as `bt`.

## Project layout

```
bunnytrail_cli/
  main.py       — Click CLI entry point (bt add, bt move, bt rename, bt crosslink, bt shell)
  repl.py       — Interactive shell (bt shell) — readline REPL, tab completion, virtual cwd
  helpers.py    — All business logic: move, rename, crosslink, tree, frontmatter, diff rendering
  wikilinks.py  — Wikilink parsing, resolution, index, preferred-form selection
tests/
  conftest.py   — Shared pytest fixture (synthetic project)
  test_crosslink.py
setup.cfg       — Package metadata and deps (click only; readline is stdlib)
```

## How to run

The project is managed with `uv`. Always use `uv run` to invoke things:

```
uv run bt shell          # start the interactive shell
uv run bt add entity ... # one-off commands
uv run pytest            # run tests
```

The live project data lives in `../alteria_world/` (a sibling directory). Run `bt shell` from there:

```
cd ../alteria_world && uv run --project ../bunnytrail-cli bt shell
```

## Content model

A bunnytrail project has this structure:

```
content/                        ← entities and collections
  <namespace>/
    _collection.md              ← marks a collection folder (has `title:` frontmatter)
    <entity-slug>/
      index.md                  ← entity stub (has `name:`, `kind:` frontmatter)
      *.md                      ← other article files
content_meta/
  kinds/                        ← kind taxonomy
    <category>/
      <kind-slug>/
        _kind.md                ← kind definition (has `singular:`, `plural:` frontmatter)
  crosslink.yml                 ← term→entity mappings for crosslink command
```

Entities are identified by their folder path relative to `content/` (e.g. `aurethia/places/sharazan`).
Kinds are identified by their leaf slug (e.g. `planet`), not their full path.

## Shell (repl.py)

- Uses Python's stdlib `readline` for tab completion — behaves exactly like a Unix shell
- `set_completer_delims(" \t\n")` — `/` is not a delimiter, so paths complete as one token
- `_Completer.complete(text, state)` — context-aware: reads the full line buffer to determine which argument position is being completed
- `_complete_fs_path(fragment, base)` — returns full replacement strings (e.g. `aurethia/history/`) for path completion
- Sub-prompts (`_ask`) swap in a temporary completer and restore the main one after
- History persisted to `~/.bt_history`

## Key helpers (helpers.py)

- `content_root(project)` / `kinds_root(project)` — locate the content and kinds directories
- `plan_move` / `execute_move` — move an entity folder and rewrite all wikilink references
- `plan_rename` / `execute_rename` — rename a slug and rewrite references
- `plan_crosslink_folder` / `execute_crosslink` — insert wikilinks into article files
- `write_frontmatter_md(path, frontmatter, body)` — write a `---\n...\n---\n` file
- `is_entity_folder(path)` — True if the folder contains `index.md` with frontmatter
- `format_diff_pair(old, new, indent, color)` — coloured before/after diff lines for the REPL

## Wikilinks (wikilinks.py)

- `[[EntityName]]` or `[[path/to/entity|Display]]` syntax
- `WikilinkIndex` / `build_index` — scan all entity files and build a lookup table
- `resolve(link, context)` — resolve a wikilink to an entity path
- `preferred_form(entity_path, context)` — choose the shortest unambiguous link form

## Coding conventions

- Python 3.9+, `from __future__ import annotations` in every file
- `pathlib.Path` throughout — no `os.path`
- Type hints on all public functions
- No external deps except `click` — stdlib only for everything else
- Tests use pytest with the synthetic fixture in `conftest.py`
