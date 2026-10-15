"""Deleting the git-ignored `.ddflow/local` must not change who the agent is (B839f599cb5).

The derived agent id is `{host}-{dir}-{clone id}` and the clone id lives in the ignored
`.ddflow/local/clone-id`. After `git clean -xfd` or a re-image the file is gone, a NEW id
was minted, and the user was locked out of their own claim ("held by <old id>"). The id is
now recovered from evidence the log already holds: a `lease.acquired` whose holder is
`{bare id}-{suffix}` and whose recorded worktree is a linked worktree of THIS clone (in its `git worktree list`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from ddflow.infra import log as L
from tests.conftest import run_cli

ID_FILE = Path(".ddflow") / "local" / "clone-id"


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(path, "config", k, v)
    (path / "README").write_text("x\n")
    _git(path, "add", "README")
    _git(path, "commit", "-qm", "init", "--no-verify")
    assert run_cli(path, "task", "add", "T1", "--title", "t", "--globs", "src/t.py")[0] == 0
    return path


def _shards(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / ".ddflow" / "events").glob("*.jsonl"))


def _suffix(repo: Path) -> str:
    return (repo / ID_FILE).read_text().strip()


def _holders(repo: Path) -> set[str]:
    out = set()
    for p in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for ln in p.read_text().splitlines():
            e = json.loads(ln)
            if e["kind"] == "lease.acquired":
                out.add(e["data"]["holder"])
    return out


@pytest.fixture(autouse=True)
def _no_caller_identity(monkeypatch):
    """These tests are about the DERIVED id; an exported `DDFLOW_AGENT` (B3a8e04ca38) would
    replace it in every `run_cli` subprocess."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)


@pytest.fixture
def claimed(tmp_path: Path) -> Path:
    repo = _repo(tmp_path / "proj")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == 0, out + err
    return repo


def test_deleting_local_keeps_the_identity(claimed):
    old = _suffix(claimed)
    shards = _shards(claimed)
    shutil.rmtree(claimed / ".ddflow" / "local")

    code, out, err = run_cli(claimed, "heartbeat", "T1")
    assert code == 0, out + err
    assert "no lease held" not in out + err
    code, out, err = run_cli(claimed, "claim", "T1")
    assert "held by" not in out + err, out + err
    assert code == 0, out + err
    assert _suffix(claimed) == old
    assert _shards(claimed) == shards
    assert len(_holders(claimed)) == 1, _holders(claimed)


def test_a_second_clone_at_another_path_never_adopts_the_suffix(claimed, tmp_path):
    _git(claimed, "add", "-A", ".ddflow/events")
    _git(claimed, "commit", "-qm", "log", "--no-verify")
    other = tmp_path / "other" / "proj"  # same directory name, same host, other path
    other.parent.mkdir()
    subprocess.run(["git", "clone", "-q", str(claimed), str(other)], check=True)
    assert (other / ".ddflow" / "events").is_dir()
    assert run_cli(other, "status")[0] == 0
    assert L._clone_suffix(other) not in ("", _suffix(claimed))


def test_without_evidence_a_new_id_is_minted(tmp_path):
    repo = _repo(tmp_path / "proj")  # task add only: no claim, nothing to recover from
    first = L._clone_suffix(repo)
    assert first
    shutil.rmtree(repo / ".ddflow" / "local")
    again = L._clone_suffix(repo)
    assert again and again != first  # nothing recorded the old one: minted afresh


def test_newest_claim_wins_when_two_suffixes_qualify(claimed):
    bare = L.bare_agent_id(claimed)
    tree = next(p for p in (claimed / ".ddflow" / "worktrees").iterdir())
    stored = str(tree.relative_to(claimed))
    shard = claimed / ".ddflow" / "events" / f"{bare}-aaaaaa.jsonl"
    ev = {
        "agent": f"{bare}-aaaaaa", "kind": "lease.acquired", "subject": "T1", "lamport": 10**9,
        "schema": 1, "id": "e" + "0" * 24, "ts": "2999-01-01T00:00:00Z",
        "data": {"holder": f"{bare}-aaaaaa", "worktree": stored, "branch": "b", "at": 9e9,
                 "ttl_s": 1800, "globs": [], "kind": "task", "note": "", "resources": []},
    }  # fmt: skip
    shard.write_text(json.dumps(ev) + "\n")
    shutil.rmtree(claimed / ".ddflow" / "local")
    assert L._clone_suffix(claimed) == "aaaaaa"


def test_an_unadopted_project_creates_nothing(tmp_path):
    repo = tmp_path / "plain"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert L._clone_suffix(repo) == ""
    assert not (repo / ".ddflow").exists()


def test_a_copied_checkout_does_not_adopt_it_either(claimed, tmp_path):
    """A copy has the worktree directory but git there does not list it as its own."""
    other = tmp_path / "other" / "proj"
    other.parent.mkdir()
    shutil.copytree(claimed, other, symlinks=True)
    shutil.rmtree(other / ".ddflow" / "local")
    assert L._clone_suffix(other) not in ("", _suffix(claimed))


def test_the_checkouts_own_root_is_no_evidence(claimed):
    """A claim recorded against the main checkout ('.') is the same text in every clone."""
    bare = L.bare_agent_id(claimed)
    shard = claimed / ".ddflow" / "events" / f"{bare}-aaaaaa.jsonl"
    ev = {
        "agent": f"{bare}-aaaaaa", "kind": "lease.acquired", "subject": "T9", "lamport": 10**9,
        "schema": 1, "id": "e" + "1" * 24, "ts": "2999-01-01T00:00:00Z",
        "data": {"holder": f"{bare}-aaaaaa", "worktree": ".", "branch": "b", "at": 9e9,
                 "ttl_s": 1800, "globs": [], "kind": "task", "note": "", "resources": []},
    }  # fmt: skip
    shard.write_text(json.dumps(ev) + "\n")
    old = _suffix(claimed)
    shutil.rmtree(claimed / ".ddflow" / "local")
    assert L._clone_suffix(claimed) == old  # T1's linked tree wins; "." is ignored


def test_a_worktree_root_beside_the_checkout_still_recovers(tmp_path):
    repo = _repo(tmp_path / "proj")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text((cfg.read_text() if cfg.exists() else "") + '\n[worktree]\nroot = "../wt"\n')
    assert run_cli(repo, "claim", "T1")[0] == 0
    old = _suffix(repo)
    shutil.rmtree(repo / ".ddflow" / "local")
    assert L._clone_suffix(repo) == old
