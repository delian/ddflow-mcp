"""Bug B20d45f540c: a squash-landed gitflow hotfix was never counted shipped on develop.

Under `merge_strategy = "squash"` the hotfix lands on main as ONE parent commit, and the
back-merge into develop is a second squash commit: neither is an ancestor of the other
line, so `reached` (ancestry only) said the item was not on develop and version planning
dropped it and its bump. The squash is now recognised by its patch-id, or by the back-merge
message ddflow writes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def gitflow_squash(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-q", "develop")
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nmodel = "gitflow"\n')
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "squash")
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt")
    return repo


def _land(repo: Path, item: str, tags: str, fname: str) -> None:
    run_cli(repo, "task", "add", item, "--tags", tags)
    code, out, err = run_cli(repo, "--json", "claim", item)
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / fname).write_text("fixed\n")
    _git(tree, "add", fname)
    _git(tree, "commit", "-qm", "fix: the outage")
    code, out, err = run_cli(repo, "merge", item)
    assert code == 0, out + err
    pass_pipeline(repo, item)
    code, out, err = run_cli(repo, "complete", item, "--model", "claude-opus-5")
    assert code == 0, out + err


def test_a_squash_landed_hotfix_ships_on_develop(gitflow_squash):
    repo = gitflow_squash
    _land(repo, "H1", "hotfix", "fix.py")
    main_tip = _git(repo, "rev-parse", "main")
    assert len(_git(repo, "rev-list", "--parents", "-n1", main_tip).split()) == 2, "one parent"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", main_tip, "develop"]
        ).returncode
        != 0
    ), "main's squash is not on develop"
    assert _git(repo, "show", "develop:fix.py") == "fixed", "the back-merge brought the work"

    code, out, err = run_cli(repo, "--json", "version", "show")
    assert code == 0, out + err
    assert "H1" in json.loads(out)["items"], out


def test_a_plain_feature_squashed_to_develop_is_still_found(gitflow_squash):
    repo = gitflow_squash
    _land(repo, "F1", "", "feat.py")
    code, out, err = run_cli(repo, "--json", "version", "show")
    assert code == 0, out + err
    assert "F1" in json.loads(out)["items"], out


def test_an_unrelated_develop_is_not_credited_with_an_unported_squash(repo):
    from ddflow.config import Config
    from ddflow.core.model import Item
    from ddflow.services.flow import reached

    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "develop")
    (repo / "d.txt").write_text("d\n")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-qm", "develop work")
    _git(repo, "checkout", "-q", "main")
    (repo / "h.txt").write_text("hotfix\n")
    _git(repo, "add", "h.txt")
    _git(repo, "commit", "-qm", "merge H9: hotfix")
    landed = _git(repo, "rev-parse", "HEAD")
    cfg = Config()
    cfg.worktree.merge_strategy = "squash"
    it = Item(id="H9", kind="task", merged_sha=landed, landed_before=base, landed_after=landed)
    assert reached(repo, cfg, it, "develop") == ""
    assert reached(repo, cfg, it, "main") == landed
    # ... until the same change is ported to develop by any means (here: a cherry-pick).
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "cherry-pick", landed)
    assert reached(repo, cfg, it, "develop") == _git(repo, "rev-parse", "develop")


def test_the_back_merge_commit_ddflow_wrote_is_found_when_the_patch_ids_differ(repo):
    """Develop drifted around the hunks, so the back-merge squash is a different patch."""
    from ddflow.config import Config
    from ddflow.core.model import Item
    from ddflow.services.flow import reached

    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "develop")
    (repo / "h.txt").write_text("drifted\n")
    _git(repo, "add", "h.txt")
    _git(repo, "commit", "-qm", "develop work")
    _git(repo, "checkout", "-q", "main")
    (repo / "h.txt").write_text("hotfix\n")
    _git(repo, "add", "h.txt")
    _git(repo, "commit", "-qm", "merge H9: hotfix")
    landed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "develop")
    (repo / "h.txt").write_text("hotfix and more\n")
    _git(repo, "commit", "-qam", "back-merge H9 into develop")
    back = _git(repo, "rev-parse", "HEAD")
    cfg = Config()
    cfg.worktree.merge_strategy = "squash"
    it = Item(id="H9", kind="task", merged_sha=landed, landed_before=base, landed_after=landed)
    assert reached(repo, cfg, it, "develop") == back
    other = Item(id="H8", kind="task", merged_sha=landed, landed_before=base, landed_after=landed)
    assert reached(repo, cfg, other, "develop") == "", "another item's back-merge is not this one's"
