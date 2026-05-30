"""
main.py — Alteria CLI entry point.

Usage:
    alteria shell                   interactive REPL (recommended)
    alteria hello
    alteria tree [--kinds] [--depth N] [PATH]
    alteria add entity PATH NAME KIND
    alteria add collection PATH
    alteria add kind PATH SINGULAR [PLURAL]
    alteria move <entity-path> <new-parent>
    alteria rename <old-path> <new-slug>
    alteria crosslink <path> <namespace-path>

All entities, collections, and kinds are authored as a single
Markdown file with YAML frontmatter (``index.md``,
``_collection.md``, ``_kind.md``). The CLI writes that shape; the
legacy split layout (separate ``index.yaml`` + ``index.md``) is no
longer supported.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .helpers import (
    content_root,
    execute_crosslink,
    execute_move,
    execute_rename,
    find_project_root,
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
    write_frontmatter_md,
)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Alteria worldbuilding compendium CLI."""
    ctx.ensure_object(dict)
    try:
        ctx.obj["root"] = find_project_root()
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# hello
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def hello(ctx: click.Context) -> None:
    """Print a greeting and confirm the project root was found."""
    root: Path = ctx.obj["root"]
    click.echo("Hello from the Alteria CLI.")
    click.echo(f"Project root: {root}")

    # Quick stats
    content = content_root(root)
    kinds = kinds_root(root)

    entity_count = sum(1 for _ in iter_entity_md_files(content))
    kind_count = sum(1 for _ in iter_kind_md_files(kinds))
    click.echo(f"Entities found: {entity_count}")
    click.echo(f"Kinds found:    {kind_count}")


# ---------------------------------------------------------------------------
# shell (interactive REPL)
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def shell(ctx: click.Context) -> None:
    """Start the interactive Alteria shell with tab completion."""
    from .repl import run_shell  # local import keeps startup fast
    run_shell(ctx.obj["root"])


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("path", default="", required=False)
@click.option("--kinds", is_flag=True, help="Browse content_meta/kinds/ instead of content/.")
@click.option("--depth", default=3, show_default=True, help="Max display depth.")
@click.pass_context
def tree(ctx: click.Context, path: str, kinds: bool, depth: int) -> None:
    """Display a directory tree of content/ (or kinds/).

    PATH is optional; if given it is a sub-path relative to content/ (or
    content_meta/kinds/ when --kinds is used).

    Legend: [E] entity folder  [C] collection folder  [K] kind folder
    """
    root: Path = ctx.obj["root"]
    base = kinds_root(root) if kinds else content_root(root)

    if path:
        base = base / path
        if not base.is_dir():
            click.echo(f"Error: '{base}' is not a directory.", err=True)
            sys.exit(1)

    click.echo(str(base))
    if kinds:
        lines = list_kinds_tree(base)
    else:
        lines = list_tree(base, max_depth=depth)

    if lines:
        click.echo("\n".join(lines))
    else:
        click.echo("(empty)")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

@cli.group()
def add() -> None:
    """Add a new entity, collection, or kind stub."""


