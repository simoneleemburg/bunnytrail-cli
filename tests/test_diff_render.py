"""
Tests for the shared diff renderer used by `rename`, `move` and
`crosslink` previews in both the CLI and the REPL.

The renderer is small but has two non-obvious contracts:

  * NO_COLOR (or a non-TTY) must produce byte-identical output to the
    previous unformatted f-string version, so test fixtures and
    piped-to-file workflows keep working unchanged.
  * When colour is on, *only the wikilinks whose i-th occurrence
    differs between the old and new lines* get the bold/bright
    highlight.  Unchanged wikilinks stay in the line's base colour.
"""
from __future__ import annotations

import os

import pytest

from alteria_cli.helpers import format_diff_pair, use_color


def test_plain_output_matches_legacy_format() -> None:
    old, new = format_diff_pair(
        "  of [[mundus|Mundus]]  ", "of [[primitives/mundus|Mundus]]",
        indent="    ", color=False,
    )
    assert old == "    - of [[mundus|Mundus]]"
    assert new == "    + of [[primitives/mundus|Mundus]]"


def test_colored_only_highlights_changed_wikilink() -> None:
    old_line, new_line = format_diff_pair(
        "of [[mundus|Mundus]] whose [[mundus-frame|Mundus Frame]] is",
        "of [[primitives/mundus|Mundus]] whose [[mundus-frame|Mundus Frame]] is",
        indent="      ", color=True,
    )
    # The changed wikilinks get the bold/bright escape; the unchanged
    # [[mundus-frame|...]] must not.
    assert "\x1b[1;91m[[mundus|Mundus]]" in old_line
    assert "\x1b[1;92m[[primitives/mundus|Mundus]]" in new_line
    assert "\x1b[1;91m[[mundus-frame" not in old_line
    assert "\x1b[1;92m[[mundus-frame" not in new_line
    # Base-colour line prefix is present.
    assert old_line.startswith("      \x1b[31m- \x1b[0m")
    assert new_line.startswith("      \x1b[32m+ \x1b[0m")


def test_colored_extra_wikilink_on_new_side_is_highlighted() -> None:
    # The old line has one wikilink; the new line has two.  The first
    # is identical and should NOT be highlighted; the second exists
    # only on the new side and SHOULD be highlighted.
    old_line, new_line = format_diff_pair(
        "See [[harmonia]].",
        "See [[harmonia]] and [[mundus]].",
        indent="  ", color=True,
    )
    assert "\x1b[1;91m" not in old_line  # no changed wikilinks on old side
    assert "\x1b[1;92m[[mundus]]" in new_line
    assert "\x1b[1;92m[[harmonia]]" not in new_line


def test_use_color_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    # Even with a pretend-TTY stream, NO_COLOR wins.
    class FakeTTY:
        def isatty(self) -> bool:
            return True
    assert use_color(FakeTTY()) is False


def test_use_color_false_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    class FakePipe:
        def isatty(self) -> bool:
            return False
    assert use_color(FakePipe()) is False


def test_use_color_true_for_tty_without_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    class FakeTTY:
        def isatty(self) -> bool:
            return True
    assert use_color(FakeTTY()) is True
