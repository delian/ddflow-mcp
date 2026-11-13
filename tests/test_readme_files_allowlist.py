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
    assert GUIDES, "the guide list is empty, so the loop below would pass vacuously"
    listed = {f for f in Config.load(ROOT).enforce.readme_files if f != "README.md"}
    assert listed == {f"docs/guide/{g}.md" for g in GUIDES}, "readme_files and GUIDES disagree"
    for g in GUIDES:
        assert is_shared(f"docs/guide/{g}.md", patterns), g
    assert not is_shared("docs/ddflow/RESEARCH.md", patterns)
    assert not is_shared("docs/guide/tokens.md", patterns), (
        "a page outside the split is not the README"
    )


def test_a_guide_edit_satisfies_the_real_readme_with_code_check(repo):
    """End to end through `completion_verdict`, in a project configured as this one is."""
    import json

    from conftest import run_cli
    from helpers import git_quiet as git

    from ddflow.api import completion_verdict
    from ddflow.api._base import _load
    from ddflow.infra import worktree as W

    files = Config.load(ROOT).enforce.readme_files
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = cfg.read_text()
    assert "[enforce]\n" in text
    cfg.write_text(
        text.replace("[enforce]\n", f"[enforce]\nreadme_files = {json.dumps(files)}\n", 1)
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "a task", "--globs", "ddflow/**,docs/**,tests/**")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == 0, out + err
    tree = W.load_path(repo, _load(repo)[2].items["T1"].worktree)

    def commit(rel: str) -> None:
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n" if rel.endswith(".py") else "# t\n")
        git(tree, "add", str(p))
        git(tree, "commit", "-qm", "work")

    def reported() -> list[str]:
        out = completion_verdict(repo, "T1")
        return [w for w in out.data["warnings"] + out.data["blockers"] if "README not updated" in w]

    commit("ddflow/feature.py")
    assert reported(), "code with no README or guide change must be reported"
    commit("docs/guide/workflow.md")
    assert not reported(), "a guide edit must count as updating the README"
