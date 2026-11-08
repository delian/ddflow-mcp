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
from ddflow.services.docsonly import TRIVIAL_EXCUSED_GATES, diff_is_docs_only, diff_is_trivial


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


def test_a_dirty_logic_edit_on_a_glob_named_file_is_not_trivial(repo):
    """rubber_duck: `git diff` path args are glob patterns too, so a tracked ``[a].py`` (a
    char class) would not match its own name, the dirty fail-close would read an empty diff and
    skip judging the uncommitted edit. An uncommitted logic edit on the ``[``-named file must
    still fail closed, so ``_is_format_or_comment`` matches each path literally like ``_mode``."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    f = repo / "src/[a].py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x=1\n")
    subprocess.run(["git", "-C", str(repo), "add", ":(literal)src/[a].py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / "src/[a].py").write_text("x = 1\n")  # committed: format-only, trivial on its own
    subprocess.run(["git", "-C", str(tree), "add", ":(literal)src/[a].py"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "format"], check=True)
    (tree / "src/[a].py").write_text("x = 2\n")  # UNCOMMITTED logic edit the fail-close must see
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_an_untracked_file_beside_a_docs_only_change_is_not_trivial(repo):
    """rubber_duck: ``_is_trivial`` classifies from COMMITTED changed paths, so a docs-only
    tracked change beside an UNTRACKED new code file would read trivial and skip the whole
    suite over real, unseen code. Untracked files fail closed."""
    from ddflow.services.testscope import _is_trivial

    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "docs/a.md", "old\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / "docs/a.md").write_text("new\n")
    subprocess.run(["git", "-C", str(tree), "add", "docs/a.md"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "docs edit"], check=True)
    f = tree / "src/new_feature.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x = 1\n")  # UNTRACKED logic beside the docs-only change
    assert not _is_trivial(cfg, it, tree)


def test_a_dirty_tracked_file_outside_the_committed_change_is_not_trivial(repo):
    """rubber_duck: `diff_is_trivial` classifies from the committed change's paths, so a
    docs-only committed edit beside a DIRTY tracked file outside those paths (an uncommitted
    logic edit) would read trivial. The whole-tree dirty check fails closed."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "docs/a.md", "old\n")
    _commit(repo, "src/foo.py", "x = 1\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / "docs/a.md").write_text("new\n")
    subprocess.run(["git", "-C", str(tree), "add", "docs/a.md"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "docs edit"], check=True)
    (tree / "src/foo.py").write_text("x = 2\n")  # DIRTY tracked, outside the docs change
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_a_staged_logic_edit_is_not_trivial(repo):
    """rubber_duck: `git diff HEAD` compares WORKTREE to HEAD and misses an index-only staged
    edit; the dirty scan must also compare the INDEX to HEAD, or a staged logic change beside a
    docs-only commit reads trivial."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "docs/a.md", "old\n")
    _commit(repo, "src/foo.py", "x = 1\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / "docs/a.md").write_text("new\n")
    subprocess.run(["git", "-C", str(tree), "add", "docs/a.md"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "docs edit"], check=True)
    (tree / "src/foo.py").write_text("x = 2\n")  # logic edit
    subprocess.run(["git", "-C", str(tree), "add", "src/foo.py"], check=True)  # stage it
    subprocess.run(
        ["git", "-C", str(tree), "restore", "--source=HEAD", "--worktree", "src/foo.py"],
        check=True,
    )  # worktree back to HEAD; the INDEX still holds x = 2
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))


def test_docs_only_is_trivial_too(cfg):

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


def test_a_staged_logic_edit_on_a_committed_path_is_not_trivial(repo):
    """critic: a staged logic edit to a file the committed comment-only change ALSO touches
    (its path is in ``paths``) is uncommitted work and must not ride the trivial tier; the
    earlier scan exempted paths already in ``paths``, so the logic edit was missed."""
    cfg = _with_ci(Config.load(repo))
    run_cli(repo, "init")
    _commit(repo, "src/foo.py", "x = 1\n")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    tree = W.load_path(repo, it.worktree)
    (tree / "src/foo.py").write_text("x=1\n")  # committed: format-only of "x = 1\n", trivial
    subprocess.run(["git", "-C", str(tree), "add", "src/foo.py"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "format"], check=True)
    (tree / "src/foo.py").write_text("x = 2\n")  # STAGED logic edit to the SAME committed path
    subprocess.run(["git", "-C", str(tree), "add", "src/foo.py"], check=True)
    subprocess.run(
        ["git", "-C", str(tree), "restore", "--source=HEAD", "--worktree", "src/foo.py"],
        check=True,
    )
    assert not diff_is_trivial(it, cfg, repo, CO.changed_paths(repo, it))
