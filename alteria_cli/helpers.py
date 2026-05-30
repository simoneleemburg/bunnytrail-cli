"""
helpers.py — shared utilities for locating project roots and reading
content/content_meta structure without loading every file.

All entities, collections, and kinds are now authored as a single
Markdown file with YAML frontmatter:

    content/<...path>/<slug>/index.md       — entity (frontmatter + prose)
    content/<...path>/<slug>/_collection.md — collection marker
    content_meta/kinds/<...path>/<kind>/_kind.md — kind marker

The legacy split layout (`index.yaml` + `index.md`) is no longer
supported.
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


def assets_root(project: Path) -> Path:
    return project / "assets"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

# A frontmatter block opens with `---` on the first line of the file
# and closes with another `---` on its own line later. The closing
# fence's preceding newline ends the YAML; the body starts after it.
_FRONTMATTER_OPEN = re.compile(r"\A---[ \t]*\r?\n")
_FRONTMATTER_CLOSE = re.compile(r"^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a Markdown document into ``(frontmatter, body)``.

    Returns ``(None, text)`` when no opening fence is present, or when
    a closing fence cannot be found later in the file (we treat that
    case as plain markdown — the leading ``---`` is a horizontal rule).

    The frontmatter string excludes the fences themselves; the body
    excludes the closing fence and the newline that follows it.
    """
    open_match = _FRONTMATTER_OPEN.match(text)
    if not open_match:
        return None, text
    after = text[open_match.end():]
    close_match = _FRONTMATTER_CLOSE.search(after)
    if not close_match:
        return None, text
    frontmatter = after[: close_match.start()]
    body = after[close_match.end():]
    return frontmatter, body


def frontmatter_lines(text: str) -> list[str]:
    """Return the frontmatter portion of *text* as a list of lines.

    Lines do not include trailing newlines. Returns an empty list
    when the file has no frontmatter — callers that depend on
    metadata should handle that as "no fields".
    """
    fm, _ = split_frontmatter(text)
    if fm is None:
        return []
    return fm.splitlines()


