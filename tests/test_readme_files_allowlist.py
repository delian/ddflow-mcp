"""A task that edits the docs where the README's reference now lives counts as updating the README.

D-readme-split moved the reference to docs/guide/*.md; `[enforce].readme_files` (the allowlist
behind the readme_with_code completion check) must name those files, or a task that documents its
change in a guide is still told the README did not move.
"""

from __future__ import annotations

from pathlib import Path

from readme_corpus import GUIDES

from ddflow.config import Config
from ddflow.core.admission import is_shared
from ddflow.services.completion.rules import _root_anchored

ROOT = Path(__file__).resolve().parents[1]


def test_the_projects_readme_files_cover_the_front_door_and_every_guide():
    patterns = _root_anchored(Config.load(ROOT).enforce.readme_files)
    assert is_shared("README.md", patterns)
    for g in GUIDES:
        assert is_shared(f"docs/guide/{g}.md", patterns), g
    assert not is_shared("docs/ddflow/RESEARCH.md", patterns)
