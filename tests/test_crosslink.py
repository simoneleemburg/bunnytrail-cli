"""Tests for the crosslink command's preferred_form integration."""
from __future__ import annotations

from pathlib import Path

from alteria_cli.helpers import execute_crosslink, plan_crosslink


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