def write_frontmatter_md(
    path: Path,
    frontmatter: str,
    body: str = "",
) -> None:
    """Write a Markdown file with the given frontmatter and body.

    *frontmatter* must already be valid YAML (no leading/trailing
    fences). It will be inserted between ``---`` fences. A blank line
    separates the closing fence from the body when *body* is
    non-empty.
    """
    fm = frontmatter.rstrip("\n")
    body_text = body.lstrip("\n")
    sep = "\n\n" if body_text else "\n"
    path.write_text(f"---\n{fm}\n---{sep}{body_text}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------

def iter_collections(base: Path) -> list[Path]:
    """Return all direct sub-directories of *base*, sorted."""
    return sorted(p for p in base.iterdir() if p.is_dir())


def is_entity_folder(path: Path) -> bool:
    """An entity folder contains an ``index.md`` with frontmatter."""
    md = path / "index.md"
    if not md.is_file():
        return False
    try:
        head = md.open("r", encoding="utf-8").read(8)
    except OSError:
        return False
    # An entity must declare metadata; that means a frontmatter fence.
    return head.startswith("---\n") or head.startswith("---\r\n") or head.startswith("---")


def is_kind_folder(path: Path) -> bool:
    """A kind folder is identified by either:

      - the presence of a ``_kind.md`` file (with or without
        frontmatter — the folder existing is enough), or
      - simply being a sub-directory of ``content_meta/kinds/``.

    Callers that need to distinguish between "registered with a
    `_kind.md`" and "implicit" should check for the file directly.
    """
    return (path / "_kind.md").is_file()


def is_collection_folder(path: Path) -> bool:
    """A collection folder carries a ``_collection.md`` marker."""
    return (path / "_collection.md").is_file()


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
# File enumeration
# ---------------------------------------------------------------------------

def iter_entity_md_files(content: Path):
    """Yield every ``index.md`` that marks an entity folder."""
    for md_file in content.rglob("index.md"):
        if is_entity_folder(md_file.parent):
            yield md_file


def iter_kind_md_files(kinds: Path):
    """Yield every ``_kind.md`` under the kinds tree."""
    for md_file in kinds.rglob("_kind.md"):
        yield md_file


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
    old_id: str                     # entity/kind id before move
    new_id: str                     # entity/kind id after move
    old_dir: Path                   # absolute path of folder before move
    new_dir: Path                   # absolute path of folder after move
    is_kind: bool = False           # True when moving a kind under content_meta/kinds/
    refs: list[MoveRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # non-fatal scanner findings
    error: str = ""                 # non-empty means the plan is invalid


def _scan_entity_refs(
    project: Path,
    old_id: str,
    new_id: str,
) -> tuple[list[MoveRef], list[str]]:
    """Find every reference to *old_id* across the project and produce
    rewrite operations targeting *new_id*.

    Returns ``(refs, warnings)``. Wikilinks are resolved using the
    full WIKILINKS.md contract — bare slugs, suffix paths, anchored
    and labelled forms, full paths, and cluster-local + universal
    fallback. The replacement form is chosen by
    :func:`wikilinks.preferred_form` so links stay as short as
    safely possible after the rewrite.

    References are looked for in:
      * ``target: <old-id>`` lines in any entity's ``index.md``
        frontmatter.
      * ``[[<link>]]`` tokens that resolve to *old_id* in any
        entity's ``index.md`` body, **scoped to the rendering page's
        cluster**.
      * ``href="/<old-id>...`` links in any SVG file under
        ``assets/`` (still rewritten as full ids — SVG hrefs aren't
        cluster-aware).
    """
    # Local import to avoid a circular import at module load.
    from .wikilinks import (
        build_index,
        iter_wikilinks,
        preferred_form,
        render_wikilink,
        resolve,
        scope_of,
    )

    content = content_root(project).resolve()
    refs: list[MoveRef] = []
    warnings: list[str] = []

    # Build the index against the pre-move state of the repo, plus a
    # post-move snapshot so preferred_form sees the new id when picking
    # a replacement form.
    index = build_index(project)
    post_index = index.with_renamed_ids({old_id: new_id})

    target_re = re.compile(
        r"^(?P<prefix>\s*target:\s*)(?P<id>" + re.escape(old_id) + r")(?P<suffix>.*)$"
    )

    for md_file in iter_entity_md_files(content):
        page_id = str(md_file.parent.relative_to(content))
        page_cluster = scope_of(page_id, index)
        # Frontmatter region is needed so we don't try to resolve
        # wikilinks inside structured YAML.
        text = md_file.read_text(encoding="utf-8")
        fm, _body = split_frontmatter(text)
        fm_line_count = 0
        if fm is not None:
            # +2 for the opening and closing fence lines themselves.
            fm_line_count = len(fm.splitlines()) + 2

        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            in_frontmatter = i <= fm_line_count

            # ---- target: rewrites only inside frontmatter ----------
            if in_frontmatter:
                m = target_re.match(line)
                if m:
                    new_line = m.group("prefix") + new_id + m.group("suffix")
                    refs.append(MoveRef(md_file, i, line, new_line))
                    continue

            # ---- wikilink rewrites only in the body ---------------
            if in_frontmatter:
                continue

            new_line, rewrote, warns = _rewrite_wikilinks_on_line(
                line=line,
                page_id=page_id,
                page_cluster=page_cluster,
                index=index,
                post_index=post_index,
                old_id=old_id,
                new_id=new_id,
            )
            warnings.extend(warns)
            if rewrote:
                refs.append(MoveRef(md_file, i, line, new_line))

    # SVG href links use a leading slash: <a href="/foundation/fabric/mundus" />
    svg_href_re = re.compile(
        r'(?P<pre>href=")/' + re.escape(old_id) + r'(?P<post>["/\s])'
    )
    assets = assets_root(project)
    if assets.is_dir():
        for svg_file in assets.rglob("*.svg"):
            lines = svg_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if svg_href_re.search(line):
                    new_line = svg_href_re.sub(
                        r"\g<pre>/" + new_id + r"\g<post>", line
                    )
                    refs.append(MoveRef(svg_file, i, line, new_line))

    return refs, warnings


def _rewrite_wikilinks_on_line(
    *,
    line: str,
    page_id: str,
    page_cluster: str | None,
    index,  # WikilinkIndex (pre-move, for resolution)
    post_index,  # WikilinkIndex (post-move, for preferred_form)
    old_id: str,
    new_id: str,
) -> tuple[str, bool, list[str]]:
    """Rewrite every ``[[…]]`` on *line* whose resolved target is *old_id*.

    Returns ``(new_line, rewrote_anything, warnings)``. The
    replacement form is selected by
    :func:`wikilinks.preferred_form` from the perspective of the
    rendering page's cluster — bare slugs stay bare where possible,
    cross-cluster targets get full ids, and so on.

    Anchors and labels on the source link are preserved verbatim.
    """
    from .wikilinks import (
        WIKILINK_RE,
        iter_wikilinks,
        preferred_form,
        render_wikilink,
        resolve,
    )

    if "[[" not in line:
        return line, False, []

    # Same-page wikilinks on the moved entity's OWN index.md don't
    # need rewriting — those are anchors local to the file. We still
    # rewrite [[old-id#anchor]] from *other* pages.
    line_stripped = line.strip()
    is_directive_line = (
        line_stripped.startswith("[[collection:")
        and line_stripped.endswith("]]")
        and line_stripped.count("[[") == 1
    )

    warnings: list[str] = []
    pieces: list[str] = []
    cursor = 0
    rewrote = False

    for m in WIKILINK_RE.finditer(line):
        inner = m.group("inner")
        # Inline replace requires the same parse/resolve as iter_wikilinks.
        from .wikilinks import parse_wikilink
        allow_collection = is_directive_line and m.group(0) == line_stripped
        parsed = parse_wikilink(inner, allow_collection=allow_collection)
        if parsed.kind in ("literal", "same-page"):
            continue
        # Note: parsed.kind == "lang" tokens may still resolve as bare
        # slugs (see WIKILINKS.md §"Language tags"). Defer to resolve
        # for the final word.

        res = resolve(parsed, page_cluster, index)
        if res.status == "resolved" and res.entity_id == old_id:
            new_path = preferred_form(new_id, page_cluster, post_index)
            if parsed.kind == "collection":
                # Whole-line directive — keep the directive prefix.
                replacement = f"[[collection:{new_path}]]"
            else:
                replacement = render_wikilink(
                    new_path, parsed.anchor, parsed.label
                )
            pieces.append(line[cursor:m.start()])
            pieces.append(replacement)
            cursor = m.end()
            rewrote = True
        elif res.status == "ambiguous" and old_id in res.candidates:
            warnings.append(
                f"{page_id}: [[{inner}]] is already ambiguous "
                f"(matches {', '.join(res.candidates)}); skipping rewrite"
            )

    if not rewrote:
        return line, False, warnings

    pieces.append(line[cursor:])
    new_line = "".join(pieces)
    # Don't report no-op rewrites (bare slug -> bare slug when the
    # preferred form is unchanged after the move).
    if new_line == line:
        return line, False, warnings
    return new_line, True, warnings


def plan_move(project: Path, entity_path: str, new_parent: str) -> MovePlan:
    """
    Build a MovePlan for moving *entity_path* under *new_parent*.

    *entity_path* and *new_parent* are both relative to content/.
    The entity (or collection) slug is preserved; only the parent changes.

    If *entity_path* points at a collection (folder with _collection.md),
    the move cascades to all descendant entity and collection IDs and
    rewrites every reference to them.

    Scans the whole project for ``target:`` and full-path wikilinks
    referring to the old id, plus SVG ``href`` links under ``assets/``.
    """
    content = content_root(project).resolve()

    old_dir = (content / entity_path).resolve()
    if not old_dir.is_dir():
        return MovePlan(
            old_id=entity_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"entity not found: content/{entity_path}",
        )

    # Dispatch to collection-move if this folder is a collection.
    if is_collection_folder(old_dir):
        return plan_move_collection(project, entity_path, new_parent)

    if not is_entity_folder(old_dir):
        return MovePlan(
            old_id=entity_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"not an entity folder or collection (no index.md with frontmatter or _collection.md): content/{entity_path}",
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

    refs, warnings = _scan_entity_refs(project, old_id, new_id)

    return MovePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        refs=refs,
        warnings=warnings,
    )


def plan_move_collection(project: Path, collection_path: str, new_parent: str) -> MovePlan:
    """
    Build a MovePlan for moving a whole collection under *new_parent*.

    *collection_path* and *new_parent* are both relative to content/.
    The collection slug is preserved; only the parent changes.

    Cascades the move to all descendant entity and collection IDs and
    rewrites every reference (``target:``, full-path wikilinks, SVG hrefs)
    in a single pass per line, so lines mentioning multiple descendants
    are rewritten in one shot.

    The collection cannot be moved into itself or any of its own
    descendants — that's caught explicitly.
    """
    content = content_root(project).resolve()

    old_dir = (content / collection_path).resolve()
    if not old_dir.is_dir():
        return MovePlan(
            old_id=collection_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"collection not found: content/{collection_path}",
        )
    if not is_collection_folder(old_dir):
        return MovePlan(
            old_id=collection_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            error=f"not a collection folder (no _collection.md): content/{collection_path}",
        )

    slug = old_dir.name
    new_parent_dir = (content / new_parent).resolve()
    new_dir = new_parent_dir / slug
    old_id = collection_path.rstrip("/")
    new_id = str(new_dir.relative_to(content))

    if new_dir == old_dir:
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            error="source and destination are the same",
        )
    # Prevent moving a collection inside itself.
    try:
        new_parent_dir.relative_to(old_dir)
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            error="cannot move a collection into itself or any of its descendants",
        )
    except ValueError:
        pass
    if new_dir.exists():
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            error=f"destination already exists: content/{new_id}",
        )

    refs, warnings = _scan_collection_refs(project, old_id, new_id)

    return MovePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        refs=refs,
        warnings=warnings,
    )


