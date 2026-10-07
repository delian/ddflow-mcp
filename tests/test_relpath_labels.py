"""Pins the repo-relative labels legacy.scan and export templates print (B-relpath-labels).

Both now go through fsio.repo_rel(as_given=True, strict=False); every path production
code builds must print exactly what the old lexical derivation printed, also when the
repository is reached through a symlink.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ddflow.services import legacy as L
from ddflow.services.export import templates as T


def _old_label(repo: Path, path: Path) -> str:
    """The derivation legacy.scan and templates.rel used before B-relpath-labels."""
    return str(path.relative_to(repo) if path.is_relative_to(repo) else path)


@pytest.fixture(params=["direct", "via-symlink"])
def repo(request, tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    if request.param == "direct":
        return real
    link = tmp_path / "link"
    os.symlink(real, link)
    return link


def test_legacy_labels_rulebooks_and_commands_as_before(repo: Path):
    (repo / "CLAUDE.md").write_text("- tick the checkbox\n")
    (repo / ".claude" / "commands").mkdir(parents=True)
    (repo / ".claude" / "commands" / "done.md").write_text("Tick the box when finished.\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "HANDOFF.md").write_text("Remember to update lessons.md each time.\n")
    proposals = L.scan(repo, [], extra=["docs/HANDOFF.md", str(repo / "CLAUDE.md")])
    labels = sorted({p.path for p in proposals})
    assert labels == [".claude/commands/done.md", "CLAUDE.md", "docs/HANDOFF.md"]
    for label in labels:
        assert label == _old_label(repo, repo / label)


def test_legacy_labels_a_named_file_outside_the_repo_by_its_path(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "handoff.md"
    outside.write_text("Remember to update lessons.md each time.\n")
    [proposal] = L.scan(repo, [], extra=[str(outside)])
    assert proposal.path == str(outside) == _old_label(repo, outside)


@pytest.mark.parametrize("kind", ["bugs", "worklog"])
def test_template_rel_matches_the_old_label(repo: Path, kind: str):
    for p in (repo / ".ddflow", T.project_path(repo, kind)):
        assert T.rel(repo, p) == _old_label(repo, p).replace(os.sep, "/")
    assert T.rel(repo, T.project_path(repo, kind)) == f".ddflow/templates/export/{kind}.md.j2"


def test_template_rel_outside_the_repo_is_the_path_as_given(tmp_path: Path):
    repo = tmp_path / "repo"
    other = tmp_path / "elsewhere" / "x.md.j2"
    assert T.rel(repo, other) == str(other)
