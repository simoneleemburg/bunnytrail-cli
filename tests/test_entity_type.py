"""
Tests for the ``type:`` and ``plural:`` entity fields.

Covers:
- collect_namespace_entities excludes type:class entities from crosslink pool
- check_entity_type_violations reports class+class: and instance+plural: misuse
- entity_display_title formats names correctly
- _write_entity_file round-trips type: and plural: (via the helpers directly)
- bt add entity --type/--plural CLI flags
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers shared with conftest
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entity(dir_: Path, name: str, kind: str = "place", body: str = "",
            entity_type: str = "", plural: str = "") -> None:
    lines = [f"name: {name}", f"kind: {kind}"]
    if entity_type and entity_type != "instance":
        lines.append(f"type: {entity_type}")
    if plural:
        lines.append(f"plural: {plural}")
    fm = "---\n" + "\n".join(lines) + "\n---\n"
    _write(dir_ / "index.md", fm + body)


def _collection(dir_: Path, title: str) -> None:
    _write(dir_ / "_collection.md", f"---\ntitle: {title}\n---\n")


# ---------------------------------------------------------------------------
# entity_display_title
# ---------------------------------------------------------------------------

def test_display_title_instance():
    from bunnytrail_cli.helpers import entity_display_title
    assert entity_display_title("Montari", "instance", "") == "Montari"


def test_display_title_class_no_plural():
    from bunnytrail_cli.helpers import entity_display_title
    assert entity_display_title("Ashclaw", "class", "") == "Ashclaw (class)"


def test_display_title_class_with_plural():
    from bunnytrail_cli.helpers import entity_display_title
    assert entity_display_title("Ashclaw", "class", "Ashclaws") == "Ashclaw / Ashclaws"


def test_display_title_default_is_instance():
    from bunnytrail_cli.helpers import entity_display_title
    # omitted type → same as instance
    assert entity_display_title("Sharazan", "", "") == "Sharazan"


# ---------------------------------------------------------------------------
# read_entity_type
# ---------------------------------------------------------------------------

def test_read_entity_type_default(tmp_path: Path):
    from bunnytrail_cli.helpers import read_entity_type
    d = tmp_path / "thing"
    _entity(d, "Thing")
    assert read_entity_type(d) == "instance"


def test_read_entity_type_class(tmp_path: Path):
    from bunnytrail_cli.helpers import read_entity_type
    d = tmp_path / "thing"
    _entity(d, "Thing", entity_type="class")
    assert read_entity_type(d) == "class"


def test_read_entity_type_missing_dir(tmp_path: Path):
    from bunnytrail_cli.helpers import read_entity_type
    assert read_entity_type(tmp_path / "nonexistent") == "instance"


# ---------------------------------------------------------------------------
# collect_namespace_entities — type:class exclusion
# ---------------------------------------------------------------------------

@pytest.fixture
def typed_project(tmp_path: Path) -> Path:
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    content = root / "content"
    _collection(content / "aurethia", "Aurethia")
    # instance entity — should appear in crosslink pool
    _entity(content / "aurethia" / "places" / "sharazan", "Sharazan")
    # class entity — should be excluded from crosslink pool
    _entity(content / "aurethia" / "nature" / "ashclaw", "Ashclaw",
            entity_type="class", plural="Ashclaws")
    # another instance — should appear
    _entity(content / "aurethia" / "people" / "montari", "Montari")
    (root / "content_meta" / "kinds" / "place").mkdir(parents=True)
    return root


def test_collect_namespace_entities_excludes_class(typed_project: Path):
    from bunnytrail_cli.helpers import collect_namespace_entities
    results = collect_namespace_entities(typed_project, "aurethia")
    names = [name for name, _ in results]
    assert "Sharazan" in names
    assert "Montari" in names
    assert "Ashclaw" not in names, "type:class entity must not appear in crosslink pool"


def test_collect_namespace_entities_includes_all_when_no_class(typed_project: Path):
    from bunnytrail_cli.helpers import collect_namespace_entities
    # Confirm the instance entities are actually present
    results = collect_namespace_entities(typed_project, "aurethia")
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Crosslink — type:class entity is not auto-linked
# ---------------------------------------------------------------------------

def test_crosslink_skips_class_entity(typed_project: Path):
    """plan_crosslink must not insert a link for a type:class entity name."""
    from bunnytrail_cli.helpers import plan_crosslink
    # Article that mentions "Ashclaw" (a class entity) and "Montari" (instance)
    article_dir = typed_project / "content" / "aurethia" / "reports" / "recon"
    _entity(article_dir, "Recon", body="Ashclaw scavengers and Montari traders.\n")
    plan = plan_crosslink(typed_project, "aurethia/reports/recon", "aurethia")
    assert not plan.error
    all_new_text = " ".join(e.new_text for e in plan.edits)
    assert "[[ashclaw" not in all_new_text.lower(), \
        "Ashclaw is type:class — crosslink must not auto-link it"
    assert "[[montari" in all_new_text.lower(), \
        "Montari is type:instance — crosslink should link it"


# ---------------------------------------------------------------------------
# check_entity_type_violations
# ---------------------------------------------------------------------------

@pytest.fixture
def violations_project(tmp_path: Path) -> Path:
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    content = root / "content"
    _collection(content / "aurethia", "Aurethia")
    (root / "content_meta" / "kinds" / "place").mkdir(parents=True)
    return root


def _entity_raw(dir_: Path, fm_body: str, body: str = "") -> None:
    """Write an entity with literal frontmatter content (for violation tests)."""
    _write(dir_ / "index.md", f"---\n{fm_body}\n---\n{body}")


def test_no_violations_clean_project(violations_project: Path):
    from bunnytrail_cli.helpers import check_entity_type_violations
    _entity(violations_project / "content" / "aurethia" / "places" / "sharazan", "Sharazan")
    _entity(violations_project / "content" / "aurethia" / "nature" / "ashclaw", "Ashclaw",
            entity_type="class", plural="Ashclaws")
    violations = check_entity_type_violations(violations_project)
    assert violations == []


def test_violation_class_entity_has_class_field(violations_project: Path):
    from bunnytrail_cli.helpers import check_entity_type_violations
    # A type:class entity that also has a class: field — invalid
    _entity_raw(
        violations_project / "content" / "aurethia" / "nature" / "ashclaw",
        "name: Ashclaw\nkind: animal\ntype: class\nclass: aurethia/nature/crustacean",
    )
    violations = check_entity_type_violations(violations_project)
    assert len(violations) == 1
    assert violations[0].name == "Ashclaw"
    assert "class:" in violations[0].violation


def test_violation_instance_has_plural_field(violations_project: Path):
    from bunnytrail_cli.helpers import check_entity_type_violations
    # A type:instance entity that has a plural: field — invalid
    _entity_raw(
        violations_project / "content" / "aurethia" / "people" / "montari",
        "name: Montari\nkind: being\nplural: Montaris",
    )
    violations = check_entity_type_violations(violations_project)
    assert len(violations) == 1
    assert violations[0].name == "Montari"
    assert "plural:" in violations[0].violation


def test_violation_multiple(violations_project: Path):
    from bunnytrail_cli.helpers import check_entity_type_violations
    _entity_raw(
        violations_project / "content" / "aurethia" / "nature" / "ashclaw",
        "name: Ashclaw\nkind: animal\ntype: class\nclass: aurethia/nature/crustacean",
    )
    _entity_raw(
        violations_project / "content" / "aurethia" / "people" / "montari",
        "name: Montari\nkind: being\nplural: Montaris",
    )
    violations = check_entity_type_violations(violations_project)
    assert len(violations) == 2
    names = {v.name for v in violations}
    assert names == {"Ashclaw", "Montari"}


def test_class_entity_no_plural_is_clean(violations_project: Path):
    from bunnytrail_cli.helpers import check_entity_type_violations
    # type:class without plural is fine
    _entity(violations_project / "content" / "aurethia" / "nature" / "ashclaw",
            "Ashclaw", entity_type="class")
    violations = check_entity_type_violations(violations_project)
    assert violations == []


# ---------------------------------------------------------------------------
# bt add entity CLI — --type and --plural flags
# ---------------------------------------------------------------------------

def _invoke_add_entity(root: Path, args: list) -> "any":
    """Invoke 'bt add entity' with project root pre-injected."""
    from unittest.mock import patch
    from click.testing import CliRunner
    from bunnytrail_cli.main import cli

    runner = CliRunner(mix_stderr=False)
    with patch("bunnytrail_cli.main.find_project_root", return_value=root):
        return runner.invoke(cli, ["add", "entity"] + args, catch_exceptions=False)


def test_add_entity_with_type_class(tmp_path: Path):
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    (root / "content_meta" / "kinds" / "animal").mkdir(parents=True)

    result = _invoke_add_entity(root, [
        "aurethia/nature/ashclaw", "Ashclaw", "animal",
        "--type", "class",
        "--plural", "Ashclaws",
    ])
    md = root / "content" / "aurethia" / "nature" / "ashclaw" / "index.md"
    assert md.is_file(), f"file not created; output: {result.output}"
    text = md.read_text()
    assert "type: class" in text
    assert "plural: Ashclaws" in text


def test_add_entity_default_type_omits_field(tmp_path: Path):
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    (root / "content_meta" / "kinds" / "place").mkdir(parents=True)

    result = _invoke_add_entity(root, [
        "aurethia/places/sharazan", "Sharazan", "place",
    ])
    md = root / "content" / "aurethia" / "places" / "sharazan" / "index.md"
    assert md.is_file(), result.output
    text = md.read_text()
    assert "type:" not in text  # instance is default, not written


def test_add_entity_plural_without_class_warns(tmp_path: Path):
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    (root / "content_meta" / "kinds" / "place").mkdir(parents=True)

    result = _invoke_add_entity(root, [
        "aurethia/places/sharazan", "Sharazan", "place",
        "--plural", "Sharazans",
    ])
    # Should warn but still create the entity
    md = root / "content" / "aurethia" / "places" / "sharazan" / "index.md"
    assert md.is_file(), result.output
    combined = (result.output or "") + (result.stderr or "")
    assert "Warning" in combined or "warning" in combined.lower()