def plan_move_kind(project: Path, kind_path: str, new_parent: str) -> MovePlan:
    """
    Build a MovePlan for moving a kind folder under a new parent within
    content_meta/kinds/.

    *kind_path* and *new_parent* are both relative to content_meta/kinds/.
    The kind slug (folder name) is preserved; only the parent changes.

    Because wikilinks and kind: fields reference kinds by leaf slug only
    (not by their full path), **no reference files need to be rewritten**
    when a kind moves.  The plan will always have an empty refs list; the
    only action taken is the physical folder move.
    """
    kinds = kinds_root(project).resolve()

    old_dir = (kinds / kind_path).resolve()
    if not old_dir.is_dir():
        return MovePlan(
            old_id=kind_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            is_kind=True,
            error=f"kind not found: content_meta/kinds/{kind_path}",
        )

    slug = old_dir.name
    new_parent_dir = (kinds / new_parent).resolve()
    new_dir = new_parent_dir / slug
    old_id = kind_path.rstrip("/")
    new_id = str(new_dir.relative_to(kinds))

    if new_dir == old_dir:
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            is_kind=True,
            error="source and destination are the same",
        )
    if new_dir.exists():
        return MovePlan(
            old_id=old_id, new_id=new_id, old_dir=old_dir, new_dir=new_dir,
            is_kind=True,
            error=f"destination already exists: content_meta/kinds/{new_id}",
        )

    # No references to rewrite — slug-based references are unaffected by
    # a structural move within the kinds tree.
    return MovePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        is_kind=True,
        refs=[],
    )


