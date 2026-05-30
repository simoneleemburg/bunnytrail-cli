"""
repl.py — interactive shell for the Alteria CLI.

Run via `alteria shell`.  Provides a prompt_toolkit REPL with:
  - context-aware tab completion (paths, kinds, commands)
  - persistent history (~/.alteria_history)
  - a virtual cwd inside content/ that you can cd around
  - interactive fallback prompts when params are omitted
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Iterable, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

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

HISTORY_FILE = Path.home() / ".alteria_history"

STYLE = Style.from_dict(
    {
        "prompt.cluster": "ansigreen bold",
        "prompt.path": "ansicyan",
        "prompt.arrow": "ansiyellow bold",
    }
)

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


# ---------------------------------------------------------------------------
# Completion helpers
# ---------------------------------------------------------------------------

def _child_dirs(path: Path) -> list[str]:
    """Sorted list of child directory names under *path*."""
    try:
        return sorted(
            p.name + "/" for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    except PermissionError:
        return []


def _complete_fs_path(
    fragment: str,
    base: Path,
) -> Iterable[Completion]:
    """
    Given a partially-typed path fragment (relative to *base*), yield
    Completion objects for matching sub-directories.
    """
    parts = fragment.split("/")
    prefix_parts = parts[:-1]
    partial_name = parts[-1]

    current = base
    for p in prefix_parts:
        current = current / p
        if not current.is_dir():
            return

    prefix = "/".join(prefix_parts) + "/" if prefix_parts else ""

    for name in _child_dirs(current):
        bare = name.rstrip("/")
        if bare.startswith(partial_name):
            display = name  # keep trailing slash for dirs
            completion_text = prefix + name
            # Replace what the user already typed
            yield Completion(
                completion_text,
                start_position=-len(fragment),
                display=display,
            )


def _all_kind_ids(kinds_base: Path) -> list[str]:
    """Return every kind folder name (leaf slug) found under *kinds_base*."""
    ids: list[str] = []
    for p in sorted(kinds_base.rglob("_kind.md")):
        ids.append(p.parent.name)
    return ids


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------

class AlteriaCompleter(Completer):
    def __init__(self, project: Path) -> None:
        self.project = project
        self._content = content_root(project)
        self._kinds = kinds_root(project)

    # prompt_toolkit calls this on every keystroke
    def get_completions(self, document, complete_event):  # noqa: ANN001
        text = document.text_before_cursor
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()

        # If text ends with space the user is starting a new token
        trailing_space = text.endswith(" ")
        if trailing_space:
            tokens.append("")

        if not tokens:
            for cmd in TOP_LEVEL_COMMANDS:
                yield Completion(cmd, start_position=0)
            return

        cmd = tokens[0]
        fragment = tokens[-1] if len(tokens) > 1 else ""

        # ---- top-level command completion ---------------------------------
        if len(tokens) == 1 and not trailing_space:
            for c in TOP_LEVEL_COMMANDS:
                if c.startswith(cmd):
                    yield Completion(c, start_position=-len(cmd))
            return

        # ---- cd: complete content/ paths ----------------------------------
        if cmd == "cd":
            if len(tokens) == 2:
                yield from _complete_fs_path(fragment, self._content)
            return

        # ---- tree: complete content/ paths (optional arg) -----------------
        if cmd == "tree":
            if len(tokens) == 2:
                yield from _complete_fs_path(fragment, self._content)
            return

        # ---- add subcommand -----------------------------------------------
        if cmd == "add":
            if len(tokens) == 2 and not trailing_space:
                for sub in ADD_SUBCOMMANDS:
                    if sub.startswith(fragment):
                        yield Completion(sub, start_position=-len(fragment))
                return

            if len(tokens) < 2:
                return

            sub = tokens[1]

            # add entity <path> <name> <kind>
            if sub == "entity":
                if len(tokens) == 3:  # completing path
                    yield from _complete_fs_path(fragment, self._content)
                elif len(tokens) == 5:  # completing kind
                    for kid in _all_kind_ids(self._kinds):
                        if kid.startswith(fragment):
                            yield Completion(kid, start_position=-len(fragment))
                return

            # add collection <path>
            if sub == "collection":
                if len(tokens) == 3:
                    yield from _complete_fs_path(fragment, self._content)
                return

            # add kind <path>
            if sub == "kind":
                if len(tokens) == 3:
                    yield from _complete_fs_path(fragment, self._kinds)
                return

        # ---- move <entity-path> <new-parent> --------------------------------
        if cmd == "move":
            # --kind flag switches completion base to kinds/
            has_kind_flag = "--kind" in tokens
            base = self._kinds if has_kind_flag else self._content
            # Count non-flag tokens to determine which positional arg we're on
            pos_tokens = [t for t in tokens[1:] if not t.startswith("-")]
            pos_count = len(pos_tokens) + (1 if trailing_space else 0)
            if pos_count in (1, 2):
                yield from _complete_fs_path(fragment, base)
            return

        # ---- rename <old-path> <new-slug> -----------------------------------
        if cmd == "rename":
            if len(tokens) == 2:
                # Try content first, then kinds
                yield from _complete_fs_path(fragment, self._content)
                yield from _complete_fs_path(fragment, self._kinds)
            # token 3 is a free-text slug — no completion
            return

        # ---- crosslink <article-path> <namespace-path> ----------------------
        if cmd == "crosslink":
            if len(tokens) in (2, 3):
                yield from _complete_fs_path(fragment, self._content)
            return


# ---------------------------------------------------------------------------
# Interactive fallback prompt
# ---------------------------------------------------------------------------

class _PathCompleter(Completer):
    """Single-use completer for a path rooted at *base*."""
    def __init__(self, base: Path) -> None:
        self._base = base

    def get_completions(self, document, complete_event):  # noqa: ANN001
        yield from _complete_fs_path(document.text_before_cursor, self._base)


def _ask(
    session: PromptSession,
    label: str,
    completer: Optional[Completer] = None,
    default: str = "",
) -> Optional[str]:
    """Prompt the user for a single value, returning None on Ctrl-C/Ctrl-D."""
    hint = f" [{default}]" if default else ""
    prompt_text = [
        ("class:prompt.arrow", f"  {label}{hint}: "),
    ]
    try:
        value = session.prompt(prompt_text, completer=completer, complete_while_typing=False)
        value = value.strip()
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
        # Hide internal underscore-files except the structural markers
        # the user is likely to care about.
        if child.name.startswith("_") and child.name not in (
            "_collection.md",
        ):
            continue
        if child.is_dir():
            marker = "[E]" if is_entity_folder(child) else "[C]"
            print(f"  {marker} {child.name}/")
        elif child.suffix == ".md":
            print(f"       {child.name}")


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
    # Allow absolute-style paths (e.g. aurethia/places) from content root
    candidate = (content / target_str) if not (cwd / target_str).is_dir() else (cwd / target_str)
    # prefer relative first
    if (cwd / target_str).is_dir():
        candidate = cwd / target_str
    elif (content / target_str).is_dir():
        candidate = content / target_str
    else:
        print(f"  no such directory: {target_str}")
        return cwd
    return candidate.resolve()


def _cmd_add(project: Path, cwd: Path, args: list[str], session: PromptSession) -> None:
    content = content_root(project)
    kinds = kinds_root(project)

    # ---- resolve subcommand ------------------------------------------------
    if not args:
        sub = _ask(session, "add what?", WordCompleter(ADD_SUBCOMMANDS))
        if not sub:
            return
    else:
        sub = args[0]

    if sub not in ADD_SUBCOMMANDS:
        print(f"  unknown add subcommand: {sub!r}")
        print("  usage: add entity|collection|kind ...")
        return

    # ---- add entity <path> <name> <kind> -----------------------------------
    if sub == "entity":
        path = args[1] if len(args) > 1 else None
        name = args[2] if len(args) > 2 else None
        kind_arg = args[3] if len(args) > 3 else None

        if path is None:
            # Default the path to cwd expressed relative to content
            try:
                default_path = str(cwd.relative_to(content))
            except ValueError:
                default_path = ""
            path = _ask(session, "path", _PathCompleter(content), default=default_path)
            if path is None:
                return

        if name is None:
            name = _ask(session, "name")
            if not name:
                return

        if kind_arg is None:
            kind_ids = _all_kind_ids(kinds)
            kind_arg = _ask(session, "kind", WordCompleter(kind_ids, sentence=True))
            if not kind_arg:
                return

        # resolve entity dir (relative to cwd first, then content root)
        path = path.rstrip("/")
        if path == ".":
            entity_slug = name.lower().replace(" ", "-")
            entity_dir = cwd / entity_slug
        else:
            raw_dir = (cwd / path) if (cwd / path).is_dir() or not (content / path).is_dir() else (content / path)
            if not raw_dir.is_relative_to(content):
                raw_dir = content / path
            entity_slug = name.lower().replace(" ", "-")
            entity_dir = raw_dir / entity_slug

        md_file = entity_dir / "index.md"

        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return

        kind_id = kind_arg.split("/")[-1]
        if not any(kinds.rglob(kind_id)):
            print(f"  warning: no kind '{kind_id}' found under content_meta/kinds/")

        entity_dir.mkdir(parents=True, exist_ok=True)
        write_frontmatter_md(md_file, f"name: {name}\nkind: {kind_id}")
        print(f"  created {md_file.relative_to(project)}")
        return

    # ---- add collection <path> <title> -------------------------------------
    if sub == "collection":
        path = args[1] if len(args) > 1 else None
        title = " ".join(args[2:]) if len(args) > 2 else None

        if path is None:
            try:
                default_path = str(cwd.relative_to(content))
            except ValueError:
                default_path = ""
            path = _ask(session, "path", _PathCompleter(content), default=default_path)
            if path is None:
                return

        if title is None:
            title = _ask(session, "title")
            if not title:
                return

        path = path.rstrip("/")
        coll_dir = (cwd / path) if not (content / path).is_dir() else (content / path)
        if not coll_dir.is_relative_to(content):
            coll_dir = content / path

        md_file = coll_dir / "_collection.md"
        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return
        coll_dir.mkdir(parents=True, exist_ok=True)
        write_frontmatter_md(md_file, f"title: {title}")
        print(f"  created {md_file.relative_to(project)}")
        return

    # ---- add kind <path> <singular> [plural] -------------------------------
    if sub == "kind":
        path = args[1] if len(args) > 1 else None
        singular = args[2] if len(args) > 2 else None
        plural = args[3] if len(args) > 3 else None

        if path is None:
            path = _ask(session, "kinds path", _PathCompleter(kinds))
            if path is None:
                return

        if singular is None:
            singular = _ask(session, "singular name")
            if not singular:
                return

        if plural is None:
            plural = _ask(session, "plural name", default=f"{singular}s")
            if not plural:
                plural = f"{singular}s"

        kind_dir = kinds / path.rstrip("/")
        md_file = kind_dir / "_kind.md"
        if md_file.exists():
            print(f"  already exists: {md_file.relative_to(project)}")
            return
        kind_dir.mkdir(parents=True, exist_ok=True)
        write_frontmatter_md(md_file, f"singular: {singular}\nplural: {plural}")
        print(f"  created {md_file.relative_to(project)}")
        return


def _cmd_move(project: Path, cwd: Path, content: Path, args: list[str], session: PromptSession) -> None:
    kinds = kinds_root(project)

    # Parse --kind flag out of args
    is_kind = "--kind" in args
    args = [a for a in args if a != "--kind"]

    entity_path = args[0] if len(args) > 0 else None
    new_parent = args[1] if len(args) > 1 else None

    base = kinds if is_kind else content
    base_label = "kinds path" if is_kind else "entity path"
    parent_label = "new kinds parent" if is_kind else "new parent"

    if entity_path is None:
        if is_kind:
            entity_path = _ask(session, base_label, _PathCompleter(base))
        else:
            try:
                default_path = str(cwd.relative_to(content))
            except ValueError:
                default_path = ""
            entity_path = _ask(session, base_label, _PathCompleter(base), default=default_path)
        if not entity_path:
            return

    if new_parent is None:
        new_parent = _ask(session, parent_label, _PathCompleter(base))
        if not new_parent:
            return

    # Resolve relative to cwd (content only; kinds paths are always absolute-style)
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

    confirm = _ask(session, "\n  Proceed? [y/N]")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    try:
        execute_move(plan)
        print(f"  Done.  Moved to {id_root}/{plan.new_id}")
    except Exception as exc:
        print(f"  Error during move: {exc}")


def _cmd_rename(project: Path, cwd: Path, content: Path, args: list[str], session: PromptSession) -> None:
    kinds = kinds_root(project)
    old_path = args[0] if len(args) > 0 else None
    new_slug = args[1] if len(args) > 1 else None

    if old_path is None:
        try:
            default_path = str(cwd.relative_to(content))
        except ValueError:
            default_path = ""
        # Complete over both content and kinds
        class _BothCompleter(Completer):
            def __init__(self, c: Path, k: Path) -> None:
                self._c, self._k = c, k
            def get_completions(self, document, complete_event):  # noqa: ANN001
                yield from _complete_fs_path(document.text_before_cursor, self._c)
                yield from _complete_fs_path(document.text_before_cursor, self._k)

        old_path = _ask(session, "entity or kind path", _BothCompleter(content, kinds), default=default_path)
        if not old_path:
            return

    if new_slug is None:
        new_slug = _ask(session, "new slug")
        if not new_slug:
            return

    # Resolve relative to cwd first, then content root, then kinds root
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

    # ------------------------------------------------------------------
    # Detect entity vs kind so we know which display-name fields to
    # prompt for.  These prompts default to the current value so
    # hitting Enter is a no-op.
    # ------------------------------------------------------------------
    from .helpers import (
        read_entity_display_name,
        read_kind_display_names,
    )
    new_display: dict[str, str] = {}
    entity_dir = content / old_path
    if entity_dir.is_dir() and (entity_dir / "index.md").is_file():
        old_name = read_entity_display_name(entity_dir)
        if old_name:
            answer = _ask(
                session,
                "new display name",
                default=old_name,
            )
            if answer and answer != old_name:
                new_display["name"] = answer
    else:
        kind_dir = kinds / old_path
        if kind_dir.is_dir() and (kind_dir / "_kind.md").is_file():
            old_singular, old_plural = read_kind_display_names(kind_dir)
            if old_singular:
                answer = _ask(
                    session,
                    "new singular",
                    default=old_singular,
                )
                if answer and answer != old_singular:
                    new_display["singular"] = answer
            if old_plural:
                answer = _ask(
                    session,
                    "new plural",
                    default=old_plural,
                )
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

    confirm = _ask(session, "\n  Proceed? [y/N]")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    try:
        execute_rename(plan)
        print(f"  Done.  Renamed to {id_root}/{plan.new_id}")
    except Exception as exc:
        print(f"  Error during rename: {exc}")


def _cmd_crosslink(project: Path, cwd: Path, content: Path, args: list[str], session: PromptSession) -> None:
    article_path = args[0] if len(args) > 0 else None
    namespace_path = args[1] if len(args) > 1 else None

    if article_path is None:
        try:
            default_path = str(cwd.relative_to(content))
        except ValueError:
            default_path = ""
        article_path = _ask(session, "article path", _PathCompleter(content), default=default_path)
        if not article_path:
            return

    if namespace_path is None:
        namespace_path = _ask(session, "namespace path", _PathCompleter(content))
        if not namespace_path:
            return

    # Resolve relative to cwd first, then content root
    def _resolve(p: str) -> str:
        p = p.rstrip("/")
        if (cwd / p).is_dir():
            return str((cwd / p).relative_to(content))
        return p

    article_path = _resolve(article_path)
    namespace_path = _resolve(namespace_path)

    plans, batch_error = plan_crosslink_folder(project, article_path, namespace_path)

    if batch_error:
        print(f"  Error: {batch_error}")
        return

    print(f"  Target:    content/{article_path}")
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

    confirm = _ask(session, "\n  Proceed? [y/N]")
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
    """Print a 'name clash' warning for a move/rename plan in the REPL.

    Mirrors :func:`alteria_cli.main._print_collisions` but uses the
    REPL's two-space indent and plain ``print``.
    """
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
    """Surface any ``warn:`` terms that were linked, grouped by term."""
    by_term: dict[str, list[tuple[str, str, int]]] = {}
    for plan in plans:
        for edit in plan.edits:
            for term in edit.warn_terms:
                by_term.setdefault(term, []).append(
                    (plan.article_id, plan.md_file.name, edit.line_no)
                )
    if not by_term:
        return
    print("\n  Warnings (terms flagged in content_meta/crosslink.yml `warn:`):")
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
  cd <path>                             change directory (relative or from content root)
  cd ..                                 go up one level
  cd                                    go back to content root
  tree [path]                           show subtree from current dir (or path)
  add entity <path> <name> <kind>       create entity stub
  add collection <path> <title>         create collection folder
  add kind <path> <singular> [plural]   create kind stub
  move <entity-path> <new-parent>       move entity and rewrite all references
  move --kind <kind-path> <new-parent>  move kind within content_meta/kinds/ (no ref rewrites)
  rename <old-path> <new-slug>          rename entity or kind slug, rewrite all references
  crosslink <path> <namespace>          insert wikilinks (entity or folder of entities)
  help                                  show this help
  exit / quit                           leave the shell

  Tab completion is available for all path and kind arguments.
  Use quotes for names with spaces: add entity places/my-city "My City" planet
  Any argument can be omitted; the shell will prompt for it.
    """
    )


