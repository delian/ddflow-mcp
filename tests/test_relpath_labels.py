"""Pins the repo-relative labels legacy.scan and export templates print (B-relpath-labels).

Both now go through fsio.repo_rel(as_given=True, strict=False); every path production
code builds prints exactly what the old lexical derivation printed, also when the
repository is reached through a symlink -- except an operator-named legacy.scan extra=
path containing '..', which repo_rel resolves rather than trusting lexically. legacy.scan's label stays native text (a Path,
str()-ed), templates.rel's stays POSIX text, as each was before.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ddflow.services import legacy as L
from ddflow.services.export import templates as T


def _old_legacy_label(repo: Path, path: Path) -> str:
    """legacy.scan's derivation before B-relpath-labels (native separators)."""
    return str(path.relative_to(repo) if path.is_relative_to(repo) else path)


def _old_template_label(repo: Path, path: Path) -> str:
    """templates.rel before B-relpath-labels (POSIX separators)."""
    return path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path)


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
    expected = [".claude/commands/done.md", "CLAUDE.md", "docs/HANDOFF.md"]
    assert labels == sorted(str(Path(e)) for e in expected)
    for label in labels:
        assert label == _old_legacy_label(repo, repo / label)


def test_legacy_labels_a_named_file_outside_the_repo_by_its_path(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "handoff.md"
    outside.write_text("Remember to update lessons.md each time.\n")
    [proposal] = L.scan(repo, [], extra=[str(outside)])
    assert proposal.path == str(outside) == _old_legacy_label(repo, outside)


@pytest.mark.parametrize("kind", ["bugs", "worklog"])
def test_template_rel_matches_the_old_label(repo: Path, kind: str):
    for p in (repo / ".ddflow", T.project_path(repo, kind)):
        assert T.rel(repo, p) == _old_template_label(repo, p)
    assert T.rel(repo, T.project_path(repo, kind)) == f".ddflow/templates/export/{kind}.md.j2"


def test_template_rel_outside_the_repo_is_the_path_as_given(tmp_path: Path):
    repo = tmp_path / "repo"
    other = tmp_path / "elsewhere" / "x.md.j2"
    assert T.rel(repo, other) == str(other)


def test_a_symlinked_rulebook_is_labelled_by_its_own_name(tmp_path: Path):
    """as_given=True: a CLAUDE.md that links to AGENTS.md (or out of the repo) prints as
    CLAUDE.md, the name the operator sees, never as its target."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("- tick the checkbox\n")
    os.symlink(repo / "AGENTS.md", repo / "CLAUDE.md")
    outside = tmp_path / "local-target.md"
    outside.write_text("- tick the checkbox\n")
    os.symlink(outside, repo / "CLAUDE.local.md")
    labels = sorted(p.path for p in L.scan(repo, []))
    assert labels == ["AGENTS.md", "CLAUDE.local.md", "CLAUDE.md"]


def test_a_symlinked_template_is_labelled_by_its_own_name(tmp_path: Path):
    repo = tmp_path / "repo"
    dst = T.project_path(repo, "bugs")
    dst.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere.md.j2"
    target.write_text("x")
    os.symlink(target, dst)
    assert T.rel(repo, dst) == ".ddflow/templates/export/bugs.md.j2"


def test_a_named_path_with_dotdot_is_labelled_as_repo_rel_resolves_it(tmp_path: Path):
    """The one deliberate difference (B-relpath-labels): repo_rel never trusts a '..'
    lexically, since '..' can leave the repository. Only legacy.scan's extra= can carry
    one, and no production caller passes extra= (api/onboard.py calls scan(repo, imported))."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "HANDOFF.md").write_text("Remember to update lessons.md each time.\n")
    [proposal] = L.scan(repo, [], extra=["docs/../HANDOFF.md"])
    assert proposal.path == "HANDOFF.md"
