"""A claimed worktree of ddflow gets a `ddflow` that runs its own code (DX-worktree-bin-on-path)."""

import subprocess
import sys
from pathlib import Path

from ddflow.services import tree_bin as TB


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text("MARK = 'tree'\n")
    (tmp_path / ".git").write_text("gitdir: x\n")
    return tmp_path


def test_shim_runs_the_trees_package(tmp_path):
    tree = _tree(tmp_path)
    (tree / "ddflow" / "__main__.py").write_text("import ddflow; print(ddflow.MARK)\n")
    shim = TB.write_shim(tree)
    assert shim == tree.resolve() / TB.SHIM_REL
    r = subprocess.run([str(shim)], capture_output=True, text=True, cwd="/")
    assert r.stdout.strip() == "tree", r.stderr


def test_no_shim_without_ddflow_source(tmp_path):
    assert TB.write_shim(tmp_path) is None
    assert TB.advice(tmp_path) == ""


def test_advice_names_shim_and_path(tmp_path):
    text = TB.advice(_tree(tmp_path))
    assert str(TB.SHIM_REL) in text and "export PATH=" in text


def test_doctor_warns_when_running_code_is_elsewhere(tmp_path):
    notes = TB.stale_notes(_tree(tmp_path))
    assert len(notes) == 1 and "not the tree you are in" in notes[0]


def test_no_warning_in_the_running_tree():
    from ddflow.infra import paths as PA

    assert TB.stale_notes(PA.package_parent()) == []
    assert sys.executable
