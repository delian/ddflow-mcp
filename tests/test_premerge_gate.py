"""The pre-merge check: the project's hooks on the item's changed files, fixes committed
(D-premerge-precommit-gate-2026-11-12)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.config_sections.premerge import PremergeConfig
from ddflow.services import premerge as PM

needs_precommit = pytest.mark.skipif(
    shutil.which("pre-commit") is None, reason="pre-commit not installed"
)

#: A portable whitespace stripper (BSD and GNU sed disagree on `-i`): rewrites each file named.
STRIP_PY = """import re, sys
for name in sys.argv[1:]:
    with open(name, "rb") as fh:
        data = fh.read()
    new = re.sub(rb"[ \\t]+(?=\\r?\\n|\\Z)", b"", data)
    if new != data:
        with open(name, "wb") as fh:
            fh.write(new)
"""
PY = shlex.quote(sys.executable)
STRIP = f"""repos:
- repo: local
  hooks:
  - id: strip-ws
    name: strip-ws
    entry: {json.dumps(PY + " strip.py")}
    language: system
    files: '\\.txt$'
"""


def git(tree: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(tree), "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def commit(tree: Path, msg: str) -> None:
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "--no-verify", "-m", msg)


def make(tmp_path: Path, config: str | None, base: dict[str, str], item: dict[str, str]) -> Path:
    tree = tmp_path / "t"
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    git(tree, "config", "commit.gpgsign", "false")
    if config is not None:
        (tree / ".pre-commit-config.yaml").write_text(config, encoding="utf-8")
    for name, text in {"seed": "s\n", "strip.py": STRIP_PY, **base}.items():
        (tree / name).write_text(text, encoding="utf-8")
    commit(tree, "base")
    git(tree, "checkout", "-q", "-b", "item")
    for name, text in item.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text, encoding="utf-8")
    commit(tree, "item work")
    return tree


def cfg_of(**knobs) -> Config:
    cfg = Config()
    cfg.premerge = PremergeConfig(**knobs)
    return cfg


def run(tree: Path, **knobs):
    return PM.run(tree, "main", cfg_of(**knobs))


def status(tree: Path) -> str:
    return git(tree, "status", "--porcelain")


@needs_precommit
def test_an_unformatted_changed_file_is_fixed_and_committed(tmp_path):
    tree = make(tmp_path, STRIP, {"x.txt": "x\n"}, {"a.txt": "hello   \n"})
    rep = run(tree)
    assert rep.outcome == PM.PASSED
    assert rep.fixed == ["a.txt"]
    assert (tree / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert git(tree, "show", "--name-only", "--format=%s", "HEAD").split() == [
        "premerge:",
        "apply",
        "hook",
        "fixes",
        "a.txt",
    ]
    assert status(tree) == ""
    assert "a.txt" in rep.summary()


@needs_precommit
def test_a_file_outside_the_item_is_never_touched_or_committed(tmp_path):
    cfg = STRIP + (
        "  - id: wide\n    name: wide\n    entry: " + json.dumps(PY + " strip.py other.txt") + "\n"
        "    language: system\n    pass_filenames: false\n    always_run: true\n"
    )
    tree = make(tmp_path, cfg, {"other.txt": "keep   \n"}, {"a.txt": "ok   \n"})
    before = (tree / "other.txt").read_bytes()
    rep = run(tree)
    assert (tree / "other.txt").read_bytes() == before
    assert "other.txt" not in git(tree, "show", "--name-only", "--format=", "HEAD")
    assert status(tree) == ""
    assert rep.fixed == ["a.txt"]
    assert rep.outside == ["other.txt"]
    # a hook that wants to rewrite what the item did not change is the project's to settle
    assert rep.outcome == PM.FAILED and "other.txt" in rep.summary()
    skipped = PM.run(tree, "main", cfg_of(skip_hooks=["wide"]))
    assert skipped.outcome == PM.PASSED
    assert (tree / "other.txt").read_bytes() == before


def test_a_missing_hook_tool_is_unavailable_never_passed(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "x   \n"})
    rep = run(tree, precommit_command="no-such-precommit-tool")
    assert rep.outcome == PM.UNAVAILABLE
    assert [r.outcome for r in rep.results if r.name == "hooks"] == [PM.UNAVAILABLE]
    assert (tree / "a.txt").read_text(encoding="utf-8") == "x   \n"
    refusing = run(tree, precommit_command="no-such-precommit-tool", on_unavailable="refuse")
    assert refusing.outcome == PM.FAILED


@needs_precommit
def test_a_hook_that_cannot_be_fixed_fails(tmp_path):
    cfg = "repos:\n- repo: local\n  hooks:\n  - id: no-todo\n    name: no-todo\n    entry: grep -n TODO\n    language: system\n    files: '\\.txt$'\n"
    cfg = cfg.replace(
        "entry: grep -n TODO", "entry: sh -c 'if grep -q TODO \"$@\"; then exit 1; fi' --"
    )
    tree = make(tmp_path, cfg, {}, {"a.txt": "TODO\n"})
    rep = run(tree)
    assert rep.outcome == PM.FAILED
    assert [r.name for r in rep.failing] == ["no-todo"]


@needs_precommit
def test_a_hook_that_always_rewrites_ends_failed_not_looping(tmp_path):
    cfg = "repos:\n- repo: local\n  hooks:\n  - id: grow\n    name: grow\n    entry: sh -c 'for f; do echo x >> \"$f\"; done' --\n    language: system\n    files: '\\.txt$'\n"
    tree = make(tmp_path, cfg, {}, {"a.txt": "a\n"})
    rep = run(tree, max_fix_rounds=2)
    assert rep.outcome == PM.FAILED
    assert "rewrit" in rep.summary()
    assert status(tree) == ""


@needs_precommit
def test_fix_off_reports_failed_and_leaves_the_tree_as_it_was(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "hello   \n"})
    head = git(tree, "rev-parse", "HEAD")
    rep = run(tree, fix=False)
    assert rep.outcome == PM.FAILED
    assert git(tree, "rev-parse", "HEAD") == head
    assert (tree / "a.txt").read_text(encoding="utf-8") == "hello   \n"
    assert status(tree) == ""


def test_a_tree_with_uncommitted_changes_is_unavailable(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "a\n"})
    (tree / "a.txt").write_text("dirty   \n", encoding="utf-8")
    assert run(tree).outcome == PM.UNAVAILABLE


def test_no_config_runs_the_generic_set(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "ruff_argv", lambda tree: [])  # no ruff on this machine
    tree = make(tmp_path, None, {}, {"a.py": "x = 1   \ny = 2"})
    rep = run(tree)
    assert rep.fixed == ["a.py"]
    assert (tree / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"
    by = {r.name: r.outcome for r in rep.results}
    assert by["ruff"] == PM.UNAVAILABLE  # a missing tool is never a pass, the rest still ran
    assert by["trailing-whitespace"] in (PM.PASSED, PM.FIXED)
    assert rep.outcome == PM.UNAVAILABLE


def test_the_generic_set_refuses_markers_and_private_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "ruff_argv", lambda tree: [])
    tree = make(
        tmp_path,
        None,
        {},
        {"a.txt": "a\n<<<<<<< HEAD\nb\n", "k.txt": "-----BEGIN RSA " + "PRIVATE KEY-----\n"},
    )
    rep = run(tree)
    assert rep.outcome == PM.FAILED
    assert {r.name for r in rep.failing} >= {"merge-conflict", "private-key"}


def test_the_generic_set_refuses_an_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "ruff_argv", lambda tree: [])
    tree = make(tmp_path, None, {}, {"big.bin": "x" * 3000})
    assert "large-file" in {r.name for r in run(tree, large_file_kb=1).failing}


def test_generic_hooks_off_leaves_a_project_without_config_with_no_hook_step(tmp_path):
    tree = make(tmp_path, None, {}, {"a.txt": "a   \n"})
    rep = run(tree, generic_hooks=False)
    assert rep.fixed == []
    assert [r.outcome for r in rep.results if r.name == "hooks"] == [PM.SKIPPED]


@needs_precommit
def test_the_changed_test_files_run_and_a_failing_one_fails(tmp_path):
    cmd = f"{PY} -m pytest -q -p no:cacheprovider"
    tree = make(tmp_path, STRIP, {}, {"tests/test_a.py": "def test_a():\n    assert False\n"})
    rep = run(tree, test_command=cmd)
    assert {r.name: r.outcome for r in rep.results}["tests"] == PM.FAILED
    assert rep.outcome == PM.FAILED
    (tree / "tests/test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    commit(tree, "fix test")
    assert {r.name: r.outcome for r in run(tree, test_command=cmd).results}["tests"] == PM.PASSED


@needs_precommit
def test_no_changed_test_file_is_skipped_not_passed(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "a\n"})
    assert {r.name: r.outcome for r in run(tree).results}["tests"] == PM.SKIPPED


def test_an_exhausted_budget_is_unavailable(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "a\n"})
    ticks = iter(range(0, 10_000, 1000))
    rep = PM.run(tree, "main", cfg_of(budget_s=1), clock=lambda: float(next(ticks)))
    assert rep.outcome == PM.UNAVAILABLE


def test_disabled_runs_nothing(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "a   \n"})
    rep = run(tree, enabled=False)
    assert rep.disabled and rep.results == []
    assert (tree / "a.txt").read_text(encoding="utf-8") == "a   \n"


def test_a_hook_that_prints_the_could_not_run_phrase_still_fails_as_a_hook(tmp_path):
    cfg = (
        "repos:\n- repo: local\n  hooks:\n  - id: noisy\n    name: noisy\n"
        "    entry: sh -c 'echo An error has occurred in my linter; exit 1' --\n"
        "    language: system\n    files: '\\.txt$'\n"
    )
    tree = make(tmp_path, cfg, {}, {"a.txt": "a\n"})
    rep = run(tree)
    assert rep.outcome == PM.FAILED
    assert [r.name for r in rep.failing] == ["noisy"]


def test_an_unreadable_changed_file_is_unavailable_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "ruff_argv", lambda tree: [])
    tree = make(tmp_path, None, {}, {"a.txt": "a   \n"})
    real = Path.read_bytes

    def deny(self):
        if self.name == "a.txt":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    rep = run(tree)
    assert {r.name: r.outcome for r in rep.results}["hooks"] == PM.UNAVAILABLE


def test_fix_without_commit_leaves_the_fix_in_the_tree_uncommitted(tmp_path):
    tree = make(tmp_path, STRIP, {}, {"a.txt": "hello   \n"})
    head = git(tree, "rev-parse", "HEAD")
    rep = run(tree, commit_fixes=False)
    assert rep.fixed == ["a.txt"] and rep.commit == ""
    assert git(tree, "rev-parse", "HEAD") == head
    assert (tree / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert status(tree).strip() == "M a.txt"


def test_the_knobs_that_weaken_the_check_are_the_operators_alone():
    from ddflow.config_sections._docs import DECLARED

    for k in ("stages", "generic_hooks", "skip_hooks", "precommit_command", "test_command", "fix"):
        assert DECLARED[f"premerge.{k}"].operator_only, k
    assert DECLARED["premerge.stages"].check([]) != ""
    assert DECLARED["premerge.stages"].check(["pre-commit"]) == ""
