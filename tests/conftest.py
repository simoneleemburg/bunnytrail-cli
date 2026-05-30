"""
Shared pytest fixtures for the Alteria CLI test suite.

The fixtures build small synthetic projects on disk so tests can
exercise the wikilink resolver and the move/rename/crosslink
machinery without depending on the real content/ tree.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entity(dir_: Path, name: str, kind: str = "place", body: str = "") -> None:
    frontmatter = f"---\nname: {name}\nkind: {kind}\n---\n"
    _write(dir_ / "index.md", frontmatter + body)


def _collection(dir_: Path, title: str, *, universal: bool = False) -> None:
    lines = ["---", f"title: {title}"]
    if universal:
        lines.append("universal: true")
    lines.append("---")
    _write(dir_ / "_collection.md", "\n".join(lines) + "\n")


def _guide(dir_: Path, title: str, body: str = "") -> None:
    fm = f"---\ntitle: {title}\nsummary: stub\n---\n"
    _write(dir_ / "index.md", fm + body)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal but realistic project.

    Layout::

        STRUCTURE.md
        content/
            foundation/              [universal]
                _collection.md
                concepts/
                    harmonia/        entity
            aurethia/                [cluster]
                _collection.md
                places/
                    sharazan/        entity
                    bayurinda/       collection
                        _collection.md
                        nuunlau/     entity
                people/
                    languages/
                        thallish/    entity (code: tha)
                    duskmere/        entity
            earth/                   [cluster]
                _collection.md
                places/
                    shanghai/        entity
                people/
                    duskmere/        entity (slug collision with aurethia)
        content_meta/
            kinds/
                place/               kind
                being/
                    human/           kind
    """
    root = tmp_path
    _write(root / "STRUCTURE.md", "stub\n")
    content = root / "content"
    meta = root / "content_meta"

    # Foundation (universal substrate)
    _collection(content / "foundation", "Foundation", universal=True)
    _entity(content / "foundation" / "concepts" / "harmonia", "Harmonia", "concept")

    # Aurethia cluster
    _collection(content / "aurethia", "Aurethia")
    _entity(content / "aurethia" / "places" / "sharazan", "Sharazan")
    _collection(content / "aurethia" / "places" / "bayurinda", "Bayurinda")
    _entity(content / "aurethia" / "places" / "bayurinda" / "nuunlau", "Nuunlau")
    _write(
        content / "aurethia" / "people" / "languages" / "thallish" / "index.md",
        "---\nname: Thallish\nkind: language\ncode: tha\n---\nBody\n",
    )
    _entity(content / "aurethia" / "people" / "duskmere", "Duskmere", "being")

    # Earth cluster
    _collection(content / "earth", "Earth")
    _entity(content / "earth" / "places" / "shanghai", "Shanghai")
    _entity(content / "earth" / "people" / "duskmere", "Duskmere", "being")

    # Kinds
    (meta / "kinds" / "place").mkdir(parents=True)
    (meta / "kinds" / "being" / "human").mkdir(parents=True)
    # The 'human' kind has a _kind.md so crosslink can match its display
    # names; 'place' and 'being' exist only as folders (folder-existence
    # is enough to register the id, per STRUCTURE.md).
    _write(
        meta / "kinds" / "being" / "human" / "_kind.md",
        "---\nsingular: human\nplural: humans\n---\n",
    )

    # A guide that mentions several entities by display name so we
    # can exercise rename-updates-guide-body and crosslink-on-guide.
    _guide(
        meta / "guides" / "cognita",
        "Alteria Cognita",
        "An overview that touches Sharazan and Nuunlau, the "
        "Aurethian cluster, and humans in general.\n",
    )

    return root
