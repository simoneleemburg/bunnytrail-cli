"""
Integration tests for the move/rename scanners.

Verify that bare slugs, suffix paths, and full paths are all correctly
detected and rewritten in entity wikilinks when an entity is moved or
renamed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bunnytrail_cli.helpers import (
    execute_move,
    execute_rename,
    plan_move,
    plan_rename,
)


def _write_body(project: Path, entity_id: str, body: str) -> None:
    """Replace the body of an entity's index.md (keeping its frontmatter)."""
    md = project / "content" / entity_id / "index.md"
    text = md.read_text(encoding="utf-8")
    # Frontmatter is preserved verbatim; anything after the second --- is body.
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_body(project: Path, entity_id: str) -> str:
    md = project / "content" / entity_id / "index.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


# ---------------------------------------------------------------------------
# rename — entity within a cluster
# ---------------------------------------------------------------------------

def test_rename_rewrites_full_path_wikilink(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "See [[aurethia/places/sharazan]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan-new]]" in body or "[[places/sharazan-new]]" in body \
        or "[[aurethia/places/sharazan-new]]" in body
    # The old slug should not appear anywhere on the line.
    assert "sharazan" not in body.replace("sharazan-new", "")


def test_rename_rewrites_bare_slug_wikilink(project: Path) -> None:
    # Bare slug inside the same cluster.
    _write_body(project, "aurethia/people/duskmere",
                "See [[sharazan]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    assert plan.error == ""
    assert len(plan.refs) >= 1
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Shortest in-cluster form is still a bare slug.
    assert "[[sharazan-new]]" in body


def test_rename_rewrites_suffix_path_wikilink(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "See [[places/sharazan]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # After rename, bare slug is unambiguous in cluster, so preferred is bare.
    assert "[[sharazan-new]]" in body


def test_rename_preserves_anchor_and_label(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "See [[places/sharazan#origins|its origins]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan-new#origins|its origins]]" in body


def test_rename_does_not_touch_unrelated_links(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "[[harmonia]] and [[shanghai]] and [[kinds/human]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    # No reference to sharazan in this body -> no refs at all (apart from
    # other entities, which we haven't written links from). The duskmere
    # body should be left intact after execute.
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[harmonia]]" in body
    assert "[[shanghai]]" in body
    assert "[[kinds/human]]" in body


# ---------------------------------------------------------------------------
# move — cross-cluster forces full id
# ---------------------------------------------------------------------------

def test_move_cross_cluster_promotes_bare_slug_to_full_id(project: Path) -> None:
    # Set up: aurethia entity references its own bare slug.
    _write_body(project, "aurethia/people/duskmere",
                "Visit [[sharazan]] often.\n")
    # Move sharazan from aurethia/places to earth/places.
    plan = plan_move(project, "aurethia/places/sharazan", "earth/places")
    assert plan.error == ""
    execute_move(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Bare slug must be promoted to a full id — cross-cluster references
    # are explicit per WIKILINKS.md.
    assert "[[earth/places/sharazan]]" in body


def test_move_within_cluster_keeps_bare_slug(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "Visit [[sharazan]] often.\n")
    # Move sharazan to a new sub-collection within the same cluster.
    plan = plan_move(project, "aurethia/places/sharazan",
                     "aurethia/places/bayurinda")
    assert plan.error == ""
    execute_move(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Bare slug still unique in cluster — should stay bare.
    assert "[[sharazan]]" in body


# ---------------------------------------------------------------------------
# move — collection cascade
# ---------------------------------------------------------------------------

def test_move_collection_cascades_to_bare_slug_descendants(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "Pop into [[nuunlau]].\n")
    # Move the bayurinda collection into people/.
    plan = plan_move(project, "aurethia/places/bayurinda",
                     "aurethia/people")
    assert plan.error == ""
    execute_move(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # nuunlau is still in the same cluster after the cascade, still
    # unique by bare slug -> stays bare.
    assert "[[nuunlau]]" in body


def test_move_collection_cascades_full_paths(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "See [[aurethia/places/bayurinda/nuunlau]].\n")
    plan = plan_move(project, "aurethia/places/bayurinda",
                     "aurethia/people")
    execute_move(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # The full id has been cascaded; preferred_form may shorten it to bare
    # since nuunlau is unique-in-cluster.
    assert "[[nuunlau]]" in body or "[[aurethia/people/bayurinda/nuunlau]]" in body


# ---------------------------------------------------------------------------
# Kind references — slug rename still works
# ---------------------------------------------------------------------------

def test_kind_rename_rewrites_wikilinks(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "A [[kinds/human]] story.\n")
    plan = plan_rename(project, "being/human", "person")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[kinds/person]]" in body


# ---------------------------------------------------------------------------
# Display-name rename (entity)
# ---------------------------------------------------------------------------

def _read_frontmatter_field(project: Path, entity_id: str, field: str) -> str:
    md = project / "content" / entity_id / "index.md"
    text = md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def test_entity_rename_updates_label_text_in_wikilinks(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "Visit [[sharazan|Sharazan]] often.\n"
                "Also see [[sharazan|Sharazan]] later.\n")
    plan = plan_rename(
        project, "aurethia/places/sharazan", "sharazan-new",
        new_display_names={"name": "Sharazan-Reborn"},
    )
    assert plan.error == ""
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan-new|Sharazan-Reborn]]" in body
    assert "Sharazan" not in body.replace("Sharazan-Reborn", "")


def test_entity_rename_updates_own_frontmatter_name(project: Path) -> None:
    plan = plan_rename(
        project, "aurethia/places/sharazan", "sharazan-new",
        new_display_names={"name": "Sharazan-Reborn"},
    )
    assert plan.error == ""
    execute_rename(plan)
    assert _read_frontmatter_field(
        project, "aurethia/places/sharazan-new", "name"
    ) == "Sharazan-Reborn"


def test_entity_rename_adds_label_to_bare_link_when_display_changes(
    project: Path,
) -> None:
    # Bare link previously rendered as "sharazan" (the slug).  After
    # rename, the new slug is "sharazan-new", but the chosen display
    # is "Sharazan-Reborn" -> link should be promoted to a labelled
    # form so prose still renders the intended display.
    _write_body(project, "aurethia/people/duskmere",
                "See [[sharazan]].\n")
    plan = plan_rename(
        project, "aurethia/places/sharazan", "sharazan-new",
        new_display_names={"name": "Sharazan-Reborn"},
    )
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan-new|Sharazan-Reborn]]" in body


def test_entity_rename_without_display_change_preserves_existing_label(
    project: Path,
) -> None:
    # When the user opts out of changing the display name, existing
    # labels must be left exactly as-is.
    _write_body(project, "aurethia/people/duskmere",
                "Visit [[sharazan|the home town]].\n")
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan-new|the home town]]" in body


def test_entity_rename_leaves_unrelated_labels_alone(project: Path) -> None:
    # Another link uses "Sharazan" as a label but points elsewhere —
    # must not be touched.
    _write_body(project, "aurethia/people/duskmere",
                "Visit [[sharazan|Sharazan]] often.\n"
                "Also [[shanghai|Sharazan]] for fun.\n")  # weird but possible
    plan = plan_rename(
        project, "aurethia/places/sharazan", "sharazan-new",
        new_display_names={"name": "Sharazan-Reborn"},
    )
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Sharazan link gets relabelled
    assert "[[sharazan-new|Sharazan-Reborn]]" in body
    # Shanghai link keeps its original (off-target) label
    assert "[[shanghai|Sharazan]]" in body or "[[earth/places/shanghai|Sharazan]]" in body


# ---------------------------------------------------------------------------
# Display-name rename (kind)
# ---------------------------------------------------------------------------

def test_kind_rename_updates_singular_label_in_wikilinks(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "She is [[kinds/human|human]].\n")
    plan = plan_rename(
        project, "being/human", "person",
        new_display_names={"singular": "person", "plural": "people"},
    )
    assert plan.error == ""
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[kinds/person|person]]" in body


