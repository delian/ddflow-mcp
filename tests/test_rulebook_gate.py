"""B23: a branch whose base changed the rules since it forked must merge before it commits.

A session loads AGENTS.md, CLAUDE.md and the rest once, at start. An edit landing on the
base afterwards is silently ignored by every branch forked before it, and the drift
compounds: in the source project a branch 98 commits behind had to be hand-ported. The
asymmetry is the one that project settled on -- the SessionStart hook INFORMS
(`tests/test_claude_hooks.py`), the commit gate BLOCKS (here).

Real repositories and, where the claim is about the hook, a real `git commit` through
the installed hook: the hook is the only place this is enforced, and a unit test of the
drift computation would not show that the hook calls it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=180
    )


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _land(root: Path, rel: str, text: str, msg: str = "change") -> None:
    """Commit one file, bypassing the hook: setup, not the thing under test."""
    _write(root, rel, text)
    _git(root, "add", "--", rel)
    r = _git(root, "commit", "-qm", msg, "--no-verify")
    assert r.returncode == 0, r.stderr


def _config(repo: Path, extra: str = "") -> None:
    # Every other pre-commit policy OFF, so nothing but the drift gate can refuse.
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\ngenerated_views = "off"\n'
        f'stale_docs = "off"\n{extra}'
    )


def _cfg(**enforce) -> Config:
    cfg = Config()
    for k, v in enforce.items():
        setattr(cfg.enforce, k, v)
    return cfg


@pytest.fixture
def forked(repo, tmp_path):
    """(repo, wt): a hooked project with rules, and a worktree on `feat` forked from main."""
    run_cli(repo, "init")
    _config(repo)
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, out + err
    _write(repo, "AGENTS.md", "# rules v1\n")
    _write(repo, "src/app.py", "x = 1\n")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "scaffold", "--no-verify").returncode == 0
    wt = tmp_path / "wt"
    r = _git(repo, "worktree", "add", "-q", "-b", "feat", str(wt), "main")
    assert r.returncode == 0, r.stderr
    return repo, wt


def _commit_in(wt: Path, rel: str = "src/work.py", text: str = "y = 2\n"):
    _write(wt, rel, text)
    _git(wt, "add", "--", rel)
    return _git(wt, "commit", "-m", "work")


# -- the stale-rules gate ------------------------------------------------------------------


def test_a_rulebook_changed_on_the_base_refuses_the_commit_through_the_real_hook(forked):
    """FAILS before B23: the commit went through, the new rule unread."""
    repo, wt = forked
    _land(repo, "AGENTS.md", "# rules v2: never do X\n")
    r = _commit_in(wt)
    assert r.returncode != 0, "a branch working to superseded rules was allowed to commit"
    assert "AGENTS.md" in r.stderr and "git merge main" in r.stderr, r.stderr
    assert "stale_rules" in r.stderr, "the refusal must name the knob that governs it"


def test_the_cli_check_refuses_too(forked):
    """`ddflow hooks check-commit` is what the hook runs; FAILS before B23 (exit 0)."""
    repo, wt = forked
    _land(repo, "CLAUDE.md", "# new\n")
    _write(wt, "src/work.py", "y = 2\n")
    _git(wt, "add", "src/work.py")
    env_py = [sys.executable, "-m", "ddflow", "hooks", "check-commit"]
    p = subprocess.run(
        env_py,
        cwd=wt,
        capture_output=True,
        text=True,
        timeout=300,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PATH": "/usr/bin:/bin"},
    )
    assert p.returncode != 0 and "CLAUDE.md" in p.stdout + p.stderr, p.stdout + p.stderr


def test_warn_mode_allows_the_commit_and_says_why(forked):
    """FAILS before B23: nothing was said."""
    repo, wt = forked
    _config(repo, 'stale_rules = "warn"\n')
    _land(repo, "AGENTS.md", "# rules v2\n")
    r = _commit_in(wt)
    assert r.returncode == 0, r.stderr
    assert "AGENTS.md" in r.stderr and "warning only" in r.stderr, r.stderr


def test_off_mode_is_silent(forked):
    """Pins that 'off' really is off. Fails before B23 only because the knobs are unknown."""
    repo, wt = forked
    _config(repo, 'stale_rules = "off"\nbehind = "off"\n')
    _land(repo, "AGENTS.md", "# rules v2\n")
    r = _commit_in(wt)
    assert r.returncode == 0, r.stderr
    assert "AGENTS.md" not in r.stderr, r.stderr


# -- exemptions ----------------------------------------------------------------------------


def test_the_commit_concluding_git_merge_base_is_never_refused(forked):
    """A CONFLICTING merge, so a commit is really needed -- it is the remedy the refusal
    names, and refusing it would make the refusal impossible to clear. The first half
    (an ordinary commit IS refused) FAILS before B23."""
    repo, wt = forked
    _land(wt, "src/app.py", "x = 'feat'\n")
    _land(repo, "src/app.py", "x = 'main'\n")
    _land(repo, "AGENTS.md", "# rules v2\n")
    assert _commit_in(wt).returncode != 0, "precondition: the stale branch is refused"

    assert _git(wt, "reset", "-q", "HEAD", "--", "src/work.py").returncode == 0
    merge = _git(wt, "merge", "main")
    assert merge.returncode != 0 and "CONFLICT" in merge.stdout, merge.stdout
    _write(wt, "src/app.py", "x = 'both'\n")
    _git(wt, "add", "src/app.py")
    r = _git(wt, "commit", "--no-edit")
    assert r.returncode == 0, f"the merge that fixes the drift was refused: {r.stderr}"
    # ...and with the base merged, ordinary work commits again.
    assert _commit_in(wt).returncode == 0


def test_merging_something_other_than_the_base_is_not_exempt(forked):
    repo, wt = forked
    _git(repo, "checkout", "-q", "-b", "side")
    _land(repo, "src/side.py", "s = 1\n")
    _git(repo, "checkout", "-q", "main")
    _land(repo, "AGENTS.md", "# rules v2\n")
    merge = _git(wt, "merge", "--no-commit", "--no-ff", "side")
    assert merge.returncode == 0, merge.stderr
    assert _git(wt, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0
    assert E.check_drift(repo, _cfg(), here=wt)[0] == 1


def test_the_primary_checkout_on_the_base_branch_is_not_behind(forked):
    repo, _wt = forked
    _land(repo, "AGENTS.md", "# rules v2\n")
    assert E.check_drift(repo, _cfg(), here=repo) == (0, "")
    _write(repo, "src/more.py", "z = 3\n")
    _git(repo, "add", "src/more.py")
    r = _git(repo, "commit", "-m", "on main")
    assert r.returncode == 0 and "behind" not in r.stderr, r.stderr


def test_a_branch_not_behind_at_all_commits_silently(forked):
    repo, wt = forked
    assert E.check_drift(repo, _cfg(), here=wt) == (0, "")


def test_rulebooks_edited_on_this_branch_are_not_staleness(forked):
    """The three-dot diff: this branch's own rule edits are the NEW rules. Behind, but
    only by a commit that touched no rulebook."""
    repo, wt = forked
    _land(wt, "AGENTS.md", "# rules v2 written here\n")
    _land(repo, "src/other.py", "q = 1\n")
    assert E.check_drift(repo, _cfg(), here=wt) == (0, "")


# -- which files are rulebooks -------------------------------------------------------------


def test_every_native_rules_file_is_a_rulebook_derived_not_retyped():
    """FAILS before B23 (no `rulebooks`)."""
    from ddflow.services.adopt import NATIVE_RULES

    books = E.rulebooks()
    for rule in NATIVE_RULES.values():
        assert rule.path in books, f"{rule.path} changing on the base would go unnoticed"
    for fixed in ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".ddflow/config.toml"):
        assert fixed in books


def test_a_native_rules_file_changed_on_the_base_counts(forked):
    """FAILS before B23: `.cursor/rules/ddflow.mdc` was not a rulebook at all."""
    repo, wt = forked
    _land(repo, ".cursor/rules/ddflow.mdc", "---\nalwaysApply: true\n---\nnew rule\n")
    code, msg = E.check_drift(repo, _cfg(), here=wt)
    assert code == 1 and ".cursor/rules/ddflow.mdc" in msg, msg


def test_a_driver_doc_changed_on_the_base_counts(forked):
    """FAILS before B23: the driver directory was not watched."""
    repo, wt = forked
    _land(repo, "docs/ddflow/drivers/implement-phase.md", "# driver v2\n")
    code, msg = E.check_drift(repo, _cfg(), here=wt)
    assert code == 1 and "docs/ddflow/drivers/implement-phase.md" in msg, msg


def test_a_generated_view_changing_on_the_base_is_not_a_rulebook(forked):
    """docs/ddflow/ also holds the rendered queue, which moves constantly: only the
    drivers under it are rules."""
    repo, wt = forked
    _land(repo, "docs/ddflow/QUEUE.md", "queue moved\n")
    assert E.check_drift(repo, _cfg(), here=wt) == (0, "")


# -- the behind count ----------------------------------------------------------------------


def test_behind_warns_above_max_behind_and_is_silent_at_it(forked):
    """FAILS before B23."""
    repo, wt = forked
    for i in range(2):
        _land(repo, f"src/m{i}.py", f"v = {i}\n")
    assert E.check_drift(repo, _cfg(max_behind=2), here=wt) == (0, ""), "at the limit"
    _land(repo, "src/m2.py", "v = 2\n")
    code, msg = E.check_drift(repo, _cfg(max_behind=2), here=wt)
    assert code == 0 and "3 commits behind `main`" in msg and "warning only" in msg, msg
    code, msg = E.check_drift(repo, _cfg(max_behind=2, behind="block"), here=wt)
    assert code == 1 and "git merge main" in msg, msg
    assert E.check_drift(repo, _cfg(max_behind=2, behind="off"), here=wt) == (0, "")


def test_max_behind_zero_or_negative_is_refused_not_read_as_off(repo, forked):
    """FAILS before B23: the knob did not exist, and 0 must never mean 'off'."""
    for bad in (0, -5):
        with pytest.raises(ValueError, match="max_behind"):
            Config.check({"enforce": {"max_behind": bad}})
        code, msg = E.check_drift(repo, _cfg(max_behind=bad), here=forked[1])
        assert code == 1 and "invalid" in msg and 'behind = "off"' in msg, msg
    Config.check({"enforce": {"max_behind": 1}})

    _config(repo, "max_behind = 0\n")
    code, out, err = run_cli(repo, "hooks", "check-commit")
    assert code != 0 and "max_behind" in out + err, out + err


def test_could_not_tell_warns_with_the_reason_and_never_blocks(repo, tmp_path):
    """An unborn HEAD: not "0 behind", and not a refusal either."""
    unborn = tmp_path / "unborn"
    subprocess.run(["git", "init", "-q", "-b", "main", str(unborn)], check=True)
    code, msg = E.check_drift(repo, _cfg(stale_rules="block", behind="block"), here=unborn)
    assert code == 0 and "could not tell" in msg and "NOT a pass" in msg, msg


# -- which base ----------------------------------------------------------------------------


def test_the_items_recorded_base_is_used_when_the_tree_is_an_items_worktree(forked):
    """A task forked from `release` merges into `release`; `main` moving its rules is not
    this branch's drift. And `release` moving its rules IS. FAILS before B23."""
    repo, _wt = forked
    wt2 = repo.parent / "wt-release"
    _git(repo, "branch", "release", "main")
    assert _git(repo, "worktree", "add", "-q", "-b", "fix", str(wt2), "release").returncode == 0
    EventLog(repo, "agent-test").append(
        "worktree.created", "T1", {"path": str(wt2), "branch": "fix", "base": "release"}
    )
    _land(repo, "AGENTS.md", "# main's rules moved\n")
    assert E.drift_base(repo, wt2) == "release"
    assert E.check_drift(repo, _cfg(), here=wt2) == (0, "")

    _git(repo, "checkout", "-q", "release")
    _land(repo, "CLAUDE.md", "# release's rules moved\n")
    _git(repo, "checkout", "-q", "main")
    code, msg = E.check_drift(repo, _cfg(), here=wt2)
    assert code == 1 and "`release`" in msg and "CLAUDE.md" in msg, msg


def test_a_recorded_base_that_no_longer_resolves_falls_back_to_the_default_branch(forked):
    repo, wt = forked
    EventLog(repo, "agent-test").append(
        "worktree.created", "T2", {"path": str(wt), "branch": "feat", "base": "gone-branch"}
    )
    assert E.drift_base(repo, wt) == "main"