def execute_move(plan: MovePlan) -> None:
    """
    Apply a MovePlan:
      1. Rewrite all reference files in place (entity moves only).
      2. Move the folder (entity or kind).

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
# Crosslink — insert wikilinks into an article's prose
# ---------------------------------------------------------------------------

@dataclass
class CrosslinkEdit:
    """A single line replacement that adds one or more wikilinks."""
    line_no: int        # 1-based
    old_text: str       # original line (without newline)
    new_text: str       # replacement line (without newline)


@dataclass
class CrosslinkPlan:
    """Everything needed to carry out (or preview) a crosslink pass."""
    article_id: str             # entity id of the article being edited
    md_file: Path               # absolute path to its index.md
    namespace: str              # namespace path relative to content/
    edits: list[CrosslinkEdit] = field(default_factory=list)
    error: str = ""             # non-empty means the plan is invalid


def _parse_yaml_field(lines: list[str], field: str) -> str:
    """Return the value of a bare scalar YAML field from a list of lines.

    Handles simple ``field: value`` lines and block-scalar headers
    (``field: >-`` / ``field: |``) by concatenating the indented body.
    Returns an empty string if the field is absent.

    *lines* should be the frontmatter lines only (use
    :func:`frontmatter_lines`); passing the full file is also safe but
    risks colliding with body content that happens to start with the
    field name.
    """
    prefix = f"{field}:"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        rest = stripped[len(prefix):].strip()
        # Block scalar — collect indented continuation lines
        if rest in (">-", ">", "|", "|-"):
            parts: list[str] = []
            for j in range(i + 1, len(lines)):
                cont = lines[j]
                if cont and (cont[0] == " " or cont[0] == "\t"):
                    parts.append(cont.strip())
                else:
                    break
            return " ".join(parts)
        # Inline value (may be quoted)
        return rest.strip("\"'")
    return ""


def collect_namespace_entities(project: Path, namespace_path: str) -> list[tuple[str, str]]:
    """Return ``(display_name, entity_id)`` for every entity under namespace_path.

    *namespace_path* is relative to ``content/``.
    Entities with an empty or missing ``name:`` field are skipped.
    """
    content = content_root(project).resolve()
    ns_dir = content / namespace_path
    if not ns_dir.is_dir():
        return []

    results: list[tuple[str, str]] = []
    for md_file in sorted(iter_entity_md_files(ns_dir)):
        entity_dir = md_file.parent
        text = md_file.read_text(encoding="utf-8")
        fm_lines = frontmatter_lines(text)
        name = _parse_yaml_field(fm_lines, "name")
        if not name:
            continue
        entity_id = str(entity_dir.relative_to(content))
        results.append((name, entity_id))
    return results


def collect_all_kinds(project: Path) -> list[tuple[str, str]]:
    """Return ``(match_text, link_target)`` for every kind's singular and plural.

    *link_target* is ``kinds/<leaf-slug>`` (the slug used in wikilinks).
    Both the singular and plural of each kind emit separate entries pointing
    at the same target.  Entries with empty names are skipped.
    """
    kinds = kinds_root(project).resolve()
    results: list[tuple[str, str]] = []
    for kind_md in sorted(iter_kind_md_files(kinds)):
        kind_dir = kind_md.parent
        slug = kind_dir.name
        target = f"kinds/{slug}"
        text = kind_md.read_text(encoding="utf-8")
        fm_lines = frontmatter_lines(text)
        singular = _parse_yaml_field(fm_lines, "singular")
        plural = _parse_yaml_field(fm_lines, "plural")
        if singular:
            results.append((singular, target))
        if plural and plural != singular:
            results.append((plural, target))
    return results


def _wikilink_spans(line: str) -> list[tuple[int, int]]:
    """Return a list of ``(start, end)`` character ranges that are already
    inside a ``[[...]]`` wikilink on *line*.  These positions must not be
    re-linked.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(line) - 1:
        if line[i] == "[" and line[i + 1] == "[":
            start = i
            close = line.find("]]", i + 2)
            if close == -1:
                break
            spans.append((start, close + 2))
            i = close + 2
        else:
            i += 1
    return spans


def _is_in_span(pos: int, length: int, spans: list[tuple[int, int]]) -> bool:
    """Return True if the range [pos, pos+length) overlaps any span."""
    end = pos + length
    return any(s < end and e > pos for s, e in spans)


