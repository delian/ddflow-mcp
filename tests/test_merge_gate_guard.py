"""Bug B279a0ebfc1: `merge` recorded the merge gate's outcome without the gate definitions,
so `outcomes.record` skipped its human-gate guard: on a project whose `merge` gate is a
human checkpoint, a failed merge wrote an agent's `failed` outcome onto it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

HUMAN_MERGE = '[gate.merge]\ntitle = "The operator lands it"\nhuman = true\n'


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _claimed_with_a_commit(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(HUMAN_MERGE)
    (repo / "a.py").write_text("x = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "commit", "-qam", "work")
    return wt


def _merge_outcomes(repo: Path) -> list[tuple[str, str]]:
    return [
        (e.kind, e.agent)
        for e in EventLog(repo).read_all()
        if e.kind.startswith("gate.") and e.data.get("gate") == "merge"
    ]


def test_a_failed_merge_writes_no_agent_outcome_on_a_human_merge_gate(repo) -> None:
    _claimed_with_a_commit(repo)
    (repo / "a.py").write_text("x = 2\n")  # main moves on: the landing conflicts
    _git(repo, "commit", "-qam", "conflicting change on main")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code != 0, out + err
    assert "ddflow approve" not in out + err  # nothing landed: no approval to ask for
    assert _merge_outcomes(repo) == []
    st = fold(EventLog(repo).read_all(), strict=False)
    assert not st.items["T1"].gate_outcome("merge")


def test_a_landed_merge_leaves_a_human_merge_gate_to_the_person(repo) -> None:
    """The same guard on the landing path: it raised ValueError AFTER git had merged (exit 1
    on a merge that happened). The person clears the gate with `ddflow approve`."""
    _claimed_with_a_commit(repo)
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == 0, out + err
    assert "ddflow approve T1 merge" in out + err
    assert _merge_outcomes(repo) == []
