"""
Tests for bunnytrail_cli.wikilinks — index building, parsing, resolution,
and preferred_form selection.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bunnytrail_cli.wikilinks import (
    ParsedLink,
    build_index,
    cluster_of,
    parse_wikilink,
    preferred_form,
    render_wikilink,
    resolve,
)


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

def test_build_index_collects_clusters_and_universals(project: Path) -> None:
    idx = build_index(project)
    assert idx.cluster_ids == {"aurethia", "earth"}
    assert idx.universal_ids == {"foundation"}
    # Cluster ids and universal ids never overlap.
    assert idx.cluster_ids.isdisjoint(idx.universal_ids)


def test_build_index_collects_all_entity_ids(project: Path) -> None:
    idx = build_index(project)
    assert "aurethia/places/sharazan" in idx.entity_ids
    assert "aurethia/places/bayurinda/nuunlau" in idx.entity_ids
    assert "foundation/concepts/harmonia" in idx.entity_ids
    assert "earth/places/shanghai" in idx.entity_ids
    # Collection folders themselves are not entities.
    assert "aurethia/places/bayurinda" not in idx.entity_ids
    assert "foundation" not in idx.entity_ids


def test_build_index_collects_lang_codes(project: Path) -> None:
    idx = build_index(project)
    assert "tha" in idx.lang_codes


def test_build_index_collects_kind_ids(project: Path) -> None:
    idx = build_index(project)
    assert "place" in idx.kind_ids
    assert "human" in idx.kind_ids
    assert "being" in idx.kind_ids


def test_cluster_of(project: Path) -> None:
    idx = build_index(project)
    assert cluster_of("aurethia/places/sharazan", idx) == "aurethia"
    assert cluster_of("earth/places/shanghai", idx) == "earth"
    # Universal substrate -> not a cluster
    assert cluster_of("foundation/concepts/harmonia", idx) is None


# ---------------------------------------------------------------------------
# parse_wikilink
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected_kind",
    [
        ("places/sharazan", "path"),
        # Any 2-8 lowercase-letter single segment is a *candidate* lang;
        # the resolver decides whether it's actually registered and
        # otherwise falls through to bare-slug resolution.
        ("sharazan", "lang"),
        ("kinds/human", "kind"),
        ("tha", "lang"),
        ("#anchor", "same-page"),
        ("Sharazan", "literal"),         # uppercase
        ("with spaces", "literal"),
        ("3-ages", "literal"),           # leading digit
        ("_underscore", "literal"),
    ],
)
def test_parse_wikilink_classification(raw: str, expected_kind: str) -> None:
    p = parse_wikilink(raw)
    assert p.kind == expected_kind, f"{raw!r} -> {p.kind!r}"


def test_parse_wikilink_path_with_hyphen_is_path() -> None:
    # Hyphenated tokens can't be lang codes (lang is letters only),
    # so they classify as "path" directly.
    p = parse_wikilink("the-naming")
    assert p.kind == "path"


def test_parse_wikilink_label_split() -> None:
    p = parse_wikilink("places/sharazan|Sharazan")
    assert p.kind == "path"
    assert p.path == "places/sharazan"
    assert p.label == "Sharazan"


def test_parse_wikilink_anchor() -> None:
    p = parse_wikilink("characters/kael#oaths|his oaths")
    assert p.kind == "path"
    assert p.path == "characters/kael"
    assert p.anchor == "oaths"
    assert p.label == "his oaths"


def test_parse_wikilink_malformed_anchor_dropped() -> None:
    p = parse_wikilink("places/sharazan#BadAnchor")
    assert p.anchor == ""


def test_parse_wikilink_collection_only_when_allowed() -> None:
    inline = parse_wikilink("collection:places/bayurinda")
    assert inline.kind == "literal"
    whole_line = parse_wikilink("collection:places/bayurinda", allow_collection=True)
    assert whole_line.kind == "collection"
    assert whole_line.path == "places/bayurinda"


# ---------------------------------------------------------------------------
# resolve — global branch
# ---------------------------------------------------------------------------

def test_resolve_full_path_global(project: Path) -> None:
    idx = build_index(project)
    p = parse_wikilink("aurethia/places/sharazan")
    res = resolve(p, "earth", idx)  # cluster-prefixed -> global branch
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/places/sharazan"


def test_resolve_suffix_global_unique(project: Path) -> None:
    idx = build_index(project)
    # From a non-cluster page (cluster=None), the global branch handles
    # everything by id or suffix.
    p = parse_wikilink("places/sharazan")
    res = resolve(p, None, idx)
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/places/sharazan"


def test_resolve_suffix_global_ambiguous(project: Path) -> None:
    idx = build_index(project)
    # "people/duskmere" matches both aurethia and earth.
    p = parse_wikilink("people/duskmere")
    res = resolve(p, None, idx)
    assert res.status == "ambiguous"
    assert set(res.candidates) == {
        "aurethia/people/duskmere", "earth/people/duskmere",
    }


# ---------------------------------------------------------------------------
# resolve — cluster-local branch
# ---------------------------------------------------------------------------

def test_resolve_bare_slug_in_cluster(project: Path) -> None:
    idx = build_index(project)
    # From aurethia, [[duskmere]] -> aurethia/people/duskmere
    p = parse_wikilink("duskmere")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/people/duskmere"


def test_resolve_bare_slug_does_not_cross_clusters(project: Path) -> None:
    idx = build_index(project)
    # From aurethia, [[shanghai]] should not resolve to earth/places/shanghai
    p = parse_wikilink("shanghai")
    res = resolve(p, "aurethia", idx)
    assert res.status == "missing"


def test_resolve_bare_slug_universal_fallback(project: Path) -> None:
    idx = build_index(project)
    # From aurethia, [[harmonia]] falls through to foundation/concepts/harmonia
    p = parse_wikilink("harmonia")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "foundation/concepts/harmonia"


def test_resolve_local_takes_precedence_over_universal(project: Path, tmp_path: Path) -> None:
    # Add a shadowing entity to aurethia and verify cluster-local wins.
    shadow = project / "content" / "aurethia" / "places" / "harmonia"
    shadow.mkdir(parents=True)
    (shadow / "index.md").write_text(
        "---\nname: Local Harmonia\nkind: place\n---\n", encoding="utf-8"
    )
    idx = build_index(project)
    p = parse_wikilink("harmonia")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/places/harmonia"


def test_resolve_cluster_prefixed_path_is_global(project: Path) -> None:
    idx = build_index(project)
    # Writing the cluster prefix forces the global branch — works the
    # same from any rendering page.
    p = parse_wikilink("aurethia/places/sharazan")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/places/sharazan"


# ---------------------------------------------------------------------------
# resolve — language / kind / collection
# ---------------------------------------------------------------------------

def test_resolve_lang_tag_wins_over_slug(project: Path) -> None:
    idx = build_index(project)
    p = parse_wikilink("tha")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"  # via lang


def test_resolve_kind_link_valid(project: Path) -> None:
    idx = build_index(project)
    p = parse_wikilink("kinds/human")
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "kinds/human"


def test_resolve_kind_link_unknown(project: Path) -> None:
    idx = build_index(project)
    p = parse_wikilink("kinds/dragon")
    res = resolve(p, "aurethia", idx)
    assert res.status == "missing"


def test_resolve_collection_directive(project: Path) -> None:
    idx = build_index(project)
    p = parse_wikilink("collection:aurethia/places/bayurinda/nuunlau",
                       allow_collection=True)
    res = resolve(p, None, idx)
    assert res.status == "resolved"


# ---------------------------------------------------------------------------
# preferred_form
# ---------------------------------------------------------------------------

def test_preferred_form_same_cluster_uses_bare_slug(project: Path) -> None:
    idx = build_index(project)
    # nuunlau is the only entity ending in /nuunlau, so a bare slug
    # resolves unambiguously from within aurethia.
    assert preferred_form("aurethia/places/bayurinda/nuunlau", "aurethia", idx) == "nuunlau"


def test_preferred_form_promotes_to_suffix_when_bare_is_ambiguous(project: Path, tmp_path: Path) -> None:
    # Add a second 'nuunlau' inside aurethia to create local ambiguity.
    extra = project / "content" / "aurethia" / "people" / "nuunlau"
    extra.mkdir(parents=True)
    (extra / "index.md").write_text(
        "---\nname: Nuunlau (person)\nkind: being\n---\n", encoding="utf-8"
    )
    idx = build_index(project)
    form = preferred_form("aurethia/places/bayurinda/nuunlau", "aurethia", idx)
    # Bare slug is ambiguous now; should pick a shortest disambiguating suffix.
    assert form != "nuunlau"
    assert form.endswith("nuunlau")
    # And the chosen suffix should resolve back to the right entity.
    p = parse_wikilink(form)
    res = resolve(p, "aurethia", idx)
    assert res.status == "resolved"
    assert res.entity_id == "aurethia/places/bayurinda/nuunlau"


def test_preferred_form_cross_cluster_uses_full_path(project: Path) -> None:
    idx = build_index(project)
    # From aurethia, linking to earth must use the full id.
    assert preferred_form("earth/places/shanghai", "aurethia", idx) == "earth/places/shanghai"


def test_preferred_form_universal_target_uses_bare_when_unshadowed(project: Path) -> None:
    idx = build_index(project)
    assert preferred_form("foundation/concepts/harmonia", "aurethia", idx) == "harmonia"


def test_preferred_form_no_cluster_page_uses_full_path(project: Path) -> None:
    idx = build_index(project)
    # Kind pages have no cluster — always-global.
    assert preferred_form("aurethia/places/sharazan", None, idx) == "aurethia/places/sharazan"


def test_preferred_form_universal_to_universal_uses_bare(project: Path) -> None:
    idx = build_index(project)
    # Rendering page IS a universal-substrate page. Linking to another
    # entity in the same substrate should pick the shortest unambiguous
    # in-substrate form (typically the bare slug).
    assert preferred_form(
        "foundation/concepts/harmonia", "foundation", idx
    ) == "harmonia"


def test_scope_of_returns_cluster_and_universal(project: Path) -> None:
    from bunnytrail_cli.wikilinks import scope_of
    idx = build_index(project)
    assert scope_of("aurethia/places/sharazan", idx) == "aurethia"
    assert scope_of("earth/places/shanghai", idx) == "earth"
    # Universal substrate is its own scope (unlike cluster_of, which
    # returns None here).
    assert scope_of("foundation/concepts/harmonia", idx) == "foundation"


# ---------------------------------------------------------------------------
# render_wikilink
# ---------------------------------------------------------------------------

def test_render_wikilink_plain() -> None:
    assert render_wikilink("places/sharazan") == "[[places/sharazan]]"


def test_render_wikilink_with_anchor_and_label() -> None:
    assert render_wikilink("characters/kael", "oaths", "his oaths") == "[[characters/kael#oaths|his oaths]]"