def plan_crosslink(project: Path, article_path: str, namespace_path: str) -> CrosslinkPlan:
    """Build a CrosslinkPlan for inserting wikilinks into *article_path*.

    Candidate link targets are:

    1. All entities found recursively under ``content/<namespace_path>``,
       matched by their ``name:`` field (exact, whole-word).
    2. All kinds in ``content_meta/kinds/``, matched by their ``singular:``
       and ``plural:`` fields (exact, whole-word).

    Rules:
    - Text already inside ``[[...]]`` is never re-linked.
    - Text inside the article's frontmatter block is never re-linked
      (we only modify body prose).
    - Only the **first** occurrence of each candidate name in the file
      is linked.
    - A whole-word boundary (``\\b``) is required on both sides of the
      match to avoid partial-word replacements.
    - The wikilink is written as ``[[target|match_text]]`` to preserve the
      original capitalisation/form.  Where the target's leaf slug equals
      the match text, a bare ``[[target]]`` is used instead.
    """
    content = content_root(project).resolve()
    article_dir = (content / article_path).resolve()

    if not article_dir.is_dir():
        return CrosslinkPlan(
            article_id=article_path, md_file=article_dir / "index.md",
            namespace=namespace_path,
            error=f"article not found: content/{article_path}",
        )
    if not is_entity_folder(article_dir):
        return CrosslinkPlan(
            article_id=article_path, md_file=article_dir / "index.md",
            namespace=namespace_path,
            error=f"not an entity folder (no index.md with frontmatter): content/{article_path}",
        )

    md_file = article_dir / "index.md"
    if not md_file.is_file():
        return CrosslinkPlan(
            article_id=article_path, md_file=md_file,
            namespace=namespace_path,
            error=f"article has no index.md: content/{article_path}",
        )

    ns_dir = content / namespace_path
    if not ns_dir.is_dir():
        return CrosslinkPlan(
            article_id=article_path, md_file=md_file,
            namespace=namespace_path,
            error=f"namespace not found: content/{namespace_path}",
        )

    # ------------------------------------------------------------------
    # Build candidate list: (match_text, wikilink_target, is_kind)
    # Longer names first so "Mundus Frame" is tested before "Mundus".
    # Exclude the article itself from the candidate pool.
    # ------------------------------------------------------------------
    article_id_norm = article_path.rstrip("/")
    candidates: list[tuple[str, str, bool]] = []

    for name, entity_id in collect_namespace_entities(project, namespace_path):
        if entity_id == article_id_norm:
            continue
        candidates.append((name, entity_id, False))

    for match_text, kind_target in collect_all_kinds(project):
        candidates.append((match_text, kind_target, True))

    # Sort longest-first to prefer specific matches over shorter substrings
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    # ------------------------------------------------------------------
    # Build a wikilink index and figure out the article's cluster so
    # we can emit short forms when they resolve unambiguously.
    # ------------------------------------------------------------------
    from .wikilinks import build_index, preferred_form, scope_of
    index = build_index(project)
    article_cluster = scope_of(article_id_norm, index)

    # ------------------------------------------------------------------
    # Compile patterns (whole-word, case-sensitive)
    # ------------------------------------------------------------------
    patterns: list[tuple[re.Pattern[str], str, str, bool]] = []
    seen_texts: set[str] = set()
    for match_text, target, is_kind_target in candidates:
        if match_text in seen_texts:
            continue
        seen_texts.add(match_text)
        pat = re.compile(r"\b" + re.escape(match_text) + r"\b")
        patterns.append((pat, match_text, target, is_kind_target))

    # ------------------------------------------------------------------
    # Walk the article line-by-line; build edits.
    # Skip lines inside the frontmatter block — we only crosslink the
    # body prose.  Frontmatter occupies the first contiguous range
    # bounded by ``---`` lines (open + close).
    # ------------------------------------------------------------------
    full_text = md_file.read_text(encoding="utf-8")
    lines = full_text.splitlines()

    body_start_idx = 0  # 0-based; lines below this are body
    if lines and lines[0].rstrip() == "---":
        # Find the closing fence
        for j in range(1, len(lines)):
            if lines[j].rstrip() == "---":
                body_start_idx = j + 1
                break
        else:
            # No closing fence — treat the whole file as body.
            body_start_idx = 0

    # Track which candidate names have already been linked (first-only rule)
    linked_texts: set[str] = set()

    edits: list[CrosslinkEdit] = []

    for line_idx, line in enumerate(lines):
        if line_idx < body_start_idx:
            continue
        new_line = line

        for pat, match_text, target, is_kind_target in patterns:
            if match_text in linked_texts:
                continue

            # Search new_line (which may already contain earlier replacements
            # on this line) so that position lookups and span checks are always
            # consistent with the current state of the string.
            spans = _wikilink_spans(new_line)
            m = pat.search(new_line)
            if m is None:
                continue

            start = m.start()
            length = len(match_text)

            # Skip if this occurrence sits inside an existing wikilink
            if _is_in_span(start, length, spans):
                continue

            # Choose the shortest valid wikilink path.
            # Kind targets (kinds/<slug>) are always written as-is —
            # they live outside the cluster system and the registry
            # handles resolution.
            if is_kind_target:
                link_path = target
            else:
                link_path = preferred_form(target, article_cluster, index)

            link_leaf = link_path.split("/")[-1]
            if link_leaf == match_text:
                replacement = f"[[{link_path}]]"
            else:
                replacement = f"[[{link_path}|{match_text}]]"

            new_line = new_line[:start] + replacement + new_line[start + length:]

            linked_texts.add(match_text)

        if new_line != line:
            edits.append(CrosslinkEdit(
                line_no=line_idx + 1,
                old_text=line,
                new_text=new_line,
            ))

    return CrosslinkPlan(
        article_id=article_path,
        md_file=md_file,
        namespace=namespace_path,
        edits=edits,
    )


