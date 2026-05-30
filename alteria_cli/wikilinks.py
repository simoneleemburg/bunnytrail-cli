"""
wikilinks.py — single source of truth for parsing and resolving
`[[…]]` wikilinks inside Alteria prose.

This module mirrors the contract laid out in WIKILINKS.md. It is the
authoritative parser for every tool that needs to scan or rewrite
wikilinks (move, rename, crosslink). Keep it aligned with WIKILINKS.md;
do not reimplement the rules elsewhere.

The resolver intentionally does not load YAML — it only needs:

  * the set of known entity ids (folder paths under content/),
  * the set of cluster ids (top-level content/ folders that are not
    universal substrates),
  * the set of universal substrate ids (top-level content/ folders
    whose ``_collection.md`` declares ``universal: true``),
  * the set of language codes (``code:`` fields in entities),
  * the set of registered kind leaf ids.

These are derived once at startup by :func:`build_index` and passed
to every consumer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .helpers import (
    content_root,
    frontmatter_lines,
    is_collection_folder,
    is_entity_folder,
    iter_entity_md_files,
    iter_kind_md_files,
    kinds_root,
    split_frontmatter,
)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

@dataclass
class WikilinkIndex:
    """Snapshot of everything the resolver needs.

    All ids are strings without a leading slash, matching the folder
    paths under content/ or content_meta/kinds/.
    """
    entity_ids: set[str] = field(default_factory=set)
    cluster_ids: set[str] = field(default_factory=set)
    universal_ids: set[str] = field(default_factory=set)
    lang_codes: set[str] = field(default_factory=set)
    kind_ids: set[str] = field(default_factory=set)

    # Cached suffix-id index: tail-path -> [full ids ending in /tail]
    # Built lazily by :meth:`_suffix_lookup`.
    _suffix_cache: dict[str, list[str]] | None = field(default=None, init=False, repr=False)

    def with_renamed_ids(self, renames: dict[str, str]) -> "WikilinkIndex":
        """Return a shallow copy of this index with *renames* applied.

        Each ``old_id -> new_id`` mapping rewrites the entity ids set
        (preserving everything else: clusters, universals, lang codes,
        kinds). Cluster ids and universal ids do not move via this
        method — pass a freshly-built index if a cluster itself
        changes shape.

        Used by the rename/move scanners so :func:`preferred_form`
        sees the post-move world when picking a replacement form.
        """
        new_ids: set[str] = set()
        for eid in self.entity_ids:
            new_ids.add(renames.get(eid, eid))
        return WikilinkIndex(
            entity_ids=new_ids,
            cluster_ids=set(self.cluster_ids),
            universal_ids=set(self.universal_ids),
            lang_codes=set(self.lang_codes),
            kind_ids=set(self.kind_ids),
        )

    def known_top(self, segment: str) -> bool:
        """True if *segment* is a cluster id or a universal substrate id."""
        return segment in self.cluster_ids or segment in self.universal_ids

    # ------------------------------------------------------------------
    # Suffix matching
    # ------------------------------------------------------------------

    def _build_suffix_cache(self) -> dict[str, list[str]]:
        cache: dict[str, list[str]] = {}
        for eid in self.entity_ids:
            parts = eid.split("/")
            # Every non-empty suffix joined by "/" maps back to this id.
            for i in range(len(parts)):
                tail = "/".join(parts[i:])
                cache.setdefault(tail, []).append(eid)
        self._suffix_cache = cache
        return cache

    def suffix_matches(self, path: str) -> list[str]:
        """Return every entity id ending in ``/path`` (or equal to path).

        Used by the resolver's suffix-match step. The result includes
        the exact-match id when present.
        """
        cache = self._suffix_cache or self._build_suffix_cache()
        return list(cache.get(path, ()))

    def cluster_suffix_matches(self, cluster: str, path: str) -> list[str]:
        """Suffix matches restricted to a single cluster."""
        prefix = f"{cluster}/"
        return [eid for eid in self.suffix_matches(path) if eid.startswith(prefix)]


def _parse_universal_flag(collection_md: Path) -> bool:
    """Return True when ``_collection.md`` declares ``universal: true``.

    Cheap line-level parser — avoids pulling in a YAML dependency.
    """
    try:
        text = collection_md.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in frontmatter_lines(text):
        stripped = line.strip()
        if stripped.startswith("universal:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'").lower()
            return value in ("true", "yes", "1")
    return False


def _parse_lang_code(index_md: Path) -> str:
    """Return the ``code:`` frontmatter field, or "" if absent.

    Tolerates both ``code: tha`` (current shape in content/) and a
    nested ``meta.code`` form that older WIKILINKS.md draft language
    described — we accept whichever the entity files actually use.
    """
    try:
        text = index_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    in_meta = False
    for line in frontmatter_lines(text):
        if not line:
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent == 0:
            in_meta = stripped.startswith("meta:")
            if stripped.startswith("code:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")
        elif in_meta and stripped.startswith("code:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def build_index(project: Path) -> WikilinkIndex:
    """Build a :class:`WikilinkIndex` for *project*.

    Walks ``content/`` once to collect entity ids and top-level folder
    classification, and ``content_meta/kinds/`` once for kind ids.
    Language codes are gathered from entity frontmatter as a side
    effect of the entity walk.
    """
    content = content_root(project).resolve()
    kinds = kinds_root(project).resolve()

    entity_ids: set[str] = set()
    lang_codes: set[str] = set()
    for md_file in iter_entity_md_files(content):
        rel = md_file.parent.relative_to(content)
        entity_ids.add(str(rel))
        code = _parse_lang_code(md_file)
        if code:
            lang_codes.add(code)

    cluster_ids: set[str] = set()
    universal_ids: set[str] = set()
    if content.is_dir():
        for child in content.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if is_entity_folder(child):
                # A top-level entity (rare) doesn't make a cluster.
                continue
            if is_collection_folder(child) and _parse_universal_flag(child / "_collection.md"):
                universal_ids.add(child.name)
            else:
                cluster_ids.add(child.name)

    kind_ids: set[str] = set()
    if kinds.is_dir():
        # Every directory under kinds_root is a registered kind id
        # (its leaf slug). The folder existing is enough — _kind.md
        # is optional per STRUCTURE.md.
        for child in kinds.rglob("*"):
            if child.is_dir() and not child.name.startswith("."):
                kind_ids.add(child.name)

    return WikilinkIndex(
        entity_ids=entity_ids,
        cluster_ids=cluster_ids,
        universal_ids=universal_ids,
        lang_codes=lang_codes,
        kind_ids=kind_ids,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# A single [[...]] occurrence. The inner content captures everything
# between the brackets so the parser can classify it. We deliberately
# allow the rich set of inner characters used by Alteria links
# (lowercase, digits, hyphen, slash, colon, hash, pipe, space — though
# spaces ultimately disqualify the token below).
WIKILINK_RE = re.compile(r"\[\[(?P<inner>[^\[\]\n]+?)\]\]")

# Path component matches WIKILINKS.md "Lowercase, kebab-case, no spaces"
# plus slashes for path segments. The first character may not be a
# digit or underscore (those are silently ignored per WIKILINKS.md
# §"What is silently ignored").
_PATH_RE = re.compile(r"^[a-z][a-z0-9\-]*(?:/[a-z][a-z0-9\-]*)*$")
_LANG_RE = re.compile(r"^[a-z]{2,8}$")
_ANCHOR_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class ParsedLink:
    """A successfully parsed wikilink token.

    ``kind`` is one of:

    * ``"path"``    — `[[type/slug]]`, `[[slug]]`, with optional anchor / label.
    * ``"kind"``    — `[[kinds/<id>]]`.
    * ``"lang"``    — `[[xx]]` where ``xx`` matches a registered lang code.
    * ``"collection"`` — `[[collection:<path>]]` (whole-line directive).
    * ``"same-page"``  — `[[#anchor]]` or `[[#anchor|label]]`.
    * ``"literal"`` — matched the brackets but is silently ignored
      (uppercase, has whitespace, starts with digit/underscore, etc).
    """
    raw: str                # original inner text between the brackets
    kind: str               # see docstring
    path: str = ""          # for "path" / "kind" / "collection": the path part
    anchor: str = ""        # heading anchor (no leading #)
    label: str = ""         # display label (empty -> default rendering)
    lang_code: str = ""     # for "lang"
    is_kind_link: bool = False  # True when path starts with "kinds/"


def _classify_path(path: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a candidate path body.

    The reason string is informational — callers only branch on ``ok``.
    """
    if not path:
        return False, "empty"
    if " " in path or "\t" in path:
        return False, "whitespace"
    # Each segment must match the kebab-case rule.
    for seg in path.split("/"):
        if not seg:
            return False, "empty-segment"
        if not _PATH_RE.match(seg):
            return False, f"bad-segment:{seg}"
    return True, ""