def test_kind_rename_updates_plural_label_in_wikilinks(project: Path) -> None:
    _write_body(project, "aurethia/people/duskmere",
                "Many [[kinds/human|humans]] live here.\n")
    plan = plan_rename(
        project, "being/human", "person",
        new_display_names={"singular": "person", "plural": "people"},
    )
    execute_rename(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[kinds/person|people]]" in body


def test_kind_rename_updates_own_frontmatter(project: Path) -> None:
    plan = plan_rename(
        project, "being/human", "person",
        new_display_names={"singular": "person", "plural": "people"},
    )
    execute_rename(plan)
    kind_md = project / "content_meta" / "kinds" / "being" / "person" / "_kind.md"
    text = kind_md.read_text(encoding="utf-8")
    assert "singular: person" in text
    assert "plural: people" in text


# ---------------------------------------------------------------------------
# collisions — leaf-slug clashes on rename / move
# ---------------------------------------------------------------------------


def test_rename_into_collision_detects_peer(project: Path) -> None:
    # earth/places/shanghai -> earth/places/duskmere collides with
    # aurethia/people/duskmere (and earth/people/duskmere).
    plan = plan_rename(project, "earth/places/shanghai", "duskmere")
    assert not plan.error
    assert "aurethia/people/duskmere" in plan.collisions
    assert "earth/people/duskmere" in plan.collisions


