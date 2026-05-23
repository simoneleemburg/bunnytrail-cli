"""
helpers.py — shared utilities for locating project roots and reading
content/content_meta structure without loading every file.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* (default: cwd) until we find the project root.

    The project root is identified by the presence of both a ``content/``
    directory and a ``STRUCTURE.md`` file.  Raises ``FileNotFoundError``
    if no such ancestor exists.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "content").is_dir() and (candidate / "STRUCTURE.md").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find the Alteria project root.  "
        "Run the command from inside the repository."
    )


def content_root(project: Path) -> Path:
    return project / "content"


def kinds_root(project: Path) -> Path:
    return project / "content_meta" / "kinds"


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------

def iter_collections(base: Path) -> list[Path]:
    """Return all direct sub-directories of *base*, sorted."""
    return sorted(p for p in base.iterdir() if p.is_dir())


def is_entity_folder(path: Path) -> bool:
    """An entity folder contains an index.yaml."""
    return (path / "index.yaml").is_file()


def is_kind_folder(path: Path) -> bool:
    """A kind folder contains a _kind.yaml."""
    return (path / "_kind.yaml").is_file()


def list_tree(base: Path, indent: int = 0, max_depth: int = 4) -> list[str]:
    """Return a simple text tree of *base* up to *max_depth*."""
    if indent > max_depth:
        return []
    lines: list[str] = []
    prefix = "  " * indent
    for child in sorted(base.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            marker = "[E]" if is_entity_folder(child) else "[C]"
            lines.append(f"{prefix}{marker} {child.name}/")
            lines.extend(list_tree(child, indent + 1, max_depth))
    return lines


def list_kinds_tree(base: Path, indent: int = 0) -> list[str]:
    """Return a simple text tree of the kinds hierarchy."""
    lines: list[str] = []
    prefix = "  " * indent
    for child in sorted(base.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        marker = "[K]" if is_kind_folder(child) else "[ ]"
        lines.append(f"{prefix}{marker} {child.name}/")
        lines.extend(list_kinds_tree(child, indent + 1))
    return lines


# ---------------------------------------------------------------------------
# Move — reference scanning and execution
# ---------------------------------------------------------------------------

@dataclass
class MoveRef:
    """A single reference that needs updating when an entity moves."""
    file: Path          # absolute path to the file containing the reference
    line_no: int        # 1-based line number
    old_text: str       # the exact string that will be replaced
    new_text: str       # what it will be replaced with


@dataclass
class MovePlan:
    """Everything needed to carry out (or preview) a move."""
    old_id: str                     # entity id before move  (e.g. aurethia/places/old/myplace)
    new_id: str                     # entity id after move
    old_dir: Path                   # absolute path of entity folder before move
    new_dir: Path                   # absolute path of entity folder after move
    refs: list[MoveRef] = field(default_factory=list)
    error: str = ""                 # non-empty means the plan is invalid


def plan_move(project: Path, entity_path: str, new_parent: str) -> MovePlan:
    """
    Build a MovePlan for moving *entity_path* under *new_parent*.

    *entity_path* and *new_parent* are both relative to content/.
    The entity slug (folder name) is preserved; only the parent changes.

    Scans the whole project for:
      - ``target: <old-id>`` lines in any index.yaml
      - ``[[<old-id>`` wikilink openings in any index.md

    Does NOT touch bare / partial wikilinks — those are resolved by the
    loader and don't embed the full path.
    """
    content = content_root(project).resolve()

    old_dir = (content / entity_path).resolve()
    if not old_dir.is_dir():
        return MovePlan(
            old_id=entity_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"entity not found: content/{entity_path}",
        )
    if not is_entity_folder(old_dir):
        return MovePlan(
            old_id=entity_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"not an entity folder (no index.yaml): content/{entity_path}",
        )

    slug = old_dir.name
    new_parent_dir = (content / new_parent).resolve()
    new_dir = new_parent_dir / slug
    old_id = entity_path.rstrip("/")
    new_id = str(new_dir.relative_to(content))

    if new_dir == old_dir:
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            error="source and destination are the same",
        )
    if new_dir.exists():
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            error=f"destination already exists: content/{new_id}",
        )

    refs: list[MoveRef] = []

    # --- scan index.yaml files for target: <old_id> ------------------------
    # Match lines like:  "    target: aurethia/places/old/myplace"
    # The id may be followed by end-of-line or a YAML comment.
    target_re = re.compile(
        r"^(?P<prefix>\s*target:\s*)(?P<id>" + re.escape(old_id) + r")(?P<suffix>.*)$"
    )

    for yaml_file in content.rglob("index.yaml"):
        lines = yaml_file.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, start=1):
            m = target_re.match(line)
            if m:
                new_line = m.group("prefix") + new_id + m.group("suffix")
                refs.append(MoveRef(
                    file=yaml_file,
                    line_no=i,
                    old_text=line,
                    new_text=new_line,
                ))

    # --- scan index.md files for [[<old_id> wikilinks ----------------------
    # Matches [[old_id]] and [[old_id|Display]] and [[old_id#anchor...]]
    wikilink_re = re.compile(
        r"\[\[" + re.escape(old_id) + r"(?P<rest>[|\]#])"
    )

    for md_file in content.rglob("index.md"):
        text = md_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            if wikilink_re.search(line):
                new_line = wikilink_re.sub(r"[[" + new_id + r"\g<rest>", line)
                refs.append(MoveRef(
                    file=md_file,
                    line_no=i,
                    old_text=line,
                    new_text=new_line,
                ))

    return MovePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        refs=refs,
    )


def execute_move(plan: MovePlan) -> None:
    """
    Apply a MovePlan:
      1. Rewrite all reference files in place.
      2. Move the entity folder.

    Raises on any filesystem error.  Reference rewrites happen before the
    folder move so that a partial failure is recoverable (the folder is
    still at the old location).
    """
    if plan.error:
        raise ValueError(f"Cannot execute invalid plan: {plan.error}")

    # Group refs by file so we only read/write each file once
    from collections import defaultdict
    by_file: dict[Path, list[MoveRef]] = defaultdict(list)
    for ref in plan.refs:
        by_file[ref.file].append(ref)

    for fpath, file_refs in by_file.items():
        lines = fpath.read_text(encoding="utf-8").splitlines(keepends=True)
        # Sort descending by line number so indices stay valid
        for ref in sorted(file_refs, key=lambda r: r.line_no, reverse=True):
            idx = ref.line_no - 1
            # Preserve original line ending
            ending = ""
            if lines[idx].endswith("\r\n"):
                ending = "\r\n"
            elif lines[idx].endswith("\n"):
                ending = "\n"
            lines[idx] = ref.new_text + ending
        fpath.write_text("".join(lines), encoding="utf-8")

    # Move the folder last
    plan.new_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.old_dir), str(plan.new_dir))


# ---------------------------------------------------------------------------
# Rename — reference scanning and execution
# ---------------------------------------------------------------------------

# Rename reuses MoveRef for individual line edits; the plan shape is similar.

@dataclass
class RenamePlan:
    """Everything needed to carry out (or preview) a rename."""
    old_id: str             # full id before rename  (content/ or kinds/ relative)
    new_id: str             # full id after rename
    old_dir: Path           # absolute path of the folder before rename
    new_dir: Path           # absolute path of the folder after rename
    is_kind: bool           # True if renaming a kind, False if a content entity
    refs: list[MoveRef] = field(default_factory=list)
    error: str = ""


def plan_rename(project: Path, old_path: str, new_slug: str) -> RenamePlan:
    """
    Build a RenamePlan for renaming the slug of a content entity or a kind.

    *old_path* is relative to content/ **or** content_meta/kinds/.
    The tool tries content/ first; if not found there it tries kinds/.

    *new_slug* is the new folder name only (no slashes).

    For a **content entity**, updates:
      - ``target: <old-id>`` in any index.yaml
      - ``[[<old-id>...]]`` full-path wikilinks in any index.md

    For a **kind**, updates:
      - ``kind: <old-slug>`` in any index.yaml  (slug-only field)
      - ``kinds/<old-slug>`` in kind-affinity fields (nativeBeings, traits, …)
      - ``[[kinds/<old-slug>...]]`` wikilinks in any index.md
    """
    if "/" in new_slug:
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=Path(), new_dir=Path(),
            is_kind=False,
            error="new-slug must be a plain folder name with no slashes",
        )

    content = content_root(project).resolve()
    kinds = kinds_root(project).resolve()

    # Resolve old_dir — try content first, then kinds
    is_kind = False
    old_dir = (content / old_path).resolve()
    if not old_dir.is_dir():
        old_dir = (kinds / old_path).resolve()
        if not old_dir.is_dir():
            return RenamePlan(
                old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
                is_kind=False,
                error=f"not found under content/ or content_meta/kinds/: {old_path}",
            )
        is_kind = True
        if not is_kind_folder(old_dir):
            return RenamePlan(
                old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
                is_kind=True,
                error=f"not a kind folder (no _kind.yaml): content_meta/kinds/{old_path}",
            )
    else:
        if not is_entity_folder(old_dir):
            return RenamePlan(
                old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
                is_kind=False,
                error=f"not an entity folder (no index.yaml): content/{old_path}",
            )

    old_slug = old_dir.name
    if old_slug == new_slug:
        return RenamePlan(
            old_id=old_path, new_id=old_path, old_dir=old_dir, new_dir=old_dir,
            is_kind=is_kind,
            error="old and new slug are the same",
        )

    new_dir = old_dir.parent / new_slug
    if new_dir.exists():
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=old_dir, new_dir=new_dir,
            is_kind=is_kind,
            error=f"destination already exists: {new_dir.relative_to(project)}",
        )

    refs: list[MoveRef] = []

    if is_kind:
        old_id = str(old_dir.relative_to(kinds))
        new_id = str(new_dir.relative_to(kinds.parent / "kinds"))

        # 1. kind: <old-slug>  in any index.yaml
        #    The kind: field stores only the leaf slug, not the full path.
        kind_field_re = re.compile(
            r"^(?P<prefix>kind:\s*)(?P<slug>" + re.escape(old_slug) + r")(?P<suffix>.*)$"
        )
        for yaml_file in content.rglob("index.yaml"):
            lines = yaml_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                m = kind_field_re.match(line)
                if m:
                    new_line = m.group("prefix") + new_slug + m.group("suffix")
                    refs.append(MoveRef(yaml_file, i, line, new_line))

        # 2. kinds/<old-slug>  in kind-affinity fields of any index.yaml
        #    Matches  kinds/old-slug  as a whole token (not mid-path)
        affinity_re = re.compile(
            r"(?P<pre>kinds/)(?P<slug>" + re.escape(old_slug) + r")(?P<post>[\s,\]\"']|$)"
        )
        for yaml_file in content.rglob("index.yaml"):
            lines = yaml_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if affinity_re.search(line):
                    new_line = affinity_re.sub(
                        r"\g<pre>" + new_slug + r"\g<post>", line
                    )
                    refs.append(MoveRef(yaml_file, i, line, new_line))

        # 3. [[kinds/<old-slug>...]] wikilinks in any index.md
        wikilink_re = re.compile(
            r"\[\[kinds/" + re.escape(old_slug) + r"(?P<rest>[|\]#])"
        )
        for md_file in content.rglob("index.md"):
            lines = md_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if wikilink_re.search(line):
                    new_line = wikilink_re.sub(
                        r"[[kinds/" + new_slug + r"\g<rest>", line
                    )
                    refs.append(MoveRef(md_file, i, line, new_line))

    else:
        old_id = str(old_dir.relative_to(content))
        new_id = str(new_dir.relative_to(content))

        # 1. target: <old-id>  in any index.yaml
        target_re = re.compile(
            r"^(?P<prefix>\s*target:\s*)(?P<id>" + re.escape(old_id) + r")(?P<suffix>.*)$"
        )
        for yaml_file in content.rglob("index.yaml"):
            lines = yaml_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                m = target_re.match(line)
                if m:
                    new_line = m.group("prefix") + new_id + m.group("suffix")
                    refs.append(MoveRef(yaml_file, i, line, new_line))

        # 2. [[<old-id>...]] full-path wikilinks in any index.md
        wikilink_re = re.compile(
            r"\[\[" + re.escape(old_id) + r"(?P<rest>[|\]#])"
        )
        for md_file in content.rglob("index.md"):
            lines = md_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if wikilink_re.search(line):
                    new_line = wikilink_re.sub(r"[[" + new_id + r"\g<rest>", line)
                    refs.append(MoveRef(md_file, i, line, new_line))

    return RenamePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        is_kind=is_kind,
        refs=refs,
    )


def execute_rename(plan: RenamePlan) -> None:
    """
    Apply a RenamePlan: rewrite all references then rename the folder.
    References are written first so a crash leaves the repo consistent
    (folder still at old location, refs already updated — re-runnable).
    """
    if plan.error:
        raise ValueError(f"Cannot execute invalid plan: {plan.error}")

    from collections import defaultdict
    by_file: dict[Path, list[MoveRef]] = defaultdict(list)
    for ref in plan.refs:
        by_file[ref.file].append(ref)

    for fpath, file_refs in by_file.items():
        lines = fpath.read_text(encoding="utf-8").splitlines(keepends=True)
        for ref in sorted(file_refs, key=lambda r: r.line_no, reverse=True):
            idx = ref.line_no - 1
            ending = ""
            if lines[idx].endswith("\r\n"):
                ending = "\r\n"
            elif lines[idx].endswith("\n"):
                ending = "\n"
            lines[idx] = ref.new_text + ending
        fpath.write_text("".join(lines), encoding="utf-8")

    shutil.move(str(plan.old_dir), str(plan.new_dir))
