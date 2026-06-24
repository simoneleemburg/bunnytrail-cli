"""Tests for the crosslink command's preferred_form integration."""
from __future__ import annotations

from pathlib import Path

from bunnytrail_cli.helpers import (
    execute_crosslink,
    plan_crosslink,
    plan_crosslink_folder,
)


def _set_body(project: Path, entity_id: str, body: str) -> None:
    md = project / "content" / entity_id / "index.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_body(project: Path, entity_id: str) -> str:
    md = project / "content" / entity_id / "index.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def test_crosslink_same_cluster_uses_bare_slug(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "I walked through Sharazan today.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    assert plan.error == ""
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Bare slug since 'sharazan' is unique-in-cluster, and the rendered
    # leaf matches the lowercased display name, so it gets a labelled link.
    assert "[[sharazan|Sharazan]]" in body


def test_crosslink_kind_link_stays_kinds_prefix(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "She is a human.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[kinds/human|human]]" in body or "[[kinds/human]]" in body


# ---------------------------------------------------------------------------
# Folder mode
# ---------------------------------------------------------------------------

def test_crosslink_folder_returns_one_plan_per_entity(project: Path) -> None:
    # Both bodies mention 'Sharazan' — folder mode should plan both.
    _set_body(project, "aurethia/people/duskmere",
              "I walked through Sharazan today.\n")
    _set_body(project, "aurethia/people/languages/thallish",
              "First spoken in Sharazan.\n")
    plans, err = plan_crosslink_folder(
        project, "aurethia/people", "aurethia/places"
    )
    assert err == ""
    ids = {p.article_id for p in plans}
    assert "aurethia/people/duskmere" in ids
    assert "aurethia/people/languages/thallish" in ids
    actionable = [p for p in plans if p.edits]
    assert len(actionable) >= 2


def test_crosslink_folder_executes_all_actionable_plans(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "I walked through Sharazan today.\n")
    _set_body(project, "aurethia/people/languages/thallish",
              "First spoken in Sharazan.\n")
    plans, err = plan_crosslink_folder(
        project, "aurethia/people", "aurethia/places"
    )
    assert err == ""
    for p in plans:
        if p.edits and not p.error:
            execute_crosslink(p)
    assert "[[sharazan|Sharazan]]" in _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in _read_body(
        project, "aurethia/people/languages/thallish"
    )


def test_crosslink_folder_falls_through_to_single_entity(project: Path) -> None:
    # When the target IS an entity folder, folder-mode just yields one plan.
    _set_body(project, "aurethia/people/duskmere",
              "Sharazan calls.\n")
    plans, err = plan_crosslink_folder(
        project, "aurethia/people/duskmere", "aurethia/places"
    )
    assert err == ""
    assert len(plans) == 1
    assert plans[0].article_id == "aurethia/people/duskmere"


def test_crosslink_folder_missing_directory(project: Path) -> None:
    plans, err = plan_crosslink_folder(
        project, "nonexistent/path", "aurethia/places"
    )
    assert plans == []
    assert "not a directory" in err


def test_crosslink_folder_empty_directory_reports_no_entities(
    project: Path,
) -> None:
    # Create an empty folder under content/ — no descendant entities.
    empty = project / "content" / "aurethia" / "empty-shelf"
    empty.mkdir()
    plans, err = plan_crosslink_folder(
        project, "aurethia/empty-shelf", "aurethia/places"
    )
    assert plans == []
    assert "no entities, collections, or guides found" in err


def test_crosslink_folder_missing_namespace(project: Path) -> None:
    plans, err = plan_crosslink_folder(
        project, "aurethia/people", "nope/nowhere"
    )
    assert plans == []
    assert "namespace not found" in err


# ---------------------------------------------------------------------------
# Existing-link simplification
# ---------------------------------------------------------------------------

def test_crosslink_simplifies_existing_full_path_to_bare(project: Path) -> None:
    # An existing link written with the full path should be shortened
    # to the bare slug when unambiguous from this page.
    _set_body(project, "aurethia/people/duskmere",
              "We visited [[aurethia/places/sharazan|Sharazan]].\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    assert plan.error == ""
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body
    assert "aurethia/places/sharazan" not in body


def test_crosslink_simplifies_preserves_anchor(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "See [[aurethia/places/sharazan#history|its history]].\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan#history|its history]]" in body


def test_crosslink_simplifies_synthesises_label_when_leaf_changes(
    project: Path,
) -> None:
    # The old link had no label and its leaf was the displayed text.
    # After simplification the leaf is different, so we synthesise the
    # old leaf as a label so the rendered prose doesn't change.
    _set_body(project, "aurethia/people/duskmere",
              "Origin: [[aurethia/places/sharazan]].\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Old leaf was 'sharazan', new (shorter) form is 'sharazan' too —
    # so this particular case stays a bare link. Verify it didn't grow
    # a spurious label.
    assert "[[sharazan]]" in body
    assert "aurethia/places/sharazan" not in body


def test_crosslink_simplification_leaves_kinds_untouched(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "She is [[kinds/human|human]].\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[kinds/human|human]]" in body


def test_crosslink_simplification_leaves_same_page_anchors(project: Path) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "See [[#history|below]].\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[#history|below]]" in body


def test_crosslink_simplification_leaves_unresolvable_links_alone(
    project: Path,
) -> None:
    _set_body(project, "aurethia/people/duskmere",
              "Visit [[aurethia/places/atlantis|Atlantis]] someday.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Atlantis doesn't exist — leave it untouched rather than mangling it.
    assert "[[aurethia/places/atlantis|Atlantis]]" in body


# ---------------------------------------------------------------------------
# Heading skip + crosslink policy config
# ---------------------------------------------------------------------------

def _write_crosslink_yml(project: Path, crosslink_body: str) -> None:
    """Write a bt.yml with the given text nested under a ``crosslink:`` key."""
    cfg = project / "content_meta" / "bt.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Indent the caller's content by two spaces so it sits under `crosslink:`
    indented = "\n".join(
        ("  " + line) if line.strip() else line
        for line in crosslink_body.splitlines()
    )
    cfg.write_text(f"crosslink:\n{indented}\n", encoding="utf-8")


def test_crosslink_skips_headings(project: Path) -> None:
    # Even though 'human' is a registered kind, a heading containing it
    # must not be touched.
    _set_body(project, "aurethia/people/duskmere",
              "## What it is to be human\n\nShe is human.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Heading untouched
    assert "## What it is to be human" in body
    assert "[[kinds/human" not in body.split("\n", 2)[0]
    # Body line still gets the link
    assert "[[kinds/human|human]]" in body or "[[kinds/human]]" in body


def test_crosslink_skips_headings_for_existing_links_too(project: Path) -> None:
    # An existing link inside a heading must not be rewritten by the
    # simplify pass either.
    _set_body(project, "aurethia/people/duskmere",
              "## See [[aurethia/places/sharazan|Sharazan]]\n\nBody.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "## See [[aurethia/places/sharazan|Sharazan]]" in body


def test_crosslink_never_filters_kind_names(project: Path) -> None:
    _write_crosslink_yml(project, "never:\n  - human\n")
    _set_body(project, "aurethia/people/duskmere",
              "She is human.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # 'human' is in `never` → no link.
    assert "[[kinds/human" not in body


def test_crosslink_never_filters_entity_names(project: Path) -> None:
    _write_crosslink_yml(project, "never:\n  - Sharazan\n")
    _set_body(project, "aurethia/people/duskmere",
              "We visited Sharazan today.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "Sharazan" in body
    assert "[[sharazan" not in body


def test_crosslink_warn_still_links_but_tags_edit(project: Path) -> None:
    _write_crosslink_yml(project, "warn:\n  - Sharazan\n")
    _set_body(project, "aurethia/people/duskmere",
              "We visited Sharazan today.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    assert len(plan.edits) == 1
    edit = plan.edits[0]
    assert "[[sharazan|Sharazan]]" in edit.new_text
    assert "Sharazan" in edit.warn_terms


def test_crosslink_config_loader_missing_file(project: Path) -> None:
    from bunnytrail_cli.helpers import load_crosslink_config
    cfg = load_crosslink_config(project)
    assert cfg.never == set()
    assert cfg.warn == set()


def test_crosslink_config_loader_parses_lists(project: Path) -> None:
    from bunnytrail_cli.helpers import load_crosslink_config
    _write_crosslink_yml(
        project,
        "# a comment\n"
        "never:\n"
        "  - Role\n"
        "  - 'Process'\n"
        "  - \"Object\"\n"
        "\n"
        "warn:\n"
        "  - Trait\n"
        "  - Event\n",
    )
    cfg = load_crosslink_config(project)
    assert cfg.never == {"Role", "Process", "Object"}
    assert cfg.warn == {"Trait", "Event"}


# ---------------------------------------------------------------------------
# Proper Noun Phrase protection
# ---------------------------------------------------------------------------
#
# Rule: if a candidate match is a strict fragment of a larger Proper
# Noun Phrase (a run of Capitalized words, optionally joined by
# lowercase connectors like 'of'/'the'), and the WHOLE phrase isn't
# itself a linkable term, skip linking the fragment.


def test_pnp_skips_linking_prefix_of_larger_phrase(project: Path) -> None:
    # 'Sharazan' is a linkable entity (aurethia/places/sharazan).
    # 'Sharazan Tower' is not.  The phrase 'Sharazan Tower' should
    # NOT get 'Sharazan' linked — the writer means a specific compound
    # name.
    _set_body(project, "aurethia/people/duskmere",
              "He climbed the Sharazan Tower at dawn.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan" not in body, body
    assert "Sharazan Tower" in body


def test_pnp_with_connectors_skips_fragment(project: Path) -> None:
    # 'Order of the Sharazan' — same rule, with lowercase connectors
    # 'of' and 'the' inside the phrase.
    _set_body(project, "aurethia/people/duskmere",
              "The Order of the Sharazan met at dusk.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan" not in body, body


def test_pnp_does_not_block_standalone_match(project: Path) -> None:
    # A bare 'Sharazan' in normal prose IS linked — it's a 1-word PNP
    # and the candidate matches the whole phrase.
    _set_body(project, "aurethia/people/duskmere",
              "She visited Sharazan last summer.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body


def test_pnp_does_not_block_lowercase_followup(project: Path) -> None:
    # 'Sharazan tower' (lowercase 't') is NOT a PNP — the run ends at
    # 'Sharazan'.  The bare 'Sharazan' should still link.
    _set_body(project, "aurethia/people/duskmere",
              "He climbed the Sharazan tower at dawn.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body


def test_pnp_sentence_initial_the_does_not_block_entity(project: Path) -> None:
    # 'The Sharazan' — sentence-initial 'The' is a connector word and must
    # not anchor a PNP.  The PNP is 'Sharazan' alone, so the entity IS linked.
    _set_body(project, "aurethia/people/duskmere",
              "The Sharazan lay beyond the mountains.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body


def test_pnp_and_connector_does_not_merge_separate_entities(project: Path) -> None:
    # 'Sharazan and Maerath' — 'and' is a coordinating conjunction, not a
    # PNP connector.  Both entities must be linked independently.
    maerath = project / "content" / "aurethia" / "places" / "maerath"
    maerath.mkdir(parents=True)
    (maerath / "index.md").write_text(
        "---\nname: Maerath\nkind: place\n---\nA place.\n",
        encoding="utf-8",
    )
    _set_body(project, "aurethia/people/duskmere",
              "Travellers cross Sharazan and Maerath in summer.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body
    assert "[[maerath|Maerath]]" in body


def test_pnp_punctuation_breaks_the_phrase(project: Path) -> None:
    # 'Sharazan, Tower of Light' — the comma after Sharazan ends the
    # PNP run, so 'Sharazan' is its own 1-word PNP and gets linked.
    _set_body(project, "aurethia/people/duskmere",
              "Sharazan, Tower of Light, gleamed.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert "[[sharazan|Sharazan]]" in body


def test_pnp_links_whole_phrase_when_it_is_a_candidate(project: Path) -> None:
    # Build a second entity whose display name is the whole phrase
    # 'Sharazan Tower'.  The matcher should link the WHOLE phrase
    # (long-first sort), and skip the bare 'Sharazan' inside it.
    tower = project / "content" / "aurethia" / "places" / "sharazan-tower"
    tower.mkdir(parents=True)
    (tower / "index.md").write_text(
        "---\nname: Sharazan Tower\nkind: place\n---\nA tower.\n",
        encoding="utf-8",
    )
    _set_body(project, "aurethia/people/duskmere",
              "He climbed the Sharazan Tower at dawn.\n")
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # The whole phrase is linked; the bare 'Sharazan' was never a
    # separate match (its position is inside the new wikilink span).
    assert "[[sharazan-tower|Sharazan Tower]]" in body
    # Make sure we did NOT also wrap 'Sharazan' as a nested/adjacent
    # link.
    assert body.count("[[sharazan") == 1, body


# ---------------------------------------------------------------------------
# First-occurrence rule honours existing wikilinks
# ---------------------------------------------------------------------------
#
# Authors sometimes write the wikilink themselves on the first mention
# of a term and expect the auto-linker to leave subsequent mentions
# alone.  Without seeding `linked_texts` from the existing body, the
# matcher would still hunt for the next plain occurrence of the
# display name and link it — producing two links to the same target.


def test_existing_wikilink_suppresses_later_auto_link_same_line(project: Path) -> None:
    # 'Sharazan' is linkable.  An author has already written
    # [[sharazan|Sharazan]] earlier on the line; the bare 'Sharazan'
    # later on the same line must NOT be auto-linked.
    _set_body(
        project, "aurethia/people/duskmere",
        "We met at [[sharazan|Sharazan]]; later, Sharazan glowed.\n",
    )
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    if plan.edits:
        execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Exactly one wikilink to sharazan, the original one.
    assert body.count("[[sharazan") == 1, body


def test_existing_wikilink_suppresses_later_auto_link_different_lines(project: Path) -> None:
    # Same rule across line boundaries.
    _set_body(
        project, "aurethia/people/duskmere",
        "First, we found [[sharazan|Sharazan]].\n"
        "\n"
        "Later that year, Sharazan was abandoned.\n",
    )
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    if plan.edits:
        execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert body.count("[[sharazan") == 1, body


def test_existing_bare_wikilink_also_suppresses(project: Path) -> None:
    # A bare [[sharazan]] (no label) renders as 'sharazan' but still
    # points at the same target — it must suppress a later 'Sharazan'.
    _set_body(
        project, "aurethia/people/duskmere",
        "I went to [[sharazan]] yesterday.\n"
        "Sharazan was quiet at dawn.\n",
    )
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    if plan.edits:
        execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    assert body.count("[[sharazan") == 1, body


def test_existing_kind_link_suppresses_singular_and_plural(project: Path) -> None:
    # The 'human' kind has both 'human' and 'humans' as candidates.
    # An existing [[kinds/human|humans]] should suppress both.
    _set_body(
        project, "aurethia/people/duskmere",
        "Many [[kinds/human|humans]] live here.\n"
        "Each human has a story; humans gather at dusk.\n",
    )
    plan = plan_crosslink(project, "aurethia/people/duskmere", "aurethia/places")
    if plan.edits:
        execute_crosslink(plan)
    body = _read_body(project, "aurethia/people/duskmere")
    # Exactly one kind link to human; neither 'human' nor 'humans' got
    # a second wrap.
    assert body.count("[[kinds/human") == 1, body


# ---------------------------------------------------------------------------
# _collection.md support
# ---------------------------------------------------------------------------
#
# Crosslink treats collection pages as articles too — body prose on a
# _collection.md is auto-linked using the same rules as an entity's
# index.md.  Folder mode picks them up alongside index.md files.


def _set_collection_body(project: Path, collection_id: str, body: str) -> None:
    """Replace the body of a _collection.md (keeping its frontmatter)."""
    md = project / "content" / collection_id / "_collection.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_collection_body(project: Path, collection_id: str) -> str:
    md = project / "content" / collection_id / "_collection.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def test_crosslink_collection_body_gets_linked(project: Path) -> None:
    # The aurethia/places/bayurinda collection has a _collection.md.
    # Put some body prose on it mentioning Sharazan and Nuunlau (both
    # entities under aurethia/places).
    _set_collection_body(
        project, "aurethia/places/bayurinda",
        "The archipelago contains Nuunlau and looks toward Sharazan.\n",
    )
    plan = plan_crosslink(project, "aurethia/places/bayurinda", "aurethia/places")
    assert plan.error == "", plan.error
    execute_crosslink(plan)
    body = _read_collection_body(project, "aurethia/places/bayurinda")
    # bayurinda is in cluster aurethia, so bare slugs work.  Nuunlau
    # is nested under bayurinda but suffix-matching makes the bare
    # form unambiguous from this page.
    assert "[[nuunlau|Nuunlau]]" in body, body
    assert "[[sharazan|Sharazan]]" in body, body


def test_crosslink_collection_frontmatter_is_not_touched(project: Path) -> None:
    # Mention 'Bayurinda' inside the description (frontmatter).  Body
    # is empty.  Crosslink must NOT rewrite frontmatter even when the
    # text matches a candidate.
    md = project / "content" / "aurethia" / "places" / "bayurinda" / "_collection.md"
    md.write_text(
        "---\n"
        "title: Bayurinda\n"
        "description: >-\n"
        "  Sharazan, Nuunlau, and the islands between.\n"
        "---\n",
        encoding="utf-8",
    )
    plan = plan_crosslink(project, "aurethia/places/bayurinda", "aurethia/places")
    assert plan.error == "", plan.error
    # No body prose to link — frontmatter mentions of Sharazan/Nuunlau
    # must be ignored.
    assert plan.edits == [], [(e.line_no, e.new_text) for e in plan.edits]


def test_crosslink_folder_walks_collection_files(project: Path) -> None:
    # Folder mode should produce plans for BOTH index.md and
    # _collection.md descendants.  Put body prose on a collection so
    # there's something to link.
    _set_collection_body(
        project, "aurethia/places/bayurinda",
        "Looks toward Sharazan.\n",
    )
    # Also put prose on an entity so we exercise both paths.
    _set_body(
        project, "aurethia/places/sharazan",
        "Once visited by Nuunlau.\n",
    )
    plans, err = plan_crosslink_folder(
        project, "aurethia/places", "aurethia/places",
    )
    assert err == "", err
    article_ids = {p.article_id for p in plans if p.edits}
    assert "aurethia/places/bayurinda" in article_ids
    assert "aurethia/places/sharazan" in article_ids


def test_crosslink_collection_self_exclusion(project: Path) -> None:
    # A collection should not generate a wikilink that points at
    # itself.  Even though collections aren't auto-link CANDIDATES
    # today, this guards the article_id_norm self-exclusion path in
    # case that changes.  Use a candidate entity that DOES exist
    # ('Sharazan') alongside the collection's own title to confirm
    # the matcher runs but doesn't trip on the article id.
    _set_collection_body(
        project, "aurethia/places/bayurinda",
        "Bayurinda contains Sharazan.\n",
    )
    plan = plan_crosslink(project, "aurethia/places/bayurinda", "aurethia/places")
    execute_crosslink(plan)
    body = _read_collection_body(project, "aurethia/places/bayurinda")
    assert "[[sharazan|Sharazan]]" in body
    # 'Bayurinda' is not a candidate (collections aren't in the pool),
    # so it stays plain text.
    assert "[[bayurinda" not in body, body


# ---------------------------------------------------------------------------
# Guides
# ---------------------------------------------------------------------------

def _set_guide_body(project: Path, slug: str, body: str) -> None:
    md = project / "content_meta" / "guides" / slug / "index.md"
    text = md.read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    md.write_text(head + "\n---\n" + body, encoding="utf-8")


def _read_guide_body(project: Path, slug: str) -> str:
    md = project / "content_meta" / "guides" / slug / "index.md"
    text = md.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n")
    return body


def test_crosslink_guide_body_gets_linked(project: Path) -> None:
    # The `cognita` guide mentions Sharazan and Nuunlau by display
    # name in its body.  Crosslink, scoped to aurethia/places, should
    # auto-link both — and because a guide lives outside any cluster,
    # the wikilink target is the full id (preferred_form rule for
    # rendering pages with cluster=None).
    _set_guide_body(
        project, "cognita",
        "An overview that visits Sharazan. Also touches Nuunlau.\n",
    )
    plan = plan_crosslink(project, "guides/cognita", "aurethia/places")
    assert plan.error == "", plan.error
    execute_crosslink(plan)
    body = _read_guide_body(project, "cognita")
    assert "[[aurethia/places/sharazan|Sharazan]]" in body, body
    assert "[[aurethia/places/bayurinda/nuunlau|Nuunlau]]" in body, body


def test_crosslink_guide_frontmatter_is_not_touched(project: Path) -> None:
    # Mention 'Sharazan' inside the guide's frontmatter (in the
    # summary).  Crosslink must not rewrite frontmatter.
    md = project / "content_meta" / "guides" / "cognita" / "index.md"
    md.write_text(
        "---\n"
        "title: Alteria Cognita\n"
        "summary: A tour through Sharazan and the rest.\n"
        "---\n"
        "Body has no candidate names.\n",
        encoding="utf-8",
    )
    plan = plan_crosslink(project, "guides/cognita", "aurethia/places")
    assert plan.error == "", plan.error
    assert plan.edits == []


def test_crosslink_folder_walks_guides(project: Path) -> None:
    _set_guide_body(
        project, "cognita",
        "Once visited Sharazan in passing.\n",
    )
    plans, err = plan_crosslink_folder(
        project, "guides", "aurethia/places",
    )
    assert err == "", err
    article_ids = {p.article_id for p in plans if p.edits}
    assert "guides/cognita" in article_ids


def test_crosslink_single_guide_folder(project: Path) -> None:
    # Single-target form: passing guides/<slug> directly to the
    # folder planner should route to plan_crosslink for that one
    # guide.
    _set_guide_body(
        project, "cognita",
        "Once visited Sharazan in passing.\n",
    )
    plans, err = plan_crosslink_folder(
        project, "guides/cognita", "aurethia/places",
    )
    assert err == "", err
    assert len(plans) == 1
    assert plans[0].article_id == "guides/cognita"
    assert plans[0].edits
