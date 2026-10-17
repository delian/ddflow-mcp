"""A gate run that began on an older commit cannot overwrite a newer commit's outcome (B27a236a534).

DX-pins-uncovered: `gate run unit_tests` was started twice in the background, the first on a
commit that failed, the second on the fixed commit. The second recorded PASSED; the first
finished later and recorded FAILED over it, and `complete` then refused. A run now notes the
commit it began on and, finishing after a descendant commit has an outcome, records nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

OK = 0


def _probe(release: Path, began: Path) -> str:
    # Judges the tree it STARTS on: no b.py -> wait for `release`, then fail; b.py -> pass.
    code = (
        "import pathlib,sys,time; "
        "bad = not pathlib.Path('b.py').exists(); "
        f"bad and pathlib.Path({str(began)!r}).write_text('x'); "
        f"r = pathlib.Path({str(release)!r}); "
        "[time.sleep(0.1) for _ in range(600) if bad and not r.exists()]; "
        "sys.exit(1 if bad else 0)"
    )
    return f'{sys.executable} -c "{code}"'


@pytest.mark.parametrize("amend", [False, True], ids=["new-commit", "amended-commit"])
def test_a_late_finishing_run_on_an_older_commit_does_not_overwrite_a_newer_pass(
    repo, tmp_path, amend
):
    release = tmp_path / "release"
    began = tmp_path / "began"
    run_cli(repo, "init")
    assert (
        run_cli(repo, "config", "--set", "gate.unit_tests.command", _probe(release, began))[0] == OK
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "add b", "--globs", "b.py,a.py")
    assert run_cli(tree, "claim", "T1", "--no-worktree")[0] == OK
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "a")

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    first = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ddflow",
            "--repo",
            str(tree),
            "--json",
            "gate",
            "run",
            "T1",
            "unit_tests",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.monotonic() + 120  # until its command has started on the commit without b.py
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert began.exists()
    (tree / "b.py").write_text("b = 1\n")
    _git(tree, "add", "b.py")
    _git(tree, "commit", *(["--amend", "--no-edit", "-q"] if amend else ["-qm", "b"]))
    code, out, err = run_cli(tree, "--json", "gate", "run", "T1", "unit_tests")
    assert code == OK, out + err
    release.write_text("go")
    first.communicate(timeout=120)

    code, out, err = run_cli(tree, "--json", "gate", "status", "T1")
    assert '"unit_tests"' in out
    shown = run_cli(tree, "--json", "show", "T1")[1]
    gates = json.loads(shown)["gates"]
    assert gates["unit_tests"]["outcome"] == "passed", shown
    assert first.returncode != OK  # said so: it recorded nothing


def test_a_recorded_outcome_on_an_ancestor_does_not_supersede_the_newer_run(tmp_path):
    """The run on the NEWER commit finishing after one on an older commit has recorded must
    still record: direction matters (the review of the first cut)."""
    from ddflow.services import gates as G

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("1")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-qm", "one")
    old = G.tree_identity(tmp_path)
    (tmp_path / "f").write_text("2")
    _git(tmp_path, "commit", "-qam", "two")
    new = G.tree_identity(tmp_path)
    assert not G.moved_past(new, old, tmp_path)  # recorded is an ancestor of the run's
    assert G.moved_past(old, new, tmp_path)  # recorded is a descendant: the run is stale
    _git(tmp_path, "commit", "-q", "--amend", "-m", "two again")
    amended = G.tree_identity(tmp_path)
    assert G.moved_past(new, amended, tmp_path)  # no ancestry either way: recorded wins
    assert not G.moved_past(new, new, tmp_path)
