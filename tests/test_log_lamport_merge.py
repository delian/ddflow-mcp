"""Two clones writing one shard, union-merged: the newest edit must still win (B190).

Bug B28589b8b26. A shard is meant to have one writer, which is what licensed reading
only its LAST line for the Lamport clock. Two clones that resolve to the same agent id
-- same hostname and directory name, or the same `DDFLOW_AGENT` -- both append to one
`<id>.jsonl`, and `merge=union` concatenates their suffixes: the merged shard reads
[1, 2, 3, 4, 1]. The tail says 1, the next append got lamport 2, and it folded BEFORE
events it was written after -- `update X1 --title v5` left the title at v4, while
`doctor` reported a healthy log.

The probe is the real thing: a bare remote, two clones, `git pull` with the union
driver `ddflow adopt` installs.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ddflow.api import reporting
from ddflow.infra import log as L
from tests.conftest import run_cli


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    """These tests are about the log's storage mechanics (event counts, byte offsets, clock
    values); the version stamp (B-upgrade.1-stamp) is tested in tests/test_upgrade_stamp.py."""
    monkeypatch.setattr(L.EventLog, "stamp", False)


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *argv], check=True, capture_output=True, text=True
    ).stdout


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


@pytest.fixture
def two_clones(tmp_path: Path) -> tuple[Path, Path]:
    """Clone A creates X1 and pushes; clone B pulls. Both then write as `sameid`."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert run_cli(a, "task", "add", "X1", "--title", "v1", agent="sameid")[0] == 0
    _commit_log(a, "X1")
    _git(a, "push", "-q", "origin", "main")
    b = _clone(remote, tmp_path / "b")
    return a, b


def _shard_lamports(repo: Path, agent: str) -> list[int]:
    shard = repo / ".ddflow" / "events" / f"{agent}.jsonl"
    return [json.loads(ln)["lamport"] for ln in shard.read_text().splitlines() if ln.strip()]


def _title(repo: Path, item: str) -> str:
    code, out, err = run_cli(repo, "--json", "show", item)
    assert code == 0, out + err
    return json.loads(out)["title"]