def execute_crosslink(plan: CrosslinkPlan) -> None:
    """Apply a CrosslinkPlan: rewrite the article's index.md in place."""
    if plan.error:
        raise ValueError(f"Cannot execute invalid plan: {plan.error}")

    lines = plan.md_file.read_text(encoding="utf-8").splitlines(keepends=True)
    for edit in sorted(plan.edits, key=lambda e: e.line_no, reverse=True):
        idx = edit.line_no - 1
        ending = ""
        if lines[idx].endswith("\r\n"):
            ending = "\r\n"
        elif lines[idx].endswith("\n"):
            ending = "\n"
        lines[idx] = edit.new_text + ending
    plan.md_file.write_text("".join(lines), encoding="utf-8")


def plan_crosslink_folder(
    project: Path,
    target_path: str,
    namespace_path: str,
) -> tuple[list[CrosslinkPlan], str]:
    """Build a CrosslinkPlan for every entity reachable from *target_path*.

    *target_path* is relative to ``content/`` and may be either:

      * a single entity folder (containing ``index.md`` with
        frontmatter) — yields exactly one plan; or
      * any other directory under ``content/`` — walks it
        recursively and yields one plan per entity found.

    *namespace_path* is also relative to ``content/`` and scopes
    which entities are candidates for linking, exactly as in
    :func:`plan_crosslink`.

    Returns ``(plans, error)``. When *error* is non-empty the plans
    list is empty and nothing should be executed. Per-entity plans
    that themselves carry an error (rare — only when an entity's
    file disappears between the walk and the plan) are kept in the
    list so callers can show them; ``execute_crosslink`` will
    refuse to apply those.

    Plans with no edits are still included so callers can print
    "scanned N entities, M to update".
    """
    content = content_root(project).resolve()
    target_dir = (content / target_path).resolve()

    if not target_dir.is_dir():
        return [], f"not a directory: content/{target_path}"

    ns_dir = (content / namespace_path).resolve()
    if not ns_dir.is_dir():
        return [], f"namespace not found: content/{namespace_path}"

    # Single-entity case: keep the simple plan_crosslink path.
    if is_entity_folder(target_dir):
        return [plan_crosslink(project, target_path, namespace_path)], ""

    # Folder case: walk and plan each descendant entity.
    plans: list[CrosslinkPlan] = []
    for md_file in sorted(iter_entity_md_files(target_dir)):
        entity_id = str(md_file.parent.relative_to(content))
        plans.append(plan_crosslink(project, entity_id, namespace_path))

    if not plans:
        return [], f"no entities found under content/{target_path}"

    return plans, ""


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
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def plan_rename(project: Path, old_path: str, new_slug: str) -> RenamePlan:
    """
    Build a RenamePlan for renaming the slug of a content entity, collection, or kind.

    *old_path* is relative to content/ **or** content_meta/kinds/.
    The tool tries content/ first; if not found there it tries kinds/.

    *new_slug* is the new folder name only (no slashes).

    For a **content entity**, updates:
      - ``target: <old-id>`` in any entity's index.md (within frontmatter)
      - ``[[<old-id>...]]`` full-path wikilinks in any entity's index.md body
      - SVG ``href="/<old-id>..."`` links under ``assets/``

    For a **collection**, renames the folder and cascades the rename to all
    descendant entity and collection IDs. Updates all references to descendants.

    For a **kind**, updates:
      - ``kind: <old-slug>`` in any entity's index.md frontmatter
      - ``kinds/<old-slug>`` in kind-affinity fields of any entity's
        index.md frontmatter
      - ``[[kinds/<old-slug>...]]`` wikilinks in any entity's index.md body
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
    else:
        # Check if it's a collection (has _collection.md)
        if is_collection_folder(old_dir):
            return plan_rename_collection(project, old_path, new_slug)
        
        if not is_entity_folder(old_dir):
            return RenamePlan(
                old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
                is_kind=False,
                error=f"not an entity folder or collection (no index.md with frontmatter or _collection.md): content/{old_path}",
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
        new_id = str(new_dir.relative_to(kinds))

        # 1. kind: <old-slug>  in any entity's index.md frontmatter.
        #    The kind: field stores only the leaf slug, not the full path.
        kind_field_re = re.compile(
            r"^(?P<prefix>kind:\s*)(?P<slug>" + re.escape(old_slug) + r")(?P<suffix>.*)$"
        )
        # 2. kinds/<old-slug>  in kind-affinity fields of any entity's
        #    index.md frontmatter.  Matches  kinds/old-slug  as a whole
        #    token (not mid-path).
        affinity_re = re.compile(
            r"(?P<pre>kinds/)(?P<slug>" + re.escape(old_slug) + r")(?P<post>[\s,\]\"']|$)"
        )
        # 3. [[kinds/<old-slug>...]] wikilinks in any entity's index.md body.
        wikilink_re = re.compile(
            r"\[\[kinds/" + re.escape(old_slug) + r"(?P<rest>[|\]#])"
        )

        for md_file in iter_entity_md_files(content):
            lines = md_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                m = kind_field_re.match(line)
                if m:
                    new_line = m.group("prefix") + new_slug + m.group("suffix")
                    refs.append(MoveRef(md_file, i, line, new_line))
                    continue
                if affinity_re.search(line):
                    new_line = affinity_re.sub(
                        r"\g<pre>" + new_slug + r"\g<post>", line
                    )
                    refs.append(MoveRef(md_file, i, line, new_line))
                    continue
                if wikilink_re.search(line):
                    new_line = wikilink_re.sub(
                        r"[[kinds/" + new_slug + r"\g<rest>", line
                    )
                    refs.append(MoveRef(md_file, i, line, new_line))

    else:
        old_id = str(old_dir.relative_to(content))
        new_id = str(new_dir.relative_to(content))
        refs, warnings = _scan_entity_refs(project, old_id, new_id)

    if not is_kind:
        return RenamePlan(
            old_id=old_id,
            new_id=new_id,
            old_dir=old_dir,
            new_dir=new_dir,
            is_kind=is_kind,
            refs=refs,
            warnings=warnings,
        )
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


# ---------------------------------------------------------------------------
# Collection rename — cascading entity ID updates
# ---------------------------------------------------------------------------


def plan_rename_collection(project: Path, old_path: str, new_slug: str) -> RenamePlan:
    """
    Build a RenamePlan for renaming a collection folder.
    
    *old_path* is relative to content/ and must point to a collection
    (folder with _collection.md).
    
    *new_slug* is the new folder name only (no slashes).
    
    This command renames the collection folder and cascades the rename to all
    descendant entity and collection IDs. It also updates the collection's
    _collection.md title field if present.
    
    For each line touched, applies all needed replacements at once:
      - ``target: <old-collection-id>/<...>`` in any entity's index.md
      - ``[[<old-collection-id>/<...>...]]`` full-path wikilinks in any
        entity's index.md body
      - SVG ``href="/<old-collection-id>/<...>..."`` links under ``assets/``
    
    Each line is rewritten at most once even if it contains multiple
    matches, so all replacements survive.
    """
    if "/" in new_slug:
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=Path(), new_dir=Path(),
            is_kind=False,
            error="new-slug must be a plain folder name with no slashes",
        )
    
    content = content_root(project).resolve()
    
    old_dir = (content / old_path).resolve()
    if not old_dir.is_dir():
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            is_kind=False,
            error=f"not found under content/: {old_path}",
        )
    
    if not is_collection_folder(old_dir):
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=old_dir, new_dir=old_dir,
            is_kind=False,
            error=f"not a collection folder (no _collection.md): content/{old_path}",
        )
    
    old_slug = old_dir.name
    if old_slug == new_slug:
        return RenamePlan(
            old_id=old_path, new_id=old_path, old_dir=old_dir, new_dir=old_dir,
            is_kind=False,
            error="old and new slug are the same",
        )
    
    new_dir = old_dir.parent / new_slug
    if new_dir.exists():
        return RenamePlan(
            old_id=old_path, new_id="", old_dir=old_dir, new_dir=new_dir,
            is_kind=False,
            error=f"destination already exists: {new_dir.relative_to(project)}",
        )
    
    old_id = str(old_dir.relative_to(content))
    new_id = str(new_dir.relative_to(content))
    
    refs, warnings = _scan_collection_refs(project, old_id, new_id)
    
    # Update _collection.md title if it appears to derive from the old slug.
    collection_md = old_dir / "_collection.md"
    if collection_md.is_file():
        title_ref = _collection_title_ref(collection_md, old_slug, new_slug)
        if title_ref is not None:
            refs.append(title_ref)
    
    return RenamePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        is_kind=False,
        refs=refs,
        warnings=warnings,
    )


def _scan_collection_refs(
    project: Path,
    old_collection_id: str,
    new_collection_id: str,
) -> tuple[list[MoveRef], list[str]]:
    """
    Scan the project for references to any entity living under
    *old_collection_id* (the collection itself or any descendant) and
    produce one MoveRef per affected line with all qualifying
    occurrences on that line rewritten to point under
    *new_collection_id*.

    Wikilinks are resolved using the full WIKILINKS.md contract — bare
    slugs, suffix paths, and full paths are all handled. The
    replacement form for each link is chosen by
    :func:`wikilinks.preferred_form` from the rendering page's
    cluster, so links stay as short as safely possible after the
    cascade.

    Matches:
      - ``target: <descendant-id>`` lines in entity frontmatter
        (anywhere the resolved descendant lives under the old
        collection id).
      - ``[[<link>]]`` tokens in entity bodies that resolve to an
        entity under the old collection id.
      - ``href="/<old-collection-id>/<...>"`` in SVG files under
        assets/ (path-prefix rewrite — SVG hrefs aren't
        cluster-aware).
    """
    from .wikilinks import (
        WIKILINK_RE,
        build_index,
        parse_wikilink,
        preferred_form,
        render_wikilink,
        resolve,
        scope_of,
    )

    content = content_root(project).resolve()
    refs: list[MoveRef] = []
    warnings: list[str] = []

    index = build_index(project)
    old_prefix = old_collection_id + "/"

    def cascade(eid: str) -> str:
        """Map a pre-move entity id to its post-move id, or return as-is."""
        if eid == old_collection_id:
            return new_collection_id
        if eid.startswith(old_prefix):
            return new_collection_id + "/" + eid[len(old_prefix):]
        return eid

    # Build a post-move index snapshot so preferred_form sees the
    # cascaded ids when picking replacement forms.
    renames = {eid: cascade(eid) for eid in index.entity_ids if cascade(eid) != eid}
    post_index = index.with_renamed_ids(renames)

    # target: <id-under-old-collection>  (frontmatter only).
    # Match the full id token greedily — we cascade based on prefix.
    target_re = re.compile(
        r"^(?P<prefix>\s*target:\s*)(?P<id>"
        + re.escape(old_collection_id)
        + r"(?:/[A-Za-z0-9_\-/]+)?)(?P<suffix>\s*)$"
    )

    for md_file in iter_entity_md_files(content):
        page_id = str(md_file.parent.relative_to(content))
        page_cluster = scope_of(page_id, index)
        text = md_file.read_text(encoding="utf-8")
        fm, _body = split_frontmatter(text)
        fm_line_count = 0
        if fm is not None:
            fm_line_count = len(fm.splitlines()) + 2

        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            in_frontmatter = i <= fm_line_count

            if in_frontmatter:
                m = target_re.match(line)
                if m:
                    new_id = cascade(m.group("id"))
                    if new_id != m.group("id"):
                        new_line = m.group("prefix") + new_id + m.group("suffix")
                        refs.append(MoveRef(md_file, i, line, new_line))
                continue

            if "[[" not in line:
                continue

            line_stripped = line.strip()
            is_directive_line = (
                line_stripped.startswith("[[collection:")
                and line_stripped.endswith("]]")
                and line_stripped.count("[[") == 1
            )

            pieces: list[str] = []
            cursor = 0
            rewrote = False
            for m in WIKILINK_RE.finditer(line):
                inner = m.group("inner")
                allow_collection = is_directive_line and m.group(0) == line_stripped
                parsed = parse_wikilink(inner, allow_collection=allow_collection)
                if parsed.kind in ("literal", "same-page", "kind"):
                    continue
                res = resolve(parsed, page_cluster, index)
                if res.status != "resolved":
                    if res.status == "ambiguous":
                        # Was any candidate inside the moved collection?
                        if any(c == old_collection_id or c.startswith(old_prefix)
                               for c in res.candidates):
                            warnings.append(
                                f"{page_id}: [[{inner}]] is already ambiguous "
                                f"(matches {', '.join(res.candidates)}); skipping rewrite"
                            )
                    continue
                resolved_id = res.entity_id
                if resolved_id != old_collection_id and not resolved_id.startswith(old_prefix):
                    continue
                new_id = cascade(resolved_id)
                new_path = preferred_form(new_id, page_cluster, post_index)
                if parsed.kind == "collection":
                    replacement = f"[[collection:{new_path}]]"
                else:
                    replacement = render_wikilink(new_path, parsed.anchor, parsed.label)
                pieces.append(line[cursor:m.start()])
                pieces.append(replacement)
                cursor = m.end()
                rewrote = True
            if rewrote:
                pieces.append(line[cursor:])
                refs.append(MoveRef(md_file, i, line, "".join(pieces)))

    # SVG href="/<old-collection-id>/<...>"
    svg_href_re = re.compile(
        r'(?P<pre>href=")/' + re.escape(old_collection_id)
        + r'/(?P<rest>[^"\s/]+(?:/[^"\s]*)?)(?P<post>["\s])'
    )
    assets = assets_root(project)
    if assets.is_dir():
        for svg_file in assets.rglob("*.svg"):
            lines = svg_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if svg_href_re.search(line):
                    new_line = svg_href_re.sub(
                        lambda mm: f'{mm.group("pre")}/{new_collection_id}/{mm.group("rest")}{mm.group("post")}',
                        line,
                    )
                    if new_line != line:
                        refs.append(MoveRef(svg_file, i, line, new_line))

    return refs, warnings


def _slug_to_title(slug: str) -> str:
    """Convert a slug like 'primitive-elements' to 'Primitive Elements'."""
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-") if word)


def _collection_title_ref(
    collection_md: Path,
    old_slug: str,
    new_slug: str,
) -> MoveRef | None:
    """
    If the collection's _collection.md has a title: field whose value matches
    the title-case form of *old_slug*, return a MoveRef that rewrites it to
    the title-case form of *new_slug*. Otherwise return None — the user
    can update the title manually if it doesn't follow the slug.
    """
    text = collection_md.read_text(encoding="utf-8")
    fm, _ = split_frontmatter(text)
    if fm is None:
        return None
    
    old_title = _slug_to_title(old_slug)
    new_title = _slug_to_title(new_slug)
    
    title_re = re.compile(r"^(?P<prefix>\s*title:\s*)(?P<value>.*?)(?P<suffix>\s*)$")
    
    # Walk the file lines, but only inspect frontmatter region.
    lines = text.splitlines()
    # frontmatter occupies lines 1..N where N is the closing fence
    # we look only inside the FM block
    for i, line in enumerate(lines, start=1):
        if i == 1:
            continue  # opening fence
        if line.strip() == "---":
            break  # closing fence — stop scanning
        m = title_re.match(line)
        if not m:
            continue
        # Strip optional surrounding quotes for comparison.
        value = m.group("value").strip()
        unquoted = value.strip("\"'")
        if unquoted == old_title:
            # Preserve quoting style if any
            if value.startswith('"') and value.endswith('"'):
                new_value = f'"{new_title}"'
            elif value.startswith("'") and value.endswith("'"):
                new_value = f"'{new_title}'"
            else:
                new_value = new_title
            new_line = m.group("prefix") + new_value + m.group("suffix")
            return MoveRef(collection_md, i, line, new_line)
        return None  # title exists but doesn't match — leave it alone
    return None
