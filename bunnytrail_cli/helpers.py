"""
helpers.py — shared utilities for locating project roots and reading
content/content_meta structure without loading every file.

All entities, collections, and kinds are now authored as a single
Markdown file with YAML frontmatter:

    content/<...path>/<slug>/index.md       — entity (frontmatter + prose)
    content/<...path>/<slug>/_collection.md — collection marker
    content_meta/kinds/<...path>/<kind>/_kind.yaml — kind marker

The legacy split layout (`index.yaml` + `index.md`) is no longer
supported.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
        "Could not find the bunnytrail project root.  "
        "Run the command from inside the repository."
    )


def content_root(project: Path) -> Path:
    return project / "content"


def kinds_root(project: Path) -> Path:
    return project / "content_meta" / "kinds"


def guides_root(project: Path) -> Path:
    return project / "content_meta" / "guides"


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

      - the presence of a ``_kind.yaml`` file (with or without
        frontmatter — the folder existing is enough), or
      - simply being a sub-directory of ``content_meta/kinds/``.

    Callers that need to distinguish between "registered with a
    `_kind.yaml`" and "implicit" should check for the file directly.
    """
    return (path / "_kind.yaml").is_file()


def is_ontology_folder(path: Path) -> bool:
    """An ontology container has ``_ontology.yaml`` but no ``_kind.yaml``.

    Applies at any depth under ``content_meta/kinds/``, though the
    ONTOLOGY.md spec allows only one level of nesting (ontologies cannot
    be nested inside other ontologies).
    """
    return (path / "_ontology.yaml").is_file() and not (path / "_kind.yaml").is_file()


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
    """Return a simple text tree of the kinds hierarchy.

    Legend: [K] kind folder  [O] ontology container  [ ] plain folder
    """
    lines: list[str] = []
    prefix = "  " * indent
    for child in sorted(base.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if is_kind_folder(child):
            marker = "[K]"
        elif is_ontology_folder(child):
            marker = "[O]"
        else:
            marker = "[ ]"
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
    """Yield every ``_kind.yaml`` under the kinds tree."""
    for md_file in kinds.rglob("_kind.yaml"):
        yield md_file


def iter_ontology_yaml_files(kinds: Path):
    """Yield every ``_ontology.yaml`` under the kinds tree (including root)."""
    for yaml_file in kinds.rglob("_ontology.yaml"):
        yield yaml_file


def iter_collection_md_files(content: Path):
    """Yield every ``_collection.md`` marker under *content*."""
    for md_file in content.rglob("_collection.md"):
        yield md_file


def iter_guide_md_files(guides: Path):
    """Yield every ``index.md`` directly under a guide folder."""
    if not guides.is_dir():
        return
    for child in sorted(guides.iterdir()):
        md = child / "index.md"
        if child.is_dir() and md.is_file():
            yield md


def iter_link_consumer_files(project: Path):
    """Yield every markdown file whose body may contain wikilinks
    that need to be kept in sync when entities/kinds/collections are
    moved or renamed.

    Currently: entity ``index.md``, collection ``_collection.md``,
    kind ``_kind.yaml``, and guide ``index.md``. Blog posts are
    deliberately excluded — wikilinks do not resolve in blog prose
    (see STRUCTURE.md).
    """
    content = content_root(project)
    if content.is_dir():
        yield from iter_entity_md_files(content)
        yield from iter_collection_md_files(content)
    kinds = kinds_root(project)
    if kinds.is_dir():
        yield from iter_kind_md_files(kinds)
    guides = guides_root(project)
    if guides.is_dir():
        yield from iter_guide_md_files(guides)


def page_id_for(md_file: Path, project: Path) -> str:
    """Return a synthetic page id for *md_file*, suitable for passing
    to :func:`wikilinks.scope_of` and for resolver page-relative
    operations.

    - Entity ``index.md``  → folder path relative to ``content/``
      (e.g. ``aurethia/places/bayurinda/sharazan``).
    - Collection ``_collection.md`` → folder path relative to
      ``content/`` (e.g. ``aurethia/places``).
    - Kind ``_kind.yaml`` → ``kinds/<path>`` (e.g. ``kinds/being/mortal``).
    - Guide ``index.md`` → ``guides/<slug>`` (e.g. ``guides/cognita``).
    """
    content = content_root(project)
    kinds = kinds_root(project)
    guides = guides_root(project)
    folder = md_file.parent
    name = md_file.name
    try:
        if name == "_kind.yaml" and kinds.is_dir():
            return "kinds/" + str(folder.relative_to(kinds))
        if folder.parent == guides and name == "index.md":
            return "guides/" + folder.name
        # Entity or collection — both live under content/.
        if content.is_dir():
            return str(folder.relative_to(content))
    except ValueError:
        pass
    # Last resort: project-relative path of the folder.
    return str(folder.relative_to(project))


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
    collisions: list[str] = field(default_factory=list)
    # Existing entity ids that share the new id's leaf slug.  Non-empty
    # means the move/rename will make bare-slug links to those peers
    # ambiguous; the scanner will rewrite those links to a longer form,
    # but the user should be warned and confirm.


def _detect_collisions(
    new_id: str,
    pre_index,           # WikilinkIndex before the move/rename
    *,
    exclude: "str | None" = None,
) -> "list[str]":
    """Return the list of existing entity ids whose **leaf slug** matches
    *new_id*'s leaf slug.

    These are the peers that will become ambiguous once *new_id* enters
    the index: a bare ``[[<leaf>]]`` link previously resolved to one of
    them, and after the move it will resolve to multiple candidates.

    *exclude* is normally the moving entity's old id (so we don't list
    the moving entity itself when its slug isn't changing).
    """
    leaf = new_id.rsplit("/", 1)[-1]
    peers: list[str] = []
    for eid in pre_index.entity_ids:
        if eid == new_id or eid == exclude:
            continue
        if eid.rsplit("/", 1)[-1] == leaf:
            peers.append(eid)
    peers.sort()
    return peers


def _scan_entity_refs(
    project: Path,
    old_id: str,
    new_id: str,
    *,
    display_renames: "dict[str, str] | None" = None,
    collision_peers: "list[str] | None" = None,
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
      * ``class: <old-id>`` (top-level), ``role: <old-id>``,
        ``within: <old-id>``, and ``species: <old-id>`` lines
        (the last three may be indented) in entity frontmatter.
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
        r"^(?P<prefix>\s*(?:-\s+)?target:\s*)(?P<id>" + re.escape(old_id) + r")(?P<suffix>\s*.*)$"
    )
    # Other frontmatter fields that store entity paths.
    # ``class:`` is always top-level; ``role:``, ``within:``, ``species:``
    # may appear as YAML list items, so the prefix may include ``- ``.
    # The path must be followed by end-of-line or whitespace (to avoid
    # matching a longer sibling path like aurethia/places/sharazan-extended).
    _path_fields = "|".join(re.escape(f) for f in ("class", "role", "within", "species"))
    extra_path_re = re.compile(
        r"^(?P<prefix>\s*(?:-\s+)?(?:" + _path_fields + r"):\s*)"
        r"(?P<id>" + re.escape(old_id) + r")"
        r"(?P<suffix>(?:\s+.*)?)$"
    )

    for md_file in iter_link_consumer_files(project):
        is_entity = md_file.name == "index.md" and is_entity_folder(md_file.parent)
        page_id = page_id_for(md_file, project)
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

            # ---- target: rewrites only inside entity frontmatter ---
            if in_frontmatter:
                if is_entity:
                    m = target_re.match(line)
                    if m:
                        new_line = m.group("prefix") + new_id + m.group("suffix")
                        refs.append(MoveRef(md_file, i, line, new_line))
                    else:
                        m = extra_path_re.match(line)
                        if m:
                            new_line = m.group("prefix") + new_id + m.group("suffix")
                            refs.append(MoveRef(md_file, i, line, new_line))
                continue

            new_line, rewrote, warns = _rewrite_wikilinks_on_line(
                line=line,
                page_id=page_id,
                page_cluster=page_cluster,
                index=index,
                post_index=post_index,
                old_id=old_id,
                new_id=new_id,
                display_renames=display_renames or {},
                collision_peers=set(collision_peers or ()),
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
    display_renames: "dict[str, str] | None" = None,
    collision_peers: "set[str] | None" = None,
) -> tuple[str, bool, list[str]]:
    """Rewrite every ``[[…]]`` on *line* whose resolved target is *old_id*
    (or a *collision_peer* whose preferred form has shifted because of
    the move).

    Returns ``(new_line, rewrote_anything, warnings)``. The
    replacement form is selected by
    :func:`wikilinks.preferred_form` from the perspective of the
    rendering page's cluster — bare slugs stay bare where possible,
    cross-cluster targets get full ids, and so on.

    Anchors are preserved verbatim.  Labels are preserved unless they
    appear in *display_renames* (an ``old_display -> new_display``
    map), in which case the new label is substituted.  When the link
    is bare (no explicit label) and *display_renames* maps the old
    leaf to a new display value, an explicit label is added so the
    rendered text matches the renamed entity's chosen display name.

    *collision_peers* names existing entities whose **leaf slug** now
    clashes with *new_id*'s leaf slug after the move.  Any link that
    used to resolve unambiguously to a peer via a bare or short form
    will, post-move, resolve ambiguously — so those links are
    rewritten to the new :func:`preferred_form` for the peer (typically
    a longer suffix or the full id).  Peer-rewrites preserve the
    original anchor and label exactly.
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
            # Decide on the label.  When an explicit label is present
            # AND it maps via display_renames, swap it.  When the link
            # was bare and the old display name matches a renames key
            # whose new value differs from the new path's leaf, add an
            # explicit label so the rendered text stays correct.
            new_label = parsed.label
            if display_renames:
                if parsed.label and parsed.label in display_renames:
                    new_label = display_renames[parsed.label]
                elif not parsed.label:
                    # The bare-link "display" is the link's leaf slug
                    # — but the entity's user-facing display name is
                    # something we know from frontmatter.  If the old
                    # display value appears in the renames map AND the
                    # new value differs from the new leaf, force a
                    # label.
                    for old_disp, new_disp in display_renames.items():
                        if new_disp != new_path.split("/")[-1]:
                            new_label = new_disp
                            break
            if parsed.kind == "collection":
                # Whole-line directive — keep the directive prefix.
                replacement = f"[[collection:{new_path}]]"
            else:
                replacement = render_wikilink(
                    new_path, parsed.anchor, new_label
                )
            pieces.append(line[cursor:m.start()])
            pieces.append(replacement)
            cursor = m.end()
            rewrote = True
        elif (
            collision_peers
            and res.status == "resolved"
            and res.entity_id in collision_peers
        ):
            # Pre-move: this link unambiguously resolved to a peer.
            # Post-move: the peer shares a leaf slug with the moved
            # entity, so the bare/short form is no longer unique.
            # Rewrite to the peer's new preferred form.
            peer_path = preferred_form(res.entity_id, page_cluster, post_index)
            # Always preserve the original label (we are not renaming
            # the peer's display name — only re-routing the link).
            if parsed.kind == "collection":
                replacement = f"[[collection:{peer_path}]]"
            else:
                replacement = render_wikilink(
                    peer_path, parsed.anchor, parsed.label
                )
            # Skip no-op (link was already in a disambiguated form).
            if replacement == m.group(0):
                continue
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

    # Detect leaf-slug collisions in the post-move world so the user
    # can be warned, and so existing links to peers can be rewritten.
    from .wikilinks import build_index
    pre_index = build_index(project)
    collisions = _detect_collisions(new_id, pre_index, exclude=old_id)

    refs, warnings = _scan_entity_refs(
        project, old_id, new_id, collision_peers=collisions or None,
    )

    return MovePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        refs=refs,
        warnings=warnings,
        collisions=collisions,
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

# ---------------------------------------------------------------------------
# bt.yml — project-level CLI config
# ---------------------------------------------------------------------------

# Path to the optional bt CLI config file, relative to a project root.
# Currently recognised top-level keys:
#
#   crosslink:   Policy for the `bt crosslink` command.
#     never:     Flat list of exact display names that must never be
#                auto-linked.
#     warn:      Flat list of exact display names that may be linked but
#                are surfaced after a run so the user can decide whether
#                to demote them to `never:`.
#
#   add:         Behaviour of the `bt add` command.
#     class_for_kinds:
#                Flat list of kind slugs for which `bt add entity` will
#                interactively prompt for a ``class:`` entity-path field.
#
# Both crosslink lists are matched case-sensitively against the same
# strings the crosslinker would otherwise turn into wikilinks (a kind's
# singular/plural, or an entity's display name).
_BT_CONFIG_PATH = "content_meta/bt.yml"


@dataclass
class CrosslinkConfig:
    """Parsed ``crosslink:`` section of ``content_meta/bt.yml``."""
    never: set[str] = field(default_factory=set)
    warn: set[str] = field(default_factory=set)


@dataclass
class AddConfig:
    """Parsed ``add:`` section of ``content_meta/bt.yml``."""
    class_for_kinds: set[str] = field(default_factory=set)


@dataclass
class BtConfig:
    """Full parsed contents of ``content_meta/bt.yml``."""
    crosslink: CrosslinkConfig = field(default_factory=CrosslinkConfig)
    add: AddConfig = field(default_factory=AddConfig)


def load_bt_config(project: Path) -> BtConfig:
    """Load ``content_meta/bt.yml`` (if present) and return a :class:`BtConfig`.

    Missing file → empty config with all defaults.  Parsing is deliberately
    lenient: only recognised keys are read; unknown keys and invalid shapes
    are silently skipped — keep the file simple.
    """
    cfg_file = project / _BT_CONFIG_PATH
    if not cfg_file.is_file():
        return BtConfig()

    text = cfg_file.read_text(encoding="utf-8")

    cl_never: set[str] = set()
    cl_warn: set[str] = set()
    add_class_for_kinds: set[str] = set()

    # Simple single-pass line parser.
    # State: which top-level section and which sub-key we are inside.
    section: "str | None" = None          # "crosslink" | "add" | None
    sub_key: "str | None" = None          # e.g. "never", "warn", "class_for_kinds"

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        # Top-level key — no leading whitespace
        if line[0] not in (" ", "\t"):
            key, _, _ = line.partition(":")
            key = key.strip()
            section = key if key in ("crosslink", "add") else None
            sub_key = None
            continue

        # Indented content — must be inside a recognised section
        if section is None:
            continue

        stripped = line.strip()

        # Sub-key line (not a list item)
        if not stripped.startswith("- "):
            key, _, _ = stripped.partition(":")
            key = key.strip()
            if section == "crosslink" and key in ("never", "warn"):
                sub_key = key
            elif section == "add" and key == "class_for_kinds":
                sub_key = key
            else:
                sub_key = None
            continue

        # List item — accumulate into the current sub-key's collection
        if sub_key is None:
            continue
        value = stripped[2:].strip().strip("\"'")
        if not value:
            continue
        if section == "crosslink":
            if sub_key == "never":
                cl_never.add(value)
            elif sub_key == "warn":
                cl_warn.add(value)
        elif section == "add" and sub_key == "class_for_kinds":
            add_class_for_kinds.add(value)

    return BtConfig(
        crosslink=CrosslinkConfig(never=cl_never, warn=cl_warn),
        add=AddConfig(class_for_kinds=add_class_for_kinds),
    )


def load_crosslink_config(project: Path) -> CrosslinkConfig:
    """Convenience shim — returns the crosslink section of :func:`load_bt_config`."""
    return load_bt_config(project).crosslink


# ---------------------------------------------------------------------------
# world.md — world-level schema (relations and properties)
# ---------------------------------------------------------------------------

_WORLD_MD_PATH = "content_meta/world.md"


@dataclass
class RelationDef:
    """One relation kind, loaded from an ``_ontology.yaml`` file.

    ``slug`` is the **full** prefixed id (e.g. ``cultural/member-of``) as
    registered in the relation registry.  For relations from the root
    ontology the slug is bare (no prefix).
    """
    slug: str
    out_label: str = ""
    in_label: str = ""
    domain: list[str] = field(default_factory=list)    # kind slugs; empty = unrestricted
    codomain: list[str] = field(default_factory=list)  # kind slugs; empty = unrestricted


@dataclass
class PropertyDef:
    """One property, loaded from a ``_kind.yaml`` ``properties:`` block.

    ``slug`` is the bare property id (e.g. ``gender``).
    ``declaring_kind`` is the leaf slug of the kind whose ``_kind.yaml``
    declared it; empty string means it came from the old ``world.md``
    style (treated as unrestricted).
    """
    slug: str
    label: str = ""
    declaring_kind: str = ""     # leaf slug of the declaring kind; "" = unrestricted
    values: list[str] = field(default_factory=list)   # empty = free text


@dataclass
class WorldConfig:
    """Relations and properties loaded from the new ontology schema.

    Relations come from ``_ontology.yaml`` files under
    ``content_meta/kinds/``; properties come from the ``properties:``
    block inside each kind's ``_kind.yaml``.

    The two strictness flags from ``content_meta/world.md`` are also
    stored here for completeness.
    """
    relations: list[RelationDef] = field(default_factory=list)
    properties: list[PropertyDef] = field(default_factory=list)
    allow_undefined_relations: bool = False
    allow_undefined_properties: bool = False

    def applicable_relations(self, kind_id: str) -> list[RelationDef]:
        """Relations whose ``domain`` includes *kind_id* or is unrestricted."""
        return [r for r in self.relations if not r.domain or kind_id in r.domain]

    def applicable_properties(self, kind_id: str) -> list[PropertyDef]:
        """Properties that apply to *kind_id*.

        Per the new ontology spec, a property declared on a kind applies
        to that kind and all its descendants via the kind hierarchy.  The
        CLI does not have access to the full kind hierarchy graph, so this
        method uses a conservative approximation:

          * Properties with no ``declaring_kind`` (loaded from the old
            ``world.md`` style or from a root-level kind) are always
            included.
          * Properties where ``declaring_kind == kind_id`` are always
            included.
          * When *kind_id* is empty (unknown), all properties are returned
            so callers can show the full set for completion purposes.
        """
        if not kind_id:
            return list(self.properties)
        return [
            p for p in self.properties
            if not p.declaring_kind or p.declaring_kind == kind_id
        ]


def _as_str_list(val: object) -> list[str]:
    """Coerce a YAML value to a list of strings (handles list, str, or None)."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _load_ontology_relations(
    ontology_yaml: Path,
    kinds_root_path: Path,
) -> list[RelationDef]:
    """Load relations from a single ``_ontology.yaml`` file.

    The relation id is constructed by prepending the ontology folder name
    to the bare slug — unless the file lives directly at the root of
    ``content_meta/kinds/`` (root ontology), in which case slugs are bare.

    Returns an empty list on any parse error.
    """
    try:
        text = ontology_yaml.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []

    # Determine the prefix: the folder name when this is a named ontology,
    # or "" when it sits directly at the kinds root.
    folder = ontology_yaml.parent
    if folder.resolve() == kinds_root_path.resolve():
        prefix = ""
    else:
        prefix = folder.name + "/"

    relations: list[RelationDef] = []
    raw_rels = data.get("relations") or {}
    if isinstance(raw_rels, dict):
        for bare_slug, defn in raw_rels.items():
            if not isinstance(defn, dict):
                defn = {}
            full_slug = prefix + str(bare_slug)
            relations.append(RelationDef(
                slug=full_slug,
                out_label=str(defn.get("outLabel") or ""),
                in_label=str(defn.get("inLabel") or ""),
                domain=_as_str_list(defn.get("domain")),
                codomain=_as_str_list(defn.get("codomain")),
            ))
    return relations


