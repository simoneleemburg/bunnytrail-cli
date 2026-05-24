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
    for yaml_file in sorted(ns_dir.rglob("index.yaml")):
        entity_dir = yaml_file.parent
        if not is_entity_folder(entity_dir):
            continue
        lines = yaml_file.read_text(encoding="utf-8").splitlines()
        name = _parse_yaml_field(lines, "name")
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
    for kind_yaml in sorted(kinds.rglob("_kind.yaml")):
        kind_dir = kind_yaml.parent
        if not is_kind_folder(kind_dir):
            continue
        slug = kind_dir.name
        target = f"kinds/{slug}"
        lines = kind_yaml.read_text(encoding="utf-8").splitlines()
        singular = _parse_yaml_field(lines, "singular")
        plural = _parse_yaml_field(lines, "plural")
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
            error=f"not an entity folder (no index.yaml): content/{article_path}",
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
    # Build candidate list: (match_text, wikilink_target)
    # Longer names first so "Mundus Frame" is tested before "Mundus".
    # Exclude the article itself from the candidate pool.
    # ------------------------------------------------------------------
    article_id_norm = article_path.rstrip("/")
    candidates: list[tuple[str, str]] = []

    for name, entity_id in collect_namespace_entities(project, namespace_path):
        if entity_id == article_id_norm:
            continue
        candidates.append((name, entity_id))

    for match_text, kind_target in collect_all_kinds(project):
        candidates.append((match_text, kind_target))

    # Sort longest-first to prefer specific matches over shorter substrings
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    # ------------------------------------------------------------------
    # Compile patterns (whole-word, case-sensitive)
    # ------------------------------------------------------------------
    patterns: list[tuple[re.Pattern[str], str, str]] = []
    seen_texts: set[str] = set()
    for match_text, target in candidates:
        if match_text in seen_texts:
            continue
        seen_texts.add(match_text)
        pat = re.compile(r"\b" + re.escape(match_text) + r"\b")
        patterns.append((pat, match_text, target))

    # ------------------------------------------------------------------
    # Walk the article line-by-line; build edits
    # ------------------------------------------------------------------
    lines = md_file.read_text(encoding="utf-8").splitlines()

    # Track which candidate names have already been linked (first-only rule)
    linked_texts: set[str] = set()

    edits: list[CrosslinkEdit] = []

    for line_idx, line in enumerate(lines):
        new_line = line

        for pat, match_text, target in patterns:
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

            # Build the replacement wikilink.
            # Use bare [[target]] when the target's last segment equals match_text,
            # otherwise use [[target|match_text]].
            target_leaf = target.split("/")[-1]
            if target_leaf == match_text:
                replacement = f"[[{target}]]"
            else:
                replacement = f"[[{target}|{match_text}]]"

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