def parse_wikilink(inner: str, *, allow_collection: bool = False) -> ParsedLink:
    """Classify the content between ``[[`` and ``]]``.

    *allow_collection* should only be set when the caller has already
    determined the token sits on its own line (the collection
    directive is whole-line per WIKILINKS.md).
    """
    raw = inner

    # ---------------- collection directive ------------------------------
    if inner.startswith("collection:"):
        path = inner[len("collection:"):]
        if allow_collection:
            ok, _ = _classify_path(path)
            if ok:
                return ParsedLink(raw=raw, kind="collection", path=path)
        return ParsedLink(raw=raw, kind="literal")

    # ---------------- label split ---------------------------------------
    label = ""
    if "|" in inner:
        target_part, label = inner.split("|", 1)
    else:
        target_part = inner

    # ---------------- same-page anchor ----------------------------------
    if target_part.startswith("#"):
        anchor = target_part[1:]
        if _ANCHOR_RE.match(anchor):
            return ParsedLink(
                raw=raw, kind="same-page", anchor=anchor, label=label,
            )
        return ParsedLink(raw=raw, kind="literal")

    # ---------------- anchor split --------------------------------------
    anchor = ""
    if "#" in target_part:
        target_part, anchor = target_part.split("#", 1)
        if not _ANCHOR_RE.match(anchor):
            anchor = ""  # malformed anchors are silently dropped

    # ---------------- path / kind / lang classification -----------------
    ok, _ = _classify_path(target_part)
    if not ok:
        return ParsedLink(raw=raw, kind="literal")

    # Lang tag: 2-8 lowercase letters, single segment, NO anchor allowed.
    if "/" not in target_part and _LANG_RE.match(target_part) and not anchor:
        # Whether it really IS a lang tag depends on the index — the
        # resolver makes the final call, but we set the candidate code
        # so the resolver doesn't re-parse.
        return ParsedLink(
            raw=raw, kind="lang", path=target_part, lang_code=target_part,
            label=label,
        )

    is_kind = target_part.startswith("kinds/")
    return ParsedLink(
        raw=raw, kind="kind" if is_kind else "path",
        path=target_part, anchor=anchor, label=label,
        is_kind_link=is_kind,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@dataclass
class Resolution:
    """Outcome of resolving a :class:`ParsedLink` against an index."""
    status: str             # "resolved" | "ambiguous" | "missing" | "literal" | "same-page"
    entity_id: str = ""     # populated when status == "resolved"
    candidates: list[str] = field(default_factory=list)  # for ambiguous


def cluster_of(entity_id: str, index: WikilinkIndex) -> str | None:
    """Return the cluster id for *entity_id*, or None for universal /
    non-cluster pages.

    A page outside of content/ (e.g. a kind page) has no cluster — pass
    ``cluster=None`` explicitly to the resolver in that case rather than
    calling this.
    """
    if not entity_id:
        return None
    head = entity_id.split("/", 1)[0]
    if head in index.cluster_ids:
        return head
    return None


def resolve(
    parsed: ParsedLink,
    cluster: str | None,
    index: WikilinkIndex,
) -> Resolution:
    """Resolve a parsed wikilink against the index, using the rules
    laid out in WIKILINKS.md §"Resolution algorithm"."""
    if parsed.kind == "literal":
        return Resolution(status="literal")
    if parsed.kind == "same-page":
        return Resolution(status="same-page")

    # ---------------- lang tag ------------------------------------------
    if parsed.kind == "lang":
        if parsed.lang_code in index.lang_codes:
            # Lang tag wins, but its "entity" is the language page itself
            # — which the resolver doesn't need to identify (lang-tag
            # rewrites don't happen on entity moves). Treat as resolved
            # with empty id; consumers checking against a specific id
            # will naturally skip.
            return Resolution(status="resolved")
        # Could still be a bare-slug fallback (e.g. "ot" not registered
        # as a lang but matching a known entity). Try that.
        # Fall through to path resolution as a bare slug.
        parsed = ParsedLink(
            raw=parsed.raw, kind="path", path=parsed.path, label=parsed.label,
        )

    # ---------------- kind link -----------------------------------------
    if parsed.kind == "kind":
        # kinds/<slug> — validate against the kind registry.
        slug = parsed.path.split("/", 1)[1] if "/" in parsed.path else ""
        if slug and slug in index.kind_ids:
            return Resolution(status="resolved", entity_id=parsed.path)
        return Resolution(status="missing")

    # ---------------- collection directive ------------------------------
    if parsed.kind == "collection":
        # Treat as a path resolution; the loader actually wants a known
        # collection, but for rewrite purposes we just need to know
        # whether the path matches something.
        path = parsed.path
        if path in index.entity_ids:
            return Resolution(status="resolved", entity_id=path)
        matches = index.suffix_matches(path)
        if len(matches) == 1:
            return Resolution(status="resolved", entity_id=matches[0])
        if len(matches) > 1:
            return Resolution(status="ambiguous", candidates=sorted(matches))
        return Resolution(status="missing")

    # ---------------- path resolution -----------------------------------
    path = parsed.path
    first_segment = path.split("/", 1)[0]
    use_global = (cluster is None) or index.known_top(first_segment)

    if use_global:
        if path in index.entity_ids:
            return Resolution(status="resolved", entity_id=path)
        matches = index.suffix_matches(path)
        if len(matches) == 1:
            return Resolution(status="resolved", entity_id=matches[0])
        if len(matches) > 1:
            return Resolution(status="ambiguous", candidates=sorted(matches))
        return Resolution(status="missing")

    # Cluster-local branch
    local_exact = f"{cluster}/{path}"
    if local_exact in index.entity_ids:
        return Resolution(status="resolved", entity_id=local_exact)

    local_suffix = index.cluster_suffix_matches(cluster, path)
    if len(local_suffix) == 1:
        return Resolution(status="resolved", entity_id=local_suffix[0])
    if len(local_suffix) > 1:
        return Resolution(status="ambiguous", candidates=sorted(local_suffix))

    # Universal fallback
    universal_hits: list[str] = []
    for u in index.universal_ids:
        u_exact = f"{u}/{path}"
        if u_exact in index.entity_ids:
            universal_hits.append(u_exact)
            continue
        for eid in index.suffix_matches(path):
            if eid.startswith(f"{u}/") and eid not in universal_hits:
                universal_hits.append(eid)

    if len(universal_hits) == 1:
        return Resolution(status="resolved", entity_id=universal_hits[0])
    if len(universal_hits) > 1:
        return Resolution(status="ambiguous", candidates=sorted(universal_hits))

    return Resolution(status="missing")


# ---------------------------------------------------------------------------
# Preferred form for emitted links
# ---------------------------------------------------------------------------

def preferred_form(
    target_id: str,
    rendering_cluster: str | None,
    index: WikilinkIndex,
) -> str:
    """Pick the shortest wikilink *path* that resolves unambiguously to
    *target_id* from a page in *rendering_cluster*.

    Returns the path text only (no ``[[…]]`` brackets, no anchor, no
    label). Callers wrap it up themselves so they control label / anchor
    preservation.

    Rules (matching the user's stated preference):

    1. Same-cluster target: try bare slug, then shortest disambiguating
       suffix in-cluster, else full path.
    2. Universal-substrate target rendered from a cluster: bare slug if
       it would resolve unambiguously to the universal entity (i.e. no
       cluster-local entity shadows it and it's the only universal
       match), else full path.
    3. Cross-cluster, or rendering from a non-cluster page: full id.
    """
    if target_id not in index.entity_ids:
        # Target isn't a known entity — caller probably knows what
        # they're doing (e.g. just-moved id not yet reindexed). Return
        # the full id; resolution against the new index will sort it
        # out.
        return target_id

    target_head = target_id.split("/", 1)[0]
    is_universal_target = target_head in index.universal_ids

    # Non-cluster rendering page (kinds, aggregate views) — always full.
    if rendering_cluster is None:
        return target_id

    # Same cluster
    if target_head == rendering_cluster:
        return _shortest_in_cluster(target_id, rendering_cluster, index)

    # Universal target from a cluster
    if is_universal_target:
        tail = _strip_head(target_id)
        if not tail:
            return target_id
        parts = tail.split("/")
        # Walk shortest-suffix-first (bare slug) to longest-suffix.
        for i in range(len(parts) - 1, -1, -1):
            candidate = "/".join(parts[i:])
            parsed = ParsedLink(raw=candidate, kind="path", path=candidate)
            res = resolve(parsed, rendering_cluster, index)
            if res.status == "resolved" and res.entity_id == target_id:
                return candidate
        return target_id

    # Cross-cluster
    return target_id


def _strip_head(eid: str) -> str:
    return eid.split("/", 1)[1] if "/" in eid else ""


def _shortest_in_cluster(
    target_id: str,
    cluster: str,
    index: WikilinkIndex,
) -> str:
    """Return the shortest in-cluster path that resolves to *target_id*.

    Walks the path tail from shortest (bare slug) to longest (full
    in-cluster path), returning the first form that unambiguously
    resolves back to *target_id*. Falls back to the full id when even
    the longest in-cluster form is ambiguous (shouldn't normally
    happen).
    """
    tail = _strip_head(target_id)
    if not tail:
        return target_id
    parts = tail.split("/")
    # Try suffixes from shortest to longest
    for i in range(len(parts) - 1, -1, -1):
        candidate = "/".join(parts[i:])
        parsed = ParsedLink(raw=candidate, kind="path", path=candidate)
        res = resolve(parsed, cluster, index)
        if res.status == "resolved" and res.entity_id == target_id:
            return candidate
    return target_id


# ---------------------------------------------------------------------------
# Helper: render a wikilink token from its parts
# ---------------------------------------------------------------------------

def render_wikilink(path: str, anchor: str = "", label: str = "") -> str:
    """Compose a ``[[path#anchor|label]]`` token, omitting empty parts."""
    target = path
    if anchor:
        target = f"{path}#{anchor}"
    if label:
        return f"[[{target}|{label}]]"
    return f"[[{target}]]"


# ---------------------------------------------------------------------------
# Convenience: walk every [[…]] on a line
# ---------------------------------------------------------------------------

def iter_wikilinks(line: str, *, line_is_directive: bool = False):
    """Yield ``(match, parsed)`` for every ``[[...]]`` occurrence on *line*.

    *line_is_directive* should be True only when the entire line
    (stripped) is a single ``[[collection:...]]`` token; this enables
    collection-directive parsing per WIKILINKS.md.
    """
    for m in WIKILINK_RE.finditer(line):
        inner = m.group("inner")
        # Only treat as a collection directive when it's the only
        # token on the line. The cheapest check: the trimmed line
        # equals exactly the matched span.
        allow_collection = line_is_directive and line.strip() == m.group(0)
        yield m, parse_wikilink(inner, allow_collection=allow_collection)