@add.command("entity")
@click.argument("path")
@click.argument("name")
@click.argument("kind")
@click.option("--summary", default="", help="One-line summary.")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
@click.pass_context
def add_entity(
    ctx: click.Context,
    path: str,
    name: str,
    kind: str,
    summary: str,
    force: bool,
) -> None:
    """Create a new entity stub at content/PATH.

    PATH is the full entity path relative to content/, e.g.
    'aurethia/places/celestial/mynewplace'.
    NAME is the display name.
    KIND must match a folder under content_meta/kinds/.

    Writes a single ``index.md`` with YAML frontmatter at the top.
    """
    root: Path = ctx.obj["root"]
    entity_dir = content_root(root) / path
    md_file = entity_dir / "index.md"

    if md_file.exists() and not force:
        click.echo(
            f"Error: '{md_file}' already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    # Validate kind exists
    kind_id = kind.split("/")[-1]  # allow 'place/celestial-body/planet' or just 'planet'
    if not any(kinds_root(root).rglob(kind_id)):
        click.echo(
            f"Warning: no kind folder named '{kind_id}' found under content_meta/kinds/.",
            err=True,
        )

    entity_dir.mkdir(parents=True, exist_ok=True)

    fm_lines = [f"name: {name}", f"kind: {kind_id}"]
    if summary:
        fm_lines.append(f"summary: {summary}")
    write_frontmatter_md(md_file, "\n".join(fm_lines))

    click.echo(f"Created entity stub: {md_file.relative_to(root)}")


@add.command("collection")
@click.argument("path")
@click.argument("title")
@click.option("--description", default="", help="Optional description.")
@click.option("--force", is_flag=True, help="Overwrite existing _collection.md.")
@click.pass_context
def add_collection(
    ctx: click.Context,
    path: str,
    title: str,
    description: str,
    force: bool,
) -> None:
    """Create a new collection folder at content/PATH.

    PATH is relative to content/, e.g. 'aurethia/places'.

    Writes a ``_collection.md`` with YAML frontmatter
    (``title:``, optional ``description:``).
    """
    root: Path = ctx.obj["root"]
    coll_dir = content_root(root) / path
    md_file = coll_dir / "_collection.md"

    if md_file.exists() and not force:
        click.echo(
            f"Error: '{md_file}' already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    coll_dir.mkdir(parents=True, exist_ok=True)

    fm_lines = [f"title: {title}"]
    if description:
        fm_lines.append(f"description: {description}")
    write_frontmatter_md(md_file, "\n".join(fm_lines))
    click.echo(f"Created collection: {md_file.relative_to(root)}")


@add.command("kind")
@click.argument("path")
@click.argument("singular")
@click.argument("plural", required=False, default="")
@click.option("--description", default="", help="Short description of the kind.")
@click.option("--force", is_flag=True, help="Overwrite existing _kind.md.")
@click.pass_context
def add_kind(
    ctx: click.Context,
    path: str,
    singular: str,
    plural: str,
    description: str,
    force: bool,
) -> None:
    """Create a new kind stub at content_meta/kinds/PATH.

    PATH is relative to content_meta/kinds/, e.g. 'being/mortal/elf'.
    SINGULAR is the singular display name.
    PLURAL defaults to SINGULAR + 's' if omitted.

    Writes a ``_kind.md`` with YAML frontmatter.
    """
    root: Path = ctx.obj["root"]
    kind_dir = kinds_root(root) / path
    md_file = kind_dir / "_kind.md"

    if md_file.exists() and not force:
        click.echo(
            f"Error: '{md_file}' already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    kind_dir.mkdir(parents=True, exist_ok=True)

    plural_val = plural or f"{singular}s"
    fm_lines = [f"singular: {singular}", f"plural: {plural_val}"]
    if description:
        fm_lines.append(f"description: {description}")
    write_frontmatter_md(md_file, "\n".join(fm_lines))
    click.echo(f"Created kind stub: {md_file.relative_to(root)}")


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

def _print_collisions(plan) -> None:
    """Print a 'name clash' warning for a move/rename plan.

    Called from both :func:`rename` and :func:`move` once the plan has
    been built.  ``plan.collisions`` is a (possibly empty) list of
    existing entity ids that share the new leaf slug; emitting them
    here lets the user decide whether to abort.  Existing links to
    those peers will be rewritten by the same scanner pass.
    """
    if not plan.collisions:
        return
    n = len(plan.collisions)
    click.echo(
        f"\nName clash: the new leaf slug is already used by "
        f"{n} existing entit{'y' if n == 1 else 'ies'}:"
    )
    for peer in plan.collisions:
        click.echo(f"  ! content/{peer}")
    click.echo(
        "  Existing bare links to those entities will be rewritten "
        "to a longer disambiguating form."
    )


@cli.command()
@click.argument("old_path")
@click.argument("new_slug")
@click.option("--dry-run", is_flag=True, help="Show what would change without touching files.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def rename(ctx: click.Context, old_path: str, new_slug: str, dry_run: bool, yes: bool) -> None:
    """Rename an entity, collection, or kind slug, updating all references.

    OLD_PATH is relative to content/ for entities/collections, or relative to
    content_meta/kinds/ for kinds.  The tool checks content/ first;
    if not found there it tries content_meta/kinds/.

    NEW_SLUG is the new folder name only — no slashes.

    For entities, updates: target: relations and full-path wikilinks
    inside any entity's index.md (frontmatter + body), and SVG href
    links under assets/.

    For collections, renames the folder and cascades to all descendant
    entity and collection IDs. Updates all references to descendants
    (target:, [[wikilinks]], SVG hrefs).

    For kinds, updates: kind: fields, kinds/<slug> affinity fields,
    and [[kinds/<slug>...]] wikilinks — all inside entities' index.md.

    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    # ------------------------------------------------------------------
    # Detect whether this is an entity / collection / kind to know
    # which display-name fields to prompt for.  Cheap: just inspect
    # the path on disk; plan_rename will reject anything bogus later.
    # ------------------------------------------------------------------
    from .helpers import (
        content_root,
        kinds_root,
        read_entity_display_name,
        read_kind_display_names,
    )
    cwd_content = content_root(root)
    cwd_kinds = kinds_root(root)
    target_dir = (cwd_content / old_path.rstrip("/")).resolve()
    new_display: dict[str, str] = {}
    if target_dir.is_dir() and (target_dir / "index.md").is_file():
        old_name = read_entity_display_name(target_dir)
        if old_name:
            new_name = click.prompt(
                f"  new display name (current: {old_name!r})",
                default=old_name,
                show_default=False,
            )
            if new_name and new_name != old_name:
                new_display["name"] = new_name
    else:
        kind_dir = (cwd_kinds / old_path.rstrip("/")).resolve()
        if kind_dir.is_dir() and (kind_dir / "_kind.md").is_file():
            old_singular, old_plural = read_kind_display_names(kind_dir)
            if old_singular:
                ns = click.prompt(
                    f"  new singular (current: {old_singular!r})",
                    default=old_singular,
                    show_default=False,
                )
                if ns and ns != old_singular:
                    new_display["singular"] = ns
            if old_plural:
                np = click.prompt(
                    f"  new plural (current: {old_plural!r})",
                    default=old_plural,
                    show_default=False,
                )
                if np and np != old_plural:
                    new_display["plural"] = np

    plan = plan_rename(
        root, old_path.rstrip("/"), new_slug,
        new_display_names=new_display or None,
    )

    if plan.error:
        click.echo(f"Error: {plan.error}", err=True)
        sys.exit(1)

    kind_label = "kind" if plan.is_kind else "entity/collection"
    id_root = "content_meta/kinds" if plan.is_kind else "content"
    click.echo(f"Rename {kind_label}:  {id_root}/{plan.old_id}")
    click.echo(f"              ->  {id_root}/{plan.new_id}")

    if plan.display_renames:
        click.echo("\nDisplay name changes:")
        for old_disp, new_disp in plan.display_renames.items():
            click.echo(f"  {old_disp!r} -> {new_disp!r}")

    if plan.refs:
        click.echo(f"\nReferences to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(root)
            click.echo(f"  {rel}:{ref.line_no}")
            click.echo(f"    - {ref.old_text.strip()}")
            click.echo(f"    + {ref.new_text.strip()}")
    else:
        click.echo("\nNo references found — only the folder will be renamed.")

    if plan.warnings:
        click.echo(f"\nWarnings ({len(plan.warnings)}):")
        for w in plan.warnings:
            click.echo(f"  ! {w}")

    _print_collisions(plan)

    if dry_run:
        click.echo("\n(dry run — no files changed)")
        return

    if not yes:
        click.confirm("\nProceed?", abort=True)

    try:
        execute_rename(plan)
    except Exception as exc:
        click.echo(f"Error during rename: {exc}", err=True)
        sys.exit(1)

    click.echo(f"\nDone.  Renamed to {id_root}/{plan.new_id}")


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("entity_path")
@click.argument("new_parent")
@click.option("--kind", "is_kind", is_flag=True, help="Move a kind instead of a content entity.")
@click.option("--dry-run", is_flag=True, help="Show what would change without touching files.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def move(ctx: click.Context, entity_path: str, new_parent: str, is_kind: bool, dry_run: bool, yes: bool) -> None:
    """Move an entity, collection, or kind to a new parent and update all references.

    Without --kind:
      ENTITY_PATH is relative to content/ (e.g. aurethia/places/old/myplace,
      or a collection like aurethia/places/old).
      NEW_PARENT  is the destination collection, also relative to content/.
      All target: relations and full-path wikilinks are rewritten automatically.

      When ENTITY_PATH points at a collection (folder with _collection.md),
      the move cascades to every descendant entity and collection id, and
      all references to them are rewritten in one pass.

    With --kind:
      ENTITY_PATH is relative to content_meta/kinds/ (e.g. being/human).
      NEW_PARENT  is also relative to content_meta/kinds/ (e.g. being/mortal).
      Because wikilinks and kind: fields reference kinds by slug only,
      no reference rewrites are needed — only the folder is moved.

    The slug (folder name) is always preserved.
    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    if is_kind:
        plan = plan_move_kind(root, entity_path.rstrip("/"), new_parent.rstrip("/"))
        id_root = "content_meta/kinds"
    else:
        plan = plan_move(root, entity_path.rstrip("/"), new_parent.rstrip("/"))
        id_root = "content"

    if plan.error:
        click.echo(f"Error: {plan.error}", err=True)
        sys.exit(1)

    click.echo(f"Move:  {id_root}/{plan.old_id}")
    click.echo(f"  ->  {id_root}/{plan.new_id}")

    if plan.refs:
        click.echo(f"\nReferences to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(root)
            click.echo(f"  {rel}:{ref.line_no}")
            click.echo(f"    - {ref.old_text.strip()}")
            click.echo(f"    + {ref.new_text.strip()}")
    else:
        if is_kind:
            click.echo("\nNo reference rewrites needed (kinds are referenced by slug).")
        else:
            click.echo("\nNo references found — only the folder will move.")

    if plan.warnings:
        click.echo(f"\nWarnings ({len(plan.warnings)}):")
        for w in plan.warnings:
            click.echo(f"  ! {w}")

    _print_collisions(plan)

    if dry_run:
        click.echo("\n(dry run — no files changed)")
        return

    if not yes:
        click.confirm("\nProceed?", abort=True)

    try:
        execute_move(plan)
    except Exception as exc:
        click.echo(f"Error during move: {exc}", err=True)
        sys.exit(1)

    click.echo(f"\nDone.  Moved to {id_root}/{plan.new_id}")


# ---------------------------------------------------------------------------
# crosslink
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("article_path")
@click.argument("namespace_path")
@click.option("--dry-run", is_flag=True, help="Show what would change without touching files.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def crosslink(
    ctx: click.Context,
    article_path: str,
    namespace_path: str,
    dry_run: bool,
    yes: bool,
) -> None:
    """Insert wikilinks into ARTICLE_PATH for entities and kinds found in NAMESPACE_PATH.

    ARTICLE_PATH is relative to content/ and may be either:

      * a single entity folder (e.g. foundation/fabric/nearing) — only
        that entity's index.md is updated; or
      * any other folder under content/ (e.g. foundation/fabric) —
        every entity reachable below it is crosslinked in one pass.

    NAMESPACE_PATH is relative to content/ and scopes which entities
    are candidates for linking (e.g. aurethia/places).

    Two sources of candidates are searched:
      - Every entity found recursively under content/NAMESPACE_PATH,
        matched by its display name (name: field).
      - Every kind in content_meta/kinds/, matched by singular: and plural:.

    Matching is exact and whole-word.  Text already inside [[...]] wikilinks
    is never re-linked.  Only the first occurrence of each name within
    a given article is linked.  The shortest valid wikilink form for
    each target is emitted — bare slug for same-cluster or universal
    targets, full path otherwise.

    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    plans, batch_error = plan_crosslink_folder(
        root, article_path.rstrip("/"), namespace_path.rstrip("/")
    )

    if batch_error:
        click.echo(f"Error: {batch_error}", err=True)
        sys.exit(1)

    click.echo(f"Target:    content/{article_path.rstrip('/')}")
    click.echo(f"Namespace: content/{namespace_path.rstrip('/')}")
    click.echo(f"Articles:  {len(plans)}")

    actionable = [p for p in plans if p.edits and not p.error]
    erroring = [p for p in plans if p.error]

    if erroring:
        click.echo(f"\nSkipped ({len(erroring)}):")
        for p in erroring:
            click.echo(f"  ! {p.article_id}: {p.error}")

    if not actionable:
        click.echo("\nNo matches found — nothing to link.")
        return

    total_edits = sum(len(p.edits) for p in actionable)
    click.echo(
        f"\nProposed wikilinks across {len(actionable)} article(s), "
        f"{total_edits} line(s) affected:"
    )
    for p in actionable:
        click.echo(f"\n  content/{p.article_id}/index.md")
        for edit in p.edits:
            click.echo(f"    line {edit.line_no}:")
            click.echo(f"      - {edit.old_text.strip()}")
            click.echo(f"      + {edit.new_text.strip()}")

    if dry_run:
        click.echo("\n(dry run — no files changed)")
        _print_crosslink_warnings(actionable)
        return

    if not yes:
        click.confirm("\nProceed?", abort=True)

    for p in actionable:
        try:
            execute_crosslink(p)
        except Exception as exc:
            click.echo(f"Error during crosslink for {p.article_id}: {exc}", err=True)
            sys.exit(1)

    click.echo(f"\nDone.  Updated {len(actionable)} article(s).")
    _print_crosslink_warnings(actionable)


def _print_crosslink_warnings(plans) -> None:
    """Print a per-term summary of any `warn:` matches that were linked.

    Reads ``warn_terms`` off each :class:`CrosslinkEdit` in *plans* and
    prints one section grouped by term, listing every article/line
    where the term was auto-linked.  Silent when no warnings fired.
    """
    by_term: dict[str, list[tuple[str, int]]] = {}
    for plan in plans:
        for edit in plan.edits:
            for term in edit.warn_terms:
                by_term.setdefault(term, []).append((plan.article_id, edit.line_no))
    if not by_term:
        return
    click.echo("\nWarnings (terms flagged in content_meta/crosslink.yml `warn:`):")
    for term in sorted(by_term):
        click.echo(f"  '{term}' linked in:")
        for article_id, line_no in by_term[term]:
            click.echo(f"    content/{article_id}/index.md:{line_no}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
