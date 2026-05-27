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
    alteria crosslink <article-path> <namespace-path>

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

@cli.command()
@click.argument("old_path")
@click.argument("new_slug")
@click.option("--dry-run", is_flag=True, help="Show what would change without touching files.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def rename(ctx: click.Context, old_path: str, new_slug: str, dry_run: bool, yes: bool) -> None:
    """Rename an entity slug or a kind slug, updating all references.

    OLD_PATH is relative to content/ for entities, or relative to
    content_meta/kinds/ for kinds.  The tool checks content/ first;
    if not found there it tries content_meta/kinds/.

    NEW_SLUG is the new folder name only — no slashes.

    For entities, updates: target: relations and full-path wikilinks
    inside any entity's index.md (frontmatter + body), and SVG href
    links under assets/.
    For kinds, updates: kind: fields, kinds/<slug> affinity fields,
    and [[kinds/<slug>...]] wikilinks — all inside entities' index.md.

    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    plan = plan_rename(root, old_path.rstrip("/"), new_slug)

    if plan.error:
        click.echo(f"Error: {plan.error}", err=True)
        sys.exit(1)

    kind_label = "kind" if plan.is_kind else "entity"
    id_root = "content_meta/kinds" if plan.is_kind else "content"
    click.echo(f"Rename {kind_label}:  {id_root}/{plan.old_id}")
    click.echo(f"              ->  {id_root}/{plan.new_id}")

    if plan.refs:
        click.echo(f"\nReferences to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(root)
            click.echo(f"  {rel}:{ref.line_no}")
            click.echo(f"    - {ref.old_text.strip()}")
            click.echo(f"    + {ref.new_text.strip()}")
    else:
        click.echo("\nNo references found — only the folder will be renamed.")

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
    """Move an entity (or kind) to a new parent and update all references.

    Without --kind:
      ENTITY_PATH is relative to content/ (e.g. aurethia/places/old/myplace).
      NEW_PARENT  is the destination collection, also relative to content/.
      All target: relations and full-path wikilinks are rewritten automatically.

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

    ARTICLE_PATH is relative to content/ (e.g. foundation/fabric/nearing).
    NAMESPACE_PATH is relative to content/ and scopes which entities are
    candidates for linking (e.g. aurethia/places).

    Two sources of candidates are searched:
      - Every entity found recursively under content/NAMESPACE_PATH, matched
        by its display name (name: field).
      - Every kind in content_meta/kinds/, matched by singular: and plural:.

    Matching is exact and whole-word.  Text already inside [[...]] wikilinks
    is never re-linked.  Only the first occurrence of each name is linked.

    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    plan = plan_crosslink(root, article_path.rstrip("/"), namespace_path.rstrip("/"))

    if plan.error:
        click.echo(f"Error: {plan.error}", err=True)
        sys.exit(1)

    click.echo(f"Article:   content/{plan.article_id}")
    click.echo(f"Namespace: content/{plan.namespace}")

    if not plan.edits:
        click.echo("\nNo matches found — nothing to link.")
        return

    click.echo(f"\nProposed wikilinks ({len(plan.edits)} line(s) affected):")
    for edit in plan.edits:
        click.echo(f"  line {edit.line_no}:")
        click.echo(f"    - {edit.old_text.strip()}")
        click.echo(f"    + {edit.new_text.strip()}")

    if dry_run:
        click.echo("\n(dry run — no files changed)")
        return

    if not yes:
        click.confirm("\nProceed?", abort=True)

    try:
        execute_crosslink(plan)
    except Exception as exc:
        click.echo(f"Error during crosslink: {exc}", err=True)
        sys.exit(1)

    click.echo(f"\nDone.  Updated content/{plan.article_id}/index.md")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