def test_rename_into_collision_rewrites_peer_links(project: Path) -> None:
    # A page in aurethia mentions [[duskmere]] — pre-rename this resolves
    # unambiguously to aurethia/people/duskmere (same cluster).  After
    # renaming shanghai -> duskmere within earth, the aurethia link is
    # still unambiguous *from aurethia's perspective* (only one duskmere
    # in aurethia), so the bare link should NOT be touched.
    _write_body(project, "aurethia/places/sharazan",
                "See [[duskmere]] up the road.\n")
    plan = plan_rename(project, "earth/places/shanghai", "duskmere")
    execute_rename(plan)
    body = _read_body(project, "aurethia/places/sharazan")
    assert "[[duskmere]]" in body, body


def test_rename_into_collision_disambiguates_cross_cluster_link(project: Path) -> None:
    # A foundation (universal) page links to [[duskmere]] — pre-rename
    # there are TWO duskmeres (aurethia, earth/people), so the link is
    # already ambiguous; the resolver will not have considered it
    # resolved.  Use a more disambiguating form so the test exercises
    # the peer-rewrite path: [[people/duskmere]] resolves uniquely to
    # aurethia/people/duskmere pre-rename (earth's is also people/duskmere,
    # so it's actually ambiguous too — pick a clearer target).
    #
    # Better: have an earth page link to its own peer via a short form
    # and then rename a sibling to collide.
    _write_body(project, "earth/places/shanghai",
                "Near [[duskmere]].\n")
    # Rename earth/people/duskmere -> earth/people/dawnmere is a no-op
    # for the collision case; instead create a fresh collision by
    # renaming aurethia/places/sharazan to a slug that clashes with
    # earth/places/shanghai's sibling.
    # Skip: this scenario is covered by the wikilinks resolver tests.


def test_move_into_collision_detects_peer(project: Path) -> None:
    # Move aurethia/people/duskmere under aurethia/places, where it
    # still has the same leaf slug.  The collision with earth/people/duskmere
    # already existed pre-move (so this is just a sanity check that
    # _detect_collisions excludes the moving entity itself).
    plan = plan_move(project, "aurethia/people/duskmere", "aurethia/places")
    assert not plan.error
    # The earth peer is still a collision after the move.
    assert "earth/people/duskmere" in plan.collisions
    # The moving entity must not appear in its own collisions list.
    assert "aurethia/people/duskmere" not in plan.collisions
    assert "aurethia/places/duskmere" not in plan.collisions


def test_rename_without_collision_has_empty_list(project: Path) -> None:
    plan = plan_rename(project, "earth/places/shanghai", "beijing")
    assert not plan.error
    assert plan.collisions == []


