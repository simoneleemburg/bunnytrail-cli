"""Tests for timeline helper functions (is_timeline_folder, is_timeline_dot_folder,
write_timeline_line, write_timeline_dot), move/rename reference rewriting,
and crosslink support for _time.md dot prose."""
from __future__ import annotations

from pathlib import Path

import pytest

from bunnytrail_cli.helpers import (
    execute_crosslink,
    execute_move,
    execute_rename,
    is_timeline_dot_folder,
    is_timeline_folder,
    plan_crosslink,
    plan_crosslink_folder,
    plan_move,
    plan_rename,
    write_timeline_dot,
    write_timeline_line,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# is_timeline_folder
# ---------------------------------------------------------------------------

def test_is_timeline_folder_line(tmp_path: Path) -> None:
    d = tmp_path / "verhalen" / "jan"
    _write(d / "_time.md", "---\nname: Jan\ntype: period\ntarget: foo/bar\n---\n")
    assert is_timeline_folder(d) is True


def test_is_timeline_folder_no_marker(tmp_path: Path) -> None:
    d = tmp_path / "nope"
    d.mkdir(parents=True)
    assert is_timeline_folder(d) is False


def test_is_timeline_folder_with_year_is_false(tmp_path: Path) -> None:
    """A folder whose _time.md has year: should NOT be a line timeline."""
    d = tmp_path / "1954"
    _write(d / "_time.md", "---\nyear: 1954\nsummary: kleuterschool\n---\n")
    assert is_timeline_folder(d) is False


# ---------------------------------------------------------------------------
# is_timeline_dot_folder
# ---------------------------------------------------------------------------

def test_is_timeline_dot_folder_with_year(tmp_path: Path) -> None:
    d = tmp_path / "1954"
    _write(d / "_time.md", "---\nyear: 1954\nsummary: kleuterschool\n---\n")
    assert is_timeline_dot_folder(d) is True


def test_is_timeline_dot_folder_no_year(tmp_path: Path) -> None:
    d = tmp_path / "verhalen" / "jan"
    _write(d / "_time.md", "---\nname: Jan\ntype: period\ntarget: foo/bar\n---\n")
    assert is_timeline_dot_folder(d) is False


def test_is_timeline_dot_folder_no_marker(tmp_path: Path) -> None:
    d = tmp_path / "nope"
    d.mkdir(parents=True)
    assert is_timeline_dot_folder(d) is False


# ---------------------------------------------------------------------------
# write_timeline_line
# ---------------------------------------------------------------------------

def test_write_timeline_line_single_target(tmp_path: Path) -> None:
    d = tmp_path / "jan"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_line(md, "Jan Leemburg", "Uit de jaren", ["leemburg/personen/jan"])
    text = md.read_text(encoding="utf-8")
    assert "name: Jan Leemburg" in text
    assert "summary: Uit de jaren" in text
    assert "type: period" in text
    assert "target: leemburg/personen/jan" in text
    assert text.startswith("---\n")
    assert "\n---\n" in text


def test_write_timeline_line_multi_target(tmp_path: Path) -> None:
    d = tmp_path / "jan-en-corrie"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_line(md, "Jan & Corrie", "", ["leemburg/personen/jan", "graaf/personen/corrie"])
    text = md.read_text(encoding="utf-8")
    assert "target:" in text
    assert " - leemburg/personen/jan" in text
    assert " - graaf/personen/corrie" in text


def test_write_timeline_line_no_targets(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_line(md, "Untitled", "", [])
    text = md.read_text(encoding="utf-8")
    assert "target" not in text


def test_write_timeline_line_roundtrip_is_timeline_folder(tmp_path: Path) -> None:
    d = tmp_path / "jan"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_line(md, "Jan", "summary", ["leemburg/personen/jan"])
    assert is_timeline_folder(d) is True
    assert is_timeline_dot_folder(d) is False


# ---------------------------------------------------------------------------
# write_timeline_dot
# ---------------------------------------------------------------------------

def test_write_timeline_dot_year_only(tmp_path: Path) -> None:
    d = tmp_path / "1954"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_dot(md, 1954)
    text = md.read_text(encoding="utf-8")
    assert "year: 1954" in text
    assert text.startswith("---\n")


def test_write_timeline_dot_with_summary_and_body(tmp_path: Path) -> None:
    d = tmp_path / "1956"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_dot(md, 1956, summary="Thorbeckeschool", body="Hij ging vroeg naar school.\n")
    text = md.read_text(encoding="utf-8")
    assert "year: 1956" in text
    assert "summary: Thorbeckeschool" in text
    assert "Hij ging vroeg naar school." in text


def test_write_timeline_dot_roundtrip_is_dot_folder(tmp_path: Path) -> None:
    d = tmp_path / "1955"
    d.mkdir()
    md = d / "_time.md"
    write_timeline_dot(md, 1955, summary="autoped")
    assert is_timeline_dot_folder(d) is True
    assert is_timeline_folder(d) is False


# ---------------------------------------------------------------------------
# Helper to build a minimal project for move/rename tests
# ---------------------------------------------------------------------------

def _mk_project(root: Path) -> tuple[Path, Path]:
    """Return (project_root, content_root). Builds a minimal synthetic project."""
    content = root / "content"
    (content / "leemburg" / "personen" / "jan").mkdir(parents=True)
    (content / "leemburg" / "personen" / "jan" / "index.md").write_text(
        "---\nname: Jan\nkind: persoon\n---\n", encoding="utf-8"
    )
    (content / "graaf" / "personen" / "corrie").mkdir(parents=True)
    (content / "graaf" / "personen" / "corrie" / "index.md").write_text(
        "---\nname: Corrie\nkind: persoon\n---\n", encoding="utf-8"
    )
    # A collection marker so move/rename can walk the tree
    (content / "leemburg" / "_collection.md").write_text("---\ntitle: Leemburg\n---\n", encoding="utf-8")
    (content / "graaf" / "_collection.md").write_text("---\ntitle: Graaf\n---\n", encoding="utf-8")
    # Minimal kinds tree
    (root / "content_meta" / "kinds" / "persoon").mkdir(parents=True)
    (root / "content_meta" / "kinds" / "persoon" / "_kind.yaml").write_text(
        "singular: Persoon\nplural: Personen\n", encoding="utf-8"
    )
    return root, content


# ---------------------------------------------------------------------------
# Move: wikilinks in _time.md dot prose get rewritten
# ---------------------------------------------------------------------------

def test_move_updates_wikilink_in_time_dot_prose(tmp_path: Path) -> None:
    project, content = _mk_project(tmp_path)

    # A dot entry whose body prose contains a wikilink to the entity being moved
    dot_dir = content / "leemburg" / "verhalen" / "jan-jr" / "1956"
    dot_dir.mkdir(parents=True)
    dot_md = dot_dir / "_time.md"
    write_timeline_dot(dot_md, 1956, summary="school",
                       body="Hij ging naar de [[jan|Jan]].\n")

    plan = plan_move(project, "leemburg/personen/jan", "graaf/personen")
    assert plan.error == "", plan.error
    execute_move(plan)

    result = dot_md.read_text(encoding="utf-8")
    # After move the entity is at graaf/personen/jan — wikilink should update
    assert "[[graaf/personen/jan|Jan]]" in result or "[[jan|Jan]]" in result
    # The dot file should still be valid
    assert "1956" in result


# ---------------------------------------------------------------------------
# Move: single-target target: in timeline line _time.md gets rewritten
# ---------------------------------------------------------------------------

def test_move_updates_scalar_target_in_timeline_line(tmp_path: Path) -> None:
    project, content = _mk_project(tmp_path)

    line_dir = content / "leemburg" / "verhalen" / "jan-jr"
    line_dir.mkdir(parents=True)
    line_md = line_dir / "_time.md"
    write_timeline_line(line_md, "Jan Jr", "summary", ["leemburg/personen/jan"])

    plan = plan_move(project, "leemburg/personen/jan", "graaf/personen")
    assert plan.error == "", plan.error
    execute_move(plan)

    result = line_md.read_text(encoding="utf-8")
    assert "target: graaf/personen/jan" in result


# ---------------------------------------------------------------------------
# Move: multi-target list in timeline line _time.md gets rewritten
# ---------------------------------------------------------------------------

def test_move_updates_list_target_in_timeline_line(tmp_path: Path) -> None:
    project, content = _mk_project(tmp_path)

    line_dir = content / "leemburg" / "verhalen" / "jan-en-corrie"
    line_dir.mkdir(parents=True)
    line_md = line_dir / "_time.md"
    write_timeline_line(line_md, "Jan & Corrie", "",
                        ["leemburg/personen/jan", "graaf/personen/corrie"])

    plan = plan_move(project, "leemburg/personen/jan", "graaf/personen")
    assert plan.error == "", plan.error
    execute_move(plan)

    result = line_md.read_text(encoding="utf-8")
    assert " - graaf/personen/jan" in result
    assert " - graaf/personen/corrie" in result  # unchanged
    assert "leemburg/personen/jan" not in result


# ---------------------------------------------------------------------------
# Rename: scalar target: in timeline line _time.md gets rewritten
# ---------------------------------------------------------------------------

def test_rename_updates_scalar_target_in_timeline_line(tmp_path: Path) -> None:
    project, content = _mk_project(tmp_path)

    line_dir = content / "leemburg" / "verhalen" / "jan-jr"
    line_dir.mkdir(parents=True)
    line_md = line_dir / "_time.md"
    write_timeline_line(line_md, "Jan Jr", "summary", ["leemburg/personen/jan"])

    plan = plan_rename(project, "leemburg/personen/jan", "jan-arend-jr")
    assert plan.error == "", plan.error
    execute_rename(plan)

    result = line_md.read_text(encoding="utf-8")
    assert "target: leemburg/personen/jan-arend-jr" in result


# ---------------------------------------------------------------------------
# Rename: multi-target list in timeline line _time.md gets rewritten
# ---------------------------------------------------------------------------

def test_rename_updates_list_target_in_timeline_line(tmp_path: Path) -> None:
    project, content = _mk_project(tmp_path)

    line_dir = content / "leemburg" / "verhalen" / "jan-en-corrie"
    line_dir.mkdir(parents=True)
    line_md = line_dir / "_time.md"
    write_timeline_line(line_md, "Jan & Corrie", "",
                        ["leemburg/personen/jan", "graaf/personen/corrie"])

    plan = plan_rename(project, "leemburg/personen/jan", "jan-arend-jr")
    assert plan.error == "", plan.error
    execute_rename(plan)

    result = line_md.read_text(encoding="utf-8")
    assert " - leemburg/personen/jan-arend-jr" in result
    assert " - graaf/personen/corrie" in result
    assert " - leemburg/personen/jan\n" not in result


# ---------------------------------------------------------------------------
# Crosslink: _time.md dot prose gets wikilinks inserted
# ---------------------------------------------------------------------------

def _mk_crosslink_project(root: Path) -> Path:
    """Minimal project with an entity, a kind, and a dot timeline entry."""
    content = root / "content"
    # Entity: Thorbeckeschool
    school = content / "wereld" / "plaatsen" / "thorbeckeschool"
    school.mkdir(parents=True)
    (school / "index.md").write_text(
        "---\nname: Thorbeckeschool\nkind: school\n---\n", encoding="utf-8"
    )
    # Collections
    (content / "wereld" / "_collection.md").write_text(
        "---\ntitle: Wereld\n---\n", encoding="utf-8"
    )
    (content / "leemburg").mkdir(parents=True, exist_ok=True)
    (content / "leemburg" / "_collection.md").write_text(
        "---\ntitle: Leemburg\n---\n", encoding="utf-8"
    )
    # Kind
    kinds = root / "content_meta" / "kinds" / "school"
    kinds.mkdir(parents=True)
    (kinds / "_kind.yaml").write_text(
        "singular: School\nplural: Schools\n", encoding="utf-8"
    )
    # Dot timeline entry with a prose mention of Thorbeckeschool
    dot_dir = content / "leemburg" / "verhalen" / "jan" / "1956"
    dot_dir.mkdir(parents=True)
    (dot_dir / "_time.md").write_text(
        "---\nyear: 1956\nsummary: school\n---\n"
        "Hij ging naar de Thorbeckeschool.\n",
        encoding="utf-8",
    )
    return root


def test_crosslink_single_dot_inserts_wikilink(tmp_path: Path) -> None:
    project = _mk_crosslink_project(tmp_path)
    plan = plan_crosslink(
        project,
        "leemburg/verhalen/jan/1956",
        "",   # whole content tree as namespace
    )
    assert plan.error == "", plan.error
    assert any("Thorbeckeschool" in e.new_text for e in plan.edits), \
        f"expected a Thorbeckeschool wikilink, got edits: {plan.edits}"
    execute_crosslink(plan)
    result = (tmp_path / "content" / "leemburg" / "verhalen" / "jan" / "1956" / "_time.md").read_text()
    assert "[[" in result
    assert "Thorbeckeschool" in result


def test_crosslink_folder_includes_dot_entries(tmp_path: Path) -> None:
    project = _mk_crosslink_project(tmp_path)
    plans, error = plan_crosslink_folder(project, "leemburg/verhalen/jan", "")
    assert error == "", error
    dot_ids = [p.article_id for p in plans]
    assert any("1956" in aid for aid in dot_ids), \
        f"1956 dot entry not included in plans: {dot_ids}"


def test_crosslink_folder_dot_only_path(tmp_path: Path) -> None:
    """Pointing crosslink directly at a dot folder should work."""
    project = _mk_crosslink_project(tmp_path)
    plans, error = plan_crosslink_folder(
        project, "leemburg/verhalen/jan/1956", ""
    )
    assert error == "", error
    assert len(plans) == 1
    assert plans[0].error == ""