def _diverge_and_merge(a: Path, b: Path) -> None:
    """B edits once and pushes; A edits three times and pulls with the union driver.

    The union driver writes OURS then THEIRS, so A's merged shard ends with B's single
    low-clock line: [1, 2, 3, 4, 2].
    """
    assert run_cli(b, "update", "X1", "--title", "b1", agent="sameid")[0] == 0
    _commit_log(b, "b edit")
    _git(b, "push", "-q", "origin", "main")
    for t in ("v2", "v3", "v4"):
        assert run_cli(a, "update", "X1", "--title", t, agent="sameid")[0] == 0
    _commit_log(a, "a edits")
    _git(a, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")


def test_the_newest_edit_wins_after_a_union_merge_of_one_shard(two_clones):
    a, b = two_clones
    _diverge_and_merge(a, b)
    # The precondition that makes this a regression test for THIS bug: the merged
    # shard's tail is not its maximum.
    lamports = _shard_lamports(a, "sameid")
    assert lamports[-1] < max(lamports), lamports

    assert run_cli(a, "update", "X1", "--title", "v5", agent="sameid")[0] == 0
    assert _title(a, "X1") == "v5"
    assert _shard_lamports(a, "sameid")[-1] > max(lamports)


def test_the_clock_reads_past_a_non_monotone_tail(tmp_path: Path):
    """The same failure without git: the unit the fix actually lives in."""
    log = L.EventLog(tmp_path, "sameid")
    for i in range(4):
        log.append("task.added", f"T{i}", {"title": "t"})
    # What a union merge leaves behind: another writer's lamport-1 line at the tail.
    stray = L.Event(kind="task.added", subject="S", data={}, agent="sameid", lamport=1, ts="x")
    with log.shard.open("a") as fh:
        fh.write(stray.to_json() + "\n")
    fresh = L.EventLog(tmp_path, "sameid")
    assert fresh.append("task.added", "T9", {"title": "t"}).lamport == 5


def test_doctor_names_a_shard_whose_clock_goes_backwards(two_clones):
    """`doctor` said Healthy about the merged shard. A decrease inside one shard means
    two writers shared an identity -- worth saying even once the clock copes with it."""
    a, b = two_clones
    _diverge_and_merge(a, b)
    out = reporting.doctor(a, agent="sameid")
    text = " ".join(out.data["notes"] + out.data["problems"])
    assert "sameid.jsonl" in text and "decrease" in text, text


def test_doctor_is_quiet_about_a_single_writer_shard(two_clones):
    _, b = two_clones
    out = reporting.doctor(b, agent="sameid")
    text = " ".join(out.data["notes"] + out.data["problems"])
    assert "decrease" not in text, text


# -- (a): a derived identity is unique per clone ----------------------------------------


def _derived(repo: Path) -> str:
    L._AGENT_ID_CACHE.clear()
    return L.default_agent_id(repo)


def test_two_clones_with_the_same_host_and_dir_name_derive_different_ids(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    one = _clone(remote, tmp_path / "x" / "proj")
    two = _clone(remote, tmp_path / "y" / "proj")
    for r in (one, two):
        (r / ".ddflow").mkdir()
    monkeypatch.chdir(tmp_path)
    assert _derived(one) != _derived(two)


def test_the_derived_id_is_stable_across_calls_and_shared_by_worktrees(
    repo: Path, tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    (repo / ".ddflow").mkdir()
    monkeypatch.chdir(repo)
    first = _derived(repo)
    assert first == _derived(repo)
    # The suffix is the CLONE's, so a linked worktree -- same clone, different tree
    # name -- differs from the primary only in the tree name, and the claim made from
    # one process and the commit hook run from another still agree.
    wt = tmp_path / "wt-one"
    _git(repo, "worktree", "add", "-q", str(wt))
    monkeypatch.chdir(wt)
    in_tree = _derived(repo)
    suffix = first.rsplit("-", 1)[1]
    assert in_tree.endswith("-" + suffix) and "wt-one" in in_tree, (first, in_tree)


def test_deriving_an_id_does_not_create_ddflow_in_an_unadopted_repo(repo: Path, monkeypatch):
    """Constructing a log happens on every handshake; it must leave nothing behind."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.chdir(repo)
    _derived(repo)
    L.EventLog(repo)
    assert not (repo / ".ddflow").exists()


def test_explicit_and_env_ids_are_not_suffixed(repo: Path, monkeypatch):
    (repo / ".ddflow").mkdir()
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    assert L.resolve_agent_id(repo) == ("alpha", "env")
    assert L.resolve_agent_id(repo, declared="beta") == ("beta", "explicit")
    assert os.listdir(repo / ".ddflow") == []


def test_a_merge_that_inserts_a_line_mid_shard_is_seen_by_a_warm_clock(tmp_path: Path):
    """Critic finding (B190): a warm parse cache reads a shard from its old EOF, so a
    higher clock a union merge inserts BEFORE that offset would be missed. It is not:
    the cached prefix is re-hashed on every read, and an insertion changes it."""
    log = L.EventLog(tmp_path, "sameid")
    for _ in range(2):
        log.append("task.added", "T", {"title": "t"})
    assert log._highest_lamport() == 2  # warms the cache at the old EOF
    lines = log.shard.read_text().splitlines(keepends=True)
    theirs = L.Event(kind="task.added", subject="S", data={}, agent="sameid", lamport=30, ts="x")
    log.shard.write_text(lines[0] + theirs.to_json() + "\n" + lines[1])
    assert log._highest_lamport() == 30
    assert log.clock_regressions() == {"sameid.jsonl": (3, 30, 2)}


def test_a_filesystem_without_hard_links_still_gets_a_suffix(repo: Path, monkeypatch):
    """Falling back to the bare id there would be the collision again, silently."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    (repo / ".ddflow").mkdir()
    monkeypatch.chdir(repo)

    def no_links(*_a, **_k):
        raise PermissionError("hard links not supported")

    monkeypatch.setattr(L.os, "link", no_links)
    ident = _derived(repo)
    suffix = (repo / L.CLONE_ID_FILE).read_text().strip()
    assert suffix and ident.endswith("-" + suffix), ident
    # The temp file is gone either way.
    assert {p.name for p in (repo / L.CLONE_ID_FILE).parent.iterdir()} == {".gitignore", "clone-id"}


def test_reading_another_repositorys_log_writes_nothing_there(repo: Path, monkeypatch):
    """Bug B244aeaad5c: the id was derived when a log was CONSTRUCTED, so a log built
    only to be read -- `external.sync` folding a sibling project, documented as never
    writing there -- created the sibling's clone-id. A log's id is now derived on first
    use, and reading does not use it."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    (repo / ".ddflow" / "events").mkdir(parents=True)
    L.EventLog(repo, "someone").append("task.added", "X1", {"title": "t"})
    L._AGENT_ID_CACHE.clear()
    L.EventLog(repo).read_all()
    assert not (repo / ".ddflow" / "local").exists()