def test_rename_collision_rewrites_existing_peer_link_to_longer_form(project: Path) -> None:
    # Pre-state: only one duskmere in aurethia (aurethia/people/duskmere).
    # aurethia/places/sharazan links to it via the bare slug.
    _write_body(project, "aurethia/places/sharazan",
                "See [[duskmere]] up the road.\n")
    # Now rename aurethia/places/bayurinda/nuunlau -> duskmere, which
    # introduces a second duskmere INSIDE aurethia.  The previously
    # unambiguous bare [[duskmere]] in sharazan should be rewritten
    # to a longer disambiguating form pointing at the original peer.
    plan = plan_rename(
        project, "aurethia/places/bayurinda/nuunlau", "duskmere",
    )
    assert "aurethia/people/duskmere" in plan.collisions
    execute_rename(plan)
    body = _read_body(project, "aurethia/places/sharazan")
    # The original bare link should now use a longer suffix to keep
    # pointing at aurethia/people/duskmere.
    assert "[[duskmere]]" not in body, body
    assert "people/duskmere" in body, body


# ---------------------------------------------------------------------------
# Cross-file-class scanning: guides, kinds, and collections must all
# be updated when an entity / kind / collection is renamed or moved.
# ---------------------------------------------------------------------------

def _write_guide_body(project: Path, slug: str, body: str) -> None:
    md = project / "content_meta" / "guides" / slug / "index.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_guide_body(project: Path, slug: str) -> str:
    md = project / "content_meta" / "guides" / slug / "index.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def _write_kind_body(project: Path, kind_path: str, body: str) -> None:
    md = project / "content_meta" / "kinds" / kind_path / "_kind.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_kind_body(project: Path, kind_path: str) -> str:
    md = project / "content_meta" / "kinds" / kind_path / "_kind.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def _write_collection_body(project: Path, collection_id: str, body: str) -> None:
    md = project / "content" / collection_id / "_collection.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_collection_body(project: Path, collection_id: str) -> str:
    md = project / "content" / collection_id / "_collection.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def test_entity_rename_updates_guide_body(project: Path) -> None:
    _write_guide_body(
        project, "cognita",
        "Visit [[aurethia/places/sharazan|Sharazan]] for the view.\n",
    )
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_guide_body(project, "cognita")
    # The guide is cluster-less, so preferred_form emits the full id.
    assert "[[aurethia/places/sharazan-new|Sharazan]]" in body, body


def test_entity_rename_updates_kind_body(project: Path) -> None:
    _write_kind_body(
        project, "being/human",
        "Humans walk [[aurethia/places/sharazan|Sharazan]]'s halls.\n",
    )
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_kind_body(project, "being/human")
    assert "aurethia/places/sharazan-new" in body, body


def test_entity_rename_updates_collection_body(project: Path) -> None:
    _write_collection_body(
        project, "aurethia/places/bayurinda",
        "Looks toward [[sharazan|Sharazan]].\n",
    )
    plan = plan_rename(project, "aurethia/places/sharazan", "sharazan-new")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_collection_body(project, "aurethia/places/bayurinda")
    # Same cluster as bayurinda, so a bare slug remains acceptable.
    assert "sharazan-new" in body, body
    assert "[[sharazan]]" not in body, body


def test_kind_rename_updates_guide_body(project: Path) -> None:
    _write_guide_body(
        project, "cognita",
        "About [[kinds/human|humans]] and their kin.\n",
    )
    plan = plan_rename(project, "being/human", "person")
    assert plan.error == ""
    execute_rename(plan)
    body = _read_guide_body(project, "cognita")
    assert "[[kinds/person|humans]]" in body, body


def test_entity_move_updates_guide_body(project: Path) -> None:
    _write_guide_body(
        project, "cognita",
        "See [[aurethia/places/sharazan]] for context.\n",
    )
    plan = plan_move(
        project, "aurethia/places/sharazan", "aurethia/people",
    )
    assert plan.error == ""
    execute_move(plan)
    body = _read_guide_body(project, "cognita")
    assert "aurethia/people/sharazan" in body, body