# ---------------------------------------------------------------------------
# Main REPL loop
# ---------------------------------------------------------------------------

def run_shell(project: Path) -> None:
    content = content_root(project)
    cwd = content

    completer = AlteriaCompleter(project)
    session: PromptSession = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        completer=completer,
        complete_while_typing=False,  # only complete on Tab
        style=STYLE,
    )

    print("Alteria shell.  Type 'help' for commands, 'exit' to quit.")
    print(f"Root: {project}\n")

    while True:
        # Build a prompt that shows content-relative cwd
        try:
            rel = cwd.relative_to(content)
            rel_str = str(rel) if str(rel) != "." else ""
        except ValueError:
            rel_str = str(cwd)

        prompt_parts = [
            ("class:prompt.cluster", "alteria"),
            ("class:prompt.path", f"/{rel_str}" if rel_str else ""),
            ("class:prompt.arrow", " > "),
        ]

        try:
            raw = session.prompt(prompt_parts)
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
        elif cmd == "tree":
            _cmd_tree(cwd, content, args)
        elif cmd == "cd":
            cwd = _cmd_cd(cwd, content, args)
        elif cmd == "add":
            _cmd_add(project, cwd, args, session)
        elif cmd == "move":
            _cmd_move(project, cwd, content, args, session)
        elif cmd == "rename":
            _cmd_rename(project, cwd, content, args, session)
        elif cmd == "crosslink":
            _cmd_crosslink(project, cwd, content, args, session)
        elif cmd == "help":
            _cmd_help()
        else:
            print(f"  unknown command: {cmd!r}  (type 'help' for commands)")
