"""`ddflow probe`: git merge-tree between an item's branch and main or a peer (LK-conflict-probe).

Throwaway repositories, real git: a clean pair, a conflicting pair, an old git (the scratch
merge answers), git's 129, an unknown ref, the knob off, and the event the command records.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.log import EventLog
from ddflow.services import probe as PR

TIMEOUT = 30


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _branch(repo: Path, name: str, path: str, text: str, *, base: str = "main") -> None:
    _git(repo, "checkout", "-q", "-b", name, base)
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", f"{name}: {path}")
    _git(repo, "checkout", "-q", "main")


def _main_edit(repo: Path, path: str, text: str) -> None:
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", f"main: {path}")


@pytest.fixture
def pair(repo: Path) -> Path:
    """`ddflow/clean` touches its own file; `clash` and main both rewrite shared.txt."""
    (repo / "shared.txt").write_text("one\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "shared")
    _branch(repo, "ddflow/clean", "mine.txt", "mine\n")
    _branch(repo, "ddflow/clash", "shared.txt", "from the branch\n")
    _main_edit(repo, "shared.txt", "from main\n")
    return repo


def test_clean_pair(pair: Path) -> None:
    p = PR.run(pair, "ddflow/clean", "main", timeout_s=TIMEOUT)
    assert (p.result, p.method, p.paths) == (PR.CLEAN, PR.MERGE_TREE, ())
    assert len(p.item_sha) == 40 and len(p.against_sha) == 40


def test_conflicting_pair_names_the_paths(pair: Path) -> None:
    p = PR.run(pair, "ddflow/clash", "main", timeout_s=TIMEOUT)
    assert p.result == PR.CONFLICT
    assert p.paths == ("shared.txt",)


def test_old_git_falls_back_to_a_scratch_merge(pair: Path, monkeypatch) -> None:
    monkeypatch.setattr(PR, "git_version", lambda repo, t: (2, 30))
    clean = PR.run(pair, "ddflow/clean", "main", timeout_s=TIMEOUT)
    clash = PR.run(pair, "ddflow/clash", "main", timeout_s=TIMEOUT)
    assert (clean.result, clean.method) == (PR.CLEAN, PR.SCRATCH)
    assert (clash.result, clash.method) == (PR.CONFLICT, PR.SCRATCH)
    assert clash.paths == ("shared.txt",)


def test_git_129_falls_back_to_a_scratch_merge(pair: Path, monkeypatch) -> None:
    real = PR.G.run

    def run(repo, *args, **kw):
        if args[:1] == ("merge-tree",):
            return PR.G.GitResult(129, "", "usage: git merge-tree")
        return real(repo, *args, **kw)

    monkeypatch.setattr(PR.G, "run", run)
    p = PR.run(pair, "ddflow/clean", "main", timeout_s=TIMEOUT)
    assert (p.result, p.method) == (PR.CLEAN, PR.SCRATCH)


def test_unknown_ref_is_unavailable_never_clean(pair: Path) -> None:
    p = PR.run(pair, "no-such-branch", "main", timeout_s=TIMEOUT)
    assert p.result == PR.UNAVAILABLE and not p.clean
    assert "no-such-branch" in p.reason


def test_a_timeout_is_unavailable(pair: Path, monkeypatch) -> None:
    real = PR.G.run

    def run(repo, *args, **kw):
        if args[:1] == ("merge-tree",):
            return PR.G.GitResult(PR.G.UNAVAILABLE, "", "timed out", unavailable=True)
        return real(repo, *args, **kw)

    monkeypatch.setattr(PR.G, "run", run)
    assert PR.run(pair, "ddflow/clean", "main", timeout_s=1).result == PR.UNAVAILABLE


def _items(repo: Path, *ids: str) -> None:
    run_cli(repo, "init")
    for i in ids:
        EventLog(repo, "operator").append("task.added", i, {"title": i})


def test_cli_clean_conflict_peer_and_event(pair: Path) -> None:
    _items(pair, "clean", "clash")
    code, out, _ = run_cli(pair, "probe", "clean")
    assert code == 0 and "clean" in out and "textually clean only" in out
    code, out, err = run_cli(pair, "probe", "clash")
    assert code == 1 and "shared.txt" in out + err and "ddflow wait" in out + err
    code, _, _ = run_cli(pair, "probe", "clean", "--against", "clash")
    assert code == 0
    code, out, _ = run_cli(pair, "--json", "probe", "clash")
    body = json.loads(out)
    assert body["result"] == "conflict" and body["paths"] == ["shared.txt"]
    assert len(body["head"]) == 40 and len(body["base"]) == 40
    events = [e for e in EventLog(pair, "x").read_all() if e.kind == "probe.recorded"]
    assert [e.data["result"] for e in events][:2] == ["clean", "conflict"]
    assert all("text" not in e.data for e in events)


def test_cli_unknown_item_fails(pair: Path) -> None:
    _items(pair)
    assert run_cli(pair, "probe", "ghost")[0] == 1


def test_cli_knob_off_is_unavailable_and_recorded(pair: Path) -> None:
    _items(pair, "clean")
    (pair / ".ddflow").mkdir(exist_ok=True)
    with (pair / ".ddflow" / "config.toml").open("a") as f:
        f.write("\n[locks]\nprobe = false\n")
    code, out, err = run_cli(pair, "probe", "clean")
    assert code == 2 and "unavailable" in out + err and "[locks].probe is off" in out + err
    events = [e for e in EventLog(pair, "x").read_all() if e.kind == "probe.recorded"]
    assert events[-1].data["result"] == "unavailable"