def _load_kind_properties(kind_yaml: Path) -> list[PropertyDef]:
    """Load properties from the ``properties:`` block of a ``_kind.yaml`` file.

    ``declaring_kind`` is set to the folder's leaf slug so
    :meth:`WorldConfig.applicable_properties` can filter by kind.

    Returns an empty list on any parse error or missing ``properties:`` key.
    """
    try:
        text = kind_yaml.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []

    raw_props = data.get("properties") or {}
    if not isinstance(raw_props, dict):
        return []

    declaring_kind = kind_yaml.parent.name
    props: list[PropertyDef] = []
    for slug, defn in raw_props.items():
        if not isinstance(defn, dict):
            defn = {}
        props.append(PropertyDef(
            slug=str(slug),
            label=str(defn.get("label") or slug),
            declaring_kind=declaring_kind,
            values=_as_str_list(defn.get("values")),
        ))
    return props


def load_world_config(project: Path) -> WorldConfig:
    """Build a :class:`WorldConfig` from the new ontology schema.

    Sources:
      * ``content_meta/kinds/**/_ontology.yaml`` — one file per named
        ontology (or the root ``content_meta/kinds/_ontology.yaml`` for
        global relations).  Relation slugs are prefixed with the ontology
        folder name; root-ontology slugs remain bare.
      * ``content_meta/kinds/**/_kind.yaml`` — ``properties:`` block on
        each kind.
      * ``content_meta/world.md`` — only the two strictness booleans
        ``allowUndefinedRelations`` and ``allowUndefinedProperties``.

    Falls back gracefully: missing files, empty files, and YAML errors
    are all tolerated (the corresponding registry is simply empty).

    Backward compatibility: if ``content_meta/world.md`` still carries a
    ``relations:`` or ``properties:`` mapping (old schema), those are
    loaded too and merged in so existing projects keep working until they
    migrate to the new layout.
    """
    kinds = kinds_root(project)
    allow_undef_rels = False
    allow_undef_props = False
    relations: list[RelationDef] = []
    properties: list[PropertyDef] = []

    # ── relations from _ontology.yaml files ────────────────────────────────
    if kinds.is_dir():
        for ontology_yaml in sorted(kinds.rglob("_ontology.yaml")):
            relations.extend(_load_ontology_relations(ontology_yaml, kinds))

    # ── properties from _kind.yaml files ───────────────────────────────────
    if kinds.is_dir():
        for kind_yaml in sorted(kinds.rglob("_kind.yaml")):
            properties.extend(_load_kind_properties(kind_yaml))

    # ── world.md — strictness flags + backward-compat registry ────────────
    world_md = project / _WORLD_MD_PATH
    if world_md.is_file():
        try:
            text = world_md.read_text(encoding="utf-8")
            fm, _ = split_frontmatter(text)
            if fm is not None:
                data = yaml.safe_load(fm)
            else:
                # world.md may be plain YAML without fences (old style)
                data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = None

        if isinstance(data, dict):
            allow_undef_rels = bool(data.get("allowUndefinedRelations", False))
            allow_undef_props = bool(data.get("allowUndefinedProperties", False))

            # Backward compatibility: relations block in world.md
            raw_rels = data.get("relations") or {}
            if isinstance(raw_rels, dict):
                for slug, defn in raw_rels.items():
                    if not isinstance(defn, dict):
                        defn = {}
                    relations.append(RelationDef(
                        slug=str(slug),
                        out_label=str(defn.get("outLabel") or ""),
                        in_label=str(defn.get("inLabel") or ""),
                        domain=_as_str_list(defn.get("domain")),
                        codomain=_as_str_list(defn.get("codomain")),
                    ))

            # Backward compatibility: properties block in world.md
            raw_props = data.get("properties") or {}
            if isinstance(raw_props, dict):
                for slug, defn in raw_props.items():
                    if not isinstance(defn, dict):
                        defn = {}
                    properties.append(PropertyDef(
                        slug=str(slug),
                        label=str(defn.get("label") or slug),
                        declaring_kind="",    # old style — unrestricted
                        values=_as_str_list(defn.get("values")),
                    ))

    return WorldConfig(
        relations=relations,
        properties=properties,
        allow_undefined_relations=allow_undef_rels,
        allow_undefined_properties=allow_undef_props,
    )


