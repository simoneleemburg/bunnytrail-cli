"""Tests for timeline helper functions (is_timeline_folder, is_timeline_dot_folder,
write_timeline_line, write_timeline_dot)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bunnytrail_cli.helpers import (
    is_timeline_dot_folder,
    is_timeline_folder,
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
