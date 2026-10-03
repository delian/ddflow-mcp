"""The multi-user model, probed with real git (B193).

docs/ddflow/MULTI-USER.md says what merges, what does not, and what a forge that ignores
`.gitattributes` `merge=union` does to a pull request. These tests are the evidence for
those sentences: a forge's server-side merge is simulated by a clone WITHOUT the
attribute, because that is what a merge driver the forge does not run looks like.
Whether GitHub or GitLab actually run it is UNVERIFIED (no forge is reachable here).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_cli

UNION = ".ddflow/events/*.jsonl merge=union\n"


def _git(cwd: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *argv], check=check, capture_output=True, text=True
    )


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _setup(tmp_path: Path, *, union: bool) -> tuple[Path, Path]:
    """A shared project (item X0, committed) and two clones of it."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    if union:
        (seed / ".gitattributes").write_text(UNION)
        _git(seed, "add", ".gitattributes")
        _git(seed, "commit", "-qm", "attrs", "--no-verify")
    assert run_cli(seed, "task", "add", "X0", "--title", "base", agent="seed")[0] == 0
    _commit(seed, "X0")
    _git(seed, "push", "-q", "origin", "main")
    return _clone(remote, tmp_path / "a"), _clone(remote, tmp_path / "b")


def _shows(repo: Path, item: str) -> bool:
    return run_cli(repo, "--json", "show", item)[0] == 0


@pytest.mark.parametrize("union", [True, False], ids=["union-honoured", "union-ignored"])
def test_distinct_writers_merge_cleanly_whether_or_not_union_is_honoured(tmp_path, union):
    """Two people, two shards: no textual conflict at all, so a forge's PR merge needs no
    merge driver. This is why the design does not depend on the forge honouring union."""
    a, b = _setup(tmp_path, union=union)
    assert run_cli(a, "task", "add", "XA", "--title", "from a", agent="alice")[0] == 0
    _commit(a, "XA")
    assert run_cli(b, "task", "add", "XB", "--title", "from b", agent="bob")[0] == 0
    _commit(b, "XB")
    _git(a, "push", "-q", "origin", "main")
    _git(b, "pull", "--no-rebase", "--no-edit", "-q", "origin", "main")
    assert _shows(b, "XA") and _shows(b, "XB") and _shows(b, "X0")


def test_one_shard_written_by_two_branches_conflicts_if_union_is_not_honoured(tmp_path):
    """The case that DOES need the driver: both branches appended to the same shard (the
    same agent id on two branches). Without union git stops with a conflict; the fix is a
    local merge, where the attribute is honoured."""
    a, b = _setup(tmp_path, union=False)
    for repo, item in ((a, "XA"), (b, "XB")):
        assert run_cli(repo, "task", "add", item, "--title", item, agent="shared")[0] == 0
        _commit(repo, item)
    _git(a, "push", "-q", "origin", "main")
    r = _git(b, "pull", "--no-rebase", "--no-edit", "-q", "origin", "main", check=False)
    assert r.returncode != 0
    assert "CONFLICT" in r.stdout + r.stderr
    assert ".ddflow/events/shared.jsonl" in _git(b, "status", "--porcelain").stdout
    # Resolved locally, with the attribute the forge ignored:
    _git(b, "merge", "--abort")
    (b / ".gitattributes").write_text(UNION)
    _git(b, "add", ".gitattributes")
    _git(b, "commit", "-qm", "attrs", "--no-verify")
    _git(b, "pull", "--no-rebase", "--no-edit", "-q", "origin", "main")
    assert _shows(b, "XA") and _shows(b, "XB")


def test_committed_session_prompts_are_in_tracked_files(tmp_path):
    """Whoever can read the repository can read what an operator typed: the prompt is an
    event in a committed shard, not machine-local state."""
    seed, _b = _setup(tmp_path, union=True)
    secret = "operator said: rotate the staging password"
    assert run_cli(seed, "session", "prompt", "--text", secret, agent="alice")[0] == 0
    shards = [p for p in (seed / ".ddflow" / "events").glob("*.jsonl") if secret in p.read_text()]
    assert shards, "the prompt text must be in an event shard"
    for p in shards:
        rel = str(p.relative_to(seed))
        assert _git(seed, "check-ignore", "-q", rel, check=False).returncode == 1, rel
    _commit(seed, "prompt")
    tracked = _git(seed, "grep", "-l", secret).stdout
    assert ".ddflow/events/" in tracked