@dataclass
class CrosslinkEdit:
    """A single line replacement that adds one or more wikilinks."""
    line_no: int        # 1-based
    old_text: str       # original line (without newline)
    new_text: str       # replacement line (without newline)
    warn_terms: list[str] = field(default_factory=list)
    # Names from the crosslink.yml `warn:` list that were linked on
    # this line.  Surfaced at the end of the run so the user can decide
    # whether to promote them to `never:`.


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
        # _kind.yaml is plain YAML (no frontmatter fences)
        fm_lines = text.splitlines()
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


# Lowercase connector words allowed inside a Proper Noun Phrase.
# These are stop-words that conventionally stay lowercase in titles and
# names but don't break the PNP — e.g. "Order of the Bellonas" is one
# phrase, not three.  Restricted to whole-token matches; a PNP must
# still START and END with a Capitalized word.
_PNP_CONNECTORS = frozenset({
    "of", "the", "and",                # English
    "de", "da", "du",                  # Romance particles
    "von", "van",                      # Germanic
    "le", "la",                        # French articles
    "el", "al",                        # Spanish / Arabic article
    "bin", "ibn",                      # Arabic patronymics
})

# A word token: a maximal run of word characters (letters/digits/_) with
# optional internal apostrophes or hyphens (so "Bellonas", "K'tharr",
# and "Foo-Bar" are each a single token).  Matches positionally so the
# scanner can rebuild span info.
_PNP_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:['\-][A-Za-z0-9]+)*")


def _is_capitalized(token: str) -> bool:
    """A token counts as Capitalized when its first letter is uppercase.

    The rest of the token is unconstrained so that acronyms (``USA``),
    mixed-case proper nouns (``McKay``), and apostrophe/hyphen forms
    (``O'Brien``, ``Foo-Bar``) all qualify.  Pure-lowercase tokens are
    not Capitalized, and digit-led tokens are excluded by the token
    regex itself.
    """
    return bool(token) and token[0].isupper()


def _proper_noun_phrase_spans(line: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, phrase_text)`` for every Proper Noun Phrase
    on *line*.

    A PNP is a maximal run of word tokens where:

      * the first and last tokens are :func:`_is_capitalized`, AND
      * any interior tokens are either Capitalized or in
        :data:`_PNP_CONNECTORS`.

    Tokens are separated by single spaces.  The run ends as soon as
    the next adjacent token (no intervening non-space characters)
    fails to qualify; punctuation, line breaks, or any non-space
    character between tokens also ends the run.

    Single-word PNPs (length 1) are included so the existing matcher
    behaviour for one-word terms is unchanged.

    Used by :func:`plan_crosslink` to skip linking a candidate whose
    match falls inside a PNP whose **whole text** is not itself a
    candidate — see the user-facing rule "if 'Identity Tilt' appears
    but only 'Identity' is linkable, don't link 'Identity'".
    """
    tokens = [(m.start(), m.end(), m.group(0)) for m in _PNP_TOKEN_RE.finditer(line)]
    spans: list[tuple[int, int, str]] = []
    i = 0
    n = len(tokens)
    while i < n:
        if not _is_capitalized(tokens[i][2]):
            i += 1
            continue
        # Try to extend the run.  Track the last index that was
        # Capitalized so we can trim trailing connectors (a PNP must
        # END on a Capitalized word).
        j = i
        last_cap = i
        while j + 1 < n:
            # Tokens must be separated by exactly one space — anything
            # else (punctuation, double space, line break) breaks the
            # run.
            gap_start = tokens[j][1]
            gap_end = tokens[j + 1][0]
            if line[gap_start:gap_end] != " ":
                break
            nxt = tokens[j + 1][2]
            if _is_capitalized(nxt):
                j += 1
                last_cap = j
            elif nxt in _PNP_CONNECTORS:
                # Tentatively extend, but only commit the PNP up to
                # last_cap unless we find another Capitalized word
                # past this connector.
                j += 1
            else:
                break
        start = tokens[i][0]
        end = tokens[last_cap][1]
        spans.append((start, end, line[start:end]))
        i = last_cap + 1
    return spans


def _enclosing_pnp(
    pos: int, length: int, pnps: list[tuple[int, int, str]]
) -> "str | None":
    """Return the text of the PNP that fully contains [pos, pos+length),
    or ``None`` if the range is not enclosed by any PNP.

    Used by the crosslink matcher to decide whether a candidate match
    is a *prefix* (or interior fragment) of a larger Proper Noun
    Phrase, in which case linking it would be wrong — the user
    intends the whole phrase to refer to a single (possibly not-yet-
    written) concept.
    """
    end = pos + length
    for s, e, text in pnps:
        if s <= pos and end <= e:
            return text
    return None


def _simplify_wikilinks_on_line(
    line: str,
    *,
    article_cluster: "str | None",
    index,
    parse_wikilink,
    preferred_form,
    render_wikilink,
    wikilink_re,
) -> str:
    """Rewrite every ``[[…]]`` on *line* to its :func:`preferred_form`.

    Only ``"path"``-kind links are rewritten — kinds, lang tags,
    same-page anchors, and collection directives are passed through
    unchanged. Anchors and labels are preserved. If the link doesn't
    resolve cleanly to a known entity (missing / ambiguous / literal)
    the original token is kept verbatim.

    Returns the (possibly modified) line.
    """
    def _replace(m):
        token = m.group(0)
        inner = m.group("inner")
        parsed = parse_wikilink(inner)
        if parsed.kind != "path":
            return token
        if parsed.path not in index.entity_ids:
            # Try a suffix resolve to get the canonical id; if that fails
            # or is ambiguous, leave the token alone.
            from .wikilinks import resolve  # local import to avoid cycle
            res = resolve(parsed, article_cluster, index)
            if res.status != "resolved" or not res.entity_id:
                return token
            target_id = res.entity_id
        else:
            target_id = parsed.path

        short = preferred_form(target_id, article_cluster, index)
        if short == parsed.path:
            return token  # already shortest

        # Preserve a non-empty label as-is. When the original had no
        # label and the short form's leaf no longer matches the
        # display the reader would have seen (the leaf of the old
        # path), synthesise a label from the old leaf so the rendered
        # text doesn't change.
        label = parsed.label
        if not label:
            old_leaf = parsed.path.split("/")[-1]
            new_leaf = short.split("/")[-1]
            if new_leaf != old_leaf:
                label = old_leaf
        return render_wikilink(short, anchor=parsed.anchor, label=label)

    return wikilink_re.sub(_replace, line)


def _resolve_article_dir(project: Path, article_path: str) -> tuple[Path, str]:
    """Resolve *article_path* to a folder on disk.

    Returns ``(article_dir, display_path)`` where ``display_path`` is
    the project-relative path (with the appropriate root prefix
    re-attached) suitable for use in error messages.

    The accepted forms are:

      * ``guides/<slug>`` — resolved under ``content_meta/guides/``;
        the display path is ``content_meta/guides/<slug>``.
      * any other path — resolved under ``content/``; the display
        path is ``content/<article_path>``.
    """
    article_path = article_path.strip("/")
    if article_path == "guides" or article_path.startswith("guides/"):
        rel = article_path[len("guides"):].lstrip("/")
        base = guides_root(project)
        path = (base / rel).resolve() if rel else base.resolve()
        return path, f"content_meta/{article_path}"
    return (
        (content_root(project) / article_path).resolve(),
        f"content/{article_path}",
    )


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
    article_dir, article_display = _resolve_article_dir(project, article_path)
    is_guide = (
        article_dir.parent == guides_root(project).resolve()
        and (article_dir / "index.md").is_file()
    )

    # Resolve which markdown file we're editing.  An "article" for
    # crosslinking purposes is either:
    #   * an entity folder (has index.md with frontmatter) — md_file
    #     is index.md; or
    #   * a collection folder (has _collection.md) — md_file is
    #     _collection.md.  Frontmatter is title/description; body
    #     prose, if any, is what we link; or
    #   * a guide folder under content_meta/guides/<slug>/ — md_file
    #     is index.md.  Wikilinks resolve in guide prose per
    #     STRUCTURE.md.
    # We probe in that order so an entity whose folder also happens
    # to contain a stray _collection.md (shouldn't happen but) wins.
    if not article_dir.is_dir():
        return CrosslinkPlan(
            article_id=article_path, md_file=article_dir / "index.md",
            namespace=namespace_path,
            error=f"article not found: {article_display}",
        )
    if is_guide:
        md_file = article_dir / "index.md"
    elif is_entity_folder(article_dir):
        md_file = article_dir / "index.md"
    elif is_collection_folder(article_dir):
        md_file = article_dir / "_collection.md"
    else:
        return CrosslinkPlan(
            article_id=article_path, md_file=article_dir / "index.md",
            namespace=namespace_path,
            error=(
                f"not an entity, collection, or guide folder "
                f"(no index.md with frontmatter, _collection.md, "
                f"or guide index.md): {article_display}"
            ),
        )

    ns_dir = content / namespace_path
    if not ns_dir.is_dir():
        return CrosslinkPlan(
            article_id=article_path, md_file=md_file,
            namespace=namespace_path,
            error=f"namespace not found: content/{namespace_path}",
        )

    # ------------------------------------------------------------------
    # Load the optional crosslink-policy config and apply `never` to
    # both the entity and kind candidate pools.
    # ------------------------------------------------------------------
    cfg = load_crosslink_config(project)

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
        if name in cfg.never:
            continue
        candidates.append((name, entity_id, False))

    for match_text, kind_target in collect_all_kinds(project):
        if match_text in cfg.never:
            continue
        candidates.append((match_text, kind_target, True))

    # Sort longest-first to prefer specific matches over shorter substrings
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    # ------------------------------------------------------------------
    # Build a wikilink index and figure out the article's cluster so
    # we can emit short forms when they resolve unambiguously.
    # ------------------------------------------------------------------
    from .wikilinks import (
        WIKILINK_RE,
        build_index,
        parse_wikilink,
        preferred_form,
        render_wikilink,
        resolve,
        scope_of,
    )
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

    # Track which candidate names have already been linked (first-only rule).
    # Pre-seeded below from existing wikilinks in the body so a manual
    # [[identity|Identity]] earlier in the article suppresses any later
    # auto-linking of the bare word "Identity".
    linked_texts: set[str] = set()

    # Build a target-id -> set of display-name strings map so we can ask
    # "does this existing wikilink point at an entity/kind whose display
    # name is one of our candidates?".  Multiple candidates can share a
    # target (kinds register both singular and plural), so the value is
    # a set.
    target_to_names: dict[str, set[str]] = {}
    for cand_name, cand_target, _is_kind in candidates:
        target_to_names.setdefault(cand_target, set()).add(cand_name)

    edits: list[CrosslinkEdit] = []

    # ------------------------------------------------------------------
    # Pass 1 (full body): simplify every existing wikilink to its
    # preferred_form, AND collect the targets of all surviving wikilinks
    # so the first-occurrence rule honours manual links written by the
    # author.  Without this seeding, a paragraph that already contains
    # [[identity|Identity]] would still get a second `Identity` further
    # down the article auto-linked, which the user explicitly wants to
    # avoid.
    # ------------------------------------------------------------------
    simplified: list[str] = list(lines)
    for line_idx in range(body_start_idx, len(lines)):
        line = simplified[line_idx]
        if line.lstrip().startswith("#"):
            continue
        simplified[line_idx] = _simplify_wikilinks_on_line(
            line,
            article_cluster=article_cluster,
            index=index,
            parse_wikilink=parse_wikilink,
            preferred_form=preferred_form,
            render_wikilink=render_wikilink,
            wikilink_re=WIKILINK_RE,
        )

    # Seed `linked_texts` from existing wikilinks in the simplified body.
    # We resolve each link with the same machinery the renderer uses;
    # any unresolvable / kind / lang / collection link is ignored for
    # seeding purposes.
    for line_idx in range(body_start_idx, len(lines)):
        line = simplified[line_idx]
        if line.lstrip().startswith("#"):
            continue
        for m in WIKILINK_RE.finditer(line):
            parsed = parse_wikilink(m.group("inner"))
            if parsed.kind in ("path", "lang"):
                # `lang` is the parser's tentative classification for
                # any all-lowercase short slug — the resolver makes the
                # final call against the index, so a `[[sharazan]]`
                # that happens to fit the lang-tag shape still resolves
                # to the entity if no language registers that code.
                res = resolve(parsed, article_cluster, index)
                if res.status == "resolved":
                    linked_texts.update(target_to_names.get(res.entity_id, ()))
            elif parsed.kind == "kind":
                # Kind links use `kinds/<slug>` as their path *and* as
                # the candidate target string, so the lookup is direct.
                linked_texts.update(target_to_names.get(parsed.path, ()))

    for line_idx in range(body_start_idx, len(lines)):
        line = lines[line_idx]
        if line.lstrip().startswith("#"):
            continue
        new_line = simplified[line_idx]
        warn_on_line: list[str] = []

        # ----------------------------------------------------------------
        # Pass 2: insert new wikilinks for unlinked mentions.
        # ----------------------------------------------------------------
        # Pre-compute the line's Proper Noun Phrase spans so the
        # matcher can skip a candidate whose match is a strict
        # fragment of a larger PNP that isn't itself a linkable term
        # (e.g. don't link 'Identity' inside 'Identity Tilt' when only
        # 'Identity' is registered).  Recomputed lazily on each
        # iteration because earlier replacements may have rewritten
        # the line.
        for pat, match_text, target, is_kind_target in patterns:
            if match_text in linked_texts:
                continue

            # Search new_line (which may already contain earlier replacements
            # on this line) so that position lookups and span checks are always
            # consistent with the current state of the string.
            spans = _wikilink_spans(new_line)
            pnps = _proper_noun_phrase_spans(new_line)
            m = pat.search(new_line)
            if m is None:
                continue

            start = m.start()
            length = len(match_text)

            # Skip if this occurrence sits inside an existing wikilink
            if _is_in_span(start, length, spans):
                continue

            # Skip if this match is a strict fragment of a larger
            # Proper Noun Phrase that isn't itself a linkable term.
            # The PNP is "linkable" iff its exact text appears in the
            # candidate set (seen_texts) — in that case the long-first
            # candidate sort already handled it on an earlier iteration
            # and this short fragment would be inside a wikilink span,
            # which we just checked above.
            enclosing = _enclosing_pnp(start, length, pnps)
            if enclosing is not None and enclosing != match_text \
                    and enclosing not in seen_texts:
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
            if match_text in cfg.warn:
                warn_on_line.append(match_text)

        if new_line != line:
            edits.append(CrosslinkEdit(
                line_no=line_idx + 1,
                old_text=line,
                new_text=new_line,
                warn_terms=warn_on_line,
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
    """Build a CrosslinkPlan for every article reachable from *target_path*.

    *target_path* may be either:

      * a single entity / collection / guide folder — yields exactly
        one plan; or
      * any other directory — walks it recursively and yields one
        plan per entity, collection, and guide found.

    Recognized roots:

      * paths under ``content/`` (the default) cover entities and
        collections.
      * ``guides`` (or ``guides/<slug>``) walks the
        ``content_meta/guides/`` tree.

    *namespace_path* is relative to ``content/`` and scopes which
    entities are candidates for linking, exactly as in
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
    target_dir, target_display = _resolve_article_dir(project, target_path)

    if not target_dir.is_dir():
        return [], f"not a directory: {target_display}"

    ns_dir = (content / namespace_path).resolve()
    if not ns_dir.is_dir():
        return [], f"namespace not found: content/{namespace_path}"

    # Single-target case: route directly to plan_crosslink, which
    # handles entity, collection, and guide folders.
    guides_dir = guides_root(project).resolve()
    is_guide_folder = (
        target_dir.parent == guides_dir
        and (target_dir / "index.md").is_file()
    )
    if (
        is_entity_folder(target_dir)
        or is_collection_folder(target_dir)
        or is_guide_folder
    ):
        return [plan_crosslink(project, target_path, namespace_path)], ""

    # Folder case: walk and plan each descendant article.  Articles
    # are entities, collections, and (when walking the guides root)
    # guides.  Collection bodies are typically empty today but the
    # same matching rules apply when they aren't.
    plans: list[CrosslinkPlan] = []
    seen_ids: set[str] = set()
    if target_dir == guides_dir or guides_dir in target_dir.parents:
        for guide_md in sorted(iter_guide_md_files(target_dir)):
            guide_id = "guides/" + guide_md.parent.name
            if guide_id in seen_ids:
                continue
            seen_ids.add(guide_id)
            plans.append(plan_crosslink(project, guide_id, namespace_path))
    else:
        for md_file in sorted(iter_entity_md_files(target_dir)):
            entity_id = str(md_file.parent.relative_to(content))
            if entity_id in seen_ids:
                continue
            seen_ids.add(entity_id)
            plans.append(plan_crosslink(project, entity_id, namespace_path))
        for collection_md in sorted(target_dir.rglob("_collection.md")):
            collection_id = str(collection_md.parent.relative_to(content))
            if collection_id in seen_ids:
                continue
            seen_ids.add(collection_id)
            plans.append(plan_crosslink(project, collection_id, namespace_path))

    if not plans:
        return [], f"no entities, collections, or guides found under {target_display}"

    return plans, ""


# ---------------------------------------------------------------------------
# Rename — reference scanning and execution
# ---------------------------------------------------------------------------

# Rename reuses MoveRef for individual line edits; the plan shape is similar.


def _rewrite_labels_inline(
    line: str,
    pattern: "re.Pattern[str]",
    label_map: "dict[str, str]",
) -> str:
    """Rewrite the ``|Label`` portion of every ``[[…]]`` match on *line*.

    *pattern* must capture a ``label`` group.  When the captured label
    matches a key in *label_map*, that key is replaced by the mapped
    value in the produced output; otherwise the token is left intact.
    """
    def _sub(m):
        old_label = m.group("label")
        new_label = label_map.get(old_label)
        if new_label is None:
            return m.group(0)
        return m.group(0).replace(f"|{old_label}]]", f"|{new_label}]]", 1)
    return pattern.sub(_sub, line)


def _frontmatter_field_edits(
    md_file: Path,
    new_values: "dict[str, str]",
    *,
    current: "dict[str, str]",
) -> "list[MoveRef]":
    """Build :class:`MoveRef` edits that rewrite top-level scalar
    frontmatter fields in *md_file* to *new_values*.

    Only single-line ``field: value`` shapes are rewritten — block
    scalars (``field: >-``) and nested fields are skipped silently.
    A field whose new value equals its current value produces no edit.

    *current* is used as a sanity-check: a field whose YAML line value
    doesn't match the recorded current value is skipped, so an
    out-of-date plan never clobbers freshly edited frontmatter.
    """
    refs: list[MoveRef] = []
    try:
        text = md_file.read_text(encoding="utf-8")
    except OSError:
        return refs
    fm, _body = split_frontmatter(text)
    if fm is None:
        return refs
    fm_line_count = len(fm.splitlines()) + 2  # opening/closing fences
    lines = text.splitlines()
    for i in range(1, fm_line_count - 1):
        if i >= len(lines):
            break
        raw = lines[i]
        # Must be a top-level scalar (no leading whitespace).
        if not raw or raw[0] in (" ", "\t"):
            continue
        key, _, rest = raw.partition(":")
        key = key.strip()
        if key not in new_values:
            continue
        new_val = new_values[key]
        if not new_val:
            continue
        value = rest.strip().strip("\"'")
        if value != current.get(key, ""):
            continue
        if value == new_val:
            continue
        new_line = f"{key}: {new_val}"
        refs.append(MoveRef(md_file, i + 1, raw, new_line))
    return refs


def _yaml_field_edits(
    yaml_file: Path,
    new_values: "dict[str, str]",
    *,
    current: "dict[str, str]",
) -> "list[MoveRef]":
    """Like :func:`_frontmatter_field_edits` but for plain YAML files
    (``_kind.yaml``) that have no frontmatter fences.

    Rewrites top-level ``field: value`` lines in the file.
    """
    refs: list[MoveRef] = []
    try:
        text = yaml_file.read_text(encoding="utf-8")
    except OSError:
        return refs
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if not raw or raw[0] in (" ", "\t", "#"):
            continue
        key, _, rest = raw.partition(":")
        key = key.strip()
        if key not in new_values:
            continue
        new_val = new_values[key]
        if not new_val:
            continue
        value = rest.strip().strip("\"'")
        if value != current.get(key, ""):
            continue
        if value == new_val:
            continue
        new_line = f"{key}: {new_val}"
        refs.append(MoveRef(yaml_file, i + 1, raw, new_line))
    return refs



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
    display_renames: dict[str, str] = field(default_factory=dict)
    # Old display name -> new display name.  For entities this has one
    # entry keyed by the old `name:` value; for kinds it has up to two
    # entries (old singular -> new singular, old plural -> new plural).
    # Empty when the caller didn't ask for display-name changes.
    collisions: list[str] = field(default_factory=list)
    # Existing entity ids that share the new id's leaf slug — see
    # :class:`MovePlan` for the full contract.


def read_entity_display_name(entity_dir: Path) -> str:
    """Return the ``name:`` field of an entity's ``index.md``, or ""."""
    md = entity_dir / "index.md"
    if not md.is_file():
        return ""
    return _parse_yaml_field(frontmatter_lines(md.read_text(encoding="utf-8")), "name")


def read_kind_display_names(kind_dir: Path) -> tuple[str, str, str]:
    """Return ``(singular, plural, description)`` from ``_kind.yaml``.

    Returns ``("", "", "")`` if the file is absent.  ``description`` is
    the optional prose description of the kind; it may be an empty string
    when the field is not present in the file.
    """
    md = kind_dir / "_kind.yaml"
    if not md.is_file():
        return "", "", ""
    # _kind.yaml is plain YAML (no frontmatter fences)
    lines = md.read_text(encoding="utf-8").splitlines()
    return (
        _parse_yaml_field(lines, "singular"),
        _parse_yaml_field(lines, "plural"),
        _parse_yaml_field(lines, "description"),
    )


def plan_rename(
    project: Path,
    old_path: str,
    new_slug: str,
    *,
    new_display_names: "dict[str, str] | None" = None,
) -> RenamePlan:
    """
    Build a RenamePlan for renaming the slug of a content entity, collection, or kind.

    *old_path* is relative to content/ **or** content_meta/kinds/.
    The tool tries content/ first; if not found there it tries kinds/.

    *new_slug* is the new folder name only (no slashes).

    *new_display_names* is an optional mapping of frontmatter field
    name to the new value for that field.  For an entity rename use
    ``{"name": "Harmonious"}``; for a kind rename use
    ``{"singular": "Harmonia", "plural": "Harmonias"}``.  Any field
    omitted keeps its current value.  When a field's new value differs
    from its old value, the planner adds:

      * a frontmatter edit on the renamed thing's own ``index.md`` (or
        ``_kind.yaml``) updating the scalar;
      * label-text rewrites on every wikilink across the project whose
        target is the renamed thing AND whose label exactly equals the
        old display value.

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
    display_renames: dict[str, str] = {}
    warnings: list[str] = []
    collisions: list[str] = []

    if is_kind:
        old_id = str(old_dir.relative_to(kinds))
        new_id = str(new_dir.relative_to(kinds))

        # ------------------------------------------------------------------
        # Compute display-name renames (singular / plural) and a label-
        # rewrite map for use inside [[kinds/<slug>|Label]] wikilinks.
        # ------------------------------------------------------------------
        old_singular, old_plural, old_description = read_kind_display_names(old_dir)
        if new_display_names:
            new_singular = new_display_names.get("singular", old_singular)
            new_plural = new_display_names.get("plural", old_plural)
            if old_singular and new_singular and old_singular != new_singular:
                display_renames[old_singular] = new_singular
            if old_plural and new_plural and old_plural != new_plural:
                display_renames[old_plural] = new_plural

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
        # 3b. [[kinds/<new-slug>|<old-label>]] — label-only rewrites for
        #     links that already point at the new slug (after step 3 has
        #     rewritten the path).  Matches both the post-rewrite case
        #     and the case where someone manually wrote the new slug
        #     before this rename ran.
        labeled_kind_link_re = re.compile(
            r"\[\[kinds/" + re.escape(new_slug) +
            r"(?P<anchor>#[a-z0-9-]*)?\|(?P<label>[^\]\n|]+)\]\]"
        )

        for md_file in iter_link_consumer_files(project):
            is_entity = (
                md_file.name == "index.md" and is_entity_folder(md_file.parent)
            )
            lines = md_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines, start=1):
                if is_entity:
                    m = kind_field_re.match(line)
                    if m:
                        new_line = m.group("prefix") + new_slug + m.group("suffix")
                        refs.append(MoveRef(md_file, i, line, new_line))
                        continue
                new_line = line
                changed = False
                if is_entity and affinity_re.search(new_line):
                    new_line = affinity_re.sub(
                        r"\g<pre>" + new_slug + r"\g<post>", new_line
                    )
                    changed = True
                if wikilink_re.search(new_line):
                    new_line = wikilink_re.sub(
                        r"[[kinds/" + new_slug + r"\g<rest>", new_line
                    )
                    changed = True
                # Label rewrites on links now targeting the new slug.
                if display_renames and "[[kinds/" + new_slug in new_line:
                    new_line = _rewrite_labels_inline(
                        new_line, labeled_kind_link_re, display_renames
                    )
                    if new_line != line:
                        changed = True
                if changed and new_line != line:
                    refs.append(MoveRef(md_file, i, line, new_line))

        # Frontmatter edits on the renamed kind's own _kind.yaml.
        if display_renames or (new_display_names and "description" in new_display_names):
            kind_yaml = old_dir / "_kind.yaml"
            if kind_yaml.is_file():
                new_description = new_display_names.get("description", old_description) if new_display_names else old_description
                fm_refs = _yaml_field_edits(
                    kind_yaml,
                    {"singular": new_display_names.get("singular", old_singular) if new_display_names else old_singular,
                     "plural":   new_display_names.get("plural",   old_plural)   if new_display_names else old_plural,
                     "description": new_description},
                    current={"singular": old_singular, "plural": old_plural, "description": old_description},
                )
                refs.extend(fm_refs)

    else:
        old_id = str(old_dir.relative_to(content))
        new_id = str(new_dir.relative_to(content))

        # ------------------------------------------------------------------
        # Compute display-name rename and pass through to the entity
        # scanner so it can rewrite labels alongside paths.
        # ------------------------------------------------------------------
        old_name = read_entity_display_name(old_dir)
        if new_display_names:
            new_name = new_display_names.get("name", old_name)
            if old_name and new_name and old_name != new_name:
                display_renames[old_name] = new_name

        # Detect leaf-slug collisions so the user can be warned and so
        # existing links to peers are rewritten in the same pass.
        from .wikilinks import build_index as _build_index
        pre_index = _build_index(project)
        collisions = _detect_collisions(new_id, pre_index, exclude=old_id)

        refs, warnings = _scan_entity_refs(
            project, old_id, new_id,
            display_renames=display_renames,
            collision_peers=collisions or None,
        )

        # Frontmatter edit on the renamed entity's own index.md.
        if display_renames:
            md = old_dir / "index.md"
            if md.is_file():
                fm_refs = _frontmatter_field_edits(
                    md,
                    {"name": new_display_names.get("name", old_name)},
                    current={"name": old_name},
                )
                refs.extend(fm_refs)

    return RenamePlan(
        old_id=old_id,
        new_id=new_id,
        old_dir=old_dir,
        new_dir=new_dir,
        is_kind=is_kind,
        refs=refs,
        warnings=warnings,
        display_renames=display_renames,
        collisions=collisions,
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
    # Other frontmatter fields that store entity paths (class:, role:,
    # within:, species:).  Same prefix-cascade logic as target_re.
    _coll_path_fields = "|".join(
        re.escape(f) for f in ("class", "role", "within", "species")
    )
    extra_path_re = re.compile(
        r"^(?P<prefix>\s*(?:-\s+)?(?:" + _coll_path_fields + r"):\s*)"
        r"(?P<id>" + re.escape(old_collection_id) + r"(?:/[A-Za-z0-9_\-/]+)?)"
        r"(?P<suffix>(?:\s+.*)?)$"
    )

    for md_file in iter_link_consumer_files(project):
        is_entity = md_file.name == "index.md" and is_entity_folder(md_file.parent)
        page_id = page_id_for(md_file, project)
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
                if is_entity:
                    m = target_re.match(line)
                    if m:
                        new_id = cascade(m.group("id"))
                        if new_id != m.group("id"):
                            new_line = m.group("prefix") + new_id + m.group("suffix")
                            refs.append(MoveRef(md_file, i, line, new_line))
                    else:
                        m = extra_path_re.match(line)
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


# ---------------------------------------------------------------------------
# Diff rendering — shared by the CLI and the REPL
# ---------------------------------------------------------------------------
#
# All three preview surfaces (rename, move, crosslink) print a two-line
# block per edit::
#
#     <indent>- <old text>
#     <indent>+ <new text>
#
# These helpers colour those blocks so the reader can scan a long
# crosslink/rename diff quickly:
#
#   * The whole '-' line is red and the whole '+' line is green
#     (familiar from git/unified diff).
#   * Wikilinks (``[[…]]``) that differ positionally between old and new
#     are rendered bold + bright; identical wikilinks fall back to the
#     line's base colour.  Prose between wikilinks is rendered in the
#     line's base colour too.
#
# Honours the de-facto-standard ``NO_COLOR`` env var and auto-disables
# colour when stdout is not a TTY (so piped output stays plain).


_ANSI_RESET = "\x1b[0m"
_ANSI_RED = "\x1b[31m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_BOLD_RED = "\x1b[1;91m"
_ANSI_BOLD_GREEN = "\x1b[1;92m"


def use_color(stream=None) -> bool:
    """Return ``True`` when ANSI colour codes are appropriate.

    Honours ``NO_COLOR`` (any value disables) and falls back to
    :py:meth:`io.IOBase.isatty` on *stream* (defaulting to stdout) so
    that piped or redirected output stays plain.
    """
    import os
    import sys
    if os.environ.get("NO_COLOR"):
        return False
    if stream is None:
        stream = sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _split_wikilinks(text: str) -> list[tuple[str, bool]]:
    """Split *text* into ``(segment, is_wikilink)`` chunks in order.

    Used by :func:`format_diff_pair` to align the wikilinks in the old
    and new lines so we can highlight just the ones that actually
    changed.  Prose runs in between are kept as separate chunks so the
    output preserves the original spacing exactly.
    """
    from .wikilinks import WIKILINK_RE
    chunks: list[tuple[str, bool]] = []
    cursor = 0
    for m in WIKILINK_RE.finditer(text):
        if m.start() > cursor:
            chunks.append((text[cursor:m.start()], False))
        chunks.append((m.group(0), True))
        cursor = m.end()
    if cursor < len(text):
        chunks.append((text[cursor:], False))
    return chunks


def format_diff_pair(
    old: str,
    new: str,
    *,
    indent: str = "  ",
    color: bool = True,
) -> tuple[str, str]:
    """Render an ``(old, new)`` edit as two ready-to-print lines.

    Each returned string starts with *indent* followed by ``- `` or
    ``+ `` and the (already-stripped) text.  When *color* is ``True``
    the entire old line is red and the new line green; wikilinks that
    appear in only one side or whose i-th occurrence differs between
    the two sides are bold-brightened so the changed segments stand
    out.

    When *color* is ``False`` the strings are plain ASCII — identical
    to what the previous unformatted ``f"  - {…}"`` calls produced.
    """
    old_text = old.strip()
    new_text = new.strip()

    if not color:
        return (f"{indent}- {old_text}", f"{indent}+ {new_text}")

    old_chunks = _split_wikilinks(old_text)
    new_chunks = _split_wikilinks(new_text)

    # Pair up wikilinks positionally: the n-th wikilink in old vs the
    # n-th in new.  Any wikilink that appears only on one side, or
    # whose text differs from its paired counterpart, is marked
    # "changed" and rendered in the bold/bright variant.
    old_wl_idx = [i for i, (_, is_wl) in enumerate(old_chunks) if is_wl]
    new_wl_idx = [i for i, (_, is_wl) in enumerate(new_chunks) if is_wl]

    old_changed: set[int] = set()
    new_changed: set[int] = set()
    pairs = max(len(old_wl_idx), len(new_wl_idx))
    for n in range(pairs):
        o = old_wl_idx[n] if n < len(old_wl_idx) else None
        p = new_wl_idx[n] if n < len(new_wl_idx) else None
        if o is None:
            new_changed.add(p)  # type: ignore[arg-type]
        elif p is None:
            old_changed.add(o)
        elif old_chunks[o][0] != new_chunks[p][0]:
            old_changed.add(o)
            new_changed.add(p)

    def _render(
        chunks: list[tuple[str, bool]],
        changed: set[int],
        base: str,
        bold: str,
    ) -> str:
        parts: list[str] = [base]
        for i, (seg, is_wl) in enumerate(chunks):
            if is_wl and i in changed:
                # Switch to bold/bright, then back to the line's base
                # colour so trailing prose stays the right hue.
                parts.append(bold)
                parts.append(seg)
                parts.append(_ANSI_RESET)
                parts.append(base)
            else:
                parts.append(seg)
        parts.append(_ANSI_RESET)
        return "".join(parts)

    old_line = (
        f"{indent}{_ANSI_RED}- {_ANSI_RESET}"
        + _render(old_chunks, old_changed, _ANSI_RED, _ANSI_BOLD_RED)
    )
    new_line = (
        f"{indent}{_ANSI_GREEN}+ {_ANSI_RESET}"
        + _render(new_chunks, new_changed, _ANSI_GREEN, _ANSI_BOLD_GREEN)
    )
    return (old_line, new_line)
