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


def test_changed_python_own_excludes_a_merge_pulled_file(tmp_path):
    """B-autoformat-stages-outside-lease: a merge pulled `base`'s `b.py` in, which a broad
    glob still matches; `changed_python(own=True)` names only the item's own edit (`a.py`)."""
    from ddflow.services.ci import ruff

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "switch", "-c", "main")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "item")
    (tmp_path / "src" / "a.py").write_text("a2\n")
    _git(tmp_path, "commit", "-qam", "item edit")
    item = _git(tmp_path, "rev-parse", "HEAD").strip()
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "src" / "b.py").write_text("b\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "main b")
    base = _git(tmp_path, "rev-parse", "HEAD").strip()

    own = ruff.changed_python(tmp_path, item, base, own=True)
    assert own == ["src/a.py"], own
    assert "src/b.py" not in own


def test_auto_fix_formats_only_files_the_item_owns(repo, monkeypatch, tmp_path):
    """B-autoformat-stages-outside-lease: the item's glob is only `src/a.py`; a merge brings
    in `src/b.py` (unformatted, outside the glob) and `changed_python` names both. The
    auto-fix must reformat only the item's own (glob) files and leave the outside-glob file
    untouched and unreported."""
    wt = _ruff_item(repo, monkeypatch, tmp_path)
    _commit(wt, {"src/a.py": "value=1\n"}, "a drifted module")
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "b.py").write_text("value=1\n")  # a merge brought it in; glob is only src/a.py

    monkeypatch.setattr(
        A.CI, "changed_python", lambda repo, sha, base, *, own=False: ["src/a.py", "src/b.py"]
    )
    res = A.run(repo, ref="T1")

    corr = res.data.get("corrections") or {}
    assert corr.get("reformatted") == ["src/a.py"], corr
    assert (wt / "src" / "b.py").read_text() == "value=1\n", (
        "a file outside the item's globs was rewritten"
    )


def test_safe_files_keeps_only_the_worktrees_own_python_files(tmp_path):
    """A path that escapes the worktree or reads as a flag is never handed to ruff."""
    from ddflow.services.ci import ruff

    root = tmp_path / "wt"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x=1\n")
    kept = ruff._safe_files(root, ["src/a.py", "../etc/x.py", "/abs/x.py", "--flag.py", "README.md"])  # fmt: skip
    assert kept == ["src/a.py"]
