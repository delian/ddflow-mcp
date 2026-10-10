"""Review and standards-gate integrity.

B1ed2b4fde6: `roborev review HEAD` run from an item's worktree once enqueued the PRIMARY
checkout's HEAD, so the standards gate was recorded against the wrong commit. `gate
record --reviewed-sha` names what was reviewed and ddflow compares it with the item's
branch head.

B2bf4d38cc1: `ddflow review` put untracked worktree files in the prompt, so a draft left
untracked was reviewed. The diff is the branch commits plus tracked edits; untracked files
are listed as "untracked, not reviewed".

B1979dac602: `gate record --model <author model>` on a reviewer gate made `complete`
judge independence against the author's own family. An author-family model on a reviewer
gate is refused unless `--reviewer-model` says it IS the reviewer.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _item(repo: Path) -> Path:
    """T1 claimed with a ddflow worktree holding one commit; returns that tree."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py,draft.py")
    code, out, err = run_cli(repo, "claim", "T1", agent="impl")
    assert code == OK, out + err
    st = fold(EventLog(repo).read_all(), strict=False)
    tree = (repo / st.items["T1"].worktree).resolve()
    (tree / "a.py").write_text("COMMITTED = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    return tree


def _gate(repo: Path, gate: str):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


# --- B1ed2b4fde6 -------------------------------------------------------------------


def test_a_reviewed_sha_that_is_not_the_branch_head_is_refused(repo):
    _item(repo)
    main_head = _git(repo, "rev-parse", "HEAD")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "standards",
        "--evidence",
        "roborev job 1",
        "--reviewed-sha",
        main_head,
        agent="impl",
    )
    assert code == REFUSED, out + err
    assert "branch head" in err
    assert "standards" not in fold(EventLog(repo).read_all(), strict=False).items["T1"].gates


def test_the_branch_head_is_recorded_as_the_reviewed_sha(repo):
    tree = _item(repo)
    head = _git(tree, "rev-parse", "HEAD")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "standards",
        "--evidence",
        "roborev job 1",
        "--reviewed-sha",
        head[:10],
        agent="impl",
    )
    assert code == OK, out + err
    assert _gate(repo, "standards").evidence["reviewed_sha"] == head


def test_an_older_branch_commit_is_recorded_with_a_warning(repo):
    tree = _item(repo)
    old = _git(tree, "rev-parse", "HEAD")
    (tree / "a.py").write_text("COMMITTED = 2\n")
    _git(tree, "commit", "-qam", "again")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "standards",
        "--evidence",
        "roborev job 1",
        "--reviewed-sha",
        old,
        agent="impl",
    )
    assert code == OK, out + err
    assert "older" in err
    assert _gate(repo, "standards").evidence["reviewed_sha"] == old


def test_the_standards_prompt_says_to_pass_an_explicit_sha():
    from ddflow.services.gates import DEFAULT_GATES

    assert "roborev review <sha>" in DEFAULT_GATES["standards"].prompt


# --- B2bf4d38cc1 -------------------------------------------------------------------


def _reviewer(repo: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(f"#!/bin/sh\ncat >> '{seen}'\nprintf 'STATUS: NO FINDINGS\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    with (repo / ".ddflow" / "config.toml").open("a") as f:
        f.write(
            f'\n[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
            'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
        )
    return seen


def test_untracked_files_are_not_reviewed_and_are_listed(repo, tmp_path):
    tree = _item(repo)
    seen = _reviewer(repo, tmp_path)
    (tree / "draft.py").write_text("UNTRACKED_DRAFT = 1\n")
    (tree / "a.py").write_text("COMMITTED = 1\nTRACKED_EDIT = 1\n")
    code, out, err = run_cli(tree, "review", "T1", agent="impl")
    assert code == OK, out + err
    prompt = seen.read_text()
    assert "COMMITTED = 1" in prompt and "TRACKED_EDIT" in prompt
    assert "UNTRACKED_DRAFT" not in prompt
    assert "untracked, not reviewed" in out + err
    assert "draft.py" in out + err
    ev = _gate(repo, "critic").evidence
    assert "untracked, not reviewed" in ev["diff_source"] and "draft.py" in ev["diff_source"]


# --- B1979dac602 -------------------------------------------------------------------


def _author(repo: Path) -> None:
    code, out, err = run_cli(repo, "session", "start", "--model", "claude-opus", agent="impl")
    assert code == OK, out + err


def test_an_author_family_model_on_a_reviewer_gate_is_refused(repo):
    _item(repo)
    _author(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--evidence",
        "found nothing",
        "--model",
        "claude",
        agent="impl",
    )
    assert code == REFUSED, out + err
    assert "--reviewer-model" in err
    assert "critic" not in fold(EventLog(repo).read_all(), strict=False).items["T1"].gates


def test_a_foreign_model_on_a_reviewer_gate_still_records_with_model(repo):
    _item(repo)
    _author(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--evidence",
        "found nothing",
        "--model",
        "gemini-2.5-pro",
        agent="impl",
    )
    assert code == OK, out + err
    assert _gate(repo, "critic").evidence["model"] == "gemini-2.5-pro"


def test_reviewer_model_states_that_the_model_is_the_reviewer(repo):
    _item(repo)
    _author(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "critic",
        "--evidence",
        "a same-family second opinion",
        "--reviewer-model",
        "claude-sonnet",
        agent="impl",
    )
    assert code == OK, out + err
    assert _gate(repo, "critic").evidence["model"] == "claude-sonnet"


def test_a_symbolic_ref_is_not_a_reviewed_sha(repo):
    _item(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "standards",
        "--evidence",
        "x",
        "--reviewed-sha",
        "HEAD",
        agent="impl",
    )
    assert code == REFUSED, out + err
    assert "symbolic ref" in err


def test_the_standards_gate_is_guarded_too(repo):
    _item(repo)
    _author(repo)
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "standards",
        "--evidence",
        "x",
        "--model",
        "claude",
        agent="impl",
    )
    assert code == REFUSED, out + err
    assert "--reviewer-model" in err
