"""
repl.py — interactive shell for the bunnytrail CLI.

Run via `bt shell`.  Provides a prompt_toolkit REPL with:
  - context-aware menu completion (paths, kinds, commands) — Tab opens a menu,
    arrow keys navigate, Enter selects
  - persistent history (~/.bt_history)
  - a virtual cwd inside content/ that you can cd around
  - interactive fallback prompts when params are omitted
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Iterable, Optional, Sequence

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle

from .helpers import (
    content_root,
    execute_crosslink,
    execute_move,
    execute_rename,
    format_diff_pair,
    is_entity_folder,
    iter_entity_md_files,
    iter_kind_md_files,
    kinds_root,
    list_kinds_tree,
    list_tree,
    load_bt_config,
    load_world_config,
    plan_crosslink,
    plan_crosslink_folder,
    plan_move,
    plan_move_kind,
    plan_rename,
    use_color,
    write_frontmatter_md,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HISTORY_FILE = Path.home() / ".bt_history"

TOP_LEVEL_COMMANDS = [
    "hello",
    "ls",
    "tree",
    "cd",
    "add",
    "move",
    "rename",
    "crosslink",
    "help",
    "exit",
    "quit",
]

ADD_SUBCOMMANDS = ["entity", "collection", "kind"]

# ANSI colour helpers
_GREEN  = "\033[1;32m"
_CYAN   = "\033[36m"
_YELLOW = "\033[1;33m"
_RESET  = "\033[0m"


# ---------------------------------------------------------------------------
# Tab key binding: first Tab opens/advances menu, second Tab accepts & closes
# ---------------------------------------------------------------------------

def _make_tab_bindings() -> KeyBindings:
    """
    Custom key bindings:
      Tab  — no menu open: open with first item pre-selected.
             item highlighted: accept it and close the menu.
             menu open, nothing selected: highlight first item.
      Escape — cancel current prompt (same as Ctrl-C).
    """
    kb = KeyBindings()

    @kb.add("tab")
    def _tab(event) -> None:
        buff = event.app.current_buffer
        state = buff.complete_state

        if state is None:
            buff.start_completion(select_first=True)
        elif state.current_completion is not None:
            buff.apply_completion(state.current_completion)
            buff.cancel_completion()
        else:
            buff.complete_next()

    @kb.add("escape")
    def _escape(event) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    return kb

def _child_dirs(path: Path) -> list[str]:
    """Sorted list of child directory names (with trailing /) under *path*."""
    try:
        return sorted(
            p.name + "/" for p in path.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    except (PermissionError, FileNotFoundError):
        return []


def _path_completions(fragment: str, base: Path) -> list[tuple[str, str]]:
    """
    Return (replacement, display) pairs for a partial path fragment under *base*.

    *replacement* is the full path text from the start of the fragment.
    *display* is just the leaf name (what to show in the menu).
    """
    parts = fragment.split("/")
    prefix_parts = parts[:-1]
    partial_name = parts[-1]

    current = base
    for p in prefix_parts:
        current = current / p
        if not current.is_dir():
            return []

    prefix = "/".join(prefix_parts) + "/" if prefix_parts else ""
    out: list[tuple[str, str]] = []
    for name in _child_dirs(current):
        if name.rstrip("/").startswith(partial_name):
            out.append((prefix + name, name))
    return out


def _all_kind_ids(kinds_base: Path) -> list[str]:
    """Return every kind folder name (leaf slug) found under *kinds_base*."""
    return [p.parent.name for p in sorted(kinds_base.rglob("_kind.yaml"))]


def _entity_parent_dirs_by_kind(
    content: Path,
    kind_ids: "str | list[str]",
    cwd: Path,
) -> list[str]:
    """
    Return content-relative parent-directory strings for all existing entities
    whose ``kind:`` field matches any of *kind_ids*, ordered by proximity to *cwd*.

    *kind_ids* may be a single slug string or a list of slugs.  An empty list
    means no filtering — all entity parent dirs are returned.
    """
    from .helpers import frontmatter_lines
    from .helpers import _parse_yaml_field  # type: ignore[attr-defined]

    if isinstance(kind_ids, str):
        kind_ids = [kind_ids]
    kind_set = set(kind_ids)  # empty set → accept all

    seen: set[str] = set()
    results: list[tuple[int, str]] = []

    for index_md in content.rglob("index.md"):
        entity_dir = index_md.parent
        try:
            fm = frontmatter_lines(index_md.read_text(encoding="utf-8"))
        except OSError:
            continue
        entity_kind = _parse_yaml_field(fm, "kind")
        if kind_set and entity_kind not in kind_set:
            continue
        parent = entity_dir.parent
        try:
            rel = str(parent.relative_to(content))
        except ValueError:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        try:
            cwd_rel = cwd.relative_to(content)
            cwd_parts = cwd_rel.parts
        except ValueError:
            cwd_parts = ()
        parent_parts = Path(rel).parts if rel != "." else ()
        shared = sum(1 for a, b in zip(cwd_parts, parent_parts) if a == b)
        distance = len(cwd_parts) + len(parent_parts) - 2 * shared
        results.append((distance, rel))

    results.sort(key=lambda t: t[0])
    return [r for _, r in results]


def _last_token_start(text: str) -> int:
    """
    Return the index in *text* where the last whitespace-delimited token begins.
    If text ends in whitespace, returns len(text).
    """
    # Find last unescaped space
    i = len(text) - 1
    while i >= 0 and text[i] != " " and text[i] != "\t":
        i -= 1
    return i + 1


# ---------------------------------------------------------------------------
# prompt_toolkit completers
# ---------------------------------------------------------------------------

class _PathCompleter(Completer):
    """Completes a path fragment by listing child directories under *base*."""

    def __init__(self, base: Path) -> None:
        self._base = base

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        for replacement, display in _path_completions(text, self._base):
            yield Completion(
                replacement,
                start_position=-len(text),
                display=display,
            )


class _CwdPathCompleter(Completer):
    """
    Completes paths relative to *cwd* by default.
    If the user types a leading '/', switches to completing from *root*
    (stripping the leading slash so the fragment matches root-relative paths).
    """

    def __init__(self, cwd: Path, root: Path) -> None:
        self._cwd = cwd
        self._root = root

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/"):
            fragment = text[1:]  # strip leading /
            for replacement, display in _path_completions(fragment, self._root):
                yield Completion(
                    "/" + replacement,
                    start_position=-len(text),
                    display=display,
                )
        else:
            for replacement, display in _path_completions(text, self._cwd):
                yield Completion(
                    replacement,
                    start_position=-len(text),
                    display=display,
                )


class _MultiPathCompleter(Completer):
    """Completes against multiple base directories (for `rename`)."""

    def __init__(self, bases: Sequence[Path]) -> None:
        self._bases = list(bases)

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        seen: set[str] = set()
        for base in self._bases:
            for replacement, display in _path_completions(text, base):
                if replacement in seen:
                    continue
                seen.add(replacement)
                yield Completion(
                    replacement,
                    start_position=-len(text),
                    display=display,
                )


class _KindSuggestedPathCompleter(Completer):
    """
    Completer for content-path prompts that can be narrowed by kind.

    When the buffer is empty, surfaces suggested parent directories:
      1. Folders containing entities of any of *kind_ids* (closest to *cwd*
         first).  Pass an empty list to skip kind-filtering and show nothing
         in suggestion mode (only typing-mode completion is active).
      2. Any *recent_collections* added this session (content-relative).

    Once the user starts typing, falls back to normal cwd-relative /
    content-root path completion (same behaviour as _CwdPathCompleter).
    """

    def __init__(
        self,
        content: Path,
        cwd: Path,
        kind_ids: "list[str]",
        recent_collections: "Sequence[str]" = (),
    ) -> None:
        self._content = content
        self._cwd = cwd
        self._kind_ids = kind_ids
        self._recent_collections = list(recent_collections)

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor

        if not text.strip():
            # ── Suggestion mode: show ranked candidates ──────────────────
            seen: set[str] = set()

            # 1. Folders with matching-kind entities (or all if kind_ids empty)
            if self._kind_ids:
                for rel in _entity_parent_dirs_by_kind(
                    self._content, self._kind_ids, self._cwd
                ):
                    if rel in seen:
                        continue
                    seen.add(rel)
                    yield Completion(rel + "/", start_position=0, display=rel + "/")

            # 2. Recently-added collections (content-relative paths)
            for rel in self._recent_collections:
                if rel in seen:
                    continue
                seen.add(rel)
                yield Completion(
                    rel + "/",
                    start_position=0,
                    display=rel + "/  [new]",
                )
            return

        # ── Typing mode: normal path completion ──────────────────────────
        if text.startswith("/"):
            fragment = text[1:]
            for replacement, display in _path_completions(fragment, self._content):
                yield Completion(
                    "/" + replacement,
                    start_position=-len(text),
                    display=display,
                )
        else:
            for replacement, display in _path_completions(text, self._cwd):
                yield Completion(
                    replacement,
                    start_position=-len(text),
                    display=display,
                )


class _ShellCompleter(Completer):
    """
    Top-level completer for the main REPL prompt.  Parses the buffer to figure
    out which command and which positional argument is being completed, then
    delegates to path/word completion.
    """

    def __init__(self, project: Path) -> None:
        self._project = project
        self._content = content_root(project)
        self._kinds = kinds_root(project)

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        line = document.text_before_cursor
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()

        trailing_space = line.endswith(" ") or line.endswith("\t")
        if trailing_space:
            tokens.append("")

        if not tokens:
            yield from _yield_words(TOP_LEVEL_COMMANDS, "", 0)
            return

        cmd = tokens[0]

        # top-level command completion
        if len(tokens) == 1 and not trailing_space:
            yield from _yield_words(TOP_LEVEL_COMMANDS, cmd, -len(cmd))
            return

        fragment = tokens[-1] if len(tokens) > 1 else ""
        frag_start = -len(fragment) if fragment else 0

        if cmd == "cd":
            if len(tokens) == 2:
                yield from _yield_paths(fragment, self._content, frag_start)

        elif cmd == "tree":
            if len(tokens) == 2:
                yield from _yield_paths(fragment, self._content, frag_start)

        elif cmd == "add":
            if len(tokens) == 2 and not trailing_space:
                yield from _yield_words(ADD_SUBCOMMANDS, fragment, frag_start)
                return
            if len(tokens) >= 2:
                sub = tokens[1]
                if sub == "entity":
                    # new order: add entity <kind> <slug> <name> <path>
                    if len(tokens) == 3:
                        # arg 1: kind
                        yield from _yield_words(
                            _all_kind_ids(self._kinds), fragment, frag_start
                        )
                    elif len(tokens) == 6:
                        # arg 4: path (after kind, slug, name)
                        yield from _yield_paths(fragment, self._content, frag_start)
                elif sub == "collection":
                    if len(tokens) == 3:
                        yield from _yield_paths(fragment, self._content, frag_start)
                elif sub == "kind":
                    if len(tokens) == 3:
                        yield from _yield_paths(fragment, self._kinds, frag_start)

        elif cmd == "move":
            has_kind_flag = "--kind" in tokens
            base = self._kinds if has_kind_flag else self._content
            pos_tokens = [t for t in tokens[1:] if not t.startswith("-")]
            pos_count = len(pos_tokens) + (1 if trailing_space else 0)
            if pos_count in (1, 2):
                yield from _yield_paths(fragment, base, frag_start)

        elif cmd == "rename":
            if len(tokens) == 2:
                yield from _yield_paths(fragment, self._content, frag_start)
                yield from _yield_paths(fragment, self._kinds, frag_start)

        elif cmd == "crosslink":
            if len(tokens) in (2, 3):
                yield from _yield_paths(fragment, self._content, frag_start)


def _yield_words(
    words: Iterable[str], fragment: str, start_position: int
) -> Iterable[Completion]:
    for w in words:
        if w.startswith(fragment):
            yield Completion(w, start_position=start_position, display=w)


def _yield_paths(
    fragment: str, base: Path, start_position: int
) -> Iterable[Completion]:
    # start_position replaces the whole fragment (incl. any prefix slashes)
    # so we re-emit replacement as-is.
    for replacement, display in _path_completions(fragment, base):
        yield Completion(
            replacement,
            start_position=start_position,
            display=display,
        )


# ---------------------------------------------------------------------------
# Interactive fallback prompt
# ---------------------------------------------------------------------------

def _ask(
    label: str,
    completer: Optional[object] = None,
    complete_from: Optional[Path] = None,
    complete_from_cwd: Optional[tuple[Path, Path]] = None,
    complete_kinds: bool = False,
    default: str = "",
) -> Optional[str]:
    """
    Prompt for a single value with optional completion.

    *complete_from*     — Tab completes paths under this fixed directory.
    *complete_from_cwd* — (cwd, root) pair: completes relative to cwd by
                          default; if the user types a leading '/', completes
                          from root instead.
    *completer*         — list of strings or a Completer instance.
    """
    hint = f" [{default}]" if default else ""

    pt_completer: Optional[Completer] = None
    if complete_from_cwd is not None:
        pt_completer = _CwdPathCompleter(*complete_from_cwd)
    elif complete_from is not None:
        pt_completer = _PathCompleter(complete_from)
    elif isinstance(completer, Completer):
        pt_completer = completer
    elif completer:
        pt_completer = WordCompleter(list(completer), match_middle=False)

    session: PromptSession = PromptSession(
        completer=pt_completer,
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=False,
        key_bindings=_make_tab_bindings(),
    )
    try:
        value = session.prompt(f"  {label}{hint}: ").strip()
        return value if value else (default or None)
    except (EOFError, KeyboardInterrupt):
        print("  (cancelled)")
        return None


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_hello(project: Path) -> None:
    content = content_root(project)
    kinds = kinds_root(project)
    entity_count = sum(1 for _ in iter_entity_md_files(content))
    kind_count = sum(1 for _ in iter_kind_md_files(kinds))
    print(f"Project root : {project}")
    print(f"Entities     : {entity_count}")
    print(f"Kinds        : {kind_count}")


def _cmd_ls(cwd: Path, content: Path) -> None:
    rel = cwd.relative_to(content)
    print(f"  {content.name}/{rel}" if str(rel) != "." else f"  {content.name}/")
    for child in sorted(cwd.iterdir()):
        if child.name.startswith("."):
            continue
        if child.name.startswith("_") and child.name not in ("_collection.md",):
            continue
        if child.is_dir():
            marker = "[E]" if is_entity_folder(child) else "[C]"
            print(f"  {marker} {child.name}/")
        elif child.suffix == ".md":
            print(f"       {child.name}")


def _cmd_info(cwd: Path, content: Path) -> None:
    from .helpers import frontmatter_lines, _parse_yaml_field, is_collection_folder  # type: ignore[attr-defined]

    output: list[str] = []

    def _info_section(folder: Path, depth: int) -> None:
        """Recursively collect output lines for *folder* at heading *depth*."""
        entries: list[tuple[str, str]] = []
        subcollections: list[Path] = []

        for child in sorted(folder.iterdir()):
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            if not child.is_dir():
                continue
            if is_entity_folder(child):
                try:
                    text = (child / "index.md").read_text(encoding="utf-8")
                except OSError:
                    continue
                fm = frontmatter_lines(text)
                name = _parse_yaml_field(fm, "name") or child.name
                summary = _parse_yaml_field(fm, "summary")
                entries.append((name, summary))
            elif is_collection_folder(child):
                subcollections.append(child)

        if entries:
            col_width = max(len(name) for name, _ in entries)
            for name, summary in entries:
                if summary:
                    output.append(f"- {name:<{col_width}}  -  {summary}")
                else:
                    output.append(f"- {name}")

        for coll in subcollections:
            try:
                coll_text = (coll / "_collection.md").read_text(encoding="utf-8")
            except OSError:
                coll_text = ""
            fm = frontmatter_lines(coll_text)
            title = _parse_yaml_field(fm, "title") or coll.name
            description = _parse_yaml_field(fm, "description")
            hashes = "#" * min(depth, 6)
            header = f"{hashes} {title}"
            if description:
                header += f"  -  {description}"
            if output:
                output.append("")
            output.append(header)
            _info_section(coll, depth + 1)

    _info_section(cwd, 1)

    if not output:
        print("(no entities here)")
        return

    print("\n".join(output))


def _cmd_edit(project: Path, cwd: Path, content: Path, args: list[str]) -> None:
    from .helpers import (  # type: ignore[attr-defined]
        frontmatter_lines,
        _parse_yaml_field,
        is_collection_folder,
        split_frontmatter,
    )

    # ------------------------------------------------------------------ helpers

    def _resolve(raw: str) -> Path:
        raw = raw.rstrip("/")
        if raw.startswith("/"):
            return (content / raw.lstrip("/")).resolve()
        return (cwd / raw).resolve()

    def _read_entity(md_file: Path) -> tuple[list[str], str] | None:
        """Return (fm_lines, body) or None on error."""
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"  cannot read {md_file}: {exc}")
            return None
        fm_str, body = split_frontmatter(text)
        return (fm_str or "").splitlines(), body

    def _write_entity(
        md_file: Path,
        name: str,
        kind_id: str,
        entity_class: str,
        summary: str,
        prop_values: dict[str, str],
        relation_entries: list[dict[str, str]],
        body: str,
    ) -> None:
        fm = f"name: {name}\nkind: {kind_id}"
        if entity_class:
            fm += f"\nclass: {entity_class.strip('/')}"
        if summary:
            fm += f"\nsummary: {summary}"
        for prop_slug, prop_val in prop_values.items():
            if any(c in prop_val for c in (':', '#', '[', ']', '{', '}', '&', '*', '!', '|', '>', "'", '"')):
                fm += f'\n{prop_slug}: "{prop_val}"'
            else:
                fm += f"\n{prop_slug}: {prop_val}"
        if relation_entries:
            fm += "\nrelations:"
            for entry in relation_entries:
                fm += f"\n  - kind: {entry['kind']}"
                fm += f"\n    target: {entry['target']}"
        write_frontmatter_md(md_file, fm, body)

    def _prompt_single_field(
        field: str,
        md_file: Path,
        kind_id: str,
        fm_lines: list[str],
        body: str,
    ) -> bool:
        """Prompt for one field, patch it in place, write, return False on cancel."""
        kinds = kinds_root(project)
        bt_cfg = load_bt_config(project)
        world_cfg = load_world_config(project)
        get = lambda f: _parse_yaml_field(fm_lines, f)

        # Re-read all existing values so we can write a complete frontmatter
        name = get("name")
        kind_val = get("kind") or kind_id
        summary = get("summary")
        entity_class = get("class")
        prop_values: dict[str, str] = {
            p.slug: v for p in world_cfg.applicable_properties(kind_id)
            if (v := get(p.slug))
        }
        # We preserve existing relation_entries as-is when editing a single field
        # (raw re-parse isn't trivial; relations stay unchanged unless field=="relations")
        relation_entries: list[dict[str, str]] = []
        # simple re-parse of relations block
        in_rel = False
        cur: dict[str, str] = {}
        for line in fm_lines:
            s = line.strip()
            if s == "relations:":
                in_rel = True
                continue
            if in_rel:
                if s.startswith("- kind:"):
                    if cur:
                        relation_entries.append(cur)
                    cur = {"kind": s[len("- kind:"):].strip()}
                elif s.startswith("kind:"):
                    cur["kind"] = s[len("kind:"):].strip()
                elif s.startswith("target:"):
                    cur["target"] = s[len("target:"):].strip()
                elif s and not s.startswith("-"):
                    in_rel = False
        if cur:
            relation_entries.append(cur)

        if field == "name":
            val = _ask("name", default=name)
            if val is None:
                return False
            name = val
        elif field == "kind":
            kind_ids = _all_kind_ids(kinds)
            val = _ask("kind", completer=kind_ids, default=kind_val)  # type: ignore[arg-type]
            if val is None:
                return False
            kind_val = val.split("/")[-1]
            kind_id = kind_val
        elif field == "summary":
            val = _ask("summary (optional)", default=summary)
            if val is None:
                return False
            summary = val
        elif field == "class":
            val = _ask("class", complete_from_cwd=(cwd, content), default=entity_class)
            if val is None:
                return False
            entity_class = val
        else:
            # world property
            prop_def = next((p for p in world_cfg.applicable_properties(kind_id) if p.slug == field), None)
            if prop_def is None:
                print(f"  unknown field: {field!r}")
                return False
            hint = f"  [{', '.join(prop_def.values[:4])}{'…' if len(prop_def.values) > 4 else ''}]" if prop_def.values else ""
            val = _ask(f"{field} (optional){hint}", completer=prop_def.values if prop_def.values else None, default=get(field))  # type: ignore[arg-type]
            if val is None:
                return False
            if val:
                prop_values[field] = val
            else:
                prop_values.pop(field, None)

        _write_entity(md_file, name, kind_id, entity_class, summary, prop_values, relation_entries, body)
        return True

    # ---------------------------------------------------------------- resolve path

    if args:
        target = _resolve(args[0])
        path_label = args[0].rstrip("/")
    else:
        raw = _ask("path", complete_from_cwd=(cwd, content))
        if not raw:
            return
        target = _resolve(raw)
        path_label = raw.rstrip("/")

    if not target.is_dir():
        print(f"  not a directory: {path_label}")
        return

    kinds = kinds_root(project)
    bt_cfg = load_bt_config(project)
    world_cfg = load_world_config(project)

    # ----------------------------------------------------------- determine scope

    # Collect entities to edit: either just the one target or all entities under a collection
    entity_targets: list[Path] = []
    collection_target: Path | None = None

    if is_entity_folder(target):
        entity_targets = [target]
    elif is_collection_folder(target):
        collection_target = target
        for child in sorted(target.rglob("index.md")):
            if is_entity_folder(child.parent):
                entity_targets.append(child.parent)
    else:
        print(f"  not an entity or collection folder: {path_label}")
        return

    # -------------------------------------------------- build field completions

    # Determine applicable fields from the first entity (or collection itself)
    if collection_target and not entity_targets:
        # Collection-only edit (title/description)
        field_choices = ["all", "title", "description"]
    else:
        # Sample the first entity's kind for property completions
        sample_kind = ""
        if entity_targets:
            result = _read_entity(entity_targets[0] / "index.md")
            if result:
                sample_kind = _parse_yaml_field(result[0], "kind")
        base_fields = ["all", "name", "kind", "summary", "class"]
        prop_fields = [p.slug for p in world_cfg.applicable_properties(sample_kind)]
        field_choices = base_fields + prop_fields

    # --------------------------------------------------------- ask what to edit

    field = _ask("edit what?", completer=field_choices, default="all")  # type: ignore[arg-type]
    if not field:
        return

    # ============================================================ COLLECTION edit (title/description)

    if collection_target and field in ("all", "title", "description"):
        md_file = collection_target / "_collection.md"
        result = _read_entity(md_file)  # reuse reader; same structure
        if result is None:
            return
        fm_lines, body = result
        get = lambda f: _parse_yaml_field(fm_lines, f)

        if field in ("all", "title"):
            title = _ask("title", default=get("title"))
            if title is None:
                return
        else:
            title = get("title")

        if field in ("all", "description"):
            description = _ask("description (optional)", default=get("description"))
            if description is None:
                return
        else:
            description = get("description")

        fm = f"title: {title}"
        if description:
            fm += f"\ndescription: {description}"
        write_frontmatter_md(md_file, fm, body)
        print(f"  updated {md_file.relative_to(project)}")

        if not entity_targets:
            return
        # fall through to edit entities under the collection too (only for "all")
        if field != "all":
            return

    # ============================================================ ENTITY edit

    for ent_dir in entity_targets:
        md_file = ent_dir / "index.md"
        result = _read_entity(md_file)
        if result is None:
            continue
        fm_lines, body = result
        get = lambda f, _fl=fm_lines: _parse_yaml_field(_fl, f)
        kind_id = get("kind")

        if len(entity_targets) > 1:
            print(f"\n  {get('name') or ent_dir.name}")

        if field == "all":
            # Full interactive edit — same as before
            name = _ask("name", default=get("name"))
            if name is None:
                return

            kind_ids = _all_kind_ids(kinds)
            kind = _ask("kind", completer=kind_ids, default=kind_id)  # type: ignore[arg-type]
            if kind is None:
                return
            kind_id = kind.split("/")[-1]

            summary = _ask("summary (optional)", default=get("summary"))
            if summary is None:
                return

            entity_class: str = get("class")
            old_class = get("class")
            if old_class or kind_id in bt_cfg.add.class_for_kinds:
                val = _ask("class", complete_from_cwd=(cwd, content), default=old_class)
                if val is None:
                    return
                entity_class = val

            prop_values: dict[str, str] = {}
            for prop in world_cfg.applicable_properties(kind_id):
                old_val = get(prop.slug)
                hint = f"  [{', '.join(prop.values[:4])}{'…' if len(prop.values) > 4 else ''}]" if prop.values else ""
                label = f"{prop.slug} (optional){hint}"
                val = _ask(label, completer=prop.values if prop.values else None, default=old_val)  # type: ignore[arg-type]
                if val is None:
                    return
                if val:
                    prop_values[prop.slug] = val

            applicable_rels = world_cfg.applicable_relations(kind_id)
            applicable_rel_slugs = [r.slug for r in applicable_rels]
            rel_by_slug = {r.slug: r for r in applicable_rels}
            relation_entries: list[dict[str, str]] = []
            if applicable_rel_slugs:
                old_rels = get("relations")
                if old_rels:
                    print(f"  current relations: {old_rels}")
                print("  relations — re-enter all (empty kind to finish):")
                while True:
                    rel_kind = _ask("  rel kind", completer=applicable_rel_slugs)  # type: ignore[arg-type]
                    if not rel_kind:
                        break
                    rel_def = rel_by_slug.get(rel_kind)
                    codomain = rel_def.codomain if rel_def else []
                    if codomain:
                        tc = _KindSuggestedPathCompleter(content, cwd, codomain)
                        rel_target = _ask("  rel target", completer=tc)
                    else:
                        rel_target = _ask("  rel target", complete_from_cwd=(cwd, content))
                    if not rel_target:
                        break
                    relation_entries.append({"kind": rel_kind, "target": rel_target.strip("/")})

            _write_entity(md_file, name, kind_id, entity_class, summary, prop_values, relation_entries, body)
            print(f"  updated {md_file.relative_to(project)}")

        else:
            # Single-field edit
            ok = _prompt_single_field(field, md_file, kind_id, fm_lines, body)
            if not ok:
                return
            print(f"  updated {md_file.relative_to(project)}")



def _cmd_check(project: Path, cwd: Path, content: Path, args: list[str]) -> None:
    from .helpers import (  # type: ignore[attr-defined]
        frontmatter_lines,
        _parse_yaml_field,
        is_collection_folder,
    )

    _ENTITY_BUILTINS = ["name", "kind", "summary", "class"]

    world_cfg = load_world_config(project)

    # --------------------------------------------------------- ask what to check
    # Offer builtins + all world properties as completions up front.
    all_world_slugs = list({p.slug for p in world_cfg.applicable_properties("")})
    field_choices = _ENTITY_BUILTINS + [s for s in all_world_slugs if s not in _ENTITY_BUILTINS]
    field = _ask("check what?", completer=field_choices)  # type: ignore[arg-type]
    if not field:
        return

    # ---------------------------------------------------------------- resolve path
    if args:
        raw = args[0].rstrip("/")
        target = ((cwd / raw) if not raw.startswith("/") else (content / raw.lstrip("/"))).resolve()
    else:
        raw = _ask("path", complete_from_cwd=(cwd, content))
        if not raw:
            return
        raw = raw.rstrip("/")
        target = ((cwd / raw) if not raw.startswith("/") else (content / raw.lstrip("/"))).resolve()

    if not target.is_dir():
        print(f"  not a directory: {raw}")
        return

    # Collect entities under target (or just target itself if it's an entity)
    entity_dirs: list[Path] = []
    if is_entity_folder(target):
        entity_dirs = [target]
    elif is_collection_folder(target) or target == content:
        for md_file in sorted(target.rglob("index.md")):
            if is_entity_folder(md_file.parent):
                entity_dirs.append(md_file.parent)
    else:
        print(f"  not an entity or collection folder: {raw}")
        return

    if not entity_dirs:
        print("  (no entities found)")
        return

    # --------------------------------------------------------- scan and report
    missing: list[str] = []
    skipped: list[str] = []

    for ent_dir in entity_dirs:
        try:
            text = (ent_dir / "index.md").read_text(encoding="utf-8")
        except OSError:
            continue
        fm = frontmatter_lines(text)
        kind_id = _parse_yaml_field(fm, "kind")
        is_builtin = field in _ENTITY_BUILTINS
        applicable = {p.slug for p in world_cfg.applicable_properties(kind_id)}
        if not is_builtin and field not in applicable:
            skipped.append(ent_dir.name)
            continue
        if not _parse_yaml_field(fm, field):
            name = _parse_yaml_field(fm, "name") or ent_dir.name
            rel = ent_dir.relative_to(content)
            missing.append(f"- {name}  ({rel})")

    if missing:
        print(f"\n  missing {field!r}:")
        print("\n".join(f"  {line}" for line in missing))
    else:
        print(f"  all entities have {field!r}")

    if skipped:
        print(f"  ({len(skipped)} entities skipped — {field!r} not applicable to their kind)")
        print(f"  ({len(skipped)} entities skipped — {field!r} not applicable to their kind)")


def _cmd_tree(cwd: Path, content: Path, args: list[str]) -> None:
    base = cwd
    depth = 3
    if args:
        candidate = cwd / args[0]
        if candidate.is_dir():
            base = candidate
        else:
            print(f"  not a directory: {args[0]}")
            return
    lines = list_tree(base, max_depth=depth)
    rel = base.relative_to(content)
    print(f"  content/{rel}" if str(rel) != "." else "  content/")
    print("\n".join(f"  {l}" for l in lines) if lines else "  (empty)")


def _cmd_cd(cwd: Path, content: Path, args: list[str]) -> Path:
    if not args or args[0] in ("", "/"):
        return content
    target_str = args[0].rstrip("/")
    if target_str == "..":
        if cwd != content:
            return cwd.parent
        return cwd
    if (cwd / target_str).is_dir():
        candidate = cwd / target_str
    elif (content / target_str).is_dir():
        candidate = content / target_str
    else:
        print(f"  no such directory: {target_str}")
        return cwd
    return candidate.resolve()


def _slug_to_title(slug: str) -> str:
    """Convert a slug like 'solar-system' or 'dark_forest' to 'Solar System'."""
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w)


def _cmd_add(
    project: Path,
    cwd: Path,
    args: list[str],
    recent_collections: "list[str] | None" = None,
) -> "str | None":
    """
    Handle the ``add`` command.

    Returns the content-relative path of a newly created *collection* so the
    caller can add it to the session's ``recent_collections`` list.  Returns
    ``None`` for entities, kinds, or on failure/cancellation.
    """
    content = content_root(project)
    kinds = kinds_root(project)
    if recent_collections is None:
        recent_collections = []

    if not args:
        sub = _ask("add what?", completer=ADD_SUBCOMMANDS)  # type: ignore[arg-type]
        if not sub:
            return None
    else:
        sub = args[0]

    if sub not in ADD_SUBCOMMANDS:
        print(f"  unknown add subcommand: {sub!r}")
        print("  usage: add entity|collection|kind ...")
        return None

    if sub == "entity":
        # New argument order: kind slug name path
        # (kind first so path suggestions can be kind-aware)
        kind_arg = args[1] if len(args) > 1 else None
        slug     = args[2] if len(args) > 2 else None
        name     = args[3] if len(args) > 3 else None
        path     = args[4] if len(args) > 4 else None

        # 1. Ask kind first
        if kind_arg is None:
            kind_ids = _all_kind_ids(kinds)
            kind_arg = _ask("kind", completer=kind_ids)  # type: ignore[arg-type]
            if not kind_arg:
                return None
        kind_id = kind_arg.split("/")[-1]

        # 2. Ask path, with kind-aware suggestions
        if path is None:
            suggested = _entity_parent_dirs_by_kind(content, [kind_id], cwd)
            if suggested or recent_collections:
                hint_parts = []
                if suggested:
                    hint_parts.append(f"{suggested[0]}/")
                if recent_collections:
                    hint_parts.append(f"{recent_collections[0]}/  [new]")
                print(
                    f"  (Tab to see suggested locations for '{kind_id}'"
                    + (f"; top: {', '.join(hint_parts[:2])}" if hint_parts else "")
                    + ")"
                )
            path_completer = _KindSuggestedPathCompleter(
                content, cwd, [kind_id], recent_collections
            )
            path = _ask("path", completer=path_completer, default=".")
            if path is None:
                return None

        if slug is None:
            slug = _ask("slug")
            if not slug:
                return None

        if name is None:
            name = _ask("name", default=_slug_to_title(slug))
            if not name:
                return None

        summary = _ask("summary (optional)")  # empty → omitted

        # Load world schema and bt config once for all prompts below
        bt_cfg = load_bt_config(project)
        world_cfg = load_world_config(project)

        # Ask for class: if this kind is configured to require one
        entity_class: "str | None" = None
        if kind_id in bt_cfg.add.class_for_kinds:
            entity_class = _ask(
                "class",
                complete_from_cwd=(cwd, content),
            )
            # empty answer → skip the field silently

        # Ask for applicable properties (those unrestricted or matching kind)
        prop_values: "dict[str, str]" = {}
        for prop in world_cfg.applicable_properties(kind_id):
            hint = f"  [{', '.join(prop.values[:4])}{'…' if len(prop.values) > 4 else ''}]" if prop.values else ""
            label = f"{prop.slug} (optional){hint}"
            val = _ask(label, completer=prop.values if prop.values else None)  # type: ignore[arg-type]
            if val:
                prop_values[prop.slug] = val

        # Ask for relations (repeating until the user enters nothing for kind)
        applicable_rels = world_cfg.applicable_relations(kind_id)
        applicable_rel_slugs = [r.slug for r in applicable_rels]
        rel_by_slug = {r.slug: r for r in applicable_rels}
        relation_entries: "list[dict[str, str]]" = []
        if applicable_rel_slugs:
            print("  relations — enter a relation kind and target (empty kind to finish):")
            while True:
                rel_kind = _ask("  rel kind", completer=applicable_rel_slugs)  # type: ignore[arg-type]
                if not rel_kind:
                    break
                # Narrow target suggestions by the relation's codomain (if any)
                rel_def = rel_by_slug.get(rel_kind)
                codomain = rel_def.codomain if rel_def else []
                if codomain:
                    target_completer = _KindSuggestedPathCompleter(
                        content, cwd, codomain
                    )
                    print(
                        f"  (Tab to see targets for '{rel_kind}'"
                        f"; kinds: {', '.join(codomain)})"
                    )
                    rel_target = _ask("  rel target", completer=target_completer)
                else:
                    rel_target = _ask("  rel target", complete_from_cwd=(cwd, content))
                if not rel_target:
                    break
                rel_target = rel_target.strip("/")
                relation_entries.append({"kind": rel_kind, "target": rel_target})

        # Resolve path: leading '/' means relative to content root, else relative to cwd
        path = path.rstrip("/")
        if path.startswith("/"):
            base_dir = content / path.lstrip("/")
        elif path in ("", "."):
            base_dir = cwd
        else:
            # Could be content-relative (from a suggestion) or cwd-relative
            content_abs = (content / path).resolve()
            cwd_abs = (cwd / path).resolve()
            if content_abs.exists() or not cwd_abs.exists():
                # Prefer content-relative when it resolves or neither resolves
                base_dir = content_abs
            else:
                base_dir = cwd_abs
        entity_dir = base_dir / slug.rstrip("/")
        md_file = entity_dir / "index.md"
        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return None

        if not any(kinds.rglob(kind_id)):
            print(f"  warning: no kind '{kind_id}' found under content_meta/kinds/")

        entity_dir.mkdir(parents=True, exist_ok=True)
        fm = f"name: {name}\nkind: {kind_id}"
        if entity_class:
            entity_class = entity_class.strip("/")
            fm += f"\nclass: {entity_class}"
        if summary:
            fm += f"\nsummary: {summary}"
        for prop_slug, prop_val in prop_values.items():
            # Quote values that contain special YAML characters
            if any(c in prop_val for c in (':',  '#', '[', ']', '{', '}', '&', '*', '!', '|', '>', "'", '"')):
                fm += f'\n{prop_slug}: "{prop_val}"'
            else:
                fm += f"\n{prop_slug}: {prop_val}"
        if relation_entries:
            fm += "\nrelations:"
            for entry in relation_entries:
                fm += f"\n  - kind: {entry['kind']}"
                fm += f"\n    target: {entry['target']}"
        write_frontmatter_md(md_file, fm)
        print(f"  created {md_file.relative_to(project)}")
        return None

    if sub == "collection":
        slug  = args[1] if len(args) > 1 else None
        title = " ".join(args[2:]) if len(args) > 2 else None

        if slug is None:
            slug = _ask("slug")
            if not slug:
                return None

        if title is None:
            title = _ask("title", default=_slug_to_title(slug))
            if not title:
                return None

        description = _ask("description (optional)")  # empty → omitted

        coll_dir = cwd / slug.rstrip("/")
        md_file = coll_dir / "_collection.md"
        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return None
        coll_dir.mkdir(parents=True, exist_ok=True)
        fm = f"title: {title}"
        if description:
            fm += f"\ndescription: {description}"
        write_frontmatter_md(md_file, fm)
        print(f"  created {md_file.relative_to(project)}")
        # Return the content-relative path so the REPL can track it
        try:
            return str(coll_dir.relative_to(content))
        except ValueError:
            return None

    if sub == "kind":
        # path is relative to kinds root, e.g. "celestial/planet"
        path     = args[1] if len(args) > 1 else None
        singular = args[2] if len(args) > 2 else None
        plural   = args[3] if len(args) > 3 else None

        if path is None:
            path = _ask("kinds path", complete_from=kinds)
            if path is None:
                return

        leaf = path.rstrip("/").split("/")[-1]

        if singular is None:
            singular = _ask("singular", default=_slug_to_title(leaf))
            if not singular:
                return

        if plural is None:
            plural = _ask("plural", default=f"{singular}s")
            if not plural:
                plural = f"{singular}s"

        kind_dir = kinds / path.rstrip("/")
        md_file = kind_dir / "_kind.yaml"
        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return
        kind_dir.mkdir(parents=True, exist_ok=True)
        md_file.write_text(f"singular: {singular}\nplural: {plural}\n", encoding="utf-8")
        print(f"  created {md_file.relative_to(project)}")
        return


def _cmd_move(project: Path, cwd: Path, content: Path, args: list[str]) -> None:
    kinds = kinds_root(project)

    is_kind = "--kind" in args
    args = [a for a in args if a != "--kind"]

    entity_path = args[0] if len(args) > 0 else None
    new_parent = args[1] if len(args) > 1 else None

    base = kinds if is_kind else content
    base_label = "kinds path" if is_kind else "entity path"
    parent_label = "new kinds parent" if is_kind else "new parent"

    if entity_path is None:
        try:
            default_path = str(cwd.relative_to(content))
        except ValueError:
            default_path = ""
        entity_path = _ask(base_label, complete_from=base, default=default_path)
        if not entity_path:
            return

    if new_parent is None:
        new_parent = _ask(parent_label, complete_from=base)
        if not new_parent:
            return

    def _resolve_content(p: str) -> str:
        p = p.rstrip("/")
        if (cwd / p).is_dir():
            return str((cwd / p).relative_to(content))
        return p

    def _resolve_kinds(p: str) -> str:
        p = p.rstrip("/")
        if (base / p).is_dir():
            return str((base / p).relative_to(kinds))
        return p

    if is_kind:
        entity_path = _resolve_kinds(entity_path)
        new_parent = _resolve_kinds(new_parent)
        plan = plan_move_kind(project, entity_path, new_parent)
        id_root = "content_meta/kinds"
    else:
        entity_path = _resolve_content(entity_path)
        new_parent = _resolve_content(new_parent)
        plan = plan_move(project, entity_path, new_parent)
        id_root = "content"

    if plan.error:
        print(f"  Error: {plan.error}")
        return

    print(f"  Move:  {id_root}/{plan.old_id}")
    print(f"    ->  {id_root}/{plan.new_id}")

    if plan.refs:
        print(f"\n  References to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(project)
            print(f"    {rel}:{ref.line_no}")
            old_line, new_line = format_diff_pair(
                ref.old_text, ref.new_text,
                indent="      ", color=use_color(),
            )
            print(old_line)
            print(new_line)
    else:
        if is_kind:
            print("  No reference rewrites needed (kinds are referenced by slug).")
        else:
            print("  No references found — only the folder will move.")

    if getattr(plan, "warnings", None):
        print(f"\n  Warnings ({len(plan.warnings)}):")
        for w in plan.warnings:
            print(f"    ! {w}")

    _print_collisions(plan)

    confirm = _ask("\n  Proceed? [y/N]")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    try:
        execute_move(plan)
        print(f"  Done.  Moved to {id_root}/{plan.new_id}")
    except Exception as exc:
        print(f"  Error during move: {exc}")


def _cmd_rename(project: Path, cwd: Path, content: Path, args: list[str]) -> None:
    kinds = kinds_root(project)
    old_path = args[0] if len(args) > 0 else None
    new_slug = args[1] if len(args) > 1 else None

    if old_path is None:
        try:
            default_path = str(cwd.relative_to(content))
        except ValueError:
            default_path = ""

        old_path = _ask(
            "entity or kind path",
            completer=_MultiPathCompleter([content, kinds]),
            default=default_path,
        )
        if not old_path:
            return

    if new_slug is None:
        new_slug = _ask("new slug")
        if not new_slug:
            return

    def _resolve(p: str) -> str:
        p = p.rstrip("/")
        if (cwd / p).is_dir():
            candidate = cwd / p
            if candidate.is_relative_to(content):
                return str(candidate.relative_to(content))
            if candidate.is_relative_to(kinds):
                return str(candidate.relative_to(kinds))
        return p

    old_path = _resolve(old_path)

    from .helpers import (
        read_entity_display_name,
        read_kind_display_names,
    )
    new_display: dict[str, str] = {}
    entity_dir = content / old_path
    if entity_dir.is_dir() and (entity_dir / "index.md").is_file():
        old_name = read_entity_display_name(entity_dir)
        if old_name:
            answer = _ask("new display name", default=old_name)
            if answer and answer != old_name:
                new_display["name"] = answer
    else:
        kind_dir = kinds / old_path
        if kind_dir.is_dir() and (kind_dir / "_kind.yaml").is_file():
            old_singular, old_plural = read_kind_display_names(kind_dir)
            if old_singular:
                answer = _ask("new singular", default=old_singular)
                if answer and answer != old_singular:
                    new_display["singular"] = answer
            if old_plural:
                answer = _ask("new plural", default=old_plural)
                if answer and answer != old_plural:
                    new_display["plural"] = answer

    plan = plan_rename(
        project, old_path, new_slug,
        new_display_names=new_display or None,
    )

    if plan.error:
        print(f"  Error: {plan.error}")
        return

    kind_label = "kind" if plan.is_kind else "entity"
    id_root = "content_meta/kinds" if plan.is_kind else "content"
    print(f"  Rename {kind_label}:  {id_root}/{plan.old_id}")
    print(f"                ->  {id_root}/{plan.new_id}")

    if plan.display_renames:
        print("\n  Display name changes:")
        for old_disp, new_disp in plan.display_renames.items():
            print(f"    {old_disp!r} -> {new_disp!r}")

    if plan.refs:
        print(f"\n  References to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(project)
            print(f"    {rel}:{ref.line_no}")
            old_line, new_line = format_diff_pair(
                ref.old_text, ref.new_text,
                indent="      ", color=use_color(),
            )
            print(old_line)
            print(new_line)
    else:
        print("  No references found — only the folder will be renamed.")

    if getattr(plan, "warnings", None):
        print(f"\n  Warnings ({len(plan.warnings)}):")
        for w in plan.warnings:
            print(f"    ! {w}")

    _print_collisions(plan)

    confirm = _ask("\n  Proceed? [y/N]")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    try:
        execute_rename(plan)
        print(f"  Done.  Renamed to {id_root}/{plan.new_id}")
    except Exception as exc:
        print(f"  Error during rename: {exc}")


def _cmd_crosslink(project: Path, cwd: Path, content: Path, args: list[str]) -> None:
    article_path = args[0] if len(args) > 0 else None
    namespace_path = args[1] if len(args) > 1 else None

    if article_path is None:
        try:
            default_path = str(cwd.relative_to(content))
        except ValueError:
            default_path = ""
        article_path = _ask("article path", complete_from=content, default=default_path)
        if not article_path:
            return

    if namespace_path is None:
        namespace_path = _ask("namespace path", complete_from=content)
        if not namespace_path:
            return

    guides_dir = (project / "content_meta" / "guides").resolve()

    def _resolve(p: str) -> str:
        p = p.rstrip("/")
        cand = (cwd / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if cand.is_dir():
            if cand == guides_dir or guides_dir in cand.parents:
                rel = cand.relative_to(guides_dir)
                return "guides" if str(rel) == "." else f"guides/{rel}"
            try:
                return str(cand.relative_to(content))
            except ValueError:
                pass
        return p

    article_path = _resolve(article_path)
    namespace_path = _resolve(namespace_path)

    plans, batch_error = plan_crosslink_folder(project, article_path, namespace_path)

    if batch_error:
        print(f"  Error: {batch_error}")
        return

    if article_path == "guides" or article_path.startswith("guides/"):
        target_display = f"content_meta/{article_path}"
    else:
        target_display = f"content/{article_path}"
    print(f"  Target:    {target_display}")
    print(f"  Namespace: content/{namespace_path}")
    print(f"  Articles:  {len(plans)}")

    actionable = [p for p in plans if p.edits and not p.error]
    erroring = [p for p in plans if p.error]

    if erroring:
        print(f"\n  Skipped ({len(erroring)}):")
        for p in erroring:
            print(f"    ! {p.article_id}: {p.error}")

    if not actionable:
        print("  No matches found — nothing to link.")
        return

    total_edits = sum(len(p.edits) for p in actionable)
    print(
        f"\n  Proposed wikilinks across {len(actionable)} article(s), "
        f"{total_edits} line(s) affected:"
    )
    for p in actionable:
        print(f"\n    content/{p.article_id}/{p.md_file.name}")
        for edit in p.edits:
            print(f"      line {edit.line_no}:")
            old_line, new_line = format_diff_pair(
                edit.old_text, edit.new_text,
                indent="        ", color=use_color(),
            )
            print(old_line)
            print(new_line)

    confirm = _ask("\n  Proceed? [y/N]")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    for p in actionable:
        try:
            execute_crosslink(p)
        except Exception as exc:
            print(f"  Error during crosslink for {p.article_id}: {exc}")
            return

    print(f"  Done.  Updated {len(actionable)} article(s).")
    _print_crosslink_warnings(actionable)


def _print_collisions(plan) -> None:
    peers = getattr(plan, "collisions", None) or []
    if not peers:
        return
    n = len(peers)
    print(
        f"\n  Name clash: the new leaf slug is already used by "
        f"{n} existing entit{'y' if n == 1 else 'ies'}:"
    )
    for peer in peers:
        print(f"    ! content/{peer}")
    print(
        "    Existing bare links to those entities will be rewritten "
        "to a longer disambiguating form."
    )


def _print_crosslink_warnings(plans) -> None:
    by_term: dict[str, list[tuple[str, str, int]]] = {}
    for plan in plans:
        for edit in plan.edits:
            for term in edit.warn_terms:
                by_term.setdefault(term, []).append(
                    (plan.article_id, plan.md_file.name, edit.line_no)
                )
    if not by_term:
        return
    print("\n  Warnings (terms flagged in content_meta/bt.yml `crosslink.warn`):")
    for term in sorted(by_term):
        print(f"    '{term}' linked in:")
        for article_id, md_name, line_no in by_term[term]:
            print(f"      content/{article_id}/{md_name}:{line_no}")


def _cmd_help() -> None:
    print(
        """
  Commands
  --------
  hello                                 project stats
  ls                                    list current directory
  info                                  show name and summary for entities in current directory
  edit <path>                           edit entity or collection frontmatter
  check <path>                          list entities missing a world property
  cd <path>                             change directory (relative or from content root)
  cd ..                                 go up one level
  cd                                    go back to content root
  tree [path]                           show subtree from current dir (or path)
  add entity <kind> <slug> [name] [path]       create entity stub (kind first, then place it)
  add collection <slug> [title]         create collection folder under cwd
  add kind <kinds-path> [singular] [plural]  create kind stub
  move <entity-path> <new-parent>       move entity and rewrite all references
  move --kind <kind-path> <new-parent>  move kind within content_meta/kinds/ (no ref rewrites)
  rename <old-path> <new-slug>          rename entity or kind slug, rewrite all references
  crosslink <path> <namespace>          insert wikilinks (entity or folder of entities)
  help                                  show this help
  exit / quit                           leave the shell

  Tab opens completion menu; arrow keys navigate; Tab again accepts selection.
  Any argument can be omitted; the shell will prompt for it.
    """
    )


# ---------------------------------------------------------------------------
# Main REPL loop
# ---------------------------------------------------------------------------

def run_shell(project: Path) -> None:
    content = content_root(project)
    cwd = content
    recent_collections: list[str] = []  # content-relative paths, newest last

    shell_completer = _ShellCompleter(project)
    history = FileHistory(str(HISTORY_FILE))
    session: PromptSession = PromptSession(
        completer=shell_completer,
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=False,
        history=history,
        key_bindings=_make_tab_bindings(),
    )

    print("bunnytrail shell.  Type 'help' for commands, 'exit' to quit.")
    print(f"Root: {project}\n")

    while True:
        try:
            rel = cwd.relative_to(content)
            rel_str = str(rel) if str(rel) != "." else ""
        except ValueError:
            rel_str = str(cwd)

        if rel_str:
            prompt_text = f"{_GREEN}bt{_RESET}{_CYAN}/{rel_str}{_RESET}{_YELLOW} > {_RESET}"
        else:
            prompt_text = f"{_GREEN}bt{_RESET}{_YELLOW} > {_RESET}"

        try:
            raw = session.prompt(ANSI(prompt_text))
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        raw = raw.strip()
        if not raw:
            continue

        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            print(f"  parse error: {exc}")
            continue

        cmd = tokens[0]
        args = tokens[1:]

        if cmd in ("exit", "quit"):
            print("Bye.")
            break
        elif cmd == "hello":
            _cmd_hello(project)
        elif cmd == "ls":
            _cmd_ls(cwd, content)
        elif cmd == "info":
            _cmd_info(cwd, content)
        elif cmd == "edit":
            _cmd_edit(project, cwd, content, args)
        elif cmd == "check":
            _cmd_check(project, cwd, content, args)
        elif cmd == "tree":
            _cmd_tree(cwd, content, args)
        elif cmd == "cd":
            cwd = _cmd_cd(cwd, content, args)
        elif cmd == "add":
            new_coll = _cmd_add(project, cwd, args, recent_collections)
            if new_coll and new_coll not in recent_collections:
                recent_collections.append(new_coll)
        elif cmd == "move":
            _cmd_move(project, cwd, content, args)
        elif cmd == "rename":
            _cmd_rename(project, cwd, content, args)
        elif cmd == "crosslink":
            _cmd_crosslink(project, cwd, content, args)
        elif cmd == "help":
            _cmd_help()
        else:
            print(f"  unknown command: {cmd!r}  (type 'help' for commands)")
