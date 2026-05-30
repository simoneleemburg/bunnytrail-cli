"""Tests for the crosslink command's preferred_form integration."""
from __future__ import annotations

from pathlib import Path

from alteria_cli.helpers import (
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
    assert "no entities found" in err


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
