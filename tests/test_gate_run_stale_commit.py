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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

OK = 0


def _probe(release: Path) -> str:
    # Judges the tree it STARTS on: no b.py -> wait for `release`, then fail; b.py -> pass.
    code = (
        "import pathlib,sys,time; "
        "bad = not pathlib.Path('b.py').exists(); "
        f"r = pathlib.Path({str(release)!r}); "
        "[time.sleep(0.1) for _ in range(600) if bad and not r.exists()]; "
        "sys.exit(1 if bad else 0)"
    )
    return f'{sys.executable} -c "{code}"'


def test_a_late_finishing_run_on_an_older_commit_does_not_overwrite_a_newer_pass(repo, tmp_path):
    release = tmp_path / "release"
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "gate.unit_tests.command", _probe(release))[0] == OK
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
    time.sleep(3)  # let it start on the commit without b.py
    (tree / "b.py").write_text("b = 1\n")
    _git(tree, "add", "b.py")
    _git(tree, "commit", "-qm", "b")
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
