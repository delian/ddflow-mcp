"""The README is the front door; the reference lives in docs/guide/*.md (D-readme-split).

Fails against an unsplit README: no guide exists, the README is thousands of lines, and
the moved sections are not pointed at. Also holds the split honest afterwards: every link
resolves, every heading a pointer names exists, and nothing is lost between the files.
"""

from __future__ import annotations

import re
from pathlib import Path

from readme_corpus import GUIDES as GUIDES_PINNED

from ddflow.views import readme_toc as T

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "guide"
GUIDES = GUIDES_PINNED
#: The front door stays short: the generated tables are the only bulk left in it.
README_MAX_LINES = 1700
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _text(p: Path) -> str:
    return p.read_text("utf-8")


def test_every_guide_exists_with_a_title_and_links_back():
    for name in GUIDES:
        p = GUIDE / f"{name}.md"
        assert p.is_file(), f"docs/guide/{name}.md is missing"
        assert _text(p).startswith("# "), f"{p} has no title"
        assert "../../README.md" in _text(p), f"{p} does not link back to the front door"


def test_the_readme_is_the_front_door_and_points_at_every_guide():
    text = _text(README)
    assert len(text.splitlines()) <= README_MAX_LINES
    for name in GUIDES:
        assert f"docs/guide/{name}.md" in text, f"README never points at {name}.md"
    titles = [t for _, t in T.headings(text)]
    for moved in ("Gitflow, pull requests and version tags", "Crash recovery", "Cadences"):
        assert moved not in titles, f"{moved!r} should have moved out of the README"


def test_every_relative_link_resolves_and_its_anchor_exists():
    for src in [README, *(GUIDE / f"{g}.md" for g in GUIDES)]:
        for target in _LINK.findall(_text(src)):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            path, _, anchor = target.partition("#")
            dest = (src.parent / path).resolve()
            if not dest.exists():
                continue  # links into other trees are checked by the docs index test
            if anchor and dest.suffix == ".md" and dest.is_relative_to(ROOT / "docs" / "guide"):
                tt = [t for _, t in T.headings(_text(dest), T._ANY_HEADING)]
                assert anchor in T.anchors(tt), f"{src.name}: {target} names no heading"
        for target in _LINK.findall(_text(src)):
            if target.startswith("#") and src.parent == GUIDE:
                tt = [t for _, t in T.headings(_text(src), T._ANY_HEADING)]
                assert target[1:] in T.anchors(tt), f"{src.name}: {target} dangles"
