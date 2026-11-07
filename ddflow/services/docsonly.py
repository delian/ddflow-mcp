"""Which tasks changed only documents (bug B-research-task-cannot-complete-docs-only).

Kept beside the gates, not in them: `gates.status` and `completion` both ask it, and the gates
package re-exports every public definition it makes."""

from __future__ import annotations

import ast
from collections.abc import Callable

from ..config import Config
from ..core.globs import inside
from ..core.model import Item
from ..infra import worktree as W


#: The gates a TRIVIAL change (format-only / docs / comment, hence behavior-preserving)
#: skips: the two cross-family review gates and the whole-suite ci gate. Everything else
#: in the pipeline stays owed -- in particular `unit_tests`, whose scope is then the
#: selected tests, and `merge`.
TRIVIAL_EXCUSED_GATES: tuple[str, ...] = ("rubber_duck", "critic", "ci")


def _trivial_pipeline(gates: list[str]) -> list[str]:
    """``gates`` minus the review + full-ci gates a behavior-preserving change skips."""
    excused = set(TRIVIAL_EXCUSED_GATES)
    return [g for g in gates if g not in excused]


def diff_is_trivial(item: Item, cfg: Config, repo, paths: list[str] | None) -> bool:
    """Did this TASK change no behavior -- every changed path a document (`diff_is_docs_only`),
    else a format-only or comment-only edit? Such a change runs the TRIVIAL tier: it skips
    the cross-family review (`rubber_duck` + `critic`) and the whole-suite `ci`, and still
    owes a SCOPED `unit_tests` run and a no-behavior-change declaration (`completion.verdict`
    records it). A logic change -- one whose diff changes code -- is never trivial: the
    answer is derived from the ACTUAL diff (never the declared globs), and anything git
    cannot read is "not trivial", never a guess. A promotion, a phase and a non-task never
    qualify."""
    if item.kind != "task" or item.promote_to:
        return False
    if diff_is_docs_only(item, cfg, paths):
        return True
    if not paths or repo is None:
        return False
    tree = W.load_path(repo, item.worktree) if item.worktree else repo
    if tree is None or not tree.is_dir():
        return False
    base = item.base or W.default_branch(repo)
    return _is_format_or_comment(tree, base, paths)


def _ast_unchanged(tree, base: str, paths: list[str]) -> bool:
    """True when every changed path is a Python file whose AST is the same at ``base`` and at
    HEAD -- so the change is format-only or comment-only (a `#` comment is not in the AST, and
    ``ast`` does not record whitespace or indentation). The AST is the one thing a formatter and
    a comment edit keep identical while a logic change -- indentation into a block, a starred
    unpacking, a value edit -- mutates, so it is the SOUND test that `git diff -w` was not
    (`-w` calls Python indentation "unchanged", grok rubber-duck finding). A file that cannot be
    read at either ref, is not ``.py``, or does not parse is NOT a format-only claim: fail closed.
    """
    for p in paths:
        if not p.endswith(".py"):
            return False
        if not _is_regular(tree, base, p) or not _is_regular(tree, "HEAD", p):
            return False
        if _mode(tree, base, p) != _mode(tree, "HEAD", p):
            return False
        before = _show(tree, base, p)
        after = _show(tree, "HEAD", p)
        if before is None or after is None or not _py_ast_equal(before, after):
            return False
    return True


def _py_ast_equal(before: bytes, after: bytes) -> bool:
    """Do two Python sources describe the same module AND the same shebang? ``ast.parse`` is
    given the RAW byte content, so PEP 263 encoding-cookie detection applies and distinct
    non-UTF-8 bytes never collapse (the string values a latin-1 file holds are read as the
    bytes they are, not as U+FFFD). Comments and whitespace are still absent from the AST, so
    AST equality is exactly "no behavior change" for a formatter or a `#` comment edit; a
    docstring IS in the AST, so a docstring edit is behavior. The shebang (line 0 at column 0,
    the interpreter that runs the file on exec) is the one semantic comment the AST ignores,
    so it is part of the signature and changed by a leading space."""
    try:
        if ast.dump(ast.parse(before)) != ast.dump(ast.parse(after)):
            return False
    except (SyntaxError, ValueError, TypeError, MemoryError):
        return False
    return _shebang(before) == _shebang(after)


