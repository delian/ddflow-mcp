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
    monkeypatch.setattr(PR, "git_version", lambda repo, t: (2, 43, 0))
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
    monkeypatch.setattr(PR, "git_version", lambda repo, t: (2, 43, 0))
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


def test_git_unavailable_at_rev_parse_is_not_a_missing_ref(pair: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        PR.G,
        "run",
        lambda repo, *a, **kw: PR.G.GitResult(PR.G.UNAVAILABLE, "", "timed out", unavailable=True),
    )
    p = PR.run(pair, "ddflow/clean", "main", timeout_s=1)
    assert p.result == PR.UNAVAILABLE and "not a commit" not in p.reason


def test_cli_names_the_default_branch_actually_probed(pair: Path) -> None:
    _git(pair, "branch", "-m", "main", "trunk")
    _items(pair, "clash")
    _git(pair, "branch", "-m", "trunk", "master")
    code, out, err = run_cli(pair, "probe", "clash")
    assert code == 1 and "master" in out + err and "main into" not in out + err


def test_patch_level_minimum_is_honoured(pair: Path, monkeypatch) -> None:
    monkeypatch.setattr(PR, "git_version", lambda repo, t: (2, 38, 0))
    p = PR.run(pair, "ddflow/clean", "main", timeout_s=TIMEOUT, min_git="2.38.1")
    assert p.method == PR.SCRATCH


def test_probe_at_accepts_a_single_place_and_reports_a_bad_type() -> None:
    from ddflow.config_sections.locks import _probe_at

    assert _probe_at("claim") == (["claim"], [])
    assert _probe_at(3)[1]


def test_scratch_conflict_names_a_path_with_a_comma_whole(repo: Path, monkeypatch) -> None:
    (repo / "a,b.txt").write_text("one\n")
    _git(repo, "add", "a,b.txt")
    _git(repo, "commit", "-qm", "comma")
    _branch(repo, "ddflow/c", "a,b.txt", "branch\n")
    _main_edit(repo, "a,b.txt", "main\n")
    monkeypatch.setattr(PR, "git_version", lambda repo, t: (2, 30))
    assert PR.run(repo, "ddflow/c", "main", timeout_s=TIMEOUT).paths == ("a,b.txt",)


def test_an_item_named_main_does_not_hijack_the_default_branch(pair: Path) -> None:
    _items(pair, "clash", "main")
    _git(pair, "branch", "ddflow/main", "ddflow/clean")
    code, out, err = run_cli(pair, "--json", "probe", "clash")
    assert json.loads(out)["against"] == "main" and code == 1


def test_conflict_text_names_the_mcp_call(pair: Path) -> None:
    _items(pair, "clash")
    _, out, _ = run_cli(pair, "--json", "probe", "clash")
    assert "ddflow_probe id=clash" in json.loads(out)["text"]


def test_peer_advice_names_the_peer_branch_not_its_item_id(pair: Path) -> None:
    _items(pair, "clash", "clean")
    _git(pair, "checkout", "-q", "ddflow/clean")
    (pair / "shared.txt").write_text("peer rewrites\n")
    _git(pair, "commit", "-qam", "peer")
    _git(pair, "checkout", "-q", "main")
    _, out, _ = run_cli(pair, "--json", "probe", "clash", "--against", "clean")
    body = json.loads(out)
    assert body["result"] == "conflict" and "Merge ddflow/clean into" in body["text"]
