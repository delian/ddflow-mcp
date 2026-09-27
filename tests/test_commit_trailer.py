"""`[enforce] require_item_trailer`, checked on the message actually being committed.

It ran in pre-commit and read `.git/COMMIT_EDITMSG` -- which git writes only AFTER
pre-commit, so it held the PREVIOUS commit's message. A commit with `Item: B168` was
refused, and after any commit that had a trailer, one without it passed. Found by
running ddflow on its own repository; there was no test of the check at all.

Every test here makes a real commit through the installed hooks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _commit(repo: Path, name: str, message: str) -> subprocess.CompletedProcess:
    (repo / name).write_text(name)
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", message],
        capture_output=True,
        text=True,
        timeout=300,
    )


def _setup(repo: Path, config: str) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(config)
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, err
    assert "commit-msg" in out


def test_the_message_BEING_committed_is_checked_not_the_previous_one(repo):
    _setup(repo, "[enforce]\nrequire_item_trailer = true\n")
    ok = _commit(repo, "a.txt", "add a\n\nItem: T1")
    assert ok.returncode == 0, ok.stderr
    refused = _commit(repo, "b.txt", "add b, no trailer")
    assert refused.returncode != 0, "a commit without a trailer passed after one with it"
    assert "Item: <id>" in refused.stderr
    again = _commit(repo, "c.txt", "add c\n\nItem: T2")
    assert again.returncode == 0, again.stderr


def test_a_project_keeps_its_own_trailer_keys(repo):
    _setup(
        repo,
        '[enforce]\nrequire_item_trailer = true\nitem_trailer_keys = ["Phase", "Phase-ships"]\n',
    )
    assert _commit(repo, "a.txt", "x\n\nPhase: OPIK.2").returncode == 0
    assert _commit(repo, "b.txt", "docs\n\nPhase-ships: none").returncode == 0
    refused = _commit(repo, "c.txt", "x\n\nItem: OPIK.3")
    assert refused.returncode != 0, "a key the project does not use satisfied the check"
    assert "`Phase: <id>` or `Phase-ships: <id>`" in refused.stderr


def test_a_trailer_only_inside_a_comment_line_does_not_count(repo):
    _setup(repo, "[enforce]\nrequire_item_trailer = true\n")
    assert _commit(repo, "a.txt", "x\n#Item: T1").returncode != 0


def test_off_by_default(repo):
    _setup(repo, "")
    assert _commit(repo, "a.txt", "no trailer and nobody minds").returncode == 0


def test_a_merge_commit_carries_its_branchs_trailers_and_is_exempt(repo):
    _setup(repo, "[enforce]\nrequire_item_trailer = true\n")
    assert _commit(repo, "base.txt", "base\n\nItem: T0").returncode == 0
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat"], check=True)
    assert _commit(repo, "f.txt", "feature\n\nItem: T1").returncode == 0
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    assert _commit(repo, "m.txt", "main moves\n\nItem: T2").returncode == 0
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "-q", "-m", "Merge feat", "feat"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert merge.returncode == 0, merge.stderr


def test_a_foreign_commit_msg_hook_is_left_alone_and_the_line_to_add_is_given(repo):
    run_cli(repo, "init")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "commit-msg").write_text("#!/bin/sh\n# pre-commit framework\nexit 0\n")
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, err
    assert "NOT INSTALLED" in out and "hooks check-msg" in out
    assert "pre-commit framework" in (hooks / "commit-msg").read_text()
    assert "DDFLOW-HOOK" in (hooks / "pre-commit").read_text()