def _shebang(src: bytes) -> bytes:
    """Line 0 when it is a shebang (`#!` at column 0), else b'' -- the file's exec behavior.
    The raw bytes up to the first newline, `\\r` included, so a CRLF ending (a `\\r` Python
    folds into the interpreter path on exec) is a different shebang from the LF one."""
    first = src.split(b"\n", 1)[0] if b"\n" in src else src
    return first if first.startswith(b"#!") else b""


def _show(tree, ref: str, path: str) -> bytes | None:
    """The raw bytes of ``path`` at ``ref`` in ``tree``, or None when git cannot read it. The
    raw bytes are handed to ``ast.parse`` themselves, so the encoding cookie of the file is
    the one that decides how they decode."""
    r = W.git(tree, "show", f"{ref}:{path}", binary=True)
    if not r.ok or r.out_bytes is None:
        return None
    return r.out_bytes


def _mode(tree, ref: str, path: str) -> str | None:
    """The file mode at ``ref`` (the leading ``100644``/``100755`` of ``git ls-tree``), or None
    when git cannot read it. A mode-only change (``chmod +x``) changes executability, which the
    blob-comparing ``_show`` cannot see, so it is compared here: a mode that moved is a
    behavior change, never a format-only claim."""
    r = W.git(tree, "ls-tree", ref, "--", path)
    if not r.ok or not r.out:
        return None
    return r.out.split(" ", 1)[0]


def _is_regular(tree, ref: str, path: str) -> bool:
    """Whether ``path`` at ``ref`` is a regular file (``100644``/``100755``), not a symbolic
    link (``120000``) or a gitlink (``160000``): ``_show`` returns a symlink's TARGET text, so
    a retarget that keeps the parsed text identical would otherwise read as "unchanged"."""
    return _mode(tree, ref, path) in ("100644", "100755")


def _is_format_or_comment(tree, base: str, paths: list[str]) -> bool:
    """True when the item's own committed change to ``paths`` is format-only or comment-only.
    Uncommitted edits fail closed (only the committed change is judged), and so does a new
    file among ``paths`` (a file absent at ``base`` has no readable base side)."""
    tracked = W.git(tree, "diff", "HEAD", "--", *paths)
    if not tracked.ok or tracked.out.strip():
        return False
    return _ast_unchanged(tree, base, paths)


def diff_is_docs_only(item: Item, cfg: Config, paths: list[str] | None) -> bool:
    """Did this TASK change only documents (every ACTUAL changed path inside
    `[gates].doc_globs`)? Then it runs the doc pipeline: a research or docs task made through
    the MCP, which has no `--kind`, has no code for `unit_tests` or an independent reviewer to
    judge (B-research-task-cannot-complete-docs-only). The ACTUAL diff decides, never the
    declared globs (B-na-required-gate): no readable diff, an empty one, a promotion and a
    phase are never docs-only. A `research` item whose kind the project pinned in
    `[gates].kind_pipelines` keeps that pipeline."""
    if item.kind not in ("task", "research") or item.kind in cfg.gates.kind_pipelines:
        return False
    if item.promote_to or not paths or not cfg.gates.doc_globs:
        return False
    return all(any(inside(p, d) for d in cfg.gates.doc_globs) for p in paths)


def pipeline_by_diff(
    item: Item,
    cfg: Config,
    gates: list[str],
    diff: Callable[[], list[str] | None] | None,
    doc_pipeline: list[str],
    repo=None,
) -> tuple[list[str], Callable[[], list[str] | None] | None]:
    """The pipeline an item runs given its ACTUAL diff: ``doc_pipeline`` when it changed only
    documents, the TRIVIAL tier (``gates`` minus the review + full-ci gates) when it changed
    no behavior, else ``gates``. Also returns ``diff`` read once, so the caller's later
    `applies_when` checks do not run git again. ``repo`` lets the trivial classification read
    the diff content; without it a non-document change is never trivial (it fails closed to
    ``gates``)."""
    if diff is None:
        return gates, None
    paths = diff()
    chosen = gates
    if diff_is_docs_only(item, cfg, paths):
        chosen = doc_pipeline
    elif diff_is_trivial(item, cfg, repo, paths):
        chosen = _trivial_pipeline(gates)
    return chosen, lambda: paths
