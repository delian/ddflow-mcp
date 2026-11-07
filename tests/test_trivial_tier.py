"""Bbd88c54354: a trivial change (format-only, docs-only, comment/typo) runs the trivial tier.

A ruff-format-only, docs-only or comment-only change changes no behavior, so it pays the full
cross-family review (rubber_duck + critic) and the whole-suite ci. This pins the trivial tier:
`diff_is_trivial` accepts such a change (so it skips review + full-ci and still owes a scoped
test run and a no-behavior-change declaration), and rejects a logic change.
"""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import Item, fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import completion as CO
from ddflow.services import gates as G
from ddflow.services.docsonly import TRIVIAL_EXCUSED_GATES, diff_is_trivial


def _state(repo):
    return fold(EventLog(repo).read_all())


def _commit(repo, path: str, content: str) -> None:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", path], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)


def _worktree_change(repo, path: str, base_content: str, changed_content: str):
    """A claimed worktree whose tracked ``path`` is edited and committed."""
    run_cli(repo, "init")
    _commit(repo, path, base_content)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / path).write_text(changed_content)
    subprocess.run(["git", "-C", str(tree), "add", path], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "change"], check=True)
    return it


def _pipeline(repo, cfg):
    it = _state(repo).items["T1"]
    return G.status(
        _state(repo), cfg, "T1", None, diff=lambda: CO.changed_paths(repo, it), repo=repo
    )


def _with_ci(cfg) -> Config:
    cfg.gates.task_pipeline = [*cfg.gates.task_pipeline, "ci"]
    return cfg


def test_a_format_only_change_accepts_the_trivial_tier(repo):
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "x=1\n", "x = 1\n")
    assert diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))
    s = _pipeline(repo, cfg)
    for excused in TRIVIAL_EXCUSED_GATES:
        assert excused not in s.pipeline, s.pipeline
    assert "unit_tests" in s.pipeline  # the scoped test run is still owed
    assert not CO.needs_independent_reviewer(it, cfg, CO.changed_paths(repo, it), repo)


def test_a_comment_only_change_accepts_the_trivial_tier(repo):
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "x = 1\n# old note\n", "x = 1\n# new note\n")
    assert diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))
    s = _pipeline(repo, cfg)
    assert "rubber_duck" not in s.pipeline and "critic" not in s.pipeline and "ci" not in s.pipeline


def test_a_logic_change_does_not_accept_the_trivial_tier(repo):
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "x = 1\n", "x = 2\n")
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))
    s = _pipeline(repo, cfg)
    assert "rubber_duck" in s.pipeline and "critic" in s.pipeline and "ci" in s.pipeline, s.pipeline
    assert CO.needs_independent_reviewer(it, cfg, CO.changed_paths(repo, it), repo)


def test_a_starred_unpacking_change_is_not_trivial(repo):
    """rubber_duck: `*a, = range(3)` is code (starred unpacking), not a comment, so it must
    never read as trivial."""
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "a = range(3)\n", "*a, = range(3)\n")
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_a_shebang_change_is_not_trivial(repo):
    """rubber_duck/grok: a shebang (`#!...`) is a comment the AST ignores but which changes
    which interpreter runs the file, so it must never read as trivial."""
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "#!/usr/bin/env python3\nx = 1\n", "#!/usr/bin/env python2\nx = 1\n")
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_a_mode_only_change_is_not_trivial(repo):
    """critic: a `chmod +x` (mode 100644 -> 100755) changes executability, not the bytes, so
    it must never read as trivial."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "src/mod.py", "x = 1\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    subprocess.run(["git", "-C", str(tree), "add", "--chmod=+x", "src/mod.py"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "chmod +x"], check=True)
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_a_mode_change_on_a_glob_named_file_is_not_trivial(repo):
    """critic: `git ls-tree` path args are glob PATTERNS, so a tracked file literally named
    ``a*.py`` would match every ``src/a*.py`` entry and read back the first match's mode. A
    `chmod +x` on the ``*``-named file must then still read as a mode change (not trivial),
    so ``_mode`` matches the path literally."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "src/a!.py", "y = 1\n")
    _commit(repo, "src/a*.py", "x = 1\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    subprocess.run(
        ["git", "-C", str(tree), "add", "--chmod=+x", ":(literal)src/a*.py"], check=True
    )
    subprocess.run(
        ["git", "-C", str(tree), "commit", "-qm", "chmod +x the *-named file"], check=True
    )
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_docs_only_is_trivial_too(cfg):
    from ddflow.services.docsonly import diff_is_docs_only

    it = Item(id="X", kind="task", title="t", globs=["src/**"])
    assert diff_is_docs_only(it, cfg, ["docs/a.md", "README.md"])
    assert diff_is_trivial(it, cfg, None, ["docs/a.md", "README.md"])


def test_the_trivial_declaration_is_recorded_on_the_verdict(repo):
    """The no-behavior-change declaration shows on the verdict for a trivial change."""
    cfg = _with_ci(Config.load(repo))
    it = _worktree_change(repo, "src/mod.py", "x=1\n", "x = 1\n")
    v = CO.verdict(_state(repo), cfg, "T1", repo=repo)
    assert v.trivial, "a trivial change records its no-behavior-change declaration"


def test_no_readable_or_empty_diff_is_never_trivial(cfg):
    it = Item(id="X", kind="task", title="t", globs=["src/**"])
    assert not diff_is_trivial(it, cfg, None, None)
    assert not diff_is_trivial(it, cfg, None, [])
