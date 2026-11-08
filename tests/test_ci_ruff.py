"""The ci gate auto-corrects ruff lint/format drift before merge (DX-ci-ruff-gate).

The pre-commit ruff hooks (``ruff check`` and ``ruff format --check``) fail a branch whose
changed Python files carry drift. The ci gate reported that failure but did not FIX it, so a
drifted branch merged anyway and the next `git push` failed (Bci-ruff-format-check-ci-lint-*).
With `[ci].ruff_autofix` on (default) the item's `ddflow ci run` auto-corrects the drift in the
item's own worktree and records the corrections in the gate evidence.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git

from ddflow.api import cirun as A

#: A pre-commit that fails the ruff-format hook when the checked tree's ``src/a.py`` still
#: carries `value=1` (what ruff format rewrites to `value = 1`): a branch-only drift.
FAKE_PRECOMMIT = """#!/bin/sh
if [ -e src/a.py ] && grep -q 'value=1' src/a.py 2>/dev/null; then
  echo 'ruff format --check (CI: Lint)...................Failed'; rc=1
else
  echo 'ruff format --check (CI: Lint)...................Passed'; rc=0
fi
echo 'ruff check (CI: Lint)...................Passed'
echo 'bandit...................Skipped'
exit $rc
"""

#: The auto-fix runs `ruff check --fix -- <files>` then `ruff format -- <files>`; this fake
#: models only the format drift: `format` rewrites it in place, `check --fix` is a no-op.
FAKE_RUFF = """#!{python}
import sys
mode = sys.argv[1]
args = sys.argv[2:]
if "--" in args:
    args = args[args.index("--") + 1:]
files = [a for a in args if not a.startswith("-")]
if mode == "format":
    for p in files:
        try:
            text = open(p).read()
        except OSError:
            continue
        rewritten = text.replace("value=1", "value = 1")
        if rewritten != text:
            open(p, "w").write(rewritten)
sys.exit(0)
"""


def _fake_tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pc = bin_dir / "pre-commit"
    pc.write_text(FAKE_PRECOMMIT)
    pc.chmod(pc.stat().st_mode | stat.S_IXUSR)
    ruff = bin_dir / "ruff"
    ruff.write_text(FAKE_RUFF.format(python=sys.executable))
    ruff.chmod(ruff.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _ruff_item(repo, monkeypatch, tmp_path, *, autofix: bool = True) -> Path:
    """A repo with a claimed item whose branch's lone changed file carries format drift."""
    _fake_tools(tmp_path, monkeypatch)
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
        + ("" if autofix else "[ci]\nruff_autofix = false\n")
    )
    run_cli(repo, "phase", "add", "P1", "--title", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--phase", "P1", "--globs", "src/a.py")
    code, out, err = run_cli(repo, "claim", "T1", agent="fix-ciruff")
    assert code == 0, out + err
    _, out, _ = run_cli(repo, "--json", "show", "T1")
    shown = json.loads(out)
    wt = Path(shown["worktree"])
    return wt if wt.is_absolute() else repo / wt


def test_a_branch_with_ruff_format_drift_is_auto_corrected_before_merge(
    repo, monkeypatch, tmp_path
):
    """Regression: drift on a branch must not go un-corrected to the merge (Bci-ruff-format-*)."""
    wt = _ruff_item(repo, monkeypatch, tmp_path)
    _commit(wt, {"src/a.py": "value=1\n"}, "a drifted module")

    res = A.run(repo, ref="T1")

    # The run still fails (the correction must be committed, then re-run), but the drift was
    # auto-corrected in the branch's worktree and the correction recorded.
    assert res.data["status"] == "failed", res.data
    assert (wt / "src/a.py").read_text() == "value = 1\n", "the drift was not auto-corrected"
    corr = res.data.get("corrections")
    assert corr, "the correction was not recorded in the evidence"
    assert corr.get("reformatted") == ["src/a.py"], corr
    assert corr.get("ruff") == ["ruff format --check (CI: Lint)"], corr


def test_ruff_autofix_off_leaves_the_drift_for_the_agent(repo, monkeypatch, tmp_path):
    """`[ci].ruff_autofix = false` reports the drift but changes nothing."""
    wt = _ruff_item(repo, monkeypatch, tmp_path, autofix=False)
    _commit(wt, {"src/a.py": "value=1\n"}, "a drifted module")

    res = A.run(repo, ref="T1")

    assert res.data["status"] == "failed", res.data
    assert "corrections" not in res.data, res.data
    assert (wt / "src/a.py").read_text() == "value=1\n", "autofix off must not rewrite the file"
