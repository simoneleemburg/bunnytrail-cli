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

Future:
    alteria rename <old-path> <new-slug>
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .helpers import (
    content_root,
    execute_move,
    find_project_root,
    is_entity_folder,
    is_kind_folder,
    kinds_root,
    list_kinds_tree,
    list_tree,
    plan_move,
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

    entity_count = sum(
        1 for p in content.rglob("index.yaml") if is_entity_folder(p.parent)
    )
    kind_count = sum(
        1 for p in kinds.rglob("_kind.yaml") if is_kind_folder(p.parent)
    )
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
    """
    root: Path = ctx.obj["root"]
    entity_dir = content_root(root) / path
    yaml_file = entity_dir / "index.yaml"
    md_file = entity_dir / "index.md"

    if yaml_file.exists() and not force:
        click.echo(
            f"Error: '{yaml_file}' already exists.  Use --force to overwrite.",
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

    yaml_lines = [
        f"name: {name}\n",
        f"kind: {kind_id}\n",
    ]
    if summary:
        yaml_lines.append(f"summary: {summary}\n")

    yaml_file.write_text("".join(yaml_lines), encoding="utf-8")
    md_file.write_text("", encoding="utf-8")  # empty prose stub

    click.echo(f"Created entity stub: {yaml_file.relative_to(root)}")
    click.echo(f"Created prose stub:  {md_file.relative_to(root)}")


@add.command("collection")
@click.argument("path")
@click.argument("title")
@click.option("--description", default="", help="Optional description.")
@click.option("--force", is_flag=True, help="Overwrite existing _collection.yaml.")
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
    """
    root: Path = ctx.obj["root"]
    coll_dir = content_root(root) / path
    yaml_file = coll_dir / "_collection.yaml"

    if yaml_file.exists() and not force:
        click.echo(
            f"Error: '{yaml_file}' already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    coll_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"title: {title}\n"]
    if description:
        lines.append(f"description: {description}\n")

    yaml_file.write_text("".join(lines), encoding="utf-8")
    click.echo(f"Created collection: {yaml_file.relative_to(root)}")


@add.command("kind")
@click.argument("path")
@click.argument("singular")
@click.argument("plural", required=False, default="")
@click.option("--description", default="", help="Short description of the kind.")
@click.option("--force", is_flag=True, help="Overwrite existing _kind.yaml.")
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
    """
    root: Path = ctx.obj["root"]
    kind_dir = kinds_root(root) / path
    yaml_file = kind_dir / "_kind.yaml"

    if yaml_file.exists() and not force:
        click.echo(
            f"Error: '{yaml_file}' already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    kind_dir.mkdir(parents=True, exist_ok=True)

    plural_val = plural or f"{singular}s"
    lines = [
        f"singular: {singular}\n",
        f"plural: {plural_val}\n",
    ]
    if description:
        lines.append(f"description: {description}\n")

    yaml_file.write_text("".join(lines), encoding="utf-8")
    click.echo(f"Created kind stub: {yaml_file.relative_to(root)}")


# ---------------------------------------------------------------------------
# rename (stub — not yet implemented)
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("old_path")
@click.argument("new_slug")
def rename(old_path: str, new_slug: str) -> None:
    """[NOT YET IMPLEMENTED] Rename an entity or kind slug.

    OLD_PATH is relative to content/ (or content_meta/kinds/).
    NEW_SLUG is the new folder name (slug only, no parent path).
    """
    click.echo("rename: not yet implemented.", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("entity_path")
@click.argument("new_parent")
@click.option("--dry-run", is_flag=True, help="Show what would change without touching files.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def move(ctx: click.Context, entity_path: str, new_parent: str, dry_run: bool, yes: bool) -> None:
    """Move an entity to a new parent collection and update all references.

    ENTITY_PATH is relative to content/ (e.g. aurethia/places/old/myplace).
    NEW_PARENT  is the destination collection, also relative to content/
                (e.g. aurethia/places/new-home).

    The entity slug (folder name) is preserved.  All target: relations and
    full-path wikilinks across the project are rewritten automatically.
    Use --dry-run to preview every change before committing.
    """
    root: Path = ctx.obj["root"]

    plan = plan_move(root, entity_path.rstrip("/"), new_parent.rstrip("/"))

    if plan.error:
        click.echo(f"Error: {plan.error}", err=True)
        sys.exit(1)

    click.echo(f"Move:  content/{plan.old_id}")
    click.echo(f"  ->  content/{plan.new_id}")

    if plan.refs:
        click.echo(f"\nReferences to update ({len(plan.refs)}):")
        for ref in plan.refs:
            rel = ref.file.relative_to(root)
            click.echo(f"  {rel}:{ref.line_no}")
            click.echo(f"    - {ref.old_text.strip()}")
            click.echo(f"    + {ref.new_text.strip()}")
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

    click.echo(f"\nDone.  Moved to content/{plan.new_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
